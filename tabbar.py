# tabbar.py - Unified workspace: multi-row tab bar with QStackedWidget + QMdiArea
#
# Normal mode: QStackedWidget (one window visible at a time, like the old tabbed mode).
# Tiled/cascaded mode: QMdiArea (free-floating windows for drag/resize).
# The tab bar is always available for switching windows in either mode.

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
      self.rightClicked.emit(QCursor.pos())
    super().mousePressEvent(event)


class _SubWindowProxy:
  """Lightweight stand-in for QMdiSubWindow so code using self.subwindow still works."""
  def __init__(self, widget):
    self._widget = widget
    self._mdi_sub = None  # set when in MDI mode

  def widget(self):
    return self._widget

  def windowTitle(self):
    return self._widget.windowTitle()

  def setFocus(self):
    self._widget.setFocus()

  def show(self): pass
  def showNormal(self): pass
  def showMaximized(self): pass
  def hide(self): pass
  def setGeometry(self, *a):
    if self._mdi_sub:
      self._mdi_sub.setGeometry(*a)
  def setWindowFlags(self, *a): pass


class TabbedWorkspace(QWidget):
  """
  Unified workspace: multi-row tab bar on top, with QStackedWidget for
  normal (maximized) mode and QMdiArea for tiled/cascaded mode.
  """

  subWindowActivated = Signal(object)

  ACTIVE = 0
  NORMAL = 1
  SKIPPED = 2

  def __init__(self, parent=None):
    super().__init__(parent)
    self._tabs = []
    self._active = None
    self._max_rows = 0
    self._activating = False
    self._tiled = False       # True when in MDI tiled/cascaded mode
    self._relayout_timer = QTimer(self)
    self._relayout_timer.setSingleShot(True)
    self._relayout_timer.timeout.connect(self._do_relayout)
    self._relayout_delay_ms = 200

    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # Tab bar area
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

    # QStackedWidget for normal (maximized/tabbed) mode
    self._stack = QStackedWidget(self)
    self._blank = QWidget(self)
    self._stack.addWidget(self._blank)
    layout.addWidget(self._stack, 1)

    # QMdiArea for tiled/cascaded mode (hidden initially)
    self._mdi = QMdiArea(self)
    self._mdi.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    self._mdi.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    self._mdi.subWindowActivated.connect(self._on_mdi_activated)
    self._mdi.hide()
    layout.addWidget(self._mdi, 1)

    self._load_colors()

  def _load_colors(self):
    cfg = state.config
    self._fg = cfg.tab_normal_fg or cfg.fgcolor
    self._bg = cfg.tab_normal_bg or cfg.bgcolor
    self._active_fg = cfg.tab_active_fg or cfg.bgcolor
    self._active_bg = cfg.tab_active_bg or cfg.fgcolor
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
    """Add a widget. Returns a _SubWindowProxy."""
    proxy = _SubWindowProxy(widget)
    self._stack.addWidget(widget)

    title = widget.windowTitle()
    label = TabLabel('  ' + title + '  ', self)
    nts = state.config.new_tab_state
    if nts == 'active':
      initial_state = self.NORMAL
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
        # Remove from whichever container it's in
        if self._tiled and t['proxy']._mdi_sub:
          self._mdi.removeSubWindow(t['widget'])
          t['proxy']._mdi_sub = None
        else:
          self._stack.removeWidget(t['widget'])
        self._tabs.pop(i)
        if was_active:
          if self._tabs:
            candidate = None
            idx = min(i, len(self._tabs) - 1)
            for offset in range(len(self._tabs)):
              t2 = self._tabs[(idx + offset) % len(self._tabs)]
              if t2['state'] != self.SKIPPED:
                candidate = t2
                break
            if candidate:
              self._activate(candidate)
            else:
              self._active = None
              if not self._tiled:
                self._stack.setCurrentWidget(self._blank)
          else:
            self._active = None
            if not self._tiled:
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
    """Return the active proxy, or None."""
    return self._active['proxy'] if self._active else None

  def subWindowList(self):
    """Return list of proxies in tab order."""
    return [t['proxy'] for t in self._tabs]

  def viewport(self):
    """Return the MDI viewport (used by tile helpers)."""
    return self._mdi.viewport()

  def findChild(self, typ):
    if typ is QTabBar:
      return None
    return super().findChild(typ)

  # --- MDI layout commands ---

  def _enter_mdi(self):
    """Switch all windows from QStackedWidget to QMdiArea."""
    if self._tiled:
      return
    self._tiled = True
    # Suppress MDI activation signals during bulk reparent
    self._activating = True
    try:
      for t in self._tabs:
        self._stack.removeWidget(t['widget'])
        sub = self._mdi.addSubWindow(t['widget'])
        t['proxy']._mdi_sub = sub
        t['widget'].show()
        sub.show()
      self._stack.hide()
      self._mdi.show()
      # Activate the correct subwindow
      if self._active and self._active['proxy']._mdi_sub:
        self._mdi.setActiveSubWindow(self._active['proxy']._mdi_sub)
    finally:
      self._activating = False

  def _exit_mdi(self):
    """Switch all windows from QMdiArea back to QStackedWidget."""
    if not self._tiled:
      return
    self._tiled = False
    self._activating = True
    try:
      for t in self._tabs:
        if t['proxy']._mdi_sub:
          self._mdi.removeSubWindow(t['widget'])
          t['proxy']._mdi_sub = None
        t['widget'].setParent(None)  # detach fully before re-adding
        self._stack.addWidget(t['widget'])
      self._mdi.hide()
      self._stack.show()
      # Restore the active window in the stack
      if self._active:
        self._stack.setCurrentWidget(self._active['widget'])
      else:
        self._stack.setCurrentWidget(self._blank)
    finally:
      self._activating = False

  def tileSubWindows(self):
    """Tile all subwindows side by side."""
    self._enter_mdi()
    self._mdi.tileSubWindows()

  def cascadeSubWindows(self):
    """Cascade all subwindows."""
    self._enter_mdi()
    self._mdi.cascadeSubWindows()

  def tileVertically(self):
    """Tile all subwindows in stacked rows."""
    self._enter_mdi()
    subs = self._mdi.subWindowList()
    if not subs:
      return
    vp = self._mdi.viewport()
    w = vp.width()
    h = vp.height()
    n = len(subs)
    row_h = h // n if n else h
    for i, sub in enumerate(subs):
      sub.setGeometry(0, i * row_h, w, row_h)

  def maximizeActive(self):
    """Return to tabbed look (exit MDI mode)."""
    self._exit_mdi()

  # --- Tab bar: update title / activity ---

  def update_tab_title(self, proxy, title):
    for t in self._tabs:
      if t['proxy'] is proxy:
        if t['title'] == title:
          return
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

  def _on_mdi_activated(self, sub):
    """Sync tab bar when MDI activates a subwindow."""
    if self._activating or sub is None:
      return
    widget = sub.widget()
    for t in self._tabs:
      if t['widget'] is widget and t is not self._active:
        t['state'] = self.NORMAL
        self._activate(t)
        return

  def _on_tab_right_clicked(self, entry, pos):
    import popups
    widget = entry['widget']
    # Highlight the tab background while context menu is shown
    entry['_ctx_highlight'] = True
    self._style_tab(entry)
    popups.show_popup('tab', widget, pos)
    entry.pop('_ctx_highlight', None)
    self._style_tab(entry)

  def _on_tab_clicked(self, entry):
    if entry is self._active:
      entry['state'] = self.SKIPPED
      self._style_tab(entry)
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
        if self._active['state'] == self.ACTIVE:
          self._active['state'] = self.NORMAL
          self._style_tab(self._active)
        elif self._active['state'] == self.SKIPPED:
          self._style_tab(self._active)
      entry['state'] = self.ACTIVE
      self._active = entry
      self._style_tab(entry)
      if self._tiled:
        if entry['proxy']._mdi_sub:
          self._mdi.setActiveSubWindow(entry['proxy']._mdi_sub)
      else:
        self._stack.setCurrentWidget(entry['widget'])
      self.subWindowActivated.emit(entry['proxy'])
    finally:
      self._activating = False

  def _deactivate(self):
    if self._active:
      if self._active['state'] == self.ACTIVE:
        self._active['state'] = self.NORMAL
        self._style_tab(self._active)
      self._active = None
    if not self._tiled:
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

    if entry.get('_ctx_highlight'):
      bg = '#4488cc'
      fg = 'white'
    elif entry['state'] == self.ACTIVE:
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
    title = entry.get('title', '')
    disconnected = entry.get('disconnected', False)
    html = '<span style="color:%s;">  %s  </span>' % (fg, title)
    if disconnected:
      html += '<span style="color:red;"> \u2715</span>'
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(html)

  # --- Row layout ---

  def _relayout(self):
    self._relayout_timer.setInterval(self._relayout_delay_ms)
    self._relayout_timer.start()

  def _do_relayout(self):
    while self._tabbar_layout.count():
      item = self._tabbar_layout.takeAt(0)
      w = item.widget()
      if w and w not in [t['label'] for t in self._tabs]:
        w.deleteLater()

    if not self._tabs:
      return

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
