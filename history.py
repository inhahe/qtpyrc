# history.py - SQLite-backed channel history for session replay

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


# ---------------------------------------------------------------------------
# Shared read queries. These take an explicit connection so the exact same SQL
# is used by both the GUI-thread writer connection (HistoryDB) and the
# background-thread reader connection (HistoryReader) -- no risk of the two
# drifting apart.
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


def _q_get_last(conn, network, channel, limit, cutoff_id=None):
  cap, cap_params = _id_cap(cutoff_id)
  cur = conn.execute(
    "SELECT ts, type, nick, text, COALESCE(prefix, '') FROM history "
    "WHERE network = ? AND channel = ?" + cap + " "
    "ORDER BY id DESC LIMIT ?",
    (network or '', channel) + cap_params + (limit,))
  rows = cur.fetchall()
  rows.reverse()
  return rows  # [(ts, type, nick, text, prefix), ...]


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
  next batch of older lines. rows are 5-tuples (ts, type, nick, text, prefix);
  oldest_id is the smallest id in the batch (pass it as *before_id* on the next
  call). When no older rows exist, returns ([], before_id)."""
  cur = conn.execute(
    "SELECT id, ts, type, nick, text, COALESCE(prefix, '') FROM history "
    "WHERE network = ? AND channel = ? AND id < ? "
    "ORDER BY id DESC LIMIT ?",
    (network or '', channel, before_id, limit))
  raw = cur.fetchall()
  if not raw:
    return [], before_id
  raw.reverse()  # oldest first
  rows = [r[1:] for r in raw]
  return rows, raw[0][0]


def _q_get_chunk(conn, network, channel, after_id, max_id, chunk):
  cur = conn.execute(
    "SELECT id, ts, type, nick, text, COALESCE(prefix, '') FROM history "
    "WHERE network = ? AND channel = ? AND id > ? AND id <= ? "
    "ORDER BY id ASC LIMIT ?",
    (network or '', channel, after_id, max_id, chunk))
  raw = cur.fetchall()
  if not raw:
    return [], after_id
  rows = [r[1:] for r in raw]
  return rows, raw[-1][0]


class HistoryReader:
  """Read-only history access that runs its SQLite queries on a dedicated
  background thread, so the GUI thread never blocks on disk I/O. WAL mode lets
  this reader connection run concurrently with the GUI-thread writer connection
  in HistoryDB. A single worker thread keeps the connection thread-affine
  (sqlite3 connections must not be shared across threads).

  The async wrappers below are meant to be awaited from the GUI thread (which
  is where the qasync asyncio loop runs); each hops to the worker thread for the
  actual query and returns the rows back on the GUI thread for rendering."""

  def __init__(self, db_path):
    self._db_path = db_path
    self._executor = ThreadPoolExecutor(
      max_workers=1, thread_name_prefix='history-reader')
    self._conn = None  # created lazily inside the worker thread

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
    return _q_replay_bounds(self._get_conn(), network, channel, limit, cutoff_id)

  def _do_get_chunk(self, network, channel, after_id, max_id, chunk):
    return _q_get_chunk(self._get_conn(), network, channel, after_id, max_id, chunk)

  def _do_get_last(self, network, channel, limit, cutoff_id):
    return _q_get_last(self._get_conn(), network, channel, limit, cutoff_id)

  def _do_get_before(self, network, channel, before_id, limit):
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

  The GUI thread owns this connection and writes through it directly: a single
  indexed INSERT plus a WAL commit is bounded work, and every caller of add()
  reads back through the same connection (a replay bounded by current_max_id
  must see the row that id names), so making the insert itself asynchronous
  would trade a bounded cost for a visibility race.

  What is *not* bounded -- pruning the tables and checkpointing the WAL -- runs
  on the maintenance thread below instead.  Both used to run inline in add(),
  and between them they account for 30 of the 39 history stall samples in
  me/hangs.log (up to 33s of frozen GUI); see _maintain()."""

  # Inserts between maintenance passes. Only a bound on how stale the prune may
  # get: the pass itself costs one indexed probe per channel that received a
  # line, and does nothing at all unless one is over the limit.
  MAINT_INTERVAL = 500

  def __init__(self, db_path, keep_limit=10000):
    self._db_path = db_path
    self._conn = sqlite3.connect(db_path)
    self._conn.execute("PRAGMA journal_mode=WAL")
    # synchronous=NORMAL: in WAL mode this is the SQLite-recommended setting.
    # Each commit no longer forces an fsync to disk (fsync happens only at
    # checkpoints), which turns our commit-per-insert in add()/add_url() from a
    # disk sync on the GUI thread into a cheap memory write. The DB stays
    # consistent; only the last transaction or two could be lost on an OS/power
    # crash -- fine for a replay cache. This was ~17% of GUI-thread time.
    self._conn.execute("PRAGMA synchronous=NORMAL")
    # ...but synchronous=NORMAL only moves the fsync to the checkpoint, and by
    # default SQLite runs that checkpoint inline in whichever commit() pushes
    # the WAL past 1000 pages -- i.e. on the GUI thread, in the middle of a
    # message arriving. That is 9 of the stall samples in me/hangs.log (2.5s to
    # 9s each). Turn the automatic checkpoint off here and let the maintenance
    # thread take it, where a multi-second fsync costs nothing.
    self._conn.execute("PRAGMA wal_autocheckpoint=0")
    # Two write connections now exist (this one and the maintenance thread's),
    # and WAL allows only one writer at a time. The maintenance writes are short
    # indexed range deletes, so waiting is far better than failing; without a
    # timeout an overlap would raise "database is locked" and lose a line.
    self._conn.execute("PRAGMA busy_timeout=15000")
    self._conn.execute("""
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
    self._conn.execute("""
      CREATE INDEX IF NOT EXISTS idx_history_lookup
      ON history (network, channel, id)
    """)
    # URL catcher table
    self._conn.execute("""
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
    self._conn.execute("""
      CREATE INDEX IF NOT EXISTS idx_urls_lookup
      ON urls (network, ts)
    """)
    self._conn.commit()
    self._migrate()
    self._keep = keep_limit
    self._url_keep = 50000
    self._add_count = 0
    # --- maintenance thread (pruning + WAL checkpoints) ---
    self._maint = ThreadPoolExecutor(
      max_workers=1, thread_name_prefix='history-maint')
    self._maint_conn = None       # created lazily inside the worker thread
    self._maint_lock = threading.Lock()
    self._maint_busy = False      # a pass is queued or running
    self._closed = False
    # (network, channel) pairs that have had rows added since the last prune.
    # A prune of anything else can only re-discover that it is still under the
    # limit, which is what made the old "prune every channel every time" pass
    # scan the whole table for nothing 188 channels at a time.
    self._dirty = set()
    # Highest row id in the table, kept current by add() so current_max_id() is
    # free to call from the GUI thread (see _id_cap for what it is used for).
    # Read once here because the table is usually non-empty at startup and no
    # insert has happened yet to tell us where it left off.
    try:
      row = self._conn.execute("SELECT MAX(id) FROM history").fetchone()
      self._max_id = (row[0] if row and row[0] is not None else 0)
    except sqlite3.Error:
      self._max_id = 0

  def _migrate(self):
    """Apply one-time schema/data migrations, tracked via PRAGMA user_version.

    v1: query history used to be keyed by "=nick:ident", but query windows and
    logging are keyed by nick alone, so /query-ing an offline nick (ident
    unknown) never matched its saved history. Re-key those rows to "=nick".
    IRC nicks and idents contain no ':' , so the format is exactly one colon and
    substr up to it yields "=nick". The predicate matches nothing once migrated,
    and user_version gates the (full-scan) UPDATE so it runs only once."""
    try:
      ver = self._conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error:
      return
    if ver < 1:
      try:
        self._conn.execute(
          "UPDATE history SET channel = substr(channel, 1, instr(channel, ':') - 1) "
          "WHERE channel LIKE '=%:%'")
        self._conn.execute("PRAGMA user_version = 1")
        self._conn.commit()
      except sqlite3.Error:
        pass

  def add(self, network, channel, event_type, nick=None, text=None, prefix=''):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
      cur = self._conn.execute(
        "INSERT INTO history (ts, network, channel, type, nick, text, prefix) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, network or '', channel, event_type, nick, text, prefix or ''))
      self._conn.commit()
      if cur.lastrowid:
        self._max_id = cur.lastrowid
    except sqlite3.ProgrammingError:
      return  # DB already closed during shutdown
    self._dirty.add((network or '', channel))
    self._add_count += 1
    if self._add_count >= self.MAINT_INTERVAL:
      self._add_count = 0
      self._schedule_maintenance()

  def current_max_id(self):
    """Return the id the table currently ends at (0 when empty).

    Cheap: maintained by add(), not queried. Callers pass it back later as a
    *cutoff_id* to read the table as it was at this moment -- see _id_cap."""
    return self._max_id

  def get_last(self, network, channel, limit, cutoff_id=None):
    """Return the last *limit* rows for a channel, oldest first.

    *cutoff_id* restricts the read to rows that existed at that id (_id_cap)."""
    return _q_get_last(self._conn, network, channel, limit, cutoff_id)

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
    return _q_replay_bounds(self._conn, network, channel, limit, cutoff_id)

  def get_chunk(self, network, channel, after_id, max_id, chunk):
    """Return (rows, last_id) for the next ascending-id slice of a channel's
    history: rows with after_id < id <= max_id, oldest first, at most *chunk*.

    rows are 5-tuples (ts, type, nick, text, prefix) ready for rendering;
    last_id is the id to pass as *after_id* on the next call. When fewer than
    *chunk* rows come back the caller has reached max_id and replay is done."""
    return _q_get_chunk(self._conn, network, channel, after_id, max_id, chunk)

  def get_before(self, network, channel, before_id, limit):
    """Return (rows, oldest_id) for the *limit* rows older than *before_id*,
    oldest first. Used by the lazy scroll-up loader to prepend older lines."""
    return _q_get_before(self._conn, network, channel, before_id, limit)

  # ------------------------------------------------------------------
  # Maintenance: pruning and WAL checkpoints, on a background thread
  # ------------------------------------------------------------------

  def _schedule_maintenance(self):
    """Queue a maintenance pass, unless one is already queued or running.

    Called from the GUI thread. Never waits: if the previous pass is still
    going, the rows this one would have pruned simply stay until the next
    call, which is what the keep-limit already tolerates."""
    if self._closed:
      return
    with self._maint_lock:
      if self._maint_busy:
        return
      self._maint_busy = True
      dirty = self._dirty
      self._dirty = set()
    try:
      self._maint.submit(self._maintain, dirty)
    except RuntimeError:      # executor shut down mid-flight
      with self._maint_lock:
        self._maint_busy = False

  def _maint_get_conn(self):
    """Return the maintenance thread's own write connection (created there)."""
    if self._maint_conn is None:
      conn = sqlite3.connect(self._db_path)
      conn.execute("PRAGMA synchronous=NORMAL")
      # This connection is the one allowed to checkpoint, and it is the one
      # that can afford to: it is not the GUI thread.
      conn.execute("PRAGMA busy_timeout=15000")
      self._maint_conn = conn
    return self._maint_conn

  def _maintain(self, dirty):
    """One maintenance pass. Runs on the maintenance thread.

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
        in __init__ (9 more samples).

    Here, each of the first two is one indexed probe for the id of the
    keep-th newest row: if there is none the table is under the limit and
    nothing is read, written or committed at all. If there is one, everything
    at or below it goes in a single indexed range delete -- no subquery, no
    ephemeral index, no scan of the rows being kept."""
    try:
      conn = self._maint_get_conn()
      for network, channel in dirty:
        self._prune_one(conn, network, channel)
      self._prune_urls(conn)
      # PASSIVE does as much of the WAL as it can without ever waiting for a
      # reader or blocking a writer, so this cannot stall the GUI's inserts.
      conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.Error:
      pass                    # a prune that fails is retried by the next pass
    finally:
      with self._maint_lock:
        self._maint_busy = False

  def _prune_one(self, conn, network, channel):
    """Trim one channel to at most self._keep rows. Maintenance thread only.

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
    """Trim the urls table to self._url_keep rows. Maintenance thread only."""
    row = conn.execute("SELECT id FROM urls ORDER BY id DESC LIMIT 1 OFFSET ?",
                       (self._url_keep,)).fetchone()
    if row is None:
      return
    conn.execute("DELETE FROM urls WHERE id <= ?", (row[0],))
    conn.commit()

  # -- URL catcher --

  def add_url(self, network, channel, nick, host, url):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
      self._conn.execute(
        "INSERT INTO urls (ts, network, channel, nick, host, url) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, network or '', channel or '', nick or '', host or '', url))
      self._conn.commit()
    except sqlite3.ProgrammingError:
      return

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
    cur = self._conn.execute(
      "SELECT ts, network, channel, nick, host, url FROM urls "
      "WHERE %s ORDER BY id DESC LIMIT ?" % where, params)
    rows = cur.fetchall()
    rows.reverse()
    return rows

  def url_networks(self):
    """Return distinct network names from captured URLs."""
    cur = self._conn.execute(
      "SELECT DISTINCT network FROM urls ORDER BY network")
    return [r[0] for r in cur.fetchall()]

  def url_channels(self, network=None):
    """Return distinct channels, optionally filtered by network."""
    if network:
      cur = self._conn.execute(
        "SELECT DISTINCT channel FROM urls WHERE network = ? ORDER BY channel",
        (network,))
    else:
      cur = self._conn.execute(
        "SELECT DISTINCT channel FROM urls ORDER BY channel")
    return [r[0] for r in cur.fetchall()]

  def close(self):
    """Stop maintenance, fold the WAL back into the DB, and close.

    The WAL is only checkpointed by the maintenance thread now (see
    wal_autocheckpoint in __init__), so shutdown is the one place that has to
    make sure it does not grow without bound across runs. TRUNCATE empties it;
    it is allowed to take its time here because the GUI is going away anyway.
    Bounded by the executor's own wait, so a hung disk cannot hang the exit."""
    self._closed = True

    def _close_maint_conn():
      # Must run on the maintenance thread: sqlite3 connections are
      # thread-affine, so closing this one from here would only raise.
      if self._maint_conn is not None:
        try:
          self._maint_conn.close()
        except sqlite3.Error:
          pass
        self._maint_conn = None

    try:
      self._maint.submit(_close_maint_conn)
    except RuntimeError:
      pass
    # Waits only for a pass already under way -- one indexed range delete and a
    # PASSIVE checkpoint, both bounded -- because _closed now stops new ones and
    # _maint_busy allows at most one outstanding. Not cancel_futures: that would
    # cancel the close job queued just above and leak the connection.
    self._maint.shutdown(wait=True)
    try:
      self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
      pass
    self._conn.close()
