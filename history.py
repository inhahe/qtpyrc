# history.py - SQLite-backed channel history for session replay

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from state import dbg, LOG_ERROR


# ---------------------------------------------------------------------------
# Shared read queries. These take an explicit connection so the exact same SQL
# is used by every one of the three connections (see HistoryDB) -- no risk of
# them drifting apart.
# ---------------------------------------------------------------------------

def _id_cap(cutoff_id):
  """SQL fragment + params restricting a read to rows that existed at *cutoff_id*.

  A window that is replaying its backlog holds its live output back in a queue
  and renders it afterwards -- but those same live lines are also written to this
  table as they arrive, so a replay bounded only by "newest row" would render
  them too and the flushed queue would then repeat every one of them. Callers
  that hold output back pass the id the table ended at when they started holding
  (Window.begin_replay_queue -> HistoryDB.current_max_id), which splits the rows
  cleanly: <= cutoff is backlog to replay, > cutoff is a line already queued.
  """
  if cutoff_id is None:
    return '', ()
  return ' AND id <= ?', (cutoff_id,)


# ---------------------------------------------------------------------------
# Every read below returns rows in one shape:
#
#     (id, ts, type, nick, text, prefix)
#
# The id is not decoration. It is the only exact identity a rendered line has,
# and two consumers need one:
#
#   * the lazy scroll-up loader and the drip-feed, which walk the table by id
#     (they used to be handed it separately, alongside the rows, as
#     ``oldest_id`` / ``last_id``);
#   * render_audit, which asks "are these two identical-looking lines the same
#     line?".  Everything else it could compare on is ambiguous: the displayed
#     timestamp is HH:MM, so a line said daily at the same minute collides with
#     itself, and the text is the *reason* it is looking.  Row identity is the
#     answer that cannot be wrong -- see irc_client._render_history_row.
# ---------------------------------------------------------------------------

def _q_get_last(conn, network, channel, limit, cutoff_id=None):
  cap, cap_params = _id_cap(cutoff_id)
  cur = conn.execute(
    "SELECT id, ts, type, nick, text, COALESCE(prefix, '') FROM history "
    "WHERE network = ? AND channel = ?" + cap + " "
    "ORDER BY id DESC LIMIT ?",
    (network or '', channel) + cap_params + (limit,))
  rows = cur.fetchall()
  rows.reverse()
  return rows  # [(id, ts, type, nick, text, prefix), ...]


def _q_replay_bounds(conn, network, channel, limit, cutoff_id=None):
  net = network or ''
  cap, cap_params = _id_cap(cutoff_id)
  row = conn.execute(
    "SELECT MAX(id) FROM history WHERE network = ? AND channel = ?" + cap,
    (net, channel) + cap_params).fetchone()
  max_id = row[0] if row else None
  if max_id is None:
    return None  # no history for this channel
  row = conn.execute(
    "SELECT id FROM history WHERE network = ? AND channel = ?" + cap + " "
    "ORDER BY id DESC LIMIT 1 OFFSET ?",
    (net, channel) + cap_params + (max(0, int(limit) - 1),)).fetchone()
  min_id = row[0] if row else 0
  return (min_id, max_id)


def _q_get_before(conn, network, channel, before_id, limit):
  """Return (rows, oldest_id) for the *limit* history rows immediately older
  than *before_id* (i.e. with id < before_id), oldest first. Used by the lazy
  scroll-up loader: when the user scrolls to the top of a window we prepend the
  next batch of older lines. rows are 6-tuples (id, ts, type, nick, text,
  prefix); oldest_id is the smallest id in the batch (pass it as *before_id* on
  the next call). When no older rows exist, returns ([], before_id)."""
  cur = conn.execute(
    "SELECT id, ts, type, nick, text, COALESCE(prefix, '') FROM history "
    "WHERE network = ? AND channel = ? AND id < ? "
    "ORDER BY id DESC LIMIT ?",
    (network or '', channel, before_id, limit))
  raw = cur.fetchall()
  if not raw:
    return [], before_id
  raw.reverse()  # oldest first
  return raw, raw[0][0]


