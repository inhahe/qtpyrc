# render_audit.py - catch the same line being drawn into a chat view twice
#
# Why this exists
# ---------------
# A user report: "some messages I send show up twice."  Everything that would
# make that easy to find had already been ruled out by looking at the data --
# the bouncer does not echo the user's own PRIVMSGs live, qtpyrc's history
# database holds no duplicate rows, and its file logs hold no duplicate lines
# and agree with the database.  So the second copy is *rendered and nothing
# else*: it never went through chanmsg()/privmsg()/Commands.say, all three of
# which log unconditionally.
#
# That narrows it to the paths that put text on screen without logging it:
#
#   * the history replay (render_history_rows / _render_history_row), reached
#     from the synchronous on-open replay, the background drip-feed, and the
#     click-to-finish path,
#   * the lazy scroll-up prepend (_prepend_history_rows),
#   * Window._flush_replay_queue(), which re-issues addline_* calls that were
#     held back while a replay was pending -- those were logged when they
#     arrived, so a second *render* of one leaves no second log line.
#
# A static audit of the cutoff machinery that is supposed to keep those from
# overlapping (Window._replay_cutoff_id, history._id_cap) found no hole, which
# is the point at which guessing stops being useful.  This module is the
# instrumentation instead: it names the path, with the stack of *both* renders,
# the first time it happens on the user's machine.  Same approach that made the
# native GUI stalls diagnosable (hang_watchdog.py).
#
# How it works
# ------------
# install() wraps the Window methods that put a line in a chat view.  Each
# wrapper reduces the call to the text it is about to draw, and remembers that
# text against the window it was drawn into.  Draw the same text into the same
# window twice inside the configured time window and both stacks are written to
# the log.
#
# Three things keep it honest and cheap:
#
#   * A call that is *held back* rather than drawn is not recorded.  The
#     addline_* methods queue themselves when a replay is pending, and counting
#     that as a render would flag every held-back line against its own flush --
#     which is the ordinary, correct case.  Whether a call drew anything is
#     decided by whether the document grew, so no knowledge of the hold-back
#     rules is duplicated here.
#   * The key is content only.  QTextCharFormats are excluded (each path picks
#     its own), mode prefixes are stripped from the front of every string
#     (/pnick decorates the live copy, the stored prefix column decorates the
#     replayed one), and *trailing whitespace is stripped* -- a server may return
#     its own copy of a line with it removed, which is precisely the shape of the
#     duplicate this module was written for and which it was at first blind to.
#   * Identical text is not enough to make two renders the same *line*, and the
#     audit drowns without a test that decides.  A replay draws thousands of rows
#     in milliseconds, so the wall-clock look-back window -- the only clock live
#     output has -- cannot tell a "* nick has joined" from 00:35 apart from an
#     identical one from 01:12; that was 979 of the first 1000 reports on the
#     user's machine.  Two tests, in order of authority:
#       - the history row id, set by whoever is drawing a stored row
#         (source_id() / _render_history_row).  Two renders that name two
#         different rows are two different lines, full stop.  This is exact.
#       - failing that (a live render names no row), the minute each was shown
#         at, within a minute: the stored row's stamp on one side, the wall clock
#         on the other, because that is what a live line is stamped with.  Only a
#         fallback: it is HH:MM, so a line said daily at the same minute still
#         collides with itself, which is exactly how report #34 -- astroo-'s
#         "hello people", posted at ~17:00 every day -- survived it.
#   * Stacks are collected by walking f_back, not by traceback.extract_stack(),
#     which reads source lines off disk -- on the GUI thread, once per rendered
#     line, during a replay of thousands.
#
# It is on by default because it is one dict lookup per rendered line and the
# bug it is looking for is live in the field.  logging.render_audit.enabled
# turns it off.

import os
import sys
import time
from collections import OrderedDict
from datetime import datetime

# The Window methods that put a line in a chat view. Anything added to Window
# that renders a line belongs here too; a method missing from this list is
# simply not audited, it does not break.
ENTRY_POINTS = ('addline', 'addline_nick', 'addline_msg', 'redmessage',
                'addlinef')

# Mode/prefix decoration that differs between the live and replayed copies of
# the same line.
_PREFIX_CHARS = '~&@%+'

# How many recent lines to remember per window. A duplicate that only shows up
# further back than this is a scroll-up artefact, which the time window below
# would have discarded anyway.
_MAX_KEYS = 600

