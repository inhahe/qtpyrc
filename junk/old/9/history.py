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
        text      TEXT
      )
    """)
    self._conn.execute("""
      CREATE INDEX IF NOT EXISTS idx_history_lookup
      ON history (network, channel, id)
    """)
    self._conn.commit()
    self._keep = keep_limit
    self._add_count = 0

  def add(self, network, channel, event_type, nick=None, text=None):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    self._conn.execute(
      "INSERT INTO history (ts, network, channel, type, nick, text) "
      "VALUES (?, ?, ?, ?, ?, ?)",
      (ts, network or '', channel, event_type, nick, text))
    self._conn.commit()
    # Prune every 500 inserts to keep the DB bounded
    self._add_count += 1
    if self._add_count >= 500:
      self._add_count = 0
      self._prune_all()

  def get_last(self, network, channel, limit):
    """Return the last *limit* rows for a channel, oldest first."""
    cur = self._conn.execute(
      "SELECT ts, type, nick, text FROM history "
      "WHERE network = ? AND channel = ? "
      "ORDER BY id DESC LIMIT ?",
      (network or '', channel, limit))
    rows = cur.fetchall()
    rows.reverse()
    return rows  # [(ts, type, nick, text), ...]

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

  def close(self):
    self._conn.close()
