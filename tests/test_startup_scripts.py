"""Startup command scripts: each file runs once, however many ways name it.

There are four ways to ask for a command script at startup -- ``--startup``,
``scripts.startup``, ``scripts.auto_load`` and ``--run`` -- and nothing stops
two of them naming the same file. The shipped config points ``scripts.startup``
at ``startup.rc``, so listing ``startup.rc`` in ``auto_load`` as well is the
obvious thing to do, and that is exactly the configuration the user was running:

    scripts:
      dir: scripts
      startup: startup.rc
      auto_load:
      - startup.rc

Nothing said so. The script ran twice, in silence, and how much that cost
depended on what was in it: the declarations are keyed by name and merely
overwrite themselves (``/on``, ``/alias``, ``/timer``), but everything with a
side effect happened twice -- ``/exec`` ran its Python again, ``/msg`` and
``/join`` and ``/server`` did it again.

What caught it was the duplicate-render audit (render_audit.py), which was
installed looking for something else entirely and reported the ``/on``
confirmation line drawn twice, from two different lines of the same function:

    *** DUPLICATE RENDER #1 in [not connected] libera - inhahe (Serverwindow) ***
      text: '[Added hook: on privmsg "bouncer_redir" ...]'
      first  render:  commands.py:1333 on <- run_script <- qtpyrc.py:1887
      second render:  commands.py:1333 on <- run_script <- qtpyrc.py:1895

This test recreates that configuration and counts the runs directly, by having
the script append to a file -- the invariant is "the file ran once", not
"some particular command was idempotent".

Both spellings of the same file are used on purpose (``startup`` resolves via
``_resolve_file``'s ``.rc`` fallback, ``startup.rc`` directly), because keying
the check on the name rather than the resolved path would pass this test while
still running the script twice in the field.

Usage:
  python tests/test_startup_scripts.py     # from the qtpyrc root directory
"""

import atexit
import json
import os
import runpy
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-scripts-')
# See tests/test_autoscroll.py for why this is atexit and not a finally: qtpyrc
# holds crash.log open for the life of the process and closes it from its own
# atexit handler, and atexit is LIFO, so registering first means running last.
atexit.register(shutil.rmtree, tmpdir, True)

scriptdir = os.path.join(tmpdir, 'scripts')
os.makedirs(scriptdir)
tally = os.path.join(tmpdir, 'ran.txt')

CONFIG = """\
nick: tester
user: tester
realname: qtpyrc startup script test

window_mode: normal
view_mode: tabbed

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
link_preview: {enabled: false}
ident: {enabled: false}
logging:
  hang_watchdog: {enabled: false}

history_replay:
  channels: 0
  queries: 0

scripts:
  dir: scripts
  startup: startup.rc
  auto_load:
  - startup
  - startup.rc
  - other.rc

networks:
  testnet:
    name: TestNet
    nick: tester
    auto_connect: false
    server:
      host: 127.0.0.1
      port: 6667
      tls: false
"""

# One line each, appending its own name. /exec runs a Python block, which is the
# clearest example of the thing a second run repeats for real. Forward slashes:
# the command parser eats backslashes on its way to exec(), so a Windows path
# written literally arrives as a broken escape sequence.
_tally = tally.replace('\\', '/')
STARTUP_RC = "/exec open('%s', 'a').write('startup\\n')\n" % _tally
OTHER_RC = "/exec open('%s', 'a').write('other\\n')\n" % _tally

with open(os.path.join(scriptdir, 'startup.rc'), 'w', encoding='utf-8') as f:
  f.write(STARTUP_RC)
with open(os.path.join(scriptdir, 'other.rc'), 'w', encoding='utf-8') as f:
  f.write(OTHER_RC)

cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)
# --run names startup.rc a fourth time, by an absolute path this time.
sys.argv = ['qtpyrc.py', '-c', cfg,
            '-r', os.path.join(scriptdir, 'startup.rc')]

import window as window_mod
from PySide6.QtCore import QTimer, QCoreApplication, QMetaObject, Qt

EXIT = 1
failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


def finish(code):
  global EXIT
  EXIT = code
  QMetaObject.invokeMethod(QCoreApplication.instance(), 'quit',
                           Qt.ConnectionType.QueuedConnection)


def runs():
  """What each script recorded, as a list of names in the order they ran."""
  if not os.path.exists(tally):
    return []
  with open(tally, encoding='utf-8') as f:
    return [line.strip() for line in f if line.strip()]


def main():
  try:
    ran = runs()
    check(ran.count('startup') == 1,
          'the startup script ran %d times, not once. It is named four ways '
          '(scripts.startup, twice in scripts.auto_load under two spellings, '
          'and --run by absolute path) and every one of them resolves to the '
          'same file.' % ran.count('startup'))
    check(ran.count('other') == 1,
          'a script named only once ran %d times -- suppressing the repeat '
          'must not suppress anything else' % ran.count('other'))
    check(ran and ran[0] == 'startup',
          'the startup script did not run first (order was %r); the repeat '
          'has to be dropped, not the original' % (ran,))
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)
  if failures:
    print('\nFAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return finish(1)
  print('All startup-script checks passed: a script named four ways ran once.')
  return finish(0)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(300, main)

try:
  runpy.run_path(os.path.join(ROOT, 'qtpyrc.py'), run_name='__main__')
except SystemExit:
  pass

sys.exit(EXIT)
