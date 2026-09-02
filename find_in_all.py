"""Find-in-all-windows dock widget.

Cross-window text search. Scans the live QTextDocument of every open window
(server / channel / query) and, optionally, the SQLite history database.
Results group by window; double-clicking a result activates the target
window and highlights the match.

Bound to Ctrl+Shift+F. View-mode-agnostic — works in tabs, MDI, tree, or
any combination.
"""

import re
import state

from PySide6.QtCore import Qt, QRegularExpression
from PySide6.QtGui import (QTextCursor, QTextDocument, QTextCharFormat)
from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QCheckBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QLabel, QTextEdit)


# Cap result counts so the tree doesn't blow up on huge buffers.
_MAX_PER_WINDOW = 500
_MAX_HISTORY_ROWS = 5000


class FindInAllDock(QDockWidget):
  """Dockable cross-window find panel."""

  def __init__(self, parent):
    super().__init__('Find in all windows', parent)
    self.setObjectName('FindInAllDock')
    self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea
                         | Qt.DockWidgetArea.TopDockWidgetArea)

    body = QWidget(self)
    v = QVBoxLayout(body)
    v.setContentsMargins(6, 6, 6, 6)
    v.setSpacing(4)

    # Explicit styling so controls stay visible on both light and dark themes.
    cfg = state.config
    fg = cfg.fgcolor.name()
    bg = cfg.bgcolor.name()
    lum = cfg.bgcolor.lightness()
    if lum > 128:
      bar_bg = cfg.bgcolor.darker(110).name()
      border = cfg.bgcolor.darker(140).name()
    else:
      bar_bg = cfg.bgcolor.lighter(140).name()
      border = cfg.bgcolor.lighter(180).name()
    body.setStyleSheet(
      "QLineEdit { background-color: %s; color: %s; border: 1px solid %s;"
      "  padding: 1px 3px; }"
      " QCheckBox { color: %s; }"
      " QPushButton { color: %s; }"
      " QLabel { color: %s; }"
      " QTreeWidget { background-color: %s; color: %s; }"
      % (bg, fg, border, fg, fg, fg, bg, fg))

    # Top row: query field + options + Find button.
    top = QHBoxLayout()
    top.setSpacing(6)
    self._query = QLineEdit(body)
    self._query.setPlaceholderText('Find in all windows...')
    self._query.returnPressed.connect(self.perform_search)
    top.addWidget(self._query, 1)
    self._cs = QCheckBox('Case', body)
    top.addWidget(self._cs)
    self._regex = QCheckBox('Regex', body)
    top.addWidget(self._regex)
    self._history = QCheckBox('History DB', body)
    self._history.setToolTip(
      'Also search the SQLite history database (older messages that may '
      'have scrolled out of the live buffer or are from past sessions).')
    top.addWidget(self._history)
    btn = QPushButton('Find', body)
    btn.clicked.connect(self.perform_search)
    top.addWidget(btn)
    close_btn = QPushButton('Close', body)
    close_btn.clicked.connect(self.hide)
    top.addWidget(close_btn)
    v.addLayout(top)

    # Status line.
    self._status = QLabel('', body)
    self._status.setStyleSheet("QLabel { color: gray; }")
    v.addWidget(self._status)

    # Results tree: window node -> match rows.
    self._tree = QTreeWidget(body)
    self._tree.setHeaderLabels(['Match', 'When'])
    self._tree.setRootIsDecorated(True)
    self._tree.setUniformRowHeights(True)
    self._tree.itemActivated.connect(self._on_result_activated)
    self._tree.itemDoubleClicked.connect(self._on_result_activated)
    v.addWidget(self._tree, 1)

    self.setWidget(body)

  # ------------------------------------------------------------------ open

  def toggle(self):
    """Show + focus the query field, or hide if already visible."""
    if self.isVisible():
      self.hide()
      return
    self.show()
    self.raise_()
    self._query.setFocus()
    self._query.selectAll()

  # ------------------------------------------------------------------ search

  def perform_search(self):
    query = self._query.text()
    self._tree.clear()
    if not query:
      self._status.setText('')
      return

    case_sensitive = self._cs.isChecked()
    use_regex = self._regex.isChecked()

    # Pre-compile a Python regex for the history DB path. Doc search uses
    # QRegularExpression so it can step through positions natively.
    py_pat = None
    if use_regex:
      try:
        py_pat = re.compile(query, 0 if case_sensitive else re.IGNORECASE)
      except re.error as e:
        self._status.setText('Invalid regex: %s' % e)
        return

    qre = None
    if use_regex:
      opts = QRegularExpression.PatternOption.NoPatternOption
      if not case_sensitive:
        opts |= QRegularExpression.PatternOption.CaseInsensitiveOption
      qre = QRegularExpression(query, opts)
      if not qre.isValid():
        self._status.setText('Invalid regex')
        return

    total_matches = 0
    total_windows = 0

    # --- Live windows ---
    for client in list(state.clients or []):
      net_label = client.network_key or client.network or getattr(
        client, 'hostname', '') or 'unknown'

      windows = []
      if getattr(client, 'window', None):
        windows.append(('(server)', client.window))
      for chan in list(client.channels.values()):
        if getattr(chan, 'window', None):
          windows.append((chan.name, chan.window))
      for q in list(client.queries.values()):
        if getattr(q, 'window', None):
          windows.append((q.nick, q.window))

      for label, win in windows:
        matches = self._search_document(win, query, case_sensitive, qre)
        if not matches:
          continue
        total_windows += 1
        total_matches += len(matches)
        node = QTreeWidgetItem(['%s / %s  (%d)' % (net_label, label, len(matches)), ''])
        node.setFirstColumnSpanned(True)
        self._tree.addTopLevelItem(node)
        for line_text, position, ts in matches:
          child = QTreeWidgetItem([line_text, ts or ''])
          # (kind, target_window, position, query_text)
          child.setData(0, Qt.ItemDataRole.UserRole,
                        ('live', win, position, query))
          node.addChild(child)
        node.setExpanded(True)

    # --- History DB ---
    if self._history.isChecked() and state.historydb:
      hist_groups = self._search_history(query, case_sensitive, py_pat)
      for (network, channel), rows in hist_groups.items():
        # Try to find a live window matching this (network, channel) pair
        # so the user can jump straight there.
        target_win = self._find_live_window_for(network, channel)
        net_label = network or '(unknown)'
        chan_label = channel or '(unknown)'
        suffix = '' if target_win else '  [no open window]'
        node = QTreeWidgetItem(
          ['%s / %s  (%d, history)%s' % (net_label, chan_label, len(rows), suffix), ''])
        node.setFirstColumnSpanned(True)
        self._tree.addTopLevelItem(node)
        for ts, nick, text in rows:
          line = '<%s> %s' % (nick or '', text or '')
          child = QTreeWidgetItem([line, ts or ''])
          child.setData(0, Qt.ItemDataRole.UserRole,
                        ('history', target_win, text or '', query))
          node.addChild(child)
        total_windows += 1
        total_matches += len(rows)
        node.setExpanded(False)

    if total_matches == 0:
      self._status.setText('No matches')
    else:
      self._status.setText('%d match%s in %d window%s' % (
        total_matches, '' if total_matches == 1 else 'es',
        total_windows, '' if total_windows == 1 else 's'))
    self._tree.resizeColumnToContents(0)

  def _search_document(self, win, query, case_sensitive, qre):
    """Run *query* over win.output's QTextDocument. Returns a list of
    (line_text, doc_position, ts_string) tuples, capped at _MAX_PER_WINDOW."""
    output = getattr(win, 'output', None)
    if output is None:
      return []
    try:
      doc = output.document()
    except RuntimeError:
      return []
    flags = QTextDocument.FindFlag(0)
    if case_sensitive:
      flags |= QTextDocument.FindFlag.FindCaseSensitively
    cursor = QTextCursor(doc)
    matches = []
    while True:
      if qre is not None:
        cursor = doc.find(qre, cursor, flags)
      else:
        cursor = doc.find(query, cursor, flags)
      if cursor.isNull() or not cursor.hasSelection():
        break
      block = cursor.block()
      line_text = block.text()
      pos = cursor.selectionStart()
      # The line begins with a "[HH:MM] " timestamp inserted by
      # _insert_timestamp(). Pull it out for the When column.
      ts = ''
      m = re.match(r'^(\[\d{1,2}:\d{2}(?::\d{2})?\])', line_text)
      if m:
        ts = m.group(1)
      matches.append((line_text, pos, ts))
      if len(matches) >= _MAX_PER_WINDOW:
        break
    return matches

  def _search_history(self, query, case_sensitive, py_pat):
    """Search the history table. Returns a dict mapping (network, channel)
    to a list of (ts, nick, text) tuples, capped at _MAX_HISTORY_ROWS overall."""
    db = state.historydb
    if db is None:
      return {}
    try:
      # read_conn() drains the history writer before handing the connection
      # over: a search that silently omits the last few minutes of a
      # conversation is worse than one that waits a moment for them.
      conn = db.read_conn()
    except AttributeError:
      return {}
    groups = {}
    total = 0
    try:
      if py_pat is not None:
        cur = conn.execute(
          "SELECT ts, network, channel, nick, text FROM history "
          "ORDER BY id DESC LIMIT 200000")
        for ts, net, chan, nick, text in cur:
          if total >= _MAX_HISTORY_ROWS:
            break
          if not text:
            continue
          if py_pat.search(text):
            groups.setdefault((net, chan), []).append((ts, nick, text))
            total += 1
      else:
        like = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        if case_sensitive:
          # SQLite LIKE is case-insensitive for ASCII by default. Force a
          # case-sensitive comparison via GLOB-like trick using INSTR.
          cur = conn.execute(
            "SELECT ts, network, channel, nick, text FROM history "
            "WHERE text IS NOT NULL AND INSTR(text, ?) > 0 "
            "ORDER BY id DESC LIMIT ?",
            (query, _MAX_HISTORY_ROWS))
        else:
          cur = conn.execute(
            "SELECT ts, network, channel, nick, text FROM history "
            "WHERE text LIKE ? ESCAPE '\\' "
            "ORDER BY id DESC LIMIT ?",
            (like, _MAX_HISTORY_ROWS))
        for ts, net, chan, nick, text in cur:
          groups.setdefault((net, chan), []).append((ts, nick, text))
          total += 1
    except Exception as e:
      self._status.setText('History DB error: %s' % e)
      return {}
    # Reverse each group so oldest -> newest within a channel.
    for k in groups:
      groups[k].reverse()
    return groups

  def _find_live_window_for(self, network, channel):
    """Find an open window matching a (network, channel) pair from the
    history DB. Channel may be a "#channel" or a "=nick:ident" query key."""
    if not channel:
      return None
    chan_lower = channel.lower()
    is_query_key = chan_lower.startswith('=')
    for client in list(state.clients or []):
      cnet = client.network or ''
      if network and cnet.lower() != (network or '').lower():
        continue
      if is_query_key:
        # =nick:ident — match by nick prefix
        nick_part = chan_lower[1:].split(':', 1)[0]
        for q in client.queries.values():
          if q.nick and q.nick.lower() == nick_part:
            return q.window
      else:
        for chan in client.channels.values():
          if chan.name and chan.name.lower() == chan_lower:
            return chan.window
    return None

  # ------------------------------------------------------------------ jump

  def _on_result_activated(self, item, column):
    data = item.data(0, Qt.ItemDataRole.UserRole)
    if not data:
      return
    kind = data[0]
    if kind == 'live':
      _, win, position, query = data
      self._activate_window(win)
      self._highlight_at_position(win, position, query)
    elif kind == 'history':
      _, win, text, query = data
      if win is None:
        self._status.setText(
          'No open window for that history entry — open the channel first.')
        return
      self._activate_window(win)
      # The historical line may or may not be in the live buffer. Try to
      # find it; if not present, just activate the window.
      if not self._highlight_text(win, text):
        self._status.setText(
          'Match is older than the live buffer — only window activated.')

  def _activate_window(self, win):
    ws = state.app.mainwin.workspace if state.app else None
    if ws is None:
      return
    sub = getattr(win, 'subwindow', None)
    if sub is not None and hasattr(ws, 'setActiveSubWindow'):
      try:
        ws.setActiveSubWindow(sub)
      except Exception:
        pass

  def _highlight_at_position(self, win, position, query):
    output = getattr(win, 'output', None)
    if output is None:
      return
    try:
      doc = output.document()
    except RuntimeError:
      return
    cursor = QTextCursor(doc)
    cursor.setPosition(position)
    cursor.setPosition(position + len(query), QTextCursor.MoveMode.KeepAnchor)
    self._apply_highlight(output, cursor)

  def _highlight_text(self, win, text):
    output = getattr(win, 'output', None)
    if output is None:
      return False
    try:
      doc = output.document()
    except RuntimeError:
      return False
    cursor = doc.find(text)
    if cursor.isNull() or not cursor.hasSelection():
      return False
    self._apply_highlight(output, cursor)
    return True

  def _apply_highlight(self, output, cursor):
    sel = QTextEdit.ExtraSelection()
    sel.cursor = cursor
    fmt = QTextCharFormat()
    try:
      fmt.setBackground(state.config.color_search_bg)
      fmt.setForeground(state.config.color_search_fg)
    except Exception:
      pass
    sel.format = fmt
    output.setExtraSelections([sel])
    output.setTextCursor(cursor)
    output.ensureCursorVisible()
