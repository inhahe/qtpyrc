"""Test that the hang watchdog detects a GUI-thread stall and names the culprit.

Deliberately blocks the Qt GUI thread and checks that the watchdog:
  1. notices the stall,
  2. records the *function that was blocking* in the captured stack,
  3. reports recovery with a plausible duration.

Runs headless (offscreen Qt platform), so it needs no display.

Usage:
  python tests/test_hang_watchdog.py     # from the qtpyrc root directory
"""

import os
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

import hang_watchdog

STALL_SECONDS = 3.0
THRESHOLD = 1.0


def the_blocking_function():
    """Stand-in for whatever wedges the GUI thread in the real app."""
    time.sleep(STALL_SECONDS)


def main():
    logpath = os.path.join(tempfile.gettempdir(), 'qtpyrc_hangtest.log')
    if os.path.exists(logpath):
        os.remove(logpath)

    app = QApplication([])
    if not hang_watchdog.start(threshold=THRESHOLD, logfile=logpath):
        print("FAIL: watchdog did not start")
        sys.exit(1)

    QTimer.singleShot(1200, the_blocking_function)
    QTimer.singleShot(int((1200 + STALL_SECONDS * 1000) + 2500), app.quit)
    app.exec()
    hang_watchdog.stop()
    time.sleep(0.4)

    with open(logpath, encoding='utf-8') as f:
        log = f.read()

    failures = []
    if 'GUI STALL detected' not in log:
        failures.append("watchdog did not report a stall")
    if 'the_blocking_function' not in log:
        failures.append("captured stack did not name the blocking function")
    if 'GUI recovered' not in log:
        failures.append("watchdog did not report recovery")

    # The reported recovery duration should be in the right ballpark.
    for line in log.splitlines():
        if 'GUI recovered after' in line:
            try:
                secs = float(line.split('after')[1].split('s')[0])
            except (IndexError, ValueError):
                failures.append("could not parse recovery duration: %r" % line)
            else:
                if not (STALL_SECONDS - 1.0) <= secs <= (STALL_SECONDS + 1.5):
                    failures.append(
                        "recovery duration %.2fs not near expected %.1fs"
                        % (secs, STALL_SECONDS))
            break

    print(log)
    if failures:
        for f_ in failures:
            print("FAIL: %s" % f_)
        sys.exit(1)
    print("PASS: hang watchdog detected the stall and named the blocking call")


if __name__ == '__main__':
    main()
