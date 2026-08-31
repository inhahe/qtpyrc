"""Tab-activity colouring and the live-output hold-back queue.

A window whose saved history hasn't finished loading holds its live output in
a replay queue and renders it once the backlog is in (window.py:
begin_replay_queue / _queue_if_replaying / _flush_replay_queue). Three things
went wrong in that mechanism, all of which hit the same case -- a query window
opened *by* an incoming private message, which is created with a replay already
pending and whose very first message therefore always lands mid-replay:

  1. set_activity() returned early whenever a replay was pending, so the tab
     never turned red. The mark was dropped, not deferred: nothing re-applies
     it when the replay finishes.
  2. The drip-feed loop assigned a fresh queue when it reached the window,
     discarding whatever the opening handler had already queued -- i.e. the
     message itself.
  3. Dropping a window from the drip-feed (nothing to replay, or replay
     disabled for it) left the queue open forever, so every later line was
     swallowed too and the window stayed permanently blank.
  4. Held-back lines are written to the history table as they arrive, so once
     they were no longer being swallowed the replay rendered them too and the
     flush then repeated every one of them -- the opening message of a query
     window appeared twice. Opening the queue now snapshots the table's last id
     (Window._replay_cutoff_id) and replay reads stop there.

These exercise the real Window methods on a bare instance -- the pieces under
test touch no widgets -- plus the real qtpyrc._bg_replay_drop and a real
on-disk HistoryDB.

Usage:
  python tests/test_activity_replay.py     # from the qtpyrc root directory
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor

import state


class FakeWorkspace(object):
  """A TabbedWorkspace as far as _apply_activity_color is concerned."""

  def __init__(self):
    self.colors = {}     # subwindow -> colour, or None once cleared
    self.active = None

  def activeSubWindow(self):
    return self.active

  def set_activity_color(self, subwindow, color):
    self.colors[subwindow] = color

  def clear_activity_color(self, subwindow):
    self.colors[subwindow] = None


class FakeMainWin(object):
  def __init__(self):
    self.workspace = FakeWorkspace()
    self.network_tree = None


class FakeApp(object):
  def __init__(self):
    self.mainwin = FakeMainWin()


class FakeConfig(object):
  color_highlight = QColor('red')
  color_new_message = QColor('blue')
  fgcolor = QColor('black')


def make_window(Window):
  """A Window with just the attributes the activity/replay code touches.

  Deliberately not a constructed widget: __init__ builds a whole chat view, and
  none of the methods under test go near one.
  """
  w = Window.__new__(Window)
  w._activity = Window.ACTIVITY_NONE
  w._replay_queue = None
  w._in_replay = False
  w.subwindow = object()
  return w


def main():
  failures = []

  def check(cond, msg):
    if not cond:
      failures.append(msg)

  state.app = FakeApp()
  state.config = FakeConfig()

  from window import Window
  import qtpyrc

  ws = state.app.mainwin.workspace

  # ---------------------------------------------------------------- 1
  # A live message on a window that is still loading its backlog must colour
  # the tab. This is the whole bug: a query opened by a PM is created with
  # _deferred_replay already set, so the opening message arrives while it is.
  w = make_window(Window)
  w._deferred_replay = ('net', '=someone:ident', None)
  w.begin_replay_queue()
  w.set_activity(Window.ACTIVITY_HIGHLIGHT)
  check(w._activity == Window.ACTIVITY_HIGHLIGHT,
        'a pending replay suppressed the activity level (got %r)' % w._activity)
  check(ws.colors.get(w.subwindow) == FakeConfig.color_highlight,
        'a pending replay left the tab uncoloured (got %r)'
        % (ws.colors.get(w.subwindow),))

  # ...and the same once the drip-feed is actually mid-flight.
  w2 = make_window(Window)
  w2._bg_replay = {'last_id': 0}
  w2.begin_replay_queue()
  w2.set_activity(Window.ACTIVITY_MESSAGE)
  check(ws.colors.get(w2.subwindow) == FakeConfig.color_new_message,
        'an in-flight replay left the tab uncoloured')

  # The rules that must survive: never mark the window being looked at, and
  # never downgrade a highlight to a plain message.
  ws.active = type('Sub', (), {'widget': lambda s: w2})()
  w3 = make_window(Window)
  ws.active = type('Sub', (), {'widget': lambda s: w3})()
  w3.set_activity(Window.ACTIVITY_HIGHLIGHT)
  check(w3._activity == Window.ACTIVITY_NONE,
        'marked the window the user is looking at')
  ws.active = None
  w.set_activity(Window.ACTIVITY_MESSAGE)
  check(w._activity == Window.ACTIVITY_HIGHLIGHT,
        'a later plain message downgraded a highlight')

  # ---------------------------------------------------------------- 2
  # Opening the queue a second time must keep what is already in it.
  w4 = make_window(Window)
  w4.begin_replay_queue()
  seen = []
  queued = w4._queue_if_replaying('_run_callback', (lambda: seen.append('pm'),), {})
  check(queued, 'output was not held back while a replay was pending')
  w4.begin_replay_queue()          # the drip-feed loop reaching this window
  check(len(w4._replay_queue) == 1,
        're-opening the queue discarded %d held line(s)'
        % (1 - len(w4._replay_queue)))
  w4._flush_replay_queue()
  check(seen == ['pm'], 'held-back output was not rendered after the replay')
  check(w4._replay_queue is None, 'the queue stayed open after flushing')
  check(not w4._queue_if_replaying('_run_callback', (lambda: None,), {}),
        'output was still held back after the replay finished')

  # ---------------------------------------------------------------- 3
  # A window dropped from the drip-feed has no replay coming, so anything held
  # back has to be released -- otherwise it is swallowed for good.
  w5 = make_window(Window)
  w5._deferred_replay = ('net', '#chan', None)
  w5.begin_replay_queue()
  released = []
  w5._queue_if_replaying('_run_callback', (lambda: released.append('line'),), {})
  qtpyrc._bg_replay_drop(w5)
  check(released == ['line'],
        'dropping a window from the drip-feed swallowed its held output')
  check(not hasattr(w5, '_deferred_replay'),
        '_bg_replay_drop left the replay marker behind')
  check(w5._replay_queue is None,
        '_bg_replay_drop left the window holding output forever')

  # ---------------------------------------------------------------- 3b
  # Same rule on the synchronous path. _history_replay() gives up before it
  # renders anything when the window's replay cap is 0 (history_replay.queries:
  # 0 is exactly that, and the settings dialog used to write it into every
  # config that was opened) or when there is no history database at all -- and
  # the queue is already open by then, because joined() and
  # _find_or_create_query() open it when they create the window, long before
  # anyone asks how much there is to replay. Returning without flushing leaves
  # the window mute for the rest of the session.
  import irc_client
  for label, limit, db_present in (('a replay cap of 0', 0, True),
                                   ('no history database', 10, False)):
    w5b = make_window(Window)
    w5b.begin_replay_queue()
    freed = []
    w5b._queue_if_replaying('_run_callback', (lambda: freed.append('line'),), {})
    saved_db = state.historydb
    if not db_present:
      state.historydb = None
    try:
      irc_client._history_replay(w5b, 'net', '#chan', limit=limit)
    finally:
      state.historydb = saved_db
    check(freed == ['line'],
          '_history_replay with %s swallowed the window\'s held output' % label)
    check(w5b._replay_queue is None,
          '_history_replay with %s left the window holding output forever'
          % label)

  # ---------------------------------------------------------------- 4
  # The property the old suppression was aiming at: replayed history must not
  # colour tabs. It never could -- render_history_rows() calls the addline_*
  # methods, which have nothing to do with activity -- so removing the gate
  # cannot have reintroduced that.
  import irc_client
  src = open(irc_client.__file__, encoding='utf-8').read()
  start = src.index('def render_history_rows')
  end = src.index('\ndef ', start + 1)
  check('activity' not in src[start:end].lower(),
        'render_history_rows() now touches activity; replayed history would '
        'mark tabs unread')

  # ---------------------------------------------------------------- 5
  # The backlog a window replays has to stop where its held-back queue starts,
  # or the message that opened the window is rendered by both.
  import shutil
  import tempfile
  import history as history_mod

  tmpdir = tempfile.mkdtemp(prefix='qtpyrc-hist-')
  db = history_mod.HistoryDB(os.path.join(tmpdir, 'h.db'))
  try:
    NET, KEY = 'net', '=alice'
    for i in range(3):
      db.add(NET, KEY, 'message', 'alice', 'backlog %d' % i)

    state.historydb = db
    w6 = make_window(Window)
    w6.begin_replay_queue()          # a PM is opening this window right now
    check(w6._replay_cutoff_id == db.current_max_id(),
          'opening the queue did not snapshot the history id (got %r, table is '
          'at %r)' % (w6._replay_cutoff_id, db.current_max_id()))

    # ...and now the message that opened it is saved and held back.
    db.add(NET, KEY, 'message', 'alice', 'the live pm')
    w6._queue_if_replaying('_run_callback', (lambda: None,), {})

    rows = db.get_last(NET, KEY, 50, w6._replay_cutoff_id)
    texts = [r[4] for r in rows]   # (id, ts, type, nick, text, prefix)
    check('the live pm' not in texts,
          'the replay includes the held-back message; it would be shown twice')
    check(len(texts) == 3, 'the replay lost backlog rows (got %r)' % (texts,))

    bounds = db.replay_bounds(NET, KEY, 50, w6._replay_cutoff_id)
    check(bounds is not None and bounds[1] == w6._replay_cutoff_id,
          'the drip-feed bounds run past the cutoff (got %r, cutoff %r)'
          % (bounds, w6._replay_cutoff_id))

    # Without a cutoff nothing is held back, so nothing may be hidden either --
    # this is also what proves the check above is the cutoff doing the work.
    check(len(db.get_last(NET, KEY, 50)) == 4,
          'an uncapped read no longer returns the whole backlog')

    w6._flush_replay_queue()
    check(w6._replay_cutoff_id is None,
          'the cutoff outlived the queue; the next replay would be truncated to '
          'a stale id')
  finally:
    state.historydb = None
    db.close()
    # close() first, or Windows keeps the .db file and the directory survives.
    shutil.rmtree(tmpdir, ignore_errors=True)

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('All activity/replay-queue checks passed.')
  return 0


if __name__ == '__main__':
  sys.exit(main())
