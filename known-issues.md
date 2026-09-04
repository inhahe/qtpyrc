# qtpyrc — known issues and technical debt

Unsolved bugs and accepted debt. Fixed things are recorded at the bottom only
when the fix left a residue worth knowing about.

---

## Open

### GUI stalls with no Python frame below `loop.run_forever()`

**Where:** partly Qt/C++, partly the machine, and — until 2026-09-02 — partly
the watchdog itself and the chat path's own disk writes.
**Evidence:** `me/hangs.log`, now 474 stall reports (2026-08-16 → 2026-09-02).
The great majority bottom out at `qtpyrc.py … loop.run_forever()` → `qasync …
self.__app.exec()` with no qtpyrc frame beneath, so whatever is blocking is
native: layout, painting, font handling, or a native dialog.

**Diagnosis is automatic.** The watchdog escalates exactly these — and only
these — to `py-spy dump --native`. Gated on
`logging.hang_watchdog.native_stacks`, needs `py-spy` installed, skipped when
the Python stack already names the blocker, and since 2026-09-02 gated on the
stall *still happening* and on it having lasted 5s, at most one sample per 5
minutes. See `hang_watchdog.py` and `tests/test_hang_watchdog.py`.

**2026-08-26, over the first 15 native samples: two causes, and the larger one
is not ours.**

**Six were `(idle)` — the GUI thread was not running at all.** The 12.51s stall
of 2026-08-26 19:35:57 has it parked in
`NtUserMsgWaitForMultipleObjectsEx` inside `QEventDispatcherWin32::processEvents`:
waiting for a message, exactly as an idle app should be, while a 500ms `QTimer`
on that same thread failed to fire for 4.49s. **A stall with an idle stack is
the scheduler's, not the program's.** The machine was at **95.3% of its commit
limit** (234,749MB of 246,244MB) with 13,557 page faults/sec, so every working
set had been trimmed — python.exe showed 5.9MB resident against 25.6MB private.
An idle thread whose pages are on disk still misses its timers while they fault
back in. Startup to first chat paint measured **3.886s cold vs 1.41s warm** on
identical config and data, the gap being paging, not work. **Nothing to fix in
qtpyrc for those.** The client cannot outrun a machine that is out of commit.

**Nine were `active`/`active+gil`.** Those are ours, and both are entries of
their own below: `ChatOutput.paintEvent` (a `QTextDocumentLayout::draw` under
`QTextBrowser::paintEvent`), and window construction on JOIN.

**2026-09-02, over all 474 reports: that reading was right about paging and
wrong about the rest, in two ways.**

**1. Two of the three biggest contributors were ours after all**, and both are
now fixed — see "The hang watchdog was the biggest single cause of the freezes
it measured" and "Chat logging and history writes blocked the GUI thread on the
filesystem" below. Briefly: the watchdog's own py-spy sampling accounted for
**429 seconds** of suspended process across 109 samples, and the send/receive
path ran up to three synchronous filesystem syscalls on the GUI thread per chat
line. Both are exactly the sort of thing that *looks* like paging from the
heartbeat, because both leave the GUI thread idle or in Qt with no Python frame.

**2. The `idle` reading was over-generalised.** 68 of the 91 native samples are
`idle`, which the 2026-08-26 analysis read as "the scheduler took the CPU away".
Some of that is real paging. But the sample is taken *after* the report has been
written to disk, so an `idle` stack is equally consistent with "the stall ended
while we were writing about it" — and once the sampling was gated on the stall
still being live, that alternative explains most of them. **An idle native stack
does not distinguish "descheduled" from "already recovered", and the earlier
entry treated it as though it did.**

**Still open**, and now properly separable for the first time:

* Whether an *active* sample ever accounts for a multi-second stall on an
  unloaded machine. Every one so far is either a known-cost operation (paint,
  tile) or one whose warm cost is three orders of magnitude below the stall it
  was blamed for. The `active` samples name
  `QTextDocumentLayout::ensureLayouted`, `QTextEngine::shapeTextWithHarfbuzzNG`,
  `QRasterPaintEngine`, `QBackingStore::flush` and DWrite — i.e. the
  `ChatOutput.paintEvent` entry below, not a filesystem stall.
* Re-check `me/hangs.log` after a session run with commit charge under ~70%
  **and** with the 2026-09-02 fixes in place. Until then there is no clean
  measurement of the residual: every earlier number is contaminated by the
  watchdog's own suspensions, and the log says by how much on each report.

### History and logs are keyed by a network name that changes spelling mid-session

**Where:** `irc_client.IRCClient._log_network` (irc_client.py:750).
**Status:** open. Found 2026-09-03 while investigating the missing "@" in
`#ops`; not caused by it and not fixed with it, because the fix needs a
decision about existing data.

```python
return self.client.network or self.client.network_key or self.client.hostname or 'unknown'
```

`client.network` is the ISUPPORT `NETWORK=` value, which is not known until the
005 burst arrives. Anything written before that falls back to the config key,
so **the same network is written under two different names depending on when a
line happened**, and the history table is keyed by that name.

Measured on `me/history.db`:

| network value | rows |
|---|---|
| `Libera.Chat` | 41,117 |
| `undernet` | 20,325 |
| `UnderNet` | 14,067 |
| `EFNet` | 3,933 |
| `DALnet` | 3,149 |
| `EFnet` | 863 |
| `irc.undernet.org` | 139 |
| `dalnet` | 48 |
| `libera` | 40 |
| `efnet` | 30 |
| `''` | 9 |

Five spellings of Undernet, three of EFnet, two each of DALnet and Libera. The
two Undernet spellings interleave by hours, not by era — `UnderNet` at
2026-09-02 11:14, `undernet` at 14:37, `UnderNet` again on 09-01 06:12 — so
this is a race with registration, not a rename. At least eight channels
(`#anxiety`, `#cinema`, `#forum`, `#life`, `#mentalhealth`, …) have rows under
both.

**Why it matters: a replay reads one key.** `_history_replay` looks up
`(network, channel)`, so a channel whose history is split across two spellings
replays only the half that matches whichever name is current *now*. Lines are
not lost from the table, but they are invisible in the window, which is
indistinguishable from lost. The log files split the same way
(`UnderNet_#Philosophy_2026-09.log` beside `undernet_#philosophy_2026-09.log`),
which is at least visible in a directory listing.

**The fix is not just "normalise the key",** because that leaves 14,067 rows
unreadable under the new key. It needs both:

1. A stable key — the config's `network_key` is the only name known before
   registration and the only one that cannot change under us. `NETWORK=` is
   still the right thing to *display*.
2. A migration that folds existing rows onto it, case-insensitively, plus the
   hostname spellings (`irc.undernet.org`) — and the same for the log tree,
   where the merge has to interleave by timestamp rather than concatenate.

Both halves are the user's call, hence open rather than done. Note the bouncer
project's `bugs.txt` carries a report — "I sent some stuff in a channel maybe
in another irc client and rejoined the channel using a client connecting to
Wicket and my latest messages aren't there" — that has the same shape as this,
and it is worth checking whether some of it is this rather than a bouncer bug.

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
show. It should be built on the first Ctrl+F.
**Measured 2026-08-26** (12 windows, warm, first discarded): a whole
`Channelwindow` costs **13.6ms mean / 15.0ms slowest**, of which `SearchBar`
is **1.3ms — 9.9%**. A hundred windows is 1.36s of construction, 0.13s of it
search bars.
**So this is real but small, and it is not the cause of anything reported.**
It was promoted for measurement because a native sample caught an 8.18s stall
whose entire stack was this constructor (`me/hangs.log`;
`_build_layout` → `Window.__init__` → `Channelwindow.__init__` → `joined()`).
13.6ms of work does not stall for 8.18s by being slow — it stalls by being
paged out, which is what the entry above now records. **Do not read that sample
as evidence for this entry.** Worth doing on its own merits, at 10% of window
construction; not worth doing as a fix for a stall.

