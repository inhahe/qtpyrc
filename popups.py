# popups.py - mIRC-compatible popup menu system

import os
import re

from PySide6.QtWidgets import QMenu, QInputDialog

import state
from commands import docommand


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_popups(text, variables=None):
  """Parse mIRC-style popup definitions into a list of menu entries.

  If *variables* is provided, ``{name}`` references in labels and commands
  are expanded.

  Returns a list of tuples:
    ('item', depth, label, command)
    ('separator', depth, None, None)
  """
  from config import _expand_vars
  entries = []
  for line in text.splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith(';'):
      continue
    # Count leading dots for nesting depth
    depth = 0
    s = stripped
    while s.startswith('.'):
      depth += 1
      s = s[1:]
    s = s.strip()
    if s == '-':
      entries.append(('separator', depth, None, None))
      continue
    # Split label:command
    colon = s.find(':')
    if colon == -1:
      # Submenu header (no command)
      label = s
      if variables:
        label = _expand_vars(label, variables)
      entries.append(('item', depth, label, None))
    else:
      label = s[:colon].strip()
      command = s[colon + 1:].strip()
      if variables:
        label = _expand_vars(label, variables)
        command = _expand_vars(command, variables)
      entries.append(('item', depth, label, command))
  return entries


def _load_sections_text(text):
  """Parse popups text.  Returns {section_name: [entries]}.

  Lines of the form ``name = value`` (outside any section, or within a
  ``[variables]`` section) define variables that can be referenced as
  ``{name}`` in labels and commands throughout the file.

  Section directives:
    ``@additive`` — when this popup triggers on a sub-element (e.g. a nick
    anchor in a channel), append the parent popup's entries below.
    ``@replace`` (default) — only show this section's entries.
  """
  sections = {}
  modes = {}  # section_name -> 'additive' or 'replace'
  variables = {}
  current = None
  current_lines = []
  for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith('[') and stripped.endswith(']'):
      if current is not None and current != 'variables':
        sections[current] = _parse_popups('\n'.join(current_lines), variables)
      current = stripped[1:-1].lower()
      current_lines = []
    elif current == 'variables' or current is None:
      if stripped and not stripped.startswith(';') and '=' in stripped and ':' not in stripped.split('=')[0]:
        name, _, value = stripped.partition('=')
        name = name.strip()
        value = value.strip()
        if name:
          variables[name] = value
          continue
      current_lines.append(line)
    else:
      # Check for @directive
      if stripped.lower() in ('@additive', '@replace'):
        modes[current] = stripped[1:].lower()
      else:
        current_lines.append(line)
  if current is not None and current != 'variables':
    sections[current] = _parse_popups('\n'.join(current_lines), variables)
  # Store modes alongside sections
  sections['_modes'] = modes
  return sections


def _load_sections(filepath):
  """Load a popups.ini file.  Returns {section_name: [entries]}."""
  try:
    with open(filepath, 'r', encoding='utf-8') as f:
      return _load_sections_text(f.read())
  except FileNotFoundError:
    return {}
  except Exception as e:
    state.dbg(state.LOG_ERROR, 'Error loading popups file: %s' % e)
    return {}


# Global parsed popups: {section: [entries]}
_popups = {}


def load(filepath=None):
  """Load (or reload) the popups file."""
  global _popups
  if filepath is None:
    filepath = _resolve_popups_path()
  if filepath and os.path.isfile(filepath):
    _popups = _load_sections(filepath)
  else:
    _popups = {}


def _resolve_popups_path():
  """Return the absolute path to the popups file."""
  name = state.config.popups_file if state.config else ''
  if not name:
    return None
  if os.path.isabs(name):
    return name
  base = os.path.dirname(os.path.abspath(state.config.path)) if state.config else '.'
  return os.path.join(base, name)


# ---------------------------------------------------------------------------
# Variable expansion
# ---------------------------------------------------------------------------

