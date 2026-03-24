#structure:
#  Client instances - each client is associated with a main server window and all its attendant windows, like in mIRC
#    window <- main server window, the gui part
#    channels
#    queries
#    conn <- underlying IRC connection (IRCClient instance). can be None if not connected.
#
#  Channel/Query instances
#    window <- the gui part
#    client <- points to its parent client instance
#    other info associated with a channel/query window that's not directly GUI-related is stored in the Channel instance,
#     not the window instance
#
#  IRCClient instances
#    client <- points to its parent Client instance
#    window  <- points to parent Client instance's window.  just for convenience.
#    channels <- points to parent Client instance's channels. just for convenience.
#    queries <- points to parent Client instance's queries.  just for convenience.
#    nickname <- nickname currently being used.
#
#  Script instances
#    module <- the script's entire module
#    script <- the script module's running Script() instance

from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *

import sys, os, asyncio, argparse, signal

APP_NAME = 'qtpyrc'
APP_VERSION = '0.1.0'

import state
from config import loadconfig, UIState
from models import Client, newclient
from window import NetworkTree
from dialogs import _validate_font, open_settings
from tabbar import TabbedWorkspace
from toolbar import build_toolbar


# ---------------------------------------------------------------------------
# App construction helpers
# ---------------------------------------------------------------------------

