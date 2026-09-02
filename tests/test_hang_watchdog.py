"""Tests for the GUI-thread stall watchdog.

Two things are checked, and they are the two halves of what the watchdog is for.

  1. A stall *reached through Python* is named by its Python stack -- and no
     native sample is taken for it, because paying py-spy (which suspends the
     process, lengthening the very stall being measured) buys nothing when the
     Python frame already says what is blocking.

  2. A stall with *no Python frame below the event loop* -- the deepest frame is
     qasync's run_forever(), i.e. QApplication::exec() -- escalates to a native
     stack. This is the shape of 239 of the first 315 stalls recorded in
     me/hangs.log, and the plain report for it says only "the interface is
     busy".

Plus unit checks on the two pieces that decide (1) from (2): the
"is it parked in the event loop" test, and pulling one thread's frames out of a
py-spy report.

Runs headless (offscreen Qt platform), so it needs no display.

Usage:
  python tests/test_hang_watchdog.py     # from the qtpyrc root directory
"""

import os
import sys
import tempfile
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

import hang_watchdog

STALL_SECONDS = 3.0
THRESHOLD = 1.0
# Long enough for the watchdog to notice the stalled heartbeat, wait out
# _NATIVE_MIN_STALL (the first-detection sample is deliberately skipped now --
# see test_native_gates), resample, run py-spy (2.5s cold) and write the report
# before the loop is stopped out from under it.
_NATIVE_WAIT = 15.0

_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def the_blocking_function():
    """Stand-in for whatever wedges the GUI thread in the real app."""
    time.sleep(STALL_SECONDS)


def _fresh_log(name):
    path = os.path.join(tempfile.gettempdir(), name)
    if os.path.exists(path):
        os.remove(path)
    return path


def _reset_watchdog():
    """Let start() run again -- it refuses while a watcher thread is registered."""
    hang_watchdog.stop()
    hang_watchdog._state['thread'] = None
    hang_watchdog._state['stop'] = None
    time.sleep(0.4)


# ---------------------------------------------------------------------------
# 1. A stall with a Python frame: named by Python, and NOT sampled natively.
# ---------------------------------------------------------------------------

def test_python_stall(app):
    logpath = _fresh_log('qtpyrc_hangtest.log')
    if not hang_watchdog.start(threshold=THRESHOLD, logfile=logpath, native=True):
        check(False, "watchdog did not start")
        return

    QTimer.singleShot(1200, the_blocking_function)
    QTimer.singleShot(int((1200 + STALL_SECONDS * 1000) + 2500), app.quit)
    app.exec()
    _reset_watchdog()

    with open(logpath, encoding='utf-8') as f:
        log = f.read()

    check('GUI STALL detected' in log, "watchdog did not report a stall")
    check('the_blocking_function' in log,
          "captured stack did not name the blocking function")
    check('GUI recovered' in log, "watchdog did not report recovery")
    # The whole point of gating the native sample: a stall Python can explain
    # must not cost a process suspension. (Not `'py-spy' not in log` -- the
    # startup banner names the py-spy it found, so that would never hold. What
    # must be absent is a *sample*.)
    check('Sampling native stack' not in log and 'native stack (py-spy' not in log,
          "a stall with a Python frame was sampled natively anyway -- that "
          "suspends the process for nothing")

    # Measure *our* stall, not whichever one is first in the file. On a loaded
    # machine the client can genuinely stall before the deliberate block --
    # THRESHOLD is 1.0s here -- and taking the first "GUI recovered after" line
    # then reports someone else's number. That is what "recovery duration 1.19s
    # not near expected 3.0s" was: a real 1.19s stall from machine load, parsed
    # in place of the 3.0s one this test caused.
    import re as _re
    blocks = _re.split(r'(?=\*\*\* GUI STALL detected)', log)
    ours = [b for b in blocks if 'the_blocking_function' in b]
    check(len(ours) == 1,
          "expected exactly one stall naming the_blocking_function, found %d "
          "-- the log cannot be attributed" % len(ours))
    for block in ours:
        m = _re.search(r'GUI recovered after ([\d.]+)s', block)
        if not m:
            check(False, "the deliberate stall never reported a recovery")
            continue
        secs = float(m.group(1))
        check((STALL_SECONDS - 1.0) <= secs <= (STALL_SECONDS + 1.5),
              "recovery duration %.2fs not near expected %.1fs"
              % (secs, STALL_SECONDS))