# mIRC variable pattern: $$1, $1, $$?, $?, $$?="prompt", $?="prompt",
# $nick, $chan, $me, $network, $server, #  (bare # = channel name)
_MIRC_VAR_RE = re.compile(
  r'#\$\$\?="([^"]*)"'  # #$$?="prompt" (required, prepend #)
  r'|#\$\?="([^"]*)"'   # #$?="prompt" (optional, prepend #)
  r'|\$\$\?="([^"]*)"'  # $$?="prompt" (required input)
  r'|\$\?="([^"]*)"'    # $?="prompt" (optional input)
  r'|\$\$(\d+)'         # $$N (required positional)
  r'|\$(\d+)'           # $N (optional positional)
  r'|\$\$\?'            # $$? (required input, no prompt)
  r'|\$\?'              # $? (optional input, no prompt)
  r'|\$(\w+)'           # $variable
)


def _expand_mirc_vars(command, variables, window):
  """Expand mIRC-style variables in a command string.

  Returns the expanded string, or None if a required input was cancelled.
  """
  cancelled = [False]

  def _repl(m):
    if cancelled[0]:
      return ''
    full = m.group(0)

    # #$$?="prompt" — required input, prepend #
    if m.group(1) is not None:
      text, ok = QInputDialog.getText(window, 'Input', m.group(1))
      if not ok or not text.strip():
        cancelled[0] = True
        return ''
      return '#' + text.strip()

    # #$?="prompt" — optional input, prepend #
    if m.group(2) is not None:
      text, ok = QInputDialog.getText(window, 'Input', m.group(2))
      if not ok:
        cancelled[0] = True
        return ''
      return '#' + text.strip()

    # $$?="prompt" — required input dialog
    if m.group(3) is not None:
      text, ok = QInputDialog.getText(window, 'Input', m.group(3))
      if not ok or not text.strip():
        cancelled[0] = True
        return ''
      return text.strip()

    # $?="prompt" — optional input dialog
    if m.group(4) is not None:
      text, ok = QInputDialog.getText(window, 'Input', m.group(4))
      if not ok:
        cancelled[0] = True
        return ''
      return text.strip()

    # $$N — required positional
    if m.group(5) is not None:
      val = variables.get(m.group(5), '')
      if not val:
        cancelled[0] = True
        return ''
      return val

    # $N — optional positional
    if m.group(6) is not None:
      return variables.get(m.group(6), '')

    # $$? — required input, no prompt text
    if full == '$$?':
      text, ok = QInputDialog.getText(window, 'Input', 'Enter value:')
      if not ok or not text.strip():
        cancelled[0] = True
        return ''
      return text.strip()

    # $? — optional input, no prompt text
    if full == '$?':
      text, ok = QInputDialog.getText(window, 'Input', 'Enter value:')
      if not ok:
        cancelled[0] = True
        return ''
      return text.strip()

    # $variable — check context variables first, then user-defined variables
    if m.group(7) is not None:
      name = m.group(7)
      if name in variables:
        return variables[name]
      if name in state._variables:
        return state._variables[name]
      return full

    return full

  result = _MIRC_VAR_RE.sub(_repl, command)
  if cancelled[0]:
    return None

  # Bare # as a standalone word means current channel
  chan = variables.get('chan', '')
  if chan:
    result = re.sub(r'(?<!\S)#(?=\s|$)', chan, result)

  return result


# ---------------------------------------------------------------------------
# Menu building
# ---------------------------------------------------------------------------

