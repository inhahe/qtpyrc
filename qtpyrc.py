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
  if not state.app or not state.app.mainwin:
    return
  mw = state.app.mainwin
  mw._tree_user_set = True
  sizes = mw._tree_splitter.sizes()
  if len(sizes) >= 2:
    mw._tree_target_tw = sizes[0]
    if state.ui_state:
      state.ui_state.treeview_width = sizes[0]

_DEFAULT_TITLEBAR_FORMAT = (
  'qtpyrc{eval("'
  "' - ' + ', '.join("
  "sorted('%s (%s)' % (c.network_key or c.network or c.hostname, c.conn.nickname) "
  "for c in state.clients if c.connected)) "
  "if any(c.connected for c in state.clients) else ''"
  '")}'
  '{eval("'
  " ' - ' + (_v.get('network_label','') + '/' if _v.get('network_label') else '')"
  " + _v['channel']"
  " + (': ' + _v['topic'] if _v.get('topic') else '')"
  " if _v.get('channel') else ''"
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
  # Strip mIRC formatting codes from topic for titlebar display
  import re
  raw_topic = variables.get('topic', '')
  variables['topic'] = re.sub(
      r'[\x02\x03\x0F\x16\x1D\x1F]|\x03\d{0,2}(?:,\d{0,2})?', '', raw_topic) if raw_topic else ''
  variables.update(state._variables)
  # Pass variables dict into eval namespace so eval can access topic safely
  title = _expand_vars(fmt, variables, allow_eval=True,
                       eval_ns={'state': state, '_v': variables})
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
  # Run deferred history replay on first activation
  replay_info = getattr(widget, '_deferred_replay', None)
  if replay_info:
    del widget._deferred_replay
    from irc_client import _history_replay
    network, chname, chan = replay_info
    _history_replay(widget, network, chname, chan_obj=chan)
    widget.add_separator(' End of saved history ')
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

def _populate_toolbar_menu(menu=None):
  """Fill the Toolbar menu with entries matching the toolbar buttons."""
  from toolbar import _resolve_toolbar_path, _load_toolbar_file, _resolve_icon, _exec_toolbar_command
  if menu is None:
    menu = state.app.mainwin.mnutoolbar
  menu.clear()
  # Clear old ui_registry entries
  for key in [k for k in state.ui_registry if k.startswith('menu.toolbar.')]:
    del state.ui_registry[key]
  for key in [k for k in state.ui_descriptions if k.startswith('menu.toolbar.')]:
    del state.ui_descriptions[key]

  filepath = _resolve_toolbar_path()
  entries = _load_toolbar_file(filepath) if filepath else []
  if not entries:
    _a = menu.addAction('(no toolbar entries)')
    _a.setEnabled(False)
    return

  from toolbar import _toolbar_slug
  slug_counts = {}
  for entry in entries:
    if entry[0] == 'linebreak':
      menu.addSeparator()
    elif entry[0] == 'separator':
      menu.addSeparator()
    else:
      _, icon_name, tooltip, command = entry
      icon = _resolve_icon(icon_name)
      if not icon.isNull():
        _a = menu.addAction(icon, tooltip)
      else:
        _a = menu.addAction(tooltip)
      _a.triggered.connect(lambda checked, cmd=command: _exec_toolbar_command(cmd))
      slug = _toolbar_slug(tooltip)
      if slug:
        n = slug_counts.get(slug, 0) + 1
        slug_counts[slug] = n
        key = 'menu.toolbar.' + (slug if n == 1 else '%s%d' % (slug, n))
        state.ui_registry[key] = _a
        state.ui_descriptions[key] = tooltip


def _connect_network(netkey):
  """Connect to a configured network (from menu)."""
  import asyncio
  # Check if already connected
  for client in state.clients:
    if client.network_key == netkey:
      # Already have a client for this network — reconnect if disconnected
      if not client.connected:
        asyncio.ensure_future(client.connect_to_server())
      else:
        # Activate the server window
        state.app.mainwin.workspace.setActiveSubWindow(client.window.subwindow)
      return
  client = Client(network_key=netkey)
  state.clients.add(client)
  asyncio.ensure_future(client.connect_to_server())


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
      label = client.network_key or client.network or getattr(client, 'hostname', '') or 'server'
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
      # If a dialog (modal or non-modal) has focus, close it instead
      if key == Qt.Key.Key_F4 and ctrl:
        if QApplication.activeModalWidget():
          return False
        # Check if focus is inside a non-modal dialog (e.g. color picker)
        fw = QApplication.focusWidget()
        if fw:
          w = fw
          while w:
            if isinstance(w, QDialog) and w is not state.app.mainwin:
              if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
              w.close()
              return True
            w = w.parentWidget()
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


def _register_settings_paths():
  """Populate ui_registry with all settings.* entries."""
  from dialogs import open_settings
  from settings.settings_dialog import get_settings_ui_paths
  reg = state.ui_registry
  desc = state.ui_descriptions
  # Clear previous settings entries
  for key in [k for k in reg if k.startswith('settings.')]:
    del reg[key]
  for key in [k for k in desc if k.startswith('settings.')]:
    del desc[key]
  for ui_path, page_id, label in get_settings_ui_paths(state.config._data):
    reg[ui_path] = lambda pid=page_id: open_settings(page=pid)
    desc[ui_path] = label


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

  class _TreeSplitter(QSplitter):
    """QSplitter that re-applies saved tree width on resize until user drags."""
    def resizeEvent(self, event):
      super().resizeEvent(event)
      mw = state.app.mainwin if state.app else None
      if mw and not mw._tree_user_set:
        total = self.width()
        tw = mw._tree_target_tw
        if total > tw:
          self.blockSignals(True)
          self.setSizes([tw, total - tw])
          self.blockSignals(False)

  app.mainwin._tree_splitter = _TreeSplitter()
  app.mainwin._tree_splitter.addWidget(app.mainwin.network_tree)
  app.mainwin._tree_splitter.addWidget(content)
  app.mainwin._tree_target_tw = state.ui_state.treeview_width if state.ui_state else 180
  app.mainwin._tree_user_set = False
  app.mainwin._tree_splitter.setSizes([app.mainwin._tree_target_tw, 600])
  app.mainwin._tree_splitter.splitterMoved.connect(_on_treeview_splitter_moved)
  app.mainwin.setCentralWidget(app.mainwin._tree_splitter)
  _refresh_navigation(app.mainwin)

  app.mainwin.workspace.subWindowActivated.connect(_on_subwindow_activated)

  # Install app-level event filter for Ctrl+Tab (Qt intercepts it before widgets)
  app._key_filter = _AppKeyFilter(app)
  app.installEventFilter(app._key_filter)


  # --- Menu bar ---
  app.mainwin.menubar = app.mainwin.menuBar()
  # Disable Qt's default toolbar toggle context menu on the main window
  app.mainwin.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

  # File menu
  _ui = state.ui_registry
  _desc = state.ui_descriptions
  def _d(*parts):
    """Build a description from menu label parts, stripping & accelerators."""
    return ' > '.join(p.replace('&', '') for p in parts)

  app.mainwin.mnufile = app.mainwin.menubar.addMenu('&File')
  app.mainwin.mnusettings = app.mainwin.mnufile.addAction('&Settings')
  app.mainwin.mnusettings.triggered.connect(open_settings)
  _ui['menu.file.settings'] = app.mainwin.mnusettings
  _desc['menu.file.settings'] = _d('File', 'Settings')
  mnuedit = app.mainwin.mnufile.addMenu('&Edit files')
  for label, page_key, ui_key in [
      ('&Startup commands', 'startup', 'menu.file.edit.startup'),
      ('&Popups', 'popups', 'menu.file.edit.popups'),
      ('&Toolbar', 'toolbar', 'menu.file.edit.toolbar'),
      ('&Variables', 'variables', 'menu.file.edit.variables'),
      ('&Config', 'config', 'menu.file.edit.config')]:
    _a = mnuedit.addAction(label)
    _a.triggered.connect(lambda checked=False, k=page_key: _open_editor_file(k))
    _ui[ui_key] = _a
    _desc[ui_key] = _d('File', 'Edit files', label)
  mnuedit.addSeparator()
  _a = mnuedit.addAction('&Open file...')
  _a.triggered.connect(lambda: _open_editor_file(None))
  _ui['menu.file.edit.open'] = _a
  _desc['menu.file.edit.open'] = _d('File', 'Edit files', 'Open file...')
  _a = mnuedit.addAction('&File editor')
  _a.triggered.connect(lambda: _open_editor_file(''))
  _ui['menu.file.edit.editor'] = _a
  _desc['menu.file.edit.editor'] = _d('File', 'Edit files', 'File editor')
  app.mainwin.mnufile.addSeparator()
  _a = app.mainwin.mnufile.addAction('&Reload configuration')
  _a.triggered.connect(lambda: _reload_config())
  _ui['menu.file.reload'] = _a
  _desc['menu.file.reload'] = _d('File', 'Reload configuration')
  _a = app.mainwin.mnufile.addAction('Save configuration &as...')
  _a.triggered.connect(lambda: _save_config_as())
  _ui['menu.file.saveas'] = _a
  _desc['menu.file.saveas'] = _d('File', 'Save configuration as...')
  app.mainwin.mnufile.addSeparator()
  app.mainwin.mnuclose = app.mainwin.mnufile.addAction('&Close')
  _ui['menu.file.close'] = app.mainwin.mnuclose
  _desc['menu.file.close'] = _d('File', 'Close')
  app.mainwin.mnunew = app.mainwin.mnufile.addMenu("&New")
  app.mainwin.mnunewclient = app.mainwin.mnunew.addAction("&Server window")
  app.mainwin.mnunewclient.triggered.connect(newclient)
  _ui['menu.file.new.server'] = app.mainwin.mnunewclient
  _desc['menu.file.new.server'] = _d('File', 'New', 'Server window')
  # Add configured networks under New menu
  _networks = state.config.networks or {}
  app.mainwin._net_actions = {}
  if _networks:
    app.mainwin.mnunew.addSeparator()
    _used_keys = set()
    for _netkey in _networks:
      # Auto-assign & accelerator to first unique letter
      _label = _netkey
      for _ci, _ch in enumerate(_netkey):
        if _ch.lower() not in _used_keys:
          _used_keys.add(_ch.lower())
          _label = _netkey[:_ci] + '&' + _netkey[_ci:]
          break
      _a = app.mainwin.mnunew.addAction(_label)
      _a.triggered.connect(lambda checked, nk=_netkey: _connect_network(nk))
      app.mainwin._net_actions[_netkey] = (_a, _label)
      _ui_key = 'menu.file.new.' + _netkey.lower()
      _ui[_ui_key] = _a
      _desc[_ui_key] = _d('File', 'New', _netkey)
  def _update_net_menu():
    for nk, (act, label) in app.mainwin._net_actions.items():
      connected = any(c.network_key == nk and c.connected for c in state.clients)
      act.setText('%s (connected)' % label if connected else label)
  app.mainwin.mnunew.aboutToShow.connect(_update_net_menu)

  # Event filter for tooltips on disabled menu items
  _menu_tt_filter = _MenuTooltipFilter(app.mainwin)

  # View menu
  mnuview = app.mainwin.menubar.addMenu('&View')

  # Toggle toolbar
  _a_toolbar = mnuview.addAction('&Toolbar')
  _a_toolbar.setCheckable(True)
  _a_toolbar.setChecked(state.config.show_toolbar)
  def _toggle_toolbar(checked):
    state.config.show_toolbar = checked
    state.config._data['show_toolbar'] = checked
    if checked:
      if not app.mainwin._toolbar:
        app.mainwin._toolbar = build_toolbar(app.mainwin)
        app.mainwin.addToolBar(app.mainwin._toolbar)
      else:
        app.mainwin._toolbar.show()
    else:
      if app.mainwin._toolbar:
        app.mainwin._toolbar.hide()
    state.config.save()
  _a_toolbar.triggered.connect(_toggle_toolbar)
  _ui['menu.view.toolbar'] = _a_toolbar
  _desc['menu.view.toolbar'] = _d('View', 'Toolbar')

  mnuview.addSeparator()

  # Navigation submenu: Tabs Bar / Treeview / Both
  _nav_menu = mnuview.addMenu('&Navigation')
  _nav_group = QActionGroup(_nav_menu)
  _nav_group.setExclusive(True)
  _nav_items = [
      ('tabs', 'Tabs &Bar', 'menu.view.nav.tabs'),
      ('tree', '&Treeview', 'menu.view.nav.tree'),
      ('both', '&Both', 'menu.view.nav.both'),
  ]
  for _nav_val, _nav_label, _nav_key in _nav_items:
    _a = _nav_menu.addAction(_nav_label)
    _a.setCheckable(True)
    _a.setChecked(state.config.navigation == _nav_val)
    def _set_nav(checked, nav=_nav_val):
      if checked:
        state.config.navigation = nav
        state.config.show_tabs = nav in ('tabs', 'both')
        state.config.show_tree = nav in ('tree', 'both')
        state.config.treeview = state.config.show_tree
        state.config._data['navigation'] = nav
        _refresh_navigation()
        state.config.save()
    _a.triggered.connect(_set_nav)
    _nav_group.addAction(_a)
    _ui[_nav_key] = _a
    _desc[_nav_key] = _d('View', 'Navigation', _nav_label)

  # Window menu
  mnuwindow = app.mainwin.menubar.addMenu('&Window')
  _is_mdi = state.config.view_mode == 'mdi'
  _a = mnuwindow.addAction('Tile &Horizontally', lambda:
    app.mainwin.workspace.tileSubWindows())
  _a.setEnabled(_is_mdi)
  _ui['menu.window.tileh'] = _a
  _desc['menu.window.tileh'] = _d('Window', 'Tile Horizontally')
  _a = mnuwindow.addAction('Tile &Vertically', _tile_vertically)
  _a.setEnabled(_is_mdi)
  _ui['menu.window.tilev'] = _a
  _desc['menu.window.tilev'] = _d('Window', 'Tile Vertically')
  _a = mnuwindow.addAction('&Cascade', lambda:
    app.mainwin.workspace.cascadeSubWindows())
  _a.setEnabled(_is_mdi)
  _ui['menu.window.cascade'] = _a
  _desc['menu.window.cascade'] = _d('Window', 'Cascade')
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
  _a = mnutools.addAction('&URL Catcher')
  _a.triggered.connect(lambda: __import__('url_catcher').show_url_catcher())
  _ui['menu.tools.urlcatcher'] = _a
  _desc['menu.tools.urlcatcher'] = _d('Tools', 'URL Catcher')
  _a = mnutools.addAction('&Sound Browser')
  _a.triggered.connect(lambda: __import__('notify').show_sound_browser())
  _ui['menu.tools.soundbrowser'] = _a
  _desc['menu.tools.soundbrowser'] = _d('Tools', 'Sound Browser')
  _a = mnutools.addAction('&Icon Browser')
  _a.triggered.connect(lambda: __import__('toolbar').show_icon_browser())
  _ui['menu.tools.iconbrowser'] = _a
  _desc['menu.tools.iconbrowser'] = _d('Tools', 'Icon Browser')
  _a = mnutools.addAction('&Color Picker')
  _a.triggered.connect(lambda: __import__('dialogs').show_color_picker())
  _ui['menu.tools.colorpicker'] = _a
  _desc['menu.tools.colorpicker'] = _d('Tools', 'Color Picker')

  # Toolbar menu — mirrors toolbar buttons as menu items
  app.mainwin.mnutoolbar = app.mainwin.menubar.addMenu('T&oolbar')
  _populate_toolbar_menu(app.mainwin.mnutoolbar)

  # Help menu
  mnuhelp = app.mainwin.menubar.addMenu('&Help')
  mnuhelp.installEventFilter(_menu_tt_filter)
  _basedir = os.path.dirname(os.path.abspath(__file__))
  _ref_path = os.path.join(_basedir, 'docs', 'reference.md')
  _a = mnuhelp.addAction('&Reference Manual', lambda p=_ref_path: _show_doc_viewer(p))
  if not os.path.isfile(_ref_path):
    _a.setEnabled(False)
    _a.setToolTip('File not found: %s' % _ref_path)
  _ui['menu.help.reference'] = _a
  _desc['menu.help.reference'] = _d('Help', 'Reference Manual')
  _example_path = os.path.join(_basedir, 'defaults', 'config.example.yaml')
  _a = mnuhelp.addAction('&Config Reference', lambda p=_example_path: _show_doc_viewer(p))
  if not os.path.isfile(_example_path):
    _a.setEnabled(False)
    _a.setToolTip('File not found: %s' % _example_path)
  _ui['menu.help.configref'] = _a
  _desc['menu.help.configref'] = _d('Help', 'Config Reference')
  mnuhelp.addSeparator()
  _a = mnuhelp.addAction('&About', _show_about)
  _ui['menu.help.about'] = _a
  _desc['menu.help.about'] = _d('Help', 'About')

  # --- Settings pages ---
  # Register all settings page paths (global + network) from shared structure
  _register_settings_paths()

  # --- Toolbar ---
  from toolbar import register_toolbar_ui_paths
  register_toolbar_ui_paths()
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
  app.aboutToQuit.connect(quit)
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

_quitting = False
def quit():
  global _quitting
  if _quitting:
    return
  _quitting = True
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
# --init / --set helpers
# ---------------------------------------------------------------------------

# Default files and directories that live alongside config.yaml.
_DEFAULT_ANCILLARY = [
    ('history.db', False),
    ('ui.yaml', False),
    ('popups.ini', False),
    ('variables.ini', False),
    ('toolbar.ini', False),
    ('logs', True),
    ('plugins', True),
    ('scripts', True),
    ('icons', True),
]

_STUB_CONFIG = ('# qtpyrc configuration\n'
                '# See config.example.yaml for all available options.\n')

def _read_default_file(name):
  """Read a default file from the defaults/ directory."""
  path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'defaults', name)
  try:
    with open(path, 'r', encoding='utf-8') as f:
      return f.read()
  except FileNotFoundError:
    return None


