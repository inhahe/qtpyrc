# toolbar.py - Configurable toolbar system

import os

from PySide6.QtWidgets import (QToolBar, QToolButton, QStyle, QInputDialog,
                                QDialog, QVBoxLayout, QScrollArea, QWidget,
                                QGridLayout, QLabel, QHBoxLayout, QSizePolicy)
from PySide6.QtGui import QIcon, QAction, QFont
from PySide6.QtCore import Qt, QSize, QRect, QPoint
from PySide6.QtWidgets import QLayout

import state
from commands import docommand


# ---------------------------------------------------------------------------
# Flow layout (wrapping toolbar items)
# ---------------------------------------------------------------------------

class _LineBreak(QWidget):
  """Zero-size sentinel widget used to force a new row in _FlowLayout."""
  def __init__(self):
    super().__init__()
    self.setFixedSize(0, 0)
    self.hide()


class _FlowLayout(QLayout):
  """A layout that arranges widgets left-to-right, wrapping to the next row.
  _LineBreak widgets force an immediate new row."""

  def __init__(self, parent=None, spacing=2):
    super().__init__(parent)
    self._items = []
    self._spacing = spacing

  def addItem(self, item):
    self._items.append(item)

  def count(self):
    return len(self._items)

  def itemAt(self, index):
    if 0 <= index < len(self._items):
      return self._items[index]
    return None

  def takeAt(self, index):
    if 0 <= index < len(self._items):
      return self._items.pop(index)
    return None

  def hasHeightForWidth(self):
    return True

  def heightForWidth(self, width):
    return self._do_layout(QRect(0, 0, width, 0), test_only=True)

  def setGeometry(self, rect):
    super().setGeometry(rect)
    self._do_layout(rect)

  def sizeHint(self):
    return self.minimumSize()

  def minimumSize(self):
    s = QSize(0, 0)
    for item in self._items:
      w = item.widget()
      if w and isinstance(w, _LineBreak):
        continue
      s = s.expandedTo(item.minimumSize())
    m = self.contentsMargins()
    return QSize(s.width() + m.left() + m.right(),
                 s.height() + m.top() + m.bottom())

  def _do_layout(self, rect, test_only=False):
    m = self.contentsMargins()
    effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
    x = effective.x()
    y = effective.y()
    row_height = 0

    for item in self._items:
      w = item.widget()
      # Forced line break
      if w and isinstance(w, _LineBreak):
        if row_height > 0:
          x = effective.x()
          y += row_height + self._spacing
          row_height = 0
        continue

      sz = item.sizeHint()
      next_x = x + sz.width() + self._spacing
      if next_x - self._spacing > effective.right() + 1 and row_height > 0:
        x = effective.x()
        y += row_height + self._spacing
        next_x = x + sz.width() + self._spacing
        row_height = 0
      if not test_only:
        item.setGeometry(QRect(QPoint(x, y), sz))
      x = next_x
      row_height = max(row_height, sz.height())

    return y + row_height - rect.y() + m.bottom()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_toolbar(text):
  """Parse a toolbar definition file.

  Format:
    icon | tooltip | command
    -                          (vertical separator)
    ---                        (force new row)
    name = value               (variable definition)
    ; comment

  Variables defined with ``name = value`` can be referenced as ``{name}``
  in icon, tooltip, and command fields.

  Returns a list of tuples:
    ('button', icon_name, tooltip, command)
    ('separator',)
    ('linebreak',)
  """
  from config import _expand_vars
  variables = {}
  entries = []
  for line in text.splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith(';'):
      continue
    if stripped == '---':
      entries.append(('linebreak',))
      continue
    if stripped == '-':
      entries.append(('separator',))
      continue
    # Variable definition: name = value
    if '=' in stripped and '|' not in stripped:
      name, _, value = stripped.partition('=')
      name = name.strip()
      value = value.strip()
      if name:
        variables[name] = value
        continue
    parts = [p.strip() for p in stripped.split('|')]
    if len(parts) >= 3:
      icon_name, tooltip, command = parts[0], parts[1], parts[2]
    elif len(parts) == 2:
      icon_name, tooltip, command = '', parts[0], parts[1]
    else:
      continue
    # Expand {variables} in all fields
    if variables:
      icon_name = _expand_vars(icon_name, variables)
      tooltip = _expand_vars(tooltip, variables)
      command = _expand_vars(command, variables)
    entries.append(('button', icon_name, tooltip, command))
  return entries


