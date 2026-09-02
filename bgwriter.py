# bgwriter.py - append to log files from a background thread
#
# Why this exists
# ---------------
# qtpyrc runs asyncio (qasync) *on the Qt GUI thread*, so anything that thread
# waits for is a freeze of the whole application. Writing a file is such a
# wait. It does not look like one -- a buffered write() to an already-open
# handle is normally microseconds -- but flush() is a WriteFile syscall, and a
# syscall against a filesystem that is busy (a backup, an antivirus scan, a
# compile, another process saturating the disk queue) blocks for as long as the
# filesystem takes. Seconds, in the reported case.
#
# That put a synchronous disk write in the hot path of every chat line, in both
# directions. Sending one ran the log write inline between putting the line on
# the wire and saving it to history, which is why the symptom was "I press
# Enter and the client freezes for a few seconds before my line appears".
#
# The fix is not to write less or to flush less often -- the log has to be
# complete and it has to survive a crash. It is to move the writing off the
# thread that has to stay responsive. Nothing in qtpyrc ever reads these files
# back, so they have no ordering constraint against anything but themselves,
# and a queue drained by a single thread preserves that.
#
# What this buys beyond the flush
# -------------------------------
# open() and os.makedirs() are filesystem operations too, and they were also on
# the GUI thread. IRCLogger caches its handles, so that cost read as "once per
# file, negligible" -- but once per file means once per conversation partner,
# and with logging.separate_by_month (the default) once per file *per month*,
# so a month boundary is a burst of first-writes. It also means once more after
# every write error, since the recovery path drops the handle so that the next
# line reopens. All of it happens on the writer thread now.
#
# Design notes
# ------------
# * **One thread, one FIFO queue.** Not a thread pool: a log file is read by a
#   human as a transcript, so the order lines were submitted in is the order
#   they must appear in. A single consumer gives that for free, for every file
#   at once, with no per-file locking.
# * **Flush when the queue drains, not per line.** While qtpyrc is idle -- when
#   an unexpected crash is most likely to cost something -- the queue empties
#   after every line, so this is exactly the old per-line durability. Under a
#   burst it batches, which is the case where per-line flushing bought little
#   anyway because the lines were already going to the same page.
# * **The queue is bounded.** A filesystem that has stopped answering must not
#   also become unbounded memory growth. Past the bound, lines are dropped and
#   *counted*, and the count is written into the file when the writer next gets
#   through: a silent hole in a log is how someone concludes a conversation
#   never happened.
# * **Nothing here raises at the caller.** A caller is a chat line arriving.

import os
import queue
import threading

from state import dbg, LOG_ERROR

# Past this many queued lines the filesystem is not merely slow, it has stopped
# answering. Chat lines are small, so this is a few tens of MB at worst --
# enough to ride out any stall short of a dead disk, and bounded enough not to
# become the second problem.
_MAX_QUEUED = 100000

# Queue markers, compared by identity.
_STOP = object()
_FLUSH = object()


