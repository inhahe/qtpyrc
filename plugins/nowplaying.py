# nowplaying.py -- announce the track foobar2000 is playing
#
#   F12          announce the current track in the focused channel or query
#   /np          the same thing as a command
#   /np <spec>   announce using a one-off foobar2000 title-format spec
#   /np -l       show it to yourself only, without sending it
#   /np -probe   diagnose the connection (what is installed, what answers)
#
# The hotkey and the command name are both configurable, in
# Settings > Plugins > nowplaying.
#
# There are two ways to reach the player, because the obvious one is dead on
# any current foobar2000:
#
#   beefweb  The foo_beefweb component's HTTP API.  Works with foobar2000 v1.6
#            and newer in *both* 32- and 64-bit builds, is actively maintained,
#            needs no Python dependency whatsoever (stdlib HTTP and JSON), and
#            keeps working when foobar2000 runs on another machine.
#
#   com      The foo_comserver2 component's COM automation object.  This is the
#            component people mean when they say "foobar2000 COM", and it is a
#            32-bit-only build of a component last released for foobar2000 0.9
#            -- foobar2000's own troubleshooter lists it for repeated crash
#            reports.  A 32-bit DLL cannot be loaded into a 64-bit process at
#            all, so on a modern x64 foobar2000 this path cannot be made to
#            work by any amount of configuration.  It is kept for the genuinely
#            old 32-bit v1.x installs where it is the only option, and it needs
#            pywin32 or comtypes.
#
# `source: auto` (the default) tries beefweb and falls back to COM, so a user
# who has either one installed gets a working plugin without configuring
# anything.

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import plugin

from PySide6.QtCore import QObject, Signal


#: Title-format spec asked of foobar2000: `artist - title [320kbps mp3]`.
#:
#: Every part of it is conditional, because a plain `%artist% - %title%
#: [%bitrate%kbps %codec%]` is wrong for most of a real library.  Measured
#: against one of 32,483 tracks (a 9,000-track sample covering 17 distinct
#: container/lossless combinations), the four things it has to survive:
#:
#: * **No artist tag.**  39% of that library had none, and `%artist%` renders
#:   as a literal `?` when absent -- so the naive spec announces `? - Title`
#:   for two tracks in five.  `$if(%artist%,%artist% - ,)` drops the artist and
#:   its separator together.  `%title%` needs no such guard: foobar2000 falls
#:   back to the filename for it, and it was present for all 9,000.
#:
#: * **Lossless files, where a bitrate is meaningless.**  The test is
#:   `%__bitspersample%` (present only for PCM-backed formats), not the file
#:   extension: `.m4a` is a container holding *either* lossy AAC or lossless
#:   ALAC, and that library has both (267 lossy, 180 lossless) -- as it does
#:   for `.wma`.  Keying on the extension would print a bitrate for every one
#:   of the lossless ones.  This is also why the rule is "lossless", not "not
#:   FLAC": the same one sentence then covers wav, ALAC and WMA Lossless.
#:
#: * **Files whose bitrate foobar2000 does not know.**  Raw `.aac` and `.webm`
#:   report `%bitrate%` as `?` (raw `.aac` has no `%length_seconds%` either, so
#:   it cannot even be computed from the file size -- the arithmetic yields
#:   19018).  `$if(%bitrate%,...)` omits it rather than announcing `?kbps`.
#:
#: * **Internet radio**, which has no filename and so no extension.  The whole
#:   bracketed group is emitted only if something landed in it, so a stream
#:   announces `DI.FM - Progressive` rather than `DI.FM - Progressive []`.
#:
#: Two pieces of title-format syntax here are not obvious and were both found
#: by testing rather than by reading:
#:
#: * **`'['` and `']'`, not `[` and `]`.**  Square brackets are foobar2000's
#:   *conditional* syntax, so literal ones have to be single-quoted or they are
#:   silently swallowed -- the first version of this printed `Artist - Title
#:   320kbps mp3` with no brackets at all.
#: * **The commas are load-bearing** -- they separate the arguments of `$if`,
#:   `$puts` and `$ifgreater` -- which means this default only works because
#:   `_beefweb_escape` escapes them.  Unescaped, beefweb would read this as a
#:   dozen separate columns and the announcement would be the fragment
#:   `<$puts(i>`.  See `test_default_format_survives_escaping`.
DEFAULT_FORMAT = (
  # Build the bracket contents first: bitrate (lossy and known only), then the
  # lowercased file extension, with $trim closing up the gap when either is
  # missing.
  "$puts(i,$trim($if(%__bitspersample%,,$if(%bitrate%,%bitrate%kbps,))"
  " $lower($ext(%filename_ext%))))"
  # "artist - title", or just "title" when there is no artist tag.
  "$if(%artist%,%artist% - ,)%title%"
  # ...and the bracketed group, only when it would not be empty.
  "$ifgreater($len($get(i)),0,' ['$get(i)']',)"
)

