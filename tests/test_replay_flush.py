"""The end of a replay must not be a freeze, and must not reorder the channel.

A window whose backlog is still loading holds live output in `_replay_queue`
and renders it afterwards. The backlog itself is drip-fed across turns of the
event loop precisely so that loading it does not lock the GUI -- and then
`_flush_replay_queue` rendered the entire held-back queue in one synchronous
loop at the end of it, undoing that for the live half.

From `me/hangs.log`, 2026-09-05, two of several:

    *** GUI STALL detected: no heartbeat for 2.16s ***
      qtpyrc.py:658  _bg_replay_loop -> window._flush_replay_queue()
      window.py:1556 _flush_replay_queue
      window.py:1672 addline_msg -> _render_text -> _insert_with_urls
      window.py:1703 cur.insertText(text[pos:], fmt)
    *** GUI recovered after 3.16s ***
    ...
    *** GUI recovered after 8.31s ***

The queue holds whatever arrived while the backlog loaded, so on a busy channel
during a slow replay it is hundreds of lines, each one an insert into a document
that already has thousands of blocks.

**The trap in chunking it is ordering, not speed.** The unchunked version could
afford to close the queue before rendering, because nothing else ran until it
finished. A chunked one returns to the event loop between chunks, so if it
closes the queue first, a line arriving in that gap renders *immediately* --
ahead of everything still waiting. The conversation comes back scrambled, which
is worse than the freeze and far harder to notice. So the queue stays open until
it is empty, and `_in_replay` is what lets a chunk render through the same
`addline_*` methods without re-queuing itself.

This uses a real window in a booted qtpyrc, because what is being asserted is
what reached the document and in what order.

Usage:
  python tests/test_replay_flush.py     # from the qtpyrc root directory
"""

import atexit
import os
import runpy
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG = """\
nick: tester
user: tester
realname: qtpyrc replay flush test

window_mode: normal
view_mode: tabbed

notifications:
  new_query: {beep: false, desktop: false}
  highlight: {beep: false, desktop: false}
link_preview: {enabled: false}
ident: {enabled: false}
logging:
  hang_watchdog: {enabled: false}
history_replay: {channels: 0, queries: 0, bg_enabled: false}

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

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-flush-')
atexit.register(shutil.rmtree, tmpdir, True)
cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.argv = ['qtpyrc.py', '-c', cfg, '--no-startup']

import state
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


def main():
  import models
  from window import Window
  app = QCoreApplication.instance()
  client = next(iter(state.clients))

  chan = models.Channel(client, '#chunked')
  client.channels['#chunked'] = chan
  w = chan.window
  w.begin_replay_queue()

  chunk = Window.FLUSH_CHUNK
  check(chunk > 0, 'FLUSH_CHUNK must be positive, it is %r' % chunk)

  n = chunk * 2 + 7
  for i in range(n):
    w.addline_msg('inhahe', 'held %d' % i)
  check(w._replay_queue is not None and len(w._replay_queue) == n,
        'the queue holds %r of %d lines'
        % (len(w._replay_queue) if w._replay_queue is not None else None, n))
  check('held 0' not in w.output.toPlainText(),
        'the held-back lines were rendered as they arrived, so this is not '
        'testing the hold-back path')

  # --- 1. one call renders one chunk, not the lot ---------------------------
  drained = w._flush_replay_queue()
  check(drained is False,
        'a queue of %d lines reported itself fully drained after one call -- '
        'that is the multi-second freeze this exists to prevent' % n)
  count = w.output.toPlainText().count('held ')
  check(count == chunk,
        'one call rendered %d lines; a chunk is %d' % (count, chunk))
  check(w._replay_queue is not None,
        'the queue was closed with %d lines still unrendered, so anything '
        'arriving now would be drawn ahead of them' % (n - count))

  # --- 2. a line arriving mid-drain waits its turn --------------------------
  before = w.output.toPlainText().count('held ')
  w.addline_msg('inhahe', 'held LATE')
  check('held LATE' not in w.output.toPlainText(),
        'a line that arrived between two chunks was rendered immediately, so '
        'it appears above the %d lines queued ahead of it'
        % (len(w._replay_queue) - 1 if w._replay_queue else 0))
  check(w.output.toPlainText().count('held ') == before,
        'the mid-drain line drew something')

  # --- 3. the scheduled chunks finish the job -------------------------------
  for _ in range(500):
    app.processEvents()
    if w._replay_queue is None:
      break
  check(w._replay_queue is None,
        'the queue never drained; %r lines left'
        % (len(w._replay_queue) if w._replay_queue is not None else None))

  text = w.output.toPlainText()
  check(text.count('held ') == n + 1,
        'the drain rendered %d lines, expected %d'
        % (text.count('held '), n + 1))

  # --- 4. order is what it was queued in ------------------------------------
  missing = [i for i in range(n) if ('held %d\n' % i) not in text + '\n']
  check(not missing,
        'the drain lost %d line(s), e.g. %r' % (len(missing), missing[:5]))
  if not missing:
    order = [text.index('held %d\n' % i) for i in range(n)]
    check(order == sorted(order),
          'the drained lines are out of order')
    check(text.index('held LATE') > order[-1],
          'the line that arrived mid-drain was rendered before lines that were '
          'already queued -- the conversation comes back scrambled')

  # --- 5. the window is live again ------------------------------------------
  check(w._replay_cutoff_id is None,
        'the cutoff outlived the fully drained queue; the next replay would be '
        'truncated to a stale id')
  check(w._flush_replay_queue() is True,
        'flushing an already-drained window did not report success')
  w.addline_msg('inhahe', 'live again')
  check('live again' in w.output.toPlainText(),
        'output was still held back after the queue drained')

  # --- 6. a queue that fits in one chunk still drains synchronously ---------
  # Callers flush and then look at the document; the ordinary case is a handful
  # of lines and must not need the event loop to turn.
  chan2 = models.Channel(client, '#small')
  client.channels['#small'] = chan2
  w2 = chan2.window
  w2.begin_replay_queue()
  for i in range(3):
    w2.addline_msg('inhahe', 'small %d' % i)
  check(w2._flush_replay_queue() is True,
        'a 3-line queue did not drain in one call')
  check(w2._replay_queue is None, 'the small queue stayed open')
  check(w2.output.toPlainText().count('small ') == 3,
        'the small queue rendered %d of 3 lines'
        % w2.output.toPlainText().count('small '))

  if failures:
    print('FAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return finish(1)
  print('the replay flush is chunked, keeps its order, and finishes.')
  return finish(0)


def _run():
  try:
    return main()
  except Exception:
    import traceback
    traceback.print_exc()
    return finish(1)


window_mod.first_chat_paint_hook = lambda: QTimer.singleShot(300, _run)

try:
  runpy.run_path(os.path.join(ROOT, 'qtpyrc.py'), run_name='__main__')
except SystemExit:
  pass

sys.exit(EXIT)
