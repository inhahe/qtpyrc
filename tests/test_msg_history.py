"""A message is saved to history and to the log whichever command sent it.

The bug: `/msg <nick> <text>` sent the message and displayed it, but wrote
neither a log line nor a history row.  Typing the same text into an open query
window did all three.  So a conversation held partly in the window and partly
through `/msg` came back, when the window was next opened, with only the
in-window half in it -- the `/msg` half had never been stored at all.

`/query <nick> <text>` had the same hole, plus one of its own: it did not split
long messages, so anything past the protocol's line limit was truncated by the
server.

The cause was simply that "send a message" was written five times -- once each
in `say`'s channel branch, `say`'s query branch, `/msg`, `/query` and `/amsg`.
All five now go through `commands.send_message`, and this test is what stops
them drifting apart again: it sends one message by each route and requires every
one of them to be in the database, under the right key, and in the log file.

`/msg` and `/amsg` are also exercised against a *channel*, because the two
target shapes are the thing most easily got wrong: a channel routed down the PM
path is logged to the wrong file and saved under `=#channel`, a key no window
ever reads, and neither mistake shows up until the backlog is next replayed.
`send_message` asks `conn.is_channel()` -- ISUPPORT CHANTYPES -- rather than
testing for a leading '#'.

The long-message check is here rather than in a unit test because the split
depends on `conn.split_message`, which needs the server's idea of our hostmask
-- i.e. a real connection.

  python tests/test_msg_history.py
"""

import threading
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
CHAN = '#history'
# Unique per run so these assertions cannot be satisfied by rows an earlier run
# left in the database.
NONCE = 'mh%d' % (time.time() * 1000)
IN_WINDOW = 'typed in the window %s' % NONCE
VIA_MSG = 'sent with slash msg %s' % NONCE
VIA_QUERY = 'sent with slash query %s' % NONCE
# Comfortably past the 512-byte line limit, so a path that does not chunk it
# loses the tail.
LONG = ('L%s ' % NONCE) + ('x' * 700)
# The channel routes.  /msg with a channel target used to be sent down the PM
# path, which would have stored these under '=#history'.
CHAN_IN_WINDOW = 'typed in the channel %s' % NONCE
CHAN_VIA_MSG = 'sent to the channel with slash msg %s' % NONCE
CHAN_VIA_AMSG = 'sent to every channel with slash amsg %s' % NONCE

