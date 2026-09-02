"""Chat logging must not put a filesystem syscall on the GUI thread.

The reported bug: "qtpyrc hangs for a few seconds every once in a while before
reacting after I hit enter on a post -- I think it may be when the filesystem
is under load". The reporter's diagnosis was right. Sending a message ran, on
the GUI thread, between putting the line on the wire and drawing it:

    state.irclogger.log_channel(...)   ->  write() + flush()   (a WriteFile)
    state.historydb.add(...)           ->  INSERT + commit()   (a WriteFile)

A buffered write to an open handle is microseconds, which is why this read as
free. flush() is not: it is a syscall, and a syscall against a filesystem that
is busy blocks for as long as the filesystem takes. me/hangs.log has the
GUI-thread stall samples to prove it, sitting in logger.py log and in
history.py add.

So the invariant is not "logging is fast". It is that **logging does not wait
for the disk on the thread that has to stay responsive** -- which is the only
formulation that still holds when the disk is the thing that has gone wrong.
Hence the tests below stall the writer deliberately rather than measuring a
healthy one: a benchmark against a fast disk cannot fail, and would have passed
against the code that produced the report.

The history half of the same invariant is in test_history_maint.py.

Usage:
  python tests/test_bgwriter.py     # from the qtpyrc root directory
"""

import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bgwriter

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


def read(path):
  if not os.path.exists(path):
    return ''
  with open(path, encoding='utf-8', errors='replace') as f:
    return f.read()