### Duplicate query windows for one nick

**Where:** `irc_client.py`, `_find_or_create_query`.
**Evidence:** user report — two query windows for the same IRC nick open at
once. Not reproduced. Suspected to involve a nick change (the window is keyed
by nick, so a rename could strand the old one), but unconfirmed; ask before
guessing at a fix.

### PMs sent from another client attached to the same bouncer never appear

**Where:** `asyncirc.irc_PRIVMSG` → `irc_client.chanmsg`.
**What happens:** the routing test is "is the destination *us*?". A PM the user
sent — from a phone, from a second attached downstream — echoes back as
`PRIVMSG <theirnick> :text`, whose destination is neither us nor a channel, so
it goes to `chanmsg()`, finds `theirnick` in no channel list, and falls off the
end of the function. Dropped without a trace: not drawn, not logged, not saved.
Channels do not have this problem — a self-sent channel line echoes to a name
that *is* in `client.channels`, so it renders normally once `_own_messages`
declines to claim it.
**Proper fix:** in `chanmsg`, handle the "destination is not a channel and the
sender is us" case explicitly: claim from `_own_messages` first (so qtpyrc's own
local echo is still suppressed — see the SelfEchoTracker section of `CLAUDE.md`),
and otherwise route it to the query window for the *destination* rather than the
sender. `privmsg(user, message)` cannot simply be given the destination: its
signature is public plugin/scripting API (`plugin.py`, `plugins.py`,
`exec_system.py`, `docs/reference.md`). The other half of that fix is the
`record()` side: `commands.send_message` deliberately does not record PMs with
`conn._own_messages`, because nothing currently claims them. It must start doing
so *in the same change*, or the moment an echoed PM reaches a window it will be
drawn on top of the local copy.
**Why it is not done yet:** the fix adds a new way for a PM to be rendered, in
exactly the area that produced the duplicate-message report above, and it can
only be validated against a bouncer that actually echoes. Doing it carelessly
turns a missing message into a doubled one. Needs `tests/irc_test_server.py`
coverage as part of the change.

### An outgoing `/notice` is shown in the wrong window and never saved to history

**Where:** `commands.Commands.notice`, against `irc_client.noticed`.
**Found:** 2026-08-31, while consolidating the message-sending paths into
`commands.send_message` — `/notice` is the one send path left outside it.
**Partly fixed 2026-08-31:** the log-file half is done. Both directions are now
written to the conversation partner's log (see the "Logging a chat line"
section of `CLAUDE.md`), covered by `tests/test_notice_log.py`. What is below is
what remains.
**What still happens:** `/notice #chan hi` sends the NOTICE, then draws it in
*whatever window you typed the command in*, and writes no history row. An
incoming notice to the same channel is drawn in the channel's window and *is*
saved (`noticed` → `_history_save(..., 'notice', ...)`). So a channel's replay
has everyone's notices in it except your own, which is the same shape as the
`/msg` bug fixed below — and `/notice` is still the only send path that puts an
outgoing line somewhere other than its target's window, which makes it the odd
one out inside qtpyrc, not merely relative to other clients.
**Proper fix:** give `send_message` a notice mode — same target-shape decision,
same chunking, same history write with type `'notice'`, display through
`addline_nick(["-", (nick,), "- %s" % chunk], state.noticeformat)` so it keeps
the notice format — and route `Commands.notice` through it, dropping the direct
`irclogger` call it now carries.
**Why it is not done yet:** one decision in it is not mine to make silently.
Moving the echo to the target's window is a visible behaviour change, and
sending a notice to a service (`/notice NickServ ...`) is a real use where
keeping the reply in front of you may be what is wanted. Note the log file
already goes to the target rather than the window — so if the echo stays put,
the displayed location and the recorded location will continue to disagree, and
that is a deliberate choice rather than an oversight.
**Watch out when doing it:** a history row for an outgoing notice means an
echoed one could be drawn twice, exactly as with PMs. See the bouncer-echo entry
above; `SelfEchoTracker` has no notice list yet.

---

## Fixed, with a residue worth knowing

### Everything said while the client was closed was drawn but never recorded (fixed 2026-09-04)

**Where:** `irc_client._log_chat` and the `_history_save` call sites.
**Reported as:** "you say bouncer replays aren't logged? they should be."

They should, and this is the more serious half of that day's report. The rule
was "record nothing inside a playback batch", added on 2026-08-31 because
**ZNC** replays a fixed tail of every channel on each reconnect and logging it
again appended a duplicate copy of the tail per reconnect. That is right for
ZNC and wrong for a bouncer that replays *what you missed* -- which is what
Wicket does, from a stored read position. There, the replay is the only time
those lines ever reach the client.

**Measured on the reporter's `history.db`:** 2026-09-04 has no rows at all for
06:00, nor for 08:00 through 12:00 -- five hours, across some thirty channels
on five networks, on a day whose 13:00 hour holds 645 rows. The client was
closed; Wicket buffered; on reattach every line was drawn on screen and written
to neither the log nor the database. That is also why the conversation the
reporter quoted could not be found in either.

Fixed by comparing instead of guessing, since a client cannot know which kind
of bouncer it is attached to. `_should_record(hist_key)` is True for a live
line, and for a replayed one only when its **server-time is newer than the
newest timestamp already stored for that target**. ZNC's re-sent tail is older
than what is stored, so it is still skipped; Wicket's backlog is newer, so it is
kept.

**Residue 1 — a replay with no server-time tag is still dropped.** There is
nothing to compare it against, and of the two possible mistakes a duplicated
log is the lesser. Every bouncer worth the name sends `server-time` (Wicket
does, when the client negotiates it, which qtpyrc does).

**Residue 2 — only the recording moved.** Notifications, link previews and
activity marks remain gated on `_in_playback_batch()` outright: a five-hour
backlog must not fire five hours of desktop alerts. In `privmsg` the history
write and the notification shared one `if` block, so the change had to split it
rather than widen it -- widening it is the obvious mistake here and it is
invisible until somebody reattaches after a long absence.

**Residue 3 — the cutoff is per target and one second granular.** `ts` stores
whole seconds, so a replayed line in the same second as the newest stored one
is treated as already recorded. That is the right way round: it is far more
likely to be that line coming round again than a second line nobody recorded.

**Residue 4 — the hole already made was filled by hand.**
`tools/import_wicket_history.py` reads Wicket's `messages` table and imports
what qtpyrc does not have. Three things about it are worth keeping in mind if
it is ever run again:

* **It respects the prune.** Anything older than the oldest row qtpyrc still
  holds for that channel was deleted by `backscroll_limit`, deliberately, and
  re-importing it would balloon the database and then be deleted again. On the
  reporter's data that distinction is the difference between 10,982 rows to
  import and 80,637: 69,655 of the "missing" lines were pruned by policy, not
  lost by the bug.
* **It rewrites the table in timestamp order.** qtpyrc reads a channel's
  backlog by row id, so rows merely *appended* would show up as the newest
  messages whenever they were actually said. Every row is renumbered.
