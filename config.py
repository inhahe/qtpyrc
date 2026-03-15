# config.py - Configuration, color helpers, ignore/auto-op logic

from PySide6.QtGui import QColor, QBrush, QTextCharFormat
from PySide6.QtCore import Qt

import traceback
from datetime import datetime
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
import fnmatch as _fnmatch

import state
import os

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


def _choice(value, choices, default):
  """Case-insensitive match against valid choices, returning lowercase."""
  if value is not None:
    v = str(value).lower()
    if v in choices:
      return v
  return default


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


def get_notify_list(network_key=None):
  """Collect the notify nick list (global + per-network, nicks only)."""
  out = list(_get_list_key(state.config._data, 'notify'))
  if network_key:
    net = state.config._net(network_key)
    out += _get_list_key(net, 'notify')
  return out


def modify_notify_entry(nick, remove, network_key=None):
  """Add or remove a nick from the notify list. Saves config."""
  _modify_list_entry('notify', nick, remove, network_key=network_key)


def get_highlights(network_key=None, channel=None):
  """Collect the additive highlights list for the given context."""
  out = list(_get_list_key(state.config._data, 'highlights'))
  if network_key:
    net = state.config._net(network_key)
    out += _get_list_key(net, 'highlights')
    if channel and net:
      chans = net.get('channels') or {}
      for k, v in chans.items():
        if k.lower() == channel.lower() and v:
          # highlights: false means disabled
          ch_val = v.get('highlights') if isinstance(v, dict) else None
          if ch_val is False:
            return []  # highlights disabled for this channel
          if isinstance(ch_val, list):
            out += ch_val
          break
  return out


def get_highlight_notify(network_key=None, channel=None):
  """Return whether highlight notifications are enabled for a channel.
  Returns False only if the channel explicitly sets highlight_notify: false."""
  if not network_key or not channel:
    return True
  net = state.config._net(network_key)
  if not net:
    return True
  chans = net.get('channels') or {}
  for k, v in chans.items():
    if k.lower() == channel.lower() and isinstance(v, dict):
      return v.get('highlight_notify', True)
  return True


import re as _re

_NAME_RE = _re.compile(
  r'\\\\|\\\{|\\\}'
  r'|\{([A-Za-z_]\w*)(?:\((?:(["\'])(.*?)\2)?\))?\}',
  _re.DOTALL
)

def _expand_vars(s, variables, warn_unknown=False, pattern_label=None,
                 allow_eval=False, eval_ns=None):
  r"""Replace {name} and {name("args")} references in a string.

  {name} and {name()} are equivalent — both look up the name with no argument.
  {name("arg")} or {name('arg')} passes a quoted argument.

  Lookup order:
    1. *variables* dict (string values — any argument is ignored)
    2. Built-in functions (eval, stdin) — only when *allow_eval* is True
    3. Unknown — left as-is

  Escaping: \{ and \} produce literal braces, \\ produces a literal backslash.
  Regex quantifiers like {3} or {1,5} are untouched (no alpha identifier inside).
  If *warn_unknown* is True, a warning is printed for each unknown name found.
  *eval_ns* is an optional dict of extra names for the eval/function namespace.
  """
  func_ns = None
  if allow_eval:
    import state as _state
    from importlib import import_module
    func_ns = {'state': _state, 'import_module': import_module,
               'stdin': lambda prompt='': input(prompt),
               'app': _state.app, 'mainwin': getattr(_state.app, 'mainwin', None),
               'clients': _state.clients}
    if eval_ns:
      func_ns.update(eval_ns)
  unknown = []
  def _repl(m):
    full = m.group(0)
    if full == '\\\\':
      return '\\'
    if full == '\\{':
      return '{'
    if full == '\\}':
      return '}'
    name = m.group(1)
    arg = m.group(3) if m.group(3) is not None else ''
    # 1. Variables dict
    if name in variables:
      return variables[name]
    # 2. Built-in functions (when allowed)
    if func_ns is not None:
      if name == 'eval':
        try:
          result = eval(arg, {'__builtins__': __builtins__}, func_ns)
          return str(result) if result is not None else ''
        except Exception:
          return ''
      elif name == 'stdin':
        try:
          return input(arg)
        except Exception:
          return ''
      elif name == 'input':
        try:
          from PySide6.QtWidgets import QInputDialog, QApplication
          parent = QApplication.activeWindow()
          text, ok = QInputDialog.getText(parent, 'Input', arg or 'Enter value:')
          return text.strip() if ok else ''
        except Exception:
          return ''
    # 3. Unknown
    unknown.append(name)
    return full
  result = _NAME_RE.sub(_repl, s)
  if warn_unknown and unknown:
    import state
    label = pattern_label or s
    for name in unknown:
      _warn_once('highlight_var_%s' % name,
                 'Highlight pattern %r: unknown variable {%s}' % (label, name))
  return result