def _load_toolbar_file(filepath):
  """Load and parse a toolbar definition file."""
  try:
    with open(filepath, 'r', encoding='utf-8') as f:
      return _parse_toolbar(f.read())
  except FileNotFoundError:
    return []
  except Exception as e:
    state.dbg(state.LOG_ERROR, 'Error loading toolbar file: %s' % e)
    return []


# ---------------------------------------------------------------------------
# Icon resolution
# ---------------------------------------------------------------------------

def _icons_dir():
  """Return the absolute path to the icons/ directory."""
  if state.config:
    return os.path.join(os.path.dirname(os.path.abspath(state.config.path)), 'icons')
  return None

# Map icon names to Qt standard pixmap as fallback
_QT_ICON_MAP = {
  'settings':    QStyle.StandardPixmap.SP_FileDialogDetailedView,
  'editor':      QStyle.StandardPixmap.SP_FileIcon,
  'toggle_view': QStyle.StandardPixmap.SP_TitleBarNormalButton,
  'tile':        QStyle.StandardPixmap.SP_TitleBarUnshadeButton,
  'cascade':     QStyle.StandardPixmap.SP_TitleBarShadeButton,
  'away':        QStyle.StandardPixmap.SP_MediaPause,
  'back':        QStyle.StandardPixmap.SP_MediaPlay,
  'new_server':  QStyle.StandardPixmap.SP_FileDialogNewFolder,
  'connect':     QStyle.StandardPixmap.SP_ArrowRight,
  'disconnect':  QStyle.StandardPixmap.SP_BrowserStop,
  'reload':      QStyle.StandardPixmap.SP_BrowserReload,
  'save':        QStyle.StandardPixmap.SP_DialogSaveButton,
  'open':        QStyle.StandardPixmap.SP_DialogOpenButton,
  'close':       QStyle.StandardPixmap.SP_DialogCloseButton,
  'help':        QStyle.StandardPixmap.SP_DialogHelpButton,
  'info':        QStyle.StandardPixmap.SP_MessageBoxInformation,
  'warning':     QStyle.StandardPixmap.SP_MessageBoxWarning,
  'error':       QStyle.StandardPixmap.SP_MessageBoxCritical,
  'up':          QStyle.StandardPixmap.SP_ArrowUp,
  'down':        QStyle.StandardPixmap.SP_ArrowDown,
  'left':        QStyle.StandardPixmap.SP_ArrowLeft,
  'right':       QStyle.StandardPixmap.SP_ArrowRight,
  'back_arrow':  QStyle.StandardPixmap.SP_ArrowBack,
  'fwd_arrow':   QStyle.StandardPixmap.SP_ArrowForward,
  'computer':    QStyle.StandardPixmap.SP_ComputerIcon,
  'trash':       QStyle.StandardPixmap.SP_TrashIcon,
  'drive':       QStyle.StandardPixmap.SP_DriveHDIcon,
  'desktop':     QStyle.StandardPixmap.SP_DesktopIcon,
}


def _load_svg_icon(path):
  """Load an SVG file, replacing ``currentColor`` with the configured fg color."""
  from PySide6.QtSvg import QSvgRenderer
  from PySide6.QtGui import QPixmap, QPainter
  try:
    with open(path, 'r', encoding='utf-8') as f:
      svg_data = f.read()
  except Exception:
    return QIcon(path)
  fg = state.config.toolbar_fgcolor.name() if state.config else '#ffffff'
  svg_data = svg_data.replace('currentColor', fg)
  renderer = QSvgRenderer(svg_data.encode('utf-8'))
  if not renderer.isValid():
    return QIcon(path)
  icon_size = state.config.toolbar_icon_size if state.config else 20
  pixmap = QPixmap(icon_size, icon_size)
  pixmap.fill(Qt.GlobalColor.transparent)
  painter = QPainter(pixmap)
  renderer.render(painter)
  painter.end()
  return QIcon(pixmap)


def _resolve_icon(icon_name):
  """Resolve an icon name to a QIcon.

  Checks for a custom SVG/PNG in the icons/ directory first,
  then falls back to Qt standard pixmaps.
  For SVGs using ``currentColor``, substitutes the configured foreground color.
  """
  if not icon_name:
    return QIcon()

  # Check for custom icon file in icons/ directory
  idir = _icons_dir()
  if idir:
    for ext in ('.svg', '.png', '.ico'):
      path = os.path.join(idir, icon_name + ext)
      if os.path.isfile(path):
        if ext == '.svg':
          return _load_svg_icon(path)
        return QIcon(path)

  # Fall back to Qt standard pixmap
  sp = _QT_ICON_MAP.get(icon_name)
  if sp is not None:
    return state.app.style().standardIcon(sp)

  return QIcon()


# ---------------------------------------------------------------------------
# Toolbar building
# ---------------------------------------------------------------------------

