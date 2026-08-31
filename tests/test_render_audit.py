"""The duplicate-render audit (render_audit.py).

A user report -- "some messages I send show up twice" -- that the data could not
explain: no duplicate rows in the history database, no duplicate lines in the
file logs, and no live echo from the bouncer. So the second copy is *rendered
and nothing else*, and the only paths that render without logging are the
history replay, the lazy scroll-up prepend, and the flush of the queue that
holds live output back while a replay runs. render_audit.py is the
instrumentation that names which of them did it.

Two invariants decide whether that instrumentation is worth having, and both are
easy to get wrong in a way that only shows up as a log full of noise:

  1. **A call that draws nothing is not a render.** The addline_* methods put
     themselves on a queue when a replay is pending, and later the flush calls
     them again for real. Counting the queued call would flag every held-back
     line against its own flush -- the ordinary, correct case -- and bury the
     bug in false positives.
  2. **The key is content, not appearance.** The same line drawn by the live
     path and by the replay path carries two different timestamps (server tag
     vs stored row) and may carry a different mode prefix, and each path picks
     its own QTextCharFormat. A key that included any of those would never
     match, and the audit would report nothing at all.
  3. **Identical text is not the same line.** Something has to decide, or a
     replay -- thousands of stored rows drawn in milliseconds, so the wall-clock
     look-back window cannot separate any two of them -- makes the audit useless;
     that was 979 of the first 1000 reports. The deciding fact is the history row
     id, carried out of the SQL for this. The displayed timestamp is only the
     fallback for renders that name no row, and it is not sufficient on its own:
     it is HH:MM, so a line said daily at the same minute passes it.

Exercised against the real Window methods -- a bare instance with a real
QTextEdit for its chat view -- so the wrappers are proved against the methods
they actually wrap, not against a mock of them.

Runs headless (offscreen Qt platform), so it needs no display.

Usage:
  python tests/test_render_audit.py     # from the qtpyrc root directory
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from PySide6.QtWidgets import QApplication, QTextEdit
from PySide6.QtGui import QTextCharFormat, QTextCursor

import config as configmod
import state
import render_audit

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


# ---------------------------------------------------------------------------
# 1. What counts as "the same line".
# ---------------------------------------------------------------------------

def test_render_key():
  k = render_audit.render_key

  # The timestamp is not content, so it is not part of the key. (Whether two
  # renders sharing a key are the same *line* is a separate question, decided by
  # _same_line and tested below.)
  check(k('addline_msg', ('inhahe', 'hello'), {'timestamp_override': None})
        == k('addline_msg', ('inhahe', 'hello'), {'timestamp_override': '11:02'}),
        'the timestamp changed the key, so a live line and its replayed copy '
        'would never be recognised as the same line')

  # Trailing whitespace: a server may return its own copy of a line with it
  # stripped (Libera does), so the two copies of the real duplicate-message bug
  # differ by one invisible character. Keying on the raw text made the audit
  # blind to the exact case it was written for.
  check(k('addline_msg', ('inhahe', "too bad quodlibet isn't here. "), {})
        == k('addline_msg', ('inhahe', "too bad quodlibet isn't here."), {}),
        'a trailing space changed the key -- the audit cannot see a duplicate '
        'whose second copy came back from the server with whitespace stripped, '
        'which is the shape of the bug it exists for')

  # Leading whitespace is content: it is how quoted or code text is indented,
  # and no server strips it.
  check(k('addline', ('    indented',), {}) != k('addline', ('indented',), {}),
        'leading whitespace was stripped, so indentation is invisible to the '
        'audit')

  # Formats are appearance, and the two paths choose them independently.
  fmt = QTextCharFormat()
  check(k('addline', ('some text',), {'fmt': fmt})
        == k('addline', ('some text',), {'fmt': None}),
        'a QTextCharFormat took part in the key')

  # /pnick decorates the live copy with the sender's channel mode; the replayed
  # copy is decorated from the stored prefix column, which can disagree.
  check(k('addline_msg', ('@inhahe', 'hello'), {})
        == k('addline_msg', ('inhahe', 'hello'), {}),
        'a mode prefix changed the key')

  # But real content still has to be distinguishable.
  check(k('addline_msg', ('inhahe', 'hello'), {})
        != k('addline_msg', ('someone', 'hello'), {}),
        'two different senders of the same text produced the same key')
  check(k('addline_msg', ('inhahe', 'hello'), {})
        != k('addline_msg', ('inhahe', 'hello there'), {}),
        'two different messages produced the same key')
  check(k('addline_msg', ('inhahe', 'hello'), {})
        != k('addline_nick', ('inhahe', 'hello'), {}),
        'a message and a notice with the same body produced the same key')

  # addline_nick's mixed list of strings and (nick,) / (text, href) tuples.
  parts = ['* ', ('inhahe',), ' has joined #chan']
  check(k('addline_nick', (parts,), {'fmt': fmt})
        == k('addline_nick', (['* ', ('inhahe',), ' has joined #chan'],), {}),
        'addline_nick parts were not flattened to the same key')


# ---------------------------------------------------------------------------
# 2. The audit against the real Window render methods.
# ---------------------------------------------------------------------------

def make_window(Window):
  """A Window with just enough of one to render into a real chat view.

  Not a constructed widget: Window.__init__ builds a search bar, an input box
  and an MDI subwindow, none of which any render method touches. The chat view
  itself is real, because the audit decides whether a call rendered by asking
  the document whether it grew -- a mock document would be the test proving
  itself.
  """
  w = Window.__new__(Window)
  w.output = QTextEdit()
  w.vs = w.output.verticalScrollBar()
  w.cur = QTextCursor(w.output.document())
  w._replay_queue = None
  w._replay_cutoff_id = None
  w._in_replay = False
  w._prepending = False
  w._history_more = None
  w._auto_scroll = True
  w._programmatic_scroll = False
  # Short-circuits _updateBottomAlign, which would want a QTimer parented to a
  # widget this instance never became.
  w._bottom_align_filled = True
  return w


def now_hhmm():
  """The minute a line drawn right now is stamped with.

  A stored row states its minute and a live line does not, so the audit fills
  the live side in from the clock (see render_audit._same_line). A test pairing
  a live render against a replayed one therefore has to stamp the replayed one
  with the same minute -- which is what the real duplicate looks like anyway: a
  message and the server's echo of it, seconds apart."""
  from datetime import datetime
  return datetime.now().strftime('%H:%M')