_warned = set()
def _warn_once(key, message):
  """Print a warning to the active server window, but only once per key."""
  if key in _warned:
    return
  _warned.add(key)
  import state
  if state.app and state.app.mainwin:
    sub = state.app.mainwin.workspace.activeSubWindow()
    if sub and sub.widget():
      sub.widget().redmessage('[Warning: %s]' % message)


def is_highlight(message, my_nick, network_key=None, channel=None):
  """Check if a message matches any highlight pattern.
  Patterns support {me} (replaced with the user's current nick).
  Plain strings are case-insensitive substrings. /regex/[ims] for regex."""
  patterns = get_highlights(network_key, channel)
  if not patterns:
    return False
  msg_lower = message.lower()
  # Variables for substitution — support both {me} and legacy {nick}
  plain_vars = {'me': my_nick or '', 'nick': my_nick or ''}
  regex_vars = {'me': _re.escape(my_nick) if my_nick else '',
                'nick': _re.escape(my_nick) if my_nick else ''}
  for pat in patterns:
    if not pat:
      continue
    # /regex/[ims] syntax
    if pat.startswith('/') and ('/' in pat[1:]):
      last_slash = pat.rindex('/')
      regex = _expand_vars(pat[1:last_slash], regex_vars,
                           warn_unknown=True, pattern_label=pat)
      flags_str = pat[last_slash + 1:]
      flags = 0
      if 'i' in flags_str:
        flags |= _re.IGNORECASE
      if 'm' in flags_str:
        flags |= _re.MULTILINE
      if 's' in flags_str:
        flags |= _re.DOTALL
      try:
        if _re.search(regex, message, flags):
          return True
      except _re.error:
        pass
    else:
      # Plain string: case-insensitive substring match
      expanded = _expand_vars(pat, plain_vars,
                              warn_unknown=True, pattern_label=pat)
      if expanded and expanded.lower() in msg_lower:
        return True
  return False