#: How the answer is turned into a line of chat.  `{title}` is whatever
#: `format` produced; see `_build_message` for the rest.
DEFAULT_TEMPLATE = 'np: {title}'

DEFAULT_HOTKEY = 'F12'
DEFAULT_COMMAND = 'np'
DEFAULT_SOURCE = 'auto'

#: Where foo_beefweb listens by default.  A full base URL rather than a port,
#: so that pointing this at another machine needs no code change.
DEFAULT_BEEFWEB_URL = 'http://localhost:8880'

#: What foo_comserver2 registers itself as.  Overridable because the trailing
#: version number belongs to the component, not to us.
DEFAULT_PROGID = 'Foobar2000.Application.0.7'

#: Seconds to wait for the player.  Short on purpose: this is a hotkey, and a
#: player that has not answered in a couple of seconds is one the user would
#: rather be told about than keep waiting for.
DEFAULT_TIMEOUT = 4.0

#: Order `source: auto` tries the backends in.  beefweb first because it is the
#: one that can work on a current foobar2000, and because asking it costs one
#: refused TCP connection when it is absent, against COM's registry and
#: out-of-process object lookups.
AUTO_ORDER = ('beefweb', 'com')


class NotRunning(Exception):
  """This source cannot answer: not installed, not running, not reachable."""


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
#
# A source knows how to answer two questions -- "what is playing?" and "why did
# that not work?" -- and nothing else.  Everything below the plugin class is
# written against this pair rather than against COM or HTTP specifically, which
# is what lets `auto` try them in turn and lets `-probe` report on all of them.
#
# Both methods run on a worker thread and must, whichever source is in use: an
# out-of-process call blocks until the *other* program answers, and foobar2000
# is a GUI application that can be busy, minimised to the tray, rescanning its
# media library or showing a modal dialog.  On the GUI thread that is a freeze
# of the whole client for as long as it takes -- this project already tracks
# GUI-thread stalls as a bug class of their own (`hang_watchdog.py`), and one
# caused by another program entirely would be the hardest of them to attribute.

class _Source(object):
  """Base class: `fetch` returns the info dict, `probe` returns diagnostics."""

  #: Config value that selects this source.
  name = ''
  #: Human-readable name, used in probe headings and error messages.
  label = ''

  def fetch(self, settings, spec):
    """Return a dict with `title`, `playing`, `paused`, `position`, `length`.

    Raises NotRunning if this source cannot answer at all.  Note that "the
    player is stopped" is *not* a failure: it returns `playing: False`, which
    is what stops `auto` falling through to the next source and announcing a
    different player's idea of the truth.
    """
    raise NotImplementedError

  def probe(self, settings):
    """Return a list of diagnostic lines.  Never raises."""
    raise NotImplementedError


def _stopped():
  return {'title': '', 'playing': False, 'paused': False,
          'position': None, 'length': None}


# --- beefweb ---------------------------------------------------------------

def _beefweb_escape(spec):
  """Escape one title-format spec for beefweb's `columns` query parameter.

  beefweb parses `columns` with boost's `escaped_list_separator` -- comma the
  separator, backslash the escape (`tryParseValueListStrict` in
  cpp/server/parsing.hpp).  Both escapes here are load-bearing:

  * A comma would otherwise split one spec into two columns, and commas in a
    title-format spec are not exotic -- they are how every `$if(%x%,a,b)` is
    written.  Splitting one of those does not yield a wrong-but-harmless
    string, it yields three unparseable fragments.
  * A backslash has to be escaped for a subtler reason: boost expands `\\n` to
    a newline and *throws* on any escape it does not recognise, so a stray
    backslash in a spec would turn the whole request into an HTTP 400.
  """
  return spec.replace('\\', '\\\\').replace(',', '\\,')


