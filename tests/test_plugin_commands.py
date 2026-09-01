"""Plugin-registered slash commands and hotkeys, and the nowplaying plugin.

Three things are being asserted, and they are three different kinds of claim.

**1. A registration either fires or refuses -- it never silently does nothing.**
That is the rule the whole `add_command` / `bind_key` design is built on, and
this project has paid for breaking it repeatedly: an entry in a config the
lister would not show, a JOIN sent three times because the hook ran per-005, an
`/aop` mask that expanded to something other than what the user read. So a
plugin command that collides with a built-in raises rather than registering
behind it; a key sequence Qt cannot parse raises rather than yielding an empty
QKeySequence that never fires; and `/alias` warns when it shadows either.

**2. Unloading one plugin unloads *that* plugin.** `plugin.irc` is a
module-level singleton, so the list of "things I registered" used to be shared
by everything that touched it, and `remove_all()` tore down every plugin's
hooks. Reloading one plugin silently disarmed the others. `for_plugin()` gives
each one its own bound view; the test that matters is the negative one -- B's
registrations survive A's teardown.

**3. The nowplaying plugin, driven against a fake foobar2000.** foobar2000 is
not installed on the machine this was written on and the COM component cannot
be exercised here at all, which is exactly why the plugin puts every COM call
behind `_fetch`/`_probe` and everything else in front of them: the parts that
can be wrong in a way a user would notice -- what gets sent, where, whether a
paused or stopped player is announced as playing, whether a changed hotkey
takes effect -- are all reachable without a player. The one thing the fake
cannot check is the shape of the COM API itself, which is what `/np -probe`
exists to report from a machine that has it.

Runs headless (offscreen Qt platform), so it needs no display.

Usage:
  python tests/test_plugin_commands.py     # from the qtpyrc root directory
"""

import contextlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'plugins'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QKeySequence

import config as configmod
import state
import plugin as plugin_api
import plugins as plugins_mod
from commands import docommand, Commands

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class FakeWindow:
  """Just enough window to receive output and be asked if it is still alive."""

  def __init__(self, wtype='server', client=None):
    self.type = wtype
    self.client = client
    self.channel = None
    self.query = None
    self.lines = []
    self._alive = True

  def addline(self, text, fmt=None, timestamp_override=None):
    self.lines.append(str(text))

  def redmessage(self, text):
    self.lines.append(str(text))

  def addline_nick(self, parts, fmt=None, timestamp_override=None):
    self.lines.append(''.join(p[0] if isinstance(p, tuple) else str(p)
                              for p in parts))

  def _widget_alive(self):
    return self._alive

  def text(self):
    return '\n'.join(self.lines)


class FakeMainWindow(QWidget):
  """A real QWidget, because QShortcut needs a real parent widget."""


class FakeApp:
  def __init__(self, mainwin):
    self.mainwin = mainwin


def fresh_registries():
  state.plugin_commands.clear()
  for entry in list(state.plugin_keys.values()):
    sc = entry['shortcut']
    sc.setEnabled(False)
    sc.setParent(None)
  state.plugin_keys.clear()
  state._on_hooks.clear()
  state._aliases.clear()


# ---------------------------------------------------------------------------
# 1. add_command: dispatch, and refusal rather than silent shadowing.
# ---------------------------------------------------------------------------

