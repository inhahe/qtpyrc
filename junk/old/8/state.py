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
layout = None
clients = None
activescripts = None
irclogger = None

# Text formats (QTextCharFormat instances, set after config is loaded)
redformat = None
defaultformat = None
timestampformat = None
infoformat = None
actionformat = None
noticeformat = None

# Global list tracking open color-picker dialogs (so we can close them)
_colorcodewindow = []

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