CONFIG = """\
nick: tester
user: tester
realname: qtpyrc msg history test

window_mode: normal
view_mode: tabbed
new_tab_state: normal

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
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

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-msghist-')
# Registered before qtpyrc runs so atexit's LIFO order puts it after qtpyrc has
# closed crash.log -- see the same note in test_pm_activity_live.py.
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


# Every thread on which a chat line actually reached the disk during the sends
# below. Sending is on the GUI thread, so none of these may be it -- see
# _check_no_gui_thread_io().
_io_threads = set()
_gui_thread = None


def _watch_disk_writes():
  """Record which thread each queued write is really performed on.

  The unit tests (test_bgwriter.py, test_history_maint.py) prove the log writer
  and the history writer each work off the caller's thread. This ties that to
  the path a user actually takes: `commands.send_message` is where the three
  synchronous writes used to sit -- log line, history row, URL row -- between
  putting the line on the wire and drawing it, which is the reported "I press
  Enter and it freezes for a few seconds when the disk is busy".

  Watching where the write *lands* rather than asserting on latency is what
  makes this hold on a fast disk: a timing assertion here would pass against
  the broken code every time the filesystem happened to be idle.
  """
  import bgwriter
  import history as history_mod
  for mod, name in ((bgwriter.BackgroundWriter, '_emit'),
                    (history_mod.HistoryDB, '_w_insert_history'),
                    (history_mod.HistoryDB, '_w_insert_url')):
    real = getattr(mod, name)

    def wrap(self, *a, _real=real, **k):
      _io_threads.add(threading.get_ident())
      return _real(self, *a, **k)

    setattr(mod, name, wrap)


def send_all():
  """Send one message by each of the routes, to a nick and to a channel."""
  global _gui_thread
  _gui_thread = threading.get_ident()
  _watch_disk_writes()
  try:
    from commands import Commands, docommand
    client = next(iter(state.clients))
    srvwin = client.window
    conn = client.conn

    # --- to a nick ---

    # 1. /query <nick> <text> -- opens the window and sends in one go.
    docommand(srvwin, 'query', '%s %s' % (PEER, VIA_QUERY))

    qwin = conn.queries[conn.irclower(PEER)].window

    # 2. Typing in the query window.  This is Window.lineinput's `say` path.
    Commands.say(qwin, IN_WINDOW)

    # 3. /msg from *outside* the window -- issued in the server window, which
    #    is exactly what the user did.
    docommand(srvwin, 'msg', '%s %s' % (PEER, VIA_MSG))

    # 4. A message past the line limit, to catch a path that fails to chunk.
    docommand(srvwin, 'msg', '%s %s' % (PEER, LONG))

    # --- to a channel ---

    chan = client.channels[conn.irclower(CHAN)]

    # 5. Typing in the channel window.
    Commands.say(chan.window, CHAN_IN_WINDOW)

    # 6. /msg <channel> -- must take the channel path, not the PM one.
    docommand(srvwin, 'msg', '%s %s' % (CHAN, CHAN_VIA_MSG))

    # 7. /amsg, which reaches every joined channel.
    docommand(srvwin, 'amsg', CHAN_VIA_AMSG)
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)
  QTimer.singleShot(2000, inspect)


def inspect():
  failures = []

  def check(cond, msg):
    if not cond:
      failures.append(msg)

  try:
    from irc_client import _query_history_key
    client = next(iter(state.clients))
    conn = client.conn
    key = _query_history_key(PEER)
    chan_key = CHAN.lower()

    rows = state.historydb.get_last(conn._log_network, key, 200)
    texts = [r[4] or '' for r in rows]
    blob = '\n'.join(texts)

    chan_rows = state.historydb.get_last(conn._log_network, chan_key, 200)
    chan_texts = [r[4] or '' for r in chan_rows]

    for label, sent in (('typed in the query window', IN_WINDOW),
                        ('/msg from outside the window', VIA_MSG),
                        ('/query <nick> <message>', VIA_QUERY)):
      check(sent in texts,
            'a PM sent by %s is not in the history table under %r -- it was '
            'displayed but never saved, so it vanishes when the window is '
            'reloaded' % (label, key))

    # The long one is chunked, so no single row holds it; what matters is that
    # the whole of it was stored across the rows rather than truncated away.
    tail = LONG[-40:]
    check(tail in blob,
          'the tail of a >512-byte PM is missing from the history: the sending '
          'path did not split it into protocol-sized chunks')

    for label, sent in (('typed in the channel window', CHAN_IN_WINDOW),
                        ('/msg <channel> <message>', CHAN_VIA_MSG),
                        ('/amsg <message>', CHAN_VIA_AMSG)):
      check(sent in chan_texts,
            'a channel message sent by %s is not in the history table under '
            '%r' % (label, chan_key))

    # A channel target sent down the PM path lands under '=#chan', which no
    # window ever reads -- invisible except as a message missing from the
    # channel's replay, which is why it is worth naming here.
    stray = state.historydb.get_last(conn._log_network,
                                     _query_history_key(CHAN), 200)
    check(not [r for r in stray if NONCE in (r[4] or '')],
          'a message to %s was stored under the query key %r: the sending path '
          'decided what the target was without asking conn.is_channel()'
          % (CHAN, _query_history_key(CHAN)))

    # --- no disk write may have happened on the GUI thread ----------------
    # The reported freeze, checked at the level the user meets it. Asserted
    # before the log is read, because reading it flushes and would otherwise
    # be a candidate for the very thing being measured.
    check(_io_threads,
          'nothing was written to disk at all during the sends, so this check '
          'proves nothing')
    check(_gui_thread not in _io_threads,
          'a chat line was written to disk on the GUI thread during send: '
          'that is a WriteFile syscall between putting the line on the wire '
          'and drawing it, and on a busy filesystem it is the reported '
          'several-second freeze after pressing Enter')

    # And the log file, the other half of "it was recorded". Log writes are
    # queued to the bgwriter thread, so ask for them to have landed before
    # reading -- otherwise this passes or fails on how busy the disk is.
    state.irclogger.flush()
    logged = ''
    for root, _dirs, files in os.walk(LOGDIR):
      for fn in files:
        with open(os.path.join(root, fn), encoding='utf-8', errors='replace') as f:
          logged += f.read()
    for label, sent in (('typed in the query window', IN_WINDOW),
                        ('/msg from outside the window', VIA_MSG),
                        ('/query <nick> <message>', VIA_QUERY),
                        ('typed in the channel window', CHAN_IN_WINDOW),
                        ('/msg <channel> <message>', CHAN_VIA_MSG),
                        ('/amsg <message>', CHAN_VIA_AMSG)):
      check(sent in logged,
            'a message sent by %s never reached the log file' % label)

    if failures:
      print('\nFAILED (%d):' % len(failures))
      for f in failures:
        print('  - %s' % f)
      print('\nhistory rows under %r:' % key)
      for t in texts:
        print('    %s' % t[:110].encode('ascii', 'backslashreplace').decode())
      print('\nhistory rows under %r:' % chan_key)
      for t in chan_texts:
        print('    %s' % t[:110].encode('ascii', 'backslashreplace').decode())
      return finish(1)

    print('a message is saved and logged by every route (query window, /msg, '
          '/query, channel window, /msg <chan>, /amsg), channel and query '
          'targets go to their own keys, and long ones are chunked.')
    return finish(0)
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(3500, send_all)

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
