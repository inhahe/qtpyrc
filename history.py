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

def _q_get_last(conn, network, channel, limit):
  cur = conn.execute(
    "SELECT ts, type, nick, text, COALESCE(prefix, '') FROM history "
    "WHERE network = ? AND channel = ? "
    "ORDER BY id DESC LIMIT ?",
    (network or '', channel, limit))
  rows = cur.fetchall()
  rows.reverse()
  return rows  # [(ts, type, nick, text, prefix), ...]


def _q_replay_bounds(conn, network, channel, limit):
  net = network or ''
  row = conn.execute(
    "SELECT MAX(id) FROM history WHERE network = ? AND channel = ?",
    (net, channel)).fetchone()
  max_id = row[0] if row else None
  if max_id is None:
    return None  # no history for this channel
  row = conn.execute(
    "SELECT id FROM history WHERE network = ? AND channel = ? "
    "ORDER BY id DESC LIMIT 1 OFFSET ?",
    (net, channel, max(0, int(limit) - 1))).fetchone()
  min_id = row[0] if row else 0
  return (min_id, max_id)


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
  def _do_replay_bounds(self, network, channel, limit):
    return _q_replay_bounds(self._get_conn(), network, channel, limit)

  def _do_get_chunk(self, network, channel, after_id, max_id, chunk):
    return _q_get_chunk(self._get_conn(), network, channel, after_id, max_id, chunk)

  def _do_get_last(self, network, channel, limit):
    return _q_get_last(self._get_conn(), network, channel, limit)

  # --- async wrappers (await from the GUI thread) ---------------------------
  async def replay_bounds(self, network, channel, limit):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
      self._executor, self._do_replay_bounds, network, channel, limit)

  async def get_chunk(self, network, channel, after_id, max_id, chunk):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
      self._executor, self._do_get_chunk, network, channel, after_id, max_id, chunk)

  async def get_last(self, network, channel, limit):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
      self._executor, self._do_get_last, network, channel, limit)

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
    self._keep = keep_limit
    self._url_keep = 50000
    self._add_count = 0

  def add(self, network, channel, event_type, nick=None, text=None, prefix=''):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
      self._conn.execute(
        "INSERT INTO history (ts, network, channel, type, nick, text, prefix) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, network or '', channel, event_type, nick, text, prefix or ''))
      self._conn.commit()
    except sqlite3.ProgrammingError:
      return  # DB already closed during shutdown
    # Prune every 500 inserts to keep the DB bounded
    self._add_count += 1
    if self._add_count >= 500:
      self._add_count = 0
      self._prune_all()
      self.prune_urls()

  def get_last(self, network, channel, limit):
    """Return the last *limit* rows for a channel, oldest first."""
    return _q_get_last(self._conn, network, channel, limit)

  def replay_bounds(self, network, channel, limit):
    """Return (min_id, max_id) covering the newest *limit* rows for a channel,
    or None if there is no history. Used by the streamed background replay so we
    can walk the window in ascending-id chunks without materialising every row
    up front (get_last() pulls all *limit* rows in one fetchall(), which was the
    single biggest chunk of GUI-thread time during the post-connect replay).

    Two tiny single-row queries (both index-served): MAX(id) gives the newest
    row, and DESC ... LIMIT 1 OFFSET (limit-1) gives the id of the limit-th
    newest row (the lower bound). If fewer than *limit* rows exist the OFFSET
    query returns nothing, so we include everything from id 0."""
    return _q_replay_bounds(self._conn, network, channel, limit)

  def get_chunk(self, network, channel, after_id, max_id, chunk):
    """Return (rows, last_id) for the next ascending-id slice of a channel's
    history: rows with after_id < id <= max_id, oldest first, at most *chunk*.

    rows are 5-tuples (ts, type, nick, text, prefix) ready for rendering;
    last_id is the id to pass as *after_id* on the next call. When fewer than
    *chunk* rows come back the caller has reached max_id and replay is done."""
    return _q_get_chunk(self._conn, network, channel, after_id, max_id, chunk)

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
