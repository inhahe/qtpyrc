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

  def _refocus_input(self):
    """Return focus to the parent window's input widget."""
    try:
      pw = self._parent_window
      if pw and hasattr(pw, 'input') and pw.input and not pw.input.hasFocus():
        pw.input.setFocus()
    except RuntimeError:
      pass  # C++ object deleted

  # focusInEvent intentionally not overridden — output can take focus
  # for scrolling. Focus returns to input after completed actions
  # (text selection, nick/link click, context menu).

  def mouseMoveEvent(self, event):
    anchor = self.anchorAt(event.pos())
    if anchor and (anchor.startswith('http') or anchor.startswith('nick:')
                   or anchor.startswith('chan:')):
      self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
    else:
      self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
    super().mouseMoveEvent(event)

  def mouseReleaseEvent(self, event):
    super().mouseReleaseEvent(event)
    if event.button() == Qt.MouseButton.LeftButton:
      if self.textCursor().hasSelection():
        # Text was selected (drag) — auto-copy if enabled, don't open links
        if state.config.auto_copy_selection:
          self.copy()
          c = self.textCursor()
          c.clearSelection()
          self.setTextCursor(c)
        self._refocus_input()
      else:
        # Plain click — open link if on one
        anchor = self.anchorAt(event.pos())
        if anchor and anchor.startswith('http'):
          from PySide6.QtGui import QDesktopServices
          from PySide6.QtCore import QUrl
          QDesktopServices.openUrl(QUrl(anchor))
          self._refocus_input()

  def mouseDoubleClickEvent(self, event):
    anchor = self.anchorAt(event.pos())
    if anchor and anchor.startswith("nick:"):
      nick = anchor[5:]
      client = getattr(self._parent_window, 'client', None)
      if client:
        _open_query(client, nick)
        return
    if anchor and anchor.startswith("chan:"):
      chan = anchor[5:]
      client = getattr(self._parent_window, 'client', None)
      conn = client.conn if client else None
      if conn and chan:
        conn.join(chan)
      return
    super().mouseDoubleClickEvent(event)
    if self.textCursor().hasSelection():
      self._refocus_input()

  def _highlight_anchor_at(self, pos):
    """Select the anchor text at pos to visually highlight it.
    Returns the old cursor to restore later, or None."""
    cursor = self.cursorForPosition(pos)
    if not cursor:
      return None
    block = cursor.block()
    # Find the fragment containing the anchor
    it = block.begin()
    while not it.atEnd():
      frag = it.fragment()
      if frag.isValid():
        fmt = frag.charFormat()
        if fmt.isAnchor() and frag.position() <= cursor.position() < frag.position() + frag.length():
          # Select this fragment
          old_cursor = self.textCursor()
          sel = QTextCursor(self.document())
          sel.setPosition(frag.position())
          sel.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
          self.setTextCursor(sel)
          return old_cursor
      it += 1
    return None

  def contextMenuEvent(self, event):
    import popups
    has_selection = self.textCursor().hasSelection()
    anchor = self.anchorAt(event.pos())
    if anchor and anchor.startswith("nick:"):
      nick = anchor[5:]
      wtype = getattr(self._parent_window, 'type', '')
      parent_section = {'channel': 'channel', 'server': 'status',
                        'query': 'query'}.get(wtype)
      old_cursor = self._highlight_anchor_at(event.pos())
      popups.show_popup('nicklist', self._parent_window, event.globalPos(),
                        extra_vars={'nick': nick, '1': nick},
                        copy_action=has_selection,
                        parent_section=parent_section)
      if old_cursor is not None:
        self.setTextCursor(old_cursor)
    elif anchor and anchor.startswith('http'):
      wtype = getattr(self._parent_window, 'type', '')
      parent_section = {'channel': 'channel', 'server': 'status',
                        'query': 'query'}.get(wtype)
      old_cursor = self._highlight_anchor_at(event.pos())
      popups.show_popup('link', self._parent_window, event.globalPos(),
                        extra_vars={'link': anchor},
                        copy_action=has_selection,
                        parent_section=parent_section)
      if old_cursor is not None:
        self.setTextCursor(old_cursor)
    else:
      # Try window-type-specific popup
      wtype = getattr(self._parent_window, 'type', '')
      section = {'channel': 'channel', 'server': 'status',
                 'query': 'query'}.get(wtype)
      if not section or not popups.show_popup(
          section, self._parent_window, event.globalPos(),
          copy_action=has_selection):
        super().contextMenuEvent(event)
    self._refocus_input()


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
    self._open = False

    # Explicit styling so the bar stays visible on both light and dark themes
    # and doesn't disappear when it loses focus.
    cfg = state.config
    fg = cfg.fgcolor.name()
    bg = cfg.bgcolor.name()
    # Derive a slightly contrasting bar background
    lum = cfg.bgcolor.lightness()
    if lum > 128:
      bar_bg = cfg.bgcolor.darker(110).name()
      border = cfg.bgcolor.darker(140).name()
    else:
      bar_bg = cfg.bgcolor.lighter(140).name()
      border = cfg.bgcolor.lighter(180).name()
    self.setStyleSheet(
      "SearchBar { background-color: %s; border-top: 1px solid %s; }"
      " SearchBar QLineEdit { background-color: %s; color: %s;"
      "   border: 1px solid %s; padding: 1px 3px; }"
      " SearchBar QCheckBox { color: %s; }"
      " SearchBar QPushButton { color: %s; }"
      % (bar_bg, border, bg, fg, border, fg, fg))

    lay = QHBoxLayout(self)
    lay.setContentsMargins(2, 2, 2, 2)

    self._input = QLineEdit()
    self._input.setPlaceholderText("Search\u2026")
    self._input.returnPressed.connect(lambda: self.find(forward=False))
    self._input.textChanged.connect(self._reset)
    self._input.installEventFilter(self)
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
    self._open = True
    self.setMinimumHeight(self.sizeHint().height())
    self.setVisible(True)
    self._input.setFocus()
    self._input.selectAll()

  def close_bar(self):
    self._open = False
    self.setMinimumHeight(0)
    self.setVisible(False)
    self._search_cursor = QTextCursor()
    try:
      self._text.setExtraSelections([])
    except RuntimeError:
      pass  # C++ object deleted
    if self._close_focus:
      try:
        self._close_focus.setFocus()
      except RuntimeError:
        pass

  def _reset(self):
    self._search_cursor = QTextCursor()
    try:
      self._text.setExtraSelections([])
    except RuntimeError:
      pass

  def find(self, forward=False):
    query = self._input.text()
    if not query:
      return
    try:
      doc = self._text.document()
    except RuntimeError:
      return  # C++ object deleted
    case_sensitive = self._case_cb.isChecked()
    use_regex = self._regex_cb.isChecked()

    if use_regex:
      try:
        flags = 0 if case_sensitive else re.IGNORECASE
        pat = re.compile(query, flags)
      except re.error:
        return
      found = self._regex_find(doc, pat, forward)
    else:
      found = self._plain_find(doc, query, case_sensitive, forward)

    try:
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
    except RuntimeError:
      self._search_cursor = QTextCursor()

  def _plain_find(self, doc, query, case_sensitive, forward):
    # Use QTextDocument.find() — it operates on document positions, which
    # correctly handles tables (link previews) and embedded images.
    flags = QTextDocument.FindFlag(0)
    if case_sensitive:
      flags |= QTextDocument.FindFlag.FindCaseSensitively
    if not forward:
      flags |= QTextDocument.FindFlag.FindBackward

    if self._search_cursor.hasSelection():
      start_cursor = QTextCursor(self._search_cursor)
      if forward:
        start_cursor.setPosition(self._search_cursor.selectionEnd())
      else:
        start_cursor.setPosition(self._search_cursor.selectionStart())
    else:
      start_cursor = QTextCursor(doc)
      if not forward:
        start_cursor.movePosition(QTextCursor.MoveOperation.End)

    found = doc.find(query, start_cursor, flags)
    if found.isNull() or not found.hasSelection():
      # Wrap around
      wrap_cursor = QTextCursor(doc)
      if not forward:
        wrap_cursor.movePosition(QTextCursor.MoveOperation.End)
      found = doc.find(query, wrap_cursor, flags)
      if found.isNull() or not found.hasSelection():
        return QTextCursor()
    return found

  def _regex_find(self, doc, pat, forward):
    # Use Qt's QRegularExpression find which operates on document positions.
    from PySide6.QtCore import QRegularExpression
    options = QRegularExpression.PatternOption.NoPatternOption
    if pat.flags & re.IGNORECASE:
      options |= QRegularExpression.PatternOption.CaseInsensitiveOption
    if pat.flags & re.MULTILINE:
      options |= QRegularExpression.PatternOption.MultilineOption
    if pat.flags & re.DOTALL:
      options |= QRegularExpression.PatternOption.DotMatchesEverythingOption
    qre = QRegularExpression(pat.pattern, options)
    if not qre.isValid():
      return QTextCursor()

    flags = QTextDocument.FindFlag(0)
    if not forward:
      flags |= QTextDocument.FindFlag.FindBackward

    if self._search_cursor.hasSelection():
      start_cursor = QTextCursor(self._search_cursor)
      if forward:
        start_cursor.setPosition(self._search_cursor.selectionEnd())
      else:
        start_cursor.setPosition(self._search_cursor.selectionStart())
    else:
      start_cursor = QTextCursor(doc)
      if not forward:
        start_cursor.movePosition(QTextCursor.MoveOperation.End)

    found = doc.find(qre, start_cursor, flags)
    if found.isNull() or not found.hasSelection():
      # Wrap around
      wrap_cursor = QTextCursor(doc)
      if not forward:
        wrap_cursor.movePosition(QTextCursor.MoveOperation.End)
      found = doc.find(qre, wrap_cursor, flags)
      if found.isNull() or not found.hasSelection():
        return QTextCursor()
    return found

  def eventFilter(self, obj, event):
    """Intercept keys on the QLineEdit before it consumes them."""
    if obj is self._input and event.type() == QEvent.Type.KeyPress:
      key = event.key()
      if key == Qt.Key.Key_Escape:
        self.close_bar()
        return True
      if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                 Qt.Key.Key_Up, Qt.Key.Key_Down,
                 Qt.Key.Key_Home, Qt.Key.Key_End):
        try:
          vs = self._text.verticalScrollBar()
        except RuntimeError:
          return True
        if key == Qt.Key.Key_Up:
          vs.setValue(vs.value() - vs.singleStep() * 3)
        elif key == Qt.Key.Key_Down:
          vs.setValue(vs.value() + vs.singleStep() * 3)
        elif key == Qt.Key.Key_PageUp:
          vs.setValue(vs.value() - vs.pageStep())
        elif key == Qt.Key.Key_PageDown:
          vs.setValue(vs.value() + vs.pageStep())
        elif key == Qt.Key.Key_Home:
          vs.setValue(vs.minimum())
        elif key == Qt.Key.Key_End:
          vs.setValue(vs.maximum())
        return True
    return super().eventFilter(obj, event)


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

  def _history_up(self):
    """Navigate to the previous (older) input history entry."""
    if not self.inputhistory:
      return
    if self._history_index == -1:
      # Save current input before browsing
      self._history_saved = self.input.toPlainText()
      self._history_index = len(self.inputhistory) - 1
    elif self._history_index > 0:
      self._history_index -= 1
    else:
      return
    self.input.setPlainText(self.inputhistory[self._history_index])
    c = self.input.textCursor()
    c.movePosition(c.MoveOperation.End)
    self.input.setTextCursor(c)

  def _history_down(self):
    """Navigate to the next (newer) input history entry."""
    if self._history_index == -1:
      return
    if self._history_index < len(self.inputhistory) - 1:
      self._history_index += 1
      self.input.setPlainText(self.inputhistory[self._history_index])
    else:
      # Past the end — restore saved input
      self._history_index = -1
      self.input.setPlainText(self._history_saved)
    c = self.input.textCursor()
    c.movePosition(c.MoveOperation.End)
    self.input.setTextCursor(c)

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
    new_text = text[:self._comp_start] + nick + text[pos_end:]
    self.input.setPlainText(new_text)
    # Move cursor after the inserted text
    cursor = self.input.textCursor()
    cursor.setPosition(self._comp_start + len(nick))
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

    # Parent to the main window so the popup is destroyed if the main
    # window goes away — prevents crashes on window switch / shutdown.
    parent_for_popup = state.app.mainwin if state.app else None
    popup = _CompPopup(nicks)
    if parent_for_popup is not None:
      popup.setParent(parent_for_popup)
    popup.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
    # Don't steal focus from the input — keys are handled by the input's
    # event filter while the popup is visible.
    popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
    self._vlayout.setSpacing(0)

    self.output = ChatOutput(self)
    self.output.setReadOnly(True)
    self.output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    _chatfont = QFont(state.config.fontfamily, state.config.fontheight)
    # Per-glyph font fallback chain. Order matters:
    #   1. user's chosen font (covers Latin etc.)
    #   2. Segoe UI       — full Unicode punctuation: curly quotes (U+2019),
    #                       em dashes, ellipsis, etc.  Must come before
    #                       Segoe UI Symbol or Qt will mis-pick the symbol
    #                       font for typographic punctuation and render it
    #                       as missing glyphs.
    #   3. Segoe UI Symbol — monochrome dingbats / BMP symbol ranges
    #                       (U+2600-27BF), kept here so they don't fall
    #                       through to oversized color emoji.
    # Qt then continues searching all system fonts after this list is
    # exhausted (where actual color emoji like U+1F600+ get picked up).
    _chatfont.setFamilies([
      state.config.fontfamily,
      'Segoe UI',
      'Segoe UI Symbol',
    ])
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

    self._search_bar = SearchBar(self.output, on_close_focus=self.input, parent=self)
    self._search_bar.setVisible(False)

    self._build_layout()

    self._replay_queue = None  # list of (method_name, args, kwargs) during replay
    self._in_replay = False    # True while replay is actively inserting lines

    self.inputhistory = list(state.ui_state.input_history) if state.ui_state else []
    self._history_index = -1
    self._history_saved = ''  # text in input before browsing history
    # Nick tab-completion state
    self._comp_popup = None    # NickCompletionPopup instance
    self._comp_prefix = ''     # the text fragment being completed
    self._comp_start = 0       # cursor position where the fragment starts
    self.input.installEventFilter(self)
    self.output.installEventFilter(self)
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
      self.output.moveCursor(QTextCursor.MoveOperation.End)
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
    self.output.moveCursor(QTextCursor.MoveOperation.End)
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
    label_px = fm.horizontalAdvance(' ' + label + ' ')
    char_w = fm.horizontalAdvance('\u2500')
    if char_w > 0:
      avail = max(width - label_px - char_w * 4, char_w * 4)
      side = max(int(avail / char_w / 2), 2)
    else:
      side = 10
    line = '\u2500' * side + ' ' + label + ' ' + '\u2500' * side
    self.cur.insertText(line, sep_fmt)

  def _queue_if_replaying(self, method_name, args, kwargs):
    """If replay is in progress, queue this call for after replay finishes.
    Returns True if queued, False if should proceed normally."""
    if self._replay_queue is not None and not self._in_replay:
      self._replay_queue.append((method_name, args, kwargs))
      return True
    return False

  def queue_replay_callback(self, callback):
    """Queue an arbitrary callback to run after replay finishes.
    Used by IRC handlers to defer side effects (link previews, etc.).
    Returns True if queued, False if no replay in progress."""
    if self._replay_queue is not None and not self._in_replay:
      self._replay_queue.append(('_run_callback', (callback,), {}))
      return True
    return False

  def _run_callback(self, callback):
    """Execute a queued callback."""
    callback()

  def _flush_replay_queue(self):
    """Flush queued live messages after replay finishes."""
    if self._replay_queue is None:
      return
    queue = self._replay_queue
    self._replay_queue = None
    for method_name, args, kwargs in queue:
      getattr(self, method_name)(*args, **kwargs)

  def addline(self, line, fmt=None, timestamp_override=None):
    if not self._widget_alive(): return
    if self._queue_if_replaying('addline', (line,), {'fmt': fmt, 'timestamp_override': timestamp_override}):
      return
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
    """Add a timestamped line with clickable anchors.

    *parts* is a list where plain strings are rendered as text. Tuples are
    rendered as clickable anchors:
      ``(nick,)``         — nick anchor (href ``nick:<clean>``)
      ``(text, href)``    — arbitrary anchor with the given href
                            (e.g. ``('#foo', 'chan:#foo')``).
    *fmt* is an optional QTextCharFormat for the base text color.
    """
    if not self._widget_alive(): return
    if self._queue_if_replaying('addline_nick', (parts,), {'fmt': fmt, 'timestamp_override': timestamp_override}):
      return
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
        if len(part) == 2:
          # (display_text, href) — arbitrary anchor
          text, href = part
          anchor_fmt = QTextCharFormat(base)
          anchor_fmt.setAnchor(True)
          anchor_fmt.setAnchorHref(href)
          anchor_fmt.setFontUnderline(False)
          cur.insertText(text, anchor_fmt)
        else:
          nick = part[0]
          # Strip mode prefix from anchor href so popups get the clean nick
          clean = nick.lstrip('~&@%+') if nick else nick
          anchor_fmt = QTextCharFormat(base)
          anchor_fmt.setAnchor(True)
          anchor_fmt.setAnchorHref("nick:" + clean)
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
    if self._queue_if_replaying('addline_msg', (nick, message), {'timestamp_override': timestamp_override}):
      return
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
    # Strip mode prefix from anchor href so popups get the clean nick
    clean = nick.lstrip('~&@%+') if nick else nick
    anchor_fmt.setAnchorHref("nick:" + clean)
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
    # Strip line-breaking characters — IRC messages are single-line by
    # protocol, but relay bots or Unicode text may embed paragraph/line
    # separators that QTextEdit would render as extra blank lines.
    line = line.replace('\r', '').replace('\n', ' ')
    line = line.replace('\u2028', ' ').replace('\u2029', ' ')
    bold = underline = italics = False
    base_fg = base_format.foreground().color() if base_format else state.config.fgcolor
    if base_format:
      tf = QTextCharFormat(base_format)
    else:
      tf = QTextCharFormat()
      tf.setForeground(QBrush(base_fg))
    fg = base_fg
    bg = state.config.bgcolor
    tf.setBackground(QBrush(bg))
    cur = self.cur
    for code, fgs, bgs, text in mircre.findall(line):
      if code in "\x03\x0F":
        fg, bg = base_fg, state.config.bgcolor
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
    if self._queue_if_replaying('redmessage', (text,), {}):
      return
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
    # Suppress during local DB history replay (replayed content shouldn't
    # mark tabs as unread)
    if hasattr(self, '_deferred_replay') or hasattr(self, '_bg_replay'):
      return
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
    self._color_fg = None
    self._color_bg = None
    colorwidget = QDialog(self)
    colorwidget.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
    colorwidget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    colorgrid = QGridLayout()
    labelfont = QFont("Arial", 10)
    hint = QLabel("Left-click: foreground (closes)  |  Right-click: background")
    hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    hint.setStyleSheet("color: gray; font-size: 8pt;")
    colorgrid.addWidget(hint, 0, 0, 1, 12)
    i = 0
    # First 16 colors: 2 rows x 8 cols
    for y in range(2):
      for x in range(8):
        lbl = QLabel()
        lbl.setFont(labelfont)
        lbl.setAutoFillBackground(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lbl.mousePressEvent = partial(self._color_clicked, str(i))
        bgcolor = irccolors[i]
        fgcolor = "black" if perceivedbrightness(*bgcolor) >= 50 else "white"
        lbl.setStyleSheet("QLabel { background-color: rgb%s; color: %s }" % (bgcolor, fgcolor))
        lbl.setText(str(i))
        colorgrid.addWidget(lbl, y + 1, x, 1, 1)
        i += 1
    # Extended colors 16-98: 7 rows x 12 cols
    for y in range(7):
      for x in range(12):
        if i < 99:
          lbl = QLabel()
          lbl.setFont(labelfont)
          lbl.setAutoFillBackground(True)
          lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
          lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
          lbl.mousePressEvent = partial(self._color_clicked, str(i))
          bgcolor = irccolors[i]
          fgcolor = "black" if perceivedbrightness(*bgcolor) >= 50 else "white"
          lbl.setStyleSheet("QLabel { background-color: rgb%s; color: %s }" % (bgcolor, fgcolor))
          lbl.setText(str(i))
          colorgrid.addWidget(lbl, y + 3, x, 1, 1)
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
    if event.button() == Qt.MouseButton.RightButton:
      # Background color: just remember it, keep picker open
      self._color_bg = numstr
    else:
      # Foreground color: set it and close (commits the color code)
      self._color_fg = numstr
      self._close_color_picker()
    self.input.activateWindow()
    self.input.setFocus()

  def _commit_color_code(self):
    """Insert the accumulated color code into the input field."""
    fg = self._color_fg
    bg = self._color_bg
    cursor = self.input.textCursor()
    if fg is not None and bg is not None:
      cursor.insertText('\x03%s,%s' % (fg, bg))
    elif fg is not None:
      cursor.insertText('\x03%s' % fg)
    elif bg is not None:
      # Background without foreground: use default fg (1 = black)
      cursor.insertText('\x03%s,%s' % ('1', bg))
    else:
      # Nothing selected — insert bare Ctrl+K
      cursor.insertText('\x03')

  def _close_color_picker(self):
    if state._colorcodewindow:
      self._commit_color_code()
      state._colorcodewindow[0].close()
      state._colorcodewindow.pop()
      self._color_fg = None
      self._color_bg = None

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
    # Close the nick-completion popup when the input loses focus
    # (clicking another window, switching tabs, etc.)
    if (obj is getattr(self, 'input', None)
        and event.type() == QEvent.Type.FocusOut
        and getattr(self, '_comp_popup', None)):
      self._close_comp_popup()
    # Claim Tab so QTextEdit doesn't consume it for indentation
    if (event.type() == QEvent.Type.ShortcutOverride
        and event.key() == Qt.Key.Key_Tab
        and not event.modifiers()
        and obj is self.input):
      event.accept()
      return True
    # Claim Ctrl+Home / Ctrl+End so QTextEdit doesn't consume them as
    # default cursor-navigation shortcuts in ShortcutOverride phase before
    # our KeyPress handler below gets a chance to scroll the chat output.
    if (event.type() == QEvent.Type.ShortcutOverride
        and event.key() in (Qt.Key.Key_Home, Qt.Key.Key_End)
        and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        and obj in (self.input, self.output)):
      event.accept()
      return True
    if event.type() == QEvent.Type.KeyPress:
      key = event.key()
      mods = event.modifiers()

      # Ctrl+F — open search bar (from any filtered widget)
      if key == Qt.Key.Key_F and (mods & Qt.KeyboardModifier.ControlModifier):
        self._search_open()
        return True

      # Ctrl+Home / Ctrl+End — scroll chat output to top/bottom
      if key == Qt.Key.Key_Home and (mods & Qt.KeyboardModifier.ControlModifier):
        self.vs.setValue(self.vs.minimum())
        return True
      if key == Qt.Key.Key_End and (mods & Qt.KeyboardModifier.ControlModifier):
        self.vs.setValue(self.vs.maximum())
        return True


      # Escape — close search bar if open, otherwise skip this tab
      if key == Qt.Key.Key_Escape:
        if self._search_bar.isVisible():
          self._search_close()
          return True
        ws = state.app.mainwin.workspace
        if hasattr(ws, 'skip_current'):
          ws.skip_current()
          return True

      # --- Input widget keys below — only when input has focus ---
      if obj is not self.input:
        return False

      # Nick-completion popup is open: nav keys drive the popup, everything
      # else closes it AND falls through to normal input handling so the key
      # the user pressed reaches the input field.
      if self._comp_popup:
        popup = self._comp_popup
        if key == Qt.Key.Key_Down:
          row = popup.currentRow()
          if row < popup.count() - 1:
            popup.setCurrentRow(row + 1)
          return True
        if key == Qt.Key.Key_Up:
          row = popup.currentRow()
          if row > 0:
            popup.setCurrentRow(row - 1)
          return True
        if key == Qt.Key.Key_PageDown:
          row = popup.currentRow()
          popup.setCurrentRow(min(popup.count() - 1, row + 10))
          return True
        if key == Qt.Key.Key_PageUp:
          row = popup.currentRow()
          popup.setCurrentRow(max(0, row - 10))
          return True
        if key == Qt.Key.Key_Home:
          if popup.count():
            popup.setCurrentRow(0)
          return True
        if key == Qt.Key.Key_End:
          if popup.count():
            popup.setCurrentRow(popup.count() - 1)
          return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
          item = popup.currentItem()
          if item:
            self._apply_completion(item.text())
          else:
            self._close_comp_popup()
          return True
        if key == Qt.Key.Key_Escape:
          self._close_comp_popup()
          return True
        # Any other key — dismiss popup and let the key be processed normally.
        self._close_comp_popup()
        # fall through

      # Close color picker on any keypress
      if state._colorcodewindow:
        self._close_color_picker()

      # Tab — nick completion (always consume to prevent tab insertion)
      if key == Qt.Key.Key_Tab and not mods:
        self._start_tab_completion()
        return True

      # Ctrl+Up/Down — navigate input history
      if key == Qt.Key.Key_Up and (mods & Qt.KeyboardModifier.ControlModifier):
        self._history_up()
        return True
      if key == Qt.Key.Key_Down and (mods & Qt.KeyboardModifier.ControlModifier):
        self._history_down()
        return True

      if key == Qt.Key.Key_Return:
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

  @property
  def remotenick(self):
    return self.query.nick if self.query else ''

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
      # Highlight the nick while context menu is shown
      old_bg = item.background()
      item.setBackground(QBrush(QColor('#4488cc')))
      old_fg = item.foreground()
      item.setForeground(QBrush(QColor('white')))
      popups.show_popup('nicklist', self.channelwindow, event.globalPos(),
                        extra_vars={'nick': nick, '1': nick},
                        parent_section='channel')
      item.setBackground(old_bg)
      item.setForeground(old_fg)

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
    if not state.config.show_mode_prefix_nicklist or not self.user or not self._chnlower:
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
    if state.config.show_mode_prefix_nicklist:
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
    # Right side: nick-count label + nicks list stacked vertically
    right = QWidget(self)
    right_layout = QVBoxLayout(right)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(0)
    self._nick_count_label = QLabel(right)
    self._nick_count_label.setStyleSheet(
      "QLabel { color: gray; padding: 2px 4px; }")
    self._nick_count_label.setText('0 users')
    right_layout.addWidget(self._nick_count_label, 0)
    self.nickslist = NicksList(self)
    right_layout.addWidget(self.nickslist, 1)
    self.splitter.addWidget(right)
    # Keep the count label in sync with the listwidget contents.
    nl_model = self.nickslist.model()
    nl_model.rowsInserted.connect(lambda *_: self._update_nick_count())
    nl_model.rowsRemoved.connect(lambda *_: self._update_nick_count())
    nl_model.modelReset.connect(self._update_nick_count)
    # Allow both sides to be resized smaller than their default sizeHint
    left.setMinimumWidth(50)
    right.setMinimumWidth(20)
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

  def _update_nick_count(self):
    """Refresh the 'N users' label above the nicks list."""
    try:
      n = self.nickslist.count()
    except RuntimeError:
      return  # widget destroyed during shutdown
    self._nick_count_label.setText('%d user%s' % (n, '' if n == 1 else 's'))

  def _update_typing_bar(self):
    """Update the typing bar label."""
    nicks = list(self._typing_nicks.keys())
    if nicks:
      self._typing_bar.setText('Typing: ' + ', '.join(nicks))
      self._typing_bar.setVisible(True)
    else:
      self._typing_bar.setVisible(False)
