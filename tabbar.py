# tabbar.py - mIRC-style multi-row tab bar with QStackedWidget

from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *

import state


class TabLabel(QLabel):
  """A single clickable tab label."""
  clicked = Signal()
  rightClicked = Signal(QPoint)

  def __init__(self, text, parent=None):
    super().__init__(text, parent)
    self.setCursor(Qt.CursorShape.PointingHandCursor)
    self.setAlignment(Qt.AlignmentFlag.AlignCenter)

  def mousePressEvent(self, event):
    if event.button() == Qt.MouseButton.LeftButton:
      self.clicked.emit()
    elif event.button() == Qt.MouseButton.RightButton:
      self.rightClicked.emit(event.globalPosition().toPoint())
    super().mousePressEvent(event)


class SubWindowProxy:
  """Lightweight stand-in for QMdiSubWindow so code using self.subwindow still works."""

  def __init__(self, widget):
    self._widget = widget

  def widget(self):
    return self._widget

  def windowTitle(self):
    return self._widget.windowTitle()

  def setFocus(self):
    self._widget.setFocus()

  # These are no-ops since the stacked widget handles visibility
  def show(self): pass
  def showNormal(self): pass
  def showMaximized(self): pass
  def hide(self): pass
  def setGeometry(self, *a): pass
  def setWindowFlags(self, *a): pass


