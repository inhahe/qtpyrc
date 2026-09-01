# qtpyrc — known issues and technical debt

Unsolved bugs and accepted debt. Fixed things are recorded at the bottom only
when the fix left a residue worth knowing about.

---

## Open

### GUI stalls of 2–9s with no Python frame below `loop.run_forever()`

**Where:** unknown — inside Qt/C++.
**Evidence:** `me/hangs.log`. 239 of the first 315 samples bottom out at
`qtpyrc.py … loop.run_forever()` → `qasync … self.__app.exec()` with no qtpyrc
frame beneath, so whatever is blocking is native: layout, painting, font
handling, or a native dialog. One sample (2026-08-16 10:44:54, 3.73s) landed in
`window.py:199 paintEvent`, which suggests at least some are the chat-view
layout cost below.
**Diagnosis is now automatic (2026-08-26).** The watchdog escalates exactly
these — and only these — to `py-spy dump --native`, recording the GUI thread's
real Qt/Win32 frames. Gated on `logging.hang_watchdog.native_stacks`, needs
`py-spy` installed, skipped when the Python stack already names the blocker
(the sample suspends the process, so it lengthens the stall it measures) and
rate-limited to one per 30s. See `hang_watchdog.py` and
`tests/test_hang_watchdog.py`.

**Answered 2026-08-26: there are two causes, and the larger one is not ours.**
Fifteen native samples now exist. They split cleanly by what the GUI thread was
doing at the moment it was sampled.

**Six are `(idle)` — the GUI thread was not running at all.** The 12.51s stall
of 2026-08-26 19:35:57 has it parked in
`NtUserMsgWaitForMultipleObjectsEx` inside `QEventDispatcherWin32::processEvents`:
waiting for a message, exactly as an idle app should be, while a 500ms `QTimer`
on that same thread failed to fire for 4.49s. **A stall with an idle stack is
the scheduler's, not the program's.** The machine was at **95.3% of its commit
limit** (234,749MB of 246,244MB) with 13,557 page faults/sec, so every working
set had been trimmed — python.exe showed 5.9MB resident against 25.6MB private.
An idle thread whose pages are on disk still misses its timers while they fault
back in. Corroboration: `py-spy` needed **3.61s** to take one sample of a
suspended process.

That is also the whole of the "several seconds between hitting enter and
anything happening" report, and of "it takes a long time between launch and
anything appearing" — startup to first chat paint measured **3.886s cold vs
1.41s warm** on identical config and data, the gap being paging, not work.
**Nothing to fix in qtpyrc for these.** The client cannot outrun a machine that
is out of commit.

**Nine are `active`/`active+gil` — those are ours**, and both are already
entries of their own below: `ChatOutput.paintEvent` (a
`QTextDocumentLayout::draw` under `QTextBrowser::paintEvent`), and window
construction on JOIN. Note the second is *also* distorted by the paging above:
a `Channelwindow` measured warm costs 13.6ms, and the sample that named it was
an 8.18s stall.

**Still open:** whether an *active* sample ever accounts for a multi-second
stall on an unloaded machine. Every one so far is either a known-cost operation
(paint, tile) or an operation whose warm cost is three orders of magnitude
below the stall it was blamed for. Re-check `me/hangs.log` after a session run
with commit charge under, say, 70%.

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

### `test_on_events.py` failed 5 of 22 once and has not repeated

**Where:** `tests/test_on_events.py`.
**Evidence (2026-08-26):** one run reported `Total: 22 | Passed: 16 | Failed: 5 |
Skipped: 1`. Five subsequent runs — including one deliberately started
immediately after `test_pm_activity_live.py`, to test the "leftover state from
the previous test" theory — all reported `21 | 0 | 1`. The failing names were not
recorded.
**Suspected:** a timing race in the test harness rather than in `/on` itself; the
test drives a live connection to `tests/irc_test_server.py` and waits on fixed
delays. **Unconfirmed** — do not "fix" it by lengthening a sleep without first
reproducing and identifying which five.

---

## Fixed, with a residue worth knowing

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
