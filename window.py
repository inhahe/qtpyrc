# window.py - GUI window classes

from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *

import re
from datetime import datetime
from functools import partial

import state
from config import _format_timestamp, _parse_color
from models import mircre, irccolors, perceivedbrightness


# ---------------------------------------------------------------------------
# GUI: Network tree view
# ---------------------------------------------------------------------------

class NetworkTree(QTreeWidget):
  """Left-side tree showing networks > server / channels / queries."""

  _ROLE_WINDOW = Qt.ItemDataRole.UserRole  # stores the Window reference

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setHeaderHidden(True)
    self.setIndentation(14)
    self.setRootIsDecorated(True)
    self.setAnimated(False)
    self.setExpandsOnDoubleClick(False)
    self._updating = False  # guard against re-entrance during sync
    self.itemClicked.connect(self._on_item_clicked)

  # --- public API ---

  def add_client(self, client):
    """Add a top-level network node for *client*."""
    node = QTreeWidgetItem(self)
    node.setText(0, self._client_label(client))
    node.setData(0, self._ROLE_WINDOW, client.window)
    node.setExpanded(True)
    client._tree_node = node
    # Add server window as first child
    srv = QTreeWidgetItem(node)
    srv.setText(0, "(server)")
    srv.setData(0, self._ROLE_WINDOW, client.window)

  def remove_client(self, client):
    node = getattr(client, '_tree_node', None)
    if node:
      idx = self.indexOfTopLevelItem(node)
      if idx >= 0:
        self.takeTopLevelItem(idx)
      client._tree_node = None

  def add_channel(self, client, channel):
    """Add a channel child node under *client*'s network node."""
    node = getattr(client, '_tree_node', None)
    if not node:
      return
    item = QTreeWidgetItem()
    item.setText(0, channel.name)
    item.setData(0, self._ROLE_WINDOW, channel.window)
    # Insert after server node but before queries, sorted among channels
    self._insert_child(node, item, section='channel')
    channel._tree_item = item

  def remove_channel(self, client, channel):
    item = getattr(channel, '_tree_item', None)
    node = getattr(client, '_tree_node', None)
    if item and node:
      node.removeChild(item)
      channel._tree_item = None

  def add_query(self, client, query):
    node = getattr(client, '_tree_node', None)
    if not node:
      return
    item = QTreeWidgetItem()
    item.setText(0, query.nick)
    item.setData(0, self._ROLE_WINDOW, query.window)
    self._insert_child(node, item, section='query')
    query._tree_item = item

  def remove_query(self, client, query):
    item = getattr(query, '_tree_item', None)
    node = getattr(client, '_tree_node', None)
    if item and node:
      node.removeChild(item)
      query._tree_item = None

  def update_client_label(self, client):
    node = getattr(client, '_tree_node', None)
    if node:
      node.setText(0, self._client_label(client))

  def sync_to_window(self, window):
    """Highlight the tree item that corresponds to *window*."""
    if self._updating:
      return
    item = self._find_item_for_window(window)
    if item:
      self._updating = True
      self.setCurrentItem(item)
      self._updating = False

  # --- internals ---

  def _client_label(self, client):
    if client.network:
      return client.network
    if client.network_key:
      return client.network_key
    return "(not connected)"

  def _insert_child(self, node, item, section):
    """Insert *item* into *node* in order: server(0), channels, queries."""
    # Channels start at index 1 (after server node), queries after channels.
    # We sort alphabetically within each section.
    start = 1
    count = node.childCount()
    text = item.text(0).lower()
    if section == 'channel':
      # Insert among channels (items starting at 1 whose text starts with # or &)
      for i in range(start, count):
        child = node.child(i)
        ct = child.text(0).lower()
        if not ct.startswith(('#', '&', '!', '+')):
          # Past channel section — queries start here
          node.insertChild(i, item)
          return
        if ct > text:
          node.insertChild(i, item)
          return
      node.addChild(item)
    else:
      # Insert among queries (after channels), sorted
      for i in range(start, count):
        child = node.child(i)
        ct = child.text(0).lower()
        if not ct.startswith(('#', '&', '!', '+')):
          # In the query section — find sorted position
          if ct > text:
            node.insertChild(i, item)
            return
      node.addChild(item)

  def _find_item_for_window(self, window):
    for ti in range(self.topLevelItemCount()):
      top = self.topLevelItem(ti)
      if top.data(0, self._ROLE_WINDOW) is window:
        return top
      for ci in range(top.childCount()):
        child = top.child(ci)
        if child.data(0, self._ROLE_WINDOW) is window:
          return child
    return None

  def _on_item_clicked(self, item, column):
    if self._updating:
      return
    window = item.data(0, self._ROLE_WINDOW)
    if window and hasattr(window, 'subwindow'):
      self._updating = True
      state.app.mainwin.workspace.setActiveSubWindow(window.subwindow)
      self._updating = False

  def contextMenuEvent(self, event):
    item = self.itemAt(event.pos())
    if item:
      window = item.data(0, self._ROLE_WINDOW)
      if window:
        import popups
        popups.show_popup('tab', window, event.globalPos())


# ---------------------------------------------------------------------------
# GUI: Window classes
# ---------------------------------------------------------------------------

class ChatOutput(QTextEdit):
  """QTextEdit subclass that supports right-clicking on nick anchors and clickable URLs."""
  def __init__(self, parent_window):
    super().__init__(parent_window)
    self._parent_window = parent_window
    self.setMouseTracking(True)

  def mouseMoveEvent(self, event):
    anchor = self.anchorAt(event.pos())
    if anchor and anchor.startswith('http'):
      self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
    else:
      self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
    super().mouseMoveEvent(event)

  def mouseReleaseEvent(self, event):
    if event.button() == Qt.MouseButton.LeftButton:
      anchor = self.anchorAt(event.pos())
      if anchor and anchor.startswith('http'):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl(anchor))
        return
    super().mouseReleaseEvent(event)
    # Auto-copy on select release (mIRC-style)
    if (event.button() == Qt.MouseButton.LeftButton
        and state.config.auto_copy_selection
        and self.textCursor().hasSelection()):
      self.copy()
      c = self.textCursor()
      c.clearSelection()
      self.setTextCursor(c)

  def contextMenuEvent(self, event):
    import popups
    has_selection = self.textCursor().hasSelection()
    anchor = self.anchorAt(event.pos())
    if anchor and anchor.startswith("nick:"):
      nick = anchor[5:]
      popups.show_popup('nicklist', self._parent_window, event.globalPos(),
                        extra_vars={'nick': nick, '1': nick},
                        copy_action=has_selection)
    else:
      # Try window-type-specific popup
      wtype = getattr(self._parent_window, 'type', '')
      section = {'channel': 'channel', 'server': 'status',
                 'query': 'query'}.get(wtype)
      if not section or not popups.show_popup(
          section, self._parent_window, event.globalPos(),
          copy_action=has_selection):
        super().contextMenuEvent(event)


# ---------------------------------------------------------------------------
# Reusable search bar for any QTextEdit / QPlainTextEdit
# ---------------------------------------------------------------------------