def modify_highlight_entry(pattern, remove, network_key=None, channel=None):
  """Add or remove a highlight pattern. Saves config."""
  _modify_list_entry('highlights', pattern, remove, network_key, channel)


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
    if data is None:
      from ruamel.yaml.comments import CommentedMap
      data = CommentedMap()
    self._data = data           # CommentedMap — preserved for round-trip saving
    self._yaml = yaml_inst
    self.data = ConfigNode(data)  # read-only chained access: config.data.font.family

    # --- top-level scalars (with defaults) ---
    self.input_lines = max(1, min(10, int(data.get('input_lines', 1))))
    # Legacy compat: multiline_input: true -> input_lines: 3 (if not explicitly set)
    if 'input_lines' not in data and data.get('multiline_input', False):
      self.input_lines = 3
    self.multiline = self.input_lines > 1
    # Tab completion: max age in seconds for prioritizing nicks (0 = no limit)
    self.tab_complete_age = data.get('tab_complete_age', 0)
    self.cmdprefix = data.get('command_prefix', '/')
    self.popups_file = data.get('popups_file', '')
    self.variables_file = data.get('variables_file', '')
    self.external_editor = data.get('external_editor', '')
    self.ui_state_file = data.get('ui_state_file', 'ui.yaml')
    self.toolbar_file = data.get('toolbar_file', '')
    self.show_toolbar = data.get('show_toolbar', True)
    self.toolbar_icon_size = int(data.get('toolbar_icon_size', 20))
    # Legacy top-level toolbar keys (now under font.toolbar_* and colors.toolbar.*)
    self._legacy_toolbar_font_family = data.get('toolbar_font_family', None)
    self._legacy_toolbar_font_size = data.get('toolbar_font_size', None)
    self._legacy_toolbar_separator_color = data.get('toolbar_separator_color', None)
    self._legacy_toolbar_foreground_color = data.get('toolbar_foreground_color', None)
    self.window_mode = _choice(data.get('window_mode'), {'maximized', 'normal'}, 'maximized')
    self.titlebar_format = data.get('titlebar_format', '')
    self.titlebar_interval = max(float(data.get('titlebar_interval', 1)), 0.1)

    ident = data.get('ident') or {}
    self.ident_enabled = ident.get('enabled', True)
    self.ident_host = ident.get('host', '0.0.0.0')
    self.ident_port = ident.get('port', 113)
    self.identid = data.get('ident_username', 'qtpyrc')

    font = data.get('font') or {}
    self.fontfamily = font.get('family', 'Fixedsys')
    self.fontheight = font.get('size', font.get('font', 15))
    self.tab_font_family = font.get('tab_family', None)  # None = use system default
    self.tab_font_size = font.get('tab_size', None)  # None = use default
    self.toolbar_font_family = font.get('toolbar_family') or self._legacy_toolbar_font_family
    self.toolbar_font_size = font.get('toolbar_size') or self._legacy_toolbar_font_size
    if self.toolbar_font_size is not None:
      self.toolbar_font_size = int(self.toolbar_font_size)
    self.menu_font_family = font.get('menu_family', None)
    self.menu_font_size = font.get('menu_size', None)
    if self.menu_font_size is not None:
      self.menu_font_size = int(self.menu_font_size)
    self.tree_font_family = font.get('tree_family', None)
    self.tree_font_size = font.get('tree_size', None)
    if self.tree_font_size is not None:
      self.tree_font_size = int(self.tree_font_size)
    self.nicklist_font_family = font.get('nicklist_family', None)
    self.nicklist_font_size = font.get('nicklist_size', None)
    if self.nicklist_font_size is not None:
      self.nicklist_font_size = int(self.nicklist_font_size)
    self.editor_font_family = font.get('editor_family') or 'Consolas'
    self.editor_font_size = int(font.get('editor_size', 10))
    self.settings_font_family = font.get('settings_family', None)
    self.settings_font_size = font.get('settings_size', None)
    if self.settings_font_size is not None:
      self.settings_font_size = int(self.settings_font_size)
    self.tab_rows = data.get('tab_rows', 0)  # 0 = dynamic

    # Window title format strings
    titles = data.get('titles') or {}
    self.title_server = titles.get('server', '{network_label} - {me}')
    self.title_server_disconnected = titles.get('server_disconnected',
        '[not connected] {network_label} - {me}')
    self.title_channel = titles.get('channel', '{channel}')
    self.title_query = titles.get('query', '{query_nick}')

    # App-wide colors: colors.foreground / colors.background
    # with fallback to legacy font.color / font.fg_color / window_color / font.bg_color
    colors = data.get('colors') or {}
    fg_raw = colors.get('foreground') or font.get('color') or font.get('fg_color', 'black')
    bg_raw = colors.get('background') or data.get('window_color') or font.get('bg_color', 'white')
    self.fgcolor = _parse_color(fg_raw)
    self.bgcolor = _parse_color(bg_raw)

    # Editor colors (default: same as app colors)
    editor = colors.get('editor') or {}
    self.editor_fgcolor = _parse_color(editor.get('foreground')) if editor.get('foreground') else self.fgcolor
    self.editor_bgcolor = _parse_color(editor.get('background')) if editor.get('background') else self.bgcolor
    # Legacy editor font keys under colors.editor (now under font.editor_*)
    if not font.get('editor_family') and editor.get('font_family'):
      self.editor_font_family = editor.get('font_family')
    if not font.get('editor_size') and editor.get('font_size'):
      self.editor_font_size = int(editor.get('font_size'))

    # Menu colors (default: same as app colors)
    menu = colors.get('menu') or {}
    self.menu_fgcolor = _parse_color(menu.get('foreground')) if menu.get('foreground') else self.fgcolor
    self.menu_bgcolor = _parse_color(menu.get('background')) if menu.get('background') else self.bgcolor

    # Tree colors for network tree sidebar (default: same as app colors)
    tree = colors.get('tree') or {}
    self.tree_fgcolor = _parse_color(tree.get('foreground')) if tree.get('foreground') else self.fgcolor
    self.tree_bgcolor = _parse_color(tree.get('background')) if tree.get('background') else self.bgcolor

    # Nick list colors (default: same as app colors)
    nicklist = colors.get('nicklist') or {}
    self.nicklist_fgcolor = _parse_color(nicklist.get('foreground')) if nicklist.get('foreground') else self.fgcolor
    self.nicklist_bgcolor = _parse_color(nicklist.get('background')) if nicklist.get('background') else self.bgcolor

    # Toolbar colors (default: foreground for icons, midpoint for separator)
    toolbar_colors = colors.get('toolbar') or {}
    toolbar_fg_raw = toolbar_colors.get('foreground') or self._legacy_toolbar_foreground_color
    self.toolbar_fgcolor = _parse_color(toolbar_fg_raw) if toolbar_fg_raw else self.fgcolor
    toolbar_sep_raw = toolbar_colors.get('separator') or self._legacy_toolbar_separator_color
    if toolbar_sep_raw:
      self.toolbar_separator_color = _parse_color(toolbar_sep_raw)
    else:
      r = (self.fgcolor.red() + self.bgcolor.red()) // 2
      g = (self.fgcolor.green() + self.bgcolor.green()) // 2
      b = (self.fgcolor.blue() + self.bgcolor.blue()) // 2
      self.toolbar_separator_color = QColor(r, g, b)

    # Settings dialog colors (pages default to app colors, tree defaults to system)
    settings = colors.get('settings') or {}
    self.settings_fgcolor = _parse_color(settings['foreground']) if settings.get('foreground') else self.fgcolor
    self.settings_bgcolor = _parse_color(settings['background']) if settings.get('background') else self.bgcolor
    settings_tree = colors.get('settings_tree') or {}
    self.settings_tree_fgcolor = _parse_color(settings_tree['foreground']) if settings_tree.get('foreground') else self.settings_fgcolor
    self.settings_tree_bgcolor = _parse_color(settings_tree['background']) if settings_tree.get('background') else self.settings_bgcolor
    self.settings_tree_sel_fgcolor = _parse_color(settings_tree['select_fg']) if settings_tree.get('select_fg') else None
    self.settings_tree_sel_bgcolor = _parse_color(settings_tree['select_bg']) if settings_tree.get('select_bg') else None

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

    # Link color
    link_raw = colors.get('link')
    self.color_link = _parse_color(link_raw) if link_raw else QColor('#0066cc')

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

    self.history_file = data.get('history_file', 'history.db')
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
    self.startup_file = scripts_cfg.get('startup', '')

    ts = data.get('timestamps') or {}
    self.timestamp_format = ts.get('display', 'HH:mm')

    # Navigation: 'tabs', 'tree', or 'both'
    nav = data.get('navigation')
    if not nav:
      nav = 'both' if data.get('treeview', False) else 'tabs'
    self.navigation = _choice(nav, {'tabs', 'tree', 'both'}, 'tabs')
    self.show_tabs = self.navigation in ('tabs', 'both')
    self.show_tree = self.navigation in ('tree', 'both')
    self.treeview = self.show_tree  # compat
    self.view_mode = _choice(data.get('view_mode'), {'tabbed', 'mdi'}, 'tabbed')
    self.new_tab_state = _choice(data.get('new_tab_state'), {'active', 'normal', 'skipped'}, 'active')
    self.close_on_kick = data.get('close_on_kick', False)
    self.close_on_disconnect = data.get('close_on_disconnect', False)

    self.show_mode_prefix = data.get('show_mode_prefix', False)
    self.auto_copy_selection = data.get('auto_copy_selection', False)

    typing = data.get('typing') or {}
    self.typing_send = typing.get('send', True)
    self.typing_show = typing.get('show', True)

    # Link previews
    lp = data.get('link_preview') or {}
    if isinstance(lp, bool):
      lp = {'enabled': lp}
    self.link_preview_enabled = bool(lp.get('enabled', False))
    self.link_preview_max_size = int(lp.get('max_size', 65536))
    self.link_preview_timeout = float(lp.get('timeout', 5.0))
    self.link_preview_width = int(lp.get('width', 400))
    self.link_preview_height = int(lp.get('height', 120))
    self.link_preview_proxy = lp.get('proxy', '') or ''

    # Notifications
    notif = data.get('notifications') or {}
    # Global default sound: "beep", "none", or a .wav path
    self.notif_default_sound = notif.get('sound', 'beep')
    if self.notif_default_sound is True:
      self.notif_default_sound = 'beep'
    elif not self.notif_default_sound:
      self.notif_default_sound = 'none'

    def _notif(key, sound_def=False, desktop_def=False):
      sub = notif.get(key) or {}
      # sound: True/False/string, or legacy 'beep' boolean
      if 'sound' in sub:
        snd = sub['sound']
      elif 'beep' in sub:
        snd = sub['beep']
      else:
        snd = sound_def
      # Normalize: True -> 'default', False -> 'none', string kept as-is
      if snd is True:
        snd = 'default'
      elif snd is False or not snd:
        snd = 'none'
      elif isinstance(snd, str) and snd not in ('default', 'none'):
        pass  # custom sound path
      return (snd, sub.get('desktop', desktop_def))

    self.notif_notice = _notif('notice')
    self.notif_new_query = _notif('new_query', sound_def=True, desktop_def=True)
    self.notif_highlight = _notif('highlight', sound_def=True, desktop_def=True)
    self.notif_connect = _notif('connect')
    self.notif_disconnect = _notif('disconnect')
    self.notif_notify_online = _notif('notify_online', sound_def=True, desktop_def=True)
    self.notif_notify_offline = _notif('notify_offline')
    self.notify_check_interval = int(notif.get('check_interval', 60))

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

