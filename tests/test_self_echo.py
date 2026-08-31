"""Suppressing a bouncer's echo of a message we sent (irc_client.SelfEchoTracker).

qtpyrc draws its own messages locally as it sends them, so an echo coming back
from a bouncer has to be recognised and dropped or the line appears twice. The
recognition used to be an exact `(target, text) in list` test, and that was wrong
in two independent ways -- one a user-visible bug for months, the other an
unbounded leak:

  1. **The echo is the server's copy, not ours.** Libera strips trailing
     whitespace, so any message the user ended with a space came back one
     character shorter, matched nothing, and was drawn and saved on top of the
     local copy. The user's report was "random messages I send show up twice";
     it was not random, it was exactly the ones ending in whitespace. It stayed
     hidden because a trailing space is invisible in a log, in a paste, and to
     every duplicate scan that compares text for equality -- so qtpyrc's history
     database looked clean while holding both copies:

       1429558 ... "too bad quodlibet isn't here. "
       1429559 ... "too bad quodlibet isn't here."

     A server may also truncate at the 512-byte limit, which it computes from its
     own idea of our hostmask -- a length no client can predict -- so a shortened
     echo has to be tolerated too.

  2. **Nothing ever expired.** On a network that never echoes (the ordinary
     case) every message the user sent was appended and never removed, so the
     list grew for the whole session.

The tolerance must not go too far, either: an unrelated longer line that happens
to begin with a short one we sent is somebody else's message, and swallowing it
would lose it silently.

Usage:
  python tests/test_self_echo.py     # from the qtpyrc root directory
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


def main():
  from irc_client import SelfEchoTracker

  # ------------------------------------------------------------------ 1
  # The plain case still works.
  t = SelfEchoTracker()
  t.record('#chan', 'hello')
  check(t.claim('#chan', 'hello'), 'an exact echo was not recognised')
  check(not t.claim('#chan', 'hello'),
        'the same entry was claimed twice; a later identical message from '
        'someone else would be swallowed')

  # ------------------------------------------------------------------ 2
  # THE bug: Libera strips trailing whitespace.
  t = SelfEchoTracker()
  t.record('##philosophy', "too bad quodlibet isn't here. ")
  check(t.claim('##philosophy', "too bad quodlibet isn't here."),
        'an echo that lost its trailing space was not recognised as ours -- '
        'this is the duplicate-message bug, and it is invisible in a log')

  # ...and the mirror image, in case a server ever adds rather than removes.
  t = SelfEchoTracker()
  t.record('#chan', 'no trailing space')
  check(t.claim('#chan', 'no trailing space  '),
        'an echo that gained trailing whitespace was not recognised')

  # ------------------------------------------------------------------ 3
  # Truncation at the 512-byte line limit.
  t = SelfEchoTracker()
  sent = 'x' * 400 + ' and a tail the server had no room for'
  t.record('#chan', sent)
  check(t.claim('#chan', sent[:410]),
        'a truncated echo was not recognised as ours')

  # ------------------------------------------------------------------ 4
  # The tolerance must not eat somebody else's message.
  t = SelfEchoTracker()
  t.record('#chan', 'ok')
  check(not t.claim('#chan', 'ok, so here is the whole story at length'),
        'a longer unrelated line beginning with ours was swallowed as an echo')
  check(t.claim('#chan', 'ok'), 'the short entry was consumed by the near miss')

  t = SelfEchoTracker()
  t.record('#chan', 'hello there')
  check(not t.claim('#other', 'hello there'),
        'an echo was matched against a different target')
  check(not t.claim('#chan', 'goodbye there'),
        'an unrelated message was claimed as our echo')

  # A prefix shorter than MIN_TRUNCATED is not a plausible truncation: real
  # truncation happens near 512 bytes, so it keeps nearly all of the text.
  t = SelfEchoTracker()
  t.record('#chan', 'a' * 300)
  check(not t.claim('#chan', 'a' * 5),
        'a five-character prefix was accepted as a truncation of 300')

  # ------------------------------------------------------------------ 5
  # Entries expire, and the store is bounded. Without this every message sent
  # on a non-echoing network accumulated for the life of the session.
  t = SelfEchoTracker()
  t.record('#chan', 'ancient')
  # Age it past the window rather than sleeping for two minutes.
  t._entries = [(tgt, txt, ts - t.WAIT_SECS - 1) for tgt, txt, ts in t._entries]
  check(not t.claim('#chan', 'ancient'),
        'an entry older than WAIT_SECS was still claimable')

  t = SelfEchoTracker()
  for i in range(t.MAX_ENTRIES * 3):
    t.record('#chan', 'message %d' % i)
  check(len(t._entries) <= t.MAX_ENTRIES,
        'the tracker grew past MAX_ENTRIES (%d entries) -- on a network that '
        'never echoes this grows for the whole session'
        % len(t._entries))
  # The newest survive: those are the ones an echo could still be coming for.
  check(t.claim('#chan', 'message %d' % (t.MAX_ENTRIES * 3 - 1)),
        'pruning discarded the most recent entry instead of the oldest')

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('All self-echo dedup checks passed.')
  return 0


if __name__ == '__main__':
  sys.exit(main())
