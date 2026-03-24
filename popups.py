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


def _load_sections(filepath):
  """Load a popups.ini file.  Returns {section_name: [entries]}.

  Lines of the form ``name = value`` (outside any section, or within a
  ``[variables]`` section) define variables that can be referenced as
  ``{name}`` in labels and commands throughout the file.
  """
  from config import _expand_vars
  sections = {}
  variables = {}
  current = None
  current_lines = []
  try:
    with open(filepath, 'r', encoding='utf-8') as f:
      for line in f:
        line = line.rstrip('\r\n')
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
          # Save previous section
          if current is not None and current != 'variables':
            sections[current] = _parse_popups('\n'.join(current_lines), variables)
          current = stripped[1:-1].lower()
          current_lines = []
        elif current == 'variables' or current is None:
          # Variable definitions before any section or in [variables]
          if stripped and not stripped.startswith(';') and '=' in stripped and ':' not in stripped.split('=')[0]:
            name, _, value = stripped.partition('=')
            name = name.strip()
            value = value.strip()
            if name:
              variables[name] = value
              continue
          current_lines.append(line)
        else:
          current_lines.append(line)
    if current is not None and current != 'variables':
      sections[current] = _parse_popups('\n'.join(current_lines), variables)
  except FileNotFoundError:
    pass
  except Exception as e:
    state.dbg(state.LOG_ERROR, 'Error loading popups file: %s' % e)
  return sections


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

def _build_menu(entries, variables, window, parent=None):
  """Build a QMenu from parsed popup entries.

  Returns the QMenu and an action->command mapping dict.
  """
  menu = QMenu(parent or window)
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
        menu.addSeparator()
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
          submenu.addSeparator()
          i += 1
          _add_items(submenu, i, depth + 1)
        else:
          act = menu.addAction(label)
          action_map[act] = command
          i += 1

  _add_items(menu, 0, 0)
  return menu, action_map


def show_popup(section, window, pos, extra_vars=None):
  """Show a popup menu for the given section name.

  *extra_vars* is an optional dict of additional variables (e.g. nick for
  nicklist popups).  Returns True if a popup was shown, False otherwise.
  """
  entries = _popups.get(section)
  if not entries:
    return False

  # Build variable dict
  variables = {}
  client = getattr(window, 'client', None)
  conn = client.conn if client else None
  if client:
    variables['me'] = client.nickname or ''
    variables['network'] = client.network or ''
  if conn:
    variables['server'] = conn.hostname or ''
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
  action = menu.exec(pos)
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