def reports_in(path):
  """Every duplicate report in the audit log, as blocks of text."""
  if not os.path.exists(path):
    return []
  with open(path, encoding='utf-8') as f:
    text = f.read()
  return [b for b in text.split('*** DUPLICATE RENDER')[1:]]


def test_history_row_identity(Window, logpath):
  """The displayed timestamp is HH:MM, so it cannot separate a line from itself.

  Report #34 on the user's machine was astroo-'s "hello people", rendered twice
  at 17:00, from two stacks that both said "_render_history_row <- _bg_replay_loop"
  -- the exact signature of the drip-feed drawing one stored row twice, which is
  the bug being hunted. It was not that. astroo- posts "hello people" at ~17:00
  *every day*; ##audio alone holds it at 2026-08-21 17:00:50, 2026-08-22 17:00:45
  and 2026-08-24 17:00:49. Three different rows, one displayed time.

  So the replay paths name the row they are drawing (render_audit.source_id, set
  by _render_history_row), and two renders that name two different rows are two
  different lines by definition. Driven through the real _render_history_row
  rather than by setting the context directly: the plumbing from the SQL row to
  the audit is the part that can break.
  """
  from irc_client import _render_history_row

  def row(rid, ts, text, etype='message', nick='astroo-'):
    return (rid, ts, etype, nick, text, '')

  # -- the same line on three different days is not a duplicate, however
  #    identical it looks on screen.
  before = len(reports_in(logpath))
  d = make_window(Window)
  _render_history_row(d, '#audio', row(101, '2026-08-21 17:00:50',
                                       'hello people'), False)
  _render_history_row(d, '#audio', row(202, '2026-08-22 17:00:45',
                                       'hello people'), False)
  _render_history_row(d, '#audio', row(303, '2026-08-24 17:00:49',
                                       'hello people'), False)
  check(len(reports_in(logpath)) == before,
        'a line stored three times on three different days -- all shown at '
        '17:00, which is all the timestamp can say -- was reported as a '
        'duplicate. This was report #34, and the whole class of them.')

  # -- but one row drawn twice is exactly the bug, even though it looks no
  #    different from the case above.
  before = len(reports_in(logpath))
  _render_history_row(d, '#audio', row(202, '2026-08-22 17:00:45',
                                       'hello people'), False)
  after = reports_in(logpath)
  check(len(after) == before + 1,
        'one history row rendered twice was not reported -- which is the '
        'entire point of carrying the row id')
  if len(after) > before:
    check('202' in after[-1],
          'the report did not name the row that was drawn twice:\n%s'
          % after[-1])

  # -- the ids must be compared, not merely present: a *different* row with the
  #    same text and the same displayed minute is the case above; a live copy
  #    with no row at all must still pair with a stored one, because that is the
  #    live-vs-replay duplicate the audit exists for.
  before = len(reports_in(logpath))
  live = make_window(Window)
  # Stored a moment ago, which is what the server's echo of a line just sent
  # looks like -- the live copy's own stamp is the clock.
  _render_history_row(live, '#audio', row(404, '2026-08-26 ' + now_hhmm() + ':01',
                                          'said live then replayed'), False)
  live.addline_msg('astroo-', 'said live then replayed')
  check(len(reports_in(logpath)) == before + 1,
        'a stored row and an unstamped live render of the same text were not '
        'paired; naming rows must not lose the duplicate that is a live line '
        'against its replayed copy')

  # -- but a live line only pairs with a stored row shown at the same *minute*.
  #    A live render states no timestamp, and taking that to mean "matches any
  #    stored row" is how a rejoin gets reported: the drip-feed draws one of the
  #    dozens of identical "* inhahe has joined ##audio" lines in the backlog
  #    (01:42) and the flush of the hold-back queue draws the live one (15:15) --
  #    reports #207 and #208 on the user's machine, thirteen and a half hours
  #    apart and 14.5 seconds apart on screen. A live line is stamped with the
  #    clock, so the audit has to read the clock too.
  before = len(reports_in(logpath))
  j = make_window(Window)
  parts = ['* ', ('inhahe',), ' has joined ##audio']
  _render_history_row(j, '#audio', row(505, '2026-08-26 01:42:03', '##audio',
                                       etype='join', nick='inhahe'), False)
  j.addline_nick(list(parts), state.infoformat)       # the live rejoin, now
  check(len(reports_in(logpath)) == before,
        'a live rejoin was paired with an identical join replayed from hours '
        'earlier; a live render carries the wall clock, not "any time at all"')

  # -- and the context must not leak out of the row it belongs to. If it did,
  #    every live line drawn after a replay would inherit the last row's id and
  #    stop matching anything.
  check(render_audit._state['source'] is None,
        'render_audit.source_id did not clear itself; every subsequent live '
        'render would be attributed to the last history row drawn')


