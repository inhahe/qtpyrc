"""End-to-end: an incoming PM must open a query window that is marked unread
*and* shows the message exactly once.

This drives the real application -- real Qt windows, real asyncio IRC client,
real SQLite history -- against tests/irc_test_server.py, sends a private message
through the server's control socket, and then inspects the query window that
appeared: its activity level, the colour the workspace was told to paint its tab,
and its document.

It exists because the unit-level checks in test_activity_replay.py cannot see the
wiring. Three separate defects in the replay hold-back conspired here, and each
one hid the next: the tab was never coloured, the message that opened the window
was discarded, and (once it wasn't) it was rendered twice -- once by the history
replay that had just saved it and once by the flushed hold-back queue. Only a
live run puts those in the same place.

Usage:
  python tests/test_pm_activity_live.py            # drip-fed history (default)
  python tests/test_pm_activity_live.py --no-bg    # replay-on-activation instead

Both modes matter: they are different replay paths (qtpyrc._bg_replay_loop vs
irc_client._history_replay) and each has to reach the same result.
"""

import atexit
import os
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_BG = '--no-bg' in sys.argv

# The window has to be in the background for its tab to mean anything: the whole
# point of the colour is to tell the user about a window they are not looking at.
# new_tab_state decides that, and its default ("active") would switch straight to
# the new query -- a window you are looking at is deliberately never marked.
CONFIG = """\
nick: tester
user: tester
realname: qtpyrc pm test

window_mode: normal
view_mode: tabbed
new_tab_state: normal

# No distractions, no outbound anything, no watchdog stack dumps.
notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
link_preview: {enabled: false}
ident: {enabled: false}
logging:
  hang_watchdog: {enabled: false}

history_replay:
  channels: 10
  queries: 10

networks:
  testnet:
    name: TestNet
    nick: tester
    auto_connect: true
    persist_autojoins: false
    server:
      host: 127.0.0.1
      port: %(port)d
      tls: false
"""


def free_port():
  s = socket.socket()
  s.bind(('127.0.0.1', 0))
  port = s.getsockname()[1]
  s.close()
  return port


PORT = free_port()
CTRL_PORT = free_port()
# Unique per run so the assertion below can tell *this* message apart from the
# copies of it that earlier runs left in the history database.
NONCE = 'pm-%d' % (time.time() * 1000)

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-pmtest-')

# Removed at interpreter exit rather than in a finally, and registered here --
# before qtpyrc is run -- on purpose. qtpyrc holds crash.log open for the life of
# the process (faulthandler needs a live file object) and closes it from its own
# atexit handler, so a rmtree in a finally runs while Windows still has the file
# locked, silently does nothing under ignore_errors, and leaves the directory
# behind on every run. atexit is LIFO, so registering first means running last:
# after qtpyrc's handler has closed the file.
atexit.register(shutil.rmtree, tmpdir, True)

cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG % {'port': PORT})

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)

argv = ['qtpyrc.py', '-c', cfg]
if NO_BG:
  argv += ['-o', 'history_bg.enabled=false']
sys.argv = argv

sys.path.insert(0, os.path.join(ROOT, 'tests'))
from irc_test_server import wait_until_listening

server = subprocess.Popen(
    [sys.executable, os.path.join(ROOT, 'tests', 'irc_test_server.py'),
     '--port', str(PORT), '--control-port', str(CTRL_PORT)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if not wait_until_listening(PORT, CTRL_PORT):
  raise SystemExit('the test IRC server never came up')

import state
import window as window_mod
from PySide6.QtCore import QTimer, QCoreApplication, QMetaObject, Qt

EXIT = 1


def control(line):
  s = socket.create_connection(('127.0.0.1', CTRL_PORT), timeout=5)
  s.sendall((line + '\n').encode())
  time.sleep(0.3)
  s.close()


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def send_pm():
  try:
    control('PRIVMSG alice hello from alice %s' % NONCE)
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)
  QTimer.singleShot(2500, inspect)


def inspect():
  failures = []

  def check(cond, msg):
    if not cond:
      failures.append(msg)

  try:
    client = next(iter(state.clients))
    ws = state.app.mainwin.workspace
    q = client.conn.queries.get('alice')
    if q is None:
      print('FAIL: no query window was opened for alice')
      return finish(1)
    w = q.window
    Window = window_mod.Window

    # A precondition, not a result: everything below is about a *background*
    # window, so a run where the query grabbed focus would pass vacuously.
    check(not w._is_active_window(),
          'the query window took focus; the tab-colour checks below would be '
          'meaningless (is new_tab_state still "normal"?)')

    check(w._activity == Window.ACTIVITY_HIGHLIGHT,
          'the query tab was not marked highlighted (activity=%r, want %r)'
          % (w._activity, Window.ACTIVITY_HIGHLIGHT))

    # TabbedWorkspace keeps the colour on its own tab record.
    colour = None
    for t in getattr(ws, '_tabs', []):
      if t['proxy'] is w.subwindow:
        colour = t.get('activity_color')
        break
    want = state.config.color_highlight
    if colour is None:
      check(False, 'the query tab was never given an activity colour')
    else:
      check(colour.name() == want.name(),
            'the tab is %s, expected the highlight colour %s'
            % (colour.name(), want.name()))

    text = w.output.toPlainText()
    n = text.count(NONCE)
    if n == 0:
      check(False, 'the private message itself is not in the window')
    else:
      check(n == 1,
            'the message is shown %d times -- the replay rendered it and the '
            'flushed hold-back queue rendered it again' % n)

    if failures:
      print('\nFAILED (%d):' % len(failures))
      for f in failures:
        print('  - %s' % f)
      # The separator lines are box-drawing characters, which a cp1252 console
      # cannot encode -- escape rather than let the diagnostic itself blow up.
      print('\nwindow text was:\n%s'
            % text.encode('ascii', 'backslashreplace').decode('ascii'))
      return finish(1)
    print('PM activity/replay end-to-end checks passed (%s).'
          % ('replay on activation' if NO_BG else 'drip-fed history'))
    return finish(0)
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


# Arm from the GUI thread once the first window has painted, so the connection
# and the initial replay have somewhere to land before the PM arrives.
window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(3500, send_pm)

try:
  runpy.run_path(os.path.join(ROOT, 'qtpyrc.py'), run_name='__main__')
except SystemExit:
  pass
finally:
  server.terminate()          # our own child, started above -- never by name
  try:
    server.wait(timeout=5)
  except Exception:
    server.kill()

sys.exit(EXIT)
