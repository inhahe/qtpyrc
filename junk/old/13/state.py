# state.py - Shared mutable globals
# No imports from other project modules.

# ---------------------------------------------------------------------------
# Debug levels
# ---------------------------------------------------------------------------

LOG_SILENT = 0
LOG_ERROR  = 1
LOG_WARN   = 2
LOG_INFO   = 3
LOG_DEBUG  = 4
LOG_TRACE  = 5

debug_level = LOG_INFO

_dbg_labels = {LOG_ERROR: 'ERROR', LOG_WARN: 'WARN', LOG_INFO: 'INFO',
               LOG_DEBUG: 'DEBUG', LOG_TRACE: 'TRACE'}

def dbg(level, *args):
  if level <= debug_level:
    print('[%s]' % _dbg_labels.get(level, '?'), *args)

# ---------------------------------------------------------------------------
# Shared mutable globals (set at startup by qtpyrc.py)
# ---------------------------------------------------------------------------

app = None
config = None
ui_state = None
clients = None
activescripts = None
irclogger = None
historydb = None

# Text formats (QTextCharFormat instances, set after config is loaded)
redformat = None
defaultformat = None
timestampformat = None
infoformat = None
actionformat = None
noticeformat = None

# Notification manager (set at startup by qtpyrc.py)
notifications = None
tray_icon = None

# Global list tracking open color-picker dialogs (so we can close them)
_colorcodewindow = []

# UI registry for /ui command — maps dot-paths to QAction or callable
# Populated by makeapp() for menu items; config pages handled by the command.
ui_registry = {}
ui_descriptions = {}  # dot-path -> human-readable description

# Named timers (/timer)
# _timers[name] = {'timer': QTimer, 'remaining': int, 'command': str,
#                   'window': Window, 'interval_ms': int}
_timers = {}

# Event hooks (/on)
# _on_hooks[event_name] = {hook_name: {'pattern': str, 'command': str,
#                                       'channel': str or None,
#                                       'network': str or None,
#                                       'window': Window}}
_on_hooks = {}

# Command aliases (/alias)
# _aliases[name] = command_string
_aliases = {}

# User-defined variables
# _variables merges persistent + temporary for lookup.
# _persistent_vars are saved to variables.ini (/set).
# _temp_vars are memory-only (/var).
_persistent_vars = {}
_temp_vars = {}
_variables = {}       # merged view — rebuilt by _merge_variables()

def _merge_variables():
  """Rebuild the merged variable dict (persistent overridden by temp)."""
  global _variables
  _variables = {}
  _variables.update(_persistent_vars)
  _variables.update(_temp_vars)

def _variables_path():
  """Return the path to variables.ini (respects config.variables_file)."""
  import os
  name = getattr(config, 'variables_file', '') if config else ''
  if not name:
    return None
  if os.path.isabs(name):
    return name
  if config and config.path:
    return os.path.join(os.path.dirname(os.path.abspath(config.path)), name)
  return None

def load_variables():
  """Load persistent variables from variables.ini."""
  global _persistent_vars
  path = _variables_path()
  if not path:
    return
  try:
    with open(path, 'r', encoding='utf-8') as f:
      for line in f:
        line = line.rstrip('\r\n')
        stripped = line.strip()
        if not stripped or stripped.startswith(';'):
          continue
        # Skip INI section headers like [variables]
        if stripped.startswith('[') and stripped.endswith(']'):
          continue
        # mIRC format: n0=%name value  or  %name value
        if '=' in stripped:
          name, _, value = stripped.partition('=')
          name = name.strip()
          value = value.strip()
          # mIRC numbered entries: n0=%string [none]
          if name and name[0].isdigit() and value.startswith('%'):
            parts = value[1:].split(None, 1)
            if parts:
              _persistent_vars[parts[0]] = parts[1] if len(parts) > 1 else ''
            continue
          # qtpyrc format: name = value
          if name:
            _persistent_vars[name] = value
        # Bare mIRC format: %name value
        elif stripped.startswith('%'):
          parts = stripped[1:].split(None, 1)
          if parts:
            _persistent_vars[parts[0]] = parts[1] if len(parts) > 1 else ''
  except FileNotFoundError:
    pass
  except Exception as e:
    dbg(LOG_ERROR, 'Error loading variables.ini: %s' % e)
  _merge_variables()

def load_variables_text(text):
  """Load persistent variables from text content."""
  global _persistent_vars
  _persistent_vars = {}
  for line in text.splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith(';'):
      continue
    if stripped.startswith('[') and stripped.endswith(']'):
      continue
    if '=' in stripped:
      name, _, value = stripped.partition('=')
      name = name.strip()
      value = value.strip()
      if name and name[0].isdigit() and value.startswith('%'):
        parts = value[1:].split(None, 1)
        if parts:
          _persistent_vars[parts[0]] = parts[1] if len(parts) > 1 else ''
        continue
      if name:
        _persistent_vars[name] = value
    elif stripped.startswith('%'):
      parts = stripped[1:].split(None, 1)
      if parts:
        _persistent_vars[parts[0]] = parts[1] if len(parts) > 1 else ''
  _merge_variables()

def save_variables():
  """Save persistent variables to variables.ini."""
  path = _variables_path()
  if not path:
    return
  try:
    with open(path, 'w', encoding='utf-8') as f:
      f.write('; variables.ini - Persistent user variables (/set)\n')
      f.write('; Format: name = value\n\n')
      for name in sorted(_persistent_vars):
        f.write('%s = %s\n' % (name, _persistent_vars[name]))
  except Exception as e:
    dbg(LOG_ERROR, 'Error saving variables.ini: %s' % e)
