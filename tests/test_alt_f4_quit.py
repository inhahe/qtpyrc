"""A window closing is not a request to quit the client.

Reported 2026-09-17: "it seems that it always disappears when i accidentally
press some random key combination." It was not a crash. `me/crash.log` had not
been touched in a fortnight -- no traceback, no faulthandler dump, no SEH
record, and crucially **no "Unexpected exit (no quit() called)" atexit marker**,
which is what an externally killed process leaves. All of that is consistent
with exactly one thing: `quit()` ran, normally, because something asked it to.

`_AppKeyFilter` is installed on the **QApplication**, so it sees Close events
for every object in the process. Its last branch quit the whole client whenever
a non-main top-level window closed *and* `QApplication.queryKeyboardModifiers()`
reported Alt.

That call polls the keyboard **at the instant it is asked**. It knows nothing
about the event being handled -- a QCloseEvent carries no modifiers, which is
presumably why it was reached for. The consequences:

  * Every menu and popup is a top-level window. Pressing Alt to reach the menu
    bar closes whatever popup is open, *while Alt is down*, which quit the
    client. So did dismissing a right-click menu, or the nick-completion popup.
  * Alt is held for plenty of things that are not Alt+F4: Alt+Tab, menu
    mnemonics, and AltGr -- which Windows reports as Ctrl+Alt -- for typing
    @ \\ | ~ EUR on a non-US layout.

What this pins:

  1. A popup/menu/tooltip closing never quits, whatever the keyboard is doing.
  2. An ordinary window closing does not quit on its own.
  3. Alt+F4 followed by a Close still does quit, so the intended behaviour
     survives -- and it is driven by a **key event this filter actually saw**,
     not by a hardware poll.
  4. One Alt+F4 arms exactly one close.

The old code cannot pass 1 or 2 without a keyboard in a known state, which is
the other half of the point: a test cannot hold Alt down, and production should
not be asking.

Usage:
  python tests/test_alt_f4_quit.py     # from the qtpyrc root directory
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, Qt, QObject  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QMenu  # noqa: E402

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


def main():
  app = QApplication.instance() or QApplication([])

  import state
  import qtpyrc as q

  # A stand-in main window, so "is this the main window?" has an answer.
  mainwin = QWidget()
  saved_app = getattr(state, 'app', None)
  state.app = type('A', (), {'mainwin': mainwin})()

  # The quit must never actually run here; record it instead.
  quits = []
  saved_singleshot = q.QTimer.singleShot
  q.QTimer.singleShot = staticmethod(lambda ms, fn: quits.append(fn))

  # Hold Alt down for the whole test. This is the reporter's condition -- the
  # menu bar reached with Alt, an Alt+Tab, an AltGr character -- and it is the
  # only state in which the old code's `queryKeyboardModifiers()` poll says
  # yes. Without it this file passes against the very bug it exists for, since
  # the poll reads the *test machine's* keyboard and nobody is holding Alt.
  saved_query = QApplication.queryKeyboardModifiers
  QApplication.queryKeyboardModifiers = staticmethod(
      lambda: Qt.KeyboardModifier.AltModifier)

  try:
    f = q._AppKeyFilter()

    def close_of(w):
      return f.eventFilter(w, QEvent(QEvent.Type.Close))

    def alt_f4():
      """An Alt+F4 key press, as this filter would see it."""
      ev = q.QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F4,
                       Qt.KeyboardModifier.AltModifier)
      f.eventFilter(mainwin, ev)

    # --- 1. a menu or popup closing is not a quit -------------------------
    menu = QMenu()
    check(menu.isWindow(),
          'a QMenu is not a top-level window here, so this test is not '
          'exercising the path the report is about')
    check(_is_transient(q, menu),
          'a QMenu is not classified as transient, so closing one still '
          'reaches the quit branch')
    alt_f4()                      # worst case: a real Alt+F4 *and* a popup
    check(close_of(menu) is False,
          'closing a menu was intercepted')
    check(not quits,
          'closing a QMenu quit the client -- this is the report: pressing '
          'Alt to reach the menu bar dismisses the open popup, and the popup '
          'going away took the whole client with it')

    popup = QWidget(None, Qt.WindowType.Popup)
    alt_f4()
    check(close_of(popup) is False and not quits,
          'closing a Qt.Popup window quit the client')

    tip = QWidget(None, Qt.WindowType.ToolTip)
    alt_f4()
    check(close_of(tip) is False and not quits,
          'closing a tooltip quit the client')

    # --- 2. an ordinary window closing, with no Alt+F4, is not a quit -----
    # The old code asked the keyboard here. With Alt held -- Alt+Tab, AltGr,
    # reaching the menu bar -- it said yes, to a close that had nothing to do
    # with Alt+F4.
    f._alt_f4_at = 0.0
    dlg = QWidget()
    dlg.setWindowFlags(Qt.WindowType.Window)
    check(close_of(dlg) is False,
          'an ordinary window close was intercepted with no Alt+F4 in sight')
    check(not quits,
          'a window closing on its own quit the client')

    # --- 3. the intended behaviour still works ----------------------------
    alt_f4()
    check(close_of(dlg) is True,
          'Alt+F4 over a child window was not intercepted, so the intended '
          'quit never happens')
    check(len(quits) == 1,
          'Alt+F4 over a child window did not schedule the quit (got %d)'
          % len(quits))

    # --- 4. one Alt+F4 arms exactly one close ------------------------------
    quits.clear()
    check(close_of(dlg) is False and not quits,
          'a second close was still attributed to the same Alt+F4')

    # --- 5. the correlation expires ---------------------------------------
    alt_f4()
    f._alt_f4_at -= (f._ALT_F4_WINDOW + 1.0)
    check(close_of(dlg) is False and not quits,
          'a close long after the Alt+F4 was still attributed to it')

    # --- 6. the main window is left alone ----------------------------------
    alt_f4()
    check(close_of(mainwin) is False,
          'the main window was intercepted; it must close natively so '
          'lastWindowClosed drives the quit')
    check(not quits, 'closing the main window took the intercept path')

  finally:
    q.QTimer.singleShot = saved_singleshot
    QApplication.queryKeyboardModifiers = saved_query
    state.app = saved_app

  if failures:
    print('FAILED (%d):' % len(failures))
    for x in failures:
      print('  - %s' % x)
    return 1
  print('a closing window only quits when an actual Alt+F4 preceded it, and '
        'never for a menu, popup or tooltip.')
  return 0


def _is_transient(q, w):
  return q._is_transient_window(w)


sys.exit(main())