* **It refuses while the client is running**, tested by asking SQLite for an
  exclusive lock rather than by matching process command lines — this machine
  runs a tray minimiser whose arguments name `qtpyrc.py`, so the obvious check
  would refuse every time.

Verified against a copy before being offered: 84,029 rows in, 95,086 out, zero
rows out of timestamp order, the 2026-09-04 gap filled (06:00 went 0 → 289,
08:00 → 228, 09:00 → 330, 10:00 → 312, 11:00 → 302, 12:00 → 324), the reported
conversation present, `HistoryDB` opens it and `get_last` reads it, and the 109
merged log files came out fully stamped and in order.

### A nick changed spelling with no nick-change line (fixed 2026-09-04)

**Where:** `irc_client.userRenamed` and `userQuit`, `models.Channel`,
`window.NicksList`.
**Reported as:** a channel that showed `<rockwood>` for a while and then
`<Rockwood>` — "it changed from rockwood to Rockwood without ever showing a
nick change. don't know if it's a bug in qtpyrc or wicket".

qtpyrc's. **IRC nick identity ignores case; the spelling is presentation
only** — and `userRenamed` decided whether a channel was affected with

```python
if oldname not in chan.nicks:
    continue
```

a case-*sensitive* test against a set holding whatever spelling the server had
last used. When the two disagreed the whole body was skipped: no "is now known
as" line, no nick-list update, no history row, no error. The messages either
side were fine, because each one is drawn from the nick in its own PRIVMSG
prefix — so the name simply changed, with nothing to say why.

**Correction — this fix is real but it is not what the reporter saw.** The
first diagnosis was that a bouncer replay delivers a *current* NAMES and then
an old NICK, so the list holds one spelling while the event carries the other.
That mismatch is real and the fix below is right, but checking Wicket settled
it the other way: `_replay_backscroll` replays only PRIVMSG, NOTICE and TOPIC,
and the separate activity replay (JOIN/PART/KICK/MODE/NICK/QUIT) is off in the
reporter's config (`replay_activity: false`). **The NICK was never sent to the
client at all**, so no client-side bug was needed to explain the missing line.

The reporter's own reading was the correct one: rockwood really did `/nick`,
and the differing spellings in the messages are simply the prefixes as the
server sent them, rendered correctly. What they did not see was an event that
never arrived.

The case-sensitivity bug below is still a bug, and would bite the moment
`replay_activity` were turned on — but it is fixed on its own merits, not as
the explanation for this report. The evidence that decided it: the whole
conversation is absent from both `me/logs/` and `history.db`, which is the
*playback* signature, and Wicket's replay filter is in `user.py`
`_replay_backscroll`.

`userQuit` had the identical test, so a QUIT spelled differently was swallowed
the same way — **no "has quit" line, and the row left in the nick list
permanently**. Four widget scans compared `item._nick == nick` with the same
blind spot.

Fixed by giving the identity a single home. `Channel` keeps a
`_nick_by_lower` index and exposes `has_nick` / `find_nick` / `rename_nick`;
`NicksList.find_row` does the widget lookup. Everything that asks "is this nick
here?" now goes through one of them, keyed by `irclower` — so it honours the
server's CASEMAPPING rather than `str.lower`, which matters because `[]\` fold
onto `{}|` on an rfc1459 network and `bob[away]` is an ordinary nick.

**Residue 1 — `rename_nick` exists because `removenick` is not its building
block.** A rename does not end the membership, so the mode prefix and the
nick-list row must survive it; `removenick` drops both (deliberately — see the
prefix comment there). Writing the rename as remove-then-add silently deopped
everyone who changed their nick. The first draft of this fix did exactly that
and the test caught it.

**Residue 2 — `Channel.users` was keyed inconsistently**, `nick.lower()` going
in and `irclower()` coming out, which I flagged as a residue on 2026-09-03. It
is `_low()` on both sides now, so the two agree for the nicks RFC1459 folds.

**Residue 3 — the first version of the test passed against the broken code.**
It renamed `rockwood` → `Rockwood` in a channel whose set already held
`rockwood`, which the old exact-match test handles perfectly well. Reproducing
the report needs the *mismatch* — the set holding one spelling, the event
carrying the other — which is what a replay produces and what the test now sets
up. A test that recreates the tidy version of a bug is worse than none: it
reports the bug as absent.

**Residue 4 — Wicket had the same mistake, on the other side.** Its
`ChannelState.members` is keyed by the server's spelling and PART/QUIT/KICK/NICK
all did plain dict lookups against it, so a differently-spelled message left a
ghost member that every attaching client is then handed in NAMES. Not the cause
of this report, but the same shape and quieter, so it is fixed too
(`UserSession._member_key`, `irc bouncer/bugs.txt`).

### Startup regressed to tens of seconds, and showed a blank window (fixed 2026-09-04)

**Where:** `plugins/nowplaying.py`, `qtpyrc._register_settings_paths`,
`qtpyrc.makeapp`.
**Reported as:** "qtpyrc takes about a *minute* between when i launch it and
when it shows anything, and then once it shows up, it's blank for a while
before any widgets load. it didn't used to take this long. and we'd actually
improved the problem once before."

Right on both counts. Two module-level imports had crept back onto the startup
path, one of them into the exact stack the earlier fix existed to keep off it.
Measured with `python -X importtime` against the reporter's own profile:

| | cost | why it was there |
|---|---|---|
| `urllib.request` (+ http.client, email.\*, ssl) | **4.8s** | `plugins/nowplaying.py` imported it at module level when it gained its beefweb source. It is in `plugins.auto_load`, so every launch paid it — for a hotkey most launches never press. |
| `settings.settings_dialog` (+ 16 page modules) | **2.4s** | `_register_settings_paths` imported it to enumerate the `settings.*` UI path *names*, and that module imports every page class. |

The first is the one that undid earlier work: `_prewarm_imports` warms exactly
those modules from a 0ms timer, *after* the window is up, precisely so they do
not compete with the GUI thread and the disk during startup. A plugin importing
them eagerly put them back.

Fixed by deferring both — nowplaying imports urllib inside the two functions
that use it (both already on a worker thread), and the settings path tables
moved to `settings.page_registry`, which imports nothing heavy. `scripts +
plugins load` went from 8.5s to 1.2s in the `--timing` breakdown.

**The blank window was a separate bug in the same report.** `show()` only makes
a window visible; nothing draws into it until a paint event is processed, and
`makeapp()` returns several seconds before the event loop is entered — font
validation, clients, tray and plugins all run in between. `makeapp()` now ends
with `processEvents(ExcludeUserInputEvents)`, so the window it just showed is
painted before that work starts.

**Residue 1 — a stopwatch cannot test this.** On the reporter's machine the
same build measured between 11.2s and 23.3s depending on load, so a startup-time
assertion cannot fail on a fast machine and cannot pass on a busy one.
`tests/test_startup_imports.py` asserts on `sys.modules` at the instant before
the event loop is entered, which is deterministic. It caught both regressions
when they were reintroduced on purpose, naming each module and why it is
forbidden.

**Residue 2 — what is left is mostly not ours.** After the fixes the `--timing`
breakdown is dominated by `python imports + arg parsing`: asyncio (7.5s
cumulative), `ruamel.yaml` (3.9s, needed to read the config at all), PySide6 and
shiboken6 (which pulls `zipfile`, 1.25s, on its own account). Those are the
floor for a Qt + asyncio + YAML application, and the absolute numbers above are
inflated by a machine that was thrashing. If startup is chased again, that is
where the remaining seconds are, and none of them is obviously removable.