def _build_app_stylesheet():
  """Build a global stylesheet from config colors."""
  cfg = state.config
  fg = cfg.fgcolor.name()
  bg = cfg.bgcolor.name()
  # Menu colors
  mfg = cfg.menu_fgcolor.name()
  mbg = cfg.menu_bgcolor.name()
  m_disabled = '#%02x%02x%02x' % (
    (cfg.menu_fgcolor.red() + cfg.menu_bgcolor.red()) // 2,
    (cfg.menu_fgcolor.green() + cfg.menu_bgcolor.green()) // 2,
    (cfg.menu_fgcolor.blue() + cfg.menu_bgcolor.blue()) // 2)
  # Tree colors
  tfg = cfg.tree_fgcolor.name()
  tbg = cfg.tree_bgcolor.name()
  # Nick list colors
  nfg = cfg.nicklist_fgcolor.name()
  nbg = cfg.nicklist_bgcolor.name()
  parts = [
    "QMainWindow { background-color: %s; color: %s; }" % (bg, fg),
  ]
  # Menu font
  menu_font = ''
  if cfg.menu_font_family:
    menu_font += "font-family: '%s'; " % cfg.menu_font_family
  if cfg.menu_font_size:
    menu_font += "font-size: %dpt; " % cfg.menu_font_size
  parts.append("QMenuBar { background-color: %s; color: %s; padding: 0px; %s}" % (mbg, mfg, menu_font))
  parts.append("QMenuBar::item { padding: 4px 8px; }")
  parts.append("QMenuBar::item:selected { background-color: %s; color: %s; }" % (mfg, mbg))
  parts.append("QMenu { background-color: %s; color: %s; border: 1px solid %s; padding: 2px; %s}" % (mbg, mfg, mfg, menu_font))
  parts.append("QMenu::item { padding: 4px 12px; }")
  parts.append("QMenu::item:disabled { color: %s; }" % m_disabled)
  parts.append("QMenu::item:selected { background-color: %s; color: %s; }" % (mfg, mbg))
  parts.append("QTextEdit { background-color: %s; color: %s; }" % (bg, fg))
  # Tree font
  tree_font = ''
  if cfg.tree_font_family:
    tree_font += "font-family: '%s'; " % cfg.tree_font_family
  if cfg.tree_font_size:
    tree_font += "font-size: %dpt; " % cfg.tree_font_size
  parts.append("QTreeWidget { background-color: %s; color: %s; %s}" % (tbg, tfg, tree_font))
  parts.append("QTreeWidget::item:selected { background-color: %s; color: %s; }" % (tfg, tbg))
  # Nick list font
  nicks_font = ''
  if cfg.nicklist_font_family:
    nicks_font += "font-family: '%s'; " % cfg.nicklist_font_family
  if cfg.nicklist_font_size:
    nicks_font += "font-size: %dpt; " % cfg.nicklist_font_size
  parts.append("QListWidget#nicklist { background-color: %s; color: %s; %s}" % (nbg, nfg, nicks_font))
  parts.append("QMdiArea { background-color: %s; }" % bg)
  return ' '.join(parts)


def _refresh_all_window_fonts():
  """Update font on all open chat windows (output, input, nick list)."""
  if not state.clients:
    return
  from PySide6.QtGui import QFont, QFontMetrics
  cfg = state.config
  f = QFont(cfg.fontfamily, cfg.fontheight)
  # Nick list font: use dedicated setting if configured, otherwise chat font
  if cfg.nicklist_font_family or cfg.nicklist_font_size:
    nf = QFont(cfg.nicklist_font_family or cfg.fontfamily,
               cfg.nicklist_font_size or cfg.fontheight)
  else:
    nf = f
  lines = max(1, cfg.input_lines)
  for client in state.clients:
    for win in _iter_windows(client):
      win.output.setFont(f)
      win.input.setFont(f)
      win.input.setFixedHeight(QFontMetrics(f).height() * lines + 10)
      if hasattr(win, 'nicklist'):
        win.nicklist.setFont(nf)


def _refresh_navigation(mw=None):
  """Show/hide tabs and tree sidebar based on config.navigation."""
  cfg = state.config
  if mw is None:
    if not state.app:
      return
    mw = state.app.mainwin
  from tabbar import TabbedWorkspace
  if isinstance(mw.workspace, TabbedWorkspace):
    mw.workspace.set_tabs_visible(cfg.show_tabs)
  if mw.network_tree:
    mw.network_tree.setVisible(cfg.show_tree)


def _iter_windows(client):
  """Yield all open windows for a client."""
  if client.window:
    yield client.window
  for chan in client.channels.values():
    if chan.window:
      yield chan.window
  for query in client.queries.values():
    if query.window:
      yield query.window


def _get_message_colors():
  """Return a dict of color-name -> QColor for colors baked into rich text."""
  cfg = state.config
  return {
    'fg': QColor(cfg.fgcolor),
    'bg': QColor(cfg.bgcolor),
    'system': QColor(cfg.color_system),
    'info': QColor(cfg.color_info),
    'action': QColor(cfg.color_action),
    'notice': QColor(cfg.color_notice),
  }


def _recolor_chat_text(old_colors, visible_only=True):
  """Swap old message colors for new ones in chat windows.

  old_colors: dict from _get_message_colors() captured before the change.
  visible_only: if True, only recolor visible blocks (live preview).
  """
  if not state.clients:
    return
  new_colors = _get_message_colors()
  # Build fg substitution map: old hex -> new QColor
  fg_map = {}
  bg_map = {}
  for key in ('fg', 'system', 'info', 'action', 'notice'):
    if old_colors[key] != new_colors[key]:
      fg_map[old_colors[key].name()] = new_colors[key]
  if old_colors['bg'] != new_colors['bg']:
    bg_map[old_colors['bg'].name()] = new_colors['bg']
  if not fg_map and not bg_map:
    return
  for client in state.clients:
    for win in _iter_windows(client):
      _recolor_document(win.output, fg_map, bg_map, visible_only)


def _recolor_document(text_edit, fg_map, bg_map, visible_only):
  """Swap colors in a QTextEdit's document."""
  from PySide6.QtGui import QTextCursor, QTextCharFormat, QBrush
  doc = text_edit.document()
  if visible_only:
    # QTextEdit doesn't have firstVisibleBlock(); find it via cursor position
    top_cursor = text_edit.cursorForPosition(QPoint(0, 0))
    bottom_cursor = text_edit.cursorForPosition(
        QPoint(0, text_edit.viewport().height()))
    first_block = top_cursor.block()
    last_num = bottom_cursor.block().blockNumber()
  else:
    first_block = doc.begin()
    last_num = doc.blockCount() - 1
  cursor = QTextCursor(doc)
  cursor.beginEditBlock()
  block = first_block
  while block.isValid() and block.blockNumber() <= last_num:
    it = block.begin()
    while not it.atEnd():
      frag = it.fragment()
      if frag.isValid():
        fmt = frag.charFormat()
        fg_name = fmt.foreground().color().name()
        bg_name = fmt.background().color().name()
        need_update = False
        new_fmt = QTextCharFormat()
        if fg_name in fg_map:
          new_fmt.setForeground(QBrush(fg_map[fg_name]))
          need_update = True
        if bg_name in bg_map:
          new_fmt.setBackground(QBrush(bg_map[bg_name]))
          need_update = True
        if need_update:
          cursor.setPosition(frag.position())
          cursor.setPosition(frag.position() + frag.length(),
                             QTextCursor.MoveMode.KeepAnchor)
          cursor.mergeCharFormat(new_fmt)
      it += 1
    block = block.next()
  cursor.endEditBlock()


class _MenuTooltipFilter(QObject):
  """Event filter that shows tooltips on disabled menu items."""
  def eventFilter(self, obj, event):
    if event.type() == QEvent.Type.ToolTip and isinstance(obj, QMenu):
      action = obj.actionAt(event.pos())
      if action and action.toolTip():
        QToolTip.showText(event.globalPos(), action.toolTip(), obj)
        return True
      else:
        QToolTip.hideText()
        return True
    return super().eventFilter(obj, event)

def _tile_vertically():
  """Tile subwindows vertically (stacked top-to-bottom). MDI only."""
  ws = state.app.mainwin.workspace
  if not hasattr(ws, 'viewport'):
    return
  subs = ws.subWindowList()
  if not subs:
    return
  vp = ws.viewport()
  w = vp.width()
  h = vp.height() // len(subs)
  for i, sub in enumerate(subs):
    sub.showNormal()
    sub.setGeometry(0, i * h, w, h)

def _on_treeview_splitter_moved(pos, index):
  if state.ui_state:
    sizes = state.app.mainwin._tree_splitter.sizes()
    if len(sizes) >= 2:
      state.ui_state.treeview_width = sizes[0]

_DEFAULT_TITLEBAR_FORMAT = (
  'qtpyrc{eval("'
  "' - ' + ', '.join("
  "sorted('%s (%s)' % (c.network_key or c.network or c.hostname, c.conn.nickname) "
  "for c in state.clients if c.connected)) "
  "if any(c.connected for c in state.clients) else ''"
  '")}'
)

def update_main_title():
  """Update the main window title bar by expanding titlebar_format."""
  if not state.app or not state.app.mainwin:
    return
  from config import _expand_vars
  from commands import _window_context_vars
  custom = getattr(state.app.mainwin, '_custom_titlebar', None)
  fmt = custom or (state.config.titlebar_format if state.config else '') or _DEFAULT_TITLEBAR_FORMAT
  ws = state.app.mainwin.workspace
  sub = ws.activeSubWindow() if ws else None
  widget = sub.widget() if sub else None
  if widget:
    variables = _window_context_vars(widget)
  else:
    variables = _window_context_vars(type('_Dummy', (), {'client': None})())
  variables.update(state._variables)
  title = _expand_vars(fmt, variables, allow_eval=True,
                       eval_ns={'state': state})
  state.app.mainwin.setWindowTitle(title)

def _refresh_window_titles():
  """Re-expand title formats on all windows (custom and config-based)."""
  if not state.clients:
    return
  from commands import expand_window_title
  for client in state.clients:
    if client.window:
      if getattr(client.window, '_custom_title', None) is not None:
        client.window.refresh_custom_title()
      else:
        fmt = state.config.title_server if client.connected else state.config.title_server_disconnected
        client.window.setWindowTitle(expand_window_title(fmt, client.window))
    for chan in client.channels.values():
      if chan.window:
        if getattr(chan.window, '_custom_title', None) is not None:
          chan.window.refresh_custom_title()
        else:
          chan.update_title()
    for query in client.queries.values():
      if query.window:
        if getattr(query.window, '_custom_title', None) is not None:
          query.window.refresh_custom_title()
        else:
          query.update_title()

def _update_all_titles():
  """Refresh main titlebar and all window titles."""
  update_main_title()
  _refresh_window_titles()

def _on_subwindow_activated(subwindow):
  """Sync the treeview selection and clear activity when switching windows."""
  if not subwindow:
    return
  widget = subwindow.widget()
  if not widget:
    return
  # Clear activity highlight on the now-active window
  if hasattr(widget, 'clear_activity'):
    widget.clear_activity()
  # Focus the input field
  if hasattr(widget, 'input'):
    widget.input.setFocus()
  # Sync treeview selection
  tree = getattr(state.app.mainwin, 'network_tree', None)
  if tree:
    tree.sync_to_window(widget)
  _update_all_titles()

def _close_active_window():
  """Close the active in-app window (Ctrl+F4)."""
  ws = state.app.mainwin.workspace
  sub = ws.activeSubWindow() if ws else None
  widget = sub.widget() if sub else None
  if widget and hasattr(widget, 'type') and widget.client:
    _close_window(widget)


def _close_window(widget, force=False):
  """Close a window.  Channels: part and close.  Queries: close.
  Server windows: confirm (unless *force*), disconnect, and close."""
  ws = state.app.mainwin.workspace
  if not widget or not hasattr(widget, 'type') or not widget.client:
    return
  client = widget.client
  conn = client.conn
  sub = widget.subwindow

  if widget.type == 'channel' and widget.channel:
    chan = widget.channel
    if conn:
      conn.leave(chan.name)
    chnlower = (conn.irclower(chan.name) if conn else chan.name.lower())
    if chnlower in client.channels:
      chan.active = False
      del client.channels[chnlower]
      tree = getattr(state.app.mainwin, 'network_tree', None)
      if tree:
        tree.remove_channel(client, chan)
      ws.removeSubWindow(sub)

  elif widget.type == 'query' and widget.query:
    for qkey, q in list(client.queries.items()):
      if q is widget.query:
        del client.queries[qkey]
        break
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.remove_query(client, widget.query)
    ws.removeSubWindow(sub)

  elif widget.type == 'server':
    has_children = bool(client.channels or client.queries)
    if not force and (conn or has_children):
      label = client.network_key or client.network or client.hostname or 'server'
      if conn and has_children:
        msg = 'Disconnect from %s and close all its windows?' % label
      elif conn:
        msg = 'Disconnect from %s?' % label
      else:
        msg = 'Close %s and all its windows?' % label
      reply = QMessageBox.question(
        state.app.mainwin, 'Close Server', msg,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
      if reply != QMessageBox.StandardButton.Yes:
        return
    if conn:
      conn.quit('Closing')
    for chan in list(client.channels.values()):
      tree = getattr(state.app.mainwin, 'network_tree', None)
      if tree:
        tree.remove_channel(client, chan)
      ws.removeSubWindow(chan.window.subwindow)
    client.channels.clear()
    for qkey, q in list(client.queries.items()):
      tree = getattr(state.app.mainwin, 'network_tree', None)
      if tree:
        tree.remove_query(client, q)
      ws.removeSubWindow(q.window.subwindow)
    client.queries.clear()
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.remove_client(client)
    ws.removeSubWindow(sub)
    if client in state.clients:
      state.clients.remove(client)

class _AppKeyFilter(QObject):
  """Application-level event filter for global key bindings."""
  def eventFilter(self, obj, event):
    if event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
      key = event.key()
      mods = event.modifiers()
      ctrl = mods & Qt.KeyboardModifier.ControlModifier
      alt = mods & Qt.KeyboardModifier.AltModifier

      # Alt+F4: close the entire application
      if key == Qt.Key.Key_F4 and alt:
        if event.type() == QEvent.Type.ShortcutOverride:
          event.accept()
          return True
        QApplication.instance().quit()
        return True

      # Ctrl+F4: close the active in-app window (part channel, close query)
      # Skip when a modal dialog is active — let the dialog handle it
      if key == Qt.Key.Key_F4 and ctrl:
        if QApplication.activeModalWidget():
          return False
        if event.type() == QEvent.Type.ShortcutOverride:
          event.accept()
          return True
        _close_active_window()
        return True

      # Ctrl+Tab / Ctrl+Shift+Tab: cycle tabs
      if key == Qt.Key.Key_Tab and ctrl:
        ws = state.app.mainwin.workspace
        if hasattr(ws, 'cycle_tab'):
          if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
          forward = not (mods & Qt.KeyboardModifier.ShiftModifier)
          ws.cycle_tab(forward=forward)
          return True
      if key == Qt.Key.Key_Backtab and ctrl:
        ws = state.app.mainwin.workspace
        if hasattr(ws, 'cycle_tab'):
          if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
          ws.cycle_tab(forward=False)
          return True

    # On Windows, Alt+F4 sends WM_CLOSE directly — arrives as QCloseEvent,
    # not a key event. Intercept close on child windows when Alt is held.
    if (event.type() == QEvent.Type.Close
        and isinstance(obj, QWidget)
        and obj.isWindow()
        and obj is not state.app.mainwin):
      if QApplication.queryKeyboardModifiers() & Qt.KeyboardModifier.AltModifier:
        obj.close()
        QTimer.singleShot(0, QApplication.instance().quit)
        return True

    return False

def makeapp(args):
  app = QApplication(args)
  app.setStyleSheet(_build_app_stylesheet())
  app.mainwin = QMainWindow()
  app.mainwin._custom_titlebar = None
  # Apply initial view mode
  if state.config.view_mode == 'mdi':
    app.mainwin.workspace = QMdiArea()
    app.mainwin.workspace.setViewMode(QMdiArea.ViewMode.SubWindowView)
  else:
    app.mainwin.workspace = TabbedWorkspace()
    tab_rows = state.config.tab_rows
    if tab_rows:
      app.mainwin.workspace.set_max_rows(tab_rows)
    if not state.config.show_tabs:
      app.mainwin.workspace.set_tabs_visible(False)

  content = app.mainwin.workspace

  app.mainwin.network_tree = NetworkTree()
  app.mainwin._tree_splitter = QSplitter()
  app.mainwin._tree_splitter.addWidget(app.mainwin.network_tree)
  app.mainwin._tree_splitter.addWidget(content)
  tw = state.ui_state.treeview_width if state.ui_state else 180
  app.mainwin._tree_splitter.setSizes([tw, 600])
  app.mainwin._tree_splitter.splitterMoved.connect(_on_treeview_splitter_moved)
  app.mainwin.setCentralWidget(app.mainwin._tree_splitter)
  _refresh_navigation(app.mainwin)

  app.mainwin.workspace.subWindowActivated.connect(_on_subwindow_activated)

  # Install app-level event filter for Ctrl+Tab (Qt intercepts it before widgets)
  app._key_filter = _AppKeyFilter(app)
  app.installEventFilter(app._key_filter)


  # --- Menu bar ---
  app.mainwin.menubar = app.mainwin.menuBar()

  # File menu
  app.mainwin.mnufile = app.mainwin.menubar.addMenu('&File')
  app.mainwin.mnusettings = app.mainwin.mnufile.addAction('&Settings')
  app.mainwin.mnusettings.triggered.connect(open_settings)
  mnuedit = app.mainwin.mnufile.addMenu('&Edit files')
  for label, page_key in [('&Startup commands', 'startup'), ('&Popups', 'popups'),
                           ('&Toolbar', 'toolbar'), ('&Variables', 'variables'),
                           ('&Config', 'config')]:
    mnuedit.addAction(label).triggered.connect(
      lambda checked=False, k=page_key: _open_editor_file(k))
  mnuedit.addSeparator()
  mnuedit.addAction('&Open file...').triggered.connect(
    lambda: _open_editor_file(None))
  mnuedit.addAction('&File editor').triggered.connect(
    lambda: _open_editor_file(''))
  app.mainwin.mnufile.addSeparator()
  app.mainwin.mnufile.addAction('&Reload configuration').triggered.connect(lambda: _reload_config())
  app.mainwin.mnufile.addAction('Save configuration &as...').triggered.connect(lambda: _save_config_as())
  app.mainwin.mnufile.addSeparator()
  app.mainwin.mnuclose = app.mainwin.mnufile.addAction('&Close')
  app.mainwin.mnunew = app.mainwin.mnufile.addMenu("&New")
  app.mainwin.mnunewclient = app.mainwin.mnunew.addAction("&Server window")
  app.mainwin.mnunewclient.triggered.connect(newclient)

  # Event filter for tooltips on disabled menu items
  _menu_tt_filter = _MenuTooltipFilter(app.mainwin)

  # Window menu
  mnuwindow = app.mainwin.menubar.addMenu('&Window')
  _is_mdi = state.config.view_mode == 'mdi'
  _act = mnuwindow.addAction('Tile &Horizontally', lambda:
    app.mainwin.workspace.tileSubWindows())
  _act.setEnabled(_is_mdi)
  _act = mnuwindow.addAction('Tile &Vertically', _tile_vertically)
  _act.setEnabled(_is_mdi)
  _act = mnuwindow.addAction('&Cascade', lambda:
    app.mainwin.workspace.cascadeSubWindows())
  _act.setEnabled(_is_mdi)
  if not _is_mdi:
    _info = QWidgetAction(mnuwindow)
    _lbl = QLabel('\u2139 Requires MDI mode \u2014 click to change')
    _lbl.setStyleSheet('color: #6688cc; padding: 4px 12px;')
    _lbl.setCursor(Qt.CursorShape.PointingHandCursor)
    _lbl.mousePressEvent = lambda e: (mnuwindow.close(), open_settings('general'))
    _info.setDefaultWidget(_lbl)
    mnuwindow.addAction(_info)

  # Tools menu
  mnutools = app.mainwin.menubar.addMenu('&Tools')
  mnutools.addAction('&URL Catcher').triggered.connect(
    lambda: __import__('url_catcher').show_url_catcher())
  mnutools.addAction('&Sound Browser').triggered.connect(
    lambda: __import__('notify').show_sound_browser())
  mnutools.addAction('&Icon Browser').triggered.connect(
    lambda: __import__('toolbar').show_icon_browser())

  # Help menu
  mnuhelp = app.mainwin.menubar.addMenu('&Help')
  mnuhelp.installEventFilter(_menu_tt_filter)
  _basedir = os.path.dirname(os.path.abspath(__file__))
  _ref_path = os.path.join(_basedir, 'docs', 'reference.md')
  _act = mnuhelp.addAction('&Reference Manual', lambda p=_ref_path: _show_doc_viewer(p))
  if not os.path.isfile(_ref_path):
    _act.setEnabled(False)
    _act.setToolTip('File not found: %s' % _ref_path)
  _example_path = os.path.join(_basedir, 'config.example.yaml')
  _act = mnuhelp.addAction('&Config Reference', lambda p=_example_path: _show_doc_viewer(p))
  if not os.path.isfile(_example_path):
    _act.setEnabled(False)
    _act.setToolTip('File not found: %s' % _example_path)
  mnuhelp.addSeparator()
  mnuhelp.addAction('&About', _show_about)

  # --- Toolbar ---
  if state.config.show_toolbar:
    app.mainwin._toolbar = build_toolbar(app.mainwin)
    app.mainwin.addToolBar(app.mainwin._toolbar)
  else:
    app.mainwin._toolbar = None

  _update_all_titles()
  # Poll on a timer since eval expressions in titlebar_format can reference
  # anything and we can't know which events should trigger updates.
  # Immediate updates also fire on connect/disconnect/nick change for responsiveness.
  app.mainwin._titlebar_timer = QTimer()
  app.mainwin._titlebar_timer.timeout.connect(_update_all_titles)
  app.mainwin._titlebar_timer.start(state.config.titlebar_interval * 1000)
  app.mainwin.resize(1024, 768)
  if state.config.window_mode == 'maximized':
    app.mainwin.showMaximized()
  else:
    app.mainwin.show()
  app.mainwin.raise_()
  app.mainwin.activateWindow()
  app.lastWindowClosed.connect(quit)
  return app

def _startup_path():
  """Return the path to the startup commands file, or None if not configured."""
  name = state.config.startup_file if state.config else ''
  if not name:
    return None
  if os.path.isabs(name):
    return name
  from commands import _resolve_cmdscripts_dir
  return os.path.join(_resolve_cmdscripts_dir(), name)

def _open_editor_file(key):
  """Open the Settings dialog on the File Editor page with a specific file.

  If *key* is None, the editor opens with a file browse dialog.
  """
  from settings.settings_dialog import SettingsDialog
  dlg = SettingsDialog(state.config, parent=state.app.mainwin)
  dlg.select_page('editor')
  editor_page = dlg._pages.get('editor')
  if editor_page:
    if key is None:
      editor_page._browse()
    elif key:
      editor_page._open_quick(key)
    editor_page.editor.setFocus()
  dlg.exec()

def _reload_config():
  """Re-read the current configuration file."""
  from config import loadconfig
  try:
    cfg = loadconfig(state.config.path)
  except Exception as e:
    QMessageBox.warning(state.app.mainwin, "Reload Failed",
                        "Error reloading config:\n%s" % e)
    return
  state.config = cfg

def _save_config_as():
  """Save configuration to a new YAML file and switch to it."""
  from PySide6.QtWidgets import QFileDialog
  path, _ = QFileDialog.getSaveFileName(
    state.app.mainwin, "Save Configuration As",
    os.path.dirname(os.path.abspath(state.config.path)),
    "YAML files (*.yaml *.yml);;All files (*)")
  if not path:
    return
  old_path = state.config.path
  state.config.path = path
  state.config.save()
  state.config.path = path  # ensure it stays

def _show_doc_viewer(path):
  """Open a document in a simple in-app viewer.  Renders .md as markdown."""
  try:
    with open(path, 'r', encoding='utf-8') as f:
      text = f.read()
  except OSError:
    return
  title = os.path.basename(path)
  dlg = QDialog(state.app.mainwin)
  dlg.setWindowTitle(title)
  dlg.resize(700, 600)
  layout = QVBoxLayout(dlg)
  browser = QTextBrowser()
  browser.setOpenExternalLinks(True)
  if path.endswith('.md'):
    browser.setMarkdown(text)
  else:
    browser.setPlainText(text)
  layout.addWidget(browser)
  btn = QPushButton('Close')
  btn.clicked.connect(dlg.close)
  layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)
  dlg.setModal(False)
  dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
  dlg.show()
  # Prevent GC
  if not hasattr(state.app.mainwin, '_doc_viewers'):
    state.app.mainwin._doc_viewers = {}
  state.app.mainwin._doc_viewers[path] = dlg
  dlg.destroyed.connect(lambda: state.app.mainwin._doc_viewers.pop(path, None))

def _show_about():
  import PySide6
  QMessageBox.about(state.app.mainwin, 'About %s' % APP_NAME,
    '<h2>%s %s</h2>'
    '<p>A cross-platform PySide6 IRC client.</p>'
    '<p>By Richard Albert Nichols III (Inhahe)<br>'
    'with Claude Opus 4.6 via Claude Code (Anthropic)</p>'
    '<p><b>Current environment (not minimum requirements):</b><br>'
    'Python %s<br>'
    'Qt %s<br>'
    'PySide6 %s<br>'
    'Platform: %s</p>'
    '<p><small>MIT License</small></p>'
    % (APP_NAME, APP_VERSION,
       sys.version.split()[0],
       PySide6.QtCore.qVersion(),
       PySide6.__version__,
       sys.platform))

def quit():
  if state.ui_state:
    state.ui_state.save()
  # Close all IRC connections
  for client in list(state.clients or []):
    if client.conn:
      client.conn.disconnect()
  # Close history database
  if state.historydb:
    try:
      state.historydb.close()
    except Exception:
      pass
  loop = asyncio.get_event_loop()
  # Cancel pending async tasks
  for task in asyncio.all_tasks(loop):
    task.cancel()
  loop.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
  import qasync
  from logger import IRCLogger
  from plugins import loadscripts, apply_hooks, init_irc
  from ident import runidentd

  # --- CLI arguments ---
  parser = argparse.ArgumentParser(description='qtpyrc IRC client')
  parser.add_argument('-c', '--config', default=None,
                      help='Path to YAML configuration file')
  parser.add_argument('-d', '--debug', type=int, default=None,
                      help='Debug output level (0=silent, 1=error, 2=warn, 3=info, 4=debug, 5=trace)')
  # Script/plugin control
  parser.add_argument('--startup', default=None,
                      help='Startup script to run instead of the configured one')
  parser.add_argument('-r', '--run', action='append', default=[],
                      help='Additional command script to run (repeatable, wildcards)')
  parser.add_argument('--no-startup', action='store_true',
                      help='Suppress loading the startup script')
  parser.add_argument('--no-scripts', action='append', default=[],
                      help='Suppress autoload scripts matching pattern (repeatable, wildcards)')
  parser.add_argument('-p', '--plugin', action='append', default=[],
                      help='Additional plugin to load (repeatable, wildcards)')
  parser.add_argument('--no-plugins', action='append', default=[],
                      help='Suppress autoload plugins matching pattern (repeatable, wildcards)')
  parser.add_argument('-e', '--exec', action='append', default=[], dest='exec_cmds',
                      help='Execute a /command on startup (repeatable)')
  cli_args, qt_args = parser.parse_known_args()

  mypath = os.path.dirname(os.path.abspath(__file__))
  configpath = cli_args.config or os.path.join(mypath, "config.yaml")
  if not os.path.isfile(configpath):
    with open(configpath, 'w') as f:
      f.write('# qtpyrc configuration\n'
              '# See config.example.yaml for all available options.\n')
  state.config = loadconfig(configpath)
  ui_name = state.config.ui_state_file
  if not os.path.isabs(ui_name):
    ui_name = os.path.join(os.path.dirname(os.path.abspath(state.config.path)), ui_name)
  state.ui_state = UIState(ui_name)

  # Debug level: CLI overrides config
  if cli_args.debug is not None:
    state.debug_level = cli_args.debug
  elif state.config.log_debug:
    state.debug_level = state.LOG_DEBUG

  # --- Logger ---
  state.irclogger = IRCLogger(state.config, mypath)

  # --- History DB ---
  from history import HistoryDB
  state.historydb = HistoryDB(os.path.join(mypath, "history.db"),
                              keep_limit=state.config.backscroll_limit)

  # --- Qt app ---
  state.app = makeapp([sys.argv[0]] + qt_args)
  loop = qasync.QEventLoop(state.app)
  asyncio.set_event_loop(loop)

  # --- Font validation ---
  _validate_font(state.config)

  # --- Apply plugin hooks to IRCClient ---
  apply_hooks()

  # --- Initialise plugin.irc singleton and auto-connect ---
  state.clients = set()
  init_irc()
  if state.config.networks:
    for netkey in state.config.networks:
      if state.config.resolve(netkey, 'auto_connect'):
        client = Client(network_key=netkey)
        state.clients.add(client)
        asyncio.ensure_future(client.connect_to_server())

  # If no auto-connect networks, create a single empty server window
  if not state.clients:
    state.clients.add(Client())

  # --- Notifications ---
  from notify import NotificationManager
  from PySide6.QtWidgets import QSystemTrayIcon
  from PySide6.QtGui import QIcon
  state.notifications = NotificationManager()
  if QSystemTrayIcon.isSystemTrayAvailable():
    _icon = state.app.mainwin.windowIcon()
    if _icon.isNull():
      _icon = QIcon()
    state.tray_icon = QSystemTrayIcon(_icon, state.app.mainwin)
    if not _icon.isNull():
      state.tray_icon.show()
  state.notifications.start_polling()

  # --- Variables ---
  state.load_variables()

  # --- Popups ---
  import popups
  popups.load()

  # --- Plugins (Python scripts) ---
  scripts = loadscripts(suppress=cli_args.no_plugins or None,
                        extra=cli_args.plugin or None)
  state.activescripts = dict(scripts)

  # --- Startup commands & command scripts ---
  from commands import run_script
  win = next(iter(state.clients)).window if state.clients else None
  # Startup commands file
  if not cli_args.no_startup:
    if cli_args.startup:
      run_script(cli_args.startup, win)
    else:
      startup = _startup_path()
      if startup and os.path.isfile(startup):
        run_script(startup, win)
  # Additional command scripts from config (with suppression)
  from commands import _resolve_cmdscripts_dir
  from plugins import _expand_auto_load
  import fnmatch as _fnmatch
  cmdscripts_dir = _resolve_cmdscripts_dir()
  for name in _expand_auto_load(state.config.scripts_auto_run, cmdscripts_dir, None):
    if cli_args.no_scripts and any(_fnmatch.fnmatch(name, p) for p in cli_args.no_scripts):
      continue
    run_script(name, win)
  # Additional command scripts from CLI (supports wildcards and paths)
  for name in _expand_auto_load(cli_args.run, cmdscripts_dir, None):
    run_script(name, win)
  # CLI -e commands
  if win and cli_args.exec_cmds:
    for cmd in cli_args.exec_cmds:
      win.lineinput(cmd)

  # --- Identd ---
  asyncio.ensure_future(runidentd())

  def _sigint_handler(*_):
    print("\n^C received, quitting.")
    quit()
  signal.signal(signal.SIGINT, _sigint_handler)

  # Catch unhandled exceptions that would otherwise silently kill the window
  def _excepthook(exc_type, exc_value, exc_tb):
    import traceback
    print("*** Unhandled exception ***", file=sys.stderr)
    traceback.print_exception(exc_type, exc_value, exc_tb)
  sys.excepthook = _excepthook

  def _unraisable_hook(unraisable):
    import traceback
    print("*** Unraisable exception in %s ***" % (unraisable.object,), file=sys.stderr)
    if unraisable.exc_value:
      traceback.print_exception(type(unraisable.exc_value), unraisable.exc_value,
                                unraisable.exc_value.__traceback__)
  sys.unraisablehook = _unraisable_hook

  def _async_exception_handler(loop, context):
    exc = context.get('exception')
    msg = context.get('message', 'Unhandled async exception')
    print("*** %s ***" % msg, file=sys.stderr)
    if exc:
      import traceback
      traceback.print_exception(type(exc), exc, exc.__traceback__)
    else:
      print(context, file=sys.stderr)
  loop.set_exception_handler(_async_exception_handler)

  with loop:
    loop.run_forever()