class SearchBar(QWidget):
  """A find bar that searches within a text widget (QTextEdit or QPlainTextEdit).

  *text_widget* is the widget whose document is searched.
  *on_close_focus* is an optional widget to focus when the bar is closed.
  *set_cursor* — if True, the text widget's cursor is moved to the match
  position (useful for editable text).  If False, only extra-selections
  are used for highlighting (useful for read-only chat output).
  """

  def __init__(self, text_widget, on_close_focus=None, set_cursor=False,
               parent=None):
    super().__init__(parent)
    self._text = text_widget
    self._close_focus = on_close_focus
    self._set_cursor = set_cursor
    self._search_cursor = QTextCursor()

    lay = QHBoxLayout(self)
    lay.setContentsMargins(2, 2, 2, 2)

    self._input = QLineEdit()
    self._input.setPlaceholderText("Search\u2026")
    self._input.returnPressed.connect(lambda: self.find(forward=False))
    self._input.textChanged.connect(self._reset)
    lay.addWidget(self._input, 1)

    self._case_cb = QCheckBox("Case sensitive")
    self._case_cb.stateChanged.connect(self._reset)
    lay.addWidget(self._case_cb)

    self._regex_cb = QCheckBox("Regex")
    self._regex_cb.stateChanged.connect(self._reset)
    lay.addWidget(self._regex_cb)

    btn_up = QPushButton("\u25b2")
    btn_up.setFixedWidth(30)
    btn_up.setToolTip("Previous match")
    btn_up.clicked.connect(lambda: self.find(forward=False))
    lay.addWidget(btn_up)

    btn_down = QPushButton("\u25bc")
    btn_down.setFixedWidth(30)
    btn_down.setToolTip("Next match")
    btn_down.clicked.connect(lambda: self.find(forward=True))
    lay.addWidget(btn_down)

    btn_close = QPushButton("\u2715")
    btn_close.setFixedWidth(30)
    btn_close.clicked.connect(self.close_bar)
    lay.addWidget(btn_close)

  def open_bar(self):
    self.setVisible(True)
    self._input.setFocus()
    self._input.selectAll()

  def close_bar(self):
    self.setVisible(False)
    self._search_cursor = QTextCursor()
    self._text.setExtraSelections([])
    if self._close_focus:
      self._close_focus.setFocus()

  def _reset(self):
    self._search_cursor = QTextCursor()
    self._text.setExtraSelections([])

  def find(self, forward=False):
    query = self._input.text()
    if not query:
      return
    case_sensitive = self._case_cb.isChecked()
    use_regex = self._regex_cb.isChecked()
    doc = self._text.document()

    if use_regex:
      try:
        flags = 0 if case_sensitive else re.IGNORECASE
        pat = re.compile(query, flags)
      except re.error:
        return
      found = self._regex_find(doc, pat, forward)
    else:
      found = self._plain_find(doc, query, case_sensitive, forward)

    if found and not found.isNull() and found.hasSelection():
      self._search_cursor = found
      sel = QTextEdit.ExtraSelection()
      sel.cursor = found
      fmt = QTextCharFormat()
      fmt.setBackground(state.config.color_search_bg)
      fmt.setForeground(state.config.color_search_fg)
      sel.format = fmt
      self._text.setExtraSelections([sel])
      view_cursor = QTextCursor(found)
      if self._set_cursor:
        self._text.setTextCursor(found)
      else:
        view_cursor.clearSelection()
        self._text.setTextCursor(view_cursor)
      self._text.ensureCursorVisible()
    else:
      self._text.setExtraSelections([])
      self._search_cursor = QTextCursor()

  def _plain_find(self, doc, query, case_sensitive, forward):
    text = doc.toPlainText()
    if case_sensitive:
      search_text, search_query = text, query
    else:
      search_text, search_query = text.casefold(), query.casefold()

    if self._search_cursor.hasSelection():
      if forward:
        start = self._search_cursor.selectionEnd()
      else:
        start = self._search_cursor.selectionStart() - 1
    else:
      start = len(search_text) if not forward else 0

    if forward:
      idx = search_text.find(search_query, start)
      if idx < 0:
        idx = search_text.find(search_query, 0)
    else:
      idx = search_text.rfind(search_query, 0, max(start + 1, 0))
      if idx < 0:
        idx = search_text.rfind(search_query)

    if idx < 0:
      return QTextCursor()
    cursor = QTextCursor(doc)
    cursor.setPosition(idx)
    cursor.setPosition(idx + len(search_query), QTextCursor.MoveMode.KeepAnchor)
    return cursor

  def _regex_find(self, doc, pat, forward):
    text = doc.toPlainText()
    if self._search_cursor.hasSelection():
      if forward:
        start = self._search_cursor.selectionEnd()
      else:
        start = self._search_cursor.selectionStart()
    else:
      start = len(text) if not forward else 0

    if forward:
      m = pat.search(text, start)
      if not m:
        m = pat.search(text, 0)
    else:
      m = None
      for candidate in pat.finditer(text, 0):
        if candidate.start() < start:
          m = candidate
        else:
          break
      if not m:
        for candidate in pat.finditer(text, 0):
          m = candidate

    if not m:
      return QTextCursor()
    cursor = QTextCursor(doc)
    cursor.setPosition(m.start())
    cursor.setPosition(m.end(), QTextCursor.MoveMode.KeepAnchor)
    return cursor

  def keyPressEvent(self, event):
    if event.key() == Qt.Key.Key_Escape:
      self.close_bar()
      return
    super().keyPressEvent(event)


class _HistoryPopup(QListWidget):
  """Popup list showing input history, appears above the input field."""

  picked = Signal(str)

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setWindowFlags(Qt.WindowType.Popup)
    self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    self.setMaximumHeight(200)
    self._last_key = ''
    self._last_key_row = -1
    self.itemActivated.connect(self._on_activated)
    self.itemClicked.connect(self._on_activated)

  def populate(self, history):
    self.clear()
    # Show most recent at the bottom (reverse order so bottom = newest)
    for text in history:
      item = QListWidgetItem(text.replace('\n', ' '))
      item.setData(Qt.ItemDataRole.UserRole, text)
      self.addItem(item)
    if self.count():
      self.setCurrentRow(self.count() - 1)
      self.scrollToBottom()

  def select_prev(self):
    row = self.currentRow()
    if row > 0:
      self.setCurrentRow(row - 1)
      self._preview()

  def select_next(self):
    row = self.currentRow()
    if row < self.count() - 1:
      self.setCurrentRow(row + 1)
      self._preview()

  def _preview(self):
    item = self.currentItem()
    if item:
      self.picked.emit(item.data(Qt.ItemDataRole.UserRole))

  def _on_activated(self, item):
    self.picked.emit(item.data(Qt.ItemDataRole.UserRole))
    self.hide()

  def keyPressEvent(self, event):
    key = event.key()
    if key == Qt.Key.Key_Return:
      item = self.currentItem()
      if item:
        self._on_activated(item)
      return
    if key == Qt.Key.Key_Escape:
      self.hide()
      return
    if key == Qt.Key.Key_Up:
      self.select_prev()
      return
    if key == Qt.Key.Key_Down:
      self.select_next()
      return
    # Printable key — jump to next entry starting with that character
    ch = event.text()
    if ch and ch.isprintable():
      ch_lower = ch.lower()
      if ch_lower == self._last_key:
        start = self._last_key_row + 1
      else:
        start = 0
        self._last_key = ch_lower
      # Search from start, wrapping around
      for i in range(self.count()):
        row = (start + i) % self.count()
        item = self.item(row)
        text = (item.data(Qt.ItemDataRole.UserRole) or '').lower()
        if text.startswith(ch_lower):
          self.setCurrentRow(row)
          self._last_key_row = row
          self._preview()
          return
    super().keyPressEvent(event)


