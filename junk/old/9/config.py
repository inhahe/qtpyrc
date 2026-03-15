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
# Null sentinel & ConfigNode — safe chained attribute access
# ---------------------------------------------------------------------------

class _Null:
  """Sentinel for missing config values.

  Falsy, and returns itself for any attribute or item access, so
  ``config.networks.nonexistent.server.host`` safely evaluates to ``null``
  instead of raising AttributeError/TypeError.
  """
  _instance = None

  def __new__(cls):
    if cls._instance is None:
      cls._instance = super().__new__(cls)
    return cls._instance

  def __bool__(self):       return False
  def __repr__(self):       return 'null'
  def __str__(self):        return ''
  def __int__(self):        return 0
  def __float__(self):      return 0.0
  def __getattr__(self, _): return self
  def __getitem__(self, _): return self
  def __contains__(self, _):return False
  def __iter__(self):       return iter(())
  def __len__(self):        return 0
  def __eq__(self, other):  return isinstance(other, _Null) or other is None
  def __ne__(self, other):  return not self.__eq__(other)
  def __hash__(self):       return hash(None)
  def get(self, key, default=None):
    return self if default is None else default
  def items(self):          return iter(())
  def keys(self):           return iter(())
  def values(self):         return iter(())

null = _Null()


class ConfigNode:
  """Read-only attribute-access wrapper around a dict (or CommentedMap).

  Returns ``null`` for missing keys or when the underlying data is None,
  and recursively wraps nested dicts so that ``node.a.b.c`` always works.

  The raw dict is still accessible via ``node._data`` for code that needs
  to mutate or save it.
  """
  __slots__ = ('_data',)

  def __init__(self, data):
    object.__setattr__(self, '_data', data if data is not None else {})

  def __getattr__(self, name):
    data = object.__getattribute__(self, '_data')
    if isinstance(data, dict):
      v = data.get(name)
      if v is not None:
        return _wrap(v)
      if name in data:
        return null          # key exists but value is None
    return null

  def __getitem__(self, key):
    data = object.__getattribute__(self, '_data')
    if isinstance(data, dict):
      v = data.get(key)
      if v is not None:
        return _wrap(v)
      if key in data:
        return null
    return null

  def __contains__(self, key):
    return key in object.__getattribute__(self, '_data')

  def __iter__(self):
    return iter(object.__getattribute__(self, '_data'))

  def __len__(self):
    return len(object.__getattribute__(self, '_data'))

  def __bool__(self):
    return bool(object.__getattribute__(self, '_data'))

  def __repr__(self):
    return 'ConfigNode(%r)' % object.__getattribute__(self, '_data')

  def get(self, key, default=None):
    data = object.__getattribute__(self, '_data')
    if isinstance(data, dict) and key in data:
      v = data[key]
      if v is None:
        return default if default is not None else null
      return _wrap(v)
    return default if default is not None else null

  def items(self):
    for k, v in object.__getattribute__(self, '_data').items():
      yield k, _wrap(v) if v is not None else null

  def keys(self):
    return object.__getattribute__(self, '_data').keys()

  def values(self):
    for v in object.__getattribute__(self, '_data').values():
      yield _wrap(v) if v is not None else null


