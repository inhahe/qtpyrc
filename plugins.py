# plugins.py - Script/plugin loading and hook system

import sys
import os
import glob as _glob
import traceback

import subprocess

import state
from state import dbg, LOG_ERROR, LOG_WARN, LOG_INFO
import plugin as _plugin_api


class Script:
  """Legacy script wrapper (kept for backward compat)."""
  def __init__(self, module, script):
    self.module = module
    self.script = script

class LoadedPlugin:
  """A loaded plugin instance (new Callbacks-based API)."""
  def __init__(self, name, module, instance, irc=None):
    self.name = name
    self.module = module
    self.instance = instance  # the Callbacks subclass instance
    self.irc = irc            # this plugin's bound view of the API


def init_irc():
  """Initialise the plugin.irc singleton.  Called once at startup."""
  def _get_active():
    sub = state.app.mainwin.workspace.activeSubWindow()
    return sub.widget() if sub else None
  from exec_system import _get_networks
  _plugin_api.irc._init(
    get_active_window=_get_active,
    get_networks=_get_networks,
  )


def dispatch_config_changed():
  """Tell every loaded plugin that the configuration changed.

  Called after the settings dialog applies, and after Reload Configuration.

  Most plugin settings need no notification at all, because
  `irc.get_config()` reads `state.config` at the moment it is asked -- a
  plugin that looks its setting up when it uses it is already current.  This
  hook exists for the settings that *cannot* be read lazily because they were
  handed to something else at load time: a hotkey is a live `QShortcut` and a
  slash command is an entry in a registry, so changing either in the settings
  dialog does nothing at all until somebody re-registers it.  A setting the
  user changed, saved, and watched have no effect is the same silent-failure
  shape `add_command` and `bind_key` refuse.
  """
  for name, loaded in list(state.activescripts.items()):
    if not isinstance(loaded, LoadedPlugin):
      continue
    handler = getattr(loaded.instance, 'config_changed', None)
    base = getattr(_plugin_api.Callbacks, 'config_changed', None)
    if handler is None or handler.__func__ is base:
      continue
    try:
      handler(loaded.instance.irc)
    except Exception:
      dbg(LOG_ERROR, 'Plugin "%s" config_changed() failed' % name)
      traceback.print_exc()

def load_plugin(name, module):
  """Instantiate a single plugin module.  Returns a LoadedPlugin or None."""
  cls = getattr(module, 'Class', None)
  if cls is None:
    # Legacy script: look for script.Script(clients)
    legacy_cls = getattr(module, 'Script', None)
    if legacy_cls:
      try:
        inst = legacy_cls(state.clients)
        return Script(module, inst)
      except Exception:
        dbg(LOG_ERROR, 'Legacy script "%s" failed to init' % name)
        traceback.print_exc()
    return None
  # New-style Callbacks plugin.  Each one gets its own bound view of the API so
  # that unloading it takes away its hooks/timers/commands/hotkeys and nobody
  # else's -- see `plugin._Irc.for_plugin`.
  view = _plugin_api.irc.for_plugin(name)
  try:
    inst = cls(view)
    return LoadedPlugin(name, module, inst, irc=view)
  except Exception:
    dbg(LOG_ERROR, 'Plugin "%s" failed to init' % name)
    traceback.print_exc()
    # A constructor that raised half-way may already have registered things.
    try:
      view.remove_all()
    except Exception:
      traceback.print_exc()
    return None

