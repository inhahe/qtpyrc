"""A nick is case-insensitive, so every lookup of one must be.

The report: a channel showed `<rockwood>` for a while and then `<Rockwood>`,
"without ever showing a nick change". Both spellings are the same person --
IRC nick identity ignores case, and the spelling is only presentation -- and
the messages themselves were fine, because each one is drawn from the nick in
its own PRIVMSG prefix. What went missing was the NICK event in between.

`IRCClient.userRenamed` skipped any channel where `oldname not in chan.nicks`,
a case-*sensitive* test against a set holding whatever spelling the server had
last used. So a rename the set could not match by exact string was dropped
whole: no "is now known as" line, no nick-list update, no history row, and no
error. The next message from that person simply appeared under a different
name.

Two ways to reach that state, and the second is the reported one:

  * a rename that only changes case (`rockwood` -> `Rockwood`), where the old
    spelling in the set is the *other* case;
  * a bouncer replaying an old NICK *after* it has sent a current NAMES, so
    the list already holds the new spelling and the replayed event carries the
    old one. The reporter is on Wicket, and this conversation was in a
    playback batch -- which is why it is in neither the log nor the history
    database, both of which are deliberately skipped during playback.

`userQuit` had the identical test, so a quit whose prefix spelling differed
was swallowed the same way: no "has quit" line, and the row left in the nick
list for good. Four widget scans compared `item._nick == nick` for the same
reason. All of them now go through `Channel.has_nick`/`find_nick`/
`rename_nick` and `NicksList.find_row`, which key by `irclower`.

Usage:
  python tests/test_nick_case.py     # from the qtpyrc root directory
"""

import atexit
import os
import runpy
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG = """nick: tester
user: tester
realname: qtpyrc nick case test

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
    nick: tester
    auto_connect: false
    server:
      host: 127.0.0.1
      port: 6667
      tls: false
"""

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-nickcase-')
# Registered before qtpyrc runs so it runs after qtpyrc's own atexit handler
# has closed crash.log -- see the same note in test_autoscroll.py.
atexit.register(shutil.rmtree, tmpdir, True)
cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.argv = ['qtpyrc.py', '-c', cfg, '--no-startup']

import state
import window as window_mod
from PySide6.QtCore import QTimer, QCoreApplication, QMetaObject, Qt

EXIT = 1
failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


class Conn:
  """The parts of IRCClient these paths touch."""

  # Enough of an IRCClient for the window title expander and qtpyrc's own
  # shutdown, which both reach into conn for descriptive strings.
  nickname = 'tester'
  realname = 'qtpyrc nick case test'
  username = 'tester'
  hostname = '127.0.0.1'
  _log_network = 'TestNet'

  def disconnect(self, *a, **k):
    """qtpyrc's shutdown calls this on every client's conn."""

  def sendLine(self, line):
    pass

  @staticmethod
  def is_channel(name):
    return bool(name) and name[0] in '#&!+'

  @staticmethod
  def irclower(s):
    # RFC1459 casemapping, which is what most networks advertise: []\~ fold
    # onto {}|^ as well as A-Z onto a-z.
    return s.lower().translate(str.maketrans('[]\\~', '{}|^'))


def build():
  """A real Channel and NicksList on the live client, with a stub conn.

  The window has to be real -- the bug is partly in the list widget -- so this
  runs inside a booted qtpyrc rather than constructing Qt objects by hand.
  """
  import models
  client = next(iter(state.clients))
  # The channel is built first, with the client as qtpyrc left it: Channel's
  # constructor expands the window title, which reaches deep into conn. The
  # stub goes in afterwards, and Channel._low() reads conn on every call, so
  # the casemapping below is the stub's from here on.
  chan = models.Channel(client, '#css')
  client.channels['#css'] = chan
  client.conn = Conn()
  return client, chan


