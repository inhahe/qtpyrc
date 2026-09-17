"""A window is not a membership, and /join must never do nothing in silence.

Reported 2026-09-05: "/join #forum <key> ... opens the channel window and says
can't join because it's +k, even though that's the correct password."

The key was right. It never left the client. `Commands.join` treated the
existence of a `Channel` object *with a window* as "already in the channel"
and returned -- no JOIN, no key, and nothing said. A window is the wrong
question: it deliberately outlives a part, a kick and a disconnect, because
its backscroll is worth keeping (`_deactivate_channel` keeps the window and
clears `Channel.active`, `rejoined()` sets it back). So every /join meant to
*re*-join a channel whose window was still open was swallowed, and the user
saw whatever error was already in that window.

In the reported case the window was not even ours to trust: the bouncer had
sent a synthesised JOIN for a channel it only believed it was in, having kept
its channel table across a reconnect. From the client's side that is
indistinguishable from a real join, which is exactly why the client must ask
"am I in it?" rather than "is there a window?".

Two properties, and the second is the one that took seven hours of somebody's
evening:

  * a /join for a channel we are not in puts a JOIN on the wire, with the key;
  * a /join that is genuinely a no-op still says so when a key came with it,
    because a key means the user believes they are not in the channel.

Asserted on the wire: a JOIN built without the key and no JOIN at all are
indistinguishable from inside the client, and the bug was the second one.

No Qt event loop and no server -- stub window, stub conn -- so it is cheap
enough to run every time.

Usage:
  python tests/test_join_command.py     # from the qtpyrc root directory
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

failures = []

KEY = '∆\U0001f95a\U0001f456'   # the reporter's key: astral-plane emoji


def check(cond, msg):
  if not cond:
    failures.append(msg)


class Conn:
  """Just enough IRCClient. `join` is the real one, so the wire is real."""

  def __init__(self, client):
    self.sent = []
    self.client = client
    self.nickname = 'tester'
    self._user_joins = set()
    self._activate_on_join = set()

  def sendLine(self, line):
    self.sent.append(line)

  def join(self, channel, key=None):
    from asyncirc import IRCClient
    IRCClient.join(self, channel, key)

  @staticmethod
  def is_channel(name):
    return bool(name) and name[0] in '#&!+'

  @staticmethod
  def irclower(s):
    return s.lower()


class Chan:
  """A Channel as far as /join is concerned: a name, a window, and whether we
  are actually in it."""

  def __init__(self, name, active=True):
    self.name = name
    self.active = active
    self.window = Subwin()


class Subwin:
  def __init__(self):
    self.subwindow = object()


class Client:
  def __init__(self):
    self.conn = None
    self.channels = {}
    self.users = {}
    self.network_key = 'testnet'
    self.network = 'TestNet'


class Win:
  def __init__(self, client):
    self.type = 'server'
    self.channel = None
    self.client = client
    self.errors = []

  def redmessage(self, text, *a, **k):
    self.errors.append(text)

  def addline(self, text, *a, **k):
    pass


class Workspace:
  def __init__(self):
    self.activated = []

  def setActiveSubWindow(self, sub):
    self.activated.append(sub)


def run(arg, channels=None):
  """Run /join and return (lines sent, errors shown, windows activated)."""
  import state
  from commands import Commands

  client = Client()
  conn = Conn(client)
  client.conn = conn
  for name, active in (channels or {}).items():
    client.channels[conn.irclower(name)] = Chan(name, active)

  ws = Workspace()
  saved = getattr(state, 'app', None)
  state.app = type('A', (), {'mainwin': type('M', (), {'workspace': ws})()})()
  try:
    win = Win(client)
    Commands.join(win, arg)
  finally:
    state.app = saved
  return conn.sent, win.errors, ws.activated


def main():
  # --- 1. the ordinary case: a channel we have never seen ------------------
  sent, errors, _ = run('#forum ' + KEY)
  check(sent == ['JOIN #forum ' + KEY],
        'a plain /join with a key put %r on the wire' % (sent,))
  check(not errors, 'a working /join complained: %r' % (errors,))

  sent, _, _ = run('#forum')
  check(sent == ['JOIN #forum'], 'a keyless /join sent %r' % (sent,))

  # A bare name is a channel name.
  sent, _, _ = run('forum ' + KEY)
  check(sent == ['JOIN #forum ' + KEY],
        '/join without a # sent %r' % (sent,))

  # --- 2. the reported bug: a window we are not in -------------------------
  # The channel object and its window are still there -- parted, kicked,
  # disconnected, or handed to us by a bouncer that was wrong -- but we are
  # not in the channel.
  sent, errors, _ = run('#forum ' + KEY, {'#forum': False})
  check(sent == ['JOIN #forum ' + KEY],
        'with a window open but no membership, /join sent %r. This is the '
        'report: the key never left the client, so the stale error in that '
        'window was all the user ever saw.' % (sent,))
  check(not errors,
        'rejoining a channel we are not in was refused: %r' % (errors,))

  sent, _, _ = run('#forum', {'#forum': False})
  check(sent == ['JOIN #forum'],
        'a keyless rejoin sent %r' % (sent,))

  # It is the same channel whatever the spelling.
  sent, _, _ = run('#FORUM ' + KEY, {'#forum': False})
  check(sent == ['JOIN #FORUM ' + KEY],
        'a differently-cased rejoin sent %r' % (sent,))

  # --- 3. genuinely already in it ------------------------------------------
  # Nothing goes on the wire -- a second JOIN is a no-op at the server -- and
  # the window comes forward, which answers the request completely.
  sent, errors, activated = run('#forum', {'#forum': True})
  check(sent == [],
        'a /join for a channel we are in sent %r; the server would ignore it '
        'and the flood queue would not' % (sent,))
  check(len(activated) == 1,
        '/join for an open channel did not bring its window forward')
  check(not errors,
        'switching to an open channel complained: %r' % (errors,))

  # ...except that a key is a statement, and dropping it in silence is how
  # the reporter concluded the server had rejected it.
  sent, errors, activated = run('#forum ' + KEY, {'#forum': True})
  check(sent == [], 'a no-op /join with a key still sent %r' % (sent,))
  check(len(activated) == 1,
        'the window did not come forward when a key was supplied')
  check(any('#forum' in e and 'key' in e.lower() for e in errors),
        'a key handed to a /join that could not use it was discarded without '
        'a word (messages: %r)' % (errors,))

  # -z means "do not switch to it", and must not turn the notice off too.
  sent, errors, activated = run('-z #forum ' + KEY, {'#forum': True})
  check(activated == [], '-z activated the window anyway')
  check(errors, '-z also silenced the discarded-key notice: %r' % (errors,))

  # --- 4. flags and the join bookkeeping -----------------------------------
  import state
  from commands import Commands
  client = Client()
  conn = Conn(client)
  client.conn = conn
  saved = getattr(state, 'app', None)
  state.app = type('A', (), {'mainwin': type('M', (), {'workspace': Workspace()})()})()
  try:
    Commands.join(Win(client), '#forum ' + KEY)
  finally:
    state.app = saved
  check('#forum' in conn._user_joins,
        'the join was not marked user-initiated, so persist_autojoins will '
        'not record it')
  check('#forum' in conn._activate_on_join,
        'the channel was not queued for activation on join')

  # --- 5. no connection ----------------------------------------------------
  client = Client()          # client.conn stays None
  win = Win(client)
  Commands.join(win, '#forum ' + KEY)
  check(win.errors and 'Not connected' in win.errors[0],
        '/join while disconnected said %r' % (win.errors,))

  # --- 6. no arguments -----------------------------------------------------
  client = Client()
  client.conn = Conn(client)
  win = Win(client)
  Commands.join(win, '')
  check(win.errors and 'Usage' in win.errors[0],
        '/join with no channel said %r' % (win.errors,))

  if failures:
    print('FAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('/join asks whether we are in the channel, not whether a window is '
        'open, and never discards a key in silence.')
  return 0


sys.exit(main())
