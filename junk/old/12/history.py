# history.py - SQLite-backed channel history for session replay

import sqlite3
from datetime import datetime


class HistoryDB:
  """Persistent channel history stored in SQLite."""

  def __init__(self, db_path, keep_limit=10000):
    self._conn = sqlite3.connect(db_path)
    self._conn.execute("PRAGMA journal_mode=WAL")
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
    self._conn.execute(
      "INSERT INTO history (ts, network, channel, type, nick, text, prefix) "
      "VALUES (?, ?, ?, ?, ?, ?, ?)",
      (ts, network or '', channel, event_type, nick, text, prefix or ''))
    self._conn.commit()
    # Prune every 500 inserts to keep the DB bounded
    self._add_count += 1
    if self._add_count >= 500:
      self._add_count = 0
      self._prune_all()
      self.prune_urls()

  def get_last(self, network, channel, limit):
    """Return the last *limit* rows for a channel, oldest first."""
    cur = self._conn.execute(
      "SELECT ts, type, nick, text, COALESCE(prefix, '') FROM history "
      "WHERE network = ? AND channel = ? "
      "ORDER BY id DESC LIMIT ?",
      (network or '', channel, limit))
    rows = cur.fetchall()
    rows.reverse()
    return rows  # [(ts, type, nick, text, prefix), ...]

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
    self._conn.execute(
      "INSERT INTO urls (ts, network, channel, nick, host, url) "
      "VALUES (?, ?, ?, ?, ?, ?)",
      (ts, network or '', channel or '', nick or '', host or '', url))
    self._conn.commit()

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