def main():
  import models
  client, chan = build()
  conn = client.conn

  # --- 1. the index is case-insensitive both ways ---------------------------
  # The channel holds "Rockwood" -- the spelling a *current* NAMES gives, which
  # is what a bouncer sends when a client attaches. The replayed NICK event
  # below then carries the older "rockwood", and that mismatch is the reported
  # bug. A rename where the set already holds the old spelling works either
  # way, so testing only that one passes against the broken code (the first
  # version of this file did exactly that).
  u = models.User('Rockwood', 'rw', 'host')
  chan.addnick('Rockwood', u)
  for spelling in ('rockwood', 'Rockwood', 'ROCKWOOD'):
    check(chan.has_nick(spelling),
          'has_nick(%r) said no; a nick is the same person however it is '
          'spelled' % spelling)
  check(chan.find_nick('ROCKWOOD') == 'Rockwood',
        'find_nick returned %r, expected the stored spelling %r'
        % (chan.find_nick('ROCKWOOD'), 'Rockwood'))
  check(chan.find_nick('someone_else') is None,
        'find_nick invented a member that is not here')

  # RFC1459 folding, not str.lower: [] \ ~ are ordinary nick characters and
  # the server folds them onto {} | ^.
  chan.addnick('bob[away]', models.User('bob[away]', 'b', 'h'))
  check(chan.has_nick('bob{away}'),
        'has_nick ignored the casemapping: on an rfc1459 server bob[away] and '
        'bob{away} are one nick, and irclower is what says so')

  # --- 2. the widget row is found however the nick is spelled ---------------
  nl = chan.window.nickslist
  idx, item = nl.find_row('ROCKWOOD', conn)
  check(item is not None and item._nick == 'Rockwood',
        'NicksList.find_row could not find the row for a differently-cased '
        'spelling (got %r)' % (item._nick if item else None))

  # --- 3. the reported bug: a case-only rename ------------------------------
  # This is what a bouncer replay looks like from the client's side: the list
  # holds one spelling and the NICK event carries the other.
  before = [nl.item(i).text() for i in range(nl.count())]
  conn_client = client
  from irc_client import IRCClient
  # Drive the real handler with our stub as `self`. Only the few attributes it
  # touches are needed, which is what keeps this test free of a live server.
  renamed = []

  class Stub(Conn):
    client = conn_client

    def _get_server_time(self):
      return None

    def _in_playback_batch(self):
      return False

    def _nick_prefix(self, nick, channel):
      return ''

    def _pnick(self, nick, channel):
      return nick

    def _get_user(self, nick, ident=None, host=None):
      low = self.irclower(nick)
      u = self.client.users.get(low)
      if u is None:
        u = models.User(nick, ident, host)
        self.client.users[low] = u
      else:
        u.nick = nick
      return u

    def _parse_user(self, hostmask):
      """Mirrors IRCClient._parse_user: (User, nick, ident, host)."""
      from asyncirc import usersplit
      m = usersplit(hostmask)
      nick, ident, host = m.groups() if m else (hostmask, None, None)
      return self._get_user(nick, ident, host), nick, ident, host

  stub = Stub()
  IRCClient.userRenamed(stub, 'rockwood', 'Rockwood')

  check(chan.has_nick('Rockwood'),
        'after the rename the channel does not have the new spelling')
  check(chan.find_nick('rockwood') == 'Rockwood',
        'the stored spelling was not updated to %r (it is %r)'
        % ('Rockwood', chan.find_nick('rockwood')))
  check('rockwood' not in chan.nicks,
        'the old spelling is still in the nick set, so the channel now has '
        'the same person twice: %r' % (sorted(chan.nicks),))
  check(chan.users.get(conn.irclower('Rockwood')) is u,
        'the rename lost the channel User entry, so the mode prefix and '
        'everything else keyed off it went with it')

  idx, item = nl.find_row('Rockwood', conn)
  check(item is not None and item._nick == 'Rockwood',
        'the nick list row was not renamed (rows now: %r)'
        % [nl.item(i).text() for i in range(nl.count())])
  check(nl.count() == 2,
        'the rename changed the number of rows from 2 to %d' % nl.count())

  # The visible half of the report: a line saying it happened.
  doc = chan.window.output.document().toPlainText()
  check('is now known as' in doc,
        'no "is now known as" line was drawn for the rename -- this is the '
        'report: the name changed with nothing to say why')
  check('Rockwood' in doc,
        'the rename line does not name the new spelling')

  # --- 4. the same blind spot in userQuit -----------------------------------
  # The quit arrives spelled differently from the list, as a replayed one can.
  chan.window.output.clear()
  IRCClient.userQuit(stub, 'ROCKWOOD!rw@host', 'Quit: Leaving')
  check(not chan.has_nick('rockwood'),
        'a quit spelled differently from the stored nick left the member in '
        'the channel: %r' % (sorted(chan.nicks),))
  idx, item = nl.find_row('rockwood', conn)
  check(item is None,
        'the nick list row survived the quit, so the list now shows somebody '
        'who has left')

  if failures:
    print('FAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return finish(1)
  print('nick lookups are case-insensitive: a case-only rename is shown, the '
        'list follows it, and a differently-spelled quit still removes.')
  return finish(0)


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def _run():
  try:
    return main()
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(300, _run)

try:
  runpy.run_path(os.path.join(ROOT, 'qtpyrc.py'), run_name='__main__')
except SystemExit:
  pass

sys.exit(EXIT)