# ---------------------------------------------------------------------------
# 2. The detector that tells the two kinds of stall apart.
# ---------------------------------------------------------------------------

def test_event_loop_detector():
    # This thread is running test code, not sitting in the event loop.
    check(not hang_watchdog._stopped_in_the_event_loop(threading.get_ident()),
          "a thread running Python code was mistaken for one parked in the "
          "event loop")
    check(not hang_watchdog._stopped_in_the_event_loop(-1),
          "a thread id that does not exist was reported as in the event loop")

    # A function named run_forever that is *not* the event loop must not count,
    # or any same-named method of ours would silence the native sample.
    ready, release = threading.Event(), threading.Event()

    def run_forever():
        ready.set()
        release.wait(5.0)

    t = threading.Thread(target=run_forever, daemon=True)
    t.start()
    ready.wait(5.0)
    check(not hang_watchdog._stopped_in_the_event_loop(t.ident),
          "a run_forever() outside qasync/asyncio was taken for the event loop")
    release.set()
    t.join(5.0)


# ---------------------------------------------------------------------------
# 3. Picking one thread out of a py-spy report.
# ---------------------------------------------------------------------------

_SAMPLE_DUMP = """Process 33424: python.exe
Python v3.14.6

Thread 40368 (active)
    QTextDocumentLayout::layoutBlock (Qt6Gui.dll)
    paintEvent (window.py:199)
Thread 27984 (idle)
    ZwWaitForMultipleObjects (ntdll.dll)
    _watch_loop (hang_watchdog.py:128)
"""


def test_extract_thread_block():
    block = hang_watchdog._extract_thread_block(_SAMPLE_DUMP, 40368)
    check('QTextDocumentLayout' in block,
          "the wanted thread's frames were not extracted")
    check('ZwWaitForMultipleObjects' not in block,
          "extraction ran past the end of the wanted thread's block")

    second = hang_watchdog._extract_thread_block(_SAMPLE_DUMP, 27984)
    check('ZwWaitForMultipleObjects' in second,
          "a thread that is not the first in the report was not found")
    check('QTextDocumentLayout' not in second,
          "extraction of a later thread picked up an earlier one")

    # An id that isn't in the report must still yield the report: a dump we
    # cannot index is the only record of where the process was.
    missing = hang_watchdog._extract_thread_block(_SAMPLE_DUMP, 99999)
    check('QTextDocumentLayout' in missing and 'ZwWaitForMultipleObjects' in missing,
          "an unmatched thread id threw the whole dump away")
    check('<py-spy produced no output>' in
          hang_watchdog._extract_thread_block('', 1),
          "an empty dump was not reported as such")


# ---------------------------------------------------------------------------
# 4. A stall with nothing below the event loop escalates to a native stack.
# ---------------------------------------------------------------------------

def test_native_escalation(app):
    if not hang_watchdog._find_py_spy():
        print('SKIP: py-spy is not installed, cannot test native escalation')
        return

    import asyncio
    import qasync

    logpath = _fresh_log('qtpyrc_hangtest_native.log')
    if not hang_watchdog.start(threshold=THRESHOLD, logfile=logpath, native=True):
        check(False, "watchdog did not start for the native test")
        return
    gui_tid = threading.get_ident()

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    seen = {}

    # Silence the heartbeat once the GUI thread is parked inside
    # QApplication::exec() via qasync. That -- not a doctored timestamp -- is
    # what the stalls being chased actually are: Qt never calls back into
    # Python, so the beat timer never gets to run and last_beat ages on its own.
    #
    # Rewinding last_beat instead was a race the test lost about half the time:
    # the beat timer fires every 500ms and the watchdog polls every 250ms, so
    # the rewound value was usually overwritten before it was ever sampled, and
    # no stall was detected at all. Stopping the timer is both deterministic and
    # the more faithful reproduction.
    loop.call_later(0.5, lambda: hang_watchdog._state['timer'].stop())

    def observe():
        # From another thread, because the question is what the GUI thread's
        # innermost frame is *while it is parked* -- asking from a callback
        # running on the GUI thread would find that callback instead.
        time.sleep(2.0)
        seen['detected'] = hang_watchdog._stopped_in_the_event_loop(gui_tid)
        time.sleep(_NATIVE_WAIT)
        loop.call_soon_threadsafe(loop.stop)

    threading.Thread(target=observe, daemon=True).start()
    with loop:
        loop.run_forever()
    asyncio.set_event_loop(None)
    _reset_watchdog()

    with open(logpath, encoding='utf-8') as f:
        log = f.read()

    check(seen.get('detected') is True,
          "a GUI thread parked in qasync's run_forever() was not recognised as "
          "being in the event loop -- the native sample would never be taken")
    check('no Python frame below the event loop' in log,
          "the watchdog did not notice that the Python stack was uninformative")
    check('native stack (py-spy' in log,
          "no native stack was recorded for a stall Python could not explain")
    check('Qt6Core.dll' in log or 'Qt6Gui.dll' in log or 'win32u.dll' in log,
          "the native stack named no Qt/Windows frame:\n%s" % log[-2000:])
    check('run_forever (qasync' in log,
          "the native stack was not the GUI thread's (no qasync frame in it)")