**Residue 3 — the general rule, now stated in `CLAUDE.md`.** An `import` at
module level in anything on the startup path *is* startup cost, and it is
invisible in review: nothing in the diff says "this adds four seconds". Both
regressions here were added by changes that were correct in every other respect.

### `/mode` was documented but never implemented (fixed 2026-09-03)

**Where:** `commands.py`.
**Reported as:** "Apparently, the /mode command was never added."

It never was, and `docommand` has no raw pass-through — an unrecognised name
gets `[Unknown command: /%s]`. So `/mode` did not quietly reach the server; it
did nothing. Meanwhile `docs/reference.md` used it in two shipped examples,
`Kick+Ban:/mode # +b $$1!*@* | /kick # $$1` under Popup Menus and the same
pairing under `/on Events`, plus a third in the `plugin.irc` section. Anyone who
copied those got a popup that silently did half of nothing.

**The second half was worse, and nobody had reported it.** `/kick` took a nick
only, so the `# $$1` in those examples — with `#` already expanded to the
channel by `popups.show_popup` — sent `KICK #chan #chan :alice`: the channel as
the nick to kick, the real nick as the reason. It reported no error. Verified
against the old code on the wire: `KICK #ops # :bob`.

Fixed by adding `/mode` (target rule, `#` resolution and the refusal cases are
described in `CLAUDE.md`) and by giving `/kick` an optional leading channel
argument. That addition cannot change the meaning of any line that worked
before, because no valid nick can begin with a channel prefix.

**Residue 1 and 2 — both swept 2026-09-04.** `/ban`, `/kban`, `/op`, `/deop`,
`/halfop`, `/dehalfop`, `/voice`, `/devoice`, `/quiet` and `/unquiet` all took a
nick only and all read `window.channel.name`, and none of them checked whether
there *was* a connection — so every one raised `AttributeError` on
`None.sendLine` while disconnected instead of saying `[Not connected]`, which
`CLAUDE.md`'s own "Adding a new slash command" recipe has always shown. Nine
copies of one twelve-line function is how both happened at once. They now share
`commands._channel_mode_command`, take an optional leading channel, and refuse
cleanly. `/unban` was added at the same time: `/ban` was the only shortcut with
no inverse, so the one thing you could not undo without dropping to `/mode` was
a ban — and undoing one is exactly where the mask expansion has to match.

**Residue 3 (was 1) — `/ban alice@host` banned nobody, and nothing said so.**
The old rule expanded a bare nick to `nick!*@*` but left anything containing
`!` or `@` alone, so `alice@host` went out verbatim — and that is not a mask:
the server reads the whole string as a nick and bans `alice@host!*@*`. It is now
`alice!*@host`, via `config.ban_mask`, which is the same reading of `x@y`
(nick@host, never ident@host) that `split_mask` documents and that `/ignore`
and `/aop` already used. The three surfaces that can ban — the command,
`plugin.irc.ban` and the `/exec` `ban()` — all call it, so they cannot drift.

**Residue 4 — a missing command was invisible to the test suite by
construction**, since the failure is an *absence*: no code to review, no test
to fail, and the documentation the only place the command is mentioned. Now
covered by `tests/test_documented_commands.py`, which extracts every `/word`
from `docs/reference.md` and asserts the client would find something to run.
86 resolve; three do not and are allowlisted *with reasons* (`/name` is a
placeholder in the `add_command` docs, `/np` is a plugin command, `/regex` is
the `/regex/` highlight syntax). The allowlist is checked in both directions —
an entry naming something that does exist, or something the reference no longer
mentions, fails the test, because a list nobody has to justify is how the next
real one gets waved through.

### A mode prefix outlived the membership it belonged to (fixed 2026-09-03)

**Where:** `models.py` (`Channel.removenick`, `Channel.rejoined`),
`irc_client.py` (`IRCClient.names`).
**Found while investigating:** "i'm in #ops and it doesn't show me as having
ops in the user list even though my friend saw that i have ops and doing /op on
her worked." That report turned out to be a Wicket bug, not a qtpyrc one (see
below) — but the investigation found this, which is the same bug pointing the
other way.

`User` objects live in `client.users` for the whole session and are shared by
every channel; `User.prefix` is a dict keyed by channel. **Nothing ever removed
an entry from it.** `modeChanged` clears one only when told to by an explicit
`-o`, `Channel.removenick` dropped the user from the channel without touching
it, and `Channel.rejoined` cleared `chan.users` — which does not own the
prefixes. So a symbol, once granted, survived a part, a quit, a rejoin and a
reconnect.

`names()` could not correct it either, because it read

```python
if prefix:
    user.prefix[chnlower] = prefix
```

— a token *without* a prefix left the old value in place. NAMES is the
authority on who holds what, so the absence of a symbol is as much a statement
as its presence. It now assigns unconditionally, popping the entry when the
token is bare.

The visible failure: an op parts and rejoins without ops and is still shown
with "@". It also reaches the history, because `userJoined` stamps
`_nick_prefix()` into the join row — `me/history.db` has
`2026-08-31 03:09:37 | join | nick=inhahe prefix='@' | #ops`, a join line
recording ops the user did not yet have in that session.

**Residue 1 — this was invisible in the common case.** A channel you stay in
gets its prefixes refreshed by MODE, and a channel you rejoin normally gets a
NAMES that re-asserts the "@" for whoever still has it. The bug only shows for
someone whose status *changed while you were not looking*, which is exactly the
case nobody tests by hand.

**Residue 2 — `Channel.addnick`/`removenick` key `self.users` with
`nick.lower()`, not `conn.irclower()`.** Everything else in this codebase uses
the server's casemapping. It does not bite today because the two differ only
for `[]\^` in nicks, but the new `Channel._chnlower()` deliberately uses
`irclower` because it has to match the key `names()` and `modeChanged` write
under. The `nick.lower()` calls are older and should be brought into line.

### The nick list showed no ops for the user themselves — Wicket, not qtpyrc (diagnosed 2026-09-03)

**Where:** `D:\visual studio projects\irc bouncer` — `user.py`,
`UserSession._update_state`.
**Reported as:** "i'm in #ops and it doesn't show me as having ops in the user
list even though my friend saw that i have ops and doing /op on her worked."

Not a qtpyrc bug. Wicket's `ChannelState.members` (nick -> prefix) is populated
from the upstream RPL_NAMREPLY and then maintained for JOIN, PART, KICK, QUIT,
NICK, TOPIC and 353 — **but there was no MODE branch at all**, so every op and
deop after the channel was first joined was never recorded. That table is what
`_replay_channel_state` turns back into the NAMES it sends each attaching
client, so every client that attached was handed the prefixes as they were when
Wicket joined the channel, possibly days earlier.

The reporter had been opped during an earlier session — their client saw that
MODE live and displayed it correctly at the time — Wicket never wrote it down,
and the next attach replayed a NAMES without the "@". qtpyrc was showing
exactly what it was told, which is why `/op` still worked and their friend still
saw the "@".

The evidence that settled it is in `me/history.db`: between the reporter's join
of `#ops` at `2026-09-03 10:44:00` and their `sets mode +o Sophie\`` at
`13:40:35` there is **no `+o inhahe` row at all**, so no MODE granting them ops
was ever received in that session — while the same table shows `Sophie\`` and
others carrying `prefix='@'` correctly, ruling out a general failure of prefix
handling.

Fixed in Wicket: `UserSession._apply_member_modes` plus `PREFIX` letter and
`CHANMODES` parsing in `upstream.py`. See
`irc bouncer/tests/test_member_modes.py`.

