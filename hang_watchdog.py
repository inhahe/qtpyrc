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

import os
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

_state = {
  'last_beat': 0.0,
  'beats': 0,
  'gui_thread_id': None,
  'threshold': 2.0,
  'logfile': None,
  'timer': None,
  'thread': None,
  'stop': None,
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
      elif now - last_sample >= _RESAMPLE_INTERVAL:
        last_sample = now
        _write('[%s]   ... still stalled (%.1fs). GUI thread stack now:'
               % (_stamp(), behind))
        _write(_capture_stack(gui_tid))
    else:
      if stalling:
        stalling = False
        total = last - stall_started
        _write('[%s] *** GUI recovered after %.2fs ***\n' % (_stamp(), total))


def start(threshold=2.0, logfile=None):
  """Begin watching the GUI thread for stalls.

  Must be called from the GUI thread (it installs a QTimer there).
  *threshold* is the number of seconds without a heartbeat that counts as a
  stall. Returns True if the watchdog started."""
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

  _write('[%s] hang watchdog started (threshold %.2fs, log %s)'
         % (_stamp(), _state['threshold'], logfile or '<console only>'))
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