def unload_plugin(name):
  """Unload the plugin registered as *name*.  Returns True if there was one.

  **The single unload path.**  `die()` is the plugin's own chance to clean up,
  but the teardown of what it registered through the API is done here rather
  than in `Callbacks.die()`, because overriding `die()` without chaining up to
  the base is both easy and common -- and a plugin whose hotkey or slash
  command survives it is worse than one that leaks a hook: the command raises
  out of a dead instance, and the hotkey does it with no visible cause.

  It was written twice before (here and in `Commands.plugin`) and the copies
  had already drifted -- `/plugin -u` reached `die()` through two `hasattr`
  chains while the reload path used a third.
  """
  loaded = state.activescripts.pop(name, None)
  if loaded is None:
    return False
  inst = getattr(loaded, 'instance', None) or getattr(loaded, 'script', None)
  die = getattr(inst, 'die', None) if inst is not None else None
  if die:
    try:
      die()
    except Exception as e:
      dbg(LOG_WARN, '[plugins] %s.die() failed: %s' % (name, e))
      traceback.print_exc()
  view = getattr(loaded, 'irc', None)
  if view is not None:
    try:
      view.remove_all()
    except Exception:
      traceback.print_exc()
  return True


# ---------------------------------------------------------------------------
# Where plugins live: a search path, not a directory
# ---------------------------------------------------------------------------
#
# `plugins.dir` names the *profile's* plugin directory, resolved relative to
# the config file -- so a profile in `me/` looks in `me/plugins/`.  The
# application's own `plugins/` directory is always searched as well, after it,
# because that is where the shipped plugins live and a profile has no business
# having to carry a copy of them.  See the "Plugins live on a search path"
# section of CLAUDE.md for why the copy is the failure and not the fix.

APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# Modules are registered in sys.modules under this prefix.  A plugin is loaded
# from an explicit file path rather than by importing its bare name, so that
# `chess` in the profile directory and `chess` in the application's directory
# are distinguishable, and so that "which file did this come from" has an
# answer that does not depend on sys.path ordering.
_MODULE_PREFIX = 'qtpyrc_plugin_'


class PluginFile:
  """Where a plugin was found.

  *name* is the name it loads under, *path* the module file to execute,
  *package_dir* the directory when it is a package (else None), and *plugin_dir*
  the search-path entry it was found in.
  """
  __slots__ = ('name', 'path', 'package_dir', 'plugin_dir')

  def __init__(self, name, path, package_dir, plugin_dir):
    self.name = name
    self.path = path
    self.package_dir = package_dir
    self.plugin_dir = plugin_dir

  def __repr__(self):
    return 'PluginFile(%r, %r)' % (self.name, self.path)


def app_plugin_dir():
  """The application's own plugin directory -- where shipped plugins live."""
  return os.path.join(APP_ROOT, 'plugins')


def _dedupe_dirs(dirs):
  """Drop repeats from a directory list, keeping the first of each."""
  out = []
  seen = set()
  for d in dirs:
    if not d:
      continue
    key = os.path.normcase(os.path.normpath(os.path.abspath(d)))
    if key in seen:
      continue
    seen.add(key)
    out.append(d)
  return out


def profile_plugin_dir(configured=None, config_dir=None):
  """The profile's plugin directory.

  *configured* defaults to `plugins.dir`; a relative value is resolved against
  *config_dir*, which defaults to the directory holding the config file.  Both
  are parameters so the settings dialog can ask about the directory the user is
  currently typing rather than the one that was loaded.
  """
  if configured is None:
    configured = state.config.scripts_dir
  configured = str(configured or '').strip() or 'plugins'
  if os.path.isabs(configured):
    return configured
  if config_dir is None:
    config_dir = os.path.dirname(os.path.abspath(state.config.path))
  return os.path.join(config_dir, configured)


def search_path_for(profile_dir):
  """The full plugin search path, given the profile's plugin directory.

  Highest priority first.  Running qtpyrc from its own directory makes the two
  entries the same, which is why this dedupes rather than always returning two.
  """
  return _dedupe_dirs([profile_dir, app_plugin_dir()])


def plugin_search_path():
  """Directories searched for plugins, highest priority first."""
  return search_path_for(profile_plugin_dir())


def _stem(path):
  base = os.path.basename(path.rstrip('\\/'))
  return base[:-3] if base.endswith('.py') else base