# How many renders of one identical text to keep. A line repeated at many
# different times ("* nick has joined", "ok") is ordinary; only the newest few
# can plausibly pair with an incoming one.
_MAX_PER_KEY = 8

# How deep to record each stack. Deep enough to get from the addline_* call out
# to the driver (_bg_replay_loop, _flush_replay_queue, docommand, ...).
_STACK_DEPTH = 16

# Stop writing after this many reports in one session, so a pathological loop
# cannot fill the disk. The first few are what matter.
_MAX_REPORTS = 500

_state = {
  'enabled': False,
  'logfile': None,
  'window': 120.0,
  'reports': 0,
  'installed': False,
  # The history row the render currently in progress is drawing, or None when
  # the render is live output and names no stored row. See source_id().
  'source': None,
}


class source_id(object):
  """Name the history row that the renders inside this block are drawing.

  Used by every path that turns a stored row into a visible line
  (irc_client._render_history_row).  Without it the audit has only the text and
  the displayed HH:MM to decide whether two identical-looking lines are the same
  line, and neither is exact -- a line said every day at the same minute looks
  like a duplicate of itself.  With it, two renders naming two different rows are
  two different lines by definition.

  A render that names no row (live output, a separator) leaves it None, which
  matches anything: the live copy of a line and the stored row a replay draws are
  genuinely the same line, and that pairing is the whole point of the audit.

  Cheap enough for the inner loop of a replay of several thousand rows: two dict
  stores per row, and nothing at all beyond that when the audit is off."""

  __slots__ = ('rid',)

  def __init__(self, rid):
    self.rid = rid

  def __enter__(self):
    _state['source'] = self.rid
    return self

  def __exit__(self, exc_type, exc, tb):
    _state['source'] = None
    return False


def _stamp():
  return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def _write(text):
  """Append *text* to the audit log and echo it to the console."""
  line = text.rstrip('\n')
  try:
    print(line, flush=True)
  except Exception:
    pass
  path = _state.get('logfile')
  if not path:
    return
  try:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
      os.makedirs(d, exist_ok=True)
    with open(path, 'a', encoding='utf-8', errors='replace') as f:
      f.write(line + '\n')
  except Exception:
    pass


def _flatten(obj, out):
  """Collect every string in *obj* into *out*, in order.

  Handles the shapes the addline_* methods accept: a bare string, and
  addline_nick's list of strings and (nick,) / (text, href) tuples. Everything
  else -- QTextCharFormat above all -- is styling rather than content and is
  dropped, because the live and replayed copies of a line choose their formats
  independently and would otherwise never compare equal."""
  if isinstance(obj, str):
    # rstrip() is not cosmetic. A server may return its own copy of a line with
    # trailing whitespace removed (Libera does), so the two copies of exactly the
    # duplicate this module exists to catch differ by a space that is invisible
    # everywhere it is displayed. Keying on the raw text made the audit blind to
    # it: the user's report was reproduced in his own history database as two
    # rows differing only in a trailing space, and not one of the 1000 reports in
    # renders.log was that message.
    out.append(obj.lstrip(_PREFIX_CHARS).rstrip())
  elif isinstance(obj, (list, tuple)):
    for item in obj:
      _flatten(item, out)


def render_key(name, args, kwargs):
  """Reduce one addline_* call to the text it will draw.

  *name* is part of the key so that, say, a notice and a message with the same
  body are not confused for each other."""
  parts = []
  _flatten(args, parts)
  for k in sorted(kwargs):
    # timestamp_override is deliberately excluded: the live path stamps a line
    # from the server's time tag and the replay path from the stored row, so
    # the same line legitimately carries two different stamps -- which is what
    # makes the duplicate visible to the user in the first place.
    if k == 'timestamp_override':
      continue
    _flatten(kwargs[k], parts)
  return (name, '\x00'.join(parts))


def _stack(skip):
  """Frames from the caller outward, innermost first.

  Walks f_back instead of using traceback.extract_stack(), which opens the
  source file for every frame to fetch the text of the line. That is fine once
  per stall; it is not fine once per rendered line with a replay of several
  thousand of them on the GUI thread."""
  out = []
  try:
    frame = sys._getframe(skip)
  except (ValueError, AttributeError):
    return out
  while frame is not None and len(out) < _STACK_DEPTH:
    code = frame.f_code
    out.append('%s:%d %s' % (os.path.basename(code.co_filename),
                             frame.f_lineno, code.co_name))
    frame = frame.f_back
  return out