**Residue — the same shape can hide anywhere a client trusts a bouncer's
replayed state.** Topic, away status and channel modes are all replayed from
tables Wicket maintains by hand from selected commands. A missing branch there
is invisible to the client by construction: it cannot tell a stale fact from a
current one. qtpyrc should not paper over it (a client that re-issues NAMES on
attach would hide the bug rather than fix it), but it is worth knowing that
"the client shows the wrong thing" and "the bouncer told it the wrong thing"
look identical from the client end.

### Right-clicking a nick scrolled the channel to the top (fixed 2026-09-02)

**Where:** `window.py`, `ChatOutput._highlight_anchor_at` / `contextMenuEvent`.
**Reported as:** "every time i right-click whois someone, the channel window
scrolls way up. i don't think it did this before."

`contextMenuEvent` highlighted the nick under the pointer by *selecting* it
with `setTextCursor()`, kept the previous cursor, and restored it once the menu
closed. **`setTextCursor()` scrolls the view to the cursor**, so the restore
scrolled to wherever that cursor happened to be — measured in the test at
`7589 → 0`, i.e. from the bottom of the backscroll to the very top.

**The reporter's "I don't think it did this before" was right, and the reason
is a fix.** Chat lines are appended through `Window.cur`, a *separate*
`QTextCursor`, so nothing in normal operation moves the widget's own cursor.
`_on_range_changed` and `_scroll_to_bottom` used to call `moveCursor(End)`,
which dragged it back to the bottom on every incoming line — and that was
removed (correctly) because it dropped the anchor of a selection the user was
making. The cursor move was load-bearing by accident: once it was gone, a
cursor parked anywhere stayed parked for the rest of the session, and every
right-click jumped back to it.

**What parks it** is ordinary use: clicking anywhere in the chat text, or
either find path — `SearchBar._apply_found` and `find_in_all._apply_highlight`
both call `setTextCursor()` on the match. So "every time" is accurate: one
click or one Ctrl+F earlier in the session is enough to make every subsequent
right-click jump there.

**The same code had a second bug, and it is the more insidious one.** The Copy
item appears on that menu *because* the user has a selection
(`copy_action=has_selection`), and `popups.show_popup` implements it as
`output.copy()` — which copies whatever is selected **when the menu closes**.
Selecting the nick replaced their selection, so Copy silently copied the nick
instead of the text they had picked. Nobody reported this, presumably because
the result looks like a mis-click rather than a bug.

Fixed by highlighting through `setExtraSelections()` instead: an extra
selection draws and does nothing else — it does not move the cursor, does not
scroll, and does not touch the user's selection. `_highlight_anchor_at` now
returns a bool and is paired with `_clear_anchor_highlight()`.

**Residue 1 — the extra-selections list has three owners now.**
`SearchBar._apply_found`, `find_in_all._apply_highlight` and this. The first
two *replace* the whole list; the popup highlight appends to it and puts back
what it displaced, so a find result survives a right-click. If a fourth owner
appears, that convention has to be made explicit rather than inferred.

**Residue 2 — the general rule.** `setTextCursor()` is not a way to highlight
text; it is a way to move the caret, which scrolls and clobbers the selection
as side effects. Anything that wants to mark a range *visually* wants
`setExtraSelections()`. The two are easy to confuse because on an empty
document they look identical.

**Residue 3 — how the first version of the test missed it.** It made a
selection (for the Copy half) and then checked the scroll in the same window —
but making a selection *moves the cursor to the selection*, near the bottom,
which is precisely the state in which this bug does not occur. It passed
against the broken code. The two cases need separate windows, and the file says
so. A test whose own setup destroys the precondition is worse than no test: it
reports that the bug is absent.

### The hang watchdog was the biggest single cause of the freezes it measured (fixed 2026-09-02)

**Where:** `hang_watchdog.py`, `_maybe_write_native()`.
**Reported as:** part of "qtpyrc hangs for a few seconds every once in a while".

`logging.hang_watchdog.native_stacks` escalates a stall with no Python frame to
`py-spy dump --native`. py-spy **suspends every thread in the target process**
while it walks them, and the target is qtpyrc itself. Measured over the whole of
`me/hangs.log` (474 stall reports, 2026-08-16 → 2026-09-02):

| | |
|---|---|
| total process suspension caused by sampling | **429s across 109 samples** |
| median recovery, stalls where py-spy ran | **6.56s** |
| median recovery, stalls where it did not | **3.66s** |
| samples that beat the 10s subprocess timeout | 10, worst **50.5s** |
| samples that caught the GUI thread `idle` | **68 of 91** (62 in `NtUserMsgWaitForMultipleObjectsEx`) |

Three things were wrong at once:

1. **The gate was "the Python stack is uninformative", never "the stall is still
   happening".** The native sample is taken *after* the GUI stack and all
   ~15 thread stacks have been written to disk and echoed to the console, and on
   a loaded filesystem that write is itself slow — so by the time py-spy ran,
   the GUI had usually recovered. That is what the 68 `idle` samples are: a
   healthy, idle event loop, frozen for seconds to be photographed.
2. **The 10s timeout could never fire.** `subprocess.run(timeout=…)` is enforced
   by the calling thread, and py-spy suspends that thread along with the rest.
   A timeout enforced from inside a process that the thing being timed has
   frozen is not a timeout.
