"""Notices are logged, in both directions, into the file the conversation is in.

Notices were the one kind of chat line qtpyrc never wrote to a log file at all.
Incoming ones were displayed and saved to the history database but not logged;
outgoing ones (`/notice`) were displayed and nothing else.  So a channel's log
had everybody's messages and nobody's notices, and a conversation conducted in
notices left no trace on disk.

The part that is easy to get wrong is not *whether* to log but *where*, and it
has to be answered the same way in both directions or the two halves of one
conversation end up in two files -- which is the `/msg` bug wearing a different
hat.  The answer used here is the one every other log call in qtpyrc already
uses: **the file is named after the conversation partner.**

  - a notice to a channel     -> that channel's log
  - a notice from/to a nick   -> that nick's log
  - a notice from the server  -> the server log (no user to attribute it to)

Note what that is *not*: the window the line was drawn in.  An incoming private
notice is shown in "whichever window was active", and an outgoing `/notice` is
shown in whichever window you typed it in.  Neither is a stable place, so two
notices from the same person could otherwise land in two different files.

The second half of this test is the playback gate.  `chanmsg`, `action` and
`privmsg` gated their *history* writes on `_in_playback_batch()` and left the
*log* writes beside them ungated, so a bouncer replaying the tail of every
channel on reconnect appended a duplicate copy of those lines to the logs every
time.  Adding a notice log call would have inherited the same bug, so all of
them now go through `IRCClient._log_chat`, which holds the gate.  Testing that
needed a server that can open a batch at all: `irc_test_server` used to NAK
every capability, so `batch` was never negotiated and nothing in the client's
playback handling was reachable from a test.

  python tests/test_notice_log.py
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

PEER = 'bob'
CHAN = '#notices'
NONCE = 'nl%d' % (time.time() * 1000)

CHAN_IN = 'channel notice from bob %s' % NONCE
PRIV_IN = 'private notice from bob %s' % NONCE
SRV_IN = 'notice straight from the server %s' % NONCE
CHAN_OUT = 'channel notice from me %s' % NONCE
PRIV_OUT = 'private notice from me %s' % NONCE
# Sent inside a znc.in/playback batch: the client has already logged this line
# once, when it first arrived, so logging it again on every reconnect is a
# duplicate.
REPLAYED_MSG = 'replayed message %s' % NONCE
REPLAYED_NOTICE = 'replayed notice %s' % NONCE
# Sent after the batch closes, so a gate that simply stopped logging would fail
# here rather than passing the check above for the wrong reason.
LIVE_AFTER = 'live message after the batch %s' % NONCE

CONFIG = """\
nick: tester
user: tester
realname: qtpyrc notice log test

window_mode: normal
view_mode: tabbed
new_tab_state: normal

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
  notice: {beep: false, desktop: false}
link_preview: {enabled: false}
ident: {enabled: false}
logging:
  dir: %(logdir)s
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
    auto_join:
      '%(chan)s': ''
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

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-noticelog-')
atexit.register(shutil.rmtree, tmpdir, True)

LOGDIR = os.path.join(tmpdir, 'logs')

cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG % {'port': PORT, 'logdir': LOGDIR.replace('\\', '/'),
                    'chan': CHAN})

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.argv = ['qtpyrc.py', '-c', cfg]

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


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def control(line):
  s = socket.create_connection(('127.0.0.1', CTRL_PORT), timeout=5)
  s.sendall((line + '\n').encode())
  time.sleep(0.3)
  s.close()


