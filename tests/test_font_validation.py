"""Test that font validation never blocks startup.

_validate_font() runs before the qasync event loop is entered, so it must not
show a modal dialog: doing so stops the rest of startup (IRC connections, tray
icon, asyncio) until the dialog is dismissed, and if the main window is
minimised to the tray that dialog may not even be visible -- qtpyrc just looks
hung.

Checks that:
  1. a present font is left alone,
  2. a missing *default* font falls back silently (returns None),
  3. a missing *user-configured* font falls back AND reports the missing name
     so the caller can offer a picker later,
  4. none of the above ever enters a nested event loop (i.e. no exec()).

Runs headless (offscreen Qt platform).

Usage:
  python tests/test_font_validation.py     # from the qtpyrc root directory
"""

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtGui import QColor

import dialogs

# A deterministic stand-in for the system font database. The offscreen Qt
# platform plugin reports no families at all, and a real one differs per
# machine; the logic under test only cares which names are present.
INSTALLED = ('Courier New', 'Segoe UI', 'Arial')


class FakeFontDatabase(object):
  @staticmethod
  def families():
    return list(INSTALLED)


class FakeConfig(object):
  """Just the attributes _validate_font touches."""

  def __init__(self, family, data):
    self.fontfamily = family
    self.fontheight = 12
    self.fgcolor = QColor(0, 0, 0)
    self.bgcolor = QColor(255, 255, 255)
    self._data = data
    self.saved = False

  def save(self):
    self.saved = True


def main():
  app = QApplication([])
  failures = []

  # Any exec() during validation would block startup -- make it impossible to
  # miss if one sneaks back in.
  def _boom(self, *a, **kw):
    raise AssertionError('_validate_font entered a nested event loop (exec())')
  QDialog.exec = _boom

  dialogs.QFontDatabase = FakeFontDatabase
  present = INSTALLED[0]
  installed = {f.lower() for f in INSTALLED}

  # 1. Present font: untouched, nothing reported.
  cfg = FakeConfig(present, {'font': {'family': present}})
  if dialogs._validate_font(cfg) is not None:
    failures.append('present font was reported as missing')
  if cfg.fontfamily != present:
    failures.append('present font was replaced')

  # 2. Missing font the user never asked for: silent fallback.
  cfg = FakeConfig('NoSuchFontFamily12345', {})
  missing = dialogs._validate_font(cfg)
  if missing is not None:
    failures.append('unconfigured missing font should not be reported, '
                    'got %r' % missing)
  expected = dialogs._pick_fallback_font(installed)
  if expected and cfg.fontfamily != expected:
    failures.append('expected fallback %r, got %r' % (expected, cfg.fontfamily))

  # 3. Missing font the user explicitly configured: fall back *and* report.
  cfg = FakeConfig('NoSuchFontFamily12345',
                   {'font': {'family': 'NoSuchFontFamily12345'}})
  missing = dialogs._validate_font(cfg)
  if missing != 'NoSuchFontFamily12345':
    failures.append('configured missing font not reported, got %r' % missing)
  if cfg.fontfamily == 'NoSuchFontFamily12345' and expected:
    failures.append('configured missing font was not replaced')
  if cfg.saved:
    failures.append('validation wrote to disk')

  # 4. The key being present is not consent. A new config.yaml is copied from
  #    config.defaults.yaml, so every fresh install has font.family spelled
  #    out; if it still matches the shipped default it was never a choice and
  #    must not prompt.
  from config import default_config_value
  shipped = default_config_value('font.family')
  if not shipped:
    failures.append('config.defaults.yaml has no font.family to test against')
  elif shipped.lower() in installed:
    print('SKIP: shipped default font %r is installed here, cannot test the '
          'untouched-default case' % shipped)
  else:
    cfg = FakeConfig(shipped, {'font': {'family': shipped}})
    missing = dialogs._validate_font(cfg)
    if missing is not None:
      failures.append('untouched shipped default %r prompted for a '
                      'replacement' % shipped)
    if expected and cfg.fontfamily != expected:
      failures.append('untouched shipped default did not fall back')

  # 5. No font enumeration at all (the offscreen platform plugin reports zero
  #    families). Every font would look missing -- validation must stand down.
  #    This is what actually wedged startup for 100+ seconds in a headless run.
  class EmptyFontDatabase(object):
    @staticmethod
    def families():
      return []

  dialogs.QFontDatabase = EmptyFontDatabase
  cfg = FakeConfig('Fixedsys Excelsior 3.01',
                   {'font': {'family': 'Fixedsys Excelsior 3.01'}})
  missing = dialogs._validate_font(cfg)
  if missing is not None:
    failures.append('empty font database prompted for a replacement')
  if cfg.fontfamily != 'Fixedsys Excelsior 3.01':
    failures.append('empty font database changed the configured font')

  if failures:
    for f_ in failures:
      print('FAIL: %s' % f_)
    sys.exit(1)
  print('PASS: font validation falls back without blocking '
        '(fallback used: %s)' % expected)


if __name__ == '__main__':
  main()
