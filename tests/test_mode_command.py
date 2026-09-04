"""/mode must exist, and must put the right MODE on the wire.

It never existed. `docommand` has no raw pass-through -- an unrecognised name
gets "[Unknown command: /mode]" -- so every documented use of it silently did
nothing, including the `Kick+Ban:/mode # +b $$1!*@* | /kick # $$1` popup and the
kick-ban /on example, both of which are in `docs/reference.md`.

What is checked here is the wire, via the test server's RECEIVED control
command, because that is the only place the answer is unambiguous: a command
that builds the wrong MODE and one that builds none look identical from inside
the client.

The rule that needs pinning is how the target is decided. The first token is
the target unless it starts with '+' or '-', which is what separates
`/mode +o alice` (channel implied, alice is a parameter) from `/mode alice +o`
(alice is the target). Getting that backwards turns an op into a user-mode
change on a nick, or vice versa, and neither reports an error -- the server
just does something other than what was asked.

Usage:
  python tests/test_mode_command.py     # from the qtpyrc root directory
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
CHAN = '#ops'

CONFIG = """\
nick: %(nick)s
user: %(nick)s
realname: qtpyrc mode command test

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

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-modetest-')
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


def sent_modes():
  """Every MODE line the server has received from us, oldest first."""
  raw = control('RECEIVED MODE')
  return [l.strip() for l in raw.split(' | ') if l.strip()]


def wait_for(count, then, tries=80):
  """Poll until *count* MODE lines have reached the server, then call *then*.

  sendLine() is flood-controlled -- a burst is queued and released over
  seconds -- so "send some commands, look at the wire" needs to wait for
  arrival rather than guess a delay. A fixed sleep here fails on a busy
  machine and reports it as a wrong MODE line, which is a lie about which
  part is broken.
  """
  if len(sent_modes()) >= count or tries <= 0:
    return then()
  QTimer.singleShot(200, lambda: wait_for(count, then, tries - 1))


# Sent after the forms that must be refused. Waiting for a *presence* is
# deterministic where waiting for an absence is not: anything the refused
# commands wrongly sent is queued ahead of this, so if the sentinel has
# arrived and nothing else did, nothing else was sent.
SENTINEL = 'MODE %s +t' % CHAN


def inspect():
  try:
    from commands import docommand
    client = next(iter(state.clients))
    conn = client.conn
    srvwin = client.window
    chan = client.channels.get(conn.irclower(CHAN))
    if chan is None:
      check(False, 'the client never joined %s' % CHAN)
      return finish(1)
    chanwin = chan.window

    before = len(sent_modes())
    cases = [
      # (window, argument, expected MODE line)
      (chanwin, '',                    'MODE %s' % CHAN),
      (chanwin, '+imnt',               'MODE %s +imnt' % CHAN),
      (chanwin, '+o alice',            'MODE %s +o alice' % CHAN),
      (chanwin, '-o alice',            'MODE %s -o alice' % CHAN),
      # An explicit channel, from any window.
      (srvwin,  '%s +o alice' % CHAN,  'MODE %s +o alice' % CHAN),
      (chanwin, '%s +k secret' % CHAN, 'MODE %s +k secret' % CHAN),
      # '#' means the current channel -- what the documented popup and /on
      # examples use. popups.py substitutes it before the command runs, the
      # /on path does not, so the command has to understand it too.
      (chanwin, '# +b alice!*@*',      'MODE %s +b alice!*@*' % CHAN),
      # A nick target is a *user* mode, not a channel one: the first token is
      # the target because it does not start with + or -.
      (chanwin, '%s +x' % NICK,        'MODE %s +x' % NICK),
      (srvwin,  '%s +x' % NICK,        'MODE %s +x' % NICK),
      # Target alone is a query.
      (srvwin,  CHAN,                  'MODE %s' % CHAN),
      # Several modes and parameters in one line -- the reason this command
      # exists at all, since no shortcut can express it.
      (chanwin, '+ovl alice bob 50',   'MODE %s +ovl alice bob 50' % CHAN),
    ]
    for win, arg, _expected in cases:
      docommand(win, 'mode', arg)
    wait_for(before + len(cases),
             lambda: verify(cases, before, srvwin, chanwin))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


def verify(cases, before, srvwin, chanwin):
  from commands import docommand
  try:
    got = sent_modes()[before:]
    want = [expected for _w, _a, expected in cases]
    check(got == want,
          'the MODE lines that reached the server do not match.\n'
          '      sent: %r\n'
          '  expected: %r' % (got, want))

    # --- the forms that must refuse rather than send ---------------------
    # A modes-only /mode outside a channel has nothing to apply to. Sending
    # "MODE +imnt" would be read by the server as a *user* mode on a nick
    # called "+imnt", so guessing is worse than refusing. Same for a bare '#'
    # that resolves to nothing.
    n = len(sent_modes())
    docommand(srvwin, 'mode', '+imnt')
    docommand(srvwin, 'mode', '')
    docommand(srvwin, 'mode', '# +o alice')
    docommand(chanwin, 'mode', '+t')      # the sentinel
    wait_for(n + 1, lambda: verify_refusals(n))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


def verify_refusals(n):
  from commands import docommand
  try:
    after = sent_modes()[n:]
    check(after == [SENTINEL],
          '/mode sent something for a form it cannot resolve a target for. '
          'After three commands that must all refuse, plus one that must '
          'send, the wire shows %r -- expected only %r'
          % (after, [SENTINEL]))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)
  return stage_kick()


def sent_kicks():
  raw = control('RECEIVED KICK')
  return [l.strip() for l in raw.split(' | ') if l.strip()]


def stage_kick():
  """The other half of the documented `Kick+Ban` popup.

  `/mode # +b $$1!*@* | /kick # $$1` pairs a MODE with a KICK, and /kick took
  a nick only -- so the expanded "#chan" became the nick to kick and the real
  nick became the reason. It now accepts an optional leading channel, which no
  valid nick can be mistaken for.
  """
  from commands import docommand
  try:
    client = next(iter(state.clients))
    conn = client.conn
    srvwin = client.window
    chan = client.channels.get(conn.irclower(CHAN))
    chanwin = chan.window
    n = len(sent_kicks())
    docommand(chanwin, 'kick', 'alice')                 # nick only, as before
    docommand(chanwin, 'kick', '# bob')                 # bare '#'
    docommand(srvwin, 'kick', '%s carol spam' % CHAN)   # explicit channel
    wait_kicks(n + 3, lambda: verify_kick(n))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


def wait_kicks(count, then, tries=80):
  if len(sent_kicks()) >= count or tries <= 0:
    return then()
  QTimer.singleShot(200, lambda: wait_kicks(count, then, tries - 1))


def verify_kick(n):
  try:
    got = sent_kicks()[n:]
    want = ['KICK %s alice' % CHAN,
            'KICK %s bob' % CHAN,
            'KICK %s carol :spam' % CHAN]
    check(got == want,
          'the KICK lines that reached the server do not match. '
          'sent: %r  expected: %r' % (got, want))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return finish(1)
  print('/mode builds the right MODE for every documented shape and refuses '
        'the ones with no target; /kick takes the optional channel the '
        'documented popup gives it.')
  return finish(0)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(3000, inspect)

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
