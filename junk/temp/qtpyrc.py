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

import sys, os, asyncio, argparse

import state
from config import loadconfig, LayoutState
from models import Client, newclient
from window import NetworkTree
from dialogs import _validate_font, open_settings


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
    "QTabBar::tab { background-color: %s; color: %s; }"
    "QTabBar::tab:selected { background-color: %s; color: %s; border-bottom: 2px solid %s; }"
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
    bg, fg,                # QTabBar tab
    bg, fg, fg,            # QTabBar selected tab
    bg,                    # QMdiArea
  )

def _apply_view_mode(mode):
  """Switch the QMdiArea between tabbed and MDI (subwindow) view."""
  ws = state.app.mainwin.workspace
  if mode == 'mdi':
    ws.setViewMode(QMdiArea.ViewMode.SubWindowView)
  else:
    ws.setViewMode(QMdiArea.ViewMode.TabbedView)
    tabbar = ws.findChild(QTabBar)
    if tabbar:
      tabbar.setExpanding(False)

def _toggle_view_mode():
  ws = state.app.mainwin.workspace
  if ws.viewMode() == QMdiArea.ViewMode.TabbedView:
    _apply_view_mode('mdi')
  else:
    _apply_view_mode('tabbed')

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

def makeapp(args):
  app = QApplication(args)
  app.setStyleSheet(_build_app_stylesheet())
  app.mainwin = QMainWindow()
  app.mainwin.workspace = QMdiArea()

  # Apply initial view mode
  if state.config.view_mode == 'mdi':
    app.mainwin.workspace.setViewMode(QMdiArea.ViewMode.SubWindowView)
  else:
    app.mainwin.workspace.setViewMode(QMdiArea.ViewMode.TabbedView)
    app.mainwin.workspace.setTabPosition(QTabWidget.TabPosition.North)
    app.mainwin.workspace.setDocumentMode(True)
    app.mainwin.workspace.setTabsClosable(True)
    app.mainwin.workspace.setTabsMovable(True)
    tabbar = app.mainwin.workspace.findChild(QTabBar)
    if tabbar:
      tabbar.setExpanding(False)

  if state.config.treeview:
    app.mainwin.network_tree = NetworkTree()
    app.mainwin._tree_splitter = QSplitter()
    app.mainwin._tree_splitter.addWidget(app.mainwin.network_tree)
    app.mainwin._tree_splitter.addWidget(app.mainwin.workspace)
    tw = state.layout.treeview_width if state.layout else 180
    app.mainwin._tree_splitter.setSizes([tw, 600])
    app.mainwin._tree_splitter.splitterMoved.connect(_on_treeview_splitter_moved)
    app.mainwin.setCentralWidget(app.mainwin._tree_splitter)
  else:
    app.mainwin.network_tree = None
    app.mainwin._tree_splitter = None
    app.mainwin.setCentralWidget(app.mainwin.workspace)

  app.mainwin.workspace.subWindowActivated.connect(_on_subwindow_activated)

  # --- Menu bar ---
  app.mainwin.menubar = app.mainwin.menuBar()

  # File menu
  app.mainwin.mnufile = app.mainwin.menubar.addMenu('&File')
  app.mainwin.mnusettings = app.mainwin.mnufile.addAction('&Settings')
  app.mainwin.mnusettings.triggered.connect(open_settings)
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
  if state.config.window_mode == 'maximized':
    app.mainwin.showMaximized()
  else:
    app.mainwin.show()
  app.lastWindowClosed.connect(quit)
  return app

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
  from plugins import loadscripts, apply_hooks
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

  # --- Qt app ---
  state.app = makeapp([sys.argv[0]] + qt_args)
  loop = qasync.QEventLoop(state.app)
  asyncio.set_event_loop(loop)

  # --- Font validation ---
  _validate_font(state.config)

  # --- Apply plugin hooks to IRCClient ---
  apply_hooks()

  # --- Auto-connect ---
  state.clients = set()
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

  # --- Identd ---
  asyncio.ensure_future(runidentd())

  with loop:
    loop.run_forever()
