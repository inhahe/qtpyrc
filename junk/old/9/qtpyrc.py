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

import state
from config import loadconfig, LayoutState
from models import Client, newclient
from window import NetworkTree
from dialogs import _validate_font, open_settings
from tabbar import TabbedWorkspace


# ---------------------------------------------------------------------------
# App construction helpers
# ---------------------------------------------------------------------------

def _build_app_stylesheet():
  """Build a global stylesheet from config colors."""
  fg = state.config.fgcolor.name()
  bg = state.config.bgcolor.name()
  return (
    "QMainWindow { background-color: %s; color: %s; }"
    "QMenuBar { background-color: %s; color: %s; }"
    "QMenuBar::item:selected { background-color: %s; color: %s; }"
    "QMenu { background-color: %s; color: %s; }"
    "QMenu::item:selected { background-color: %s; color: %s; }"
    "QTextEdit { background-color: %s; color: %s; }"
    "QTreeWidget { background-color: %s; color: %s; }"
    "QTreeWidget::item:selected { background-color: %s; color: %s; }"
    "QListWidget { background-color: %s; color: %s; }"
    "QMdiArea { background-color: %s; }"
  ) % (
    bg, fg,                # QMainWindow
    bg, fg,                # QMenuBar
    fg, bg,                # QMenuBar selected (inverted)
    bg, fg,                # QMenu
    fg, bg,                # QMenu selected (inverted)
    bg, fg,                # QTextEdit (output + input)
    bg, fg,                # QTreeWidget
    fg, bg,                # QTreeWidget selected (inverted)
    bg, fg,                # QListWidget (nick list)
    bg,                    # QMdiArea
  )

def _apply_view_mode(mode):
  """View mode is set at startup; this is a no-op placeholder."""
  pass

def _toggle_view_mode():
  """View mode toggle is not supported with the custom tab bar."""
  pass

def _tile_vertically():
  """Tile subwindows vertically (stacked top-to-bottom)."""
  ws = state.app.mainwin.workspace
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
  if state.layout:
    sizes = state.app.mainwin._tree_splitter.sizes()
    if len(sizes) >= 2:
      state.layout.treeview_width = sizes[0]

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
  # Sync treeview selection
  tree = getattr(state.app.mainwin, 'network_tree', None)
  if tree:
    tree.sync_to_window(widget)

class _AppTabFilter(QObject):
  """Application-level event filter to intercept Ctrl+Tab before Qt consumes it."""
  def eventFilter(self, obj, event):
    if event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
      if event.key() == Qt.Key.Key_Tab and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
        ws = state.app.mainwin.workspace
        if hasattr(ws, 'cycle_tab'):
          if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()  # tell Qt we'll handle this
            return True
          forward = not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
          ws.cycle_tab(forward=forward)
          return True
      if event.key() == Qt.Key.Key_Backtab and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
        ws = state.app.mainwin.workspace
        if hasattr(ws, 'cycle_tab'):
          if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
          ws.cycle_tab(forward=False)
          return True
    return False

