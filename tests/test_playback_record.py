"""A bouncer replay must be recorded if it has not been recorded before.

The old rule was "never record anything inside a playback batch", and it was
written for **ZNC**, which replays a fixed tail of every channel on each
reconnect. Those lines really had been recorded already, so logging them again
appended a duplicate copy of the tail per reconnect -- which is the bug that
rule fixed (see the 2026-08-31 entry in known-issues.md).

It is wrong for a bouncer that replays *what you missed*, which is what Wicket
does. There the replay is the only time those lines ever reach the client, so
suppressing it loses them permanently. Measured on the reporter's history.db:
2026-09-04 has no rows at all for 06:00, and 08:00 through 12:00 -- five hours
across some thirty channels, on a day whose 13:00 hour has 645. The client was
closed; Wicket buffered; on reattach every line was drawn on screen and written
nowhere. The reporter noticed because a conversation they could see was in
neither the log nor the history.

A client cannot know which kind of bouncer it is talking to, so it must not
guess. `_should_record` compares instead: a replayed line is recorded when it
is newer than anything already stored for that target. Both bouncers then get
the right answer from the same rule.

What this pins:

  1. a live line is recorded (the case that must not regress);
  2. a replayed line *newer* than everything stored is recorded -- the Wicket
     case, and the reported loss;
  3. a replayed line *older* than what is stored is not -- the ZNC case, and
     the duplicate-log bug that must not come back;
  4. a replay with no server-time tag is not recorded, because with nothing to
     compare the old rule is the safer of the two failures.

Usage:
  python tests/test_playback_record.py     # from the qtpyrc root directory
"""

import atexit
import os
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NICK = 'tester'
CHAN = '#css'

CONFIG = """\
nick: %(nick)s
user: %(nick)s
realname: qtpyrc playback record test

window_mode: normal
view_mode: tabbed

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
link_preview: {enabled: false}
ident: {enabled: false}
logging:
  hang_watchdog: {enabled: false}
history_replay: {channels: 0, queries: 0, bg_enabled: false}

networks:
  testnet:
    name: TestNet
    nick: %(nick)s
    auto_connect: true
    persist_autojoins: false
    server:
      host: 127.0.0.1
      port: %(port)d
      tls: false
    auto_join:
      '%(chan)s':
"""


def free_port():
  s = socket.socket()
  s.bind(('127.0.0.1', 0))
  port = s.getsockname()[1]
  s.close()
  return port


PORT = free_port()
CTRL_PORT = free_port()

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-playback-')
atexit.register(shutil.rmtree, tmpdir, True)
cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG % {'port': PORT, 'nick': NICK, 'chan': CHAN})

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
os.chdir(ROOT)
sys.argv = ['qtpyrc.py', '-c', cfg]

from irc_test_server import wait_until_listening


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
failures = []

LIVE = 'a live line %d' % os.getpid()
NEWER = 'replayed and never seen %d' % os.getpid()
OLDER = 'replayed but already stored %d' % os.getpid()
UNTAGGED = 'replayed with no time tag %d' % os.getpid()


def check(cond, msg):
  if not cond:
    failures.append(msg)


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def stored_rows():
  db = state.historydb
  db.flush_pending()
  return db.read_conn().execute(
    "SELECT ts, text FROM history WHERE channel = ?", (CHAN.lower(),)).fetchall()


def stored_texts():
  return [t or '' for _ts, t in stored_rows()]


def logged_text():
  state.irclogger.flush()
  client = next(iter(state.clients))
  p = state.irclogger._path(client.conn._log_network, CHAN)
  if not os.path.exists(p):
    return ''
  with open(p, encoding='utf-8', errors='replace') as f:
    return f.read()


def send_live():
  control('CHANMSG alice %s %s' % (CHAN, LIVE))
  QTimer.singleShot(1500, send_replay)


def send_replay():
  """A batch whose lines are timestamped either side of the live one."""
  import datetime
  now = datetime.datetime.now(datetime.timezone.utc)
  older = (now - datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
  newer = (now + datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
  control('BATCH pb chathistory')
  control('CHANMSGTIME %s alice %s %s' % (older, CHAN, OLDER))
  control('CHANMSGTIME %s alice %s %s' % (newer, CHAN, NEWER))
  control('CHANMSGTIME - alice %s %s' % (CHAN, UNTAGGED))
  control('ENDBATCH pb')
  QTimer.singleShot(2500, inspect)


def inspect():
  try:
    texts = stored_texts()
    log = logged_text()

    check(LIVE in texts, 'a live message was not saved to history at all, so '
                         'this test proves nothing')
    check(LIVE in log, 'a live message was not written to the log file')

    check(NEWER in texts,
          'a replayed message newer than anything stored was not saved. That '
          'is the reported loss: the client was closed, the bouncer buffered, '
          'and on reattach the backlog was drawn and recorded nowhere.')
    check(NEWER in log,
          'a replayed message newer than anything stored was not logged')

    check(OLDER not in texts,
          'a replayed message older than what is already stored was saved '
          'again -- that is the ZNC duplicate-tail bug coming back')
    check(OLDER not in log,
          'a replayed message older than what is already stored was logged '
          'again')

    check(UNTAGGED not in texts,
          'a replayed message with no server-time tag was recorded; with '
          'nothing to compare against it must fall back to the old rule')

    # ...and it is recorded under the time it *happened*, not the time it
    # arrived. The replay carries a server-time tag two hours ahead here, so a
    # row stamped "now" means the backlog was dated to the reconnection --
    # which puts every replayed line at the wrong place in its own history and
    # makes the stored row disagree with the line already on screen.
    import datetime
    stamped = [ts for ts, t in stored_rows() if t == NEWER]
    check(stamped, 'the replayed line is not in the table, so its timestamp '
                   'cannot be checked')
    if stamped:
      want = (datetime.datetime.now() +
              datetime.timedelta(hours=2)).strftime('%Y-%m-%d %H:%M')
      check(stamped[0].startswith(want),
            'the replayed line was stored at %r; expected the server-time it '
            'carried (%r...) rather than the moment it arrived'
            % (stamped[0], want))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return finish(1)
  print('playback is recorded when it is new and skipped when it is not.')
  return finish(0)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(3000, send_live)

try:
  runpy.run_path(os.path.join(ROOT, 'qtpyrc.py'), run_name='__main__')
except SystemExit:
  pass
finally:
  try:
    server.terminate()
    server.wait(timeout=5)
  except Exception:
    pass

sys.exit(EXIT)
