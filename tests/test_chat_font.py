"""Test the shared chat font and its lazy fallback installation.

The chat font must name exactly one family. A QFont that names more than one
makes Qt enumerate the whole system font database before it can match anything
-- roughly half a second with the font files already cached, and considerably
worse without -- and that used to be charged to the first chat window's
constructor, i.e. before anything was on screen at all.

Qt falls back per glyph on its own, so the only thing an explicit list buys is
*which* substitute wins for characters the chat font lacks. window.py therefore
registers that preference on demand, the first time such a character actually
turns up -- a moment when Qt has to build the database anyway, so it costs
nothing extra, and a font that covers everything its user reads never pays it.

Checks that:
  1. chat_font() names a single family,
  2. chat_line_height() matches that font's own metrics,
  3. text the font fully covers installs no fallbacks,
  4. a character the font lacks does install them, and re-fonts open windows,
  5. it only happens once, and
  6. invalidate_chat_font() forgets what it learned about the old font.

Needs a real font database, so it does NOT use the offscreen platform (which
reports no families at all). Creates no visible windows.

Usage:
  python tests/test_chat_font.py     # from the qtpyrc root directory
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QRawFont
from PySide6.QtCore import QCoreApplication, QEventLoop

import state


class FakeConfig(object):
  """Just the attributes the chat-font code touches."""

  def __init__(self, family, size=12):
    self.fontfamily = family
    self.fontheight = size


def pick_family():
  """An installed family that has no glyph for at least one character we can
  feed it, so the on-demand path is actually reachable."""
  families = QFontDatabase.families()
  if not families:
    return None, None
  probes = ['\U0001f600', '\u4f60', '\u2764', '\u0e01']
  for fam in families:
    raw = QRawFont.fromFont(QFont(fam, 12))
    if not raw.isValid():
      continue
    # Must render plain ASCII, or case 3 is vacuous.
    if not all(raw.supportsCharacter(ord(c)) for c in 'Hello, world! 123'):
      continue
    for ch in probes:
      if not raw.supportsCharacter(ord(ch)):
        return fam, ch
  return None, None


def drain():
  """Run the queued singleShot(0) that defers the fallback install."""
  QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)


def main():
  app = QApplication([])
  failures = []

  import window

  family, uncovered = pick_family()
  if not family:
    print('SKIP: no usable font found in the system font database')
    return 0
  print('using font %r, probe character U+%04X' % (family, ord(uncovered)))

  state.config = FakeConfig(family)
  window.invalidate_chat_font()
  window._chat_fallbacks_installed.discard(family)

  refreshes = []
  window.refresh_fonts_hook = lambda: refreshes.append(1)

  # 1. Single family. This is the whole point: families() returning more than
  #    one name is what triggers the full font-database enumeration.
  f = window.chat_font()
  fams = [x for x in f.families() if x]
  if len(fams) > 1:
    failures.append('chat_font() names %d families (%r); it must name one, '
                    'or matching it enumerates the entire font database'
                    % (len(fams), fams))
  if f.family() != family:
    failures.append('chat_font() family is %r, expected %r'
                    % (f.family(), family))

  # 2. Line height comes from that same font.
  expected_h = QFontMetrics(QFont(family, 12)).height()
  if window.chat_line_height() != expected_h:
    failures.append('chat_line_height() is %d, expected %d'
                    % (window.chat_line_height(), expected_h))

  # 3. Fully covered text must not install anything.
  window.note_chat_text('<somenick> hello, world! 12345')
  drain()
  if family in window._chat_fallbacks_installed:
    failures.append('plain ASCII text installed fallback fonts')
  if refreshes:
    failures.append('plain ASCII text re-fonted every open window')

  # 4. A character the font has no glyph for must install them.
  window.note_chat_text('<somenick> look: ' + uncovered)
  drain()
  if family not in window._chat_fallbacks_installed:
    failures.append('a character the font lacks did not install fallbacks')
  subs = [s.lower() for s in QFont.substitutes(family)]
  for want in window.CHAT_FALLBACK_FAMILIES:
    if want.lower() not in subs:
      failures.append('fallback %r not registered for %r (got %r)'
                      % (want, family, subs))
  if len(refreshes) != 1:
    failures.append('expected exactly one font refresh, got %d'
                    % len(refreshes))

  # 5. Repeat characters must not reinstall or re-refresh.
  window.note_chat_text(uncovered * 5)
  drain()
  if len(refreshes) != 1:
    failures.append('fallback install repeated (%d refreshes)' % len(refreshes))

  # 6. Changing the font forgets what was learned about the old one -- coverage
  #    is a property of the family, not of the application.
  window._chat_covered_chars.add('\u0001')
  window.invalidate_chat_font()
  if window._chat_covered_chars:
    failures.append('invalidate_chat_font() kept the old coverage cache')
  if window._chat_font_cache is not None:
    failures.append('invalidate_chat_font() kept the old font')

  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f_ in failures:
      print('  - %s' % f_)
    return 1
  print('\nAll chat-font checks passed.')
  return 0


if __name__ == '__main__':
  sys.exit(main())