def makeapp(args):
  app = QApplication(args)
  app.setStyleSheet(_build_app_stylesheet())
  app.mainwin = QMainWindow()
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

  if state.config.show_tree:
    app.mainwin.network_tree = NetworkTree()
    app.mainwin._tree_splitter = QSplitter()
    app.mainwin._tree_splitter.addWidget(app.mainwin.network_tree)
    app.mainwin._tree_splitter.addWidget(content)
    tw = state.layout.treeview_width if state.layout else 180
    app.mainwin._tree_splitter.setSizes([tw, 600])
    app.mainwin._tree_splitter.splitterMoved.connect(_on_treeview_splitter_moved)
    app.mainwin.setCentralWidget(app.mainwin._tree_splitter)
  else:
    app.mainwin.network_tree = None
    app.mainwin._tree_splitter = None
    app.mainwin.setCentralWidget(content)

  app.mainwin.workspace.subWindowActivated.connect(_on_subwindow_activated)

  # Install app-level event filter for Ctrl+Tab (Qt intercepts it before widgets)
  app._tab_filter = _AppTabFilter(app)
  app.installEventFilter(app._tab_filter)

  # --- Menu bar ---
  app.mainwin.menubar = app.mainwin.menuBar()

  # File menu
  app.mainwin.mnufile = app.mainwin.menubar.addMenu('&File')
  app.mainwin.mnusettings = app.mainwin.mnufile.addAction('&Settings')
  app.mainwin.mnusettings.triggered.connect(open_settings)
  app.mainwin.mnustartup = app.mainwin.mnufile.addAction('Edit startup &commands')
  app.mainwin.mnustartup.triggered.connect(lambda: _edit_startup_commands())
  app.mainwin.mnufile.addSeparator()
  app.mainwin.mnufile.addAction('&Reload configuration').triggered.connect(lambda: _reload_config())
  app.mainwin.mnufile.addAction('Save configuration &as...').triggered.connect(lambda: _save_config_as())
  app.mainwin.mnufile.addSeparator()
  app.mainwin.mnuclose = app.mainwin.mnufile.addAction('&Close')
  app.mainwin.mnunew = app.mainwin.mnufile.addMenu("&New")
  app.mainwin.mnunewclient = app.mainwin.mnunew.addAction("&Server window")
  app.mainwin.mnunewclient.triggered.connect(newclient)

  # Window menu
  mnuwindow = app.mainwin.menubar.addMenu('&Window')
  mnuwindow.addAction('&Toggle Tabbed/MDI', _toggle_view_mode)
  mnuwindow.addSeparator()
  mnuwindow.addAction('Tile &Horizontally', lambda: (
    _apply_view_mode('mdi'),
    app.mainwin.workspace.tileSubWindows(),
  ))
  mnuwindow.addAction('Tile &Vertically', lambda: (
    _apply_view_mode('mdi'),
    _tile_vertically(),
  ))
  mnuwindow.addAction('&Cascade', lambda: (
    _apply_view_mode('mdi'),
    app.mainwin.workspace.cascadeSubWindows(),
  ))

  app.mainwin.setWindowTitle("qtpyrc")
  app.mainwin.resize(1024, 768)
  if state.config.window_mode == 'maximized':
    app.mainwin.showMaximized()
  else:
    app.mainwin.show()
  app.lastWindowClosed.connect(quit)
  return app

def _startup_path():
  """Return the path to the default startup commands file."""
  return os.path.join(os.path.dirname(os.path.abspath(state.config.path)), 'startup.rc')

def _edit_startup_commands():
  """Open the default startup commands file in the system text editor."""
  path = _startup_path()
  if not os.path.isfile(path):
    with open(path, 'w', encoding='utf-8') as f:
      f.write('; qtpyrc startup commands\n; Each line is a /command or text. Lines starting with ; are comments.\n')
  QDesktopServices.openUrl(QUrl.fromLocalFile(path))

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

def quit():
  if state.layout:
    state.layout.save()
  loop = asyncio.get_event_loop()
  loop.stop()
  state.app.quit()
  sys.exit()


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
  cli_args, qt_args = parser.parse_known_args()

  mypath = os.path.dirname(os.path.abspath(__file__))
  configpath = cli_args.config or os.path.join(mypath, "config.yaml")
  state.config = loadconfig(configpath)
  state.layout = LayoutState(os.path.join(mypath, "layout.yaml"))

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

  # --- Scripts ---
  scripts = loadscripts()
  state.activescripts = dict(scripts)

  # --- Startup commands & command scripts ---
  from commands import run_script
  win = next(iter(state.clients)).window if state.clients else None
  # Default startup commands file (always runs if present)
  startup = _startup_path()
  if os.path.isfile(startup):
    run_script(startup, win)
  # Additional command scripts from config
  for name in state.config.scripts_auto_run:
    name = str(name).strip()
    if name:
      run_script(name, win)

  # --- Identd ---
  asyncio.ensure_future(runidentd())

  def _sigint_handler(*_):
    print("\n^C received, quitting.")
    quit()
  signal.signal(signal.SIGINT, _sigint_handler)

  with loop:
    loop.run_forever()
