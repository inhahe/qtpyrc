# hang_watchdog.py - detect and log GUI-thread stalls (freezes)
#
# qtpyrc runs asyncio (qasync) *on the Qt GUI thread*. When something blocks
# that thread -- a slow disk read, a synchronous DNS/socket call, a huge
# QTextEdit reflow, a long history query -- the whole UI freezes: keystrokes are
# ignored, the window won't repaint or restore from minimised. Windows only
# paints the grey "not responding" overlay after ~5s, so shorter stalls (the
# "couldn't do anything for a number of seconds" kind) leave no trace at all.
#
# This module detects those stalls and, crucially, records *where* the GUI
# thread was stuck.
#
# How it works
# ------------
# A QTimer on the GUI thread bumps a monotonic heartbeat. A plain daemon thread
# (deliberately NOT an asyncio task -- an asyncio task lives on the very loop
# that's blocked, so it could never fire during a stall) samples that heartbeat.
# If the heartbeat goes stale by more than *threshold* seconds, the GUI thread
# is wedged, and the watchdog captures its Python stack via
# sys._current_frames(). If the thread is blocked inside C/Qt code, the stack
# still shows the last Python frame that called in -- which is exactly the
# culprit we need.
#
# A long stall is re-sampled periodically, so a stack that keeps changing points
# at slow-but-progressing work (e.g. rendering thousands of lines), while a
# stack frozen on one frame points at a single blocking call.
#
# When the Python stack has nothing to say
# ---------------------------------------
# Most of the stalls recorded so far end at the event loop itself -- the deepest
# Python frame is qasync's run_forever(), i.e. QApplication::exec(). That means
# Qt is inside its own C++ event processing and never called into Python at all,
# so there is no Python frame that could name the blocker; the report says only
# "the GUI thread is busy", which is what we already knew. Out of the first 315
# samples in me/hangs.log, 239 look like that.
#
# For exactly those, the watchdog shells out to py-spy (`py-spy dump --native`)
# against its own pid and records the *native* stack, which names the Qt/Win32
# call: QTextDocument layout, a DirectWrite font enumeration, a Shell_NotifyIcon
# balloon, a blocking MessageBeep, and so on. py-spy suspends the process while
# it samples, which lengthens the very stall being measured -- acceptable only
# because it is limited to the case where the cheap Python sample is useless.
# Controlled by logging.hang_watchdog.native_stacks.

import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

# Heartbeat interval on the GUI thread. Must be well under the threshold.
_BEAT_INTERVAL_MS = 500
# How often the watchdog thread samples the heartbeat.
_POLL_INTERVAL = 0.25
# While a stall is ongoing, re-capture the stack this often (seconds) so we can
# tell "stuck on one call" from "slowly grinding through work".
_RESAMPLE_INTERVAL = 5.0
# How long to let py-spy run before giving up on it. Measured against a running
# qtpyrc: 0.6s warm, 2.5s on the first call of a session (loading symbols for
# every module). Anything past this is a py-spy that has wedged, and waiting on
# it would hold the watchdog thread -- and the process it has suspended.
_NATIVE_TIMEOUT = 10.0
# Don't take native samples faster than this. Each one suspends the process, so
# a run of back-to-back stalls would otherwise spend more time being measured
# than running. Every stall still gets its (free) Python stack.
_NATIVE_MIN_INTERVAL = 30.0

_state = {
  'last_beat': 0.0,
  'beats': 0,
  'gui_thread_id': None,
  'threshold': 2.0,
  'logfile': None,
  'timer': None,
  'thread': None,
  'stop': None,
  'native': False,     # capture native stacks when Python has nothing to say
  'py_spy': None,      # resolved py-spy path, '' if looked for and not found
  'last_native': 0.0,  # monotonic time of the last native sample
}


def _write(text):
  """Append *text* to the stall log and echo it to the console."""
  line = text.rstrip('\n')
  try:
    print(line, flush=True)
  except Exception:
    pass
  path = _state.get('logfile')
  if not path:
    return
  try:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
      os.makedirs(d, exist_ok=True)
    with open(path, 'a', encoding='utf-8', errors='replace') as f:
      f.write(line + '\n')
  except Exception:
    pass


def _stamp():
  return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def _capture_stack(thread_id):
  """Return the formatted Python stack of *thread_id*, or a note if unavailable."""
  try:
    frames = sys._current_frames()
  except Exception as e:
    return '  <could not read thread frames: %s>' % e
  frame = frames.get(thread_id)
  if frame is None:
    return '  <GUI thread %s not found>' % thread_id
  try:
    return ''.join(traceback.format_stack(frame)).rstrip('\n')
  except Exception as e:
    return '  <could not format stack: %s>' % e


