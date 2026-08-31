"""Chat view: auto-scroll behaviour, and the full-document layouts around it.

Two things in the chat view are cheap to write and expensive to run, because
both end up asking a QTextEdit for something that can only be answered by
laying the *whole* backscroll out:

  * ``Window._on_range_changed`` used to call ``output.moveCursor(End)`` before
    setting the scrollbar. The scrollbar move alone is what scrolls; the cursor
    move additionally forced a layout and -- the visible bug -- dropped the
    anchor of any selection the user was making, so text they had highlighted
    was deselected the moment somebody spoke.
  * ``Window._doBottomAlign`` asks ``doc.size()`` for the content height so it
    can top-pad a short document to the bottom of the viewport. That is a full
    layout, measured at ~130ms per window for 3000 lines, and every geometry
    change asks again (showEvent/resizeEvent clear ``_bottom_align_filled``).
    It was the single largest cost in "Window -> Tile Side by Side", which
    re-shows and re-sizes every open window at once -- 32 seconds of frozen GUI
    in me/hangs.log, 2026-08-17 06:56:18. A document with more blocks than the
    viewport has lines cannot fit in it at any width, so that case now answers
    "margin 0" without measuring.

The behaviour those two shortcuts must not break is pinned here alongside them:
stick to the bottom while at the bottom, stay put when the user has scrolled
up, and still bottom-align a document that genuinely is shorter than the
viewport.

Usage:
  python tests/test_autoscroll.py     # from the qtpyrc root directory
"""