def _build_menu(entries, variables, window, parent=None, target_menu=None):
  """Build a QMenu from parsed popup entries.

  If *target_menu* is given, items are added to it directly instead of
  creating a new QMenu.
  Returns the QMenu and an action->command mapping dict.
  """
  menu = target_menu or QMenu(parent or window)
  action_map = {}
  i = 0

  def _add_items(menu, start, depth):
    nonlocal i
    i = start
    while i < len(entries):
      kind, d, label, command = entries[i]
      if d < depth:
        return  # back up to parent
      if d > depth:
        continue  # skip (shouldn't happen if well-formed)
      if kind == 'separator':
        _add_menu_separator(menu)
        i += 1
      elif command is None:
        # Submenu header — look ahead for children
        submenu = menu.addMenu(label)
        i += 1
        _add_items(submenu, i, depth + 1)
      else:
        # Check if next entries are children (making this a submenu too)
        if i + 1 < len(entries) and entries[i + 1][1] > depth:
          submenu = menu.addMenu(label)
          # This item itself also has a command — add it as first entry
          act = submenu.addAction(label)
          action_map[act] = command
          _add_menu_separator(submenu)
          i += 1
          _add_items(submenu, i, depth + 1)
        else:
          act = menu.addAction(label)
          action_map[act] = command
          i += 1

  _add_items(menu, 0, 0)
  return menu, action_map


def get_mode(section):
  """Return 'additive' or 'replace' for a popup section."""
  modes = _popups.get('_modes', {})
  return modes.get(section, 'replace')


def show_popup(section, window, pos, extra_vars=None, copy_action=False,
               parent_section=None):
  """Show a popup menu for the given section name.

  *extra_vars* is an optional dict of additional variables (e.g. nick for
  nicklist popups).  *copy_action* adds a Copy item if True.
  *parent_section* is the fallback section for additive mode (e.g. 'channel'
  when showing 'nicklist' in a channel window).
  Returns True if a popup was shown, False otherwise.
  """
  entries = _popups.get(section)
  if not entries:
    return False

  # Build variable dict
  variables = {}
  client = getattr(window, 'client', None)
  conn = client.conn if client else None
  if client:
    variables['me'] = getattr(conn, 'nickname', '') or '' if conn else ''
    variables['network'] = getattr(client, 'network', '') or ''
    variables['server'] = getattr(client, 'hostname', '') or ''
  chan = getattr(window, 'channel', None)
  if chan:
    variables['chan'] = chan.name
  else:
    variables['chan'] = ''
  # Query window: $nick is the query target
  query = getattr(window, 'query', None)
  if query:
    variables.setdefault('nick', query.nick or '')
  if extra_vars:
    variables.update(extra_vars)

  menu, action_map = _build_menu(entries, variables, window, window)

  # Additive mode: append parent section entries directly into our menu
  if parent_section and get_mode(section) == 'additive':
    parent_entries = _popups.get(parent_section)
    if parent_entries:
      _add_menu_separator(menu)
      _, parent_map = _build_menu(parent_entries, variables, window,
                                  parent=menu, target_menu=menu)
      action_map.update(parent_map)

  # Add Copy action at the top when text is selected
  copy_act = None
  if copy_action:
    copy_act = menu.addSeparator()
    copy_act = menu.addAction("Copy")
    first = menu.actions()[0] if menu.actions() else None
    if first and first is not copy_act:
      # Move copy + separator to the top
      sep = menu.insertSeparator(first)
      menu.removeAction(copy_act)
      menu.insertAction(sep, copy_act)

  action = menu.exec(pos)
  if action is copy_act:
    from PySide6.QtWidgets import QApplication
    output = getattr(window, 'output', None)
    if output:
      output.copy()
    return True
  if not action or action not in action_map:
    return True  # menu was shown, just nothing selected

  command = action_map[action]

  # Expand mIRC variables
  expanded = _expand_mirc_vars(command, variables, window)
  if expanded is None:
    return True  # user cancelled a required input

  # Also run {variable} expansion
  from config import _expand_vars
  expanded = _expand_vars(expanded, variables)

  # Execute — support pipe-separated multiple commands
  for part in expanded.split(' | '):
    part = part.strip()
    if not part:
      continue
    _exec_popup_command(window, part)

  return True