# ---------------------------------------------------------------------------
# 5. The gates on the native sample: it may only be paid for a stall that is
#    still happening, has lasted long enough to be worth it, and has not just
#    been sampled.
#
#    This is the fix for the watchdog having become the largest single cause of
#    the freezes it exists to find. py-spy suspends every thread in this
#    process while it reads them, so a sample taken against a GUI thread that
#    has already recovered freezes a healthy application for seconds and
#    photographs an idle event loop. Over me/hangs.log that was 429 seconds of
#    self-inflicted suspension across 109 samples, 68 of which caught the GUI
#    thread idle.
#
#    _native_stack is stubbed rather than really run: the question here is
#    whether it is *called*, and stubbing it also means this runs in a second
#    and needs no py-spy installed.
# ---------------------------------------------------------------------------

def test_native_gates():
    calls = []
    real_native_stack = hang_watchdog._native_stack
    real_in_loop = hang_watchdog._stopped_in_the_event_loop

    def fake_native_stack(tid):
        calls.append(tid)
        return ('  <stubbed native stack>', 0.01)

    # Section 4 covers the real frame detection. Here it is forced true so the
    # only thing deciding the outcome is the gate under test.
    hang_watchdog._native_stack = fake_native_stack
    hang_watchdog._stopped_in_the_event_loop = lambda tid: True

    logpath = _fresh_log('qtpyrc_hangtest_gates.log')
    try:
        for label, last_beat_age, last_native_age, want_call, want_text in (
            # The one that matters: the heartbeat is current, so whatever the
            # stall was, it is over. Sampling now would freeze a running client.
            ('recovered', 0.0, 0.0, False, 'the GUI has recovered'),
            # Over the threshold but a mere blip. Not worth seconds of freeze.
            ('too short', 2.5, 0.0, False, 'only worth it past'),
            # A real, ongoing stall. This is what the sample is for.
            ('ongoing', 8.0, 0.0, True, 'still stalled'),
            # ...but not twice in a row.
            ('too soon', 8.0, 5.0, False, 'minimum interval'),
        ):
            hang_watchdog._state.update({
                'native': True,
                'threshold': 2.0,
                'logfile': logpath,
                'gui_thread_id': threading.get_ident(),
                'last_beat': time.monotonic() - last_beat_age,
                'last_native': (time.monotonic() - last_native_age)
                               if last_native_age else 0.0,
            })
            before = len(calls)
            hang_watchdog._maybe_write_native(hang_watchdog._state['gui_thread_id'])
            called = len(calls) > before
            check(called == want_call,
                  "native sample for a %r stall: py-spy %s called, expected %s"
                  % (label, 'was' if called else 'was not',
                     'called' if want_call else 'not called'))
            with open(logpath, encoding='utf-8') as f:
                log = f.read()
            check(want_text in log,
                  "the %r case did not say why in the log (looking for %r)"
                  % (label, want_text))
            # Each case must leave its own reason in the log, so truncate
            # between them rather than searching a growing file.
            open(logpath, 'w').close()
    finally:
        hang_watchdog._native_stack = real_native_stack
        hang_watchdog._stopped_in_the_event_loop = real_in_loop
        hang_watchdog._state['native'] = False
        hang_watchdog._state['logfile'] = None
        hang_watchdog._state['last_native'] = 0.0


def main():
    app = QApplication([])
    test_python_stall(app)
    test_event_loop_detector()
    test_extract_thread_block()
    test_native_escalation(app)
    test_native_gates()

    if _failures:
        for f_ in _failures:
            print("FAIL: %s" % f_)
        sys.exit(1)
    print("PASS: hang watchdog names Python stalls, and escalates the ones "
          "Python cannot explain to a native stack")


if __name__ == '__main__':
    main()
