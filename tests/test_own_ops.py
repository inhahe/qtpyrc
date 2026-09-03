"""Your own mode prefix must show in the nick list, from NAMES and from MODE.

The report: "i'm in #ops and it doesn't show me as having ops in the user list
even though my friend saw that i have ops and doing /op on her worked". So the
server had the reporter as +o and the client knew enough to op somebody else --
only the nick list disagreed.

Two ways a prefix arrives, and both are checked here because they are different
code paths that can fail independently:

  * **RPL_NAMREPLY (353)** at join time -- ``IRCClient.names`` parses each
    token with ``parse_names_token`` and stores the symbol in
    ``User.prefix[chnlower]``. Covered as a pure function by
    tests/test_names_parse.py; what was *not* covered is whether that stored
    prefix ever reaches the list widget.
  * **MODE +o/-o** after joining -- ``IRCClient.modeChanged`` updates the same
    dict and calls ``NickItem.refresh_prefix()``.

Nothing distinguishes your own nick from anyone else's in either path, which is
exactly why this is worth a test: a bug that only shows on your own row is
invisible to code review of a function that never mentions "self".

Usage:
  python tests/test_own_ops.py      # from the qtpyrc root directory
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

NICK = 'tester'
CHAN = '#ops'
PEER = 'alice'

CONFIG = """\
nick: %(nick)s
user: %(nick)s
realname: qtpyrc own-ops test

window_mode: normal
view_mode: tabbed

show_mode_prefix_nicklist: true

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

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-opstest-')
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


def check(cond, msg):
  if not cond:
    failures.append(msg)


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def rows(chan):
  """(display text, bare nick) for every row of the nick list."""
  nl = chan.window.nickslist
  return [(nl.item(i).text(), nl.item(i)._nick) for i in range(nl.count())]


def shown_for(chan, nick):
  for text, bare in rows(chan):
    if bare.lower() == nick.lower():
      return text
  return None


def stage_names():
  """After the join: NAMES said "@tester alice bob", so the list must agree."""
  try:
    client = next(iter(state.clients))
    conn = client.conn
    chan = client.channels.get(conn.irclower(CHAN))
    if chan is None:
      check(False, 'the client never joined %s' % CHAN)
      return finish(1)

    listed = rows(chan)
    check(listed, 'the nick list is empty, so this test proves nothing')

    # The user object is the model behind the row; check both, because a
    # correct model with a stale row and a wrong model look identical on
    # screen and need different fixes.
    me = client.users.get(conn.irclower(NICK))
    check(me is not None,
          'no User object for our own nick, so nothing could carry a prefix')
    if me is not None:
      check(me.prefix.get(conn.irclower(CHAN)) == '@',
            'NAMES said "@%s" but User.prefix holds %r -- the parse or the '
            'store is wrong, not the display'
            % (NICK, me.prefix.get(conn.irclower(CHAN))))

    check(shown_for(chan, NICK) == '@' + NICK,
          'the nick list shows our own row as %r, expected %r. NAMES gave us '
          '@%s at join, which is the "it does not show me as having ops" '
          'report.' % (shown_for(chan, NICK), '@' + NICK, NICK))

    # Somebody else's prefix from the same NAMES line, as a control: if this
    # is wrong too the bug is in NAMES handling generally, not in self.
    check(shown_for(chan, PEER) == PEER,
          'a plain nick from the same NAMES line rendered as %r, expected %r'
          % (shown_for(chan, PEER), PEER))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)
  QTimer.singleShot(0, stage_mode)


def stage_mode():
  """And a MODE arriving later must update our own row too."""
  try:
    client = next(iter(state.clients))
    conn = client.conn
    chan = client.channels.get(conn.irclower(CHAN))
    control('MODE %s %s -o %s' % (PEER, CHAN, NICK))
    QTimer.singleShot(1200, lambda: stage_mode_check(chan, conn, client))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


def stage_mode_check(chan, conn, client):
  try:
    check(shown_for(chan, NICK) == NICK,
          'after -o on ourselves the list still shows %r, expected %r'
          % (shown_for(chan, NICK), NICK))
    control('MODE %s %s +o %s' % (PEER, CHAN, NICK))
    QTimer.singleShot(1200, lambda: stage_final(chan))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


def stage_final(chan):
  """And +o on us reaches our own row."""
  try:
    check(shown_for(chan, NICK) == '@' + NICK,
          'after +o on ourselves the list shows %r, expected %r -- a mode '
          'change on your own nick did not reach your own row'
          % (shown_for(chan, NICK), '@' + NICK))
    # Now the mirror-image bug: a prefix must not outlive the membership.
    control('MODE %s %s +o %s' % (PEER, CHAN, PEER))
    QTimer.singleShot(1200, lambda: stage_stale_part(chan))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


def stage_stale_part(chan):
  """alice is opped; part her, then bring her back with no ops."""
  try:
    check(shown_for(chan, PEER) == '@' + PEER,
          'could not op %s, so the stale-prefix check below proves nothing '
          '(row is %r)' % (PEER, shown_for(chan, PEER)))
    control('PART %s %s' % (PEER, CHAN))
    QTimer.singleShot(1200, lambda: stage_stale_rejoin(chan))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


def stage_stale_rejoin(chan):
  try:
    check(shown_for(chan, PEER) is None,
          '%s is still listed after parting' % PEER)
    control('JOIN %s %s' % (PEER, CHAN))
    QTimer.singleShot(1200, lambda: stage_stale_check(chan))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


def stage_stale_check(chan):
  """A rejoin with no ops must not still wear the old "@".

  User objects live in client.users for the whole session and are shared by
  every channel, so a prefix left behind by a departed membership comes back
  the next time the nick is seen. Nothing cleared it: modeChanged only removes
  a prefix it is explicitly told to, and names() used to *set* one only when
  the NAMES token carried a symbol -- so it could never clear either.
  """
  try:
    check(shown_for(chan, PEER) == PEER,
          '%s rejoined without ops but the list shows %r -- the mode prefix '
          'outlived the membership it belonged to, so a departed op comes '
          'back still wearing the "@"'
          % (PEER, shown_for(chan, PEER)))
    client = next(iter(state.clients))
    conn = client.conn
    u = client.users.get(conn.irclower(PEER))
    check(u is None or not u.prefix.get(conn.irclower(CHAN)),
          'User.prefix still holds %r for %s after a part and a clean rejoin'
          % (u.prefix.get(conn.irclower(CHAN)) if u else None, PEER))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    try:
      print('\nnick list rows: %r' % (rows(chan),))
    except Exception:
      pass
    return finish(1)
  print('own mode prefix shows in the nick list, from NAMES and from MODE.')
  return finish(0)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(3000, stage_names)

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