class Window(QWidget):

  def lineinput(self, text):
    self.input.setText("")
    if text.strip():
      # Avoid consecutive duplicates
      if not self.inputhistory or self.inputhistory[-1] != text:
        self.inputhistory.append(text)
        # Cap and persist
        _MAX_INPUT_HISTORY = 200
        if len(self.inputhistory) > _MAX_INPUT_HISTORY:
          self.inputhistory = self.inputhistory[-_MAX_INPUT_HISTORY:]
        if state.ui_state:
          state.ui_state.input_history = self.inputhistory
    self._history_index = -1
    # Split multiline input into separate lines
    lines = text.split('\n')
    for line in lines:
      line = line.rstrip('\r')
      if not line:
        continue
      if line.startswith(state.config.cmdprefix):
        from commands import docommand
        docommand(self, *(line[len(state.config.cmdprefix):].split(" ", 1)))
      else:
        from commands import docommand
        docommand(self, "say", line)

  def _show_history_popup(self):
    """Show the input history popup above the input field."""
    if not self.inputhistory:
      return
    if not self._history_popup:
      self._history_popup = _HistoryPopup()
      self._history_popup.picked.connect(self._pick_history)
    self._history_popup.populate(self.inputhistory)
    # Position above the input field
    pos = self.input.mapToGlobal(self.input.rect().topLeft())
    w = self.input.width()
    self._history_popup.setFixedWidth(w)
    h = min(200, self._history_popup.sizeHintForRow(0) * min(self._history_popup.count(), 10) + 4)
    self._history_popup.setFixedHeight(h)
    self._history_popup.move(pos.x(), pos.y() - h)
    self._history_popup.show()
    self._history_popup.setFocus()

  def _pick_history(self, text):
    """Called when a history item is selected."""
    self.input.setPlainText(text)
    c = self.input.textCursor()
    c.movePosition(c.MoveOperation.End)
    self.input.setTextCursor(c)
    self.input.setFocus()

  def _get_completable_nicks(self):
    """Return the set of nicks available for completion in this window."""
    own = self.client.conn.nickname.lower() if self.client and self.client.conn else None
    if hasattr(self, 'channel') and self.channel:
      return {n for n in self.channel.nicks if n.lower() != own}
    if hasattr(self, 'query') and self.query:
      return {self.query.nick}
    return set()

  def _sort_nicks_for_completion(self, nicks):
    """Sort nicks by most recent activity (spoke or mentioned), then alpha.
    Uses channel.history to derive activity timestamps."""
    history = getattr(getattr(self, 'channel', None), 'history', None)
    own = self.client.conn.nickname.lower() if self.client and self.client.conn else None
    cutoff = state.config.tab_complete_age
    now = datetime.now()
    active = {}  # nick_lower -> datetime (most recent activity)
    nicks_lower = {n.lower() for n in nicks}
    if history:
      for msg in reversed(history):
        if not hasattr(msg, 'type') or msg.type not in ('message', 'action'):
          continue
        if cutoff and (now - msg.time).total_seconds() > cutoff:
          break
        nl = msg.nick.lower() if msg.nick else None
        if not nl:
          continue
        # Nick spoke (not us)
        if nl != own and nl in nicks_lower and nl not in active:
          active[nl] = msg.time
        # We mentioned nicks in our message
        if nl == own and msg.text:
          words = set(re.split(r'[\s,.:!?]+', msg.text.lower()))
          for n in nicks_lower:
            if n in words and n != own and n not in active:
              active[n] = msg.time
    def sort_key(nick):
      nl = nick.lower()
      t = active.get(nl)
      if t:
        return (0, -t.timestamp(), nick.lower())
      return (1, 0, nick.lower())
    return sorted(nicks, key=sort_key)

  def _start_tab_completion(self):
    """Initiate or update tab completion from the current cursor position."""
    cursor = self.input.textCursor()
    text = self.input.toPlainText()
    pos = cursor.position()
    # Find the word fragment before the cursor
    start = pos
    while start > 0 and text[start - 1] not in ' \t\n,:':
      start -= 1
    prefix = text[start:pos]
    if not prefix:
      return False
    # Get matching nicks
    all_nicks = self._get_completable_nicks()
    if not all_nicks:
      return False
    prefix_lower = prefix.lower()
    matches = [n for n in all_nicks if n.lower().startswith(prefix_lower)]
    if not matches:
      return False
    matches = self._sort_nicks_for_completion(matches)
    self._comp_prefix = prefix
    self._comp_start = start
    if len(matches) == 1:
      # Single match — insert directly
      self._apply_completion(matches[0])
      return True
    # Multiple matches — show popup
    self._show_comp_popup(matches)
    return True

  def _apply_completion(self, nick):
    """Replace the prefix with the completed nick."""
    text = self.input.toPlainText()
    pos_end = self._comp_start + len(self._comp_prefix)
    # Add ": " if at start of line, " " otherwise
    suffix = ': ' if self._comp_start == 0 else ' '
    new_text = text[:self._comp_start] + nick + suffix + text[pos_end:]
    self.input.setPlainText(new_text)
    # Move cursor after the inserted text
    cursor = self.input.textCursor()
    cursor.setPosition(self._comp_start + len(nick) + len(suffix))
    self.input.setTextCursor(cursor)
    self._close_comp_popup()

  def _show_comp_popup(self, nicks):
    """Show a dropdown list of matching nicks above the input field."""
    self._close_comp_popup()
    window = self

    class _CompPopup(QListWidget):
      def __init__(self_, all_nicks):
        super().__init__()
        self_._all_nicks = all_nicks  # full sorted list
      def keyPressEvent(self_, event):
        key = event.key()
        if key == Qt.Key.Key_Down:
          row = self_.currentRow()
          if row < self_.count() - 1:
            self_.setCurrentRow(row + 1)
        elif key == Qt.Key.Key_Up:
          row = self_.currentRow()
          if row > 0:
            self_.setCurrentRow(row - 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Tab):
          item = self_.currentItem()
          if item:
            window._apply_completion(item.text())
        elif key == Qt.Key.Key_Escape:
          window._close_comp_popup()
        elif key == Qt.Key.Key_Backspace:
          if len(window._comp_prefix) > 1:
            window._comp_prefix = window._comp_prefix[:-1]
            self_._filter()
          else:
            window._close_comp_popup()
        elif event.text() and event.text().isprintable():
          window._comp_prefix += event.text()
          self_._filter()
        else:
          window._close_comp_popup()
      def _filter(self_):
        prefix_lower = window._comp_prefix.lower()
        matches = [n for n in self_._all_nicks if n.lower().startswith(prefix_lower)]
        if not matches:
          window._close_comp_popup()
        elif len(matches) == 1:
          window._apply_completion(matches[0])
        else:
          self_.clear()
          for nick in matches:
            self_.addItem(nick)
          self_.setCurrentRow(0)
          self_._resize()
      def _resize(self_):
        item_h = self_.sizeHintForRow(0) if self_.count() else 20
        visible = min(self_.count(), 10)
        self_.setFixedHeight(item_h * visible + 4)
        self_.setFixedWidth(max(self_.sizeHintForColumn(0) + 20, 120))
        input_pos = window.input.mapToGlobal(QPoint(0, 0))
        self_.move(input_pos.x(), input_pos.y() - self_.height())
      def focusOutEvent(self_, event):
        super().focusOutEvent(event)
        window._close_comp_popup()

    popup = _CompPopup(nicks)
    popup.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
    popup.setFont(self.input.font())
    for nick in nicks:
      popup.addItem(nick)
    popup.setCurrentRow(0)
    # Size: fit content, max 10 items visible
    item_h = popup.sizeHintForRow(0) if popup.count() else 20
    visible = min(popup.count(), 10)
    popup.setFixedHeight(item_h * visible + 4)
    popup.setFixedWidth(max(popup.sizeHintForColumn(0) + 20, 120))
    # Position above the input field
    input_pos = self.input.mapToGlobal(QPoint(0, 0))
    popup.move(input_pos.x(), input_pos.y() - popup.height())
    popup.show()
    popup.setFocus()
    popup.itemClicked.connect(lambda item: self._apply_completion(item.text()))
    self._comp_popup = popup

  def _close_comp_popup(self):
    """Close the completion popup if open."""
    if self._comp_popup:
      self._comp_popup.close()
      self._comp_popup = None
      self.input.setFocus()

  def _updateBottomAlign(self):
    """Push content to bottom of viewport when it doesn't fill the window."""
    doc = self.output.document()
    viewport_height = self.output.viewport().height()
    root_frame = doc.rootFrame()
    fmt = root_frame.frameFormat()
    old_margin = fmt.topMargin()
    content_height = doc.size().height() - old_margin
    new_margin = max(0, viewport_height - content_height)
    if abs(old_margin - new_margin) > 1:
      fmt.setTopMargin(new_margin)
      root_frame.setFrameFormat(fmt)

  # Activity levels: higher overrides lower
  ACTIVITY_NONE = 0
  ACTIVITY_MESSAGE = 1
  ACTIVITY_HIGHLIGHT = 2

  @property
  def conn(self):
    """The active IRC connection, or None if disconnected."""
    return self.client.conn if self.client else None

  @property
  def network(self):
    """The Network object for this window's connection."""
    return self.client.net if self.client else None

  @property
  def network_key(self):
    """The config network key (e.g. 'libera'), or None."""
    return self.client.network_key if self.client else None

  def __init__(self, client):
    QWidget.__init__(self)
    self.client = client
    self._activity = self.ACTIVITY_NONE
    self._custom_title = None  # set by /title, overrides automatic titles
    self._auto_title = ''      # last automatic title (for restoring)

    # --- layout: output on top, input on bottom ---
    self._vlayout = QVBoxLayout(self)
    self._vlayout.setContentsMargins(0, 0, 0, 0)

    self.output = ChatOutput(self)
    self.output.setReadOnly(True)
    self.output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    _chatfont = QFont(state.config.fontfamily, state.config.fontheight)
    self.output.setFont(_chatfont)
    # Set default document font explicitly
    self.output.document().setDefaultFont(_chatfont)
    if state.config.backscroll_limit > 0:
      self.output.document().setMaximumBlockCount(state.config.backscroll_limit)
    self.vs = self.output.verticalScrollBar()
    self._auto_scroll = True
    self._programmatic_scroll = False
    self.vs.rangeChanged.connect(self._on_range_changed)
    self.vs.valueChanged.connect(self._on_scroll_changed)
    self.cur = QTextCursor(self.output.document())

    self.input = QTextEdit(self)
    self.input.setAcceptRichText(False)
    self.input.setFont(_chatfont)
    fm = QFontMetrics(_chatfont)
    lines = max(1, state.config.input_lines)
    self.input.setFixedHeight(fm.height() * lines + 10)
    self.input.setVerticalScrollBarPolicy(
      Qt.ScrollBarPolicy.ScrollBarAlwaysOff if lines <= 1
      else Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    self._search_bar = SearchBar(self.output, on_close_focus=None, parent=self)
    self._search_bar.setVisible(False)

    self._build_layout()

    self.inputhistory = list(state.ui_state.input_history) if state.ui_state else []
    self._history_index = -1
    self._history_saved = ''  # text in input before browsing history
    self._history_popup = None
    # Nick tab-completion state
    self._comp_popup = None    # NickCompletionPopup instance
    self._comp_prefix = ''     # the text fragment being completed
    self._comp_start = 0       # cursor position where the fragment starts
    self.input.installEventFilter(self)
    self.subwindow = state.app.mainwin.workspace.addSubWindow(self)
    self.show()

  def _search_open(self):
    self._search_bar.open_bar()

  def _search_close(self):
    self._search_bar.close_bar()
    self.input.setFocus()

  def _build_layout(self):
    """Default layout — subclasses may override."""
    self._vlayout.addWidget(self.output, 1)
    self._vlayout.addWidget(self._search_bar, 0)
    self._vlayout.addWidget(self.input, 0)

  def _strip_mirc(self, text):
    """Strip mIRC formatting codes for plain-text logging."""
    return re.sub(r'[\x02\x03\x0F\x16\x1F]|\x03\d{0,2}(?:,\d{0,2})?', '', text)

  def _insert_timestamp(self):
    """Insert a formatted timestamp at the cursor position."""
    fmt = state.config.timestamp_format
    if not fmt:
      return
    ts = _format_timestamp(fmt)
    self.cur.insertText('[%s] ' % ts, state.timestampformat)

  def _insert_timestamp_override(self, ts_str):
    """Insert a pre-formatted timestamp string."""
    if not state.config.timestamp_format:
      return
    self.cur.insertText('[%s] ' % ts_str, state.timestampformat)

  def _near_bottom(self):
    """True if the scrollbar is at or very near the bottom."""
    font_height = self.output.fontMetrics().height()
    return self.vs.maximum() - self.vs.value() <= font_height * 2

  def _on_range_changed(self, _min, new_max):
    """Auto-scroll when the scrollbar range grows and we were at the bottom."""
    if self._auto_scroll:
      self._programmatic_scroll = True
      self.vs.setValue(new_max)
      self._programmatic_scroll = False

  def _on_scroll_changed(self, _value):
    """Track whether user has scrolled away from bottom."""
    if not self._programmatic_scroll:
      self._auto_scroll = self._near_bottom()

  def _scroll_to_bottom(self):
    """Scroll the output to the very bottom."""
    self._auto_scroll = True
    self._programmatic_scroll = True
    self.vs.setValue(self.vs.maximum())
    self._programmatic_scroll = False

  def _widget_alive(self):
    """Return False if the underlying C++ objects have been deleted."""
    try:
      self.vs.value()
      return True
    except RuntimeError:
      return False

  def add_separator(self, label=" End of saved history "):
    """Insert a visible separator line with a centered label."""
    if not self._widget_alive(): return
    if self.cur.position():
      self.cur.insertText('\n')
    sep_fmt = QTextCharFormat()
    sep_fmt.setForeground(QBrush(QColor(128, 128, 128)))
    width = self.output.viewport().width()
    fm = QFontMetrics(self.output.font())
    label_width = fm.horizontalAdvance(label)
    char_width = fm.horizontalAdvance('\u2500')
    if char_width > 0:
      total_chars = max((width // char_width) - 4, 20)
      side = max((total_chars - len(label) - 2) // 2, 2)
    else:
      side = 20
    line = '\u2500' * side + ' ' + label + ' ' + '\u2500' * side
    self.cur.insertText(line, sep_fmt)

  def addline(self, line, fmt=None, timestamp_override=None):
    if not self._widget_alive(): return
    if self.cur.position():
      self.cur.insertText('\n')
    if timestamp_override:
      self._insert_timestamp_override(timestamp_override)
    else:
      self._insert_timestamp()
    if fmt:
      self._render_text(line, base_format=fmt)
    else:
      self._render_text(line)
    self._updateBottomAlign()

  def addline_nick(self, parts, fmt=None, timestamp_override=None):
    """Add a timestamped line with clickable nick anchors.

    *parts* is a list where plain strings are rendered as text and
    single-element tuples ``(nick,)`` are rendered as clickable anchors.
    *fmt* is an optional QTextCharFormat for the base text color.
    """
    if not self._widget_alive(): return
    cur = self.cur
    if cur.position():
      cur.insertText('\n')
    if timestamp_override:
      self._insert_timestamp_override(timestamp_override)
    else:
      self._insert_timestamp()
    base = fmt or state.defaultformat
    for part in parts:
      if isinstance(part, tuple):
        nick = part[0]
        anchor_fmt = QTextCharFormat(base)
        anchor_fmt.setAnchor(True)
        anchor_fmt.setAnchorHref("nick:" + nick)
        anchor_fmt.setFontUnderline(False)
        cur.insertText(nick, anchor_fmt)
      else:
        self._render_text(part, base_format=base)
    cur.movePosition(QTextCursor.MoveOperation.End)
    self._updateBottomAlign()

  @staticmethod
  def _nick_color(nick):
    """Return a QColor for *nick* based on the nick_colors palette, or None."""
    cfg = state.config
    if not cfg.nick_colors_enabled or not cfg.nick_color_palette:
      return None
    # Strip mode prefixes (@+%) for consistent coloring
    clean = nick.lstrip('@+%~&')
    h = hash(clean.lower())
    palette = cfg.nick_color_palette
    color_str = palette[h % len(palette)]
    return QColor(color_str)

  def addline_msg(self, nick, message, timestamp_override=None):
    """Add a <nick> message line with the nick as a right-clickable anchor."""
    if not self._widget_alive(): return
    cur = self.cur
    if cur.position():
      cur.insertText('\n')
    if timestamp_override:
      self._insert_timestamp_override(timestamp_override)
    else:
      self._insert_timestamp()
    # Determine nick color
    nick_qcolor = self._nick_color(nick)
    bracket_fmt = QTextCharFormat(state.defaultformat)
    if nick_qcolor:
      bracket_fmt.setForeground(QBrush(nick_qcolor))
    # Insert "<"
    cur.insertText('<', bracket_fmt)
    # Insert nick as anchor
    anchor_fmt = QTextCharFormat(state.defaultformat)
    anchor_fmt.setAnchor(True)
    anchor_fmt.setAnchorHref("nick:" + nick)
    anchor_fmt.setFontUnderline(False)
    if nick_qcolor:
      anchor_fmt.setForeground(QBrush(nick_qcolor))
    else:
      anchor_fmt.setForeground(QBrush(state.config.fgcolor))
    cur.insertText(nick, anchor_fmt)
    # Insert "> "
    cur.insertText('> ', bracket_fmt)
    cur.movePosition(QTextCursor.MoveOperation.End)
    # Now render the message body with mIRC formatting
    self._render_text(message)
    self._updateBottomAlign()

  def _insert_with_urls(self, cur, text, fmt):
    """Insert *text* with URLs rendered as clickable anchors."""
    from irc_client import _URL_RE
    pos = 0
    for m in _URL_RE.finditer(text):
      # Insert text before the URL
      if m.start() > pos:
        cur.insertText(text[pos:m.start()], fmt)
      # Strip trailing punctuation (same logic as _extract_urls)
      url = m.group(0)
      while url and url[-1] in '.,;:!?\'"':
        url = url[:-1]
      while url.endswith(')') and url.count(')') > url.count('('):
        url = url[:-1]
      if url:
        url_fmt = QTextCharFormat(fmt)
        url_fmt.setAnchor(True)
        url_fmt.setAnchorHref(url)
        url_fmt.setFontUnderline(True)
        url_fmt.setForeground(state.config.color_link)
        cur.insertText(url, url_fmt)
      # Insert any stripped trailing chars as plain text
      stripped = text[m.start() + len(url):m.end()]
      if stripped:
        cur.insertText(stripped, fmt)
      pos = m.end()
    # Insert remaining text after last URL
    if pos < len(text):
      cur.insertText(text[pos:], fmt)

  def _render_text(self, line, base_format=None):
    """Render mIRC-formatted text at current cursor position."""
    bold = underline = italics = False
    fg = base_format.foreground().color() if base_format else state.config.fgcolor
    bg = state.config.bgcolor
    tf = QTextCharFormat()
    cur = self.cur
    for code, fgs, bgs, text in mircre.findall(line):
      if code in "\x03\x0F":
        fg, bg = state.config.fgcolor, state.config.bgcolor
        tf.setForeground(fg)
        tf.setBackground(bg)
        if code=="\x0F":
          underline = italics = bold = False
          tf.setFontUnderline(False)
          tf.setFontItalic(False)
          tf.setFontWeight(QFont.Weight.Normal)
      elif code.startswith("\x03"):
        fgi = int(fgs)
        if "," in code:
          bgi = int(bgs)
          bg = QColor(*irccolors[bgi % len(irccolors)]) if fgi < 99 else state.config.bgcolor
        fg = QColor(*irccolors[fgi % len(irccolors)]) if fgi < 99 else bg
        tf.setForeground(fg)
        tf.setBackground(bg)
      elif code=="\x1F":
        underline = not underline
        tf.setFontUnderline(underline)
      elif code=="\x1D":
        italics = not italics
        tf.setFontItalic(italics)
      elif code=="\x16":
        fg, bg = bg, fg
        tf.setForeground(fg)
        tf.setBackground(bg)
      elif code=="\x02":
        bold = not bold
        tf.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
      elif code=="":
        tf.setForeground(fg)
        tf.setBackground(bg)
      self._insert_with_urls(cur, text, tf)
      cur.movePosition(QTextCursor.MoveOperation.End)

  def redmessage(self, text):
    if not self._widget_alive(): return
    if self.cur.position():
      self.cur.insertText('\n')
    self._insert_timestamp()
    self.cur.insertText(text, state.redformat)
    self.cur.movePosition(QTextCursor.MoveOperation.End)
    self._updateBottomAlign()

  def addlinef(self, text, format):
    if not self._widget_alive(): return
    if self.cur.position():
      self.cur.insertText('\n'+text, format)
    else:
      self.cur.insertText(text, format)
    self.cur.movePosition(QTextCursor.MoveOperation.End)
    self._updateBottomAlign()

  # --- activity tracking (tab/tree highlighting) ---

  def _is_active_window(self):
    """Return True if this window is the currently viewed one."""
    sub = state.app.mainwin.workspace.activeSubWindow()
    return sub is not None and sub.widget() is self

  def set_activity(self, level):
    """Set activity level if higher than current, and update tab/tree colors."""
    if self._is_active_window():
      return  # don't mark the window the user is looking at
    if level <= self._activity:
      return  # don't downgrade
    self._activity = level
    self._apply_activity_color()

  def clear_activity(self):
    """Clear activity (called when window becomes active)."""
    if self._activity == self.ACTIVITY_NONE:
      return
    self._activity = self.ACTIVITY_NONE
    self._apply_activity_color()

  def _apply_activity_color(self):
    """Apply the current activity color to the tab and treeview item."""
    if self._activity == self.ACTIVITY_HIGHLIGHT:
      color = state.config.color_highlight
    elif self._activity == self.ACTIVITY_MESSAGE:
      color = state.config.color_new_message
    else:
      color = state.config.fgcolor  # reset to default

    # Update tab color
    ws = state.app.mainwin.workspace
    if hasattr(ws, 'set_activity_color'):
      # TabbedWorkspace
      if self._activity == self.ACTIVITY_NONE:
        ws.clear_activity_color(self.subwindow)
      else:
        ws.set_activity_color(self.subwindow, color)
    else:
      # QMdiArea fallback
      tabbar = ws.findChild(QTabBar)
      if tabbar:
        for i in range(tabbar.count()):
          subs = ws.subWindowList()
          if i < len(subs) and subs[i].widget() is self:
            tabbar.setTabTextColor(i, color)
            break

    # Update treeview color
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      item = tree._find_item_for_window(self)
      if item:
        item.setForeground(0, QBrush(color))

  # --- color picker & formatting shortcuts ---

  def _show_color_picker(self):
    """Open the mIRC color picker dialog above the input field."""
    cursor = self.input.textCursor()
    cursor.insertText('\x03')
    colorwidget = QDialog(self)
    colorwidget.setWindowTitle("Colors")
    colorgrid = QGridLayout()
    labelfont = QFont("Arial", 10)
    i = 0
    # First 16 colors: 2 rows x 8 cols
    for y in range(2):
      for x in range(8):
        lbl = QLabel()
        lbl.setFont(labelfont)
        lbl.setAutoFillBackground(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.mousePressEvent = partial(self._color_clicked, str(i))
        bgcolor = irccolors[i]
        fgcolor = "black" if perceivedbrightness(*bgcolor) >= 50 else "white"
        lbl.setStyleSheet("QLabel { background-color: rgb%s; color: %s }" % (bgcolor, fgcolor))
        lbl.setText(str(i))
        colorgrid.addWidget(lbl, y, x, 1, 1)
        i += 1
    # Extended colors 16-98: 7 rows x 12 cols
    for y in range(7):
      for x in range(12):
        if i < 99:
          lbl = QLabel()
          lbl.setFont(labelfont)
          lbl.setAutoFillBackground(True)
          lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
          lbl.mousePressEvent = partial(self._color_clicked, str(i))
          bgcolor = irccolors[i]
          fgcolor = "black" if perceivedbrightness(*bgcolor) >= 50 else "white"
          lbl.setStyleSheet("QLabel { background-color: rgb%s; color: %s }" % (bgcolor, fgcolor))
          lbl.setText(str(i))
          colorgrid.addWidget(lbl, y + 2, x, 1, 1)
        i += 1
    colorwidget.setLayout(colorgrid)
    colorwidget.raise_()
    colorwidget.show()
    size = colorwidget.frameSize()
    w, h = size.width(), size.height()
    pos = self.input.mapToParent(self.input.cursorRect().topLeft())
    colorwidget.move(max(pos.x() - w // 2, 0), max(self.input.y() - h, 0))
    state._colorcodewindow.append(colorwidget)
    self.input.setFocus()
    self.input.activateWindow()

  def _color_clicked(self, numstr, event):
    cursor = self.input.textCursor()
    cursor.insertText(numstr)
    self.input.activateWindow()
    self.input.setFocus()

  def _close_color_picker(self):
    if state._colorcodewindow:
      state._colorcodewindow[0].close()
      state._colorcodewindow.pop()

  def _insert_format_char(self, ch):
    cursor = self.input.textCursor()
    cursor.insertText(ch)

  def eventFilter(self, obj, event):
    # Propagate nicklist width to other windows when splitter drag finishes
    if (hasattr(self, 'splitter') and obj is self.splitter.handle(1)
        and event.type() == QEvent.Type.MouseButtonRelease):
      if self._splitter_dirty:
        self._splitter_dirty = False
        self._propagate_nicklist_width()
    # Claim Tab so QTextEdit doesn't consume it for indentation
    if (event.type() == QEvent.Type.ShortcutOverride
        and event.key() == Qt.Key.Key_Tab
        and not event.modifiers()
        and obj is self.input):
      event.accept()
      return True
    if event.type() == QEvent.Type.KeyPress:
      key = event.key()
      mods = event.modifiers()

      # Ctrl+F — open search bar (from any filtered widget)
      if key == Qt.Key.Key_F and (mods & Qt.KeyboardModifier.ControlModifier):
        self._search_open()
        return True

      # Escape — skip this tab and activate next
      if key == Qt.Key.Key_Escape:
        ws = state.app.mainwin.workspace
        if hasattr(ws, 'skip_current'):
          ws.skip_current()
          return True

      # --- Input widget keys below ---

      # Close color picker on non-digit/non-comma keys
      if state._colorcodewindow and not (Qt.Key.Key_0 <= key <= Qt.Key.Key_9 or key == Qt.Key.Key_Comma):
        self._close_color_picker()

      # Tab — nick completion
      if key == Qt.Key.Key_Tab and not mods:
        if self._start_tab_completion():
          return True

      # Ctrl+Up — open/navigate input history popup
      if key == Qt.Key.Key_Up and (mods & Qt.KeyboardModifier.ControlModifier):
        if self.inputhistory:
          if not self._history_popup or not self._history_popup.isVisible():
            self._show_history_popup()
          else:
            self._history_popup.select_prev()
        return True
      if key == Qt.Key.Key_Down and (mods & Qt.KeyboardModifier.ControlModifier):
        if self._history_popup and self._history_popup.isVisible():
          self._history_popup.select_next()
        return True

      if key == Qt.Key.Key_Return:
        if state._colorcodewindow:
          self._close_color_picker()
        if state.config.multiline and (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
          # Ctrl+Enter or Shift+Enter inserts a newline in multiline mode
          obj.textCursor().insertText('\n')
          return True
        self.lineinput(str(obj.toPlainText()))
        return True
      elif key == Qt.Key.Key_K and (mods & Qt.KeyboardModifier.ControlModifier):
        self._show_color_picker()
        return True
      elif key == Qt.Key.Key_B and (mods & Qt.KeyboardModifier.ControlModifier):
        self._insert_format_char('\x02')
        return True
      elif key == Qt.Key.Key_U and (mods & Qt.KeyboardModifier.ControlModifier):
        self._insert_format_char('\x1f')
        return True
      elif key == Qt.Key.Key_I and (mods & Qt.KeyboardModifier.ControlModifier):
        self._insert_format_char('\x1d')
        return True
      elif key == Qt.Key.Key_R and (mods & Qt.KeyboardModifier.ControlModifier):
        self._insert_format_char('\x16')
        return True
      elif key == Qt.Key.Key_O and (mods & Qt.KeyboardModifier.ControlModifier):
        self._insert_format_char('\x0f')
        return True
    return False

  def setWindowTitle(self, title):
    self._auto_title = title
    if self._custom_title is not None:
      title = self._expand_custom_title()
    self._apply_title(title)

  def _apply_title(self, title):
    """Actually set the window/tab title."""
    super().setWindowTitle(title)
    ws = state.app.mainwin.workspace
    if hasattr(ws, 'update_tab_title') and hasattr(self, 'subwindow'):
      ws.update_tab_title(self.subwindow, title)

  def _expand_custom_title(self):
    """Expand {variables} in the custom title format string."""
    from commands import _window_context_vars
    from config import _expand_vars
    variables = _window_context_vars(self)
    variables.update(state._variables)
    return _expand_vars(self._custom_title, variables, allow_eval=True,
                        eval_ns={'state': state})

  def set_custom_title(self, title):
    """Set a custom title format that overrides automatic titles."""
    self._custom_title = title
    self._apply_title(self._expand_custom_title())

  def clear_custom_title(self):
    """Clear the custom title and restore the automatic one."""
    self._custom_title = None
    self._apply_title(self._auto_title)

  def refresh_custom_title(self):
    """Re-expand the custom title format. Called periodically."""
    if self._custom_title is not None:
      self._apply_title(self._expand_custom_title())

  def showEvent(self, event):
    super().showEvent(event)
    self._updateBottomAlign()
  def resizeEvent(self, event):
    self._updateBottomAlign()
  def moveEvent(self, event):
    QWidget.moveEvent(self, event)


class Serverwindow(Window):
  def __init__(self, client):
    Window.__init__(self, client)
    self.type = "server"
    self.setWindowTitle("[not connected] " + state.config.nick)
    self.show()


class Querywindow(Window):
  def __init__(self, client):
    self.query = None  # set by Query.__init__ after window creation
    self._typing_timer = None
    Window.__init__(self, client)
    self.type = "query"
    self._typing_send_time = 0
    self.input.textChanged.connect(self._on_input_changed)
    self.show()

  def _build_layout(self):
    self._typing_bar = QLabel(self)
    self._typing_bar.setWordWrap(True)
    self._typing_bar.setStyleSheet(
      "QLabel { color: gray; padding: 2px 4px; }")
    self._typing_bar.setVisible(False)
    self._vlayout.addWidget(self._typing_bar, 0)
    self._vlayout.addWidget(self.output, 1)
    self._vlayout.addWidget(self._search_bar, 0)
    self._vlayout.addWidget(self.input, 0)

  _TYPING_TIMEOUT = 6000
  _TYPING_SEND_INTERVAL = 3

  def set_nick_typing(self, nick, typing):
    if typing:
      if self._typing_timer is None:
        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.timeout.connect(self._typing_expired)
      self._typing_timer.start(self._TYPING_TIMEOUT)
      self._typing_bar.setText('%s is typing\u2026' % nick)
      self._typing_bar.setVisible(True)
    else:
      if self._typing_timer:
        self._typing_timer.stop()
      self._typing_bar.setVisible(False)

  def _typing_expired(self):
    self._typing_bar.setVisible(False)

  def _on_input_changed(self):
    conn = self.client.conn if self.client else None
    if not conn or not self.query:
      return
    text = self.input.toPlainText()
    if not text:
      import time as _time
      if self._typing_send_time > 0:
        conn._send_typing(self.query.nick, 'done')
        self._typing_send_time = 0
      return
    import time as _time
    now = _time.monotonic()
    if now - self._typing_send_time >= self._TYPING_SEND_INTERVAL:
      conn._send_typing(self.query.nick, 'active')
      self._typing_send_time = now

  def lineinput(self, text):
    conn = self.client.conn if self.client else None
    if conn and self.query and self._typing_send_time > 0:
      conn._send_typing(self.query.nick, 'done')
      self._typing_send_time = 0
    super().lineinput(text)


class NicksList(QListWidget):
  """Sorted nick list for channel windows with no persistent selection."""
  def __init__(self, channelwindow):
    super().__init__(parent=channelwindow)
    self.setObjectName("nicklist")
    self.setSortingEnabled(True)
    self.channelwindow = channelwindow
    cfg = state.config
    if cfg.nicklist_font_family or cfg.nicklist_font_size:
      self.setFont(QFont(cfg.nicklist_font_family or cfg.fontfamily,
                         cfg.nicklist_font_size or cfg.fontheight))
    else:
      self.setFont(QFont(cfg.fontfamily, cfg.fontheight))
    # Reduce vertical spacing between items
    self.setSpacing(0)
    self.setUniformItemSizes(True)
    class _CompactDelegate(QStyledItemDelegate):
      def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        fm = option.fontMetrics
        sh.setHeight(fm.height() + 2)
        return sh
    self.setItemDelegate(_CompactDelegate(self))
    # Prevent persistent selection highlight
    self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

  def contextMenuEvent(self, event):
    item = self.itemAt(event.pos())
    if item:
      import popups
      nick = item._nick
      popups.show_popup('nicklist', self.channelwindow, event.globalPos(),
                        extra_vars={'nick': nick, '1': nick})

  def mouseDoubleClickEvent(self, event):
    """Double-click a nick to open a message window."""
    item = self.itemAt(event.pos())
    if item:
      _open_query(self.channelwindow.client, item._nick)
    else:
      super().mouseDoubleClickEvent(event)


class NickItem(QListWidgetItem):
  TYPING_PREFIX = '\u2026 '  # "… "

  def __init__(self, nick, user=None, chnlower=None):
    super().__init__(nick)
    self._nick = nick
    self.user = user  # User object (may be None for backward compat)
    self._chnlower = chnlower  # lowercase channel name for prefix lookup
    self._typing = False
    self._update_display()

  def _mode_prefix(self):
    """Return the mode prefix symbol for this nick in its channel, or ''."""
    if not state.config.show_mode_prefix or not self.user or not self._chnlower:
      return ''
    return self.user.prefix.get(self._chnlower, '')

  def _update_display(self):
    """Recompose display text from mode prefix, typing prefix, and nick."""
    mp = self._mode_prefix()
    if self._typing:
      self.setText(mp + self.TYPING_PREFIX + self._nick)
    else:
      self.setText(mp + self._nick)

  def set_typing(self, typing):
    if typing == self._typing:
      return
    self._typing = typing
    self._update_display()

  def set_nick(self, nick):
    self._nick = nick
    self._update_display()

  def refresh_prefix(self):
    """Refresh display after a mode prefix change."""
    self._update_display()

  _PREFIX_RANK = '~&@%+'  # standard prefix order, highest to lowest

  def _prefix_sort_key(self):
    """Return a sort key: (rank, nick_lower). Lower rank = higher status."""
    mp = self._mode_prefix()
    if mp:
      idx = self._PREFIX_RANK.find(mp)
      rank = idx if idx >= 0 else len(self._PREFIX_RANK)
    else:
      rank = len(self._PREFIX_RANK) + 1
    return (rank, self._nick.lower())

  def __lt__(self, other):
    if state.config.show_mode_prefix:
      return self._prefix_sort_key() < other._prefix_sort_key()
    return self._nick.lower() < other._nick.lower()


class Inputwidget(QTextEdit):
  def __init__(self):
    QTextEdit.__init__(self)


def _open_query(client, nick):
  """Open (or focus) a query/message window for *nick*."""
  from models import Query
  conn = client.conn
  if not conn:
    return
  lnick = conn.irclower(nick)
  # Find existing query by nick
  for key, q in client.queries.items():
    if conn.irclower(q.nick) == lnick:
      q.window.subwindow.setFocus()
      return q
  q = Query(client, nick)
  client.queries[lnick] = q
  q.window.subwindow.setFocus()
  return q


class Channelwindow(Window):
  def __init__(self, client, channel):
    self._typing_nicks = {}   # nick -> QTimer
    self._typing_send_time = 0  # monotonic time of last sent typing notification
    Window.__init__(self, client)
    self.type = "channel"
    self.channel = channel
    self.input.textChanged.connect(self._on_input_changed)
    self.show()

  def _build_layout(self):
    """Override: use QSplitter with output | nicklist, then input below."""
    # Left side: typing bar + output stacked vertically
    left = QWidget(self)
    left_layout = QVBoxLayout(left)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(0)
    self._typing_bar = QLabel(left)
    self._typing_bar.setWordWrap(True)
    self._typing_bar.setStyleSheet(
      "QLabel { color: gray; padding: 2px 4px; }")
    self._typing_bar.setVisible(False)
    left_layout.addWidget(self._typing_bar, 0)
    left_layout.addWidget(self.output, 1)

    self.splitter = QSplitter(self)
    self.splitter.addWidget(left)
    self.nickslist = NicksList(self)
    self.splitter.addWidget(self.nickslist)
    # Allow both sides to be resized smaller than their default sizeHint
    left.setMinimumWidth(50)
    self.nickslist.setMinimumWidth(20)
    self.splitter.setCollapsible(0, False)
    self.splitter.setCollapsible(1, False)
    self._target_nw = state.ui_state.nicklist_width if state.ui_state else 150
    self._nw_user_set = False  # True once the user drags the splitter
    self._splitter_dirty = False  # True while dragging, propagate on release
    self.splitter.setSizes([600, self._target_nw])
    self.splitter.splitterMoved.connect(self._on_splitter_moved)
    # Install event filter on the splitter handle to detect drag end
    self.splitter.handle(1).installEventFilter(self)
    self._vlayout.addWidget(self.splitter, 1)
    self._vlayout.addWidget(self._search_bar, 0)
    self._vlayout.addWidget(self.input, 0)

  def resizeEvent(self, event):
    super().resizeEvent(event)
    if not self._nw_user_set:
      total = self.splitter.width()
      nw = self._target_nw
      if total > nw:
        self.splitter.blockSignals(True)
        self.splitter.setSizes([total - nw, nw])
        self.splitter.blockSignals(False)

  def _on_splitter_moved(self, pos, index):
    self._nw_user_set = True
    self._splitter_dirty = True
    sizes = self.splitter.sizes()
    if len(sizes) < 2:
      return
    nw = sizes[1]
    self._target_nw = nw
    if state.ui_state:
      state.ui_state.nicklist_width = nw

  def _propagate_nicklist_width(self):
    """Apply nicklist width to all other channel windows (debounced)."""
    nw = self._target_nw
    for client in state.clients:
      for chan in client.channels.values():
        w = chan.window
        if w and w is not self and hasattr(w, 'splitter'):
          w._target_nw = nw
          total = w.splitter.width()
          if total > nw:
            w.splitter.blockSignals(True)
            w.splitter.setSizes([total - nw, nw])
            w.splitter.blockSignals(False)

  # --- Typing indicator ---

  _TYPING_TIMEOUT = 6000   # ms
  _TYPING_SEND_INTERVAL = 3  # seconds between sending 'active'

  def _on_input_changed(self):
    """Send typing notification when user types (throttled)."""
    conn = self.client.conn if self.client else None
    if not conn or not self.channel:
      return
    text = self.input.toPlainText()
    if not text:
      # Input cleared without sending — send done
      import time as _time
      if self._typing_send_time > 0:
        conn._send_typing(self.channel.name, 'done')
        self._typing_send_time = 0
      return
    import time as _time
    now = _time.monotonic()
    if now - self._typing_send_time >= self._TYPING_SEND_INTERVAL:
      conn._send_typing(self.channel.name, 'active')
      self._typing_send_time = now

  def lineinput(self, text):
    """Override to send typing=done when message is sent."""
    conn = self.client.conn if self.client else None
    if conn and self.channel and self._typing_send_time > 0:
      conn._send_typing(self.channel.name, 'done')
      self._typing_send_time = 0
    super().lineinput(text)

  def set_nick_typing(self, nick, typing):
    """Update the typing state for a nick."""
    if typing:
      if nick in self._typing_nicks:
        # Reset existing timer
        self._typing_nicks[nick].start(self._TYPING_TIMEOUT)
      else:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda n=nick: self._typing_expired(n))
        timer.start(self._TYPING_TIMEOUT)
        self._typing_nicks[nick] = timer
      self._update_nick_typing(nick, True)
    else:
      self._clear_nick_typing(nick)
    self._update_typing_bar()

  def _typing_expired(self, nick):
    """Called when a typing timer expires."""
    self._clear_nick_typing(nick)
    self._update_typing_bar()

  def _clear_nick_typing(self, nick):
    """Remove typing state for a nick."""
    timer = self._typing_nicks.pop(nick, None)
    if timer:
      timer.stop()
    self._update_nick_typing(nick, False)

  def _update_nick_typing(self, nick, typing):
    """Update the nick list item's typing indicator."""
    nl = self.nickslist
    for i in range(nl.count()):
      item = nl.item(i)
      if item and item._nick == nick:
        item.set_typing(typing)
        break

  def _update_typing_bar(self):
    """Update the typing bar label."""
    nicks = list(self._typing_nicks.keys())
    if nicks:
      self._typing_bar.setText('Typing: ' + ', '.join(nicks))
      self._typing_bar.setVisible(True)
    else:
      self._typing_bar.setVisible(False)