def _get_default_config():
  """Build the default config.yaml from config.example.yaml.

  Comments out the networks section (has bogus example values) and
  replaces placeholder identity values.
  """
  example_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'defaults', 'config.example.yaml')
  try:
    with open(example_path, 'r', encoding='utf-8') as f:
      lines = f.readlines()
  except FileNotFoundError:
    # Fallback if config.example.yaml isn't available
    return ('# qtpyrc configuration\n'
            '# config.example.yaml not found — see docs for options.\n'
            'popups_file: popups.ini\n'
            'variables_file: variables.ini\n'
            'toolbar_file: toolbar.ini\n'
            'nick: qtpyrc_user\n'
            'user: qtpyrc\n'
            'realname: qtpyrc user\n')

  result = []
  in_networks = False
  for line in lines:
    stripped = line.rstrip('\n')
    # Detect the networks section and comment it all out
    if stripped == 'networks:':
      in_networks = True
      result.append('# Uncomment and edit to add your networks:\n')
      result.append('#networks:\n')
      continue
    if in_networks:
      # Still in networks section — everything indented or blank
      if stripped and not stripped.startswith((' ', '#', '\t')):
        in_networks = False  # hit a new top-level key
      else:
        # Comment out this line (preserve indentation)
        if stripped.lstrip().startswith('#') or not stripped.strip():
          result.append(line)
        else:
          result.append('#' + line)
        continue

    # Replace example identity values with generic defaults
    if stripped.startswith('nick: ') and 'myuser' in stripped:
      result.append('nick: qtpyrc_user\n')
    elif stripped.startswith('  - myuser_'):
      result.append('  - qtpyrc_user_\n')
    elif stripped.startswith('  - myuser`'):
      result.append('  - qtpyrc_user`\n')
    elif stripped.startswith('user: ') and 'myuser' in stripped:
      result.append('user: qtpyrc\n')
    elif stripped.startswith('ident_username: ') and 'myuser' in stripped:
      result.append('ident_username: qtpyrc\n')
    elif stripped.startswith('nickname: ') and 'myuser' in stripped:
      result.append('nickname: qtpyrc_user\n')
    elif stripped.startswith('  family: Fixedsys'):
      result.append('  family: Consolas\n')
    elif stripped == '# Copy it to config.yaml and edit to taste.':
      result.append('# Edit to taste.  See config.example.yaml for detailed documentation.\n')
    elif stripped == '# Run with: python qtpyrc.py -c config.yaml':
      continue  # skip this line
    else:
      result.append(line)

  return ''.join(result)


