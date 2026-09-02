"""End-to-end: registration side effects must happen exactly once per connect.

The bug this exists for: the autojoin loop and the NickServ IDENTIFY lived in
`IRCClient.isupport()`, which is a *per-message* callback.  A server splits
ISUPPORT across as many 005 lines as it needs to stay inside the 512-byte
message limit -- two or three on every real network -- so both ran once per 005:
every configured channel was JOINed two or three times and the account password
was put on the wire two or three times.

It hid for a long time because on a channel you *can* join it is invisible.  The
first JOIN succeeds, `joined()` strips the still-queued duplicates, and a JOIN
to a channel you are already in is a no-op at the server.  It only surfaces on a
channel you *cannot* join -- +b, or +k with the wrong key -- where there is no
`joined()` to clean up and each JOIN earns its own error reply.  That is the
doubled "#ops Cannot join channel (+b)" in `me/renders.log` that led here.

So this test asserts on the wire, via the test server's RECEIVED control
command, not on anything the client says about itself: the second JOIN leaves no
trace on the client side at all.

Note the test server used to send one tidy 005 line, which is why its existing
tests never caught this.  It now splits ISUPPORT across two, like a real server.

  python tests/test_register_once.py
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

# Three autojoin channels, so a miscount is unambiguous about which channel and
# by how much.  Two of them are configured on the server to be *unjoinable* --
# and that is the whole point, not decoration.  On a channel the client can
# join, the duplicate JOIN never reaches the wire: the server's JOIN echo
# arrives first, `joined()` strips the still-queued copies from the flood queue,
# and the test passes even against the broken code.  (Verified: with the fix
# reverted, an all-joinable version of this test still reported 3 JOINs for 3
# channels.)  Only a channel with no JOIN echo coming back leaves the queued
# duplicate in place long enough to be sent -- which is exactly why the bug
# reached the user as a doubled "Cannot join channel (+b)" and nothing else.
#
# The other side effect that moved out of isupport() -- the NickServ IDENTIFY --
# is not exercised here, and cannot be: `login_method`/`login_password` are
# per-connection overrides set by /server switches (irc_client.py reads them
# from `ov`), with no config equivalent, so an auto-connecting test cannot ask
# for one.  It shares a single guard with the autojoin, so the JOIN count is a
# faithful proxy; if that ever stops being true -- if the two get separate
# guards -- this test needs a second half driven through /server.
CHANNELS = ['#alpha', '#beta', '#gamma']
# channel -> (numeric, text) the server answers a JOIN with.
REJECT = {'#beta': (474, 'Cannot join channel (+b)'),
          '#gamma': (475, 'Cannot join channel (+k)')}

CONFIG = """\
nick: tester
user: tester
realname: qtpyrc register test

window_mode: normal
view_mode: tabbed

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
link_preview: {enabled: false}
ident: {enabled: false}
logging:
  hang_watchdog: {enabled: false}

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
    auto_join:
%(channels)s
"""


def free_port():
  s = socket.socket()
  s.bind(('127.0.0.1', 0))
  port = s.getsockname()[1]
  s.close()
  return port


PORT = free_port()
CTRL_PORT = free_port()

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-regtest-')
# Before qtpyrc runs, so atexit's LIFO order puts it after qtpyrc has closed
# crash.log -- see the same note in test_pm_activity_live.py.
atexit.register(shutil.rmtree, tmpdir, True)

cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG % {'port': PORT,
                    'channels': '\n'.join("      '%s':" % c for c in CHANNELS)})

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.argv = ['qtpyrc.py', '-c', cfg]


def control(line):
  s = socket.create_connection(('127.0.0.1', CTRL_PORT), timeout=5)
  s.sendall((line + '\n').encode())
  s.settimeout(5)
  buf = b''
  while b'\n' not in buf:
    chunk = s.recv(65536)
    if not chunk:
      break
    buf += chunk
  s.close()
  reply = buf.decode('utf-8', 'replace').strip()
  if not reply.startswith('OK'):
    raise RuntimeError('control command failed: %r' % reply)
  return reply[2:].strip()


sys.path.insert(0, os.path.join(ROOT, 'tests'))
from irc_test_server import wait_until_listening

server = subprocess.Popen(
    [sys.executable, os.path.join(ROOT, 'tests', 'irc_test_server.py'),
     '--port', str(PORT), '--control-port', str(CTRL_PORT)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if not wait_until_listening(PORT, CTRL_PORT):
  raise SystemExit('the test IRC server never came up')

# Configured before qtpyrc is even imported, so it is certainly in place by the
# time the autojoin burst arrives.
for chan, (num, text) in REJECT.items():
  control('REJECT %s %d %s' % (chan, num, text))

import window as window_mod
from PySide6.QtCore import QTimer, QCoreApplication, QMetaObject, Qt

EXIT = 1


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def inspect():
  failures = []

  def check(cond, msg):
    if not cond:
      failures.append(msg)

  try:
    joins = [l for l in control('RECEIVED JOIN').split(' | ') if l.strip()]
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)

  # The flood queue is what makes this worth checking on the wire: a duplicate
  # JOIN is not dropped by the client, it is queued behind the first and sent a
  # second or two later.  Count per channel rather than in total, so a failure
  # says which channel and how many.
  for chan in CHANNELS:
    n = sum(1 for l in joins if l.split()[1:2] == [chan])
    check(n == 1,
          'JOIN %s was sent %d times, expected 1 -- registration work is '
          'running once per 005 line again (see IRCClient.registered)'
          % (chan, n))

  check(len(joins) == len(CHANNELS),
        'the server received %d JOINs for %d configured channels: %r'
        % (len(joins), len(CHANNELS), joins))

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    print('\nJOINs the server received: %r' % (joins,))
    return finish(1)

  print('registration side effects ran once each (%d JOINs for %d channels) '
        'across a two-line ISUPPORT burst.' % (len(joins), len(CHANNELS)))
  return finish(0)


# Long enough for the flood-controlled join queue to drain: the failing case
# queues three extra JOINs behind the real ones, so a short window would let a
# regression pass by simply not having sent the duplicates yet.
window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(6000, inspect)

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
