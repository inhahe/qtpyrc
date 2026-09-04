"""Nothing expensive may be imported before the window is on screen.

`CLAUDE.md` states the rule -- "nothing that isn't needed to put the window on
screen should run before the event loop turns" -- and qtpyrc has already paid
to learn it twice: the multi-family chat font and `_prewarm_imports` (the
stdlib HTTP/email stack, warmed for link previews) were both moved off the
startup path onto a 0ms timer so they compete with neither the GUI thread nor
the disk while the first window is being built.

Both regressions since were the same shape, and neither was visible to any
existing test:

  * **plugins/nowplaying.py imported `urllib.request` at module level** when it
    gained its beefweb source. It is in `plugins.auto_load`, so every launch
    dragged in http.client, email.* and ssl -- 4.8s measured with
    `python -X importtime` -- to have a hotkey ready that most launches never
    press. That is the very stack `_prewarm_imports` had been rearranged to
    keep *off* the startup path.
  * **`qtpyrc._register_settings_paths` imported `settings.settings_dialog`**
    to enumerate the `settings.*` UI path names, and that module imports all
    seventeen settings page classes: 2.4s to build a list of strings nothing
    had asked for. The tables now live in `settings.page_registry`, which
    imports nothing heavy.

Why this test is written against `sys.modules` and not a stopwatch: a startup
*time* assertion cannot fail on a fast machine and cannot pass on a busy one --
measured here, the same build varied between 11.2s and 23.3s. "Was this module
imported yet?" is the same answer every run.

Usage:
  python tests/test_startup_imports.py     # from the qtpyrc root directory
"""

import atexit
import os
import runpy
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules that must not be loaded before the event loop turns. Each is here
# because it was measured, not because it looks heavy.
FORBIDDEN = {
    'urllib.request': 'the stdlib HTTP stack (4.8s); _prewarm_imports warms it '
                      'from a 0ms timer *after* the window is up',
    'http.client': 'pulled in by urllib.request',
    'email.parser': 'pulled in by http.client',
    'settings.settings_dialog': 'imports all 17 settings page classes (2.4s); '
                                'settings.page_registry has the path names',
    'settings.page_identity': 'a settings page (0.38s), only needed when the '
                              'settings dialog is opened',
    'settings.page_network': 'a settings page (0.23s)',
    'settings.page_autojoin': 'a settings page (0.21s)',
    'settings.page_dcc': 'a settings page (0.13s)',
}

CONFIG = """\
nick: tester
user: tester
realname: qtpyrc startup import test

window_mode: normal
view_mode: tabbed
auto_connect: false

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
ident: {enabled: false}
logging:
  hang_watchdog: {enabled: false}
history_replay: {channels: 0, queries: 0, bg_enabled: false}

# The plugins are the point: they load before the event loop turns, so an
# eager import in one of them lands squarely in the startup path. nowplaying
# is the one that regressed.
plugins:
  auto_load:
  - nowplaying

networks:
  testnet:
    name: TestNet
    nick: tester
    auto_connect: false
    server:
      host: 127.0.0.1
      port: 65000
      tls: false
"""

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-startimp-')
atexit.register(shutil.rmtree, tmpdir, True)
cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.argv = ['qtpyrc.py', '-c', cfg, '--no-startup']

# `window` first: it is what the rest of the suite imports before touching Qt,
# and importing qasync ahead of PySide6 makes qasync bind to whichever Qt it
# finds first -- here that failed outright with "DLL load failed while
# importing QtCore".
import window as _window_mod          # noqa: F401  (import order matters)
from PySide6.QtCore import QTimer, QCoreApplication
import qasync

# The capture point is the instant before the event loop is entered, which is
# exactly the line the rule is about. Hooking a paint or a 0ms timer instead
# would race the prewarm timer, which is *allowed* to import these -- the
# question is only whether they were already in before the loop started.
snapshot = {}
_real_run_forever = qasync.QEventLoop.run_forever


def _capturing_run_forever(self):
  snapshot['modules'] = set(sys.modules)
  QTimer.singleShot(0, lambda: QCoreApplication.instance().quit())
  return _real_run_forever(self)


qasync.QEventLoop.run_forever = _capturing_run_forever

try:
  runpy.run_path(os.path.join(ROOT, 'qtpyrc.py'), run_name='__main__')
except SystemExit:
  pass
finally:
  qasync.QEventLoop.run_forever = _real_run_forever

failures = []
loaded = snapshot.get('modules')

if loaded is None:
  failures.append('the event loop was never entered, so nothing was measured')
else:
  # A sanity check first: if the app never got far enough to load plugins, the
  # absence of nowplaying's imports would prove nothing.
  if 'plugins' not in loaded:
    failures.append('qtpyrc never got as far as loading plugins, so this test '
                    'proves nothing')
  for mod, why in sorted(FORBIDDEN.items()):
    if mod in loaded:
      failures.append('%s was imported before the event loop turned -- %s'
                      % (mod, why))

if failures:
  print('FAILED (%d):' % len(failures))
  for f in failures:
    print('  - %s' % f)
  sys.exit(1)
print('none of the %d expensive modules is imported before the window is shown '
      '(%d modules loaded at that point).' % (len(FORBIDDEN), len(loaded)))
sys.exit(0)