import atexit
import os
import runpy
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# No network: every window here is built directly, so the client never connects.
# history_replay off keeps each window's content ours alone.
CONFIG = """\
nick: tester
user: tester
realname: qtpyrc autoscroll test

window_mode: normal
view_mode: tabbed

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
link_preview: {enabled: false}
ident: {enabled: false}
logging:
  hang_watchdog: {enabled: false}

backscroll_limit: 2000
history_replay:
  channels: 0
  queries: 0

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

# A document this size takes ~100ms to lay out in full, so the shortcut and the
# measurement are three orders of magnitude apart -- no machine is slow enough
# for one to be mistaken for the other.
BIG_LINES = 3000
LAYOUT_BUDGET = 0.020   # seconds

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-scrolltest-')

# Removed at interpreter exit rather than in a finally, and registered here --
# before qtpyrc is run -- on purpose. qtpyrc holds crash.log open for the life of
# the process (faulthandler needs a live file object) and closes it from its own
# atexit handler, so a rmtree in a finally runs while Windows still has the file
# locked, silently does nothing under ignore_errors, and leaves the directory
# behind on every run. atexit is LIFO, so registering first means running last:
# after qtpyrc's handler has closed the file.
atexit.register(shutil.rmtree, tmpdir, True)

cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.argv = ['qtpyrc.py', '-c', cfg]

import state
import window as window_mod
from PySide6.QtCore import QTimer, QCoreApplication, QMetaObject, Qt
from PySide6.QtGui import QTextCursor

EXIT = 1
failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def make_window(client, name):
  import models
  chan = models.Channel(client, name)
  w = window_mod.Channelwindow(client, chan)
  chan.window = w
  client.channels[name] = chan
  return w


def fill(w, n, start=0):
  for i in range(start, start + n):
    w.addline_msg('somenick', 'line %d lorem ipsum dolor sit amet consectetur '
                              'adipiscing elit sed do eiusmod tempor' % i)


def at_bottom(w):
  return w.vs.value() == w.vs.maximum()


def run():
  from PySide6.QtWidgets import QApplication
  app = QApplication.instance()
  client = next(iter(state.clients))
  ws = state.app.mainwin.workspace

  # ---------------------------------------------------------------- 1
  # The basic contract: a window at the bottom stays at the bottom.
  w = make_window(client, '#stick')
  w.resize(700, 400)
  app.processEvents()
  fill(w, 400)
  app.processEvents()
  check(at_bottom(w),
        'the view did not stay at the bottom while lines were added '
        '(value=%d, maximum=%d)' % (w.vs.value(), w.vs.maximum()))

  # ...including once the backscroll limit starts dropping blocks off the top,
  # which shrinks the range from underneath the view.
  fill(w, state.config.backscroll_limit + 200, start=400)
  app.processEvents()
  check(w.output.document().blockCount() <= state.config.backscroll_limit + 1,
        'the backscroll limit is not being applied, so this case is not '
        'actually being tested (blocks=%d)'
        % w.output.document().blockCount())
  check(at_bottom(w),
        'dropping blocks off the top left the view off the bottom '
        '(value=%d, maximum=%d)' % (w.vs.value(), w.vs.maximum()))

  # ...and across a width change, which re-wraps every line and so changes the
  # scrollbar range without anything being added.
  w.resize(380, 400)
  app.processEvents()
  fill(w, 5, start=99000)
  app.processEvents()
  check(at_bottom(w),
        'a width change left the view off the bottom (value=%d, maximum=%d)'
        % (w.vs.value(), w.vs.maximum()))

  # ---------------------------------------------------------------- 2
  # A user who has scrolled up must be left where they are.
  w.vs.setValue(w.vs.maximum() // 2)
  app.processEvents()
  parked = w.vs.value()
  check(not w._auto_scroll, 'scrolling up did not disengage auto-scroll')
  fill(w, 5, start=98000)
  app.processEvents()
  check(w.vs.value() == parked,
        'an incoming line yanked a scrolled-up view (was %d, now %d)'
        % (parked, w.vs.value()))
  w._scroll_to_bottom()
  app.processEvents()
  check(at_bottom(w) and w._auto_scroll,
        '_scroll_to_bottom() did not return the view to the bottom')

  # ---------------------------------------------------------------- 3
  # An incoming line must not destroy a selection the user is making.
  # A fresh window, so the backscroll limit isn't dropping the very blocks the
  # selection covers -- that would take the selection with it for real reasons.
  sel = make_window(client, '#select')
  sel.resize(700, 400)
  fill(sel, 50)
  app.processEvents()
  # Select the last few lines, i.e. what the user sees while sitting at the
  # bottom of a live channel. Selecting further up would scroll the view there
  # and disengage auto-scroll, and then the handler under test never runs.
  # Stop short of the very last line: a selection that ends at the end of the
  # document grows when text is appended there, which is Qt adjusting cursors
  # around an insertion and not what is being checked.
  cur = sel.output.textCursor()
  cur.movePosition(QTextCursor.MoveOperation.End)
  cur.movePosition(QTextCursor.MoveOperation.Up,
                   QTextCursor.MoveMode.MoveAnchor, 3)
  cur.movePosition(QTextCursor.MoveOperation.Down,
                   QTextCursor.MoveMode.KeepAnchor, 2)
  sel.output.setTextCursor(cur)
  app.processEvents()
  check(sel.output.textCursor().hasSelection(),
        'the test could not make a selection to begin with')
  check(sel._auto_scroll,
        'making the selection disengaged auto-scroll, so the auto-scroll path '
        'this checks would not run at all')
  before = (sel.output.textCursor().selectionStart(),
            sel.output.textCursor().selectionEnd())
  fill(sel, 1, start=97000)
  app.processEvents()
  now = (sel.output.textCursor().selectionStart(),
         sel.output.textCursor().selectionEnd())
  check(sel.output.textCursor().hasSelection() and now == before,
        'an incoming message disturbed the selection the user had made '
        '(was %r, now %r)' % (before, now))

  # ---------------------------------------------------------------- 4
  # Bottom-align must not measure a document that obviously overflows.
  big = make_window(client, '#big')
  big.resize(700, 400)
  fill(big, BIG_LINES)
  app.processEvents()
  doc = big.output.document()
  check(doc.blockCount() * big.output.fontMetrics().lineSpacing()
        > big.output.viewport().height(),
        'the "big" window is not actually taller than its viewport, so this '
        'case is not being tested')
  # Re-wrap first: a stale layout is what makes doc.size() expensive, and it is
  # the state a tile leaves every window in. Without it doc.size() answers from
  # cache for nothing, and the shortcut would have nothing to beat. Setting the
  # text width is what QTextEdit's own resizeEvent does, and unlike resizing
  # the widget it works whether or not this window is the visible tab.
  doc.setTextWidth(doc.textWidth() - 40)
  big._bottom_align_filled = False      # as showEvent/resizeEvent leave it
  t0 = time.perf_counter()
  big._doBottomAlign()
  elapsed = time.perf_counter() - t0
  check(elapsed < LAYOUT_BUDGET,
        'bottom-aligning an overflowing %d-line document took %.3fs -- it is '
        'laying the whole document out again instead of answering from the '
        'block count' % (BIG_LINES, elapsed))
  check(big._bottom_align_filled,
        'bottom-align did not settle on "content fills the viewport", so it '
        'will keep re-measuring on every geometry change')
  check(doc.rootFrame().frameFormat().topMargin() == 0,
        'an overflowing document was given a top margin')

  # ...but a document that really is shorter than the viewport still gets
  # padded down to the bottom. This is the feature the shortcut must not eat.
  short = make_window(client, '#short')
  short.resize(700, 400)
  fill(short, 3)
  app.processEvents()
  short._bottom_align_filled = False
  short._doBottomAlign()
  check(short.output.document().rootFrame().frameFormat().topMargin() > 0,
        'a three-line window was not bottom-aligned (top margin is %r)'
        % short.output.document().rootFrame().frameFormat().topMargin())

  # ---------------------------------------------------------------- 5
  # Tiling reparents, shows and resizes every window at once. It must survive
  # that and leave each one showing its newest line.
  ws.tileSubWindows()
  app.processEvents()
  for tw in (w, sel, big, short):
    if tw._auto_scroll and not at_bottom(tw):
      check(False, 'a tiled window is no longer at the bottom '
                   '(value=%d, maximum=%d)' % (tw.vs.value(), tw.vs.maximum()))
      break
  ws.maximizeActive()
  app.processEvents()


def main():
  try:
    run()
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)
  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return finish(1)
  print('All chat auto-scroll / bottom-align checks passed.')
  return finish(0)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(300, main)

try:
  runpy.run_path(os.path.join(ROOT, 'qtpyrc.py'), run_name='__main__')
except SystemExit:
  pass

sys.exit(EXIT)