def _describe(window):
  """A short name for *window*, for the report."""
  for attr in ('windowTitle', 'objectName'):
    try:
      val = getattr(window, attr)()
    except Exception:
      continue
    if val:
      return '%s (%s)' % (val, type(window).__name__)
  return type(window).__name__


def _seen(window):
  """The per-window record of recently rendered lines.

  Kept on the window so it is collected with it, and so two windows showing the
  same conversation (a channel and its log, a query re-opened under a new
  object) are never confused for one another."""
  seen = window.__dict__.get('_render_audit_seen')
  if seen is None:
    seen = OrderedDict()
    window._render_audit_seen = seen
  return seen


def _describe_render(src, ts):
  """How a render identified itself, for the report."""
  if src is not None:
    return 'history row %s, shown as %s' % (src, ts)
  return 'live, drawn at %s' % (ts,)


def _minutes(hhmm):
  """"HH:MM" as minutes since midnight, or None if it is not that."""
  if not hhmm or len(hhmm) != 5 or hhmm[2] != ':':
    return None
  try:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])
  except ValueError:
    return None


def _minutes_apart(a, b):
  """How far apart two "HH:MM" stamps are, in minutes, or 0 if unknown.

  Unknown counts as zero -- i.e. as agreeing -- because being unable to read a
  timestamp must never be the thing that hides a real duplicate."""
  ma, mb = _minutes(a), _minutes(b)
  if ma is None or mb is None:
    return 0
  d = abs(ma - mb)
  return min(d, 1440 - d)     # wrap at midnight


def _report(window, name, key, cur, prev, now, prev_time):
  """*cur* and *prev* are (time, stack, ts, src) render records."""
  _state['reports'] += 1
  n = _state['reports']
  if n > _MAX_REPORTS:
    return
  lines = ['', '[%s] *** DUPLICATE RENDER #%d in %s -- %.3fs apart ***'
                % (_stamp(), n, _describe(window), now - prev_time),
           '  method: %s' % name,
           '  text:   %r' % key[1].replace('\x00', ' ')]
  lines.append('  first  render (%s):' % _describe_render(prev[3], prev[2]))
  lines.extend('      %s' % f for f in prev[1])
  lines.append('  second render (%s):' % _describe_render(cur[3], cur[2]))
  lines.extend('      %s' % f for f in cur[1])
  if n == _MAX_REPORTS:
    lines.append('  (report limit of %d reached; no more will be written this '
                 'session)' % _MAX_REPORTS)
  _write('\n'.join(lines))


def _same_line(src_a, ts_a, src_b, ts_b):
  """Could two renders of identical text be the same line of conversation?

  Something has to answer this or the audit is unusable during a replay, which
  is the very thing it is watching: a replay draws thousands of rows in a few
  milliseconds, so the look-back window -- measured in wall clock, because for
  live output that is the only clock there is -- cannot separate them. A
  "* nick has joined" from 00:35 and another from 01:12 are two seconds apart on
  screen and two identical strings. That was 979 of the first 1000 reports on
  the user's machine, and it buried the 21 real ones.

  When both renders name a history row (source_id), the row ids settle it
  exactly: different rows are different lines, the same row twice is precisely
  the bug being hunted.

  Otherwise one of them is live output, which names no row, and all that is left
  is the minute each was shown at -- a stored row's is the stamp it displays, a
  live line's is the wall clock, since that is the stamp it displays. One minute
  of slack, because a live line drawn at 19:44:59 and its replayed copy drawn two
  seconds later name different minutes and are still the same line.

  Filling the live side in from the clock is not a detail. Leaving it blank meant
  "matches any stored row", and a channel rejoin renders a live
  "* inhahe has joined ##audio" that pairs with every one of the dozens of
  identical joins in the backlog -- the drip-feed drew one from 01:42 and the
  flush of the hold-back queue drew the live one at 15:15, 14.5 seconds and
  thirteen and a half hours apart, reported as a duplicate.

  The row test is not merely the more precise of the two, it is the only one that
  can be trusted: the displayed stamp is HH:MM, so a line said daily at the same
  minute -- report #34 was astroo-'s "hello people", posted at ~17:00 every day
  and stored three times in ##audio alone -- passes the minute test against its
  own copy from a different day."""
  if src_a is not None and src_b is not None:
    return src_a == src_b
  return _minutes_apart(ts_a, ts_b) <= 1


