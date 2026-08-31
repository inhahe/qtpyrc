"""Opening the settings dialog must not change how qtpyrc behaves.

Every settings page fills its widgets in `load_from_data(data)`, falling back to
a literal default when the key is absent, and `save_to_data(data)` writes
whatever is in the widget straight back out.  So a page's literal default is not
just what the user is *shown* -- it is what gets *written*, and merely opening
the dialog and pressing OK makes it real.  A page default that disagrees with
the one `config.py` applies at runtime therefore silently rewrites the user's
configuration into something that behaves differently from the same
configuration before the dialog was opened.

Three of those had accumulated, all of them invisible until you compared two
files:

  * `logging.timestamp` -- the page offered "YYYY-MM-DD HH:MM:SS", but MM is the
    month and mm is the minutes, so every log line written afterwards recorded
    the month where its minutes belonged.
  * `auto_connect` -- config.py defaults it off, the page defaulted it on.
  * `history_replay.queries` -- config.py defaults it to backscroll_limit so a
    query reloads its past conversation; the page defaulted it to 0, which that
    spin box displays as "disabled".

Comparing page literals against defaults/config.defaults.yaml does not catch
these reliably, because that file doubles as an example (its identity fields
hold placeholders like "myuser" that no page should default to).  So this test
compares *behaviour* instead, which is the thing that actually has to hold: build
an AppConfig from nothing, round-trip an empty config through every page, build
an AppConfig from the result, and require the two to be indistinguishable.

Runs headless (offscreen Qt platform), so it needs no display.

Usage:
  python tests/test_settings_defaults.py     # from the qtpyrc root directory
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from PySide6.QtWidgets import QApplication

import config as configmod
import state

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


# Attributes of AppConfig that are not settings in their own right: the path it
# was loaded from, the per-network sub-maps (no global page writes those), and
# `data`, which is the whole YAML document re-exposed for chained access -- it
# necessarily differs, since the point of saving is to write keys into it. The
# individual settings parsed out of it are what must not move.
_NOT_A_SETTING = ('path', 'networks', 'data')


def settings_of(cfg):
  """Every scalar setting an AppConfig exposes, by attribute name."""
  out = {}
  for name in dir(cfg):
    if name.startswith('_') or name in _NOT_A_SETTING:
      continue
    try:
      val = getattr(cfg, name)
    except Exception:
      continue
    if callable(val):
      continue
    out[name] = val
  return out


def same(a, b):
  """Compare two setting values, tolerating types that don't define __eq__."""
  if type(a) is not type(b):
    # int/float and bool/int comparisons are still meaningful
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
      return float(a) == float(b)
    return repr(a) == repr(b)
  try:
    return bool(a == b)
  except Exception:
    return repr(a) == repr(b)


def pages():
  """Every global settings page that round-trips the top-level config.

  Network- and server-scoped pages (NetworkPage, ServerPage, SASLPage,
  AutoJoinPage) read a network's sub-map rather than the top-level one, and
  list editors (ListsPage, ScriptsPage, ...) carry no scalar defaults, so
  neither can be exercised this way.
  """
  from settings.page_general import (GeneralPage, InterfacePage, TitlesPage,
                                     FilesPage)
  from settings.page_logging import LoggingPage
  from settings.page_link_preview import LinkPreviewPage
  from settings.page_dcc import DCCPage
  from settings.page_notifications import NotificationsPage
  from settings.page_ident_server import IdentServerPage
  from settings.page_identity import IdentityPage
  return [GeneralPage, InterfacePage, TitlesPage, FilesPage, LoggingPage,
          LinkPreviewPage, DCCPage, NotificationsPage, IdentServerPage,
          IdentityPage]


def run():
  yaml = YAML()
  path = os.path.join(ROOT, 'defaults', 'config.defaults.yaml')

  # The config a user who has configured nothing runs with.
  base = configmod.AppConfig(path, CommentedMap(), yaml)
  state.config = base
  before = settings_of(base)

  checked = 0
  for cls in pages():
    try:
      page = cls()
    except Exception as e:
      check(False, 'could not build %s: %s: %s'
                   % (cls.__name__, type(e).__name__, e))
      continue
    if not (hasattr(page, 'load_from_data') and hasattr(page, 'save_to_data')):
      continue

    # Open the dialog on an unconfigured config, and press OK.
    out = CommentedMap()
    try:
      page.load_from_data({})
      page.save_to_data(out)
    except Exception as e:
      check(False, '%s could not round-trip an empty config: %s: %s'
                   % (cls.__name__, type(e).__name__, e))
      continue

    after = settings_of(configmod.AppConfig(path, out, yaml))
    checked += 1
    for name, was in sorted(before.items()):
      if name not in after:
        continue
      now = after[name]
      check(same(was, now),
            'opening %s and saving changed %s: %r -> %r -- the page default '
            'disagrees with the one config.py applies, so merely visiting this '
            'page rewrites the user\'s configuration'
            % (cls.__name__, name, was, now))

  check(checked >= 8,
        'only %d settings pages could be round-tripped; this test is not '
        'covering what it claims to' % checked)


def main():
  QApplication.instance() or QApplication([])
  try:
    run()
  except Exception:
    import traceback
    traceback.print_exc()
    return 1
  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('All settings-default checks passed: opening a page and saving leaves '
        'every runtime setting as it was.')
  return 0


sys.exit(main())
