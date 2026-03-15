# config.py - Configuration, color helpers, ignore/auto-op logic

from PySide6.QtGui import QColor, QBrush, QTextCharFormat
from PySide6.QtCore import Qt

import traceback
from datetime import datetime
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
import fnmatch as _fnmatch

import state

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

_qt_colors = {
  'white': Qt.white, 'black': Qt.black, 'red': Qt.red, 'green': Qt.green,
  'blue': Qt.blue, 'cyan': Qt.cyan, 'magenta': Qt.magenta, 'yellow': Qt.yellow,
  'darkRed': Qt.darkRed, 'darkGreen': Qt.darkGreen, 'darkBlue': Qt.darkBlue,
  'darkCyan': Qt.darkCyan, 'darkMagenta': Qt.darkMagenta, 'darkYellow': Qt.darkYellow,
  'gray': Qt.gray, 'darkGray': Qt.darkGray, 'lightGray': Qt.lightGray,
}

def _parse_color(value):
  if isinstance(value, str):
    if value.startswith('#'):
      return QColor(value)
    return _qt_colors.get(value, Qt.black)
  return value


def _mask_match(s, mask):
  """Simple IRC wildcard mask match (* and ? only)."""
  return _fnmatch.fnmatch(s.lower(), mask.lower())

def _match_any(user, masks):
  """Return True if *user* matches any entry in *masks*."""
  if not masks:
    return False
  ulow = user.lower()
  nick = ulow.split('!', 1)[0]
  for m in masks:
    ml = m.lower()
    # Plain nick match (no ! or @)
    if '!' not in ml and '@' not in ml:
      if nick == ml:
        return True
    else:
      if _mask_match(ulow, ml):
        return True
  return False


def _get_list_key(data, key):
  """Read a list from a CommentedMap, always returning a Python list."""
  v = data.get(key) if data else None
  if v is None:
    return []
  return list(v)


def _set_list_key(data, key, lst):
  """Write a list into a CommentedMap (or delete the key if empty)."""
  if lst:
    data[key] = lst
  elif key in data:
    del data[key]


def _ensure_channels_map(net_data, channel):
  """Ensure networks.<net>.channels.<channel> exists as a CommentedMap."""
  if 'channels' not in net_data or net_data['channels'] is None:
    net_data['channels'] = CommentedMap()
  chans = net_data['channels']
  clow = channel.lower()
  # Find existing key case-insensitively
  for k in chans:
    if k.lower() == clow:
      if chans[k] is None:
        chans[k] = CommentedMap()
      return chans[k]
  chans[channel] = CommentedMap()
  return chans[channel]


# ---------------------------------------------------------------------------
# Three-level ignore / auto-op helpers
# ---------------------------------------------------------------------------

def get_ignores(network_key=None, channel=None):
  """Collect the additive ignore list for the given context."""
  out = list(_get_list_key(state.config._data, 'ignores'))
  if network_key:
    net = state.config._net(network_key)
    out += _get_list_key(net, 'ignores')
    if channel and net:
      chans = net.get('channels') or {}
      for k, v in chans.items():
        if k.lower() == channel.lower() and v:
          out += _get_list_key(v, 'ignores')
          break
  return out


def get_auto_ops(network_key=None, channel=None):
  """Collect the additive auto-op list for the given context."""
  out = list(_get_list_key(state.config._data, 'auto_ops'))
  if network_key:
    net = state.config._net(network_key)
    out += _get_list_key(net, 'auto_ops')
    if channel and net:
      chans = net.get('channels') or {}
      for k, v in chans.items():
        if k.lower() == channel.lower() and v:
          out += _get_list_key(v, 'auto_ops')
          break
  return out


def is_ignored(user, network_key=None, channel=None):
  """Check if a user string (nick or nick!ident@host) is ignored."""
  return _match_any(user, get_ignores(network_key, channel))


def is_auto_op(user, network_key=None, channel=None):
  """Check if a user matches an auto-op entry."""
  return _match_any(user, get_auto_ops(network_key, channel))


