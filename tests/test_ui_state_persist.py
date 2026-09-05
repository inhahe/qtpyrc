"""A UI size the user has set must reach ui.yaml, not just memory.

The report: the splitter between the network tree and the chat area "isn't
remembering its position properly across runs". *Properly* is the tell -- it
worked sometimes.

`UIState.treeview_width` and `.nicklist_width` are plain properties: assigning
to one updates the in-memory dict and nothing else. Both were assigned from
their splitter handlers and never saved, so the only thing that ever wrote them
to disk was the `ui_state.save()` in `qtpyrc.quit()`. That makes the setting
survive a clean shutdown and vanish to anything else -- a crash, a kill, a
session that ends without quit() running -- and *also* makes it appear to work
at random, because any other UIState mutation (a colour picked, a sound chosen,
a recent script recorded) calls save() and flushes the pending width with it.

Which is the sharper point here: a value that persists only as a side effect of
some unrelated action is worse than one that never persists, because it teaches
the user that the feature works.

The other setters in the class -- saved_colors, recent_colors, plugins_order,
hex_uppercase -- all save immediately. These two were the odd ones out.

What this pins: after a splitter move, the value is **on disk**, without
anything else having to happen and without shutting down. It uses the real
UIState against a real file, and drives the real handlers.

Usage:
  python tests/test_ui_state_persist.py     # from the qtpyrc root directory
"""

import atexit
import os
import runpy
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG = """\
nick: tester
user: tester
realname: qtpyrc ui state test

window_mode: normal
view_mode: tabbed
navigation: both

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
link_preview: {enabled: false}
ident: {enabled: false}
logging:
  hang_watchdog: {enabled: false}
history_replay: {channels: 0, queries: 0, bg_enabled: false}

networks:
  testnet:
    name: TestNet
    nick: tester
    auto_connect: false
    server:
      host: 127.0.0.1
      port: 6667
      tls: false
"""

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-uistate-')
atexit.register(shutil.rmtree, tmpdir, True)
cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG)
UI_YAML = os.path.join(tmpdir, 'ui.yaml')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.argv = ['qtpyrc.py', '-c', cfg, '--no-startup']

import state
import window as window_mod
from PySide6.QtCore import QTimer, QCoreApplication, QMetaObject, Qt

EXIT = 1
failures = []

TREE_W = 271
NICK_W = 233


def check(cond, msg):
  if not cond:
    failures.append(msg)


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def on_disk():
  """ui.yaml as it is *on disk*, read fresh -- not the in-memory object."""
  from ruamel.yaml import YAML
  try:
    with open(UI_YAML, encoding='utf-8') as f:
      return YAML().load(f) or {}
  except FileNotFoundError:
    return {}


