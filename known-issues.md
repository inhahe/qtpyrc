# qtpyrc — known issues and technical debt

Unsolved bugs and accepted debt. Fixed things are recorded at the bottom only
when the fix left a residue worth knowing about.

---

## Open

### GUI stalls of 2–9s with no Python frame below `loop.run_forever()`

**Where:** unknown — inside Qt/C++.
**Evidence:** `me/hangs.log`, 10 events between 2026-08-16 06:50 and
2026-08-17 06:21, 2.20s to 9.17s. The watchdog's GUI-thread stack bottoms out
at `qtpyrc.py … loop.run_forever()` → `qasync … self.__app.exec()` with no
qtpyrc frame beneath it, so whatever is blocking is native: layout, painting,
font handling, or a native dialog. One sample (2026-08-16 10:44:54, 3.73s)
landed in `window.py:199 paintEvent`, which suggests at least some of them are
the chat-view layout cost below.
**To reproduce/diagnose:** the Python-level watchdog cannot see into this. Next
step is a native sampler — `cdb` (installed, `D:\utils\dbg\`) attached while a
stall is in progress, or `py-spy dump --native` (see `pyspy_dump.bat`).
**Not yet triaged.**

### `ChatOutput.paintEvent` lays out the whole backscroll when the view is at the bottom

**Where:** `window.py`, `ChatOutput.paintEvent` — inherent to `QTextEdit`.
**Cost:** ~100–170ms per window per width change. It is what remains of the
"Window → Tile Side by Side" freeze after `_doBottomAlign` was fixed: 1.88s of
GUI work for 10 windows × 5000 lines, 1.69s of it here.
**Why:** `QTextDocumentLayout` has no per-block position index. To paint the
blocks under a viewport that is scrolled to the bottom it must walk down from
the top of the document, laying out every block on the way.
**Proper fix:** replace `QTextEdit` with a line-based view (the
`QPlainTextEdit` layout model, or a custom `QAbstractScrollArea`). That is a
large project, and it costs `anchorAt()` — which is how clickable nicks and
URLs work — plus rich-text frames and the root-frame `topMargin` that
bottom-align uses. All three would need reimplementing.
**Accepted for now:** it is a fifth of what it was, and it is only paid on an
explicit tile/cascade or a window resize, not on incoming messages.

### `SearchBar` is built eagerly for every window

**Where:** `window.py`, `Window.__init__` → `self._search_bar`.
**Debt:** every chat window constructs a search bar it will probably never
show. It should be built on the first Ctrl+F. Costs startup time in proportion
to the number of windows restored.
**Not measured since the font-database fix**, so the size of the win is
unknown; measure with `--timing` before doing the work.

### Duplicate query windows for one nick

**Where:** `irc_client.py`, `_find_or_create_query`.
**Evidence:** user report — two query windows for the same IRC nick open at
once. Not reproduced. Suspected to involve a nick change (the window is keyed
by nick, so a rename could strand the old one), but unconfirmed; ask before
guessing at a fix.

---

## Fixed, with a residue worth knowing

### History pruning and WAL checkpoints on the GUI thread (fixed 2026-08-17)

30 of the 39 history-related stall samples in `me/hangs.log`, up to 33s. Both
ran inline in `HistoryDB.add()`. See the "History DB" section of `CLAUDE.md`
for the rules that replaced them and `tests/test_history_maint.py` for the
regression test.

Residue: `HistoryDB.add()` is still synchronous on the GUI thread, on purpose —
a replay bounded by `current_max_id()` has to see the row that id names, so
making the insert asynchronous would trade a bounded cost for a visibility
race. If that ever needs to change, the queue needs a barrier that the
synchronous read paths (`irc_client.py:294/298`, `qtpyrc.py:716`,
`window.py:1304`) wait on.