def _wrap(value):
  """Wrap a value for safe chained access."""
  if value is None:
    return null
  if isinstance(value, dict):
    return ConfigNode(value)
  if isinstance(value, list):
    return [_wrap(v) for v in value]
  return value


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
    return QColor(_qt_colors.get(value, Qt.black))
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
    self.data = ConfigNode(data)  # read-only chained access: config.data.font.family

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
    self.tab_font_family = font.get('tab_family', None)  # None = use system default
    self.tab_font_size = font.get('tab_size', None)  # None = use default
    self.tab_rows = data.get('tab_rows', 0)  # 0 = dynamic
    self.show_network_in_tabs = data.get('show_network_in_tabs', True)

    # App-wide colors: colors.foreground / colors.background
    # with fallback to legacy font.color / font.fg_color / window_color / font.bg_color
    colors = data.get('colors') or {}
    fg_raw = colors.get('foreground') or font.get('color') or font.get('fg_color', 'black')
    bg_raw = colors.get('background') or data.get('window_color') or font.get('bg_color', 'white')
    self.fgcolor = _parse_color(fg_raw)
    self.bgcolor = _parse_color(bg_raw)

    # System message color (connection status, errors, etc.)
    system_raw = colors.get('system')
    self.color_system = _parse_color(system_raw) if system_raw else QColor(Qt.red)

    # Tab colors (defaults: active = inverted fg/bg, skipped = dimmed fg)
    tabs = colors.get('tabs') or {}
    self.tab_active_fg = _parse_color(tabs['active_fg']) if tabs.get('active_fg') else None
    self.tab_active_bg = _parse_color(tabs['active_bg']) if tabs.get('active_bg') else None
    self.tab_normal_fg = _parse_color(tabs['normal_fg']) if tabs.get('normal_fg') else None
    self.tab_normal_bg = _parse_color(tabs['normal_bg']) if tabs.get('normal_bg') else None
    self.tab_skipped_fg = _parse_color(tabs['skipped_fg']) if tabs.get('skipped_fg') else None
    self.tab_skipped_bg = _parse_color(tabs['skipped_bg']) if tabs.get('skipped_bg') else None
    self.tab_bar_bg = _parse_color(tabs['bar_bg']) if tabs.get('bar_bg') else None

    # Chat message type colors
    info_raw = colors.get('info')
    self.color_info = _parse_color(info_raw) if info_raw else QColor(Qt.darkGreen)
    action_raw = colors.get('action')
    self.color_action = _parse_color(action_raw) if action_raw else QColor(Qt.darkMagenta)
    notice_raw = colors.get('notice')
    self.color_notice = _parse_color(notice_raw) if notice_raw else QColor(Qt.darkCyan)

    # Search highlight color
    search_bg_raw = colors.get('search_bg')
    search_fg_raw = colors.get('search_fg')
    self.color_search_bg = _parse_color(search_bg_raw) if search_bg_raw else QColor(255, 255, 0)
    self.color_search_fg = _parse_color(search_fg_raw) if search_fg_raw else QColor(0, 0, 0)

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
    self.log_timestamp_format = log.get('timestamp', 'YYYY-MM-DD HH:mm:SS')

    self.backscroll_limit = data.get('backscroll_limit', 10000)
    hr = data.get('history_replay') or {}
    if isinstance(hr, int):
      # legacy: single value applies to channels only
      self.history_replay_channels = hr
      self.history_replay_queries = 0
    else:
      self.history_replay_channels = hr.get('channels', self.backscroll_limit)
      self.history_replay_queries = hr.get('queries', 0)

    self.networks = data.get('networks') or {}
    self.nickswidth = data.get('nickswidth', 100)

    plugins_cfg = data.get('plugins') or {}
    self.scripts_dir = plugins_cfg.get('dir', 'plugins')
    self.scripts_auto_load = list(plugins_cfg.get('auto_load') or [])

    scripts_cfg = data.get('scripts') or {}
    self.cmdscripts_dir = scripts_cfg.get('dir', 'scripts')
    self.scripts_auto_run = list(scripts_cfg.get('auto_load') or [])

    ts = data.get('timestamps') or {}
    self.timestamp_format = ts.get('display', 'HH:mm')

    # Navigation: 'tabs', 'tree', or 'both'
    nav = data.get('navigation')
    if nav:
      self.navigation = nav
    else:
      # Legacy fallback
      self.navigation = 'both' if data.get('treeview', False) else 'tabs'
    self.show_tabs = self.navigation in ('tabs', 'both')
    self.show_tree = self.navigation in ('tree', 'both')
    self.treeview = self.show_tree  # compat
    self.view_mode = data.get('view_mode', 'tabbed')  # 'tabbed' or 'mdi'
    self.new_tab_state = data.get('new_tab_state', 'active')  # 'active', 'normal', or 'skipped'
    self.close_on_kick = data.get('close_on_kick', False)
    self.close_on_disconnect = data.get('close_on_disconnect', False)

  # --- resolution helpers ---

  def _net(self, network_key):
    """Return the raw network dict (CommentedMap) for mutation, or {}."""
    return (self.networks.get(network_key) or {}) if network_key else {}

  def net(self, network_key):
    """Return a ConfigNode for the network — safe for chained attribute access."""
    return ConfigNode(self._net(network_key))

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
  state.redformat.setForeground(QBrush(cfg.color_system))
  state.redformat.setBackground(QBrush(cfg.bgcolor))
  state.defaultformat = QTextCharFormat()
  state.defaultformat.setForeground(QBrush(cfg.fgcolor))
  state.defaultformat.setBackground(QBrush(cfg.bgcolor))
  state.timestampformat = QTextCharFormat()
  state.timestampformat.setForeground(QBrush(QColor(128, 128, 128)))
  state.timestampformat.setBackground(QBrush(cfg.bgcolor))
  state.infoformat = QTextCharFormat()
  state.infoformat.setForeground(QBrush(cfg.color_info))
  state.infoformat.setBackground(QBrush(cfg.bgcolor))
  state.actionformat = QTextCharFormat()
  state.actionformat.setForeground(QBrush(cfg.color_action))
  state.actionformat.setBackground(QBrush(cfg.bgcolor))
  state.noticeformat = QTextCharFormat()
  state.noticeformat.setForeground(QBrush(cfg.color_notice))
  state.noticeformat.setBackground(QBrush(cfg.bgcolor))
  return cfg

# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------

def _format_timestamp(fmt, dt=None):
  """Format a timestamp using user-specified variable tokens.

  Supported tokens (case-sensitive):
    YYYY  — 4-digit year           YY  — 2-digit year
    MM    — zero-padded month      MON — month abbreviation (Jan, Feb, ...)
    DD    — zero-padded day        DOW — weekday abbreviation (Mon, Tue, ...)
    HH    — 24-hour hour           hh  — 12-hour hour
    mm    — zero-padded minutes    MI  — minutes (alias for mm)
    SS    — zero-padded seconds
    AP    — AM/PM (uppercase)      ap  — am/pm (lowercase)
  """
  if dt is None:
    dt = datetime.now()
  s = fmt
  # Replace longest tokens first to avoid partial matches
  s = s.replace('YYYY', dt.strftime('%Y'))
  s = s.replace('YY', dt.strftime('%y'))
  s = s.replace('DOW', dt.strftime('%a'))
  s = s.replace('MON', dt.strftime('%b'))
  s = s.replace('DD', dt.strftime('%d'))
  s = s.replace('MM', dt.strftime('%m'))
  s = s.replace('HH', dt.strftime('%H'))
  s = s.replace('hh', dt.strftime('%I'))
  s = s.replace('MI', dt.strftime('%M'))
  s = s.replace('mm', dt.strftime('%M'))
  s = s.replace('SS', dt.strftime('%S'))
  s = s.replace('AP', dt.strftime('%p').upper())
  s = s.replace('ap', dt.strftime('%p').lower())
  return s


def _color_to_config(color):
  """Convert a QColor to a config-storable string.

  Returns a Qt named color key if it matches exactly, otherwise hex.
  """
  for name, qt_color in _qt_colors.items():
    if QColor(qt_color) == color:
      return name
  return color.name()