# Full default content for --init and "Restore Defaults".
# All templates are loaded from files at runtime:
#   config  — built from config.example.yaml by _get_default_config()
#   popups  — defaults/popups.ini
#   toolbar — defaults/toolbar.ini
#   startup — defaults/startup.rc
#   variables — defaults/variables.ini
_DEFAULT_TEMPLATES = {
    'config':    None,
    'startup':   None,
    'popups':    None,
    'toolbar':   None,
    'variables': None,
}

def _resolve_template(key):
  """Lazily load a default template, caching in _DEFAULT_TEMPLATES."""
  if _DEFAULT_TEMPLATES[key] is not None:
    return _DEFAULT_TEMPLATES[key]
  if key == 'config':
    _DEFAULT_TEMPLATES[key] = _get_default_config()
  else:
    _file_map = {
        'startup':   'startup.rc',
        'popups':    'popups.ini',
        'toolbar':   'toolbar.ini',
        'variables': 'variables.ini',
    }
    content = _read_default_file(_file_map[key])
    if content is None:
      # Fallback to stub if default file is missing
      content = ''
    _DEFAULT_TEMPLATES[key] = content
  return _DEFAULT_TEMPLATES[key]


def init_default_files(directory, config_name='config.yaml', overwrite=None):
  """Create all default files and directories in *directory*.

  *config_name* is the filename for the config file (allows alternatives
  when config.yaml already exists).

  *overwrite* is an optional set of filenames to overwrite if they exist.
  Files not in the set are skipped when they already exist.

  Returns ``(created, skipped, overwritten)`` — three lists of
  ``(name, kind)`` tuples.
  Raises OSError on directory creation failure.
  """
  overwrite = overwrite or set()
  created = []
  skipped = []
  overwritten = []
  os.makedirs(directory, exist_ok=True)

  def _write_file(path, name, content, kind):
    exists = os.path.isfile(path)
    if exists and name not in overwrite:
      skipped.append((name, 'already exists'))
      return
    with open(path, 'w', encoding='utf-8') as f:
      f.write(content)
    if exists:
      overwritten.append((name, kind))
    else:
      created.append((name, kind))

  # Config file
  config_path = os.path.join(directory, config_name)
  _write_file(config_path, config_name, _resolve_template('config'), 'config')

  # Ancillary files and directories
  for name, is_dir in _DEFAULT_ANCILLARY:
    path = os.path.join(directory, name)
    if is_dir:
      if os.path.isdir(path):
        skipped.append((name + '/', 'already exists'))
      else:
        os.makedirs(path, exist_ok=True)
        created.append((name + '/', 'directory'))
    else:
      stem = os.path.splitext(name)[0]
      content = _resolve_template(stem) if stem in _DEFAULT_TEMPLATES else ''
      if content:
        _write_file(path, name, content, 'file')

  # Copy bundled icons into the icons/ directory
  import shutil
  bundled_icons = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons')
  target_icons = os.path.join(directory, 'icons')
  if os.path.isdir(bundled_icons):
    os.makedirs(target_icons, exist_ok=True)
    for fname in os.listdir(bundled_icons):
      src = os.path.join(bundled_icons, fname)
      dst = os.path.join(target_icons, fname)
      if os.path.isfile(src):
        if not os.path.isfile(dst):
          shutil.copy2(src, dst)
          created.append(('icons/' + fname, 'icon'))
        elif ('icons/' + fname) not in overwrite:
          skipped.append(('icons/' + fname, 'already exists'))

  # Copy bundled plugins into the plugins/ directory
  bundled_plugins = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plugins')
  target_plugins = os.path.join(directory, 'plugins')
  if os.path.isdir(bundled_plugins):
    for item in os.listdir(bundled_plugins):
      src = os.path.join(bundled_plugins, item)
      dst = os.path.join(target_plugins, item)
      if os.path.isfile(src):
        if not os.path.isfile(dst):
          shutil.copy2(src, dst)
          created.append(('plugins/' + item, 'plugin'))
        elif ('plugins/' + item) not in overwrite:
          skipped.append(('plugins/' + item, 'already exists'))
      elif os.path.isdir(src):
        if not os.path.isdir(dst):
          shutil.copytree(src, dst)
          created.append(('plugins/' + item + '/', 'plugin data'))
        else:
          skipped.append(('plugins/' + item + '/', 'already exists'))

  return created, skipped, overwritten