def test_add_command():
  fresh_registries()
  view = plugin_api.irc.for_plugin('tester')
  seen = []

  name = view.add_command('np', lambda w, t: seen.append(str(t)),
                          'announce something')
  check(name == 'np', 'add_command did not return the normalised name')

  win = FakeWindow()
  docommand(win, 'np', 'hello world')
  check(seen == ['hello world'],
        'a plugin command did not receive the text it was invoked with: %r'
        % (seen,))

  # Case and a leading slash are the user's spelling, not two commands.
  seen.clear()
  view.add_command('/NP2', lambda w, t: seen.append('np2'))
  docommand(win, 'NP2', '')
  check(seen == ['np2'],
        'a plugin command registered as "/NP2" did not answer to "NP2"')

  # A built-in wins the lookup, so registering one is a registration that could
  # never fire.  It must raise, not register.
  try:
    view.add_command('msg', lambda w, t: None)
    failures.append('add_command accepted "msg", which is a built-in -- the '
                    'registration would have been shadowed by Commands.msg and '
                    'silently never fired')
  except ValueError:
    pass
  check('msg' not in state.plugin_commands,
        'a refused registration was left in the registry anyway')

  # /exec is reached as Commands.exec_, so hasattr(Commands, 'exec') is False;
  # it still must not be claimable.
  try:
    view.add_command('exec', lambda w, t: None)
    failures.append('add_command accepted "exec" -- docommand maps it to '
                    'Commands.exec_ before the plugin tier is reached, so the '
                    'registration would never fire')
  except ValueError:
    pass

  for bad in ('', '   ', '_hidden', 'two words'):
    try:
      view.add_command(bad, lambda w, t: None)
      failures.append('add_command accepted the unusable name %r' % bad)
    except ValueError:
      pass

  try:
    view.add_command('notcallable', 'this is a string')
    failures.append('add_command accepted a non-callable handler, which would '
                    'raise only when the user first typed the command')
  except ValueError:
    pass

  # Re-registering replaces, so reloading a plugin needs no special case.
  seen.clear()
  view.add_command('np', lambda w, t: seen.append('second'))
  docommand(win, 'np', '')
  check(seen == ['second'],
        're-registering a command name did not replace the old handler')

  # An exception out of a plugin command is reported, not propagated: the
  # alternative is a traceback out of the input box with the window left in an
  # unknown state.
  def _boom(w, t):
    raise RuntimeError('deliberate')
  view.add_command('boom', _boom)
  win2 = FakeWindow()
  try:
    # docommand prints the traceback, which is the point; swallow it here so a
    # deliberate one is not mistaken for a failure in this test's own output.
    import contextlib, io
    with contextlib.redirect_stderr(io.StringIO()):
      docommand(win2, 'boom', '')
  except RuntimeError:
    failures.append('an exception from a plugin command escaped docommand')
  check('boom failed' in win2.text() and 'deliberate' in win2.text(),
        'a failing plugin command did not report itself: %r' % win2.text())

  view.remove_all()
  check(not state.plugin_commands,
        'remove_all left plugin commands registered')
  win3 = FakeWindow()
  docommand(win3, 'np', '')
  check('Unknown command' in win3.text(),
        'a command survived the unload of the plugin that registered it')


# ---------------------------------------------------------------------------
# 2. /alias warns when it shadows something that outranks it.
# ---------------------------------------------------------------------------

def test_alias_shadow_warning():
  fresh_registries()
  view = plugin_api.irc.for_plugin('tester')
  view.add_command('np', lambda w, t: None)

  win = FakeWindow()
  Commands.alias(win, 'np /say nothing')
  check('never run' in win.text(),
        'aliasing a name a plugin had registered did not warn that the alias '
        'can never run: %r' % win.text())

  win2 = FakeWindow()
  Commands.alias(win2, 'msg /say nothing')
  check('never run' in win2.text(),
        'aliasing a built-in did not warn: %r' % win2.text())

  # An alias that shadows nothing must not cry wolf; a warning printed for
  # every alias is a warning nobody reads by the time it matters.
  win3 = FakeWindow()
  Commands.alias(win3, 'nothingshadowed /say hi')
  check('never run' not in win3.text(),
        'an alias that shadows nothing warned anyway: %r' % win3.text())

  view.remove_all()


# ---------------------------------------------------------------------------
# 3. bind_key: canonical form, replacement, refusal.
# ---------------------------------------------------------------------------

def test_bind_key():
  fresh_registries()
  view = plugin_api.irc.for_plugin('tester')
  fired = []

  canonical = view.bind_key('  f12  ', lambda: fired.append(1), 'test key')
  check(canonical == QKeySequence('F12').toString(),
        'bind_key did not return the canonical sequence: %r' % canonical)
  check(list(state.plugin_keys) == [canonical],
        'bind_key stored something other than the canonical sequence: %r'
        % (list(state.plugin_keys),))

  # "f12" and "F12" are one binding, not two.  Keyed on what the user typed,
  # a plugin that spelled it differently on reload would accumulate shortcuts,
  # each still live.
  view.bind_key('F12', lambda: fired.append(2))
  check(len(state.plugin_keys) == 1,
        'two spellings of one sequence produced two bindings: %r'
        % (list(state.plugin_keys),))

  for bad in ('', '   ', 'not a key at all'):
    try:
      view.bind_key(bad, lambda: None)
      failures.append('bind_key accepted %r -- QKeySequence yields an empty '
                      'sequence for it, which is a shortcut that silently '
                      'never fires' % bad)
    except ValueError:
      pass

  try:
    view.bind_key('F11', 'not callable')
    failures.append('bind_key accepted a non-callable handler')
  except ValueError:
    pass

  check(view.unbind_key('f12') is True,
        'unbind_key did not report removing an existing binding')
  check(not state.plugin_keys, 'unbind_key left the binding registered')
  check(view.unbind_key('f12') is False,
        'unbind_key claimed to remove a binding that was not there')

  view.remove_all()


# ---------------------------------------------------------------------------
# 4. Ownership: unloading one plugin must not disarm another.
# ---------------------------------------------------------------------------