def _resolve_toolbar_path():
  """Return the absolute path to the toolbar definition file."""
  name = getattr(state.config, 'toolbar_file', '') if state.config else ''
  if not name:
    return None
  if os.path.isabs(name):
    return name
  base = os.path.dirname(os.path.abspath(state.config.path)) if state.config else '.'
  return os.path.join(base, name)


def _exec_toolbar_command(command):
  """Execute a toolbar command string."""
  # Get the active window
  win = None
  ws = state.app.mainwin.workspace
  sub = ws.activeSubWindow()
  if sub:
    win = sub.widget()
  if not win and state.clients:
    win = next(iter(state.clients)).window
  if not win:
    return

  # Expand $?="prompt" variables (reuse popups module)
  from popups import _expand_mirc_vars
  expanded = _expand_mirc_vars(command, {}, win)
  if expanded is None:
    return  # user cancelled

  prefix = state.config.cmdprefix if state.config else '/'
  if expanded.startswith(prefix):
    parts = expanded[len(prefix):].split(None, 1)
    docommand(win, parts[0], parts[1] if len(parts) > 1 else '')
  elif expanded.startswith('/'):
    parts = expanded[1:].split(None, 1)
    docommand(win, parts[0], parts[1] if len(parts) > 1 else '')
  else:
    docommand(win, 'say', expanded)


def _toolbar_font():
  """Return a QFont for toolbar buttons based on config."""
  icon_size = state.config.toolbar_icon_size if state.config else 20
  family = state.config.toolbar_font_family if state.config else None
  size = state.config.toolbar_font_size if state.config else None
  if size is None:
    size = max(8, int(icon_size * 0.55))
  else:
    size = int(size)
  f = QFont()
  if family:
    f.setFamily(family)
  f.setPointSize(size)
  return f


class _FlowToolbarWidget(QWidget):
  """A widget that holds toolbar buttons in a wrapping flow layout."""

  def __init__(self, parent=None, spacing=2):
    super().__init__(parent)
    self._flow = _FlowLayout(self, spacing=spacing)
    self._flow.setContentsMargins(2, 2, 2, 2)
    self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

  def add_button(self, btn):
    self._flow.addWidget(btn)

  def add_separator(self, icon_size):
    from PySide6.QtWidgets import QFrame
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Plain)
    sep.setFixedSize(2, icon_size)
    color = state.config.toolbar_separator_color.name() if state.config else '#666666'
    sep.setStyleSheet("QFrame { color: %s; }" % color)
    self._flow.addWidget(sep)

  def add_linebreak(self):
    self._flow.addWidget(_LineBreak())

  def hasHeightForWidth(self):
    return True

  def heightForWidth(self, width):
    return self._flow.heightForWidth(width)

  def sizeHint(self):
    # Use the flow layout's height for our current width
    w = self.width() if self.width() > 0 else 200
    h = self._flow.heightForWidth(w)
    return QSize(w, h)

  def resizeEvent(self, event):
    super().resizeEvent(event)
    self.updateGeometry()
    # Update parent toolbar's fixed height to match content
    toolbar = self.parent()
    if toolbar and hasattr(toolbar, 'setFixedHeight'):
      h = self._flow.heightForWidth(event.size().width())
      toolbar.setFixedHeight(h + 4)


def build_toolbar(parent):
  """Build and return a QToolBar from the toolbar definition file."""
  toolbar = QToolBar("Main Toolbar", parent)
  icon_size = state.config.toolbar_icon_size if state.config else 20
  toolbar.setIconSize(QSize(icon_size, icon_size))
  toolbar.setMovable(False)

  filepath = _resolve_toolbar_path()
  entries = _load_toolbar_file(filepath) if filepath else []

  if not entries:
    toolbar.hide()
    return toolbar

  font = _toolbar_font()
  icon_sz = QSize(icon_size, icon_size)
  flow_widget = _FlowToolbarWidget(spacing=2)

  for entry in entries:
    if entry[0] == 'linebreak':
      flow_widget.add_linebreak()
    elif entry[0] == 'separator':
      flow_widget.add_separator(icon_size)
    else:
      _, icon_name, tooltip, command = entry
      icon = _resolve_icon(icon_name)
      has_icon = not icon.isNull()
      btn = QToolButton()
      btn.setFont(font)
      btn.setIconSize(icon_sz)
      btn.setToolTip(tooltip)
      btn.setAutoRaise(True)
      if has_icon:
        btn.setIcon(icon)
      else:
        btn.setText(tooltip)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
      btn.clicked.connect(lambda checked, cmd=command: _exec_toolbar_command(cmd))
      flow_widget.add_button(btn)

  toolbar.addWidget(flow_widget)
  # Let Qt settle the toolbar width, then fix the height to match content
  from PySide6.QtCore import QTimer
  def _fix_height():
    w = flow_widget.width() if flow_widget.width() > 0 else toolbar.width()
    if w > 0:
      h = flow_widget.heightForWidth(w)
      toolbar.setFixedHeight(h + 4)  # small padding
  QTimer.singleShot(0, _fix_height)
  toolbar._flow_widget = flow_widget  # keep ref for resize updates
  return toolbar