def _init_config(app_dir, path_arg, set_opts):
  """Generate a new config file and exit."""
  path_arg = path_arg.strip()
  target = os.path.abspath(path_arg)

  # If target looks like a directory (ends with separator or exists as dir),
  # use default filename inside it
  if (path_arg.endswith(os.sep) or path_arg.endswith('/')
      or (os.path.isdir(target) and not target.endswith('.yaml')
          and not target.endswith('.yml'))):
    target = os.path.join(target, 'config.yaml')
  elif (not os.path.exists(target)
        and not target.endswith('.yaml') and not target.endswith('.yml')):
    # Ambiguous: could be a directory or a filename
    print('"%s" does not exist and has no file extension.' % path_arg)
    print('  [d] Create as a directory (config.yaml inside it)')
    print('  [f] Create as a config file with that name')
    print('  [c] Cancel')
    choice = input('Choice [d/f/c]: ').strip().lower()
    if choice == 'd':
      target = os.path.join(target, 'config.yaml')
    elif choice == 'f':
      pass  # use as-is
    else:
      sys.exit(0)

  config_dir = os.path.dirname(target)
  filename = os.path.basename(target)

  # Abort if config file already exists
  if os.path.isfile(target):
    print('Error: %s already exists' % target, file=sys.stderr)
    sys.exit(1)

  try:
    created, skipped, _ = init_default_files(config_dir, filename)
  except OSError as e:
    print('Error: %s' % e, file=sys.stderr)
    sys.exit(1)

  # Apply --set options to seed the config file
  if set_opts:
    from io import StringIO
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
    yaml = YAML()
    data = CommentedMap()
    for opt in set_opts:
      if '=' not in opt:
        print('Error: --set requires KEY=VALUE format: %s' % opt,
              file=sys.stderr)
        sys.exit(1)
      key_path, value_str = opt.split('=', 1)
      try:
        parsed = yaml.load(StringIO(value_str))
      except Exception:
        parsed = value_str
      parts = key_path.strip().split('.')
      node = data
      for p in parts[:-1]:
        if p not in node or not isinstance(node.get(p), dict):
          node[p] = CommentedMap()
        node = node[p]
      node[parts[-1]] = parsed
    buf = StringIO()
    yaml.dump(data, buf)
    with open(target, 'a', encoding='utf-8') as f:
      f.write('\n' + buf.getvalue())

  for name, kind in created:
    print('Created %s' % name)
  for name, reason in skipped:
    print('Warning: %s (%s)' % (name, reason))

  print('Run with: python qtpyrc.py -c %s' % (
      filename if config_dir == os.getcwd() else target))