def test_ownership_isolation():
  fresh_registries()
  a = plugin_api.irc.for_plugin('plugin_a')
  b = plugin_api.irc.for_plugin('plugin_b')

  a.add_command('acmd', lambda w, t: None)
  a.bind_key('F9', lambda: None)
  a.on('chanmsg', 'ahook', '*', '/say a')

  b.add_command('bcmd', lambda w, t: None)
  b.bind_key('F10', lambda: None)
  b.on('chanmsg', 'bhook', '*', '/say b')

  a.remove_all()

  check('acmd' not in state.plugin_commands,
        "plugin A's command survived plugin A's teardown")
  check('bcmd' in state.plugin_commands,
        "plugin B's command was torn down by plugin A -- the registration "
        "lists are shared again, which is the bug for_plugin() exists to fix")
  check(QKeySequence('F9').toString() not in state.plugin_keys,
        "plugin A's hotkey survived plugin A's teardown")
  check(QKeySequence('F10').toString() in state.plugin_keys,
        "plugin B's hotkey was unbound by plugin A's teardown")
  check('ahook' not in state._on_hooks.get('chanmsg', {}),
        "plugin A's /on hook survived plugin A's teardown")
  check('bhook' in state._on_hooks.get('chanmsg', {}),
        "plugin B's /on hook was removed by plugin A's teardown -- this is the "
        "original shared-singleton bug")

  b.remove_all()
  check(not state.plugin_commands and not state.plugin_keys,
        'teardown of the second plugin left registrations behind')


# ---------------------------------------------------------------------------
# 5. Live references: `irc.config` must not go stale across a config reload.
# ---------------------------------------------------------------------------

def test_live_config_reference():
  before = state.config
  view = plugin_api.irc.for_plugin('tester')
  check(view.config is before, 'irc.config did not point at the live config')

  yaml = YAML()
  path = os.path.join(ROOT, 'defaults', 'config.defaults.yaml')
  replacement = configmod.AppConfig(path, CommentedMap(), yaml)
  state.config = replacement
  try:
    check(view.config is replacement,
          'irc.config still points at the AppConfig from startup -- '
          'qtpyrc._reload_config replaces state.config, so a plugin would be '
          'reading the settings the client launched with')
    check(plugin_api.irc.config is replacement,
          'the plugin.irc singleton kept a stale config reference')
  finally:
    state.config = before


# ---------------------------------------------------------------------------
# 6. The nowplaying plugin, against a fake foobar2000.
# ---------------------------------------------------------------------------

def load_nowplaying(cfg=None):
  """Instantiate the plugin with *cfg* as its saved settings.

  Registered in `state.activescripts` the way the loader would, so that
  `plugins.dispatch_config_changed()` reaches it -- the settings-dialog path is
  half of what is being tested and a hand-rolled call would not exercise it.
  """
  import nowplaying
  state.config._data['plugins'] = CommentedMap(
    {'nowplaying': CommentedMap(cfg or {})})
  view = plugin_api.irc.for_plugin('nowplaying')
  inst = nowplaying.NowPlaying(view)
  state.activescripts['nowplaying'] = plugins_mod.LoadedPlugin(
    'nowplaying', nowplaying, inst, irc=view)
  return nowplaying, inst, view


def unload_nowplaying():
  plugins_mod.unload_plugin('nowplaying')


def pump(inst, timeout=5.0):
  """Spin the event loop until the worker thread's result is delivered.

  The delivery is a real cross-thread signal emission, not a direct call, so
  this exercises the actual path: if `_Result` were ever created off the GUI
  thread, or the emit replaced with a direct call, this is what would notice.
  """
  app = QApplication.instance()
  deadline = time.time() + timeout
  while inst._busy and time.time() < deadline:
    app.processEvents()
    time.sleep(0.005)
  app.processEvents()
  return not inst._busy


@contextlib.contextmanager
def stub_source():
  """Install a fake foobar2000 as a nowplaying *source*, and remove it after.

  These tests used to replace the module-level `_fetch` instead.  That patched
  a global and never put it back, so every later test in this file silently ran
  against whichever stub had been installed last -- which is exactly how the
  beefweb tests below first came to "pass" without making a single HTTP
  request.  Registering a source is undoable, and it is also the more faithful
  double: it leaves `_fetch`'s real source selection, fallback and error
  handling in the path being tested.
  """
  import nowplaying as mod

  class StubSource(mod._Source):
    name, label = 'stub', 'test stub'

    def __init__(self):
      self.asked = []
      self.result = {'title': 'Bad Brains - Sailin On.flac', 'playing': True,
                     'paused': False, 'position': 61, 'length': 121}
      self.error = None

    def fetch(self, settings, spec):
      self.asked.append(spec)
      if self.error is not None:
        raise self.error
      return dict(self.result)

    def probe(self, settings):
      return ['stub source']

  stub = StubSource()
  mod.SOURCES[stub.name] = stub
  try:
    yield stub
  finally:
    mod.SOURCES.pop(stub.name, None)