def _beefweb_columns(specs):
  """Build the `columns` query-parameter value for *specs*, in order."""
  return ','.join(_beefweb_escape(s) for s in specs)


def _beefweb_get(settings, path):
  """GET *path* from the configured beefweb and return the decoded JSON."""
  base = str(settings.get('beefweb_url') or DEFAULT_BEEFWEB_URL).rstrip('/')
  url = base + path
  req = urllib.request.Request(url, headers={'Accept': 'application/json'})
  try:
    with urllib.request.urlopen(req, timeout=settings.get('timeout',
                                                          DEFAULT_TIMEOUT)) as r:
      raw = r.read()
  except urllib.error.HTTPError as e:
    # Worth distinguishing: a 4xx means beefweb is there and rejected what we
    # asked, which is a bug in the spec or in this plugin -- not something the
    # user fixes by installing anything.
    raise NotRunning('beefweb at %s returned HTTP %s' % (base, e.code))
  except urllib.error.URLError as e:
    raise NotRunning('cannot reach beefweb at %s (%s)' % (base, e.reason))
  except OSError as e:
    raise NotRunning('cannot reach beefweb at %s (%s)' % (base, e))
  try:
    return json.loads(raw.decode('utf-8', 'replace'))
  except ValueError as e:
    raise NotRunning('beefweb at %s sent a malformed reply (%s)' % (base, e))


def _beefweb_player(settings, specs):
  """Return the `player` object from /api/player, asking for *specs*."""
  query = urllib.parse.urlencode({'columns': _beefweb_columns(specs)})
  data = _beefweb_get(settings, '/api/player?' + query)
  if not isinstance(data, dict):
    raise NotRunning('beefweb sent %s where an object was expected'
                     % type(data).__name__)
  return data.get('player') or {}


def _positive(value):
  """Return *value* as a float, or None when it is absent or a placeholder.

  beefweb reports an unknown position or duration as a negative number rather
  than omitting it, and a stream has no duration at all.  Letting -1 through
  would print an elapsed time of `0:00` for a live stream, which reads as a
  fact rather than as a missing value.
  """
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if number >= 0 else None


class _BeefwebSource(_Source):

  name = 'beefweb'
  label = 'foo_beefweb (HTTP API)'

  def fetch(self, settings, spec):
    player = _beefweb_player(settings, [spec])
    state = str(player.get('playbackState') or 'stopped').lower()
    if state == 'stopped':
      return _stopped()

    item = player.get('activeItem') or {}
    columns = item.get('columns') or []
    return {'title': (columns[0] if columns else '').strip(),
            'playing': True,
            'paused': state == 'paused',
            'position': _positive(item.get('position')),
            'length': _positive(item.get('duration'))}

  def probe(self, settings):
    base = str(settings.get('beefweb_url') or DEFAULT_BEEFWEB_URL).rstrip('/')
    lines = ['url: %s' % base]
    specs = [settings.get('format') or DEFAULT_FORMAT, '%artist% - %title%']
    try:
      player = _beefweb_player(settings, specs)
    except NotRunning as e:
      lines.append('reachable: NO -- %s' % e)
      lines.append('install foo_beefweb from '
                   'https://www.foobar2000.org/components/view/foo_beefweb')
      return lines
    lines.append('reachable: yes')

    info = player.get('info') or {}
    lines.append('player: %s %s (foo_beefweb %s)'
                 % (info.get('name', '?'), info.get('version', '?'),
                    info.get('pluginVersion', '?')))
    lines.append('playbackState = %r' % player.get('playbackState'))
    item = player.get('activeItem') or {}
    for key in ('playlistIndex', 'index', 'position', 'duration'):
      lines.append('  %s = %r' % (key, item.get(key)))
    columns = item.get('columns') or []
    for i, spec in enumerate(specs):
      value = columns[i] if i < len(columns) else None
      lines.append('  %s = %r' % (spec, value))
    return lines


# --- foo_comserver2 (COM) --------------------------------------------------

def _com_module():
  """Return an object with `GetActiveObject(progid)`, or raise NotRunning.

  pywin32 is tried first because it is the more widely installed of the two;
  comtypes is accepted as an alternative so that a user who has only that one
  is not told to install a second COM library.  Neither is a dependency of
  qtpyrc: this is the only code in the client that wants COM, it is optional
  even within this plugin, and it is Windows-only.
  """
  try:
    import win32com.client as _w
    return _w
  except ImportError:
    pass
  try:
    import comtypes.client as _c
    return _c
  except ImportError:
    pass
  raise NotRunning(
    'no COM library available -- install pywin32 (pip install pywin32)')