def _apply_set_opts(config, set_opts):
  """Apply --set KEY=VALUE options to a loaded config."""
  from io import StringIO
  from ruamel.yaml import YAML
  from ruamel.yaml.comments import CommentedMap
  from config import AppConfig
  yaml = YAML()
  for opt in set_opts:
    if '=' not in opt:
      print('Warning: ignoring --set without = : %s' % opt, file=sys.stderr)
      continue
    key_path, value_str = opt.split('=', 1)
    try:
      parsed = yaml.load(StringIO(value_str))
    except Exception:
      parsed = value_str
    parts = key_path.strip().split('.')
    node = config._data
    for p in parts[:-1]:
      if p not in node or not isinstance(node.get(p), dict):
        node[p] = CommentedMap()
      node = node[p]
    node[parts[-1]] = parsed
  # Re-initialize config with updated data (don't save — runtime only)
  AppConfig.__init__(config, config.path, config._data, config._yaml)


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
  parser.add_argument('-o', '--override', action='append', default=[],
                      dest='set_opts', metavar='KEY=VALUE',
                      help='Override a config option at runtime without saving '
                           '(dot path, repeatable, e.g. -o font.size=15). '
                           'With --init, seeds the value into the new file')
  parser.add_argument('--ui', default=None, metavar='PATH',
                      help='Trigger a UI path on startup (e.g. --ui menu.tools.colorpicker)')
  parser.add_argument('--ui-list', action='store_true',
                      help='List all registered /ui paths and exit')
  parser.add_argument('--init', nargs='?', const='config.yaml', default=None,
                      metavar='[PATH]',
                      help='Generate a new config file and exit. '
                           'PATH can be a filename, directory, or dir/filename '
                           '(default: config.yaml in current directory)')
  cli_args, qt_args = parser.parse_known_args()
  # Error on unrecognized arguments (parse_known_args silently ignores them)
  unknown = [a for a in qt_args if a.startswith('-')]
  if unknown:
    print('Error: unrecognized arguments: %s' % ' '.join(unknown), file=sys.stderr)
    parser.print_usage(sys.stderr)
    sys.exit(2)

  mypath = os.path.dirname(os.path.abspath(__file__))

  # --- --init: generate a new config file and exit ---
  if cli_args.init is not None:
    _init_config(mypath, cli_args.init, cli_args.set_opts)
    sys.exit(0)

  configpath = cli_args.config or os.path.join(mypath, "config.yaml")
  if not os.path.isfile(configpath):
    if cli_args.config:
      print('Error: config file not found: %s' % configpath, file=sys.stderr)
      print('Use --init to create a new config file.', file=sys.stderr)
      sys.exit(1)
    with open(configpath, 'w') as f:
      f.write(_STUB_CONFIG)
  state.config = loadconfig(configpath)

  # Apply --set overrides
  if cli_args.set_opts:
    _apply_set_opts(state.config, cli_args.set_opts)
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
  config_dir = os.path.dirname(os.path.abspath(state.config.path))
  state.irclogger = IRCLogger(state.config, config_dir)

  # --- History DB ---
  from history import HistoryDB
  hf = state.config.history_file
  if not os.path.isabs(hf):
    hf = os.path.join(config_dir, hf)
  state.historydb = HistoryDB(hf, keep_limit=state.config.backscroll_limit)

  # --- Qt app ---
  state.app = makeapp([sys.argv[0]] + qt_args)

  # --- --ui-list: print all registered paths and exit ---
  if cli_args.ui_list:
    paths = sorted(state.ui_registry.keys())
    width = max(len(p) for p in paths) if paths else 0
    for path in paths:
      desc = state.ui_descriptions.get(path, '')
      if desc:
        print('%-*s  %s' % (width, path, desc))
      else:
        print(path)
    sys.exit(0)

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
  # CLI --ui trigger (deferred until the main window is fully mapped on screen)
  if cli_args.ui and win:
    from commands import docommand
    _ui_cmd = cli_args.ui
    # Validate the path
    _ui_lower = _ui_cmd.strip().lower()
    if _ui_lower not in state.ui_registry:
      _matches = [k for k in state.ui_registry if k.startswith(_ui_lower + '.') or k.startswith(_ui_lower)]
      if not _matches:
        print('Error: unknown --ui path: %s' % _ui_cmd, file=sys.stderr)
        print('Use --ui-list to see available paths.', file=sys.stderr)
        sys.exit(2)
    _mw = state.app.mainwin
    class _ExposeFilter(QObject):
      def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.WindowActivate:
          _mw.removeEventFilter(self)
          QTimer.singleShot(0, lambda: docommand(win, 'ui', _ui_cmd))
          return False
        return False
    _mw.installEventFilter(_ExposeFilter(_mw))

  # --- Identd ---
  asyncio.ensure_future(runidentd())

  def _sigint_handler(*_):
    print("\n^C received, quitting.")
    quit()
  signal.signal(signal.SIGINT, _sigint_handler)

  # Enable faulthandler to get tracebacks on segfaults
  import faulthandler
  _crash_log = os.path.join(os.path.dirname(os.path.abspath(state.config.path)), 'crash.log')
  _crash_fh = open(_crash_log, 'a')
  faulthandler.enable(file=_crash_fh)

  # Log all exceptions to crash.log as well as stderr
  def _log_exception(header, exc_type=None, exc_value=None, exc_tb=None):
    import traceback
    from datetime import datetime
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg = '[%s] %s\n' % (ts, header)
    print(header, file=sys.stderr)
    _crash_fh.write(msg)
    if exc_type and exc_value:
      traceback.print_exception(exc_type, exc_value, exc_tb)
      traceback.print_exception(exc_type, exc_value, exc_tb, file=_crash_fh)
    _crash_fh.flush()

  # Catch unhandled exceptions that would otherwise silently kill the window
  def _excepthook(exc_type, exc_value, exc_tb):
    _log_exception('*** Unhandled exception ***', exc_type, exc_value, exc_tb)
  sys.excepthook = _excepthook

  def _unraisable_hook(unraisable):
    _log_exception('*** Unraisable exception in %s ***' % (unraisable.object,),
                   type(unraisable.exc_value) if unraisable.exc_value else None,
                   unraisable.exc_value,
                   unraisable.exc_value.__traceback__ if unraisable.exc_value else None)
  sys.unraisablehook = _unraisable_hook

  def _async_exception_handler(loop, context):
    exc = context.get('exception')
    msg = context.get('message', 'Unhandled async exception')
    if exc:
      _log_exception('*** %s ***' % msg, type(exc), exc, exc.__traceback__)
    else:
      _log_exception('*** %s: %s ***' % (msg, context))
  loop.set_exception_handler(_async_exception_handler)

  # atexit: log if we're exiting without quit() being called
  import atexit
  def _atexit():
    if not _quitting:
      from datetime import datetime
      ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
      _crash_fh.write('[%s] *** Unexpected exit (no quit() called) ***\n' % ts)
      _crash_fh.flush()
    _crash_fh.close()
  atexit.register(_atexit)

  with loop:
    loop.run_forever()