def note(window, name, args, kwargs, stack_skip=2):
  """Record that *window* just rendered the line described by the call, and
  report it if the same line was rendered there recently.

  Public so a test can drive it without a real QTextDocument."""
  if not _state['enabled']:
    return
  key = render_key(name, args, kwargs)
  if not key[1]:
    return          # nothing visible was drawn; nothing to duplicate
  now = time.monotonic()
  horizon = now - _state['window']
  seen = _seen(window)
  # The minute this render puts on screen. A replayed row states it; a live line
  # is stamped "now", so read the clock -- see _same_line for why leaving it
  # blank is not the same thing. datetime.now() is not on the replay's hot path:
  # every replayed row supplies an override, so the `or` short-circuits.
  ts = kwargs.get('timestamp_override') or datetime.now().strftime('%H:%M')
  src = _state['source']
  cur = (now, _stack(stack_skip), ts, src)
  # Several renders can share one text key -- the same words said again at a
  # different time, or on a different day. Each is kept with the identity it
  # rendered under, and only one that could be the *same line* is a duplicate.
  prev_list = seen.get(key)
  if prev_list is None:
    prev_list = []
    seen[key] = prev_list
  else:
    prev_list[:] = [e for e in prev_list if e[0] >= horizon]
    for prev in prev_list:
      if _same_line(src, ts, prev[3], prev[2]):
        _report(window, name, key, cur, prev, now, prev[0])
        break
  prev_list.append(cur)
  # One text repeated many times at many different times is not interesting, and
  # must not be allowed to grow without bound.
  if len(prev_list) > _MAX_PER_KEY:
    del prev_list[:len(prev_list) - _MAX_PER_KEY]
  seen.move_to_end(key)
  # Evict by age first (cheap: the oldest entries are at the front), then by
  # count, so a quiet window doesn't hold stale keys that could pair with a
  # line rendered hours later.
  while seen:
    oldest = next(iter(seen))
    entries = seen[oldest]
    if entries and entries[-1][0] >= horizon and len(seen) <= _MAX_KEYS:
      break
    del seen[oldest]


def _wrap(name, func):
  """Return *func* with a duplicate check around it.

  The check runs *after* the call and only when the document actually grew: an
  addline_* that queued itself because a replay is pending, or returned early
  because its widget is gone, drew nothing, and treating it as a render would
  flag every held-back line against its own flush -- the ordinary case, not the
  bug. Asking the document is what keeps this from re-implementing (and drifting
  from) the hold-back rules in window.py."""
  def audited(self, *args, **kwargs):
    before = _char_count(self)
    result = func(self, *args, **kwargs)
    if before is not None and _char_count(self) != before:
      # 3 == _stack, note, audited: start the recorded stack at whoever asked
      # for the line, which is the thing the report is trying to name.
      note(self, name, args, kwargs, stack_skip=3)
    return result
  audited.__name__ = name
  audited.__doc__ = func.__doc__
  audited.__wrapped__ = func
  return audited


def _char_count(window):
  """Size of the window's document, or None if it has no live one."""
  try:
    return window.output.document().characterCount()
  except (AttributeError, RuntimeError):
    return None


def install(enabled=True, logfile=None, window_seconds=120.0):
  """Wrap the chat-view render methods with the duplicate check.

  Returns True if the audit is now active. Idempotent: a second call only
  updates the settings, so the methods are never double-wrapped."""
  _state['enabled'] = bool(enabled)
  _state['logfile'] = logfile
  try:
    _state['window'] = max(1.0, float(window_seconds))
  except (TypeError, ValueError):
    _state['window'] = 120.0
  if not _state['enabled']:
    return False
  if _state['installed']:
    return True
  try:
    from window import Window
  except Exception as e:
    _write('[%s] render audit NOT installed: %s' % (_stamp(), e))
    _state['enabled'] = False
    return False
  wrapped = []
  for name in ENTRY_POINTS:
    func = getattr(Window, name, None)
    if func is None or getattr(func, '__wrapped__', None) is not None:
      continue
    setattr(Window, name, _wrap(name, func))
    wrapped.append(name)
  _state['installed'] = True
  _write('[%s] render audit started (window %.0fs, log %s, watching %s)'
         % (_stamp(), _state['window'], logfile or '<console only>',
            ', '.join(wrapped) or '<nothing>'))
  return True


def stop():
  """Stop reporting. Leaves the wrappers in place -- unwrapping a class method
  that something else may have wrapped since is the more dangerous half."""
  _state['enabled'] = False
