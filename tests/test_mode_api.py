"""The mode shortcuts, as commands and as plugin API, must agree.

There are two ways to op somebody -- `/op alice` and `irc.op(conn, chan,
'alice')` -- and until 2026-09-04 only the first existed for most of them:
`plugin.irc` had `kick` and a bare `mode`, so a plugin wanting to ban had to
build the mode string itself, and then it was building it to *its own* rule.
That is how the two drift.

The property this pins is not "each produces a MODE line" but "**both produce
the same one**", checked by running the command and the API method over the
same inputs and comparing the wire. A ban is where it bites: `/ban alice` bans
`alice!*@*`, so a plugin that sends `+b alice` bans nothing, and a plugin that
unbans `alice` cannot remove what `/ban alice` set.

Also covered here, because they are the other half of the same sweep:

  * every shortcut takes an optional leading channel, so the mIRC spelling the
    documentation uses (`/op # alice`) works;
  * every shortcut says "[Not connected]" instead of raising AttributeError on
    ``None.sendLine`` when there is no connection. That was true of all nine of
    them, and it is the kind of thing nine copies of one function guarantees.

No Qt event loop and no server: the commands are called directly with a stub
window, which is what makes this fast enough to be worth running every time.

Usage:
  python tests/test_mode_api.py     # from the qtpyrc root directory
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


class Conn:
  """Just enough IRCClient for these commands."""

  def __init__(self):
    self.sent = []
    self.nickname = 'tester'

  def sendLine(self, line):
    self.sent.append(line)

  @staticmethod
  def is_channel(name):
    return bool(name) and name[0] in '#&!+'

  @staticmethod
  def irclower(s):
    return s.lower()


class Client:
  def __init__(self, conn):
    self.conn = conn
    # _build_exec_context reads these while assembling the namespace.
    self.users = {}
    self.channels = {}
    self.network_key = 'testnet'
    self.network = 'TestNet'


class Chan:
  def __init__(self, name):
    self.name = name


class Win:
  """Stub window: a channel window when *channel* is given, else a server one."""

  def __init__(self, conn, channel=None):
    self.type = 'channel' if channel else 'server'
    self.channel = Chan(channel) if channel else None
    self.client = Client(conn)
    self.errors = []

  def redmessage(self, text, *a, **k):
    self.errors.append(text)

  def addline(self, text, *a, **k):
    pass

  def addline_nick(self, *a, **k):
    pass


def run_command(name, arg, channel='#ops', conn=None):
  """Run Commands.<name> in a window and return (lines sent, errors)."""
  from commands import Commands
  conn = conn or Conn()
  win = Win(conn, channel)
  getattr(Commands, name)(win, arg)
  return conn.sent, win.errors


def main():
  from commands import Commands
  from plugin import irc

  SHORTCUTS = [
    # (command name, plugin method, mode string, sample target)
    ('op',       'op',       '+o', 'alice'),
    ('deop',     'deop',     '-o', 'alice'),
    ('halfop',   'halfop',   '+h', 'alice'),
    ('dehalfop', 'dehalfop', '-h', 'alice'),
    ('voice',    'voice',    '+v', 'alice'),
    ('devoice',  'devoice',  '-v', 'alice'),
    ('quiet',    'quiet',    '+q', 'alice!*@*'),
    ('unquiet',  'unquiet',  '-q', 'alice!*@*'),
    ('ban',      'ban',      '+b', 'alice'),
    ('unban',    'unban',    '-b', 'alice'),
  ]

  # --- 1. the plugin API exists at all --------------------------------------
  for _cmd, meth, _m, _t in SHORTCUTS:
    check(hasattr(irc, meth),
          'plugin.irc has no %s(); a plugin has to build the mode string by '
          'hand, which is how it ends up disagreeing with the command' % meth)
  for extra in ('kban', 'mode', 'kick'):
    check(hasattr(irc, extra), 'plugin.irc has no %s()' % extra)

  # --- 2. command and API produce the same wire -----------------------------
  for cmd, meth, modes, target in SHORTCUTS:
    sent, errors = run_command(cmd, target)
    if not hasattr(irc, meth):
      continue          # already reported above; skip rather than crash here
    conn = Conn()
    getattr(irc, meth)(conn, '#ops', target)
    check(sent == conn.sent,
          '/%s and irc.%s disagree: command sent %r, plugin API sent %r'
          % (cmd, meth, sent, conn.sent))
    check(not errors, '/%s reported %r for a plain target' % (cmd, errors))
    check(len(sent) == 1 and sent[0].startswith('MODE #ops %s ' % modes),
          '/%s sent %r, expected a "MODE #ops %s ..." line' % (cmd, sent, modes))

  # --- 3. the ban mask rule, which is the one that has to match -------------
  # A ban set from "alice" is on "alice!*@*"; an unban that does not expand the
  # same way cannot find it. And "alice@host" is nick@host, never ident@host --
  # sent verbatim it is not a mask at all and the server bans nobody.
  has_ban = hasattr(irc, 'ban') and hasattr(irc, 'unban')
  for given, expected in (('alice', 'alice!*@*'),
                          ('alice@host', 'alice!*@host'),
                          ('alice!*@*', 'alice!*@*'),
                          ('*@host', '*!*@host'),
                          ('alice!~a@h', 'alice!~a@h')):
    sent, _e = run_command('ban', given)
    check(sent == ['MODE #ops +b %s' % expected],
          '/ban %r sent %r, expected the mask %r' % (given, sent, expected))
    if not has_ban:
      continue
    conn = Conn()
    irc.ban(conn, '#ops', given)
    check(conn.sent == ['MODE #ops +b %s' % expected],
          'irc.ban(%r) sent %r, expected the mask %r'
          % (given, conn.sent, expected))
    conn = Conn()
    irc.unban(conn, '#ops', given)
    check(conn.sent == ['MODE #ops -b %s' % expected],
          'irc.unban(%r) sent %r -- it must expand exactly as ban() does or it '
          'cannot remove what ban() set' % (given, conn.sent))

  # --- 4. the optional channel argument -------------------------------------
  for cmd, _meth, modes, target in SHORTCUTS:
    sent, errors = run_command(cmd, '#other %s' % target)
    check(sent == ['MODE #other %s %s' % (modes, _expand(cmd, target))],
          '/%s did not accept an explicit channel: sent %r' % (cmd, sent))
    sent, errors = run_command(cmd, '# %s' % target)
    check(sent == ['MODE #ops %s %s' % (modes, _expand(cmd, target))],
          "/%s did not resolve a bare '#' to the current channel: sent %r"
          % (cmd, sent))
    # ...and from a window that is not a channel, an explicit one still works
    # while an implicit one is refused rather than guessed.
    sent, errors = run_command(cmd, '#other %s' % target, channel=None)
    check(sent == ['MODE #other %s %s' % (modes, _expand(cmd, target))],
          '/%s from a server window with an explicit channel sent %r'
          % (cmd, sent))
    sent, errors = run_command(cmd, target, channel=None)
    check(not sent and errors,
          '/%s outside a channel window with no channel named sent %r and '
          'reported %r -- it must refuse and say so' % (cmd, sent, errors))

  # --- 5. no connection is an error, not a traceback ------------------------
  # Every one of these called window.client.conn.sendLine() with no check, so
  # running one while disconnected raised AttributeError on None.
  from commands import Commands as C
  for cmd, _meth, _m, target in SHORTCUTS + [('kick', None, None, 'alice'),
                                             ('kban', None, None, 'alice'),
                                             ('mode', None, None, '+o alice')]:
    win = Win(None, '#ops')
    win.client.conn = None
    try:
      getattr(C, cmd)(win, target)
    except Exception as e:
      check(False, '/%s raised %s when disconnected instead of reporting it'
                   % (cmd, type(e).__name__))
      continue
    check(any('not connected' in e.lower() for e in win.errors),
          '/%s said %r when disconnected; expected "[Not connected]"'
          % (cmd, win.errors))

  # --- 6. kban bans before it kicks, both sides -----------------------------
  sent, _e = run_command('kban', 'alice spam')
  check(sent == ['MODE #ops +b alice!*@*', 'KICK #ops alice :spam'],
        '/kban sent %r' % (sent,))
  if not (hasattr(irc, 'kban') and has_ban):
    return _report()      # already reported; kban is built on ban
  conn = Conn()
  irc.kban(conn, '#ops', 'alice', 'spam')
  check(conn.sent == sent,
        'irc.kban sent %r, /kban sent %r' % (conn.sent, sent))

  # --- 7. the /exec script context offers the same set, to the same rule ----
  # Three spellings of one operation -- slash command, plugin.irc, /exec
  # script -- is exactly how three different ban masks happen.
  import exec_system
  conn = Conn()
  win = Win(conn, '#ops')
  ctx = exec_system._build_exec_context(win)
  for cmd, meth, modes, target in SHORTCUTS:
    check(meth in ctx, 'the /exec context has no %s()' % meth)
    if meth not in ctx:
      continue
    conn.sent[:] = []
    ctx[meth](target)
    expected, _e = run_command(cmd, target)
    check(conn.sent == expected,
          '/exec %s() sent %r but /%s sent %r' % (meth, conn.sent, cmd, expected))
  check('kban' in ctx, 'the /exec context has no kban()')
  if 'kban' in ctx:
    conn.sent[:] = []
    ctx['kban']('alice', 'spam')
    check(conn.sent == ['MODE #ops +b alice!*@*', 'KICK #ops alice :spam'],
          '/exec kban() sent %r' % (conn.sent,))

  # --- 8. irc.mode covers what the /mode command does -----------------------
  for args, expected in (
      (('#ops',),                     'MODE #ops'),
      (('#ops', '+imnt'),             'MODE #ops +imnt'),
      (('#ops', '+o', 'alice'),       'MODE #ops +o alice'),
      (('#ops', '+o alice'),          'MODE #ops +o alice'),
      (('#ops', '+ovl', 'a', 'b', '50'), 'MODE #ops +ovl a b 50'),
      (('tester', '+x'),              'MODE tester +x')):
    conn = Conn()
    irc.mode(conn, *args)
    check(conn.sent == [expected],
          'irc.mode%r sent %r, expected %r' % (args, conn.sent, expected))

  return _report()


def _report():
  if failures:
    print('FAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('the mode shortcuts agree as commands and as plugin API, take an '
        'optional channel, and refuse cleanly when disconnected.')
  return 0


def _expand(cmd, target):
  """What the command will have made of *target* by the time it is sent."""
  if cmd in ('ban', 'unban'):
    from config import ban_mask
    return ban_mask(target)
  return target


if __name__ == '__main__':
  sys.exit(main())