def _stopped_in_the_event_loop(thread_id):
  """True when *thread_id*'s innermost Python frame is the event loop itself.

  sys._current_frames() hands back the innermost frame, so this is asking: has
  Qt called into Python at all? If the answer is no -- the thread is still
  sitting in the run_forever() that entered QApplication::exec() -- then the
  Python stack cannot name what is blocking, because whatever it is was never
  reached through Python. That is the case a native stack is for."""
  try:
    frame = sys._current_frames().get(thread_id)
  except Exception:
    return False
  if frame is None:
    return False
  code = frame.f_code
  if code.co_name not in ('run_forever', 'exec', 'exec_'):
    return False
  # Guard against a same-named function of our own: the event-loop entry lives
  # in qasync (or asyncio, if the loop is ever run without the Qt integration).
  path = (code.co_filename or '').replace('\\', '/').lower()
  return '/qasync/' in path or '/asyncio/' in path


def _find_py_spy():
  """Locate the py-spy executable, or '' if it isn't installed.

  Resolved once and remembered (including the failure), so a stall storm doesn't
  pay for a filesystem search each time."""
  found = _state.get('py_spy')
  if found is not None:
    return found
  cand = shutil.which('py-spy') or ''
  if not cand:
    # A pip-installed py-spy lands beside the interpreter that owns it, which
    # need not be on PATH -- and it is that interpreter's process we're
    # sampling, so its own Scripts/bin directory is the right place to look.
    base = os.path.dirname(os.path.abspath(sys.executable))
    for sub, name in (('Scripts', 'py-spy.exe'), ('bin', 'py-spy'), ('', 'py-spy')):
      p = os.path.join(base, sub, name) if sub else os.path.join(base, name)
      if os.path.isfile(p):
        cand = p
        break
  _state['py_spy'] = cand
  return cand


def _native_stack(thread_id):
  """Return the native (C/C++) stack of *thread_id*, sampled with py-spy.

  Returns (text, seconds_spent). py-spy attaches to this very process and
  suspends it to walk the stacks, so the seconds are added to the stall and the
  caller reports them -- otherwise the recorded stall length silently includes
  the cost of measuring it."""
  exe = _find_py_spy()
  if not exe:
    return ('  <no native stack: py-spy is not installed (pip install py-spy)>', 0.0)
  cmd = [exe, 'dump', '--native', '--pid', str(os.getpid())]
  t0 = time.monotonic()
  try:
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          errors='replace', timeout=_NATIVE_TIMEOUT,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
  except subprocess.TimeoutExpired:
    return ('  <no native stack: py-spy timed out after %.0fs>' % _NATIVE_TIMEOUT,
            time.monotonic() - t0)
  except Exception as e:
    return ('  <no native stack: could not run py-spy: %s>' % e,
            time.monotonic() - t0)
  spent = time.monotonic() - t0
  if proc.returncode != 0:
    err = (proc.stderr or proc.stdout or '').strip().splitlines()
    return ('  <no native stack: py-spy exited %s: %s>'
            % (proc.returncode, err[-1] if err else '(no output)'), spent)
  block = _extract_thread_block(proc.stdout or '', thread_id)
  return (block, spent)


def _extract_thread_block(dump, thread_id):
  """Pull one thread's frames out of a `py-spy dump` report.

  py-spy prints `Thread <os-tid> (state)` followed by indented frames, and on
  Windows that id is GetCurrentThreadId() -- the same number threading.get_ident()
  returns, so the GUI thread can be picked out by id rather than guessed at."""
  want = 'Thread %s ' % thread_id
  lines = (dump or '').splitlines()
  out = []
  taking = False
  for line in lines:
    if line.startswith('Thread '):
      if taking:
        break
      taking = line.startswith(want)
      if taking:
        out.append('  ' + line.rstrip())
      continue
    if taking:
      out.append('  ' + line.rstrip())
  if out:
    return '\n'.join(out).rstrip()
  # No block for that id: report everything rather than nothing, since a dump we
  # cannot index is still the only record of where the process was.
  body = '\n'.join('  ' + l for l in lines).rstrip()
  return body or '  <py-spy produced no output>'


def _all_thread_stacks():
  """Formatted stacks for every thread -- useful when the GUI thread is blocked
  waiting on another thread (a lock, a queue, a DB handle)."""
  out = []
  try:
    frames = sys._current_frames()
  except Exception as e:
    return '  <could not read thread frames: %s>' % e
  names = {t.ident: t.name for t in threading.enumerate()}
  for tid, frame in frames.items():
    if tid == _state.get('gui_thread_id'):
      continue
    out.append('  --- thread %s (%s) ---' % (tid, names.get(tid, '?')))
    try:
      out.append(''.join(traceback.format_stack(frame)).rstrip('\n'))
    except Exception as e:
      out.append('    <could not format: %s>' % e)
  return '\n'.join(out) if out else '  <no other threads>'


def _beat():
  _state['last_beat'] = time.monotonic()
  _state['beats'] += 1