class UIState:
  """Persists UI state (splitter sizes, recent sounds, etc.) in ui.yaml."""

  def __init__(self, path):
    self.path = path
    self._yaml = YAML()
    self._yaml.preserve_quotes = True
    # Migrate from old layout.yaml if needed
    if not os.path.isfile(path):
      old = os.path.join(os.path.dirname(path), 'layout.yaml')
      if os.path.isfile(old):
        try:
          os.rename(old, path)
        except OSError:
          pass
    try:
      with open(path, 'r') as f:
        self._data = self._yaml.load(f) or {}
    except FileNotFoundError:
      self._data = {}
    # Ensure defaults exist so the file is always populated
    changed = False
    if 'nicklist_width' not in self._data:
      self._data['nicklist_width'] = 150
      changed = True
    if 'treeview_width' not in self._data:
      self._data['treeview_width'] = 180
      changed = True
    if changed:
      self.save()

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

  @property
  def recent_sounds(self):
    return list(self._data.get('recent_sounds') or [])

  def add_recent_sound(self, value):
    """Add a sound to the recent list (deduplicated, most recent first, max 20)."""
    if not value or value in ('beep', 'none', 'default'):
      return
    lst = list(self._data.get('recent_sounds') or [])
    if value in lst:
      lst.remove(value)
    lst.insert(0, value)
    self._data['recent_sounds'] = lst[:20]
    self.save()

  # -- recent external scripts / plugins --

  def _recent_list(self, key):
    return list(self._data.get(key) or [])

  def _add_recent(self, key, value, maxlen=50):
    lst = self._recent_list(key)
    if value in lst:
      return  # already present
    lst.insert(0, value)
    self._data[key] = lst[:maxlen]
    self.save()

  def _remove_recent(self, key, value):
    lst = self._recent_list(key)
    if value in lst:
      lst.remove(value)
      self._data[key] = lst
      self.save()

  @property
  def recent_script_paths(self):
    return self._recent_list('recent_script_paths')

  def add_recent_script_path(self, path):
    self._add_recent('recent_script_paths', path)

  def remove_recent_script_path(self, path):
    self._remove_recent('recent_script_paths', path)

  @property
  def recent_plugin_names(self):
    return self._recent_list('recent_plugin_names')

  def add_recent_plugin_name(self, name):
    self._add_recent('recent_plugin_names', name)

  def remove_recent_plugin_name(self, name):
    self._remove_recent('recent_plugin_names', name)

  # -- saved color picker colors --

  @property
  def saved_colors(self):
    return list(self._data.get('saved_colors') or [])

  @saved_colors.setter
  def saved_colors(self, colors):
    self._data['saved_colors'] = list(colors)
    self.save()

  @property
  def recent_colors(self):
    return list(self._data.get('recent_colors') or [])

  @recent_colors.setter
  def recent_colors(self, colors):
    self._data['recent_colors'] = list(colors)
    self.save()

  # -- script/plugin list order --

  @property
  def plugins_order(self):
    return list(self._data.get('plugins_order') or [])

  @plugins_order.setter
  def plugins_order(self, order):
    self._data['plugins_order'] = list(order)
    self.save()

  @property
  def scripts_order(self):
    return list(self._data.get('scripts_order') or [])

  @scripts_order.setter
  def scripts_order(self, order):
    self._data['scripts_order'] = list(order)
    self.save()

  @property
  def hex_uppercase(self):
    return bool(self._data.get('hex_uppercase', False))

  @hex_uppercase.setter
  def hex_uppercase(self, val):
    self._data['hex_uppercase'] = bool(val)
    self.save()

  @property
  def input_history(self):
    return list(self._data.get('input_history') or [])

  @input_history.setter
  def input_history(self, history):
    self._data['input_history'] = list(history)

  def save(self):
    try:
      with open(self.path, 'w') as f:
        self._yaml.dump(self._data, f)
    except Exception:
      traceback.print_exc()


def _update_text_formats(cfg):
  """Update global text formats from config colors."""
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


def loadconfig(configpath):
  yaml = YAML()
  yaml.preserve_quotes = True
  with open(configpath, 'r') as f:
    data = yaml.load(f)
  cfg = AppConfig(configpath, data, yaml)
  _update_text_formats(cfg)
  return cfg

def loadconfig_text(text, configpath):
  """Load config from text content, using *configpath* for file resolution."""
  from io import StringIO
  yaml = YAML()
  yaml.preserve_quotes = True
  data = yaml.load(StringIO(text))
  cfg = AppConfig(configpath, data, yaml)
  _update_text_formats(cfg)
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