def _co_initialize():
  """Initialise COM on the calling thread.  Returns a callable to undo it.

  A COM call from a thread that never called CoInitialize fails with
  CO_E_NOTINITIALIZED, and the worker is a fresh thread every time.
  """
  try:
    import pythoncom
  except ImportError:
    return lambda: None
  pythoncom.CoInitialize()
  return pythoncom.CoUninitialize


def _com_connect(progid):
  """Return the running foobar2000 automation object.

  Uses `GetActiveObject`, never `Dispatch`, and that is the whole point of this
  function: `Dispatch` **launches** the application if it is not running, so a
  user who pressed the hotkey by accident -- or who has the hotkey bound and no
  music playing -- would find foobar2000 starting up underneath them.  "Nothing
  is playing" must never become "something is now playing".
  """
  com = _com_module()
  try:
    return com.GetActiveObject(progid)
  except Exception as e:
    raise NotRunning(
      'foobar2000 is not running, or foo_comserver2 is not installed (%s)' % e)


class _ComSource(_Source):

  name = 'com'
  label = 'foo_comserver2 (COM, 32-bit only)'

  def fetch(self, settings, spec):
    progid = settings.get('progid') or DEFAULT_PROGID
    app = _com_connect(progid)
    try:
      playback = app.Playback
    except Exception as e:
      raise NotRunning('foo_comserver2 exposed no Playback object (%s)' % e)

    # Every field but `title` is read defensively: foo_comserver2 is an
    # unmaintained component whose exact member set differs between builds, and
    # a missing `Position` must cost the optional `{elapsed}` placeholder
    # rather than the whole announcement.
    def _get(name, default=None):
      try:
        return getattr(playback, name)
      except Exception:
        return default

    playing = _get('IsPlaying')
    paused = bool(_get('IsPaused'))
    # `IsPlaying` is the one field whose absence is fatal: without it there is
    # no way to tell "playing track X" from "stopped, with track X still
    # loaded", and announcing the latter is announcing something untrue.
    if playing is None:
      raise NotRunning('foo_comserver2 exposed no IsPlaying flag')
    if not playing:
      return _stopped()

    try:
      title = playback.FormatTitle(spec)
    except Exception as e:
      raise NotRunning('FormatTitle(%r) failed (%s)' % (spec, e))

    return {'title': (title or '').strip(),
            'playing': True,
            'paused': paused,
            'position': _get('Position'),
            'length': _get('Length')}

  def probe(self, settings):
    progid = settings.get('progid') or DEFAULT_PROGID
    lines = []
    for mod in ('win32com.client', 'comtypes.client', 'pythoncom'):
      try:
        __import__(mod)
        lines.append('%s: present' % mod)
      except ImportError as e:
        lines.append('%s: MISSING (%s)' % (mod, e))

    lines.append('progid: %s' % progid)
    try:
      import winreg
      with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid):
        lines.append('registered: yes (HKCR\\%s)' % progid)
    except ImportError:
      lines.append('registered: unknown (no winreg -- not Windows?)')
    except OSError:
      lines.append('registered: NO -- foo_comserver2 is not installed.  Note '
                   'it is 32-bit only and cannot load into a 64-bit '
                   'foobar2000; use the beefweb source instead.')

    try:
      app = _com_connect(progid)
    except NotRunning as e:
      lines.append('running instance: no (%s)' % e)
      return lines
    lines.append('running instance: yes')

    try:
      playback = app.Playback
    except Exception as e:
      lines.append('Playback: FAILED (%s)' % e)
      return lines
    lines.append('Playback: ok')

    for name in ('IsPlaying', 'IsPaused', 'Position', 'Length', 'Volume',
                 'IsMuted', 'CanSeek'):
      try:
        lines.append('  %s = %r' % (name, getattr(playback, name)))
      except Exception as e:
        lines.append('  %s: unavailable (%s)' % (name, e))
    for spec in (settings.get('format') or DEFAULT_FORMAT,
                 '%artist% - %title%'):
      try:
        lines.append('  FormatTitle(%s) = %r'
                     % (spec, playback.FormatTitle(spec)))
      except Exception as e:
        lines.append('  FormatTitle(%s): failed (%s)' % (spec, e))
    return lines