def find_plugin(entry, path=None):
  """Locate the plugin named by *entry*.  Returns a PluginFile or None.

  *entry* is a plain name, looked up on the search path in order, or a path (it
  contains a separator or is absolute), used directly.  A name matches a
  ``<name>.py`` file or a ``<name>/__init__.py`` package, the file first.
  """
  entry = str(entry).strip()
  if not entry:
    return None
  if _has_path(entry):
    p = os.path.abspath(entry)
    if os.path.isfile(p):
      return PluginFile(_stem(p), p, None, os.path.dirname(p))
    init = os.path.join(p, '__init__.py')
    if os.path.isfile(init):
      return PluginFile(_stem(p), init, p, os.path.dirname(p))
    return None
  for d in (plugin_search_path() if path is None else path):
    py = os.path.join(d, entry + '.py')
    if os.path.isfile(py):
      return PluginFile(entry, py, None, d)
    pkg = os.path.join(d, entry)
    init = os.path.join(pkg, '__init__.py')
    if os.path.isfile(init):
      return PluginFile(entry, init, pkg, d)
  return None


def available_plugins(path=None):
  """Every loadable plugin on the search path, as a dict name -> PluginFile.

  The first directory to offer a name wins, exactly as `find_plugin` resolves
  it -- so what the settings dialog lists is what would actually load.
  """
  found = {}
  for d in (plugin_search_path() if path is None else path):
    if not os.path.isdir(d):
      continue
    for entry in sorted(os.listdir(d)):
      if entry.startswith('_') or entry.startswith('.'):
        continue
      full = os.path.join(d, entry)
      if os.path.isfile(full) and entry.endswith('.py'):
        name = entry[:-3]
        if name not in found:
          found[name] = PluginFile(name, full, None, d)
      elif os.path.isdir(full) and os.path.isfile(
          os.path.join(full, '__init__.py')):
        if entry not in found:
          found[entry] = PluginFile(
            entry, os.path.join(full, '__init__.py'), full, d)
  return found


def _ensure_scripts_on_path():
  """Put the plugin search path on sys.path.  Returns the search path.

  Plugins import ``plugin`` (the API module, which lives beside this one) and
  may import helper modules sitting next to themselves, so the application's
  own directory and every plugin directory have to be importable.  The plugin
  modules themselves are *not* imported through sys.path -- see `_import_script`
  -- so ordering here decides only where a plugin's own imports resolve from.
  """
  dirs = plugin_search_path()
  for d in reversed(dirs + [APP_ROOT]):
    if os.path.isdir(d) and d not in sys.path:
      sys.path.insert(0, d)
  return dirs


def _find_requirements(found):
  """Return the path to a plugin's requirements.txt, or None."""
  if not found.package_dir:
    return None
  req = os.path.join(found.package_dir, 'requirements.txt')
  return req if os.path.isfile(req) else None