3. **`--native` and `--nonblocking` are mutually exclusive** in py-spy ("Can't
   get native stack traces with the --nonblocking option"), so there is no cheap
   sample to fall back to. Verified against the running client, which is also
   the only safe way to check: `--nonblocking` does not pause the target.

Fixed by gating on liveness rather than on having-started: the heartbeat is
re-read immediately before spawning py-spy and the sample is skipped if the GUI
has recovered; the stall must have lasted `_NATIVE_MIN_STALL` (5s); and
`_NATIVE_MIN_INTERVAL` went 30s → 300s. Every stall still gets its free Python
stack. Skipped samples say which gate stopped them.

**Residue 1 — the numbers in the entry above were measured through this.** Every
"GUI recovered after Ns" in `me/hangs.log` before 2026-09-02 that has a py-spy
block in it is inflated by that block, and the log says by how much. Do not
compare a pre-2026-09-02 duration against a post- one without subtracting it.

**Residue 2 — the general rule.** An instrument that perturbs what it measures
must be gated on the measurement still being live, not on it having begun. The
duplicate-render audit already obeys this in its own way (it ignores a call that
drew nothing, so it never reports a held-back line against its own flush); the
watchdog did not.

### Chat logging and history writes blocked the GUI thread on the filesystem (fixed 2026-09-02)

**Where:** `logger.py` (`IRCLogger.log`), `history.py` (`HistoryDB.add`,
`add_url`), `render_audit.py` (`_write`).
**Reported as:** "qtpyrc still hangs for a few seconds every once in a while
before reacting after I hit enter on a post. i think it may be when the
filesystem is under load." The reporter's diagnosis was correct.

Sending one message ran three synchronous filesystem operations on the GUI
thread, between putting the line on the wire and drawing it:

```
irclogger.log_channel(...)   write() + flush()      <- a WriteFile syscall
historydb.add(...)           INSERT + commit()      <- a WriteFile syscall
historydb.add_url(...)       INSERT + commit()      <- per URL in the line
```

Receiving one ran the same three. All of them read as free because each is a
handful of microseconds against a healthy disk — but `flush()` and `commit()`
are syscalls, and a syscall against a busy filesystem takes as long as the
filesystem takes. `me/hangs.log` names all of them as GUI-thread stall sites:
`logger.py log`, `history.py add`, `history.py add_url`, `render_audit _write`.

A fourth wait was purely structural: `HistoryDB` had **two** write connections
(the GUI thread's and the maintenance thread's), WAL permits one writer, and the
two were serialised by `busy_timeout` — set to **15000**. Every 500 inserts the
maintenance pass took a write transaction, and until it let go the GUI thread's
next insert blocked. By design, for up to fifteen seconds.

Fixed by moving all of it off the GUI thread:

* New `bgwriter.py`: one background thread, one FIFO queue, shared by the chat
  logs and the render audit. Callers compute the path and stamp the line (which
  must happen at call time) and hand it over.
* `HistoryDB` collapsed onto a **single writer thread** that owns the only write
  connection and also does the pruning and the checkpoints, so the
  `busy_timeout` contention no longer exists rather than being tuned. The GUI
  thread gets a `query_only` connection.
* Row ids are allocated on the calling thread so `current_max_id()` stays
  correct immediately, and every read drains the write queue first
  (`flush_pending()`) so a replay bounded by that id can still see the row.

**Residue 1 — the trade is deliberate, and it moves the wait rather than
abolishing it.** A *read* can now wait for the writer, where before every
*message* waited for the disk. Reads happen on a join, on opening a window and
on scrolling to the top; writes happen on every line of traffic. If a future
report is "opening a window is slow when the disk is busy", this is why, and the
answer is not to drop the barrier — dropping it makes a just-sent message vanish
from its own backlog.

**Residue 2 — the diagnostics that were left switched on.** Both the render
audit and the watchdog's native stacks were enabled in `me/config.yaml`, and
both had already answered the question they were installed for. The cheapest
instrument is the one that is switched off once it has done its job.

The render audit was the worse offender, because it shipped **on by default** —
`config.py`, `settings/page_logging.py` and `defaults/config.defaults.yaml` all
said `true`, and the defaults file is copied wholesale into every new profile,
so every user ran it forever without asking. It is an instrument, not a feature:
it wraps every render method, keys and retains every line drawn, and appends to
a log file for the whole session (1.8 MB in one day here). **Default flipped to
`false` on 2026-09-02** in all three, and the key taken out of both YAML files
— in `defaults/config.defaults.yaml` as `#~ enabled: false`, the documented
commented-out form, so the help text and the settings tooltip survive.

`logging.hang_watchdog.native_stacks` is deliberately still on: unlike the
render audit its question is still open (see the GUI-stalls entry), and it is
now gated so it costs almost nothing until a real multi-second stall happens.

**Residue 2a — a default that only lives in code is invisible.** Nothing tested
that the render audit shipped enabled; `tests/test_settings_defaults.py` only
checks that `config.py` and the settings page *agree*, which they did — on the
wrong answer. Agreement is not correctness, and a shared wrong default is
exactly the shape that test cannot see.

**Residue 3 — `print(flush=True)` is still on the GUI thread** in both
`render_audit._write` and `hang_watchdog._write`. Writing to a console is not
free either. It is left alone because it is how the user sees a report at all
when running with a console attached, and because neither is on the per-message
path any more — but if console output ever becomes one, it goes through
`bgwriter` too.

### Six tests assumed they were the only thing running on the machine (fixed 2026-09-02)

**Where:** `tests/test_msg_history.py`, `test_notice_log.py`,
`test_pm_activity_live.py`, `test_register_once.py`, `test_on_events.py`; plus a
separate `SLEEP 5` inside `test_on_events.py` waiting for the *client*, and
`test_hang_watchdog.py` (below).

A flat sleep is a guess about how long another process takes to start, and the
guess is wrong exactly when the machine is busy — which is when tests are most
often run. Under load on 2026-09-02 (a parallel OS build saturating the disk)
`test_register_once.py` died with `ConnectionRefusedError` on the control port,
and `test_on_events.py` reported every one of its 20 events as "never fired".
**Neither message says "the fixture had not started yet"**, which is what makes
this worth an entry: both look like failures of the thing under test. One run
reported `join: expected 'bob', got 'alice'` — alice being the client's *own*
join, still working its way through a client that had not finished connecting.

**This closes a two-week-old open entry.** `test_on_events.py failed 5 of 22
once and has not repeated` recorded a run of `22 | 16 | 5 | 1` on 2026-08-26
that five later runs could not reproduce; the failing names were not recorded.
Its guess was right -- "a timing race in the test harness rather than in `/on`
itself; the test waits on fixed delays" -- and so was its instruction not to fix
it by lengthening a sleep without first reproducing it. Reproducing it turned
out to need only a loaded machine: under a parallel build it fails every time,
and the fix is to delete the sleeps rather than to grow them.

A sixth of the same kind, found while re-running the suite under that load:
`test_hang_watchdog.py` read the recovery duration from the **first**
`GUI recovered after` line in the log, assuming the only stall in it was the one
the test caused. With `THRESHOLD` at 1.0s a loaded machine supplies its own, so
the test measured a stall it did not cause and reported
`recovery duration 1.19s not near expected 3.0s`. It now splits the log per
stall and takes the block whose stack names `the_blocking_function`, and fails
loudly if that is not exactly one block. **The general shape, three times over
now: a test that assumes it is the only thing happening on the machine.**

Fixed with `irc_test_server.wait_until_listening(*ports)`, shared by all five,
and `test_on_events.wait_until_joined()`, which polls the client's own stdout
for the `myjoined` marker instead of guessing. The client took **15.7s** to
reach that point on the loaded machine, against the 5s the test used to allow.
Both report a timeout explicitly, so a slow start can never again present as a
bug in the events.

### A profile's plugin directory shadowed the shipped one, with no fallback (fixed 2026-09-01)

**Reported as:** two symptoms at once for `nowplaying`, neither of which names
the cause — "nowplaying settings aren't showing up under plugins in the
treeview" and "the checkbox line for nowplaying in the plugins page is indented
a bit compared to the other 5 plugins listed".

**Cause:** `plugins._resolve_scripts_dir()` joined `plugins.dir` to the *config
file's* directory and that was the only place plugins were looked for. A client
run with `me/config.yaml` loaded from `me/plugins/` and could not see the
shipped `plugins/` at all. Both symptoms follow: the plugin never loaded, so
`page_plugin_config.get_plugin_names()` (which lists only plugins that loaded
with `config_fields`, or that already have a saved `plugins.<name>` block) had
nothing to list; and a name in `auto_load` that is not in the directory was
classified *external* and drawn by `_add_external_item` as a checkbox+X widget
rather than a plain `QListWidgetItem`, whose margins are the indentation.

**Fix:** `plugin_search_path()` — profile directory, then the application's own
`plugins/` — with `find_plugin` / `available_plugins` as the single resolvers
used by the loader, the settings pages and `/plugins` alike. See the "Plugins
live on a search path" section of `CLAUDE.md`. Covered by
`tests/test_plugin_search_path.py`.

**Residue 1: the profile copies were made by us, at profile-creation time.**
`qtpyrc._create_profile` copied the whole of `plugins/` into every new profile,
which is what made the single-directory loader appear to work. Each copy is a
fork that receives no fixes: the reporter's `me/plugins/triviabot` was six
months behind (missing the `plugin_prefix` support added 2026-03-27) and nothing
said so. The copying is gone, and the duplicates in `me/plugins/` were deleted
on 2026-09-01 (`chess.py`, `nowplaying.py`, `rotate.py`, `secret.py`,
`triviabot/`; `example.py` is the profile's own and stayed). **Any profile
created before 2026-09-01 still has its copies** — they will keep winning over
the shipped versions until deleted, which is now safe to do and is what the
shadow markers in `/plugins` and the settings page are for.

**Residue 2: a stale override and a deliberate one are the same file.** The
difference is intent, which lives only in the user's head, so nothing can
resolve this automatically — the shadowed path is reported instead, and the
choice is left where it belongs.

### Bouncer playback appended a duplicate copy of every channel's tail to the log files (fixed 2026-08-31)

**Reported as:** nothing. Found while adding notice logging, because the new
call would have inherited it.

**Cause:** `chanmsg`, `action`, `privmsg` and `kickedFrom` each wrote to history
*and* to the log file, one line apart. The history write was inside
`if not self._in_playback_batch():`. The log write was above it, outside. So
every ZNC reconnect, which replays the tail of each channel inside a
`znc.in/playback` batch, appended those lines to the log files a second time —
and a third, and a fourth.

**Fix:** all of them go through `IRCClient._log_chat` / `_log_chat_server`,
which hold the gate. Covered by `tests/test_notice_log.py`.

**Residue 1: the two writes were adjacent and only one was guarded.** Not a case
of the author forgetting the concept — the very next line proves they had it in
mind. A guard that has to be repeated at each of N call sites is a guard that
will be missing at one of them, and the only durable fix is for there to be one
call site.

**Residue 2: a duplicate in a log file has no symptom.** Every other duplicate
bug in this file announced itself on screen, which is what got it reported. This
one only ever appeared in a text file nobody diffs, at a timestamp that looks
plausible, and would have gone on indefinitely. It is the argument for auditing
*writes* rather than waiting for a rendering complaint.

**Residue 3: the test server was better behaved than a real one, again.**
`irc_test_server` NAKed every capability, so `batch` was never negotiated and no
test could put the client inside a playback batch — meaning *every*
"suppressed during playback" rule in `irc_client.py` (history saves,
notifications, link previews, the separator lines) was and still is largely
untested. It now advertises and ACKs `batch` and `server-time` and has
`BATCH`/`ENDBATCH` control commands. This is the second time the same root cause
has hidden a bug; see the split-005 entry below.

### `/aop -l` said "empty" while an invisible entry opped a channel takeover (fixed 2026-08-31)

**Reported as:** "Someone showed me in a log that qtpyrc auto-opped his nick,
which resulted in a channel takeover. Yet `/aop -l` told me the list was empty.
And `/aop -r <nick>` said it removed it, but it says that whether it's there or
not, apparently, since running it multiple times in a row says it removed it
multiple times. So I have no way of knowing what's actually in or not in my
`/aop` list."

**Cause:** six separate faults, all in the gap between what the auto-op *check*
reads and what the list and remove commands touch. `is_auto_op()` reads global +
network + channel additively; `/aop -l` called `get_auto_ops(network_key,
channel)` with the channel taken from the current window, *and* honoured `-w`,
which does not mean "all networks" but "the global scope only" — so `/aop -lw`
answered "empty" from every window, the reporter's `#forum` channel window
included, while four entries were live. `/aop -r` wrote to one scope guessed from
the current window — the network scope from a server window — so it never touched
the channel entry. `_modify_list_entry` returned nothing and the callers printed
"Removed" unconditionally, so the miss was indistinguishable from a success. The
flag parser kept any `-x` whose letters were not all alphabetic as a positional
argument, so `/aop -?` was read as "add the mask `-?`"; `?` is a wildcard, so that
entry auto-opped every two-character nick beginning with `-`. It was still in the
reporter's live config (`auto_ops: [-?]`, at network scope) when this was
diagnosed. And `_match_any` matched a mask flat, so an omitted component behaved
like an impossible one (`hegemon@host` matched nobody) while a nick-only entry
(`HEGEMON`, in that same live config) opped whoever held the nick from any host —
via `fnmatch`, which additionally read `[...]` in a mask as a character class
even though `[`/`]` are ordinary IRC nick characters.

**Fix:** `config.list_all_entries` / `remove_entry_everywhere` / `scope_label`
and the `LIST_*` return codes; one shared command body,
`commands._mask_list_command`, for `/ignore` and `/aop`, with `/highlight` and
`/notify` on the same parser and lister; `config.split_mask` / `expand_mask` and
a purpose-built `_mask_regex` instead of `fnmatch`; and
`commands._breadth_warning` on `/aop` adds. See the "Scoped mask lists" and
"Masks" sections of `CLAUDE.md`; covered by `tests/test_aop_list.py`.

**Residue 1: the visible symptom and the dangerous fault were different bugs.**
The list being wrong is what got reported; the *remove* being wrong is what let
the entry survive long enough to op someone. A user who never ran `-l` would have
had the takeover with no symptom at all — they'd have run `-r`, been told
"Removed", and believed it. Any command that reports success without asking
whether anything changed can hide an arbitrary amount of damage behind a correct
looking transcript.

**Residue 2: the documentation was right and the code silently disagreed.**
`docs/reference.md` had said `-l` "shows all scopes: global, network, channel"
since long before that was true. So checking the docs actively *reinforced* the
wrong conclusion — the list looked authoritative because it was documented to be.
Only a behavioural test catches a divergence in that direction; nothing about
reading either the code or the prose does.

**Residue 3: a mistyped flag became a live config entry.** This is a general
hazard of any parser that falls back to treating an unknown `-x` as a value —
`_parse_list_flags` now raises, and `--` exists for the rare value that really
starts with `-`. Worth checking anywhere else in `commands.py` that splits its
own arguments.

**Residue 4: the first root-cause analysis was wrong, and plausible.** It said
the empty list came from running `-l` in a *server* window, where the
channel-scoped entries were out of view. The reporter corrected it — they were in
the channel window — and running the old code against their real config settled
it: from a server window `-l` printed the network-scoped `-?` entry, never
"empty", so `-w` is the only spelling that produces the message they saw. The
symptom was specific enough to identify the path exactly; guessing a plausible
one instead put a wrong explanation into four files. **Reproduce the reported
output, don't reason to it** — an explanation that accounts for *some* failure is
not the same as one that accounts for *this* failure.

**Not retroactive:** the fix stops new junk being written, but does not clean
what is already in a config. `me/config.yaml` still holds `auto_ops: [-?]` at
network scope and three entries under `#forum` — `HEGEMON` (a nick with no host,
so it ops whoever holds that nick from anywhere) and `*@lakitu.users.undernet.org`
/ `*!*@lakitu.users.undernet.org` (which op *anyone* on that host, not only
hegemon). `/aop -l` now shows all four, labelled with their scope, and
`/aop -r <mask>` removes each from every scope at once.

### `/msg` was displayed but never saved or logged (fixed 2026-08-31)

**Reported as:** "I wrote something to someone in a PM window, then I `/msg`'d
him outside the window a couple of times, and when the window was reloaded
later, only what I PM'd in the window showed up in the history, not what I
`/msg`'d."

**Cause:** `Commands.msg` sent the line and drew it, and did nothing else — no
`irclogger.log`, no `historydb.add`. The window's own send path did all three.
So the two halves of one conversation were stored on different terms, and the
`/msg` half was dropped on the floor the moment it left the screen.

**Fix:** all five send paths now go through `commands.send_message`. See the
"Sending a message" section of `CLAUDE.md`; covered by
`tests/test_msg_history.py`.

**Residue 1: the reported hole was the smallest of five.** Consolidating the
copies is what surfaced the rest, none of which had been reported and one of
which is worse than the bug that started it: `/amsg` never called
`_own_messages.record`, so on a bouncer that echoes, every `/amsg` line came
back and was drawn *and stored* a second time in every channel. `/query <nick>
<msg>` did not chunk, so anything past 512 bytes was truncated by the server.
Link previews had been added to the window path and to nothing else, so a URL
was previewed only if you had typed it into a window.

**Residue 2: the consolidation nearly introduced a worse bug than it fixed.**
`/msg` accepts a channel target. The first version of `send_message` was
PM-shaped throughout, which would have logged `/msg #chan hi` with `log()`
instead of `log_channel()` and saved it under `=#chan` — a history key no window
ever reads. Nothing throws; the message simply is not in the channel's backlog
when it next replays. The general form of the trap: **a target has two shapes,
they differ in four places at once (log function, history key, displayed nick,
and whether self-echo suppression and plugin dispatch happen), and choosing
wrong is silent.** `conn.is_channel()` — ISUPPORT `CHANTYPES`, added in the same
change — is the one place that decides, and the test asserts on the query key
being empty precisely because that failure has no other symptom.

### The same channel error twice after a JOIN — autojoin ran per-005 (fixed 2026-08-26)

**Reported as:** four duplicate-render reports in `me/renders.log`, each a pair
of `#ops Cannot join channel (+b)` / `#life Cannot join channel (+k)` renders
~0.86s apart arriving from two *separate* `asyncirc._lineReceived` calls. The
entry that recorded it concluded "not a rendering bug — the server genuinely
sent it twice", and left open whether qtpyrc had provoked it. It had.

**Cause:** the autojoin loop (and the NickServ IDENTIFY beside it) sat in
`IRCClient.isupport()`, which is a *per-005-line* callback. Servers split
ISUPPORT across two or three messages, so every autojoin channel was JOINed
two or three times. Now in `registered()`, run once per connection from the end
of the MOTD. See the "Registration" section of `CLAUDE.md` and
`tests/test_register_once.py`.

**Residue 1: the visible symptom was the rarest case, not the common one.** The
duplicate JOIN is invisible on a channel you can join — the JOIN echo arrives,
`joined()` strips the queued copies, and a redundant JOIN is a server-side
no-op. It is visible *only* on a channel you cannot join. So the one line in
the log was the tip of a bug that was mis-sending on every channel, every
connect, for as long as the code existed; and reading "the server sent it
twice" as a server-side fact was the natural and wrong inference.

**Residue 2: the test server was better behaved than a real server, and that is
why nothing caught it.** It sent one tidy 005 line. Every test that drove a full
connection passed a client that did its registration work twice. The fix was to
make the fake server split ISUPPORT like a real one — the general lesson being
that a test double which is *more* well-formed than the real thing silently
removes a whole class of test coverage.

**Residue 3: a test written against the joinable case would have passed both
ways.** The first version of `test_register_once.py` used three ordinary
channels and reported "3 JOINs for 3 channels" against the *broken* code. It is
only a regression test because two of its channels are `REJECT`ed. Verified in
both directions before being kept.

### Some sent messages were displayed twice — a trailing space (fixed 2026-08-26)

**Cause:** Libera strips trailing whitespace from the copy of a PRIVMSG it sends
to the channel. qtpyrc draws its own messages locally and suppresses the
bouncer's echo by matching the text it sent; the echo of a message the user ended
with a space came back one character shorter, matched nothing, and was drawn
*and saved* on top of the local copy. Wicket's `_match_pending_echo` had the
identical flaw. Both now compare `rstrip()`ped text and tolerate truncation; see
the SelfEchoTracker section of `CLAUDE.md` and `tests/test_self_echo.py`.

**Residue 1: the elimination reasoning that preceded it was sound and wrong.**
This entry used to record, at length, that the second copy appeared in neither
the file logs nor the history database — from which it followed that the culprit
had to be a render-only path (history replay, the replay-queue flush, the lazy
scroll-up prepend). Every one of those observations was correct as *measured*,
and the conclusion was still false: the duplicate scans compared text for
equality, so they could not see two rows that differed by a trailing space, and
neither could the eye, a log, or a paste. **A negative result from a scan is only
as strong as the scan's idea of "the same".**

**Residue 2: `render_audit.render_key()` deliberately does not normalise trailing
whitespace.** It is the one comparison in the program that must keep seeing what
every other one was blind to.

**Residue 3: two hypotheses from this hunt were never excluded, only made
irrelevant** — wicket's read-position case-folding bug (`inhahe` vs `Inhahe`) in
its own `bugs.txt`, and a second attached downstream (`identifier: 'mobile2'` in
wicket's `read_positions` writes). If duplicates ever return, start there.

### Startup command scripts ran up to four times (fixed 2026-08-26)

`scripts.startup: startup.rc` plus `startup.rc` in `scripts.auto_load` — the
obvious thing to write, and what the user was running — ran the file twice; with
`--startup` and `--run` it could reach four. Fixed with a path-keyed
`run_script_once()` in `qtpyrc._load_scripts_and_plugins`; see the "Startup
scripts" section of `CLAUDE.md` and `tests/test_startup_scripts.py`.

**Residue: it was found by an audit installed to look for something else.** The
duplicate-render audit reported an `[Added hook: …]` line drawn twice from two
different lines of one function — a diagnostic aimed at doubled *chat messages*
naming a bug in the script loader. Nothing else would have: the config was
valid, the declarations a doubled run repeats are name-keyed and overwrite
themselves silently, and the only surface evidence was two identical
confirmation lines that look exactly like a script that prints twice on purpose.

### A window with nothing to replay stayed mute for good (fixed 2026-08-26)

`irc_client._history_replay()` returned early — no history database, or a replay
cap of 0 — without calling `window._flush_replay_queue()`. But the queue is
opened by whoever *creates* the window (`joined()`, `_find_or_create_query()`),
well before anyone asks how much there is to replay, so that early return left it
open forever and `_queue_if_replaying()` swallowed every line for the rest of the
window's life. The drip-feed path had the same case and already handled it
(`qtpyrc._bg_replay_drop`); this was the synchronous one.

**Residue: the trigger was another bug's output.** `limit <= 0` is what
`history_replay.queries: 0` means, and that is precisely the value the settings
dialog used to write into every config that was opened (see the entry below), so
anyone who had opened the dialog had silently disabled query windows. Found while
tracing render paths for the duplicate-message report, not from a report of its
own. Regression test: `tests/test_activity_replay.py` section 3b.

### Settings pages whose defaults disagreed with `config.py` (fixed 2026-08-26)

Five settings wrote a different value than `config.py` applies, so merely
opening the dialog and pressing OK rewrote the user's configuration:
`logging.timestamp` (`HH:MM:SS` puts the *month* in the minutes field),
`auto_connect`, `history_replay.queries` (0 = "disabled"), the identity fields
(`user`/`realname` blanked over the fallback that derives them from `nick`), and
`popups_file`/`toolbar_file`/`variables_file` (prefilled with the conventional
name, which switched all three features on and overrode a deliberately-emptied
setting). Now enforced by `tests/test_settings_defaults.py`.

**Residue: the fix does not repair configs already written.** Anyone who opened
the dialog before this still has the wrong values on disk — in particular
`me/config.yaml` holds `timestamp: YYYY-MM-DD HH:MM:SS`, so its log lines record
the month where the minutes belong until that is edited to `HH:mm:SS`. The
values are the user's data now, indistinguishable from ones chosen on purpose,
which is why nothing migrates them automatically.

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