def main():
  import models
  app = QCoreApplication.instance()
  mw = state.app.mainwin
  client = next(iter(state.clients))

  check(state.ui_state is not None, 'no ui_state, so nothing could persist')
  check(os.path.isfile(UI_YAML), 'ui.yaml was never created')

  # --- 1. the tree splitter -------------------------------------------------
  # Drive the real handler the way a drag does: set the sizes, then emit what
  # splitterMoved would. Calling the handler directly is the point -- the bug
  # is in what the handler does, not in Qt's drag detection.
  import qtpyrc as qtpyrc_mod
  mw.resize(1200, 800)
  app.processEvents()
  ok = window_mod.set_splitter_pane(mw._tree_splitter, 0, TREE_W)
  app.processEvents()
  check(ok and mw._tree_splitter.sizes()[0] == TREE_W,
        'asking for a %dpx tree pane produced %r. QSplitter divides the width '
        'minus the handles, so sizes that add up to the full width lose the '
        'handle out of one pane -- which is the 1px-per-run drift.'
        % (TREE_W, mw._tree_splitter.sizes()))
  qtpyrc_mod._on_treeview_splitter_moved(TREE_W, 1)
  check(state.ui_state.treeview_width == TREE_W,
        'the handler did not even set the in-memory value (%r)'
        % state.ui_state.treeview_width)

  # The round trip, which is the property the report is about: restoring a
  # saved width and reading it back must give the same number, every time.
  # It used to lose exactly one pixel per cycle, so the divider crept narrower
  # on every launch.
  w = TREE_W
  for i in range(5):
    window_mod.set_splitter_pane(mw._tree_splitter, 0, w)
    app.processEvents()
    back = mw._tree_splitter.sizes()[0]
    check(back == w,
          'round trip %d: restored %d and read back %d -- the tree splitter '
          'loses a pixel per run' % (i + 1, w, back))
    w = back

  # --- 1b. the restored width must survive the window being resized ---------
  # This is the "off by hundreds of pixels" report. QSplitter scales its panes
  # *proportionally* on resize, so a tree that is 217 of 640 is 34% -- and 34%
  # of a maximised 2560px window is about 870px. Only the re-apply in
  # _TreeSplitter.resizeEvent keeps it at 217, and that re-apply used to be
  # skipped whenever it could not reach state.app.mainwin.
  sp = mw._tree_splitter
  # Reset both the splitter's own state and the copy on the main window, so
  # this measures the re-apply itself rather than which of the two an
  # implementation happens to consult.
  sp.target_width = TREE_W
  sp.user_set = False
  mw._tree_target_tw = TREE_W
  mw._tree_user_set = False
  for w in (900, 1400, 2560, 1024):
    mw.resize(w, 800)
    app.processEvents()
    got = sp.sizes()[0]
    check(got == TREE_W,
          'after resizing the window to %dpx the tree pane is %d, expected %d '
          '-- the saved width is being scaled with the window instead of kept'
          % (w, got, TREE_W))

  # ...and it must not depend on state.app, which is assigned from makeapp()'s
  # *return value* -- so during startup, when the window is first shown and
  # maximised, it is still None. Every resize in that window was silently
  # skipped, which is how the proportional scaling above was left standing.
  saved_app = state.app
  try:
    state.app = None
    mw.resize(1800, 800)
    app.processEvents()
    got = sp.sizes()[0]
  finally:
    state.app = saved_app
  check(got == TREE_W,
        'with state.app unset (which is its value throughout makeapp, where '
        'the window is shown) the resize left the tree at %d instead of %d'
        % (got, TREE_W))
  mw.resize(1200, 800)
  app.processEvents()

  # --- 2. the nicklist splitter ---------------------------------------------
  chan = models.Channel(client, '#uistate')
  client.channels['#uistate'] = chan
  chan.window.resize(700, 400)
  app.processEvents()
  ok = window_mod.set_splitter_pane(chan.window.splitter, 1, NICK_W)
  app.processEvents()
  check(ok and chan.window.splitter.sizes()[1] == NICK_W,
        'asking for a %dpx nick list produced %r'
        % (NICK_W, chan.window.splitter.sizes()))
  chan.window._on_splitter_moved(0, 1)
  check(state.ui_state.nicklist_width == NICK_W,
        'the nicklist handler did not set the in-memory value (%r)'
        % state.ui_state.nicklist_width)

  # The save is debounced, so give the timer its moment. Nothing else is
  # allowed to write ui.yaml in between -- if the value only lands because
  # some unrelated action saved, that is the bug this exists to catch.
  QTimer.singleShot(1600, verify)


def verify():
  try:
    data = on_disk()
    check(data.get('treeview_width') == TREE_W,
          'ui.yaml on disk has treeview_width=%r, expected %d. The splitter '
          'position is only in memory, so it survives a clean shutdown and is '
          'lost to anything else.' % (data.get('treeview_width'), TREE_W))
    check(data.get('nicklist_width') == NICK_W,
          'ui.yaml on disk has nicklist_width=%r, expected %d'
          % (data.get('nicklist_width'), NICK_W))

    # A width that does not fit is declined rather than written as a ratio:
    # that is what made a saved 217 come back as 318 on a wider window.
    mw = state.app.mainwin
    before = list(mw._tree_splitter.sizes())
    check(not window_mod.set_splitter_pane(mw._tree_splitter, 0, 99999),
          'a pane wider than the splitter was accepted; Qt turns that into a '
          'ratio and the restored width is wrong by however much')
    check(mw._tree_splitter.sizes() == before,
          'declining to apply an impossible width still changed the sizes')
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)

  if failures:
    print('FAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return finish(1)
  print('splitter positions reach ui.yaml on their own, without a shutdown.')
  return finish(0)


def _run():
  try:
    return main()
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(300, _run)

try:
  runpy.run_path(os.path.join(ROOT, 'qtpyrc.py'), run_name='__main__')
except SystemExit:
  pass

sys.exit(EXIT)
