# plugins.py - Script/plugin loading and hook system

import sys
import os
import traceback

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
  def __init__(self, name, module, instance):
    self.name = name
    self.module = module
    self.instance = instance  # the Callbacks subclass instance


def init_irc():
  """Initialise the plugin.irc singleton.  Called once at startup."""
  def _get_active():
    sub = state.app.mainwin.workspace.activeSubWindow()
    return sub.widget() if sub else None
  from exec_system import _get_networks
  _plugin_api.irc._init(
    clients=state.clients, config=state.config, app=state.app,
    get_active_window=_get_active,
    get_networks=_get_networks,
  )

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
  # New-style Callbacks plugin
  try:
    inst = cls(_plugin_api.irc)
    return LoadedPlugin(name, module, inst)
  except Exception:
    dbg(LOG_ERROR, 'Plugin "%s" failed to init' % name)
    traceback.print_exc()
    return None

def _resolve_scripts_dir():
  """Return the absolute path to the scripts directory."""
  d = state.config.scripts_dir
  if os.path.isabs(d):
    return d
  # Relative to config file's directory
  return os.path.join(os.path.dirname(os.path.abspath(state.config.path)), d)

def _ensure_scripts_on_path():
  """Make sure the scripts directory's *parent* is on sys.path so we can
  import ``<dirname>.<scriptname>`` or, if scripts live directly in a
  standalone dir, that the dir itself is importable."""
  scripts_dir = _resolve_scripts_dir()
  if not os.path.isdir(scripts_dir):
    return scripts_dir
  # We need the *parent* of the scripts dir on sys.path so that
  # ``import <basename>.<script>`` works.  But we also add the scripts
  # dir itself so ``import plugin`` works from inside scripts.
  parent = os.path.dirname(scripts_dir)
  if parent and parent not in sys.path:
    sys.path.insert(0, parent)
  if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
  return scripts_dir

# The import package name derived from the scripts dir basename
_scripts_pkg = None

def _import_script(scripts_dir, name):
  """Import a script module by *name* from *scripts_dir*.

  Tries package-style import first (``<pkg>.<name>``), then falls back to
  a direct import if the scripts dir is on sys.path.
  Returns the module or raises ImportError.
  """
  global _scripts_pkg
  basename = os.path.basename(scripts_dir)
  if _scripts_pkg is None:
    _scripts_pkg = basename

  # Try as sub-module of the scripts package
  qual = "%s.%s" % (_scripts_pkg, name)
  try:
    __import__(qual)
    return sys.modules[qual]
  except ImportError:
    pass

  # Try direct import (scripts dir itself is on sys.path)
  import importlib
  return importlib.import_module(name)


def load_script_by_name(name, report_window=None):
  """Load (or reload) a single script by name.  Returns True on success."""
  scripts_dir = _resolve_scripts_dir()
  if not os.path.isdir(scripts_dir):
    msg = 'Scripts directory not found: %s' % scripts_dir
    dbg(LOG_ERROR, msg)
    if report_window:
      report_window.redmessage("[%s]" % msg)
    return False

  _ensure_scripts_on_path()

  # Check the script exists as a .py file or package dir
  py_path = os.path.join(scripts_dir, name + '.py')
  pkg_path = os.path.join(scripts_dir, name)
  if not os.path.isfile(py_path) and not (
      os.path.isdir(pkg_path) and os.path.isfile(os.path.join(pkg_path, '__init__.py'))):
    msg = 'Script "%s" not found in %s' % (name, scripts_dir)
    dbg(LOG_ERROR, msg)
    if report_window:
      report_window.redmessage("[%s]" % msg)
    return False

  # If already loaded, unload first
  if name in state.activescripts:
    old = state.activescripts.pop(name)
    if hasattr(old, 'instance') and hasattr(old.instance, 'die'):
      try:
        old.instance.die()
      except Exception:
        pass
    elif hasattr(old, 'script') and hasattr(old.script, 'die'):
      try:
        old.script.die()
      except Exception:
        pass

  try:
    # Force reimport if already cached
    qual = "%s.%s" % (_scripts_pkg or os.path.basename(scripts_dir), name)
    if qual in sys.modules:
      import importlib
      mod = importlib.reload(sys.modules[qual])
    elif name in sys.modules:
      import importlib
      mod = importlib.reload(sys.modules[name])
    else:
      mod = _import_script(scripts_dir, name)

    loaded = load_plugin(name, mod)
    if loaded:
      state.activescripts[name] = loaded
      dbg(LOG_INFO, 'Loaded script: %s' % name)
      if report_window:
        report_window.redmessage("[Loaded script: %s]" % name)
      return True
    else:
      msg = 'Script "%s" has no Class or Script attribute' % name
      dbg(LOG_ERROR, msg)
      if report_window:
        report_window.redmessage("[%s]" % msg)
      return False
  except Exception as e:
    dbg(LOG_ERROR, 'Could not load script "%s": %s' % (name, e))
    traceback.print_exc()
    if report_window:
      report_window.redmessage('[Error loading "%s": %s]' % (name, e))
    return False


def loadscripts():
  """Load auto_load scripts from config on startup."""
  scripts_dir = _resolve_scripts_dir()
  _ensure_scripts_on_path()
  loaded = {}
  for name in state.config.scripts_auto_load:
    name = str(name).strip()
    if not name:
      continue
    try:
      mod = _import_script(scripts_dir, name)
      p = load_plugin(name, mod)
      if p:
        loaded[name] = p
        dbg(LOG_INFO, 'Auto-loaded script: %s' % name)
      else:
        dbg(LOG_WARN, 'Script "%s" has no Class or Script attribute' % name)
    except Exception as e:
      dbg(LOG_ERROR, 'Could not auto-load script "%s": %s' % (name, e))
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

def _dispatch_to_plugins(name, conn, args, kwargs):
  """Call plugin hooks for event *name*.  Returns True if any suppressed."""
  from exec_system import _dispatch_on_hooks
  for loaded in state.activescripts.values():
    if isinstance(loaded, LoadedPlugin):
      handler = getattr(loaded.instance, name, None)
      if handler:
        # Check it's actually overridden (not the no-op base)
        base_method = getattr(_plugin_api.Callbacks, name, None)
        if handler.__func__ is not (base_method if base_method else None):
          try:
            if handler(loaded.instance.irc, conn, *args, **kwargs):
              return True
          except Exception:
            traceback.print_exc()
      # Also try on_numeric for irc_* events not in the named set
      if name.startswith('irc_') and name not in _SCRIPT_HOOKS:
        on_num = getattr(loaded.instance, 'on_numeric', None)
        base_on_num = getattr(_plugin_api.Callbacks, 'on_numeric', None)
        if on_num and on_num.__func__ is not base_on_num:
          try:
            if on_num(loaded.instance.irc, conn, name, *args, **kwargs):
              return True
          except Exception:
            traceback.print_exc()
    elif isinstance(loaded, Script):
      # Legacy script
      handler = getattr(loaded.script, name, None)
      if handler:
        try:
          if handler(conn, *args, **kwargs):
            return True
        except Exception:
          traceback.print_exc()
  # Dispatch /on hooks
  try:
    _dispatch_on_hooks(name, conn, args)
  except Exception:
    traceback.print_exc()
  return False

def _make_hook(name, original):
  """Wrap an IRCClient event method to dispatch to plugin hooks first."""
  def hooked(self, *args, **kwargs):
    if _dispatch_to_plugins(name, self, args, kwargs):
      return
    return original(self, *args, **kwargs)
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