def _q_get_chunk(conn, network, channel, after_id, max_id, chunk):
  cur = conn.execute(
    "SELECT id, ts, type, nick, text, COALESCE(prefix, '') FROM history "
    "WHERE network = ? AND channel = ? AND id > ? AND id <= ? "
    "ORDER BY id ASC LIMIT ?",
    (network or '', channel, after_id, max_id, chunk))
  raw = cur.fetchall()
  if not raw:
    return [], after_id
  return raw, raw[-1][0]


class HistoryReader:
  """Read-only history access that runs its SQLite queries on a dedicated
  background thread, so the GUI thread never blocks on disk I/O. WAL mode lets
  this reader connection run concurrently with the GUI-thread writer connection
  in HistoryDB. A single worker thread keeps the connection thread-affine
  (sqlite3 connections must not be shared across threads).

  The async wrappers below are meant to be awaited from the GUI thread (which
  is where the qasync asyncio loop runs); each hops to the worker thread for the
  actual query and returns the rows back on the GUI thread for rendering."""

  def __init__(self, db_path, db=None):
    self._db_path = db_path
    self._db = db      # the HistoryDB whose queued writes we must not overtake
    self._executor = ThreadPoolExecutor(
      max_workers=1, thread_name_prefix='history-reader')
    self._conn = None  # created lazily inside the worker thread

  def _barrier(self):
    """Wait for HistoryDB's writer to catch up before reading. Worker thread.

    Same requirement as HistoryDB's own reads -- a replay bounded by
    current_max_id() has to be able to see the row that id names -- but paid on
    this thread rather than the GUI one, so the drip-feed waiting for the disk
    costs the user nothing."""
    if self._db is not None:
      self._db.flush_pending()

  def _get_conn(self):
    # Runs IN the worker thread. Open a private read-only connection.
    if self._conn is None:
      self._conn = sqlite3.connect(self._db_path)
      # We only ever read; query_only makes accidental writes fail loudly and
      # tells SQLite it never needs to touch the WAL as a writer.
      self._conn.execute("PRAGMA query_only=1")
    return self._conn

  # --- worker-thread bodies -------------------------------------------------
  def _do_replay_bounds(self, network, channel, limit, cutoff_id):
    self._barrier()
    return _q_replay_bounds(self._get_conn(), network, channel, limit, cutoff_id)

  def _do_get_chunk(self, network, channel, after_id, max_id, chunk):
    self._barrier()
    return _q_get_chunk(self._get_conn(), network, channel, after_id, max_id, chunk)

  def _do_get_last(self, network, channel, limit, cutoff_id):
    self._barrier()
    return _q_get_last(self._get_conn(), network, channel, limit, cutoff_id)

  def _do_get_before(self, network, channel, before_id, limit):
    self._barrier()
    return _q_get_before(self._get_conn(), network, channel, before_id, limit)

  # --- async wrappers (await from the GUI thread) ---------------------------
  async def replay_bounds(self, network, channel, limit, cutoff_id=None):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
      self._executor, self._do_replay_bounds, network, channel, limit, cutoff_id)

  async def get_chunk(self, network, channel, after_id, max_id, chunk):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
      self._executor, self._do_get_chunk, network, channel, after_id, max_id, chunk)

  async def get_last(self, network, channel, limit, cutoff_id=None):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
      self._executor, self._do_get_last, network, channel, limit, cutoff_id)

  async def get_before(self, network, channel, before_id, limit):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
      self._executor, self._do_get_before, network, channel, before_id, limit)

  def close(self):
    """Close the reader connection (on the worker thread) and stop the pool."""
    def _close():
      if self._conn is not None:
        try:
          self._conn.close()
        except Exception:
          pass
        self._conn = None
    try:
      self._executor.submit(_close)
    except Exception:
      pass
    self._executor.shutdown(wait=False)