def test_against_real_window():
  from window import Window

  logpath = os.path.join(tempfile.gettempdir(), 'qtpyrc_render_audit_test.log')
  if os.path.exists(logpath):
    os.remove(logpath)
  if not render_audit.install(enabled=True, logfile=logpath,
                              window_seconds=120.0):
    check(False, 'render_audit.install() refused to install')
    return

  # Every method the audit claims to watch has to exist on Window, or the audit
  # is quietly watching less than it says it is.
  for name in render_audit.ENTRY_POINTS:
    func = getattr(Window, name, None)
    check(func is not None,
          'render_audit watches %r, which Window does not have' % name)
    check(getattr(func, '__wrapped__', None) is not None,
          'Window.%s was not wrapped by install()' % name)

  # -- a line rendered once is not a duplicate
  w = make_window(Window)
  w.addline_msg('inhahe', 'a line said only once')
  check(len(reports_in(logpath)) == 0,
        'a single render was reported as a duplicate')

  # -- the same line rendered twice into the same window is
  before = len(reports_in(logpath))
  w.addline_msg('inhahe', 'said twice')
  w.addline_msg('inhahe', 'said twice')
  after = reports_in(logpath)
  check(len(after) == before + 1,
        'rendering the same line twice produced %d reports, expected 1'
        % (len(after) - before))
  if len(after) > before:
    body = after[-1]
    check('said twice' in body,
          'the report did not quote the duplicated text:\n%s' % body)
    check('test_render_audit.py' in body,
          'the report did not name the caller of either render:\n%s' % body)

  # -- the live/replayed shape of the same line: one copy stamped from a stored
  #    row and one stamped "now", mode prefix on one copy only. This is the
  #    actual bug's signature, and the one the key normalisation exists for.
  before = len(reports_in(logpath))
  w2 = make_window(Window)
  w2.addline_msg('inhahe', 'the message in question')
  w2.addline_msg('@inhahe', 'the message in question',
                 timestamp_override=now_hhmm())
  check(len(reports_in(logpath)) == before + 1,
        'a live line and its replayed copy -- same text, different timestamp '
        'and mode prefix -- were not recognised as the same line, which is '
        'the case the audit exists for')

  # -- two windows are two conversations
  before = len(reports_in(logpath))
  a, b = make_window(Window), make_window(Window)
  a.addline_msg('inhahe', 'same words, different windows')
  b.addline_msg('inhahe', 'same words, different windows')
  check(len(reports_in(logpath)) == before,
        'the same line in two different windows was reported as a duplicate')

  # -- THE invariant: a call held back by the replay queue draws nothing, so
  #    the flush that finally draws it is the first render, not the second.
  before = len(reports_in(logpath))
  h = make_window(Window)
  h.begin_replay_queue()
  h.addline_msg('inhahe', 'arrived while the backlog was loading')
  check(h.output.document().characterCount() <= 1,
        'the held-back line was rendered immediately, so this is not testing '
        'the hold-back path')
  h._flush_replay_queue()
  check('arrived while the backlog' in h.output.toPlainText(),
        'the flush did not render the held-back line')
  check(len(reports_in(logpath)) == before,
        'a line that was queued and then flushed was reported as a duplicate '
        '-- the audit counted the queued call, which draws nothing, as a '
        'render. Every held-back line would be a false positive.')

  # -- the same words at two different displayed times are two different lines.
  #    This is the *fallback* test, used when at least one of the two renders
  #    named no history row. It is what makes the audit usable at all during a
  #    replay: a replay draws thousands of stored rows in a few milliseconds, so
  #    the look-back window (wall clock, because for live output that is the only
  #    clock there is) cannot separate them. On the user's machine 979 of the
  #    first 1000 reports were this -- an identical join/part/quit line from a
  #    different hour -- and they buried the 21 real ones.
  before = len(reports_in(logpath))
  r = make_window(Window)
  parts = ['* ', ('someone',), ' has joined #chan']
  r.addline_nick(list(parts), state.infoformat, timestamp_override='00:35')
  r.addline_nick(list(parts), state.infoformat, timestamp_override='01:12')
  r.addline_nick(list(parts), state.infoformat, timestamp_override='04:45')
  check(len(reports_in(logpath)) == before,
        'a line shown at three different times was reported as a duplicate; '
        'during a history replay the audit is nothing but this')

  # ...but two renders that agree on the displayed time, with nothing more
  # precise to go on, cannot be two different moments in the conversation.
  before = len(reports_in(logpath))
  r.addline_nick(list(parts), state.infoformat, timestamp_override='01:12')
  check(len(reports_in(logpath)) == before + 1,
        'the same line rendered twice at the same displayed time was not '
        'reported -- suppressing the different-time case must not suppress '
        'the identical-time one, which is a genuine double render')

  # A live render states no timestamp, but it is not therefore timeless: it must
  # still pair with a replayed copy of the same minute. That is the live-vs-replay
  # duplicate -- a message and the server's echo of it seconds later -- and
  # narrowing on the minute must not lose it.
  before = len(reports_in(logpath))
  m = make_window(Window)
  m.addline_msg('inhahe', 'said live then replayed',
                timestamp_override=now_hhmm())
  m.addline_msg('inhahe', 'said live then replayed')
  check(len(reports_in(logpath)) == before + 1,
        'a stamped replay copy and an unstamped live copy were not paired')

  # -- the whole bug, end to end: the live copy as typed, and the server's copy
  #    with its trailing whitespace stripped.
  before = len(reports_in(logpath))
  w3 = make_window(Window)
  w3.addline_msg('inhahe', "too bad quodlibet isn't here. ")
  w3.addline_msg('inhahe', "too bad quodlibet isn't here.",
                 timestamp_override=now_hhmm())
  check(len(reports_in(logpath)) == before + 1,
        "the real duplicate -- local echo as typed, server echo with the "
        "trailing space stripped -- was not detected")

  test_history_row_identity(Window, logpath)

  # -- other entry points are watched too, not just addline_msg
  before = len(reports_in(logpath))
  n = make_window(Window)
  parts = ['* ', ('inhahe',), ' has joined #chan']
  n.addline_nick(list(parts), state.infoformat)
  n.addline_nick(list(parts), state.infoformat, timestamp_override=now_hhmm())
  check(len(reports_in(logpath)) == before + 1,
        'addline_nick renders were not audited')

  # -- a look-back window of zero length means nothing is ever a duplicate,
  #    which is how the setting is meant to behave at its lower bound
  before = len(reports_in(logpath))
  render_audit.install(enabled=True, logfile=logpath, window_seconds=-1)
  z = make_window(Window)
  z.addline_msg('inhahe', 'window clamped')
  z.addline_msg('inhahe', 'window clamped')
  check(len(reports_in(logpath)) == before + 1,
        'the look-back window was not clamped to a sane minimum (a negative '
        'value should not disable detection outright)')
  render_audit.install(enabled=True, logfile=logpath, window_seconds=120.0)

  # -- turning it off stops the reporting
  before = len(reports_in(logpath))
  render_audit.stop()
  off = make_window(Window)
  off.addline_msg('inhahe', 'audit is off')
  off.addline_msg('inhahe', 'audit is off')
  check(len(reports_in(logpath)) == before,
        'reports were still written after render_audit.stop()')
  check('audit is off' in off.output.toPlainText(),
        'stopping the audit stopped the window rendering as well')

  # _write() opens and closes per line, so nothing is holding this now.
  try:
    os.remove(logpath)
  except OSError:
    pass


def main():
  QApplication.instance() or QApplication([])
  yaml = YAML()
  path = os.path.join(ROOT, 'defaults', 'config.defaults.yaml')
  state.config = configmod.AppConfig(path, CommentedMap(), yaml)
  configmod._update_text_formats(state.config)

  test_render_key()
  test_against_real_window()

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('All render-audit checks passed: a line drawn twice is reported with '
        'both stacks, and a line merely queued is not.')
  return 0


sys.exit(main())