def test_nowplaying_announce():
  fresh_registries()
  with stub_source() as stub:
    mod, inst, view = load_nowplaying({'source': 'stub'})

    check('np' in state.plugin_commands,
          'the plugin did not register its default command')
    check(QKeySequence('F12').toString() in state.plugin_keys,
          'the plugin did not bind its default hotkey')

    # A server window has no conversation to send to, so the answer is shown
    # rather than discarded.
    win = FakeWindow('server')
    docommand(win, 'np', '')
    check(pump(inst), 'the worker thread never delivered a result')
    check(stub.asked and stub.asked[0] == mod.DEFAULT_FORMAT,
          'the configured title-format spec was not the one asked for: %r'
          % (stub.asked,))
    check('np: Bad Brains - Sailin On.flac' in win.text(),
          'the announcement was not shown in a window with nowhere to send '
          'it: %r' % win.text())

    # A one-off spec on the command line overrides the configured one.
    stub.asked.clear()
    win2 = FakeWindow('server')
    docommand(win2, 'np', '%artist% - %title%')
    check(pump(inst), 'the worker thread never delivered a result')
    check(stub.asked and stub.asked[0] == '%artist% - %title%',
          'a spec given on the command line was ignored: %r' % (stub.asked,))

    # Stopped is not "playing something": announcing the loaded-but-stopped
    # track would put a claim in the channel that is not true.
    stub.result = {'title': '', 'playing': False, 'paused': False,
                   'position': None, 'length': None}
    win3 = FakeWindow('server')
    docommand(win3, 'np', '')
    check(pump(inst), 'the worker thread never delivered a result')
    check('not playing' in win3.text(),
          'a stopped player did not produce a "not playing" message: %r'
          % win3.text())

    # Every failure is reported locally.  None of them may reach the channel:
    # a player error is a fact about this machine and nobody else's business.
    stub.error = mod.NotRunning('foobar2000 is not running')
    win4 = FakeWindow('server')
    docommand(win4, 'np', '')
    check(pump(inst), 'the worker thread never delivered a result')
    check('not running' in win4.text(),
          'a source failure was not reported: %r' % win4.text())

    # An unexpected exception must be reported too, not swallowed into
    # silence.  `_fetch` only catches NotRunning, so this one travels all the
    # way out to the worker's catch-all -- which is the path being checked.
    stub.error = OSError('the RPC server is unavailable')
    win5 = FakeWindow('server')
    docommand(win5, 'np', '')
    check(pump(inst), 'the worker thread never delivered a result')
    check('OSError' in win5.text() and 'RPC server' in win5.text(),
          'an unexpected exception from a source was not reported: %r'
          % win5.text())
    check(inst._busy is False,
          'the in-flight guard was left set after a failure, so the next '
          'hotkey press would be refused for the rest of the session')

    # A window closed while foobar2000 was thinking is not written to.
    stub.error = None
    stub.result = {'title': 'a.flac', 'playing': True, 'paused': False,
                   'position': None, 'length': None}
    win6 = FakeWindow('server')
    win6._alive = False
    docommand(win6, 'np', '')
    check(pump(inst), 'the worker thread never delivered a result')
    check(win6.lines == [],
          'the result was written to a window that had been closed: %r'
          % win6.lines)

    unload_nowplaying()
    check(not state.plugin_commands and not state.plugin_keys,
          'unloading the plugin left its command or hotkey registered')


def test_nowplaying_sends_to_channel():
  """The announcement goes through commands.send_message, to the focused window."""
  fresh_registries()
  import commands
  stub_cm = stub_source()
  stub = stub_cm.__enter__()
  stub.result = {'title': 'track.flac', 'playing': True, 'paused': False,
                 'position': None, 'length': None}
  mod, inst, view = load_nowplaying({'source': 'stub'})

  sent = []
  real_send = commands.send_message
  real_action = commands.send_action
  commands.send_message = lambda w, c, t, x, display_window=None: sent.append(
    ('msg', t, x))
  commands.send_action = lambda w, c, x: sent.append(('action', None, x))
  try:
    # `docommand` expands {variables} before dispatching, which reads a fair
    # amount of the window/client/conn surface.  These doubles carry only what
    # that walk asks for.
    class FakeChannel:
      name = '#qtpyrc'
      topic = ''
      key = ''
      nicks = ()

    class FakeConn:
      nickname = 'inhahe'
      realname = 'inhahe'
      _network_name = 'testnet'

      @staticmethod
      def irclower(s):
        return s.lower()

    class FakeClient:
      conn = FakeConn()
      users = {}
      channels = {}
      network_key = 'testnet'
      network = 'testnet'
      hostname = 'irc.example.org'
      port = 6697
      connected = True
      tls = True

    win = FakeWindow('channel', client=FakeClient())
    win.channel = FakeChannel()

    docommand(win, 'np', '')
    check(pump(inst), 'the worker thread never delivered a result')
    check(sent == [('msg', '#qtpyrc', 'np: track.flac')],
          'the announcement did not go through send_message to the focused '
          "channel: %r" % (sent,))

    # -l shows it without sending it.  A user checking what would be announced
    # must not announce it in the act of checking.
    sent.clear()
    win2 = FakeWindow('channel', client=FakeClient())
    win2.channel = FakeChannel()
    docommand(win2, 'np', '-l')
    check(pump(inst), 'the worker thread never delivered a result')
    check(sent == [], '/np -l sent the announcement anyway: %r' % (sent,))
    check('np: track.flac' in win2.text(),
          '/np -l did not show the announcement locally: %r' % win2.text())

    # action: True sends a CTCP ACTION instead, through send_action -- which
    # knows about ACTION's 9 bytes of overhead when it chunks.
    sent.clear()
    state.config._data['plugins']['nowplaying']['action'] = True
    win3 = FakeWindow('channel', client=FakeClient())
    win3.channel = FakeChannel()
    docommand(win3, 'np', '')
    check(pump(inst), 'the worker thread never delivered a result')
    check(sent == [('action', None, 'np: track.flac')],
          'action: True did not send through send_action: %r' % (sent,))
  finally:
    commands.send_message = real_send
    commands.send_action = real_action
    unload_nowplaying()
    stub_cm.__exit__(None, None, None)