def _prompt_install_requirements(req_path, name):
  """Ask the user whether to pip install from requirements.txt.

  Returns True if installation succeeded, False otherwise.
  """
  from PySide6.QtWidgets import QMessageBox
  reply = QMessageBox.question(
    state.app.mainwin, 'Missing Dependencies',
    'Plugin "%s" failed to import a required module.\n\n'
    'Install dependencies from:\n%s?' % (name, req_path),
    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
  if reply != QMessageBox.StandardButton.Yes:
    return False
  try:
    result = subprocess.run(
      [sys.executable, '-m', 'pip', 'install', '-r', req_path],
      capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
      dbg(LOG_INFO, 'Installed requirements for "%s"' % name)
      return True
    else:
      dbg(LOG_ERROR, 'pip install failed for "%s": %s' % (name, result.stderr))
      QMessageBox.warning(
        state.app.mainwin, 'Installation Failed',
        'pip install failed:\n%s' % result.stderr[:500])
      return False
  except Exception as e:
    dbg(LOG_ERROR, 'pip install error for "%s": %s' % (name, e))
    return False


def _import_script(found):
  """Execute the module *found* names and return it.

  The plugin is loaded from its file, not by importing its bare name, because
  the same name can exist in more than one directory on the search path and
  only `find_plugin` gets to decide which one wins.  Executing it afresh every
  time is also what makes reloading work: there is no cached module to reload,
  and a plugin that moved between directories is picked up from wherever it is
  now.
  """
  import importlib.util
  modname = _MODULE_PREFIX + found.name
  spec = importlib.util.spec_from_file_location(
    modname, found.path,
    submodule_search_locations=(
      [found.package_dir] if found.package_dir else None))
  if spec is None or spec.loader is None:
    raise ImportError('cannot load plugin "%s" from %s'
                      % (found.name, found.path))
  mod = importlib.util.module_from_spec(spec)
  # Registered before execution so that a package's own ``from . import x``
  # can find its parent, and removed again if execution fails so that a broken
  # plugin does not leave a half-built module behind for the next attempt.
  sys.modules[modname] = mod
  try:
    spec.loader.exec_module(mod)
  except BaseException:
    sys.modules.pop(modname, None)
    raise
  return mod


def load_script_by_name(name, report_window=None):
  """Load (or reload) a single plugin by name.  Returns True on success."""
  dirs = _ensure_scripts_on_path()
  found = find_plugin(name, dirs)
  if found is None:
    msg = 'Plugin "%s" not found in %s' % (name, ', '.join(dirs))
    dbg(LOG_ERROR, msg)
    if report_window:
      report_window.redmessage("[%s]" % msg)
    return False

  key = found.name
  # If already loaded, unload first
  _reloading = unload_plugin(key)

  try:
    mod = _import_script(found)
    loaded = load_plugin(key, mod)
    if loaded:
      state.activescripts[key] = loaded
      verb = 'Reloaded' if _reloading else 'Loaded'
      dbg(LOG_INFO, '%s plugin: %s (%s)' % (verb, key, found.path))
      if report_window:
        report_window.redmessage("[%s plugin: %s]" % (verb, key))
      return True
    else:
      msg = 'Plugin "%s" has no Class or Script attribute' % key
      dbg(LOG_ERROR, msg)
      if report_window:
        report_window.redmessage("[%s]" % msg)
      return False
  except (ImportError, ModuleNotFoundError) as e:
    req = _find_requirements(found)
    if req and _prompt_install_requirements(req, key):
      # Retry after installing
      try:
        mod = _import_script(found)
        loaded = load_plugin(key, mod)
        if loaded:
          state.activescripts[key] = loaded
          dbg(LOG_INFO, 'Loaded plugin: %s (after installing deps)' % key)
          if report_window:
            report_window.redmessage("[Loaded plugin: %s]" % key)
          return True
      except Exception as e2:
        dbg(LOG_ERROR, 'Still could not load "%s" after install: %s' % (key, e2))
        traceback.print_exc()
        if report_window:
          report_window.redmessage('[Error loading "%s": %s]' % (key, e2))
        return False
    dbg(LOG_ERROR, 'Could not load plugin "%s": %s' % (key, e))
    traceback.print_exc()
    if report_window:
      report_window.redmessage('[Error loading "%s": %s]' % (key, e))
    return False
  except Exception as e:
    dbg(LOG_ERROR, 'Could not load plugin "%s": %s' % (key, e))
    traceback.print_exc()
    if report_window:
      report_window.redmessage('[Error loading "%s": %s]' % (key, e))
    return False


def _has_path(entry):
  """Return True if *entry* contains a directory component."""
  return os.sep in entry or '/' in entry or os.path.isabs(entry)

def _expand_auto_load(names, directories, extension='.py'):
  """Expand a list of auto_load names, resolving glob/wildcard patterns.

  Each entry is either a plain name or a pattern containing ``*``, ``?``,
  or ``[`` characters.  Plain names are returned as-is; patterns are matched
  against files in *directories* -- a search path, highest priority first --
  with the given *extension* and expanded to the stem (filename without
  extension).  Duplicates are suppressed while preserving order, so a name
  offered by two directories is listed once and resolved later by
  `find_plugin`, which searches the same path in the same order.

  Entries that contain a directory component (path separators or absolute
  paths) are globbed or returned directly — they are never joined with a
  search-path entry.  For such entries, the full path is returned instead of
  a stem.
  """
  result = []
  seen = set()
  for entry in names:
    entry = str(entry).strip()
    if not entry:
      continue
    is_wild = any(c in entry for c in ('*', '?', '['))
    if _has_path(entry):
      # Path-style entry — glob or pass through directly
      if is_wild:
        for path in sorted(_glob.glob(entry)):
          if os.path.isfile(path) and path not in seen:
            seen.add(path)
            result.append(path)
      else:
        if entry not in seen:
          seen.add(entry)
          result.append(entry)
    elif is_wild:
      # Bare wildcard — glob against every directory on the search path
      pat = entry
      if extension and not pat.endswith(extension):
        pat = pat + extension
      for directory in directories:
        if not os.path.isdir(directory):
          continue
        for path in sorted(_glob.glob(os.path.join(directory, pat))):
          if not os.path.isfile(path):
            continue
          base = os.path.basename(path)
          if base.startswith('_'):
            continue  # skip __init__.py, __pycache__, etc.
          stem = base[:-len(extension)] if extension and base.endswith(extension) else base
          if stem not in seen:
            seen.add(stem)
            result.append(stem)
    else:
      if entry not in seen:
        seen.add(entry)
        result.append(entry)
  return result


def loadscripts(suppress=None, extra=None):
  """Load auto_load scripts from config on startup.

  *suppress* is a list of fnmatch patterns; matching names are skipped.
  *extra* is a list of additional plugin names to load.
  """
  dirs = _ensure_scripts_on_path()
  names = _expand_auto_load(state.config.scripts_auto_load, dirs, '.py')
  if suppress:
    import fnmatch as _fnmatch
    names = [n for n in names
             if not any(_fnmatch.fnmatch(n, p) for p in suppress)]
  if extra:
    seen = set(names)
    for name in _expand_auto_load(extra, dirs, '.py'):
      if name not in seen:
        seen.add(name)
        names.append(name)
  loaded = {}
  for entry in names:
    found = find_plugin(entry, dirs)
    if found is None:
      dbg(LOG_ERROR, 'Could not auto-load plugin "%s": not found in %s'
          % (entry, ', '.join(dirs)))
      continue
    name = found.name
    try:
      mod = _import_script(found)
      p = load_plugin(name, mod)
      if p:
        loaded[name] = p
        dbg(LOG_INFO, 'Auto-loaded plugin: %s (%s)' % (name, found.path))
      else:
        dbg(LOG_WARN, 'Plugin "%s" has no Class or Script attribute' % name)
    except (ImportError, ModuleNotFoundError) as e:
      req = _find_requirements(found)
      if req and _prompt_install_requirements(req, name):
        try:
          mod = _import_script(found)
          p = load_plugin(name, mod)
          if p:
            loaded[name] = p
            dbg(LOG_INFO, 'Auto-loaded plugin: %s (after installing deps)' % name)
            continue
        except Exception as e2:
          dbg(LOG_ERROR, 'Still could not load "%s" after install: %s' % (name, e2))
          traceback.print_exc()
          continue
      dbg(LOG_ERROR, 'Could not auto-load plugin "%s": %s' % (name, e))
      traceback.print_exc()
    except Exception as e:
      dbg(LOG_ERROR, 'Could not auto-load plugin "%s": %s' % (name, e))
      traceback.print_exc()
  return loaded


# ---------------------------------------------------------------------------
# Plugin hook system
# ---------------------------------------------------------------------------

_SCRIPT_HOOKS = frozenset({
  # Connection lifecycle
  'connectionMade', 'connectionLost', 'signedOn',
  # Channel events
  'joined', 'left', 'names', 'endofnames',
  'userJoined', 'userLeft', 'userQuit', 'userKicked', 'kickedFrom',
  'topicUpdated', 'modeChanged',
  # Messages
  'privmsg', 'chanmsg', 'noticed', 'action',
  # Nick
  'nickChanged', 'userRenamed',
  # Other
  'receivedMOTD', 'bounce', 'isupport', 'irc_unknown',
  'networkChanged', 'invited', 'ctcpReply',
})

_PLUGIN_FULL_SUPPRESS = frozenset({'default', 'notify', 'activity'})

def _dispatch_to_plugins(name, conn, args, kwargs):
  """Call plugin hooks for event *name*.

  Returns a set of suppression flags (see _dispatch_on_hooks). Plugins that
  return truthy from a handler are treated as full suppression (default +
  notify + activity), matching legacy behavior."""
  from exec_system import _dispatch_on_hooks
  flags = set()
  for sname, loaded in state.activescripts.items():
    if isinstance(loaded, LoadedPlugin):
      handler = getattr(loaded.instance, name, None)
      if handler:
        # Check it's actually overridden (not the no-op base)
        base_method = getattr(_plugin_api.Callbacks, name, None)
        if handler.__func__ is not (base_method if base_method else None):
          try:
            if handler(loaded.instance.irc, conn, *args, **kwargs):
              return set(_PLUGIN_FULL_SUPPRESS)
          except Exception:
            traceback.print_exc()
      # Also try on_numeric for irc_* events not in the named set
      if name.startswith('irc_') and name not in _SCRIPT_HOOKS:
        on_num = getattr(loaded.instance, 'on_numeric', None)
        base_on_num = getattr(_plugin_api.Callbacks, 'on_numeric', None)
        if on_num and on_num.__func__ is not base_on_num:
          try:
            if on_num(loaded.instance.irc, conn, name, *args, **kwargs):
              return set(_PLUGIN_FULL_SUPPRESS)
          except Exception:
            traceback.print_exc()
    elif isinstance(loaded, Script):
      # Legacy script
      handler = getattr(loaded.script, name, None)
      if handler:
        try:
          if handler(conn, *args, **kwargs):
            return set(_PLUGIN_FULL_SUPPRESS)
        except Exception:
          traceback.print_exc()
  # Dispatch /on hooks
  try:
    on_flags = _dispatch_on_hooks(name, conn, args)
    if on_flags:
      flags |= on_flags
  except Exception:
    traceback.print_exc()
  return flags

# Events where the last positional arg is a message/text to tokenize
_TOKENIZE_EVENTS = {
  'chanmsg': 2,     # (user, channel, message) -> index 2
  'privmsg': 1,     # (user, message) -> index 1
  'noticed': 2,     # (user, channel, message) -> index 2
  'action': 2,      # (user, channel, data) -> index 2
}

def _make_hook(name, original):
  """Wrap an IRCClient event method to dispatch to plugin hooks first."""
  def hooked(self, *args, **kwargs):
    # Wrap message args with TokenizedString for plugin access
    if name in _TOKENIZE_EVENTS:
      idx = _TOKENIZE_EVENTS[name]
      if len(args) > idx and isinstance(args[idx], str):
        from commands import TokenizedString
        args = list(args)
        args[idx] = TokenizedString(args[idx])
        args = tuple(args)
    flags = _dispatch_to_plugins(name, self, args, kwargs)
    if 'default' in flags:
      return
    prev = getattr(self, '_suppress_flags', frozenset())
    self._suppress_flags = flags
    try:
      return original(self, *args, **kwargs)
    finally:
      self._suppress_flags = prev
  hooked.__name__ = name
  return hooked

def apply_hooks():
  """Apply plugin hooks to IRCClient. Must be called after irc_client is imported."""
  from irc_client import IRCClient
  # Wrap the explicit event hooks
  for _name in _SCRIPT_HOOKS:
    _orig = getattr(IRCClient, _name, None)
    if _orig and callable(_orig):
      setattr(IRCClient, _name, _make_hook(_name, _orig))

  # Also wrap irc_* handlers so plugins can hook specific IRC commands/numerics
  for _name in dir(IRCClient):
    if _name.startswith('irc_') and _name not in _SCRIPT_HOOKS:
      _orig = getattr(IRCClient, _name)
      if callable(_orig):
        setattr(IRCClient, _name, _make_hook(_name, _orig))