def append_section_to_menu(menu, section, window, extra_vars=None):
  """Append a popup section's entries to an existing QMenu.

  Returns an action_map dict for dispatching selected actions.
  """
  if not section:
    return {}
  entries = _popups.get(section)
  if not entries:
    return {}

  variables = {}
  client = getattr(window, 'client', None)
  conn = client.conn if client else None
  if client:
    variables['me'] = getattr(conn, 'nickname', '') or '' if conn else ''
    variables['network'] = getattr(client, 'network', '') or ''
    variables['server'] = getattr(client, 'hostname', '') or ''
  chan = getattr(window, 'channel', None)
  if chan:
    variables['chan'] = chan.name
  else:
    variables['chan'] = ''
  query = getattr(window, 'query', None)
  if query:
    variables.setdefault('nick', query.nick or '')
  if extra_vars:
    variables.update(extra_vars)

  # Build entries directly into the target menu, with separator if appending
  action_map = {}
  # Prepend separator if appending to existing menu, but skip if
  # the section already starts with a separator (avoid double line)
  need_sep = bool(menu.actions())
  if need_sep and entries and entries[0][0] == 'separator':
    need_sep = False
  _build_into_menu(menu, entries, variables, action_map, prepend_sep=need_sep)
  return action_map


def _add_menu_separator(menu):
  """Add a visible separator to a QMenu using a widget action.

  Works around Qt/Windows 11 rendering inconsistencies with addSeparator()
  when QMenu has a custom stylesheet.
  """
  from PySide6.QtWidgets import QWidgetAction, QLabel
  wa = QWidgetAction(menu)
  lbl = QLabel(menu)
  lbl.setFixedHeight(9)
  lbl.setStyleSheet(
      "background: transparent;"
      "border-top: 1px solid #aaa;"
      "margin: 4px 6px 0px 6px;")
  wa.setDefaultWidget(lbl)
  menu.addAction(wa)


def _build_into_menu(menu, entries, variables, action_map, prepend_sep=False):
  """Add parsed popup entries directly into an existing QMenu."""
  if prepend_sep:
    _add_menu_separator(menu)
  i = 0
  def _add_items(target, start, depth):
    nonlocal i
    i = start
    while i < len(entries):
      kind, d, label, command = entries[i]
      if d < depth:
        return
      if d > depth:
        continue
      if kind == 'separator':
        _add_menu_separator(target)
        i += 1
      elif command is None:
        submenu = target.addMenu(label)
        i += 1
        _add_items(submenu, i, depth + 1)
      else:
        if i + 1 < len(entries) and entries[i + 1][1] > depth:
          submenu = target.addMenu(label)
          act = submenu.addAction(label)
          action_map[act] = command
          submenu.addSeparator()
          i += 1
          _add_items(submenu, i, depth + 1)
        else:
          act = target.addAction(label)
          action_map[act] = command
          i += 1
  _add_items(menu, 0, 0)


def exec_action(command, window):
  """Execute a popup command string with variable expansion."""
  variables = {}
  client = getattr(window, 'client', None)
  conn = client.conn if client else None
  if client:
    variables['me'] = getattr(conn, 'nickname', '') or '' if conn else ''
    variables['network'] = getattr(client, 'network', '') or ''
    variables['server'] = getattr(client, 'hostname', '') or ''
  chan = getattr(window, 'channel', None)
  if chan:
    variables['chan'] = chan.name
  expanded = _expand_mirc_vars(command, variables, window)
  if expanded is None:
    return
  from config import _expand_vars
  expanded = _expand_vars(expanded, variables)
  for part in expanded.split(' | '):
    part = part.strip()
    if part:
      _exec_popup_command(window, part)


def _exec_popup_command(window, text):
  """Execute a single popup command string."""
  prefix = state.config.cmdprefix
  if text.startswith(prefix):
    parts = text[len(prefix):].split(None, 1)
    docommand(window, parts[0], parts[1] if len(parts) > 1 else '')
  elif text.startswith('/'):
    parts = text[1:].split(None, 1)
    docommand(window, parts[0], parts[1] if len(parts) > 1 else '')
  else:
    docommand(window, 'say', text)