def test_nowplaying_config_changed():
  """A hotkey or command name changed in the settings dialog must take effect.

  Neither can be re-read at the point of use -- one is a live QShortcut, the
  other a key in a registry -- so without `config_changed` the user changes the
  setting, sees it saved, and finds the old key still working and the new one
  doing nothing, with no error anywhere to explain it.
  """
  fresh_registries()
  mod, inst, view = load_nowplaying()
  f12 = QKeySequence('F12').toString()
  check(f12 in state.plugin_keys and 'np' in state.plugin_commands,
        'the defaults were not registered')

  state.config._data['plugins']['nowplaying']['hotkey'] = 'Ctrl+Shift+N'
  state.config._data['plugins']['nowplaying']['command'] = 'playing'
  plugins_mod.dispatch_config_changed()

  check(f12 not in state.plugin_keys,
        'the old hotkey was still bound after the setting changed -- pressing '
        'it would still announce, with nothing on screen to say why')
  check(QKeySequence('Ctrl+Shift+N').toString() in state.plugin_keys,
        'the new hotkey was not bound')
  check('np' not in state.plugin_commands and 'playing' in state.plugin_commands,
        'the command name did not follow the setting: %r'
        % (sorted(state.plugin_commands),))

  # Blank means off, for both.  (A blank format or template is a mistake with
  # no useful meaning and falls back to the default instead.)
  state.config._data['plugins']['nowplaying']['hotkey'] = ''
  state.config._data['plugins']['nowplaying']['command'] = ''
  plugins_mod.dispatch_config_changed()
  check(not state.plugin_keys,
        'a blank hotkey setting did not disable the hotkey')
  check(not state.plugin_commands,
        'a blank command setting did not remove the command')
  check(inst._cfg('format', mod.DEFAULT_FORMAT) == mod.DEFAULT_FORMAT,
        'a setting that fell back did not fall back to its default')

  # Applying twice must not accumulate.
  state.config._data['plugins']['nowplaying']['hotkey'] = 'F12'
  state.config._data['plugins']['nowplaying']['command'] = 'np'
  plugins_mod.dispatch_config_changed()
  plugins_mod.dispatch_config_changed()
  check(len(state.plugin_keys) == 1 and len(state.plugin_commands) == 1,
        'applying the settings twice produced duplicate registrations: %r %r'
        % (list(state.plugin_keys), sorted(state.plugin_commands)))

  unload_nowplaying()


def test_nowplaying_message_building():
  """Template expansion, including the things a settings text box can contain."""
  import nowplaying as mod
  info = {'title': 'a.flac', 'playing': True, 'paused': True,
          'position': 61, 'length': 3725}
  check(mod._build_message('np: {title}', info) == 'np: a.flac',
        'the default template did not expand')
  check(mod._build_message('{title}{state}', info) == 'a.flac (paused)',
        '{state} did not report a paused player')
  check(mod._build_message('{time}', info) == '1:01/1:02:05',
        '{time} did not format as elapsed/length')

  # Unknown or malformed placeholders come from a text box in the settings
  # dialog.  A typo must produce a visibly odd line the user can correct, not
  # an exception raised from a hotkey press with nothing to connect it to.
  check(mod._build_message('{nosuchthing}', info) == '{nosuchthing}',
        'an unknown placeholder raised instead of surviving as written')
  check(mod._build_message('{title', info) == '{title',
        'an unbalanced brace raised instead of surviving as written')

  # A player with no position/length reported must not print "/".
  bare = {'title': 'b.flac', 'playing': True, 'paused': False,
          'position': None, 'length': None}
  check(mod._build_message('{title} {time}', bare).strip() == 'b.flac',
        'an unknown position produced a stray separator: %r'
        % mod._build_message('{title} {time}', bare))