def _modify_list_entry(list_key, mask, remove, network_key=None, channel=None):
  """Add or remove a mask from ignores/auto_ops at the right level. Saves config."""
  mask_low = mask.lower()
  if channel and network_key:
    nets = state.config._data.get('networks')
    if nets and network_key in nets:
      net = nets[network_key]
      if net is None:
        nets[network_key] = CommentedMap()
        net = nets[network_key]
      ch_data = _ensure_channels_map(net, channel)
      lst = _get_list_key(ch_data, list_key)
      existing = [m for m in lst if m.lower() == mask_low]
      if remove:
        lst = [m for m in lst if m.lower() != mask_low]
      elif not existing:
        lst.append(mask)
      _set_list_key(ch_data, list_key, lst)
  elif network_key:
    nets = state.config._data.get('networks')
    if nets and network_key in nets:
      net = nets[network_key]
      if net is None:
        nets[network_key] = CommentedMap()
        net = nets[network_key]
      lst = _get_list_key(net, list_key)
      existing = [m for m in lst if m.lower() == mask_low]
      if remove:
        lst = [m for m in lst if m.lower() != mask_low]
      elif not existing:
        lst.append(mask)
      _set_list_key(net, list_key, lst)
  else:
    lst = _get_list_key(state.config._data, list_key)
    existing = [m for m in lst if m.lower() == mask_low]
    if remove:
      lst = [m for m in lst if m.lower() != mask_low]
    elif not existing:
      lst.append(mask)
    _set_list_key(state.config._data, list_key, lst)
  state.config.save()

# ---------------------------------------------------------------------------
# Configuration  (ruamel.yaml round-trip for comment preservation)
# ---------------------------------------------------------------------------