SOURCES = {s.name: s for s in (_BeefwebSource(), _ComSource())}


def _select(settings):
  """Return the sources to try, in order.

  An unrecognised `source` raises rather than quietly falling back to `auto`.
  Silently answering a different question from the one asked is this codebase's
  signature failure mode, and a user who typed `source: beefwebb` must be told
  so rather than left believing they are testing beefweb.
  """
  source = str(settings.get('source') or DEFAULT_SOURCE).strip().lower()
  if source in SOURCES:
    return [SOURCES[source]]
  if source in ('auto', ''):
    return [SOURCES[n] for n in AUTO_ORDER]
  raise NotRunning('unknown source %r -- expected auto, %s'
                   % (source, ' or '.join(sorted(SOURCES))))


def _fetch(settings, spec):
  """Ask each selected source in turn; the first to answer wins.

  When every source fails, the error names *all* of them.  Reporting only the
  last is how a user ends up installing pywin32 to fix a missing foo_beefweb.
  """
  reasons = []
  for source in _select(settings):
    try:
      return source.fetch(settings, spec)
    except NotRunning as e:
      reasons.append('%s: %s' % (source.name, e))
  raise NotRunning('; '.join(reasons))


def _probe(settings):
  """Collect diagnostics from every selected source.  Never raises.

  Exists because the failure this plugin is most likely to hand a user is
  "nothing happened", and there are many separate reasons for it -- no
  component installed, the wrong component for their foobar2000's
  architecture, the player not running, a wrong URL or ProgID, a missing COM
  library.  One line naming one of them sends people to fix the wrong thing,
  so every source reports its own findings under its own heading.
  """
  lines = ['source: %s' % (settings.get('source') or DEFAULT_SOURCE)]
  try:
    sources = _select(settings)
  except NotRunning as e:
    lines.append(str(e))
    return lines
  for source in sources:
    lines.append('--- %s ---' % source.label)
    try:
      lines.extend('  ' + line for line in source.probe(settings))
    except Exception as e:                             # noqa: BLE001
      # A probe exists to explain a failure, so it must not become one.
      lines.append('  probe failed: %s: %s' % (type(e).__name__, e))
  return lines


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _mmss(seconds):
  """Format a number of seconds as m:ss (or h:mm:ss), or '' if unknown."""
  try:
    total = int(float(seconds))
  except (TypeError, ValueError):
    return ''
  if total < 0:
    return ''
  h, rem = divmod(total, 3600)
  m, s = divmod(rem, 60)
  if h:
    return '%d:%02d:%02d' % (h, m, s)
  return '%d:%02d' % (m, s)


def _build_message(template, info):
  """Expand *template* against the dict `_fetch` returned.

  Placeholders: `{title}`, `{elapsed}`, `{length}`, `{time}` (``elapsed/length``,
  empty when either is unknown) and `{state}` (`` (paused)`` or empty).

  An unknown placeholder is left as written rather than raising, because the
  template comes from a text box in the settings dialog: a typo there must
  produce a visibly odd line the user can correct, not an exception from a
  hotkey press with nothing on screen to connect it to.
  """
  elapsed = _mmss(info.get('position'))
  length = _mmss(info.get('length'))
  values = {
    'title': info.get('title', ''),
    'elapsed': elapsed,
    'length': length,
    'time': ('%s/%s' % (elapsed, length)) if elapsed and length else '',
    'state': ' (paused)' if info.get('paused') else '',
  }

  class _Lenient(dict):
    def __missing__(self, key):
      return '{%s}' % key

  try:
    return template.format_map(_Lenient(values))
  except (ValueError, IndexError):
    # An unbalanced brace ("{title") is a ValueError from format_map itself,
    # which __missing__ never sees.
    return template


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------

class _Result(QObject):
  """Carries a worker-thread result back to the GUI thread.

  Created on the GUI thread, so the queued connection Qt picks for a
  cross-thread emit delivers `ready` there -- which is required, because the
  slot writes to a chat window.
  """
  ready = Signal(object)