def _maybe_write_native(gui_tid):
  """Record a native stack if the Python one couldn't name the blocker.

  Deliberately not done for every stall: py-spy freezes this process to read it,
  so it makes the stall it is measuring worse. When there *is* a Python frame
  below the event loop, that frame already names the culprit and paying for a
  native dump on top of it buys nothing."""
  if not _state.get('native'):
    return
  if not _stopped_in_the_event_loop(gui_tid):
    return
  now = time.monotonic()
  since = now - _state['last_native']
  if _state['last_native'] and since < _NATIVE_MIN_INTERVAL:
    _write('  (no Python frame below the event loop; skipping the native sample '
           '-- one was taken %.0fs ago, minimum interval %.0fs)'
           % (since, _NATIVE_MIN_INTERVAL))
    return
  _state['last_native'] = now
  _write('  (no Python frame below the event loop -- the GUI thread is inside '
         'Qt/Win32. Sampling native stack with py-spy...)')
  text, spent = _native_stack(gui_tid)
  _state['last_native'] = time.monotonic()
  _write('  GUI thread native stack (py-spy, %.2fs -- this process was '
         'suspended for that long, so it is part of the stall above):' % spent)
  _write(text)


def _watch_loop():
  stop = _state['stop']
  threshold = _state['threshold']
  gui_tid = _state['gui_thread_id']
  stalling = False
  stall_started = 0.0
  last_sample = 0.0

  while not stop.is_set():
    stop.wait(_POLL_INTERVAL)
    if stop.is_set():
      break
    last = _state['last_beat']
    if not last:
      continue
    now = time.monotonic()
    behind = now - last

    if behind > threshold:
      if not stalling:
        stalling = True
        stall_started = last
        last_sample = now
        _write('\n[%s] *** GUI STALL detected: no heartbeat for %.2fs '
               '(threshold %.2fs) ***' % (_stamp(), behind, threshold))
        if not _state['beats']:
          # No beat has *ever* arrived. Legitimate during a long startup (the
          # event loop hasn't been entered yet), but it also describes a timer
          # that can't fire at all -- say it plainly so the report isn't
          # mistaken for a mid-session freeze.
          _write('  (note: heartbeat has not fired since the watchdog started; '
                 'the event loop may not be running yet)')
        _write('  GUI thread stack at stall:')
        _write(_capture_stack(gui_tid))
        _write('  Other threads:')
        _write(_all_thread_stacks())
        _maybe_write_native(gui_tid)
        last_sample = time.monotonic()   # the native dump can take seconds
      elif now - last_sample >= _RESAMPLE_INTERVAL:
        last_sample = now
        _write('[%s]   ... still stalled (%.1fs). GUI thread stack now:'
               % (_stamp(), behind))
        _write(_capture_stack(gui_tid))
        _maybe_write_native(gui_tid)
        last_sample = time.monotonic()
    else:
      if stalling:
        stalling = False
        total = last - stall_started
        _write('[%s] *** GUI recovered after %.2fs ***\n' % (_stamp(), total))


def start(threshold=2.0, logfile=None, native=False):
  """Begin watching the GUI thread for stalls.

  Must be called from the GUI thread (it installs a QTimer there).
  *threshold* is the number of seconds without a heartbeat that counts as a
  stall. *native* allows py-spy to be used for the stalls whose Python stack
  ends at the event loop (see _maybe_write_native). Returns True if the watchdog
  started."""
  if _state.get('thread') is not None:
    return True  # already running
  try:
    from PySide6.QtCore import QTimer, QCoreApplication
  except Exception:
    return False

  # Qt cannot start a timer before an application object exists; it prints
  # "Timers can only be used with threads started with QThread" and the timer
  # silently never fires. A heartbeat that never fires is indistinguishable
  # from a permanently wedged GUI thread, so the watchdog would report an
  # endless fake stall. Refuse to start rather than lie.
  if QCoreApplication.instance() is None:
    _write('[%s] hang watchdog NOT started: no QApplication yet (start it '
           'after the Qt application is created)' % _stamp())
    return False

  _state['threshold'] = float(threshold)
  _state['logfile'] = logfile
  _state['native'] = bool(native)
  _state['py_spy'] = None
  _state['last_native'] = 0.0
  _state['gui_thread_id'] = threading.get_ident()
  _state['last_beat'] = time.monotonic()
  _state['beats'] = 0
  _state['stop'] = threading.Event()

  timer = QTimer()
  timer.setInterval(_BEAT_INTERVAL_MS)
  timer.timeout.connect(_beat)
  timer.start()
  if not timer.isActive():
    _write('[%s] hang watchdog NOT started: heartbeat timer refused to start'
           % _stamp())
    _state['stop'] = None
    return False
  _state['timer'] = timer  # keep a reference so it isn't garbage collected

  t = threading.Thread(target=_watch_loop, name='hang-watchdog', daemon=True)
  t.start()
  _state['thread'] = t

  native_note = ''
  if _state['native']:
    exe = _find_py_spy()
    native_note = (', native stacks via %s' % exe) if exe else \
                  (', native stacks requested but py-spy is not installed '
                   '(pip install py-spy)')
  _write('[%s] hang watchdog started (threshold %.2fs, log %s%s)'
         % (_stamp(), _state['threshold'], logfile or '<console only>',
            native_note))
  return True


def stop():
  """Stop the watchdog (used on shutdown so the thread doesn't outlive us)."""
  ev = _state.get('stop')
  if ev is not None:
    ev.set()
  timer = _state.get('timer')
  if timer is not None:
    try:
      timer.stop()
    except Exception:
      pass
  _state['timer'] = None
  _state['thread'] = None