def test_probe_reports_the_reason():
  """`/np -probe` must distinguish the several reasons for "nothing happened"."""
  import nowplaying as mod
  settings = {'source': 'auto', 'progid': 'Nonexistent.ProgID.0',
              'beefweb_url': 'http://127.0.0.1:1', 'format': '%filename_ext%',
              'timeout': 1.0}
  lines = '\n'.join(mod._probe(settings))

  # Both sources report, under their own headings.  Probing only the one that
  # happens to be configured is how a user ends up installing pywin32 to fix a
  # missing foo_beefweb.
  check('foo_beefweb' in lines and 'foo_comserver2' in lines,
        'the probe did not report on both sources: %r' % lines)
  check('reachable: NO' in lines,
        'the probe did not say beefweb was unreachable: %r' % lines)
  check('win32com.client' in lines and 'pythoncom' in lines,
        'the probe did not report whether a COM library is installed: %r'
        % lines)
  check('registered:' in lines,
        'the probe did not report whether the ProgID is registered: %r' % lines)

  # An unrecognised source is named as such rather than silently probed as
  # `auto`, which would report on things the user did not ask about and stay
  # silent about the typo that is the actual problem.
  bad = '\n'.join(mod._probe(dict(settings, source='beefwebb')))
  check('unknown source' in bad and 'beefwebb' in bad,
        'a mistyped source was not reported by the probe: %r' % bad)

  # A probe exists to explain a failure and must never become one.
  class _Exploding(mod._Source):
    name, label = 'boom', 'exploding source'

    def probe(self, settings):
      raise RuntimeError('probe blew up')

  mod.SOURCES['boom'] = _Exploding()
  try:
    out = '\n'.join(mod._probe(dict(settings, source='boom')))
    check('probe failed' in out and 'probe blew up' in out,
          'a source whose probe raised took the whole probe down: %r' % out)
  finally:
    del mod.SOURCES['boom']


# ---------------------------------------------------------------------------
# 7. The beefweb source, against a real HTTP server.
# ---------------------------------------------------------------------------
#
# This is the half of nowplaying that *can* be tested end to end.  The COM path
# needs a 32-bit foobar2000 with an unmaintained component installed, so it is
# exercised only through a fake; beefweb speaks HTTP and JSON, so a stdlib
# server reproduces it exactly -- including the query-string escaping, which is
# the one place this plugin can silently ask the wrong question.