class NowPlaying(plugin.Callbacks):

  config_fields = [
    ('hotkey', str, DEFAULT_HOTKEY,
     'Hotkey that announces the current track\n'
     '(e.g. F12, Ctrl+Shift+P; blank to disable)'),
    ('command', str, DEFAULT_COMMAND,
     'Slash command name, without the prefix\n(blank to disable)'),
    ('format', str, DEFAULT_FORMAT,
     'foobar2000 title-format spec\n(e.g. %artist% - %title%)'),
    ('template', str, DEFAULT_TEMPLATE,
     'Message sent to the channel.  Placeholders:\n'
     '{title} {elapsed} {length} {time} {state}'),
    ('action', bool, False,
     'Send as an action (/me) instead of a message'),
    ('source', str, DEFAULT_SOURCE,
     'How to reach foobar2000: auto, beefweb or com.\n'
     'com (foo_comserver2) is 32-bit only and cannot\n'
     'work with a 64-bit foobar2000.'),
    ('beefweb_url', str, DEFAULT_BEEFWEB_URL,
     'Base URL of the foo_beefweb component\n(source: beefweb)'),
    ('progid', str, DEFAULT_PROGID,
     'COM ProgID published by foo_comserver2\n(source: com)'),
  ]

  def __init__(self, irc):
    plugin.Callbacks.__init__(self, irc)
    self._result = _Result()
    self._result.ready.connect(self._deliver)
    # One query at a time.  A held-down hotkey would otherwise start a thread
    # per repeat, each waiting on a player that is already not answering.
    self._busy = False
    self._registered_hotkey = None
    self._registered_command = None
    self._apply_config()

  # --- configuration -------------------------------------------------------

  def _cfg(self, key, default):
    """Read one setting, falling back to *default* for absent or blank."""
    value = self.irc.get_config('nowplaying', key, default)
    if isinstance(default, str):
      value = str(value if value is not None else '').strip()
      # A blank `format`/`template`/`progid`/`source`/`beefweb_url` is a
      # mistake with no useful meaning, so it falls back.  A blank
      # `hotkey`/`command` means "off" and is honoured -- see `_apply_config`.
      if not value and key not in ('hotkey', 'command'):
        return default
      return value
    if isinstance(default, bool):
      return bool(value)
    return value

  def _settings(self):
    """Snapshot every setting the worker thread needs.

    Taken on the GUI thread and passed by value, because `irc.get_config`
    reads `state.config`, which `qtpyrc._reload_config` *replaces* -- reading
    it from the worker would be a race against the user pressing Apply.
    """
    return {
      'source': self._cfg('source', DEFAULT_SOURCE).lower(),
      'beefweb_url': self._cfg('beefweb_url', DEFAULT_BEEFWEB_URL),
      'progid': self._cfg('progid', DEFAULT_PROGID),
      'format': self._cfg('format', DEFAULT_FORMAT),
      'timeout': DEFAULT_TIMEOUT,
    }

  def _apply_config(self):
    """(Re-)register the hotkey and the command from the current config.

    Registration is idempotent by design: `bind_key` and `add_command` both
    replace an existing entry of the same name, and this drops the previous
    one first when the name changed, so applying the settings dialog twice
    leaves one binding rather than three.
    """
    irc = self.irc

    hotkey = self._cfg('hotkey', DEFAULT_HOTKEY)
    if self._registered_hotkey and self._registered_hotkey != hotkey:
      irc.unbind_key(self._registered_hotkey)
      self._registered_hotkey = None
    if hotkey:
      try:
        self._registered_hotkey = irc.bind_key(
          hotkey, self._on_hotkey, 'Announce the track foobar2000 is playing')
      except (ValueError, RuntimeError) as e:
        irc.dbg(irc.LOG_WARN, 'nowplaying: cannot bind hotkey %r: %s'
                % (hotkey, e))
        self._registered_hotkey = None

    command = self._cfg('command', DEFAULT_COMMAND).lower().lstrip('/')
    if self._registered_command and self._registered_command != command:
      irc.remove_command(self._registered_command)
      self._registered_command = None
    if command:
      try:
        self._registered_command = irc.add_command(
          command, self._on_command,
          'announce the track foobar2000 is playing.  '
          '/%s [<title-format>] | -l (show locally) | -probe (diagnose)'
          % command)
      except ValueError as e:
        irc.dbg(irc.LOG_WARN, 'nowplaying: cannot register /%s: %s'
                % (command, e))
        self._registered_command = None

    # Reject a mistyped source here as well as at announce time.  A user who
    # is watching the log gets told when they save the settings, rather than
    # the next time they press the hotkey.
    source = self._cfg('source', DEFAULT_SOURCE).lower()
    if source not in SOURCES and source != 'auto':
      irc.dbg(irc.LOG_WARN,
              'nowplaying: unknown source %r -- expected auto, %s'
              % (source, ' or '.join(sorted(SOURCES))))

  def config_changed(self, irc):
    """Re-bind after the settings dialog or a config reload.

    The hotkey and the command name are the two settings that cannot be read
    at the point of use -- one is a live QShortcut, the other a key in a
    registry -- so without this, changing either in the settings dialog would
    appear to work and do nothing.
    """
    self._apply_config()

  # --- triggering ----------------------------------------------------------

  def _on_hotkey(self):
    self._announce(self.irc.active_window)

  def _on_command(self, window, text):
    arg = str(text).strip()
    if arg.lower() in ('-probe', '--probe'):
      self._start(window, None, mode='probe')
      return
    local = False
    if arg.lower() == '-l' or arg.lower().startswith('-l '):
      local = True
      arg = arg[2:].strip()
    elif arg.startswith('--'):
      window.redmessage('[nowplaying: unknown option %s]' % arg.split()[0])
      return
    self._announce(window, spec=arg or None, local=local)

  def _announce(self, window, spec=None, local=False):
    if window is None:
      return
    self._start(window, spec or self._cfg('format', DEFAULT_FORMAT),
                mode='local' if local else 'send')

  def _start(self, window, spec, mode):
    """Kick off the query on a worker thread."""
    if self._busy:
      window.redmessage('[nowplaying: still waiting on foobar2000]')
      return
    self._busy = True
    settings = self._settings()

    def _work():
      uninit = None
      try:
        # COM is initialised whether or not the COM source ends up being used:
        # `auto` may fall through to it, and CoInitialize on a thread that
        # never makes a COM call costs nothing.
        uninit = _co_initialize()
        if mode == 'probe':
          payload = ('probe', _probe(settings))
        else:
          payload = ('info', _fetch(settings, spec))
      except NotRunning as e:
        payload = ('error', str(e))
      except Exception as e:                           # noqa: BLE001
        payload = ('error', '%s: %s' % (type(e).__name__, e))
      finally:
        if uninit:
          try:
            uninit()
          except Exception:
            pass
      # Emitted from the worker thread; Qt queues it to the GUI thread because
      # `_Result` lives there.
      self._result.ready.emit((window, mode, payload))

    threading.Thread(target=_work, name='nowplaying-query',
                     daemon=True).start()

  # --- delivery (GUI thread) ----------------------------------------------

  def _deliver(self, bundle):
    self._busy = False
    window, mode, (kind, payload) = bundle
    # The user may have closed the window while foobar2000 was thinking, which
    # leaves a live Python wrapper around a deleted QWidget; `_widget_alive` is
    # the client's existing answer to that question (the same one
    # `link_preview` asks before it writes back its result).  There is nowhere
    # to put the answer and nothing worth reopening for.
    if window is None or not window._widget_alive():
      return

    if kind == 'error':
      window.redmessage('[nowplaying: %s]' % payload)
      return
    if kind == 'probe':
      window.redmessage('[nowplaying: probe]')
      for line in payload:
        window.redmessage('[  %s]' % line)
      return

    info = payload
    if not info.get('playing'):
      window.redmessage('[nowplaying: foobar2000 is not playing anything]')
      return
    message = _build_message(self._cfg('template', DEFAULT_TEMPLATE), info)
    if not message.strip():
      window.redmessage('[nowplaying: the message template produced nothing]')
      return

    conn = window.client.conn if getattr(window, 'client', None) else None
    if mode == 'local' or conn is None or window.type not in ('channel', 'query'):
      # Not an error worth a red line of its own: the answer is still useful,
      # so it is shown rather than thrown away.  Every reason not to send it
      # (asked for -l, not connected, a window with no conversation behind it)
      # ends the same way.
      window.addline(message)
      return

    from commands import send_message, send_action
    if self._cfg('action', False):
      send_action(window, conn, message)
    else:
      target = (window.channel.name if window.type == 'channel'
                else window.remotenick)
      send_message(window, conn, target, message, display_window=window)


Class = NowPlaying