class BackgroundWriter:
  """Append text lines to files, on a thread of its own.

  write(path, line) returns immediately, having done no filesystem work. The
  writer thread creates directories, opens files (once each, cached) and
  flushes when it runs out of queued work.
  """

  def __init__(self, name='bg-writer'):
    self._q = queue.Queue(maxsize=_MAX_QUEUED)
    self._handles = {}          # path -> open file object (writer thread only)
    self._dirs_made = set()     # directories created (writer thread only)
    self._dropped = 0           # lines lost to a full queue, not yet reported
    self._lock = threading.Lock()   # guards _dropped only
    self._closed = False
    self._thread = threading.Thread(target=self._run, name=name, daemon=True)
    self._thread.start()

  # --- caller side (any thread; usually the GUI thread) ---------------------

  def write(self, path, line):
    """Queue *line* to be appended to *path*. Never blocks, never raises."""
    if self._closed:
      return
    try:
      self._q.put_nowait((path, line))
    except queue.Full:
      with self._lock:
        self._dropped += 1

  def flush(self, timeout=5.0):
    """Block until everything queued so far has been written and flushed.

    For shutdown and for tests. Nothing on the chat path may call this: the
    whole point of the class is that the chat path does not wait for the disk.
    """
    if self._closed or not self._thread.is_alive():
      return False
    done = threading.Event()
    try:
      # A blocking put, unlike write()'s: the queue being full is the one
      # moment a caller most needs to be able to wait for it to empty, so
      # put_nowait here would fail precisely when it was asked to do its job.
      self._q.put((_FLUSH, done), timeout=timeout)
    except queue.Full:
      return False
    return done.wait(timeout)

  def close(self, timeout=5.0):
    """Drain, close every handle, and stop the thread.

    *timeout* is a bound rather than a target: a filesystem wedged at exit must
    not stop qtpyrc exiting, and what is still queued at that point is chat
    lines we would have liked to keep, not state anything depends on.
    """
    if self._closed:
      return
    self._closed = True
    try:
      # Same reasoning as flush(): a full queue must not be the reason the
      # stop marker never arrives, or the thread runs until the process dies.
      # _closed is already set, so nothing new is being added behind us and
      # the space this waits for is certain to appear.
      self._q.put(_STOP, timeout=timeout)
    except queue.Full:
      pass
    self._thread.join(timeout)

  # --- writer thread --------------------------------------------------------

  def _handle(self, path):
    """Open handle for *path*, creating its directory once. Writer thread."""
    f = self._handles.get(path)
    if f is not None:
      return f
    d = os.path.dirname(path)
    if d and d not in self._dirs_made:
      os.makedirs(d, exist_ok=True)
      self._dirs_made.add(d)
    f = open(path, 'a', encoding='utf-8', errors='replace')
    self._handles[path] = f
    return f

  def _drop_handle(self, path):
    f = self._handles.pop(path, None)
    if f is not None:
      try:
        f.close()
      except Exception:
        pass

  def _emit(self, path, line):
    """Write one line. Writer thread. Swallows and reports failures."""
    try:
      self._handle(path).write(line + '\n')
      return True
    except Exception:
      dbg(LOG_ERROR, 'Log write failed:', path)
      self._drop_handle(path)   # reopen cleanly on the next line
      return False

  def _report_drops(self, path):
    """Note in the file itself that lines were lost, if any were.

    It goes to whichever file is being written when we notice, because the
    queue does not remember where a dropped line was headed -- that went with
    the line. Approximate placement of an accurate count beats exact silence.
    """
    with self._lock:
      n, self._dropped = self._dropped, 0
    if n:
      self._emit(path, '[%d log line(s) lost: the write queue filled up, which '
                       'means the filesystem stopped accepting writes]' % n)

  def _flush_all(self):
    for path in list(self._handles):
      try:
        self._handles[path].flush()
      except Exception:
        dbg(LOG_ERROR, 'Log flush failed:', path)
        self._drop_handle(path)

  def _run(self):
    while True:
      item = self._q.get()
      if item is _STOP:
        break
      if item[0] is _FLUSH:
        self._flush_all()
        item[1].set()
        continue
      path, line = item
      if self._dropped:
        self._report_drops(path)
      self._emit(path, line)
      # Keep writing while there is more queued; flush once we run dry. That is
      # the batching: a burst costs one flush, an idle client costs one per
      # line, and neither costs the GUI thread anything.
      if self._q.empty():
        self._flush_all()
    self._flush_all()
    for path in list(self._handles):
      self._drop_handle(path)


# One writer serves the whole application: the chat logs and the render audit
# both just want lines appended somewhere off the GUI thread, and a second
# thread would buy nothing but a second thing to shut down. Ordering is
# per-file, so sharing cannot interleave two files' lines with each other.
_shared = None
_shared_done = False    # close_shared() has run; do not start another one
_shared_lock = threading.Lock()


def shared():
  """Return the process-wide writer, starting it on first use.

  After close_shared() it keeps handing back the closed instance rather than
  starting a replacement. A writer created during shutdown would be a daemon
  thread nobody is left to drain, so its lines would be lost anyway -- and a
  lost line is better than a lost line plus a thread that outlives the code
  that was meant to stop it. A closed writer accepts write() and discards it.
  """
  global _shared
  with _shared_lock:
    if _shared is None and not _shared_done:
      _shared = BackgroundWriter(name='log-writer')
    if _shared is None:
      _shared = _NullWriter()
    return _shared


def close_shared(timeout=5.0):
  """Stop the process-wide writer, if one was ever started."""
  global _shared, _shared_done
  with _shared_lock:
    w = _shared
    _shared_done = True
    _shared = _NullWriter()
  if w is not None and not isinstance(w, _NullWriter):
    w.close(timeout)


class _NullWriter:
  """What shared() hands out after shutdown: the same shape, doing nothing."""

  _closed = True

  def write(self, path, line):
    pass

  def flush(self, timeout=5.0):
    return True

  def close(self, timeout=5.0):
    pass
