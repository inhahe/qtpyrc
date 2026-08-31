"""Skipping a window has to mean the same thing in both workspace containers.

TabbedWorkspace keeps its windows in one of two places -- a QStackedWidget when
the workspace is "maximized" (the ordinary tabbed look) and a QMdiArea once the
user has tiled or cascaded -- and a tab's state is supposed to say whether its
window is on screen.  Only the stack ever honoured that: SKIPPED was expressed
by *showing something else* (the next window, or the blank tab-bar-coloured
widget when there was nothing left), which an MDI area cannot do, because it
shows every window at once.  So after a tile, clicking the active tab still
cycled to the next window but minimised nothing, and once every tab was skipped
the workspace kept displaying the last window instead of going blank.

Two things made that hard to notice: a maximised QMdiSubWindow fills the
workspace exactly like the stack does, so there is no cue that the workspace is
in MDI mode at all, and nothing but the Window menu's Maximize ever left it.

Usage:
  python tests/test_tab_skip.py     # from the qtpyrc root directory
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

import state

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


class FakeConfig(object):
  """Everything TabbedWorkspace reads out of state.config."""
  fgcolor = QColor('black')
  bgcolor = QColor('white')
  tab_normal_fg = None
  tab_normal_bg = None
  tab_active_fg = None
  tab_active_bg = None
  tab_skipped_fg = None
  tab_skipped_bg = None
  tab_bar_bg = QColor('#203040')
  tab_font_family = None
  tab_font_size = None
  new_tab_state = 'normal'


def visible_subs(ws):
  """Titles of the windows the MDI area is actually showing."""
  return [t['title'] for t in ws._tabs
          if t['proxy']._mdi_sub is not None and t['proxy']._mdi_sub.isVisible()]


def add(ws, title):
  w = QWidget()
  w.setWindowTitle(title)
  ws.addSubWindow(w)
  return w


def find(ws, title):
  for t in ws._tabs:
    if t['title'] == title:
      return t
  raise KeyError(title)


def click(ws, title):
  ws._on_tab_clicked(find(ws, title))


def make_workspace(app, titles=('#one', '#two', '#three')):
  ws = TabbedWorkspace()
  ws.resize(800, 600)
  ws.show()
  for title in titles:
    add(ws, title)
  ws._activate(find(ws, titles[0]))
  app.processEvents()
  return ws


def run(app):
  # ------------------------------------------------------------------ 1
  # The tabbed case, which always worked and has to keep working: clicking the
  # active tab skips it and moves on, and when the last one is skipped the
  # blank widget -- painted in the tab bar's own colour -- takes the workspace.
  ws = make_workspace(app)
  for title in ('#one', '#two', '#three'):
    click(ws, title)
  app.processEvents()
  check(ws._active is None,
        'clicking every tab left %r active' % (ws._active and ws._active['title'],))
  check(ws._stack.currentWidget() is ws._blank,
        'the stack still shows a window after every tab was skipped')
  ws.deleteLater()

  # ------------------------------------------------------------------ 2
  # ...and the tiled case, which did not. A skipped window has to leave the
  # screen here too, which for an MDI area means hiding its subwindow -- there
  # is no "show something else" to hide it behind.
  ws = make_workspace(app)
  ws.tileSubWindows()
  app.processEvents()
  check(ws._tiled, 'tileSubWindows() did not put the workspace in MDI mode')
  check(sorted(visible_subs(ws)) == ['#one', '#three', '#two'],
        'tiling did not show every window: %r' % (visible_subs(ws),))

  click(ws, '#one')
  app.processEvents()
  check('#one' not in visible_subs(ws),
        'clicking the active tab while tiled left its window on screen '
        '(showing %r)' % (visible_subs(ws),))
  check(ws._active is not None and ws._active['title'] != '#one',
        'clicking the active tab while tiled did not move on to another window')

  click(ws, ws._active['title'])
  click(ws, ws._active['title'])
  app.processEvents()
  check(ws._active is None,
        'clicking every tab while tiled left %r active'
        % (ws._active and ws._active['title'],))
  check(visible_subs(ws) == [],
        'every tab was skipped but the tiled workspace is still showing %r '
        'instead of its background' % (visible_subs(ws),))

  # ------------------------------------------------------------------ 3
  # And back: clicking a skipped tab brings its window back into the tiling.
  click(ws, '#two')
  app.processEvents()
  check(visible_subs(ws) == ['#two'],
        'clicking a skipped tab while tiled showed %r' % (visible_subs(ws),))
  check(ws._active is not None and ws._active['title'] == '#two',
        'clicking a skipped tab while tiled did not activate it')

  # ------------------------------------------------------------------ 4
  # A tile only lays out the windows that are showing; a skipped one must not
  # be given a share of the workspace and then left empty.
  #
  # Tiled against a workspace whose scroll bars are up, too.  They are
  # AsNeeded, so the arrangement a tile produces decides whether they stay --
  # and a tiler that measures the viewport while a bar left over from the
  # previous arrangement is still up lays its rows out one scroll bar extent
  # short, then removes the bar it made room for and leaves a dead strip along
  # the edge.  Overflowing the workspace first makes that certain rather than a
  # matter of what the last arrangement happened to leave behind, which is what
  # made this an intermittent failure.
  find(ws, '#two')['proxy']._mdi_sub.setGeometry(0, 0, 2000, 2000)
  app.processEvents()
  check(ws._mdi.verticalScrollBar().isVisible(),
        'the workspace did not scroll for a window twice its size, so the tile '
        'below is not being tested against the case that matters')

  ws.tileVertically()
  app.processEvents()
  vp_h = ws._mdi.viewport().height()
  sub = find(ws, '#two')['proxy']._mdi_sub
  check(abs(sub.height() - vp_h) <= 2,
        'a vertical tile with one window showing gave it %d of %d pixels -- '
        'either the skipped windows were counted, or the workspace was measured '
        'with a scroll bar up that the tile then removed' % (sub.height(), vp_h))
  ws.deleteLater()

  # ------------------------------------------------------------------ 5
  # Skips made before the tile survive it: entering MDI mode renders the tab
  # states it finds rather than showing everything unconditionally.
  ws = make_workspace(app)
  click(ws, '#one')
  ws.tileSubWindows()
  app.processEvents()
  check('#one' not in visible_subs(ws),
        'tiling un-skipped a window that had been skipped beforehand (showing '
        '%r)' % (visible_subs(ws),))

  # ------------------------------------------------------------------ 6
  # A window opened while tiled belongs in the MDI area. Adding it to the
  # QStackedWidget instead -- which is hidden behind the MDI area -- leaves it
  # invisible and unreachable until the user happens to maximize.
  add(ws, '#late')
  app.processEvents()
  late = find(ws, '#late')
  check(late['proxy']._mdi_sub is not None,
        'a window opened while tiled got no subwindow, so it went into the '
        'hidden stack')
  check('#late' in visible_subs(ws),
        'a window opened while tiled is not showing: %r' % (visible_subs(ws),))
  click(ws, '#late')          # not active yet, so this activates it
  check(ws._active is late, 'a window opened while tiled ignored its own tab')
  click(ws, '#late')          # and now it is, so this skips it
  app.processEvents()
  check('#late' not in visible_subs(ws),
        'a window opened while tiled cannot be skipped again')

  # ...and it opens in the state the config asks for. new_tab_state: skipped
  # means a channel that joins by itself must not take the screen, which in a
  # tiled workspace means its subwindow must never be shown in the first place.
  state.config.new_tab_state = 'skipped'
  try:
    add(ws, '#quiet')
    app.processEvents()
  finally:
    state.config.new_tab_state = 'normal'
  check('#quiet' not in visible_subs(ws),
        'a window opened while tiled ignored new_tab_state: skipped and showed '
        'itself anyway (showing %r)' % (visible_subs(ws),))

  # ------------------------------------------------------------------ 7
  # The subwindow's own minimize button is the tab click: off the screen and on
  # to the next window. Left to Qt it would be an iconified stub parked in the
  # corner of the workspace instead -- a second, worse tab bar.
  ws._activate(find(ws, '#three'))
  app.processEvents()
  find(ws, '#three')['proxy']._mdi_sub.showMinimized()
  app.processEvents()
  check('#three' not in visible_subs(ws),
        'minimizing a tiled subwindow left it on screen: %r' % (visible_subs(ws),))
  check(find(ws, '#three')['state'] == ws.SKIPPED,
        'minimizing a tiled subwindow did not skip its tab (state %r)'
        % (find(ws, '#three')['state'],))
  check(ws._active is not None and ws._active['title'] != '#three',
        'minimizing a tiled subwindow did not move on to another window')
  # ...and it comes back full size, not as the icon Qt left it as.
  click(ws, '#three')
  app.processEvents()
  check(not find(ws, '#three')['proxy']._mdi_sub.isMinimized(),
        'a window restored from a minimize came back as an icon')

  # ------------------------------------------------------------------ 8
  # Maximizing a tile is the tabbed look, so the workspace has to adopt it.
  # Otherwise the user is left in MDI mode with nothing on screen to say so --
  # which is how the skipping bug above reached them without their ever having
  # asked for a tiled workspace.
  ws._activate(find(ws, '#two'))
  app.processEvents()
  find(ws, '#two')['proxy']._mdi_sub.showMaximized()
  app.processEvents()
  check(not ws._tiled,
        'maximizing a tiled subwindow left the workspace in MDI mode')
  check(ws._stack.isVisible() and not ws._mdi.isVisible(),
        'maximizing a tiled subwindow did not hand the workspace back to the '
        'stack')
  check(ws._stack.currentWidget() is find(ws, '#two')['widget'],
        'maximizing a tiled subwindow lost the window that was maximized')

  # ------------------------------------------------------------------ 9
  # Leaving MDI mode must leave no subwindows behind: QMdiArea.removeSubWindow
  # only takes the *widget* out of its subwindow, and an orphaned subwindow is
  # an empty frame that the next tile lays out alongside the real ones.
  check(ws._mdi.subWindowList() == [],
        'leaving MDI mode left %d subwindow(s) behind'
        % len(ws._mdi.subWindowList()))

  # ----------------------------------------------------------------- 10
  # ...and the two containers have to look the same where nothing is showing,
  # since either one can be the thing behind an all-skipped workspace.
  check(ws._mdi.background().color() == ws._bar_bg,
        'the MDI background is %r, not the tab bar colour %r the blank widget '
        'uses' % (ws._mdi.background().color().name(), ws._bar_bg.name()))
  ws.deleteLater()


def main():
  app = QApplication.instance() or QApplication([])
  state.config = FakeConfig()
  try:
    run(app)
  except Exception:
    import traceback
    traceback.print_exc()
    return 1
  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('All tab-skip checks passed.')
  return 0


from tabbar import TabbedWorkspace  # noqa: E402  (needs QT_QPA_PLATFORM first)

sys.exit(main())
