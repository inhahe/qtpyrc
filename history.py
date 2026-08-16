# history.py - SQLite-backed channel history for session replay

import asyncio
import sqlite3
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
  """Persistent channel history stored in SQLite."""

  def __init__(self, db_path, keep_limit=10000):
    self._conn = sqlite3.connect(db_path)
    self._conn.execute("PRAGMA journal_mode=WAL")
    # synchronous=NORMAL: in WAL mode this is the SQLite-recommended setting.
    # Each commit no longer forces an fsync to disk (fsync happens only at
    # checkpoints), which turns our commit-per-insert in add()/add_url() from a
    # disk sync on the GUI thread into a cheap memory write. The DB stays
    # consistent; only the last transaction or two could be lost on an OS/power
    # crash -- fine for a replay cache. This was ~17% of GUI-thread time.
    self._conn.execute("PRAGMA synchronous=NORMAL")
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
    # Prune every 500 inserts to keep the DB bounded
    self._add_count += 1
    if self._add_count >= 500:
      self._add_count = 0
      self._prune_all()
      self.prune_urls()

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

  def _prune_all(self):
    """Prune all channels to keep at most self._keep rows each."""
    cur = self._conn.execute(
      "SELECT DISTINCT network, channel FROM history")
    pairs = cur.fetchall()
    for network, channel in pairs:
      self._conn.execute(
        "DELETE FROM history WHERE network = ? AND channel = ? AND id NOT IN "
        "(SELECT id FROM history WHERE network = ? AND channel = ? "
        "ORDER BY id DESC LIMIT ?)",
        (network, channel, network, channel, self._keep))
    self._conn.commit()

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

  def prune_urls(self):
    """Keep only the most recent urls."""
    self._conn.execute(
      "DELETE FROM urls WHERE id NOT IN "
      "(SELECT id FROM urls ORDER BY id DESC LIMIT ?)",
      (self._url_keep,))
    self._conn.commit()

  def close(self):
    self._conn.close()