def drive():
  try:
    from commands import docommand
    client = next(iter(state.clients))
    srvwin = client.window

    # --- incoming, the three sender shapes ---
    control('NOTICE %s %s %s' % (PEER, CHAN, CHAN_IN))
    control('NOTICE %s tester %s' % (PEER, PRIV_IN))
    control('SERVERNOTICE %s' % SRV_IN)

    # --- outgoing, from the server window in both cases, so that "the window
    #     it was typed in" and "the file it belongs in" are different ---
    docommand(srvwin, 'notice', '%s %s' % (CHAN, CHAN_OUT))
    docommand(srvwin, 'notice', '%s %s' % (PEER, PRIV_OUT))

    # --- a bouncer replaying the channel on reconnect ---
    control('BATCH pb znc.in/playback %s' % CHAN)
    control('CHANMSG alice %s %s' % (CHAN, REPLAYED_MSG))
    control('NOTICE alice %s %s' % (CHAN, REPLAYED_NOTICE))
    control('ENDBATCH pb')

    # --- and ordinary traffic once it is over ---
    control('CHANMSG alice %s %s' % (CHAN, LIVE_AFTER))
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
    net = client.conn._log_network

    # Log writes are queued to the bgwriter thread, so a test that reads the
    # files has to say when it wants them to have arrived. Without this the
    # suite passes or fails on how busy the disk happens to be.
    state.irclogger.flush()

    def logfile(target):
      """Read the log the *logger* says this target belongs in.

      Asked of `irclogger._path` rather than rebuilt here: the question this
      test exists to answer is which file a line lands in, so reconstructing
      the naming scheme locally would let both copies be wrong together.
      """
      p = state.irclogger._path(net, target)
      if not os.path.exists(p):
        return ''
      with open(p, encoding='utf-8', errors='replace') as f:
        return f.read()

    chan_log = logfile(CHAN)
    peer_log = logfile(PEER)
    srv_log = logfile('_server_')
    everything = ''
    for root, _dirs, files in os.walk(LOGDIR):
      for fn in files:
        with open(os.path.join(root, fn), encoding='utf-8',
                  errors='replace') as f:
          everything += f.read()

    # --- 1. every notice is in a log file at all -------------------------
    for label, sent in (('an incoming channel notice', CHAN_IN),
                        ('an incoming private notice', PRIV_IN),
                        ('an incoming server notice', SRV_IN),
                        ('an outgoing /notice to a channel', CHAN_OUT),
                        ('an outgoing /notice to a nick', PRIV_OUT)):
      check(sent in everything,
            '%s reached no log file at all: it was displayed and then dropped, '
            'so a conversation held in notices leaves nothing on disk' % label)

    # --- 2. and in the *right* one ---------------------------------------
    check(CHAN_IN in chan_log,
          'an incoming channel notice is not in %s\'s log' % CHAN)
    check(CHAN_OUT in chan_log,
          'an outgoing /notice %s is not in that channel\'s log' % CHAN)
    check(PRIV_IN in peer_log,
          'an incoming private notice from %s is not in %s\'s log -- filing it '
          'under the window it was shown in ("whichever was active") would put '
          'two notices from the same person in two different files' % (PEER, PEER))
    check(PRIV_OUT in peer_log,
          '/notice %s <text> is not in %s\'s log. Both halves of a notice '
          'conversation must be in one file, in order, or reading it back is '
          'the /msg bug again: half the exchange stored somewhere else'
          % (PEER, PEER))
    check(SRV_IN in srv_log,
          'a notice from the server itself is not in the server log')

    # --- 3. the window it was drawn in is not where it was filed ---------
    check(PRIV_OUT not in srv_log,
          '/notice %s <text>, typed in the server window, was logged to the '
          'server log. The echo is drawn there, but the line belongs to the '
          'conversation with %s' % (PEER, PEER))
    check(PRIV_IN not in srv_log,
          'an incoming private notice was logged to the server log rather '
          'than under its sender')

    # --- 4. both directions are formatted the same -----------------------
    check('-%s- %s' % (PEER, PRIV_IN) in peer_log,
          'an incoming notice is not logged as "-nick- text": %r'
          % peer_log[-300:])
    check('-tester- %s' % PRIV_OUT in peer_log,
          'an outgoing notice is not logged as "-nick- text", so the two '
          'directions of one conversation are not comparable: %r'
          % peer_log[-300:])

    # --- 5. playback is not logged again ---------------------------------
    for label, sent in (('a message', REPLAYED_MSG),
                        ('a notice', REPLAYED_NOTICE)):
      check(sent not in everything,
            '%s replayed inside a znc.in/playback batch was written to the log '
            'file. It was already logged when it first arrived, so a bouncer '
            'that replays on every reconnect appends a duplicate copy of the '
            'tail of every channel each time.' % label)

    # And the gate discriminates: ordinary traffic after the batch is logged.
    check(LIVE_AFTER in chan_log,
          'a message sent after the batch closed was not logged -- the '
          'playback gate is stuck on, which would make the check above pass '
          'for the wrong reason')

    if failures:
      print('\nFAILED (%d):' % len(failures))
      for f in failures:
        print('  - %s' % f)
      for name, body in (('channel', chan_log), (PEER, peer_log),
                         ('server', srv_log)):
        print('\n%s log:' % name)
        for ln in body.splitlines()[-12:]:
          print('    %s' % ln.encode('ascii', 'backslashreplace').decode())
      return finish(1)

    print('notices are logged in both directions, into the conversation '
          'partner\'s file rather than the window they were shown in, and '
          'nothing replayed inside a playback batch is logged twice.')
    return finish(0)
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(3500, drive)

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