class HistoryDB:
  """Persistent channel history stored in SQLite.

  Threading: the GUI thread never writes
  --------------------------------------
  Three connections, each owned by exactly one thread:

    * ``_wconn`` -- the writer thread. Every INSERT, every DELETE and every WAL
      checkpoint happens here, one at a time, in submission order.
    * ``_rconn`` -- the GUI thread, opened ``query_only``. WAL lets it read
      while the writer writes, so a read never waits for a write.
    * ``HistoryReader`` -- its own thread and its own read-only connection, for
      the drip-feed replay.

  add() used to run the INSERT and the commit inline on the GUI thread, on the
  grounds that a single indexed insert plus a WAL commit is bounded work. It is
  bounded in *rows touched*, which is not the same as bounded in *time*: a
  commit is a WriteFile against the WAL, and a syscall against a loaded
  filesystem takes as long as the filesystem takes. That is the reported bug --
  press Enter, wait several seconds, watch the line appear -- because on the
  send path this sits between putting the line on the wire and drawing it.

  Nor was it the only wait. Two write connections existed (this one and the
  maintenance thread's) and WAL allows one writer, so the two were serialised
  by ``busy_timeout``, set to 15000. Every 500 inserts the maintenance thread
  takes a write transaction to prune and to checkpoint, and for as long as it
  holds it the GUI thread's next insert blocks -- by design, for up to fifteen
  seconds. Collapsing both onto one writer thread removes that contention
  rather than tuning it: there is no second writer left to wait for, and
  busy_timeout stops being load-bearing.

  What the old comment got right, and how it is kept
  --------------------------------------------------
  The visibility requirement is real: a replay bounded by current_max_id() must
  be able to see the row that id names, or a line is written to the table,
  excluded from its own backlog by the cutoff, and never rendered -- a message
  that silently disappears when the window is next opened. It does not follow
  that the *write* has to be synchronous, only that a *read* has to be ordered
  after it. So:

    * Row ids are allocated here, on the calling thread, before the write is
      queued. current_max_id() answers from that counter, so it is correct the
      instant add() returns and costs nothing. One writer thread means the ids
      still reach the table in order, and an explicit id on an AUTOINCREMENT
      column keeps the sequence in step.
    * Every read drains the queue first (flush_pending). Normally the queue is
      empty and the barrier is free. When it is not -- which is exactly when
      the filesystem is misbehaving -- a read waits, where the old code made
      every *message* wait. Reads happen on a join, on opening a window and on
      scrolling to the top; writes happen on every line of traffic.
  """

  # Inserts between maintenance passes. Only a bound on how stale the prune may
  # get: the pass itself costs one indexed probe per channel that received a
  # line, and does nothing at all unless one is over the limit.
  MAINT_INTERVAL = 500

  def __init__(self, db_path, keep_limit=10000):
    self._db_path = db_path
    self._keep = keep_limit
    self._url_keep = 50000
    self._add_count = 0
    self._closed = False
    self._write_errors = 0
    # (network, channel) pairs that have had rows added since the last prune.
    # A prune of anything else can only re-discover that it is still under the
    # limit, which is what made the old "prune every channel every time" pass
    # scan the whole table for nothing 188 channels at a time.
    self._dirty = set()
    self._lock = threading.Lock()   # guards _dirty, _add_count and the ids

    # Schema, migrations and the starting ids, done once on the calling thread
    # before either worker connection exists. This connection is closed again
    # immediately: sqlite3 connections are thread-affine, so the one that
    # creates the schema is not one that may go on being used.
    boot = sqlite3.connect(db_path)
    self._configure_write_conn(boot)
    self._create_schema(boot)
    self._migrate(boot)
    self._next_id = self._max_of(boot, 'history')
    self._next_url_id = self._max_of(boot, 'urls')
    boot.close()

    # The GUI thread's read connection. query_only makes an accidental write
    # fail loudly instead of quietly re-introducing the thing this class was
    # rearranged to stop doing.
    self._rconn = sqlite3.connect(db_path)
    self._rconn.execute("PRAGMA query_only=1")
    self._rconn.execute("PRAGMA busy_timeout=15000")

    # The single writer. The worker count is the whole point: one thread means
    # one writer, which means no lock contention with ourselves.
    self._writer = ThreadPoolExecutor(
      max_workers=1, thread_name_prefix='history-write')
    self._wconn = None            # created lazily inside the writer thread

  # ------------------------------------------------------------------
  # Setup helpers
  # ------------------------------------------------------------------

  @staticmethod
  def _configure_write_conn(conn):
    """Apply the pragmas every writing connection needs."""
    conn.execute("PRAGMA journal_mode=WAL")
    # synchronous=NORMAL: in WAL mode this is the SQLite-recommended setting.
    # Each commit no longer forces an fsync to disk (fsync happens only at
    # checkpoints), which turns the commit-per-insert from a disk sync into a
    # cheap memory write. The DB stays consistent; only the last transaction or
    # two could be lost on an OS/power crash -- fine for a replay cache.
    conn.execute("PRAGMA synchronous=NORMAL")
    # ...but synchronous=NORMAL only moves the fsync to the checkpoint, and by
    # default SQLite runs that checkpoint inline in whichever commit() pushes
    # the WAL past 1000 pages. That used to land on the GUI thread in the
    # middle of a message arriving (9 of the stall samples in me/hangs.log,
    # 2.5s to 9s each). It lands on the writer thread either way now, but the
    # maintenance pass is still the right place for it: a checkpoint blocks
    # this connection's own next insert, so we would rather choose when.
    conn.execute("PRAGMA wal_autocheckpoint=0")
    # There is only one writer now, so this no longer covers a self-inflicted
    # collision. It still covers an outside one -- a second qtpyrc, or a
    # sqlite3 shell open on the same file.
    conn.execute("PRAGMA busy_timeout=15000")

  @staticmethod
  def _create_schema(conn):
    conn.execute("""
      CREATE TABLE IF NOT EXISTS history (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        TEXT    NOT NULL,
        network   TEXT    NOT NULL,
        channel   TEXT    NOT NULL,
        type      TEXT    NOT NULL,
        nick      TEXT,
        text      TEXT,
        prefix    TEXT    DEFAULT ''
      )
    """)
    conn.execute("""
      CREATE INDEX IF NOT EXISTS idx_history_lookup
      ON history (network, channel, id)
    """)
    conn.execute("""
      CREATE TABLE IF NOT EXISTS urls (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        TEXT    NOT NULL,
        network   TEXT    NOT NULL,
        channel   TEXT    NOT NULL,
        nick      TEXT    NOT NULL DEFAULT '',
        host      TEXT    NOT NULL DEFAULT '',
        url       TEXT    NOT NULL
      )
    """)
    conn.execute("""
      CREATE INDEX IF NOT EXISTS idx_urls_lookup
      ON urls (network, ts)
    """)
    conn.commit()

  @staticmethod
  def _max_of(conn, table):
    """Highest id currently in *table*, or 0. The id counter starts here."""
    try:
      row = conn.execute("SELECT MAX(id) FROM %s" % table).fetchone()
      return row[0] if row and row[0] is not None else 0
    except sqlite3.Error:
      return 0

  def _migrate(self, conn):
    """Apply one-time schema/data migrations, tracked via PRAGMA user_version.

    v1: query history used to be keyed by "=nick:ident", but query windows and
    logging are keyed by nick alone, so /query-ing an offline nick (ident
    unknown) never matched its saved history. Re-key those rows to "=nick".
    IRC nicks and idents contain no ':' , so the format is exactly one colon and
    substr up to it yields "=nick". The predicate matches nothing once migrated,
    and user_version gates the (full-scan) UPDATE so it runs only once."""
    try:
      ver = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error:
      return
    if ver < 1:
      try:
        conn.execute(
          "UPDATE history SET channel = substr(channel, 1, instr(channel, ':') - 1) "
          "WHERE channel LIKE '=%:%'")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
      except sqlite3.Error:
        pass

  # ------------------------------------------------------------------
  # Writer thread
  # ------------------------------------------------------------------

  def _wget(self):
    """The writer thread's connection, opened there on first use."""
    if self._wconn is None:
      conn = sqlite3.connect(self._db_path)
      self._configure_write_conn(conn)
      self._wconn = conn
    return self._wconn

  def _submit(self, fn, *args):
    """Queue *fn* on the writer thread. Returns a Future, or None if closed."""
    if self._closed:
      return None
    try:
      return self._writer.submit(fn, *args)
    except RuntimeError:        # executor shut down mid-flight
      return None

  def _w_insert_history(self, row):
    try:
      conn = self._wget()
      conn.execute(
        "INSERT INTO history (id, ts, network, channel, type, nick, text, prefix) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
      conn.commit()
    except sqlite3.Error as e:
      self._note_write_error('history', e)

  def _w_insert_url(self, row):
    try:
      conn = self._wget()
      conn.execute(
        "INSERT INTO urls (id, ts, network, channel, nick, host, url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", row)
      conn.commit()
    except sqlite3.Error as e:
      self._note_write_error('urls', e)

  def _note_write_error(self, table, exc):
    """A lost row is a lost chat line, so it is reported rather than swallowed.

    Only the first is reported: if the disk has gone, every subsequent line
    produces the same message and scrolls the useful one away."""
    self._write_errors += 1
    if self._write_errors == 1:
      dbg(LOG_ERROR, 'History write failed (%s): %s' % (table, exc))

  # ------------------------------------------------------------------
  # Writing (called from the GUI thread; queues, never blocks)
  # ------------------------------------------------------------------

  def add(self, network, channel, event_type, nick=None, text=None, prefix='',
          ts=None):
    """Queue one history row. Does no filesystem work and cannot block.

    The id is allocated here so that current_max_id() is right the moment this
    returns -- see the class docstring for why that matters.

    *ts* is `%Y-%m-%d %H:%M:%S` local, and defaults to now. Callers pass it
    when they know better than the clock: a line replayed from a bouncer
    happened hours ago, and stamping it with the time it *arrived* dates the
    whole backlog to the moment of reconnection. The window already shows the
    server-time (addline's timestamp_override), so without this the stored row
    and the line the user is looking at disagree.
    """
    ts = ts or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with self._lock:
      self._next_id += 1
      rid = self._next_id
      self._dirty.add((network or '', channel))
      self._add_count += 1
      due = self._add_count >= self.MAINT_INTERVAL
      if due:
        self._add_count = 0
      # Submitted under the same lock that allocated the id, so submission
      # order is id order. Only the GUI thread calls add() today, which makes
      # this free -- but "the ids reach the table in order" is an invariant the
      # class docstring leans on, and an invariant that holds only because of
      # who happens to call it is one that breaks silently the day someone
      # logs a line from a worker.
      self._submit(self._w_insert_history,
                   (rid, ts, network or '', channel, event_type, nick, text,
                    prefix or ''))
    # Outside the lock: _schedule_maintenance takes it, and it is not reentrant.
    if due:
      self._schedule_maintenance()

  def add_url(self, network, channel, nick, host, url):
    """Queue one captured URL. Same contract as add()."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with self._lock:
      self._next_url_id += 1
      rid = self._next_url_id
      self._submit(self._w_insert_url,
                   (rid, ts, network or '', channel or '', nick or '',
                    host or '', url))

  def current_max_id(self):
    """Return the id the table currently ends at (0 when empty).

    Free: maintained by add(), never queried. Callers pass it back later as a
    *cutoff_id* to read the table as it was at this moment -- see _id_cap. It
    counts rows that are queued but not yet committed, which is the point;
    flush_pending() below is what makes them readable."""
    return self._next_id

  def flush_pending(self, timeout=30.0):
    """Block until every write queued so far has been committed.

    Every read goes through this. The executor has a single worker and a FIFO
    queue, so a job submitted now runs after everything submitted before it --
    waiting on that job is exactly "the table has caught up with add()".

    Returns False if the wait timed out or the writer is gone, in which case
    the caller reads a table that may be missing its newest rows. That is
    strictly better than the alternatives: hanging the GUI for as long as a
    dead disk takes, or reading without anyone being able to tell."""
    fut = self._submit(lambda: None)
    if fut is None:
      return False
    try:
      fut.result(timeout)
      return True
    except Exception:
      return False

  # ------------------------------------------------------------------
  # Reading (GUI thread, read-only connection, ordered after the writer)
  # ------------------------------------------------------------------

  def get_last(self, network, channel, limit, cutoff_id=None):
    """Return the last *limit* rows for a channel, oldest first.

    *cutoff_id* restricts the read to rows that existed at that id (_id_cap)."""
    self.flush_pending()
    return _q_get_last(self._rconn, network, channel, limit, cutoff_id)

  def replay_bounds(self, network, channel, limit, cutoff_id=None):
    """Return (min_id, max_id) covering the newest *limit* rows for a channel,
    or None if there is no history. Used by the streamed background replay so we
    can walk the window in ascending-id chunks without materialising every row
    up front (get_last() pulls all *limit* rows in one fetchall(), which was the
    single biggest chunk of GUI-thread time during the post-connect replay).

    Two tiny single-row queries (both index-served): MAX(id) gives the newest
    row, and DESC ... LIMIT 1 OFFSET (limit-1) gives the id of the limit-th
    newest row (the lower bound). If fewer than *limit* rows exist the OFFSET
    query returns nothing, so we include everything from id 0.

    *cutoff_id* restricts the bounds to rows that existed at that id (_id_cap)."""
    self.flush_pending()
    return _q_replay_bounds(self._rconn, network, channel, limit, cutoff_id)

  def get_chunk(self, network, channel, after_id, max_id, chunk):
    """Return (rows, last_id) for the next ascending-id slice of a channel's
    history: rows with after_id < id <= max_id, oldest first, at most *chunk*.

    rows are 6-tuples (id, ts, type, nick, text, prefix) ready for rendering;
    last_id is the id to pass as *after_id* on the next call. When fewer than
    *chunk* rows come back the caller has reached max_id and replay is done."""
    self.flush_pending()
    return _q_get_chunk(self._rconn, network, channel, after_id, max_id, chunk)

  def get_before(self, network, channel, before_id, limit):
    """Return (rows, oldest_id) for the *limit* rows older than *before_id*,
    oldest first. Used by the lazy scroll-up loader to prepend older lines."""
    self.flush_pending()
    return _q_get_before(self._rconn, network, channel, before_id, limit)

  def read_conn(self):
    """The GUI thread's read-only connection, with the writer drained first.

    For queries that do not fit the fixed set above -- Find in All Windows
    builds its own SQL and streams a cursor rather than materialising it. The
    connection is query_only, so a caller that writes through it raises instead
    of quietly reintroducing a second writer.

    Reach for one of the named methods before this: they are what the barrier
    and the row shape are documented against."""
    self.flush_pending()
    return self._rconn

  # ------------------------------------------------------------------
  # Maintenance: pruning and WAL checkpoints, on the writer thread
  # ------------------------------------------------------------------

  def _schedule_maintenance(self):
    """Queue a maintenance pass behind the writes already submitted.

    No busy flag and no lock against a concurrent pass: the executor has one
    worker, so a pass cannot overlap an insert or another pass. It can only
    delay them, and delaying a queued insert costs nobody anything -- which is
    the difference between this and the two-connection arrangement it replaced,
    where the same work blocked the GUI thread through busy_timeout."""
    with self._lock:
      dirty, self._dirty = self._dirty, set()
    if dirty:
      self._submit(self._maintain, dirty)

  def _maintain(self, dirty):
    """One maintenance pass. Runs on the writer thread.

    Three things used to happen inline in add(), on the GUI thread, every 500
    inserts, and all three are unbounded in the size of the database:

      * Pruning every channel, discovered with a full index scan
        (``SELECT DISTINCT network, channel``) and pruned with
        ``DELETE ... WHERE id NOT IN (SELECT id ... LIMIT keep)`` -- which
        materialises up to *keep* ids into an ephemeral index per channel.
        Measured on the real history.db: 188 channels, none of them over the
        limit, so the entire pass deleted nothing. It is 19 of the stall
        samples in me/hangs.log, up to 33s.
      * The same pattern over the urls table, with a 50000-row subquery
        (8 more samples).
      * The WAL checkpoint that commit() triggers -- see wal_autocheckpoint
        in _configure_write_conn (9 more samples).

    Here, each of the first two is one indexed probe for the id of the
    keep-th newest row: if there is none the table is under the limit and
    nothing is read, written or committed at all. If there is one, everything
    at or below it goes in a single indexed range delete -- no subquery, no
    ephemeral index, no scan of the rows being kept."""
    try:
      conn = self._wget()
      for network, channel in dirty:
        self._prune_one(conn, network, channel)
      self._prune_urls(conn)
      # PASSIVE does as much of the WAL as it can without ever waiting for a
      # reader, so it cannot stall the GUI thread's reads.
      conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.Error:
      pass                    # a prune that fails is retried by the next pass

  def _prune_one(self, conn, network, channel):
    """Trim one channel to at most self._keep rows. Writer thread only.

    keep_limit comes from config.backscroll_limit, where 0 documented-ly means
    "unlimited" -- so it must mean "never prune" here. Taking it literally
    deletes the channel's entire history every 500 messages, which is what both
    "keep the newest 0 rows" formulations do (the old
    ``NOT IN (SELECT ... LIMIT 0)`` matched every row; an ``OFFSET 0`` probe
    finds the newest row and deletes everything up to and including it)."""
    if self._keep <= 0:
      return
    row = conn.execute(
      "SELECT id FROM history WHERE network = ? AND channel = ? "
      "ORDER BY id DESC LIMIT 1 OFFSET ?",
      (network, channel, self._keep)).fetchone()
    if row is None:
      return                  # under the limit: nothing read, nothing written
    conn.execute("DELETE FROM history WHERE network = ? AND channel = ? "
                 "AND id <= ?", (network, channel, row[0]))
    conn.commit()

  def _prune_urls(self, conn):
    """Trim the urls table to self._url_keep rows. Writer thread only."""
    row = conn.execute("SELECT id FROM urls ORDER BY id DESC LIMIT 1 OFFSET ?",
                       (self._url_keep,)).fetchone()
    if row is None:
      return
    conn.execute("DELETE FROM urls WHERE id <= ?", (row[0],))
    conn.commit()

  # -- URL catcher --

  def search_urls(self, network=None, channel=None, nick=None,
                  host=None, date_from=None, date_to=None, limit=1000):
    """Search captured URLs with optional filters.

    Returns list of (ts, network, channel, nick, host, url) tuples.
    """
    clauses = []
    params = []
    if network:
      clauses.append("network = ?")
      params.append(network)
    if channel:
      clauses.append("channel = ?")
      params.append(channel.lower())
    if nick:
      clauses.append("nick = ?")
      params.append(nick)
    if host:
      # Support wildcards via LIKE
      clauses.append("host LIKE ?")
      params.append(host.replace('*', '%').replace('?', '_'))
    if date_from:
      clauses.append("ts >= ?")
      params.append(date_from + " 00:00:00")
    if date_to:
      clauses.append("ts <= ?")
      params.append(date_to + " 23:59:59")
    where = " AND ".join(clauses) if clauses else "1"
    params.append(limit)
    self.flush_pending()
    cur = self._rconn.execute(
      "SELECT ts, network, channel, nick, host, url FROM urls "
      "WHERE %s ORDER BY id DESC LIMIT ?" % where, params)
    rows = cur.fetchall()
    rows.reverse()
    return rows

  def url_networks(self):
    """Return distinct network names from captured URLs."""
    self.flush_pending()
    cur = self._rconn.execute(
      "SELECT DISTINCT network FROM urls ORDER BY network")
    return [r[0] for r in cur.fetchall()]

  def url_channels(self, network=None):
    """Return distinct channels, optionally filtered by network."""
    self.flush_pending()
    if network:
      cur = self._rconn.execute(
        "SELECT DISTINCT channel FROM urls WHERE network = ? ORDER BY channel",
        (network,))
    else:
      cur = self._rconn.execute(
        "SELECT DISTINCT channel FROM urls ORDER BY channel")
    return [r[0] for r in cur.fetchall()]

  def close(self):
    """Drain the writer, fold the WAL back into the DB, and close.

    The WAL is only checkpointed by the maintenance pass now (see
    wal_autocheckpoint), so shutdown is the one place that has to make sure it
    does not grow without bound across runs. TRUNCATE empties it, and it is
    allowed to take its time because the GUI is going away anyway.

    _closed is set first so nothing new is queued, then the executor is drained
    -- not cancelled. What is queued is chat lines that have already been shown
    to the user, and that would otherwise be missing from the backlog next
    time."""
    if self._closed:
      return
    self._closed = True

    def _finish():
      # Runs on the writer thread, after every queued insert: sqlite3
      # connections are thread-affine, so this is the only place its own
      # connection can be checkpointed and closed.
      if self._wconn is None:
        return
      try:
        self._wconn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
      except sqlite3.Error:
        pass
      try:
        self._wconn.close()
      except sqlite3.Error:
        pass
      self._wconn = None

    try:
      self._writer.submit(_finish)
    except RuntimeError:
      pass
    # Not cancel_futures: that would drop the queued inserts, and the close job
    # along with them.
    self._writer.shutdown(wait=True)
    try:
      self._rconn.close()
    except sqlite3.Error:
      pass
