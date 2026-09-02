"""History DB: every write is off the GUI thread, and nothing is lost for it.

qtpyrc runs asyncio on the Qt GUI thread, so anything that thread waits for is
a freeze of the whole client. ``HistoryDB`` used to do two kinds of waiting
there, and both were reported as "I press Enter and it hangs for a few seconds
before my line appears":

  * **The insert itself.** add() ran INSERT + commit inline, on the grounds
    that a single indexed insert is bounded work. It is bounded in rows, not in
    time: the commit is a WriteFile against the WAL, and a filesystem under
    load takes as long as it takes.
  * **Waiting for the other writer.** Pruning and WAL checkpoints ran on a
    second connection, and WAL allows one writer at a time, so the two were
    serialised by ``busy_timeout`` -- set to 15000. Every 500 inserts the
    maintenance pass took a write transaction, and until it let go the GUI
    thread's next insert blocked. By design, for up to fifteen seconds.

Before that, pruning ran inline in add() as well, and was the single most
frequent hang in the client -- 30 of the 39 history stall samples in
me/hangs.log, the worst 33 seconds. Three things were wrong with it, and this
still pins all three:

  * It pruned *every* channel on every pass, found with a full index scan
    (``SELECT DISTINCT network, channel``).  On the real database that was 188
    channels, none of them over the limit, so the whole pass deleted nothing.
  * It pruned with ``DELETE ... WHERE id NOT IN (SELECT id ... LIMIT keep)``,
    which materialises up to *keep* ids into an ephemeral index per channel.
  * Its commits carried SQLite's automatic WAL checkpoint, so the fsync landed
    on the GUI thread too.

What must not break in exchange -- and this is the half that makes an
asynchronous write dangerous rather than merely fast:

  * A row is visible to a read as soon as add() returns. current_max_id() is
    used as a replay cutoff, so a row counted by it but not yet in the table
    would be excluded from its own backlog and never drawn again: a message
    that vanishes when the window is next opened.
  * Nothing queued is dropped at close().
  * A channel over the limit really is trimmed, to exactly the newest ``keep``
    rows, and the rows that survive are the newest ones.

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
  """Wait for the writer thread to finish everything queued."""
  return db.flush_pending(timeout)


def count(db, network, channel):
  return db.read_conn().execute(
    'SELECT COUNT(*) FROM history WHERE network = ? AND channel = ?',
    (network, channel)).fetchone()[0]


def run(tmpdir):
  path = os.path.join(tmpdir, 'h.db')
  db = history_mod.HistoryDB(path, keep_limit=KEEP)
  main_thread = threading.get_ident()

  # ------------------------------------------------------------------ 1
  # The INSERT must happen on the writer thread, not the caller's. The caller
  # is the GUI thread, and this is the whole reason the class was rearranged.
  insert_threads = []
  real_insert = history_mod.HistoryDB._w_insert_history

  def watched_insert(self, row):
    insert_threads.append(threading.get_ident())
    return real_insert(self, row)

  history_mod.HistoryDB._w_insert_history = watched_insert
  try:
    db.add('net', '#thread', 'message', 'alice', 'hello')
    check(drain(db), 'the writer never drained')
  finally:
    history_mod.HistoryDB._w_insert_history = real_insert
  check(insert_threads, 'no insert happened at all, so this test proves nothing')
  check(all(t != main_thread for t in insert_threads),
        'the INSERT ran on the calling thread -- in the client that is the GUI '
        'thread, which is the freeze this class exists to avoid')

  # ------------------------------------------------------------------ 2
  # ...and add() must return without waiting for it. A filesystem that takes
  # seconds to accept a write is the reported bug; the client must not care.
  # STALL is deliberately short and QUEUED deliberately small. A regression
  # makes every one of these adds take STALL seconds, so the whole section has
  # to stay inside a sane runtime -- a test that hangs for four minutes instead
  # of failing is one nobody runs twice. STALL * (QUEUED + 1) is the worst case,
  # and the margin against the assertion below is still three orders of
  # magnitude.
  STALL = 2.0
  QUEUED = 5
  gate = threading.Event()
  real_insert = history_mod.HistoryDB._w_insert_history

  def slow_insert(self, row):
    gate.wait(STALL)            # stand in for a disk that has stopped answering
    return real_insert(self, row)

  history_mod.HistoryDB._w_insert_history = slow_insert
  try:
    db.add('net', '#slow', 'message', 'alice', 'first')   # occupies the writer
    t0 = time.monotonic()
    for i in range(QUEUED):
      db.add('net', '#slow', 'message', 'alice', 'queued %d' % i)
    elapsed = time.monotonic() - t0
    check(elapsed < 0.5,
          'add() waited %.2fs for a stalled writer; it must queue and return, '
          'because on the GUI thread that wait is a frozen client' % elapsed)

    # And the ids it handed out are usable *now*, before any of it has been
    # written. This is what lets the write be asynchronous at all.
    check(db.current_max_id() >= QUEUED + 1,
          'current_max_id() did not count the queued rows (%d), so a replay '
          'bounded by it would cut them off' % db.current_max_id())
  finally:
    gate.set()
    history_mod.HistoryDB._w_insert_history = real_insert
  check(drain(db), 'the writer never drained after the gate opened')

  # ------------------------------------------------------------------ 3
  # The visibility invariant: a row is readable as soon as add() returns, and
  # in particular it is inside the cutoff current_max_id() reports. Get this
  # wrong and a message is written, excluded from its own backlog, and never
  # seen again.
  db.add('net', '#vis', 'message', 'alice', 'just said this')
  cutoff = db.current_max_id()
  rows = db.get_last('net', '#vis', 100, cutoff)
  check(rows and rows[-1][4] == 'just said this',
        'a line was not readable immediately after add() returned, within the '
        'cutoff current_max_id() had just reported (%d rows back)' % len(rows))

  # ------------------------------------------------------------------ 4
  # Pruning must also be off the calling thread, and must actually prune.
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

  # One more pass settles whatever the last inserts left dirty.
  with db._lock:
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

  # ------------------------------------------------------------------ 5
  # A pass over channels that are all under the limit must issue no DELETE and
  # no commit. This is the case that ran 188 pointless DELETEs -- each building
  # an ephemeral index of up to *keep* ids -- over the real database, every 500
  # messages, and it is what the stalls were made of. Watch the SQL rather than
  # the row counts: those DELETEs removed nothing either, and that was the
  # point -- the cost was in finding out.
  sql = []
  real_wget = history_mod.HistoryDB._wget

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

  history_mod.HistoryDB._wget = lambda self: Watched(real_wget(self))
  try:
    with db._lock:
      db._dirty.add(('net', '#small'))
    db._schedule_maintenance()
    check(drain(db), 'the no-op maintenance pass never finished')
  finally:
    history_mod.HistoryDB._wget = real_wget
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

  # ------------------------------------------------------------------ 6
  # The GUI thread's connection must be incapable of writing. It is the one
  # connection reachable from the code that must never wait for the disk, and
  # query_only turns "somebody added a write here" from a silent return of the
  # 15s busy_timeout into an exception at the call site.
  try:
    db._rconn.execute(
      "INSERT INTO history (ts, network, channel, type) VALUES ('x','n','#c','m')")
    check(False, 'the GUI-thread connection accepted a write; it is supposed '
                 'to be query_only, so that a second writer cannot be '
                 'reintroduced by accident')
  except sqlite3.Error:
    pass

  # ...and the writer's connection must never checkpoint on its own: SQLite
  # does that inline in commit(), and the fsync it carries would land in the
  # middle of the write queue instead of in the maintenance pass that chose it.
  autos = []
  db._submit(lambda: autos.append(
    db._wget().execute('PRAGMA wal_autocheckpoint').fetchone()[0]))
  check(drain(db), 'could not read the writer pragma')
  check(autos and autos[0] == 0,
        'the writer connection still has automatic WAL checkpointing on '
        '(threshold %r)' % (autos[0] if autos else '?',))

  # ------------------------------------------------------------------ 7
  # keep_limit comes straight from config.backscroll_limit, where 0 means
  # "unlimited". Read literally it means "keep the newest zero rows", i.e.
  # delete the entire history every 500 messages -- which is what both the old
  # NOT IN (... LIMIT 0) and a naive OFFSET 0 probe do.
  unlimited = history_mod.HistoryDB(os.path.join(tmpdir, 'u.db'), keep_limit=0)
  try:
    for i in range(20):
      unlimited.add('net', '#keep', 'message', 'alice', 'line %d' % i)
    with unlimited._lock:
      unlimited._dirty.add(('net', '#keep'))
    unlimited._schedule_maintenance()
    check(drain(unlimited), 'the unlimited-keep maintenance pass never finished')
    n = count(unlimited, 'net', '#keep')
    check(n == 20,
          'backscroll_limit 0 means unlimited, but pruning left %d of 20 rows'
          % n)
  finally:
    unlimited.close()

  # ------------------------------------------------------------------ 8
  # close() must drain rather than drop: what is queued has already been shown
  # to the user, so losing it means the backlog disagrees with what they read.
  # It must also stop the thread, close the connection on its own thread, and
  # fold the WAL back in.
  db.add('net', '#big', 'message', 'alice', 'the very last line')
  db.close()
  check(not db._writer._threads or
        all(not t.is_alive() for t in db._writer._threads),
        'the writer thread outlived close()')
  wal = path + '-wal'
  size = os.path.getsize(wal) if os.path.exists(wal) else 0
  check(size == 0,
        'close() left a %d-byte WAL behind instead of checkpointing it' % size)

  # And the data is still there, including the line queued just before close().
  conn = sqlite3.connect(path)
  n = conn.execute('SELECT COUNT(*) FROM history').fetchone()[0]
  last = conn.execute(
    'SELECT text FROM history ORDER BY id DESC LIMIT 1').fetchone()[0]
  conn.close()
  check(last == 'the very last line',
        'close() dropped a write that was still queued (newest row is %r)'
        % (last,))
  # KEEP + 50 pruned rows, plus #thread, #slow (QUEUED + 1) and #vis, plus the
  # line queued at close.
  expected = KEEP + 50 + 1 + (QUEUED + 1) + 1 + 1
  check(n == expected,
        'reopening the database found %d rows, expected %d' % (n, expected))


def main():
  tmpdir = tempfile.mkdtemp(prefix='qtpyrc-histtest-')
  try:
    run(tmpdir)
  except Exception:
    import traceback
    traceback.print_exc()
    failures.append('the test raised')
  finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

  if failures:
    print('FAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('OK')
  return 0


if __name__ == '__main__':
  sys.exit(main())
