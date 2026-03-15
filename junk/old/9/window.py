# window.py - GUI window classes

from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *

import re
import time
from functools import partial

import state
from config import _format_timestamp, _parse_color, _match_any, _modify_list_entry, get_ignores, get_auto_ops
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


# ---------------------------------------------------------------------------
# GUI: Window classes
# ---------------------------------------------------------------------------

class ChatOutput(QTextEdit):
  """QTextEdit subclass that supports right-clicking on nick anchors."""
  def __init__(self, parent_window):
    super().__init__(parent_window)
    self._parent_window = parent_window

  def contextMenuEvent(self, event):
    anchor = self.anchorAt(event.pos())
    if anchor and anchor.startswith("nick:"):
      nick = anchor[5:]
      nickcontextmenu(self._parent_window, nick, event.globalPos())
    else:
      super().contextMenuEvent(event)


class Window(QWidget):

  def lineinput(self, text):
    self.input.setText("")
    if text.startswith(state.config.cmdprefix):
      from commands import docommand
      docommand(self, *(text[len(state.config.cmdprefix):].split(" ", 1)))
    else:
      from commands import docommand
      docommand(self, "say", text)

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

    # --- layout: output on top, input on bottom ---
    self._vlayout = QVBoxLayout(self)
    self._vlayout.setContentsMargins(0, 0, 0, 0)

    self.output = ChatOutput(self)
    self.output.setReadOnly(True)
    self.output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    _chatfont = QFont(state.config.fontfamily, state.config.fontheight)
    self.output.setFont(_chatfont)
    if state.config.backscroll_limit > 0:
      self.output.document().setMaximumBlockCount(state.config.backscroll_limit)
    self.vs = self.output.verticalScrollBar()
    self.cur = QTextCursor(self.output.document())

    self.input = QTextEdit(self)
    self.input.setAcceptRichText(False)
    self.input.setFont(_chatfont)
    fm = QFontMetrics(_chatfont)
    self.input.setFixedHeight(fm.height() + 10)
    self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

    self._search_bar = self._build_search_bar()
    self._search_bar.setVisible(False)
    self._search_cursor = QTextCursor()  # null cursor = no current match

    self._build_layout()

    self.inputhistory = []
    self.input.installEventFilter(self)
    self._search_input.installEventFilter(self)
    self.subwindow = state.app.mainwin.workspace.addSubWindow(self)
    self.show()

  def _build_search_bar(self):
    bar = QWidget(self)
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(2, 2, 2, 2)

    self._search_input = QLineEdit()
    self._search_input.setPlaceholderText("Search…")
    self._search_input.returnPressed.connect(lambda: self._search_do(forward=False))
    self._search_input.textChanged.connect(self._search_reset_highlight)
    lay.addWidget(self._search_input, 1)

    self._search_case = QCheckBox("Case sensitive")
    self._search_case.setChecked(False)
    self._search_case.stateChanged.connect(self._search_reset_highlight)
    lay.addWidget(self._search_case)

    self._search_regex = QCheckBox("Regex")
    self._search_regex.setChecked(False)
    self._search_regex.stateChanged.connect(self._search_reset_highlight)
    lay.addWidget(self._search_regex)

    btn_up = QPushButton("▲")
    btn_up.setFixedWidth(30)
    btn_up.setToolTip("Previous match")
    btn_up.clicked.connect(lambda: self._search_do(forward=False))
    lay.addWidget(btn_up)

    btn_down = QPushButton("▼")
    btn_down.setFixedWidth(30)
    btn_down.setToolTip("Next match")
    btn_down.clicked.connect(lambda: self._search_do(forward=True))
    lay.addWidget(btn_down)

    btn_close = QPushButton("✕")
    btn_close.setFixedWidth(30)
    btn_close.clicked.connect(self._search_close)
    lay.addWidget(btn_close)

    return bar

  def _search_open(self):
    self._search_bar.setVisible(True)
    self._search_input.setFocus()
    self._search_input.selectAll()

  def _search_close(self):
    self._search_bar.setVisible(False)
    self._search_cursor = QTextCursor()
    self.output.setExtraSelections([])
    self.input.setFocus()

  def _search_reset_highlight(self):
    """Clear the current match when search text or options change."""
    self._search_cursor = QTextCursor()
    self.output.setExtraSelections([])

  def _search_do(self, forward=False):
    """Find the next (or previous) match.  Default direction is backward (up)."""
    query = self._search_input.text()
    if not query:
      return
    case_sensitive = self._search_case.isChecked()
    use_regex = self._search_regex.isChecked()
    doc = self.output.document()

    if use_regex:
      try:
        flags = 0 if case_sensitive else re.IGNORECASE
        pat = re.compile(query, flags)
      except re.error as e:
        self.redmessage('[Search: invalid regex: %s]' % e)
        return
      found_cursor = self._search_regex_find(doc, pat, forward)
    else:
      found_cursor = self._search_plain_find(doc, query, case_sensitive, forward)

    if found_cursor and not found_cursor.isNull() and found_cursor.hasSelection():
      self._search_cursor = found_cursor
      # Highlight the match
      sel = QTextEdit.ExtraSelection()
      sel.cursor = found_cursor
      fmt = QTextCharFormat()
      fmt.setBackground(state.config.color_search_bg)
      fmt.setForeground(state.config.color_search_fg)
      sel.format = fmt
      self.output.setExtraSelections([sel])
      # Scroll to the match
      view_cursor = QTextCursor(found_cursor)
      view_cursor.clearSelection()
      self.output.setTextCursor(view_cursor)
      self.output.ensureCursorVisible()
    else:
      self.output.setExtraSelections([])
      self._search_cursor = QTextCursor()

  def _search_plain_find(self, doc, query, case_sensitive, forward):
    """Plain text search with optional case insensitivity."""
    text = doc.toPlainText()
    if case_sensitive:
      search_text = text
      search_query = query
    else:
      search_text = text.casefold()
      search_query = query.casefold()

    # Determine start position from current match
    if self._search_cursor.hasSelection():
      if forward:
        start = self._search_cursor.selectionEnd()
      else:
        start = self._search_cursor.selectionStart() - 1
    else:
      # Default: start from end (searching up) or start (searching down)
      start = len(search_text) if not forward else 0

    if forward:
      idx = search_text.find(search_query, start)
      if idx < 0:  # wrap around
        idx = search_text.find(search_query, 0)
    else:
      idx = search_text.rfind(search_query, 0, max(start + 1, 0))
      if idx < 0:  # wrap around
        idx = search_text.rfind(search_query)

    if idx < 0:
      return QTextCursor()

    cursor = QTextCursor(doc)
    cursor.setPosition(idx)
    cursor.setPosition(idx + len(search_query), QTextCursor.MoveMode.KeepAnchor)
    return cursor

  def _search_regex_find(self, doc, pat, forward):
    """Regex search."""
    text = doc.toPlainText()
    search_text = text

    # Determine start position
    if self._search_cursor.hasSelection():
      if forward:
        start = self._search_cursor.selectionEnd()
      else:
        start = self._search_cursor.selectionStart()
    else:
      start = len(search_text) if not forward else 0

    if forward:
      m = pat.search(search_text, start)
      if not m:  # wrap
        m = pat.search(search_text, 0)
    else:
      # Find the last match before start
      m = None
      for candidate in pat.finditer(search_text, 0):
        if candidate.start() < start:
          m = candidate
        else:
          break
      if not m:  # wrap — find last match in entire text
        for candidate in pat.finditer(search_text, 0):
          m = candidate

    if not m:
      return QTextCursor()

    cursor = QTextCursor(doc)
    cursor.setPosition(m.start())
    cursor.setPosition(m.end(), QTextCursor.MoveMode.KeepAnchor)
    return cursor

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
    return self.vs.maximum() - self.vs.value() <= 5

  def _widget_alive(self):
    """Return False if the underlying C++ objects have been deleted."""
    try:
      self.vs.value()
      return True
    except RuntimeError:
      return False

  def add_separator(self, label=" End of history playback "):
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
    stb = self._near_bottom()
    if self.cur.position():
      self.cur.insertText('\n')
    if timestamp_override:
      self._insert_timestamp_override(timestamp_override)
    else:
      self._insert_timestamp()
    if fmt:
      self._render_mirc(line, base_format=fmt)
    else:
      self._render_mirc(line)
    if stb:
      self.output.moveCursor(QTextCursor.MoveOperation.End)
      self.output.ensureCursorVisible()
    self._updateBottomAlign()

  def addline_nick(self, parts, fmt=None, timestamp_override=None):
    """Add a timestamped line with clickable nick anchors.

    *parts* is a list where plain strings are rendered as text and
    single-element tuples ``(nick,)`` are rendered as clickable anchors.
    *fmt* is an optional QTextCharFormat for the base text color.
    """
    if not self._widget_alive(): return
    stb = self._near_bottom()
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
        cur.insertText(part, base)
    cur.movePosition(QTextCursor.MoveOperation.End)
    if stb:
      self.output.moveCursor(QTextCursor.MoveOperation.End)
      self.output.ensureCursorVisible()
    self._updateBottomAlign()

  def addline_msg(self, nick, message, timestamp_override=None):
    """Add a <nick> message line with the nick as a right-clickable anchor."""
    if not self._widget_alive(): return
    stb = self._near_bottom()
    cur = self.cur
    if cur.position():
      cur.insertText('\n')
    if timestamp_override:
      self._insert_timestamp_override(timestamp_override)
    else:
      self._insert_timestamp()
    # Insert "<"
    cur.insertText('<', state.defaultformat)
    # Insert nick as anchor
    anchor_fmt = QTextCharFormat(state.defaultformat)
    anchor_fmt.setAnchor(True)
    anchor_fmt.setAnchorHref("nick:" + nick)
    anchor_fmt.setFontUnderline(False)
    anchor_fmt.setForeground(QBrush(state.config.fgcolor))
    cur.insertText(nick, anchor_fmt)
    # Insert "> "
    cur.insertText('> ', state.defaultformat)
    cur.movePosition(QTextCursor.MoveOperation.End)
    # Now render the message body with mIRC formatting
    self._render_mirc(message)
    if stb:
      self.output.moveCursor(QTextCursor.MoveOperation.End)
      self.output.ensureCursorVisible()
    self._updateBottomAlign()

  def _render_mirc(self, line, base_format=None):
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
      cur.insertText(text, tf)
      cur.movePosition(QTextCursor.MoveOperation.End)

  def redmessage(self, text):
    if not self._widget_alive(): return
    stb = self._near_bottom()
    if self.cur.position():
      self.cur.insertText('\n')
    self._insert_timestamp()
    self.cur.insertText(text, state.redformat)
    self.cur.movePosition(QTextCursor.MoveOperation.End)
    if stb:
      self.output.moveCursor(QTextCursor.MoveOperation.End)
      self.output.ensureCursorVisible()
    self._updateBottomAlign()

  def addlinef(self, text, format):
    if not self._widget_alive(): return
    stb = self._near_bottom()
    if self.cur.position():
      self.cur.insertText('\n'+text, format)
    else:
      self.cur.insertText(text, format)
    self.cur.movePosition(QTextCursor.MoveOperation.End)
    if stb:
      self.output.moveCursor(QTextCursor.MoveOperation.End)
      self.output.ensureCursorVisible()
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
    if event.type() == QEvent.Type.KeyPress:
      key = event.key()
      mods = event.modifiers()

      # Ctrl+F — open search bar (from any filtered widget)
      if key == Qt.Key.Key_F and (mods & Qt.KeyboardModifier.ControlModifier):
        self._search_open()
        return True

      # Search input-specific keys
      if obj is self._search_input:
        if key == Qt.Key.Key_Escape:
          self._search_close()
          return True
        # Let the search input handle its own keys normally
        return False

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

      if key == Qt.Key.Key_Return:
        if state._colorcodewindow:
          self._close_color_picker()
        if not (state.config.multiline and mods & Qt.KeyboardModifier.ShiftModifier):
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
    super().setWindowTitle(title)
    ws = state.app.mainwin.workspace
    if hasattr(ws, 'update_tab_title') and hasattr(self, 'subwindow'):
      ws.update_tab_title(self.subwindow, title)

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
    self.setWindowTitle("[not connected] - " + state.config.nick)
    self.show()