def run(tmpdir):
  main_thread = threading.get_ident()

  # ------------------------------------------------------------------ 1
  # The obvious one: lines arrive, in the order they were submitted, in the
  # file they were addressed to. Everything else here is worthless if the
  # writer does not actually write.
  w = bgwriter.BackgroundWriter(name='test-writer-1')
  a = os.path.join(tmpdir, 'a.log')
  b = os.path.join(tmpdir, 'sub', 'dir', 'b.log')   # directory does not exist
  try:
    for i in range(50):
      w.write(a, 'a %d' % i)
      w.write(b, 'b %d' % i)
    check(w.flush(10.0), 'flush() timed out')
    check(read(a).split() == sum([['a', str(i)] for i in range(50)], []),
          'lines arrived out of order or incomplete in a.log')
    check(read(b).count('b 49') == 1,
          'the writer did not create the missing directory for b.log')
  finally:
    w.close()

  # ------------------------------------------------------------------ 2
  # No filesystem call may happen on the caller's thread. Not the write, not
  # the flush, and -- the part the old handle cache still got wrong -- not the
  # open() and makedirs() either. Those are once per file, which sounds
  # negligible until you notice "per file" means per conversation partner, and
  # with logging.separate_by_month once per file *per month*.
  import builtins
  offenders = []
  real_open, real_makedirs = builtins.open, os.makedirs

  def watched_open(*a, **k):
    if threading.get_ident() == main_thread:
      offenders.append('open%r' % (a[:1],))
    return real_open(*a, **k)

  def watched_makedirs(*a, **k):
    if threading.get_ident() == main_thread:
      offenders.append('makedirs%r' % (a[:1],))
    return real_makedirs(*a, **k)

  w = bgwriter.BackgroundWriter(name='test-writer-2')
  c = os.path.join(tmpdir, 'fresh', 'c.log')
  builtins.open, os.makedirs = watched_open, watched_makedirs
  try:
    for i in range(20):
      w.write(c, 'c %d' % i)
  finally:
    builtins.open, os.makedirs = real_open, real_makedirs
  check(w.flush(10.0), 'flush() timed out')
  check(not offenders,
        'the caller thread performed filesystem work: %r -- on the GUI thread '
        'that is exactly the freeze this module exists to remove' % (offenders,))
  check('c 19' in read(c), 'the lines never arrived after all')
  w.close()

  # ------------------------------------------------------------------ 3
  # The one that reproduces the report: with the disk not answering, writing
  # must still return immediately. A wall-clock assertion is normally a bad
  # test, but here the quantity under test *is* wall-clock latency, and the
  # margin is four orders of magnitude (a queue put against a 5s stall).
  w = bgwriter.BackgroundWriter(name='test-writer-3')
  d = os.path.join(tmpdir, 'd.log')
  gate = threading.Event()
  real_emit = bgwriter.BackgroundWriter._emit

  def stalled_emit(self, path, line):
    # Short on purpose: a regression makes every write below take this long,
    # so the section has to fail in seconds rather than hang for minutes. The
    # margin against the assertion is still three orders of magnitude.
    gate.wait(2.0)              # a filesystem that has stopped answering
    return real_emit(self, path, line)

  bgwriter.BackgroundWriter._emit = stalled_emit
  try:
    w.write(d, 'occupies the writer')
    time.sleep(0.05)            # let the writer pick it up and block
    t0 = time.monotonic()
    for i in range(20):
      w.write(d, 'queued %d' % i)
    elapsed = time.monotonic() - t0
    check(elapsed < 0.5,
          'writing 20 lines took %.2fs while the disk was stalled; it must '
          'queue and return, because on the GUI thread that wait is the '
          'reported freeze' % elapsed)
  finally:
    gate.set()
    bgwriter.BackgroundWriter._emit = real_emit
  check(w.flush(10.0), 'flush() timed out after the stall cleared')
  check('queued 19' in read(d),
        'lines written during the stall were lost once it cleared')
  w.close()

  # ------------------------------------------------------------------ 4
  # A queue that fills up must say so in the file. Dropping is the right
  # behaviour -- the alternative to a bounded queue is unbounded memory on top
  # of a broken disk -- but dropping *silently* is how someone concludes a
  # conversation never happened.
  real_max = bgwriter._MAX_QUEUED
  bgwriter._MAX_QUEUED = 5
  gate = threading.Event()
  bgwriter.BackgroundWriter._emit = stalled_emit
  w = bgwriter.BackgroundWriter(name='test-writer-4')
  e = os.path.join(tmpdir, 'e.log')
  try:
    w.write(e, 'occupies the writer')
    time.sleep(0.05)
    for i in range(50):         # far more than 5
      w.write(e, 'flood %d' % i)
  finally:
    gate.set()
    bgwriter.BackgroundWriter._emit = real_emit
    bgwriter._MAX_QUEUED = real_max
  check(w.flush(10.0), 'flush() timed out after the flood')
  text = read(e)
  check('log line(s) lost' in text,
        'lines were dropped when the queue filled and the log does not say so')
  w.close()

  # ------------------------------------------------------------------ 5
  # close() flushes rather than abandoning: what is queued has already been
  # shown to the user.
  w = bgwriter.BackgroundWriter(name='test-writer-5')
  f = os.path.join(tmpdir, 'f.log')
  for i in range(100):
    w.write(f, 'closing %d' % i)
  w.close()
  check('closing 99' in read(f),
        'close() dropped lines that were still queued')
  check(not w._thread.is_alive(), 'the writer thread outlived close()')

  # ------------------------------------------------------------------ 6
  # IRCLogger is the caller that matters, so check it end to end: no
  # filesystem work on the calling thread, and the line lands in the file its
  # own _path() names, with the timestamp taken when it was logged rather than
  # whenever the disk got round to it.
  import config as config_mod

  class Cfg:
    log_dir = os.path.join(tmpdir, 'logs')
    log_use_subdirs = False
    log_separate_by_month = False
    log_timestamp_format = 'HH:mm:SS'

  from logger import IRCLogger
  lg = IRCLogger(Cfg(), tmpdir)
  offenders = []
  builtins.open, os.makedirs = watched_open, watched_makedirs
  try:
    lg.log('TestNet', '#chan', '<alice> hello')
  finally:
    builtins.open, os.makedirs = real_open, real_makedirs
  check(not offenders,
        'IRCLogger.log() did filesystem work on the calling thread: %r'
        % (offenders,))
  check(lg.flush(10.0), 'IRCLogger.flush() timed out')
  check('<alice> hello' in read(lg._path('TestNet', '#chan')),
        'IRCLogger.log() did not reach the file its own _path() names')
  bgwriter.close_shared()


def main():
  tmpdir = tempfile.mkdtemp(prefix='qtpyrc-bgwtest-')
  try:
    run(tmpdir)
  except Exception:
    import traceback
    traceback.print_exc()
    failures.append('the test raised')
  finally:
    try:
      bgwriter.close_shared()
    except Exception:
      pass
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