class AppConfig:
  """Application configuration backed by a ruamel.yaml round-trip document.

  Settings resolution order (most specific wins):
    server-level  >  network-level  >  global (top-level)
  """

  def __init__(self, path, data, yaml_inst):
    self.path = path
    self._data = data           # CommentedMap — preserved for round-trip saving
    self._yaml = yaml_inst

    # --- top-level scalars (with defaults) ---
    self.multiline = data.get('multiline_input', False)
    self.cmdprefix = data.get('command_prefix', '/')
    self.window_mode = data.get('window_mode', 'maximized')

    ident = data.get('ident') or {}
    self.ident_enabled = ident.get('enabled', True)
    self.ident_host = ident.get('host', '0.0.0.0')
    self.ident_port = ident.get('port', 113)
    self.identid = data.get('ident_username', 'qtpyrc')

    font = data.get('font') or {}
    self.fontfamily = font.get('family', 'Courier New')
    self.fontheight = font.get('size', font.get('font', 20))

    # App-wide colors: colors.foreground / colors.background
    # with fallback to legacy font.color / font.fg_color / window_color / font.bg_color
    colors = data.get('colors') or {}
    fg_raw = colors.get('foreground') or font.get('color') or font.get('fg_color', 'black')
    bg_raw = colors.get('background') or data.get('window_color') or font.get('bg_color', 'white')
    self.fgcolor = _parse_color(fg_raw)
    self.bgcolor = _parse_color(bg_raw)

    # Activity highlight colors (None = no change)
    highlight_raw = colors.get('highlight')
    newmsg_raw = colors.get('new_message')
    self.color_highlight = _parse_color(highlight_raw) if highlight_raw else QColor(255, 0, 0)
    self.color_new_message = _parse_color(newmsg_raw) if newmsg_raw else QColor(0, 0, 255)

    # IRC identity (global defaults — overridable per-network)
    self.nick = data.get('nick', data.get('nickname', 'qtpyrc_user'))
    self.alt_nicks = list(data.get('alt_nicks') or [])
    self.user = data.get('user', self.nick)
    self.realname = data.get('realname', self.nick)
    self.nickname = data.get('nickname', self.nick)    # display / compat

    self.auto_connect = data.get('auto_connect', False)
    self.persist_autojoins = data.get('persist_autojoins', False)
    self.rate_limit = (data.get('rate_limit', data.get('ms', 0)) or 0) / 1000.0
    self.flood = data.get('flood') or {}

    log = data.get('logging') or {}
    self.log_dir = log.get('dir', 'logs')
    self.log_use_subdirs = log.get('use_subdirs', False)
    self.log_separate_by_month = log.get('separate_by_month', False)
    self.log_debug = log.get('debug', False)
    self.log_timestamp_format = log.get('timestamp', 'YYYY-MM-DD HH:MM:SS')

    self.networks = data.get('networks') or {}
    self.nickswidth = data.get('nickswidth', 100)

    scripts_cfg = data.get('scripts') or {}
    self.scripts_dir = scripts_cfg.get('dir', 'scripts')
    self.scripts_auto_load = list(scripts_cfg.get('auto_load') or [])

    ts = data.get('timestamps') or {}
    self.timestamp_format = ts.get('display', 'HH:MM')

    self.treeview = data.get('treeview', False)
    self.view_mode = data.get('view_mode', 'tabbed')  # 'tabbed' or 'mdi'
    self.close_on_kick = data.get('close_on_kick', False)
    self.close_on_disconnect = data.get('close_on_disconnect', False)

  # --- resolution helpers ---

  def _net(self, network_key):
    return (self.networks.get(network_key) or {}) if network_key else {}

  def resolve(self, network_key, key, default=None):
    """Resolve a setting: network-level overrides global-level."""
    net = self._net(network_key)
    if key in net:
      v = net[key]
      if key in ('rate_limit', 'ms'):
        return (v or 0) / 1000.0
      return v
    val = getattr(self, key, default)
    return val

  def resolve_server(self, network_key, key, default=None):
    """Resolve: server-level > network-level > global-level.

    When multiple servers exist, checks the first server entry.
    """
    net = self._net(network_key)
    # Support both 'server' (single dict) and 'servers' (list of dicts)
    servers = net.get('servers')
    if servers and isinstance(servers, list) and len(servers) > 0:
      srv = servers[0] if isinstance(servers[0], dict) else {}
    else:
      srv = net.get('server') or {}
    if key in srv:
      return srv[key]
    if key in net:
      return net[key]
    return getattr(self, key, default)

  def get_servers(self, network_key):
    """Return a list of server dicts for a network.

    Supports both formats:
      server: {host: ..., port: ..., tls: ...}
      servers:
        - {host: ..., port: ..., tls: ...}
        - {host: ..., port: ..., tls: ...}
    """
    net = self._net(network_key)
    servers = net.get('servers')
    if servers and isinstance(servers, list):
      return [s for s in servers if isinstance(s, dict) and s.get('host')]
    srv = net.get('server') or {}
    if srv.get('host'):
      return [srv]
    return []

  # --- network matching ---

  def find_network_key(self, reported_name):
    """Find the config network key that matches a server-reported network name."""
    if not reported_name:
      return None
    rlow = reported_name.lower()
    for key, net in (self.networks or {}).items():
      if not net:
        continue
      name = (net.get('name') or key).lower()
      if name == rlow or name in rlow or rlow in name:
        return key
    return None

  # --- autojoin ---

  def get_autojoins(self, network_key):
    net = self._net(network_key)
    aj = net.get('auto_join')
    if not aj:
      return {}
    return {ch: (k if k else None) for ch, k in aj.items()}

  def update_autojoin(self, network_key, channel, key=None, remove=False):
    if not network_key:
      return
    nets = self._data.get('networks')
    if not nets or network_key not in nets:
      return
    net = nets[network_key]
    if not net:
      return
    if 'auto_join' not in net or net['auto_join'] is None:
      if remove:
        return
      net['auto_join'] = CommentedMap()
    aj = net['auto_join']
    if remove:
      if channel in aj:
        del aj[channel]
    else:
      aj[channel] = key
    self.save()

  def save(self):
    try:
      with open(self.path, 'w') as f:
        self._yaml.dump(self._data, f)
    except Exception:
      state.dbg(state.LOG_ERROR, 'Failed to save config to', self.path)
      traceback.print_exc()


# ---------------------------------------------------------------------------
# Layout state (UI sizes persisted across sessions)
# ---------------------------------------------------------------------------