class FakeBeefweb(object):
  """A stand-in for foo_beefweb's /api/player endpoint."""

  def __init__(self, playback_state='playing'):
    self.state = playback_state
    self.requests = []          # raw query strings, as the server received them
    self.status = 200
    self.body = None            # override the reply to test malformed input

    outer = self

    class Handler(BaseHTTPRequestHandler):
      def log_message(self, *a):
        pass

      def do_GET(self):
        parsed = urlparse(self.path)
        outer.requests.append(parsed.query)
        if outer.status != 200:
          self.send_response(outer.status)
          self.send_header('Content-Length', '0')
          self.end_headers()
          return
        if outer.body is not None:
          payload = outer.body.encode('utf-8')
        else:
          # beefweb evaluates each column separately and returns them in
          # order; the fake echoes each spec back so a test can see exactly
          # what arrived after the server un-escaped it.
          params = parse_qs(parsed.query, keep_blank_values=True)
          raw = params.get('columns', [''])[0]
          columns = ['<%s>' % c for c in _unescape_list(raw)]
          payload = json.dumps({'player': {
            'info': {'name': 'foobar2000', 'version': '2.26',
                     'pluginVersion': '0.10'},
            'playbackState': outer.state,
            'activeItem': {'playlistIndex': 0, 'index': 3,
                           'position': 61.0, 'duration': 121.0,
                           'columns': columns},
          }}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    self.server = HTTPServer(('127.0.0.1', 0), Handler)
    self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
    self.thread = threading.Thread(target=self.server.serve_forever,
                                   daemon=True)
    self.thread.start()

  def stop(self):
    self.server.shutdown()
    self.server.server_close()
    self.thread.join(timeout=5)


def _unescape_list(raw):
  """Split a beefweb `columns` value the way boost's escaped_list_separator does.

  Comma separates, backslash escapes.  Written out rather than reusing the
  plugin's escaper in reverse, because a test that undoes the escaping with the
  same code that applied it cannot detect a wrong escaping convention -- it
  would agree with itself no matter what the real server expects.
  """
  out, cur, esc = [], '', False
  for ch in raw:
    if esc:
      cur += ch
      esc = False
    elif ch == '\\':
      esc = True
    elif ch == ',':
      out.append(cur)
      cur = ''
    else:
      cur += ch
  out.append(cur)
  return out


def test_beefweb_source():
  """The beefweb source, driven against a real HTTP server."""
  import nowplaying as mod
  fake = FakeBeefweb()
  try:
    settings = {'source': 'beefweb', 'beefweb_url': fake.url,
                'format': '%filename_ext%', 'timeout': 5.0}

    info = mod._fetch(settings, '%filename_ext%')
    check(info['playing'] is True and info['paused'] is False,
          'a playing player was not reported as playing: %r' % (info,))
    check(info['title'] == '<%filename_ext%>',
          'the title did not come from the requested column: %r' % (info,))
    check(info['position'] == 61.0 and info['length'] == 121.0,
          'position/duration were not carried through: %r' % (info,))

    # A spec containing commas is the case that matters.  `$if(%x%,a,b)` is
    # ordinary title formatting, and beefweb splits `columns` on unescaped
    # commas -- so an unescaped spec does not arrive wrong, it arrives as
    # three separate columns and the announcement becomes a fragment.
    spec = '$if(%artist%,%artist%,unknown) - %title%'
    fake.requests.clear()
    info = mod._fetch(settings, spec)
    check(len(fake.requests) == 1, 'expected exactly one request')
    got = _unescape_list(parse_qs(fake.requests[0])['columns'][0])
    check(got == [spec],
          'a spec containing commas was split into separate columns: %r' % (got,))
    check(info['title'] == '<%s>' % spec,
          'the comma-bearing spec did not round-trip: %r' % (info,))

    # A backslash must survive too: boost expands \n and *throws* on an escape
    # it does not recognise, so an unescaped one is an HTTP 400, not a typo.
    fake.requests.clear()
    mod._fetch(settings, 'a\\nb')
    got = _unescape_list(parse_qs(fake.requests[0])['columns'][0])
    check(got == ['a\\nb'],
          'a backslash in the spec was not escaped: %r' % (got,))

    # Stopped is not a failure -- it is an answer, and it must not fall
    # through to another source.
    fake.state = 'stopped'
    info = mod._fetch(settings, '%filename_ext%')
    check(info['playing'] is False,
          'a stopped player was not reported as stopped: %r' % (info,))

    fake.state = 'paused'
    info = mod._fetch(settings, '%filename_ext%')
    check(info['playing'] is True and info['paused'] is True,
          'a paused player was misreported: %r' % (info,))

    # An unknown position/duration comes back negative rather than absent.
    # Letting -1 through prints "0:00" for a live stream, which reads as a
    # fact rather than as a missing value.
    fake.state = 'playing'
    fake.body = json.dumps({'player': {
      'playbackState': 'playing',
      'activeItem': {'position': -1, 'duration': -1, 'columns': ['stream']},
    }})
    info = mod._fetch(settings, '%filename_ext%')
    check(info['position'] is None and info['length'] is None,
          'a negative position/duration was reported as a real time: %r'
          % (info,))
    check(mod._build_message('{title} {time}', info).strip() == 'stream',
          'a stream printed a bogus elapsed time')

    # A malformed reply is reported, not raised through as a bare ValueError.
    fake.body = 'this is not json'
    try:
      mod._fetch(settings, '%filename_ext%')
      check(False, 'a malformed reply did not raise NotRunning')
    except mod.NotRunning as e:
      check('malformed' in str(e), 'unexpected error text: %s' % e)

    # So is an HTTP error status.
    fake.body = None
    fake.status = 500
    try:
      mod._fetch(settings, '%filename_ext%')
      check(False, 'an HTTP 500 did not raise NotRunning')
    except mod.NotRunning as e:
      check('500' in str(e), 'the HTTP status was not reported: %s' % e)
  finally:
    fake.stop()


def test_source_selection():
  """`auto` falls through, an explicit source does not, a typo is refused."""
  import nowplaying as mod
  fake = FakeBeefweb()
  try:
    tried = []

    class _Recording(mod._Source):
      name, label = 'com', 'recording stand-in'

      def fetch(self, settings, spec):
        tried.append('com')
        raise mod.NotRunning('no COM here')

      def probe(self, settings):
        return ['stand-in']

    real_com = mod.SOURCES['com']
    mod.SOURCES['com'] = _Recording()
    try:
      # auto: beefweb answers, so COM is never consulted.
      settings = {'source': 'auto', 'beefweb_url': fake.url,
                  'format': '%filename_ext%', 'timeout': 5.0}
      info = mod._fetch(settings, '%filename_ext%')
      check(info['playing'] is True, 'auto did not get an answer from beefweb')
      check(tried == [],
            'auto consulted COM even though beefweb answered: %r' % (tried,))

      # A *stopped* player is an answer too.  Falling through to COM here
      # would let a second player's state be announced as foobar2000's.
      fake.state = 'stopped'
      tried.clear()
      info = mod._fetch(settings, '%filename_ext%')
      check(info['playing'] is False and tried == [],
            'auto fell through on a stopped player: %r %r' % (info, tried))
      fake.state = 'playing'

      # auto: beefweb unreachable, so it falls through to COM -- and when both
      # fail the error names both, so the user is not sent to fix whichever
      # one happened to be tried last.
      tried.clear()
      dead = dict(settings, beefweb_url='http://127.0.0.1:1', timeout=1.0)
      try:
        mod._fetch(dead, '%filename_ext%')
        check(False, 'auto succeeded with every source failing')
      except mod.NotRunning as e:
        check(tried == ['com'], 'auto did not fall through to COM: %r' % (tried,))
        check('beefweb:' in str(e) and 'com:' in str(e),
              'the combined error did not name both sources: %s' % e)

      # An explicit source is not a preference: naming beefweb and having it
      # fail must not quietly announce via COM instead.
      tried.clear()
      try:
        mod._fetch(dict(dead, source='beefweb'), '%filename_ext%')
        check(False, 'an unreachable explicit source succeeded')
      except mod.NotRunning as e:
        check(tried == [],
              'an explicit source fell through to another one: %r' % (tried,))
        check('com:' not in str(e),
              'the error blamed a source that was not selected: %s' % e)

      # A mistyped source is refused rather than silently treated as `auto`.
      try:
        mod._fetch(dict(settings, source='beefwebb'), '%filename_ext%')
        check(False, 'a mistyped source was accepted')
      except mod.NotRunning as e:
        check('unknown source' in str(e),
              'a mistyped source was not named as such: %s' % e)
    finally:
      mod.SOURCES['com'] = real_com
  finally:
    fake.stop()


def test_default_format_survives_escaping():
  """The shipped default `format` must reach beefweb as one intact column.

  This is a guard on a dependency that is easy to lose sight of: the default
  spec is built from `$if`/`$puts`/`$ifgreater`, whose arguments are separated
  by commas -- and a comma is also how beefweb separates one column from the
  next.  So the default only works at all because `_beefweb_escape` escapes
  them, and if that escaping ever regressed, every user running the default
  configuration would get the fragment `<$puts(i>` instead of a track name.
  The older default (`%filename_ext%`) contained no commas and was therefore
  immune, which is exactly why this needs testing now and did not before.
  """
  import nowplaying as mod

  # Assert the precondition rather than assume it.  Without this, simplifying
  # the default to something comma-free would leave the test passing while
  # testing nothing at all.
  check(',' in mod.DEFAULT_FORMAT,
        'the default format has no commas, so this test proves nothing')
  check("['" in mod.DEFAULT_FORMAT and "']'" in mod.DEFAULT_FORMAT,
        'the default format does not single-quote its brackets, so foobar2000 '
        'would read them as conditional syntax and swallow them')

  fake = FakeBeefweb()
  try:
    settings = {'source': 'beefweb', 'beefweb_url': fake.url,
                'format': mod.DEFAULT_FORMAT, 'timeout': 5.0}
    fake.requests.clear()
    info = mod._fetch(settings, mod.DEFAULT_FORMAT)

    got = _unescape_list(parse_qs(fake.requests[0])['columns'][0])
    check(got == [mod.DEFAULT_FORMAT],
          'the default format did not arrive as one intact column: %r' % (got,))
    check(info['title'] == '<%s>' % mod.DEFAULT_FORMAT,
          'the default format did not round-trip: %r' % (info['title'],))

    # `probe` asks for the configured format alongside a second spec, so it is
    # the one place two comma-bearing specs share a `columns` value -- the
    # case where a missing escape would merge them rather than merely split one.
    fake.requests.clear()
    lines = mod._probe(settings)
    got = _unescape_list(parse_qs(fake.requests[0])['columns'][0])
    check(len(got) == 2 and got[0] == mod.DEFAULT_FORMAT,
          'probe did not send the default format as its own column: %r' % (got,))
    check(any('reachable: yes' in l for l in lines),
          'probe did not report the fake server as reachable: %r' % (lines,))
  finally:
    fake.stop()


def main():
  QApplication.instance() or QApplication([])
  yaml = YAML()
  path = os.path.join(ROOT, 'defaults', 'config.defaults.yaml')
  state.config = configmod.AppConfig(path, CommentedMap(), yaml)
  configmod._update_text_formats(state.config)
  state.clients = set()
  mainwin = FakeMainWindow()
  state.app = FakeApp(mainwin)
  state.activescripts = {}
  plugins_mod.init_irc()

  test_add_command()
  test_alias_shadow_warning()
  test_bind_key()
  test_ownership_isolation()
  test_live_config_reference()
  test_nowplaying_announce()
  test_nowplaying_sends_to_channel()
  test_nowplaying_config_changed()
  test_nowplaying_message_building()
  test_probe_reports_the_reason()
  test_beefweb_source()
  test_source_selection()
  test_default_format_survives_escaping()

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('All plugin command/hotkey checks passed: registrations fire or refuse, '
        'one plugin\'s teardown leaves the others alone, and nowplaying '
        'announces what a fake foobar2000 says it is playing.')
  return 0


sys.exit(main())
