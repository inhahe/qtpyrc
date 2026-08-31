"""History DB: pruning and WAL upkeep must be correct *and* off the GUI thread.

Pruning used to run inline in ``HistoryDB.add()`` every 500 inserts, on the GUI
thread, and it was the single most frequent hang in the client -- 30 of the 39
history-related stall samples in me/hangs.log, the worst 33 seconds long.  Three
things were wrong with it, and this pins all three:

  * It pruned *every* channel on every pass, found with a full index scan
    (``SELECT DISTINCT network, channel``).  On the real database that was 188
    channels, none of them over the limit, so the whole pass deleted nothing.
  * It pruned with ``DELETE ... WHERE id NOT IN (SELECT id ... LIMIT keep)``,
    which materialises up to *keep* ids into an ephemeral index per channel.
  * Its commits carried SQLite's automatic WAL checkpoint, so the fsync landed
    on the GUI thread too.

What must not break in exchange: a channel over the limit really is trimmed, to
exactly the newest ``keep`` rows, and the rows that survive are the newest ones.

Usage:
  python tests/test_history_maint.py     # from the qtpyrc root directory
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import history as history_mod

KEEP = 200
failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


def drain(db, timeout=10.0):
  """Wait for the maintenance thread to finish whatever it is doing."""
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    with db._maint_lock:
      if not db._maint_busy:
        return True
    time.sleep(0.005)
  return False


def count(db, network, channel):
  return db._conn.execute(
    'SELECT COUNT(*) FROM history WHERE network = ? AND channel = ?',
    (network, channel)).fetchone()[0]


def run(tmpdir):
  path = os.path.join(tmpdir, 'h.db')
  db = history_mod.HistoryDB(path, keep_limit=KEEP)

  # ------------------------------------------------------------------ 1
  # add() must not do the pruning itself. The maintenance thread exists so
  # that unbounded disk work cannot land on the GUI thread, which is the
  # thread every caller of add() is on.
  main_thread = threading.get_ident()
  seen = []
  real_prune_one = history_mod.HistoryDB._prune_one

  def watched_prune_one(self, conn, network, channel):
    seen.append(threading.get_ident())
    return real_prune_one(self, conn, network, channel)

  history_mod.HistoryDB._prune_one = watched_prune_one
  try:
    # Two channels: one that will end up well over the limit, one that stays
    # under it. Enough inserts to cross MAINT_INTERVAL several times.
    total = db.MAINT_INTERVAL * 3
    for i in range(total):
      db.add('net', '#big', 'message', 'alice', 'big %d' % i)
    for i in range(50):
      db.add('net', '#small', 'message', 'bob', 'small %d' % i)
    check(drain(db), 'the maintenance pass never finished')
    check(seen, 'nothing was pruned at all, so this test proves nothing')
    check(all(t != main_thread for t in seen),
          'pruning ran on the calling thread -- in the client that is the GUI '
          'thread, which is exactly what must not happen')
  finally:
    history_mod.HistoryDB._prune_one = real_prune_one

  # ------------------------------------------------------------------ 2
  # ...and it must actually prune. Scheduling is throttled (one pass
  # outstanding at a time), so the last inserts may not be covered yet; one
  # more pass settles it.
  db._dirty.add(('net', '#big'))
  db._schedule_maintenance()
  check(drain(db), 'the final maintenance pass never finished')
  n = count(db, 'net', '#big')
  check(n == KEEP, 'an over-limit channel was left with %d rows, not the %d it '
                   'is meant to keep' % (n, KEEP))
  check(count(db, 'net', '#small') == 50,
        'an under-limit channel lost rows it should have kept')

  # The rows kept must be the *newest* ones.
  rows = db.get_last('net', '#big', KEEP)   # (id, ts, type, nick, text, prefix)
  check(rows[-1][4] == 'big %d' % (db.MAINT_INTERVAL * 3 - 1),
        'the newest line did not survive the prune (last row is %r)'
        % (rows[-1][4],))
  check(rows[0][4] == 'big %d' % (db.MAINT_INTERVAL * 3 - KEEP),
        'the prune kept the wrong window of rows (oldest kept is %r)'
        % (rows[0][4],))

  # ------------------------------------------------------------------ 3
  # A pass over channels that are all under the limit must issue no DELETE and
  # no commit. This is the case that ran 188 pointless DELETEs -- each building
  # an ephemeral index of up to *keep* ids -- over the real database, every 500
  # messages, and it is what the stalls were made of. Watch the SQL rather than
  # the row counts: those DELETEs removed nothing either, and that was the
  # point -- the cost was in finding out.
  sql = []
  real_get_conn = history_mod.HistoryDB._maint_get_conn

  class Watched:
    def __init__(self, conn):
      self._c = conn

    def execute(self, s, *a):
      sql.append(s)
      return self._c.execute(s, *a)

    def commit(self):
      sql.append('COMMIT')
      return self._c.commit()

    def __getattr__(self, name):
      return getattr(self._c, name)

  history_mod.HistoryDB._maint_get_conn = lambda self: Watched(
    real_get_conn(self))
  try:
    db._dirty.add(('net', '#small'))
    db._schedule_maintenance()
    check(drain(db), 'the no-op maintenance pass never finished')
  finally:
    history_mod.HistoryDB._maint_get_conn = real_get_conn
  check(sql, 'the maintenance pass issued no SQL at all, so this test proves '
             'nothing')
  wrote = [s for s in sql if 'DELETE' in s.upper() or s == 'COMMIT']
  check(not wrote,
        'a maintenance pass with nothing over the limit still wrote to the '
        'database: %r' % (wrote,))
  # ...and it must look only at the channels that received a line, not go
  # hunting for every channel that has ever existed.
  scans = [s for s in sql if 'DISTINCT' in s.upper()]
  check(not scans,
        'the maintenance pass scanned the whole table for channels to consider '
        'instead of using the ones that were written to: %r' % (scans,))

  # ------------------------------------------------------------------ 4
  # The GUI connection must never checkpoint: SQLite does that inline in
  # commit(), and the fsync it carries is seconds of frozen GUI.
  auto = db._conn.execute('PRAGMA wal_autocheckpoint').fetchone()[0]
  check(auto == 0,
        'the GUI-thread connection still has automatic WAL checkpointing on '
        '(threshold %r), so a commit can fsync on the GUI thread' % (auto,))

  # ------------------------------------------------------------------ 5
  # keep_limit comes straight from config.backscroll_limit, where 0 means
  # "unlimited". Read literally it means "keep the newest zero rows", i.e.
  # delete the entire history every 500 messages -- which is what both the old
  # NOT IN (... LIMIT 0) and a naive OFFSET 0 probe do.
  unlimited = history_mod.HistoryDB(os.path.join(tmpdir, 'u.db'), keep_limit=0)
  try:
    for i in range(20):
      unlimited.add('net', '#keep', 'message', 'alice', 'line %d' % i)
    unlimited._dirty.add(('net', '#keep'))
    unlimited._schedule_maintenance()
    check(drain(unlimited), 'the unlimited-keep maintenance pass never finished')
    n = count(unlimited, 'net', '#keep')
    check(n == 20,
          'backscroll_limit 0 means unlimited, but pruning left %d of 20 rows'
          % n)
  finally:
    unlimited.close()

  # ------------------------------------------------------------------ 6
  # close() must leave nothing behind: the maintenance thread stopped, its
  # connection closed on its own thread, and the WAL folded back in.
  db.close()
  check(not db._maint._threads or all(not t.is_alive() for t in db._maint._threads),
        'the maintenance thread outlived close()')
  wal = path + '-wal'
  size = os.path.getsize(wal) if os.path.exists(wal) else 0
  check(size == 0,
        'close() left a %d-byte WAL behind instead of checkpointing it' % size)

  # And the data is still there and readable by a fresh connection.
  conn = sqlite3.connect(path)
  n = conn.execute('SELECT COUNT(*) FROM history').fetchone()[0]
  conn.close()
  check(n == KEEP + 50,
        'reopening the database found %d rows, expected %d' % (n, KEEP + 50))


def main():
  tmpdir = tempfile.mkdtemp(prefix='qtpyrc-histtest-')
  try:
    run(tmpdir)
  except Exception:
    import traceback
    traceback.print_exc()
    return 1
  finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('All history maintenance checks passed.')
  return 0


sys.exit(main())