class LayoutState:
  """Persists UI layout values (splitter sizes, etc.) in layout.yaml."""

  def __init__(self, path):
    self.path = path
    self._yaml = YAML()
    self._yaml.preserve_quotes = True
    try:
      with open(path, 'r') as f:
        self._data = self._yaml.load(f) or {}
    except FileNotFoundError:
      self._data = {}

  @property
  def nicklist_width(self):
    return self._data.get('nicklist_width', 150)

  @nicklist_width.setter
  def nicklist_width(self, val):
    self._data['nicklist_width'] = int(val)

  @property
  def treeview_width(self):
    return self._data.get('treeview_width', 180)

  @treeview_width.setter
  def treeview_width(self, val):
    self._data['treeview_width'] = int(val)

  def save(self):
    try:
      with open(self.path, 'w') as f:
        self._yaml.dump(self._data, f)
    except Exception:
      traceback.print_exc()


def loadconfig(configpath):
  yaml = YAML()
  yaml.preserve_quotes = True
  with open(configpath, 'r') as f:
    data = yaml.load(f)
  cfg = AppConfig(configpath, data, yaml)
  state.redformat = QTextCharFormat()
  state.redformat.setForeground(QBrush(Qt.red))
  state.redformat.setBackground(QBrush(cfg.bgcolor))
  state.defaultformat = QTextCharFormat()
  state.defaultformat.setForeground(QBrush(cfg.fgcolor))
  state.defaultformat.setBackground(QBrush(cfg.bgcolor))
  state.timestampformat = QTextCharFormat()
  state.timestampformat.setForeground(QBrush(QColor(128, 128, 128)))
  state.timestampformat.setBackground(QBrush(cfg.bgcolor))
  return cfg

# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------

def _format_timestamp(fmt, dt=None):
  """Format a timestamp using user-specified variable tokens.

  Supported tokens (case-sensitive):
    YYYY  — 4-digit year         YY — 2-digit year
    MM    — zero-padded month    DD — zero-padded day
    HH    — zero-padded hour (24h)  hh — 12-hour hour
    MI    — zero-padded minute (alias for when MM is ambiguous)
    SS    — zero-padded second
    AP    — AM/PM                ap — am/pm
    DOW   — weekday name (Mon, Tue, ...)
    MON   — month name (Jan, Feb, ...)

  For convenience, bare HH:MM means hour:minute (MM after HH is treated as
  minutes).  The tokens are replaced left-to-right so longer tokens match
  first.
  """
  if dt is None:
    dt = datetime.now()
  # We need to handle MM carefully: after HH or a colon it's minutes,
  # otherwise it's month.  Easiest: replace specific tokens longest-first.
  s = fmt
  s = s.replace('YYYY', dt.strftime('%Y'))
  s = s.replace('YY', dt.strftime('%y'))
  s = s.replace('DOW', dt.strftime('%a'))
  s = s.replace('MON', dt.strftime('%b'))
  s = s.replace('DD', dt.strftime('%d'))
  s = s.replace('HH', '\x00H\x00')   # placeholder to avoid double-replace
  s = s.replace('hh', dt.strftime('%I'))
  s = s.replace('MI', dt.strftime('%M'))
  s = s.replace('SS', dt.strftime('%S'))
  s = s.replace('AP', dt.strftime('%p').upper())
  s = s.replace('ap', dt.strftime('%p').lower())
  # Now handle MM: if it appears right after the hour placeholder, it's minutes
  # Replace hour placeholder first
  hour_str = dt.strftime('%H')
  s = s.replace('\x00H\x00', hour_str)
  # MM: if preceded by hour digits + separator, treat as minutes; else month
  # Simple heuristic: replace all remaining MM with minutes if HH was in
  # the original format, otherwise with month.
  if 'HH' in fmt:
    s = s.replace('MM', dt.strftime('%M'))
  else:
    s = s.replace('MM', dt.strftime('%m'))
  return s


def _color_to_config(color):
  """Convert a QColor to a config-storable string.

  Returns a Qt named color key if it matches exactly, otherwise hex.
  """
  for name, qt_color in _qt_colors.items():
    if QColor(qt_color) == color:
      return name
  return color.name()