class TabbedWorkspace(QWidget):
  """
  Replaces QMdiArea for tabbed mode.  Contains a MultiRowTabBar on top
  and a QStackedWidget below.  Exposes the subset of QMdiArea API that
  the rest of the codebase uses.
  """

  # Emitted when a different window becomes active (mirrors QMdiArea signal)
  subWindowActivated = Signal(object)

  ACTIVE = 0
  NORMAL = 1
  SKIPPED = 2

  def __init__(self, parent=None):
    super().__init__(parent)
    self._tabs = []        # list of {proxy, widget, label, state, activity_color}
    self._active = None    # active entry
    self._max_rows = 0     # 0 = dynamic
    self._activating = False
    self._relayout_timer = QTimer(self)
    self._relayout_timer.setSingleShot(True)
    self._relayout_timer.setInterval(0)
    self._relayout_timer.timeout.connect(self._do_relayout)

    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # Tab bar area — override minimum size so it doesn't force a wide window
    class _TabBarWidget(QWidget):
      def minimumSizeHint(self):
        return QSize(100, 0)
    self._tabbar_widget = _TabBarWidget(self)
    self._tabbar_layout = QVBoxLayout(self._tabbar_widget)
    self._tabbar_layout.setContentsMargins(0, 0, 0, 0)
    self._tabbar_layout.setSpacing(0)
    layout.addWidget(self._tabbar_widget)

    # Separator line between tabs and content
    self._separator = QFrame(self)
    self._separator.setFrameShape(QFrame.Shape.HLine)
    self._separator.setFixedHeight(1)
    layout.addWidget(self._separator)

    # Stacked widget for content
    self._stack = QStackedWidget(self)
    self._blank = QWidget(self)
    self._stack.addWidget(self._blank)
    layout.addWidget(self._stack, 1)

    self._load_colors()

  def _load_colors(self):
    cfg = state.config
    # Normal tab colors (default: same as global fg/bg)
    self._fg = cfg.tab_normal_fg or cfg.fgcolor
    self._bg = cfg.tab_normal_bg or cfg.bgcolor
    # Active tab colors (default: inverted)
    self._active_fg = cfg.tab_active_fg or cfg.bgcolor
    self._active_bg = cfg.tab_active_bg or cfg.fgcolor
    # Skipped tab colors (default: gray text, light gray background)
    self._skipped_fg = cfg.tab_skipped_fg or QColor(Qt.gray)
    self._skipped_bg = cfg.tab_skipped_bg or QColor(Qt.lightGray)
    self._sep_color = cfg.fgcolor
    if hasattr(self, '_separator'):
      self._separator.setStyleSheet("background-color: %s;" % self._sep_color.name())
    if cfg.tab_bar_bg:
      self._bar_bg = cfg.tab_bar_bg
    elif cfg.new_tab_state == 'skipped':
      self._bar_bg = self._skipped_bg
    else:
      self._bar_bg = self._bg
    if hasattr(self, '_tabbar_widget'):
      self._tabbar_widget.setStyleSheet("background-color: %s;" % self._bar_bg.name())
    if hasattr(self, '_blank'):
      self._blank.setStyleSheet("background-color: %s;" % self._bar_bg.name())

  def set_max_rows(self, n):
    self._max_rows = n
    self._relayout()

  def set_tabs_visible(self, visible):
    self._tabbar_widget.setVisible(visible)
    self._separator.setVisible(visible)

  # --- QMdiArea-compatible API ---

  def addSubWindow(self, widget):
    """Add a widget; returns a SubWindowProxy (stands in for QMdiSubWindow)."""
    proxy = SubWindowProxy(widget)
    self._stack.addWidget(widget)
    title = widget.windowTitle()
    label = TabLabel('  ' + title + '  ', self)
    nts = state.config.new_tab_state
    if nts == 'active':
      initial_state = self.NORMAL  # will be set to ACTIVE by _activate below
    elif nts == 'skipped':
      initial_state = self.SKIPPED
    else:
      initial_state = self.NORMAL
    entry = {'proxy': proxy, 'widget': widget, 'label': label,
             'state': initial_state, 'activity_color': None,
             'title': title, 'disconnected': False}
    self._tabs.append(entry)
    label.clicked.connect(lambda e=entry: self._on_tab_clicked(e))
    label.rightClicked.connect(lambda pos, e=entry: self._on_tab_right_clicked(e, pos))
    self._group_tab(entry)
    self._relayout()
    self._style_tab(entry)
    if nts == 'active':
      self._activate(entry)
    return proxy

  def removeSubWindow(self, proxy):
    """Remove a window by its proxy."""
    for i, t in enumerate(self._tabs):
      if t['proxy'] is proxy:
        was_active = (t is self._active)
        t['label'].deleteLater()
        self._stack.removeWidget(t['widget'])
        self._tabs.pop(i)
        if was_active:
          if self._tabs:
            # Try to find a non-skipped tab nearby
            candidate = None
            idx = min(i, len(self._tabs) - 1)
            for offset in range(len(self._tabs)):
              t = self._tabs[(idx + offset) % len(self._tabs)]
              if t['state'] != self.SKIPPED:
                candidate = t
                break
            if candidate:
              self._activate(candidate)
            else:
              self._active = None
              self._stack.setCurrentWidget(self._blank)
          else:
            self._active = None
            self._stack.setCurrentWidget(self._blank)
        self._relayout()
        return

  def setActiveSubWindow(self, proxy):
    """Activate the window for *proxy*."""
    if self._activating:
      return
    for t in self._tabs:
      if t['proxy'] is proxy and t is not self._active:
        t['state'] = self.NORMAL
        self._activate(t)
        return

  def activeSubWindow(self):
    """Return the active SubWindowProxy, or None."""
    return self._active['proxy'] if self._active else None

  def subWindowList(self):
    """Return list of SubWindowProxy objects in tab order."""
    return [t['proxy'] for t in self._tabs]

  def findChild(self, typ):
    """Compatibility: return None for QTabBar (we don't use Qt's tab bar)."""
    if typ is QTabBar:
      return None
    return super().findChild(typ)

  # MDI-mode stubs (not used in tabbed mode, but called by toggle)
  def setViewMode(self, mode): pass
  def viewMode(self): return 0
  def cascadeSubWindows(self): pass
  def setTabPosition(self, *a): pass
  def setDocumentMode(self, *a): pass
  def setTabsClosable(self, *a): pass
  def setTabsMovable(self, *a): pass

  # --- Tab bar: update title / activity ---

  def update_tab_title(self, proxy, title):
    for t in self._tabs:
      if t['proxy'] is proxy:
        if t['title'] == title:
          return  # no change
        t['title'] = title
        self._style_tab(t)
        self._relayout()
        break

  def set_disconnected(self, proxy, disconnected):
    for t in self._tabs:
      if t['proxy'] is proxy:
        t['disconnected'] = disconnected
        self._style_tab(t)
        break

  def set_activity_color(self, proxy, color):
    for t in self._tabs:
      if t['proxy'] is proxy:
        t['activity_color'] = color
        self._style_tab(t)
        break

  def clear_activity_color(self, proxy):
    for t in self._tabs:
      if t['proxy'] is proxy:
        t['activity_color'] = None
        self._style_tab(t)
        break

  # --- Tab cycling ---

  def skip_current(self):
    """Skip the active tab (as if clicked) and activate the next, or deactivate."""
    if not self._active:
      return
    entry = self._active
    entry['state'] = self.SKIPPED
    self._style_tab(entry)
    has_other = any(t['state'] != self.SKIPPED for t in self._tabs if t is not entry)
    if has_other:
      self._activate_next(entry)
    else:
      self._deactivate()

  def cycle_tab(self, forward=True):
    if not self._tabs or not self._active:
      return
    cur_idx = self._tabs.index(self._active)
    n = len(self._tabs)
    step = 1 if forward else -1
    for offset in range(1, n):
      candidate = self._tabs[(cur_idx + offset * step) % n]
      if candidate['state'] != self.SKIPPED:
        self._activate(candidate)
        return

  # --- Internal ---

  def _on_tab_right_clicked(self, entry, pos):
    """Show a context menu for a tab."""
    import popups
    widget = entry['widget']
    popups.show_popup('tab', widget, pos)

  def _on_tab_clicked(self, entry):
    if entry is self._active:
      entry['state'] = self.SKIPPED
      self._style_tab(entry)
      # Try to activate another non-skipped tab; if none, deactivate all
      has_other = any(t['state'] != self.SKIPPED for t in self._tabs if t is not entry)
      if has_other:
        self._activate_next(entry)
      else:
        self._deactivate()
    else:
      entry['state'] = self.NORMAL
      self._activate(entry)

  def _activate(self, entry):
    self._activating = True
    try:
      if self._active and self._active is not entry:
        # Only reset to NORMAL if it's still ACTIVE (don't overwrite SKIPPED)
        if self._active['state'] == self.ACTIVE:
          self._active['state'] = self.NORMAL
          self._style_tab(self._active)
        elif self._active['state'] == self.SKIPPED:
          self._style_tab(self._active)  # just restyle, keep SKIPPED
      entry['state'] = self.ACTIVE
      self._active = entry
      self._style_tab(entry)
      self._stack.setCurrentWidget(entry['widget'])
      self.subWindowActivated.emit(entry['proxy'])
    finally:
      self._activating = False

  def _deactivate(self):
    """Deactivate all tabs, showing a blank screen."""
    if self._active:
      if self._active['state'] == self.ACTIVE:
        self._active['state'] = self.NORMAL
        self._style_tab(self._active)
      self._active = None
    self._stack.setCurrentWidget(self._blank)
    self.subWindowActivated.emit(None)

  def _activate_next(self, skip_entry):
    if not self._tabs:
      return
    idx = self._tabs.index(skip_entry)
    n = len(self._tabs)
    for offset in range(1, n):
      candidate = self._tabs[(idx + offset) % n]
      if candidate['state'] != self.SKIPPED:
        self._activate(candidate)
        return

  def _group_tab(self, entry):
    widget = entry['widget']
    if not hasattr(widget, 'client'):
      return
    client = widget.client
    idx = self._tabs.index(entry)
    last_sibling = -1
    for i, t in enumerate(self._tabs):
      if t is entry:
        continue
      if getattr(t['widget'], 'client', None) is client:
        last_sibling = i
    if last_sibling >= 0 and idx > last_sibling + 1:
      self._tabs.pop(idx)
      self._tabs.insert(last_sibling + 1, entry)

  # --- Styling ---

  def _style_tab(self, entry):
    label = entry['label']
    cfg = state.config

    if entry['activity_color'] and entry['state'] != self.ACTIVE:
      fg = entry['activity_color'].name()
    elif entry['state'] == self.ACTIVE:
      fg = self._active_fg.name()
    elif entry['state'] == self.SKIPPED:
      fg = self._skipped_fg.name()
    else:
      fg = self._fg.name()

    if entry['state'] == self.ACTIVE:
      bg = self._active_bg.name()
    elif entry['state'] == self.SKIPPED:
      bg = self._skipped_bg.name()
    else:
      bg = self._bg.name()

    font_parts = []
    if cfg.tab_font_family:
      font_parts.append("font-family: '%s';" % cfg.tab_font_family)
    if cfg.tab_font_size:
      font_parts.append("font-size: %dpt;" % cfg.tab_font_size)
    font_css = ' '.join(font_parts)
    label.setStyleSheet(
      "QLabel { background-color: %s; padding: 3px 6px; %s }" % (bg, font_css))
    # Build rich text label with optional red X for disconnected tabs
    title = entry.get('title', '')
    disconnected = entry.get('disconnected', False)
    html = '<span style="color:%s;">  %s  </span>' % (fg, title)
    if disconnected:
      html += '<span style="color:red;"> ✕</span>'
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(html)

  # --- Row layout ---

  def _relayout(self):
    """Schedule a relayout on the next event loop iteration.
    Multiple calls before the event loop runs are collapsed into one."""
    if not self._relayout_timer.isActive():
      self._relayout_timer.start()

  def _do_relayout(self):
    # Clear existing rows (but keep tab labels alive)
    while self._tabbar_layout.count():
      item = self._tabbar_layout.takeAt(0)
      w = item.widget()
      if w and w not in [t['label'] for t in self._tabs]:
        w.deleteLater()

    if not self._tabs:
      return

    # Group tabs by client
    groups = []
    current_group = []
    prev_client = None
    for t in self._tabs:
      client = getattr(t['widget'], 'client', None)
      if client is not prev_client and current_group:
        groups.append(current_group)
        current_group = []
      current_group.append(t)
      prev_client = client
    if current_group:
      groups.append(current_group)

    # Build flat list with separator markers
    items = []
    for gi, group in enumerate(groups):
      if gi > 0:
        items.append(None)
      for t in group:
        items.append(t)

    sep_width = 6
    available_width = self._tabbar_widget.width() if self._tabbar_widget.width() > 100 else self.width()
    if available_width < 100:
      available_width = 800

    item_widths = []
    for item in items:
      if item is None:
        item_widths.append(sep_width)
      else:
        w = item['label'].sizeHint().width()
        item_widths.append(max(w, 40))

    # Distribute into rows
    rows = [[]]
    row_width = 0
    max_rows = self._max_rows if self._max_rows > 0 else 9999

    for i, item in enumerate(items):
      w = item_widths[i]
      if row_width + w > available_width and rows[-1] and len(rows) < max_rows:
        rows.append([])
        row_width = 0
      rows[-1].append(item)
      row_width += w

    # Build row widgets
    for row in rows:
      row_widget = QWidget(self._tabbar_widget)
      row_widget.setMinimumWidth(0)
      row_layout = QHBoxLayout(row_widget)
      row_layout.setContentsMargins(0, 0, 0, 0)
      row_layout.setSpacing(0)
      row_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

      for item in row:
        if item is None:
          sep = QFrame(row_widget)
          sep.setFrameShape(QFrame.Shape.VLine)
          sep.setStyleSheet("color: %s;" % self._sep_color.name())
          sep.setFixedWidth(2)
          row_layout.addWidget(sep)
        else:
          item['label'].setParent(row_widget)
          row_layout.addWidget(item['label'])

      row_layout.addStretch(1)
      self._tabbar_layout.addWidget(row_widget)

  def resizeEvent(self, event):
    super().resizeEvent(event)
    self._relayout()