class Querywindow(Window):
  def __init__(self, client):
    self.query = None  # set by Query.__init__ after window creation
    Window.__init__(self, client)
    self.type = "query"
    self.show()


class NicksList(QListWidget):
  """Sorted nick list for channel windows with no persistent selection."""
  def __init__(self, channelwindow):
    super().__init__(parent=channelwindow)
    self.setSortingEnabled(True)
    self.channelwindow = channelwindow
    self.setFont(QFont(state.config.fontfamily, state.config.fontheight))
    # Prevent persistent selection highlight
    self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

  def contextMenuEvent(self, event):
    item = self.itemAt(event.pos())
    if item:
      nickcontextmenu(self.channelwindow, item.text(), event.globalPos())

  def mouseDoubleClickEvent(self, event):
    """Double-click a nick to open a message window."""
    item = self.itemAt(event.pos())
    if item:
      _open_query(self.channelwindow.client, item.text())
    else:
      super().mouseDoubleClickEvent(event)


class NickItem(QListWidgetItem):
  def __init__(self, nick, user=None):
    super().__init__(nick)
    self.user = user  # User object (may be None for backward compat)
  def __lt__(self, other):
    return self.text().lower() < other.text().lower()


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


def nickcontextmenu(window, nick, pos):
  """Right-click context menu for a nick in the nick list or chat."""
  conn = window.client.conn
  if not conn:
    return

  chan = getattr(window, 'channel', None)
  channame = chan.name if chan else None

  menu = QMenu(window)

  # --- informational ---
  whoisAction = menu.addAction("&Whois")
  messageAction = menu.addAction("&Message")
  inviteAction = menu.addAction("&Invite to channel")
  menu.addSeparator()

  # --- CTCP ---
  ctcpMenu = menu.addMenu("&CTCP")
  ctcpPing = ctcpMenu.addAction("&Ping")
  ctcpVersion = ctcpMenu.addAction("&Version")
  ctcpFinger = ctcpMenu.addAction("&Finger")
  ctcpTime = ctcpMenu.addAction("&Time")
  menu.addSeparator()

  # --- channel operator actions (only in channel windows) ---
  kickAction = kickMsgAction = banAction = kickbanAction = kickbanMsgAction = None
  voiceAction = devoiceAction = None
  quietAction = unquietAction = None
  opAction = deopAction = halfopAction = dehalfopAction = None

  if channame:
    opMenu = menu.addMenu("&Operator")
    kickAction = opMenu.addAction("&Kick")
    kickMsgAction = opMenu.addAction("Kick (&message)...")
    banAction = opMenu.addAction("&Ban")
    kickbanAction = opMenu.addAction("Kick+ba&n")
    kickbanMsgAction = opMenu.addAction("Kick+ban (m&essage)...")
    opMenu.addSeparator()
    voiceAction = opMenu.addAction("Set &voice (+v)")
    devoiceAction = opMenu.addAction("Unset v&oice (-v)")
    opMenu.addSeparator()
    quietAction = opMenu.addAction("Set &quiet (+q)")
    unquietAction = opMenu.addAction("Unset q&uiet (-q)")
    opMenu.addSeparator()
    opAction = opMenu.addAction("&Op (+o)")
    deopAction = opMenu.addAction("&De-op (-o)")
    halfopAction = opMenu.addAction("&Half-op (+h)")
    dehalfopAction = opMenu.addAction("De-h&alf-op (-h)")

  # --- ignore / auto-op ---
  menu.addSeparator()
  nk = window.client.network_key
  ctx_channel = channame

  cur_ignores = get_ignores(nk, ctx_channel)
  is_ign = _match_any(nick, cur_ignores)
  ignoreAction = menu.addAction("&Unignore" if is_ign else "&Ignore")

  autoopAction = None
  if channame:
    cur_aops = get_auto_ops(nk, ctx_channel)
    is_aop = _match_any(nick, cur_aops)
    autoopAction = menu.addAction("Remove a&uto-op" if is_aop else "A&uto-op")

  action = menu.exec(pos)
  if not action:
    return

  # --- dispatch ---
  if action == whoisAction:
    conn.do_whois(nick, window)
  elif action == messageAction:
    _open_query(window.client, nick)
  elif action == inviteAction:
    if channame:
      default_ch = channame
    else:
      default_ch = '#'
    ch, ok = QInputDialog.getText(window, "Invite", "Channel to invite %s to:" % nick, text=default_ch)
    if ok and ch.strip():
      conn.sendLine("INVITE %s %s" % (nick, ch.strip()))
      window.addline("[Invited %s to %s]" % (nick, ch.strip()))
  elif action == ctcpPing:
    conn.do_ctcp(nick, 'PING', str(int(time.time())), window)
    window.addline("[CTCP PING %s]" % nick)
  elif action == ctcpVersion:
    conn.do_ctcp(nick, 'VERSION', None, window)
    window.addline("[CTCP VERSION %s]" % nick)
  elif action == ctcpFinger:
    conn.do_ctcp(nick, 'FINGER', None, window)
    window.addline("[CTCP FINGER %s]" % nick)
  elif action == ctcpTime:
    conn.do_ctcp(nick, 'TIME', None, window)
    window.addline("[CTCP TIME %s]" % nick)
  elif action == ignoreAction:
    _modify_list_entry('ignores', nick, is_ign, nk, ctx_channel)
    if is_ign:
      window.redmessage("[Unignored %s]" % nick)
    else:
      window.redmessage("[Ignored %s]" % nick)
  elif autoopAction and action == autoopAction:
    _modify_list_entry('auto_ops', nick, is_aop, nk, ctx_channel)
    if is_aop:
      window.redmessage("[Removed auto-op for %s]" % nick)
    else:
      window.redmessage("[Added auto-op for %s]" % nick)
  # --- channel ops ---
  elif channame and action == kickAction:
    conn.sendLine("KICK %s %s" % (channame, nick))
  elif channame and action == kickMsgAction:
    reason, ok = QInputDialog.getText(window, "Kick", "Kick message (leave blank for none):")
    if ok:
      if reason:
        conn.sendLine("KICK %s %s :%s" % (channame, nick, reason))
      else:
        conn.sendLine("KICK %s %s" % (channame, nick))
  elif channame and action == banAction:
    conn.sendLine("MODE %s +b %s!*@*" % (channame, nick))
  elif channame and action == kickbanAction:
    conn.sendLine("MODE %s +b %s!*@*" % (channame, nick))
    conn.sendLine("KICK %s %s" % (channame, nick))
  elif channame and action == kickbanMsgAction:
    reason, ok = QInputDialog.getText(window, "Kick+Ban", "Kick message (leave blank for none):")
    if ok:
      conn.sendLine("MODE %s +b %s!*@*" % (channame, nick))
      if reason:
        conn.sendLine("KICK %s %s :%s" % (channame, nick, reason))
      else:
        conn.sendLine("KICK %s %s" % (channame, nick))
  elif channame and action == voiceAction:
    conn.sendLine("MODE %s +v %s" % (channame, nick))
  elif channame and action == devoiceAction:
    conn.sendLine("MODE %s -v %s" % (channame, nick))
  elif channame and action == quietAction:
    conn.sendLine("MODE %s +q %s" % (channame, nick))
  elif channame and action == unquietAction:
    conn.sendLine("MODE %s -q %s" % (channame, nick))
  elif channame and action == opAction:
    conn.sendLine("MODE %s +o %s" % (channame, nick))
  elif channame and action == deopAction:
    conn.sendLine("MODE %s -o %s" % (channame, nick))
  elif channame and action == halfopAction:
    conn.sendLine("MODE %s +h %s" % (channame, nick))
  elif channame and action == dehalfopAction:
    conn.sendLine("MODE %s -h %s" % (channame, nick))


class Channelwindow(Window):
  def __init__(self, client, channel):
    Window.__init__(self, client)
    self.type = "channel"
    self.channel = channel
    self.show()

  def _build_layout(self):
    """Override: use QSplitter with output | nicklist, then input below."""
    self.splitter = QSplitter(self)
    self.splitter.addWidget(self.output)
    self.nickslist = NicksList(self)
    self.splitter.addWidget(self.nickslist)
    nw = state.layout.nicklist_width if state.layout else 150
    self.splitter.setSizes([600, nw])
    self.splitter.splitterMoved.connect(self._on_splitter_moved)
    self._vlayout.addWidget(self.splitter, 1)
    self._vlayout.addWidget(self._search_bar, 0)
    self._vlayout.addWidget(self.input, 0)

  def _on_splitter_moved(self, pos, index):
    if state.layout:
      sizes = self.splitter.sizes()
      if len(sizes) >= 2:
        state.layout.nicklist_width = sizes[1]