def reload_toolbar():
  """Reload the toolbar from the definition file."""
  mainwin = state.app.mainwin
  if hasattr(mainwin, '_toolbar') and mainwin._toolbar:
    mainwin.removeToolBar(mainwin._toolbar)
    mainwin._toolbar.deleteLater()
  if state.config.show_toolbar:
    mainwin._toolbar = build_toolbar(mainwin)
    mainwin.addToolBar(mainwin._toolbar)
  else:
    mainwin._toolbar = None


# ---------------------------------------------------------------------------
# Icon browser dialog
# ---------------------------------------------------------------------------

def show_icon_browser():
  """Open a non-modal dialog showing all available icons."""
  dlg = QDialog(state.app.mainwin)
  dlg.setWindowTitle("Icon Browser — use these names in toolbar.ini")
  dlg.resize(750, 550)

  layout = QVBoxLayout(dlg)

  # Info label
  info = QLabel("Icons from the icons%s directory.  Use the name (without extension) "
                "in toolbar.ini's icon field." % os.sep)
  info.setWordWrap(True)
  layout.addWidget(info)

  scroll = QScrollArea()
  scroll.setWidgetResizable(True)
  container = QWidget()
  grid = QGridLayout(container)
  grid.setSpacing(12)

  display_size = max(32, state.config.toolbar_icon_size if state.config else 32)
  cols = 6

  # Collect custom icons from icons/ directory
  custom_icons = []
  idir = _icons_dir()
  if idir and os.path.isdir(idir):
    for fname in sorted(os.listdir(idir)):
      name, ext = os.path.splitext(fname)
      if ext.lower() in ('.svg', '.png', '.ico'):
        custom_icons.append((name, os.path.join(idir, fname)))

  # Section header for custom icons
  idx = 0
  if custom_icons:
    hdr = QLabel("<b>Custom Icons (icons%s directory)</b>" % os.sep)
    grid.addWidget(hdr, 0, 0, 1, cols)
    idx = 1
    for i, (name, path) in enumerate(custom_icons):
      row, col = divmod(i, cols)
      row += idx
      cell = QWidget()
      cl = QVBoxLayout(cell)
      cl.setContentsMargins(4, 4, 4, 4)
      icon_label = QLabel()
      icon = _load_svg_icon(path) if path.lower().endswith('.svg') else QIcon(path)
      icon_label.setPixmap(icon.pixmap(QSize(display_size, display_size)))
      icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
      text_label = QLabel(name)
      text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
      text_label.setWordWrap(True)
      f = text_label.font()
      f.setPointSize(8)
      text_label.setFont(f)
      cl.addWidget(icon_label)
      cl.addWidget(text_label)
      grid.addWidget(cell, row, col)
    idx += (len(custom_icons) - 1) // cols + 2  # next row after icons + gap

  # Section header for Qt built-in icons
  qt_names = sorted(n for n in _QT_ICON_MAP
                    if not any(c[0] == n for c in custom_icons))
  if qt_names:
    hdr2 = QLabel("<b>Qt Built-in Icons (fallback)</b>")
    grid.addWidget(hdr2, idx, 0, 1, cols)
    idx += 1
    for i, name in enumerate(qt_names):
      row, col = divmod(i, cols)
      row += idx
      cell = QWidget()
      cl = QVBoxLayout(cell)
      cl.setContentsMargins(4, 4, 4, 4)
      icon_label = QLabel()
      sp = _QT_ICON_MAP[name]
      icon_label.setPixmap(state.app.style().standardIcon(sp).pixmap(
        QSize(display_size, display_size)))
      icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
      text_label = QLabel(name)
      text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
      text_label.setWordWrap(True)
      f = text_label.font()
      f.setPointSize(8)
      text_label.setFont(f)
      cl.addWidget(icon_label)
      cl.addWidget(text_label)
      grid.addWidget(cell, row, col)

  scroll.setWidget(container)
  layout.addWidget(scroll)

  # Non-modal so user can keep it open while editing toolbar.ini
  dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
  dlg.show()
