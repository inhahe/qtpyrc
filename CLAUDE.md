# qtpyrc - PySide6 IRC Client

## Code Quality

- **Always do the proper fix.** Never put off a correct fix in favor of a quick hack "for now." Quick fixes accumulate as tech debt and create bugs that are harder to diagnose later. If a fix requires a large refactor, do the refactor.

Entrypoint: `qtpyrc.py`

**Do not edit `todo.txt`** — that file is for the user's own use only.

**Always update docs when making changes:**
- `docs/reference.md` — commands, variables, CLI options, scripting API
- `config.defaults.yaml` — any new or changed config option (in `defaults/`)
- `update.bat` — add new files to the copy list

## File Map

| File | Role |
|------|------|
| `qtpyrc.py` | Entry point, QApplication setup, main window, menus, startup flow |
| `state.py` | Global singletons: `app`, `config`, `clients`, `historydb`, `irclogger`, text formats |
| `asyncirc.py` | Asyncio IRC protocol base class. Message parsing, sending (flood-controlled), CAP/SASL/BATCH negotiation, ISUPPORT |
| `irc_client.py` | IRCClient subclass with all qtpyrc-specific handlers. Message routing, history save/replay, typing notifications, SASL auth |
| `window.py` | GUI: Window base, Channelwindow, Querywindow, Serverwindow, NickItem, NicksList, ChatOutput, Inputwidget, search bar, color picker, NetworkTree |
| `models.py` | Data: User, Channel, Query, Client, Network. mIRC color parsing |
| `commands.py` | Slash command dispatcher. `Commands` class with static methods, `docommand()` routes by name |
| `config.py` | YAML config loading (ruamel.yaml round-trip), AppConfig, ConfigNode, _Null sentinel, color/font parsing, ignore/auto-op, timestamp formatting |
| `history.py` | SQLite DB for session replay. Schema: `(id, ts, network, channel, type, nick, text, prefix)` |
| `notify.py` | NotificationManager (beep/desktop alerts), /notify nick watch list, ISON polling |
| `tabbar.py` | Custom multi-row tab bar for tabbed mode (TabbedWorkspace) |
| `exec_system.py` | `/timer` and `/on` hook execution |
| `plugins.py` | Plugin/script loading system |
| `logger.py` | File logging (IRCLogger) |
| `settings/` | Settings dialog pages. Pattern: `load_from_data(dict)` / `save_to_data(dict)` |
| `docs/reference.md` | Command reference and scripting API docs. Update when adding/changing commands |
| `config.defaults.yaml` | Documents every config option. Update when adding new options |

## Architecture

### Incoming message flow

```
network data -> asyncirc._read_loop() -> _lineReceived(line)
  -> parsemsg() returns (tags, prefix, command, args)
  -> stores tags in self._current_tags
  -> handleCommand() dispatches to irc_<COMMAND>()
  -> subclass handler (e.g. chanmsg()) updates models, calls window.addline_*()
  -> _history_save() writes to SQLite (skipped during playback batches)
```

### User input flow

```
Enter key -> Window.lineinput(text)
  -> if starts with command_prefix: docommand(window, cmd, args)
    -> Commands.<cmd>(window, text) via getattr
  -> else: conn.msg(target, text) + echo to window
```

### Key data structures

- **Client** (`models.py`): One per network. Holds `channels` (dict irclower->Channel), `queries`, `users` (dict irclower->User), `conn` (IRCClient or None), `window` (Serverwindow)
- **Channel** (`models.py`): `nicks` (set), `users` (dict irclower->User), `window` (Channelwindow), `topic`, `key`, `active`
- **User** (`models.py`): `nick`, `ident`, `host`, `channels` (set), `prefix` (dict irclower(channel) -> mode symbol like "@", "+")
- **NickItem** (`window.py`): `_nick` (clean nick), `user` (User ref), `_chnlower`, `_typing`. Display text composed from mode prefix + typing prefix + nick

### Config resolution (3-level)

`resolve(network_key, key)` checks network-level then global. `resolve_server()` checks server > network > global. Config uses ruamel.yaml CommentedMap for round-trip comment preservation.

### Adding a new config option

**Every config option must be editable in the settings UI.** No YAML-only options.

1. **`config.py`**: Add `self.<option> = data.get('<option>', default)` in AppConfig.__init__
2. **`config.defaults.yaml`**: Document the option with a comment (see format below)
3. **`settings/page_general.py`** (or appropriate page): Add widget in `__init__` using `_ck(Widget(), 'dotted.yaml.key')`, load in `load_from_data`, save in `save_to_data`

### config.defaults.yaml comment format

Comments in `config.defaults.yaml` are auto-parsed to generate tooltips and right-click Help text in the settings dialog (`settings/config_help.py`). The parser uses a simple convention:

- `# comment` — help text (accumulated for the next key)
- `key: value` — active config key (help buffer assigned to it)
- `#~ key: value` — commented-out config key (treated same as active key)
- `# ===` or `# ---` — section separator (resets help buffer)

**Rules:**
- Comment lines (`# text`) above a key become that key's help text
- Multiple consecutive `# ` lines are joined with newlines
- A single blank line between comment lines is preserved (as paragraph break)
- Inline comments (`key: value  # comment`) are used as fallback if no block comment exists
- Use `#~` (not plain `#`) for commented-out config keys — this is how the parser distinguishes "this is a disabled config option" from "this is a description comment"

**Example:**
```yaml
# Maximum lines kept in each window's backscroll buffer.
# Older lines are discarded. 0 = unlimited.
backscroll_limit: 10000

# Lines of history to replay from the database.
history_replay:
  # lines to replay on channel join
  channels: 10000
  # lines to replay on query open
  queries: 0

# Main window title format. Leave empty for the default.
#~ titlebar_format: ""
```

The `_ck()` helper tags each settings widget with its dotted YAML key (e.g. `'history_replay.channels'`), which is used to look up both the help text and the default value automatically. No manual mapping dicts needed.

### Adding a new slash command

Add a static method to the `Commands` class in `commands.py`:
```python
def mycommand(window, text):
    conn = window.client.conn if window.client else None
    if not conn:
        window.redmessage('[Not connected]')
        return
    # do stuff
```
Dispatched automatically by name. Alias: `othername = mycommand`

### Chat font (`window.py`)

One shared `QFont` for every chat view, cached in `_chat_font_cache` and reached
through `chat_font()` / `chat_line_height()`; `invalidate_chat_font()` drops it
after a font config change.

**The font names exactly one family, deliberately.** A `QFont` that names more
than one family makes Qt populate the *entire* system font database before it can
match anything (~0.5s warm, far worse cold), and that cost used to be paid inside
the first window's constructor, before anything was on screen. Qt already falls
back per glyph on its own; an explicit list only decides *which* stand-in wins.

So the preference (`CHAT_FALLBACK_FAMILIES`) is registered lazily:
`note_chat_text(text)` — called from every method that puts text in a chat view
(`_render_text`, `addlinef`, `addline_msg`, `addline_nick`, `redmessage`,
`add_separator`) — asks `QRawFont.supportsCharacter(ord(ch))` about each *new*
character, memoises the answer in `_chat_covered_chars`, and on the first
uncovered one calls `QFont.insertSubstitutions()` (deferred by a 0ms timer, since
it re-lays-out every open document) and re-fonts open windows via
`refresh_fonts_hook` (set by `qtpyrc._refresh_all_window_fonts`).

Traps: `QRawFont.supportsCharacter(str)` is mis-bound and only answers correctly
for Latin-1 — always pass `ord(ch)`. Prewarming the font database on a worker
thread is a *net loss* (Qt has one global font lock; measured +0.35s of added
startup latency). Covered by `tests/test_chat_font.py`.

### Startup cost

`--timing` prints a milestone breakdown (the last mark, `first chat paint`, is
driven by `window.first_chat_paint_hook`, fired from `ChatOutput.paintEvent`).
Rule of thumb: **nothing that isn't needed to put the window on screen should run
before the event loop turns.** Two things were moved out on that basis — the
multi-family chat font above, and `_prewarm_imports` (the HTTP/email stack warmed
for link previews), which now starts from a 0ms timer instead of competing for
the GIL and the disk with the GUI thread that is building the first window.

### Window display methods

- `addline(text, fmt)` - plain text with mIRC color code rendering
- `addline_msg(nick, message)` - `<nick> message` with clickable nick anchor
- `addline_nick(parts, fmt)` - mixed text and `(nick,)` tuples rendered as clickable anchors
- `redmessage(text)` - red system message
- `add_separator(label)` - horizontal line with centered label

### IRC protocol (asyncirc.py)

- `sendLine(line)` - queued with token-bucket flood control (burst + rate)
- `_send_raw(line)` - bypasses queue (used for TAGMSG typing)
- `parsemsg(line)` - returns 4-tuple `(tags, prefix, command, args)` with IRCv3 tag parsing
- `_prefix_modes` / `_prefix_symbols` - from server ISUPPORT PREFIX= (e.g. "ohv" / "@%+")
- `irclower(s)` - IRC case-insensitive lowering (respects server casemapping)

### IRCv3 features (irc_client.py + asyncirc.py)

- **CAP negotiation**: `CAP LS 302` -> `CAP REQ` -> `CAP ACK`/`NAK` -> `CAP END`
- **SASL**: PLAIN (`\0user\0pass` base64) and EXTERNAL (client cert)
- **BATCH**: Tracks playback batches (`chathistory`, `znc.in/playback`). Suppresses DB saves during playback
- **server-time**: `@time=` tag parsed to local HH:MM via `_get_server_time()`
- **Typing**: `+typing` via TAGMSG. 3s send throttle, 6s receive timeout. Shown as "..." prefix in nick list + typing bar above output

### Mode prefixes

`_pnick(nick, channel)` prepends mode symbol when `show_mode_prefix` enabled. `_nick_prefix(nick, channel)` returns just the symbol. Stored in `User.prefix[irclower(channel)]`, updated by NAMES reply and MODE changes. Saved to history DB `prefix` column for replay.

### History DB

- Channel key: `"#channel"` (lowercase)
- Query key: `"=nick:ident"` (lowercase, ~ stripped from ident)
- `_history_save()` / `_history_replay()` in irc_client.py
- Replay inserts lines then `add_separator(" End of saved history ")`
- Bouncer playback shows separate start/end separators

**Three connections, and what may run on which thread.** `HistoryDB._conn`
belongs to the GUI thread and does the writing; `HistoryReader` owns a
`query_only` connection on its own thread for the drip-feed replay;
`HistoryDB._maint_conn` belongs to the maintenance thread and does everything
whose cost grows with the size of the database.

**Nothing unbounded may run on the GUI-thread connection.** A single indexed
INSERT plus a WAL commit is fine and has to stay synchronous — a replay bounded
by `current_max_id()` must be able to see the row that id names — but pruning
and WAL checkpoints are not, and both used to run inline in `add()` every 500
inserts. Between them they are 30 of the 39 history stall samples in
`me/hangs.log`, the worst 33s. Specifically:

- `PRAGMA wal_autocheckpoint=0` on the GUI connection. Otherwise SQLite runs
  the checkpoint — and its fsync — inside whichever `commit()` pushes the WAL
  past 1000 pages, i.e. on the GUI thread mid-message. Measured at 0.96s per
  500 inserts warm; seconds when cold. `_maintain()` takes the checkpoint
  (`PASSIVE`, so it never waits for a reader or blocks the GUI's writes), and
  `close()` does a final `TRUNCATE` so the WAL doesn't survive the session.
- Pruning is **proportional**: `add()` records `(network, channel)` in
  `_dirty`, and a pass looks only at those. Per channel it is one indexed probe
  for the id of the keep-th newest row; if there is none the channel is under
  the limit and nothing is read, written or committed. The old pass found
  channels with `SELECT DISTINCT network, channel` (full index scan) and pruned
  each with `DELETE ... WHERE id NOT IN (SELECT id ... LIMIT keep)`, which
  materialises up to *keep* ids into an ephemeral index — on the real database
  that was 188 channels, none of them over the limit, so the entire pass
  deleted nothing.
- Both write connections need `busy_timeout`: WAL allows one writer at a time,
  and without it an overlap raises "database is locked" and loses a line.

Net: 500 messages cost 1.12s of GUI thread before, 0.06s after. Covered by
`tests/test_history_maint.py`.

### Chat view layout: never force a full document layout per geometry change

`QTextEdit` lays a document out lazily, so a width change is cheap until
something asks a question that can only be answered by laying the whole
backscroll out. Two such questions are one line of code each, and both used to
be on paths that run once per window per geometry change:

- `doc.size()` / `documentLayout().documentSize()` — ~130ms for 3000 lines.
  `Window._doBottomAlign` asked it to top-pad a short document to the bottom of
  the viewport, and `showEvent`/`resizeEvent` clear `_bottom_align_filled` so
  every geometry change asked again. It answers from the block count now: a
  document with more blocks than the viewport has lines cannot fit at any
  width. This was the 32s "Window → Tile Side by Side" freeze
  (`me/hangs.log`, 2026-08-17 06:56:18) — 4.62s → 1.88s in a 10-window ×
  5000-line benchmark.
- `moveCursor(End)` → `ensureCursorVisible()` needs the cursor's rectangle.
  Gone from `_on_range_changed` / `_scroll_to_bottom`, though for the
  *selection* bug rather than the cost (it dropped the anchor of a selection
  the user was making). Measured A/B on a real tile: it made no difference to
  the time — the layout is owed either way.

What is left is `ChatOutput.paintEvent` (~100-170ms per window per width),
which is inherent to `QTextEdit`: painting a view scrolled to the bottom
requires laying out everything above the viewport. See `known-issues.md`.

Covered by `tests/test_autoscroll.py`.

### View modes

- **Tabbed**: TabbedWorkspace (tabbar.py) - multi-row tab bar + QStackedWidget
- **MDI**: QMdiArea with free-floating subwindows
- **Navigation**: tabs bar, tree sidebar, or both (configurable)

### One workspace, two containers: `_sync_view` is the only renderer

`TabbedWorkspace` holds its windows in a `QStackedWidget` (`_stack`, with
`_blank` at index 0) when maximized, and moves every one of them into a
`QMdiArea` (`_mdi`) on a tile or cascade — `_tiled` says which, `_enter_mdi()` /
`_exit_mdi()` switch. The tab bar drives both, so **every tab state must mean
the same thing in either container**, and the two say the same thing very
differently:

- The stack shows one widget, so `SKIPPED` is expressed by showing something
  *else* — the next window, or `_blank` when there is none.
- The MDI area shows every window at once, so the same thing has to be said by
  hiding that window's subwindow. `_blank`'s stand-in there is the MDI
  background, which `_load_colors` paints the tab-bar colour for that reason.

Only the stack half ever existed, so after a tile, clicking the active tab
cycled onward but minimized nothing, and an all-skipped workspace kept showing
the last window. Hence: **`_sync_view()` is the single renderer** — it puts on
screen whatever the states currently say — **`_set_state()` is the only place a
state is assigned**, and it ends by calling `_sync_view()`. Nothing else touches
`entry['state']` or a container. `_activate()` assigns `self._active` *before*
calling `_set_state`, because rendering reads it.

Three more consequences of there being two containers, all of them once bugs:

- `addSubWindow` must put a new window in whichever container is *live*. Into
  the stack while tiled means invisible until the next Maximize.
- `_unplace_from_mdi` removes the subwindow as well as the widget:
  `QMdiArea.removeSubWindow(widget)` only lifts the widget out of its frame, and
  the orphaned frame is laid out by the next tile as an empty window.
- A **maximized** `QMdiSubWindow` is indistinguishable from the stack, so the
  workspace adopts the tabbed look instead (`_on_sub_state_changed` →
  `_exit_mdi`, which is all `maximizeActive()` ever was). Without that the user
  sits in MDI mode with no cue, which is how the skipping bug reached them.
  A **minimized** one is routed to the same skip a tab click does, rather than
  leaving an icon stub — the tab bar is already that.

Covered by `tests/test_tab_skip.py`.

### Notifications (`notify.py`)

- `NotificationManager`: fires beep (`QApplication.beep()`) and/or desktop (`QSystemTrayIcon.showMessage()`) per event type
- Events: `notice`, `new_query`, `highlight`, `connect`, `disconnect`, `notify_online`, `notify_offline`
- Config: `notifications.<event>.beep` / `.desktop` bools. Stored as tuples in `config.notif_<event>`
- `/notify` command: nick watch list, per-network or global, stored in config like ignores
- ISON polling on a timer (default 60s). `isonReply()` compares with previous state, fires on change
- Notifications suppressed during playback batches
- Settings page: `settings/page_notifications.py`

### Activity tracking

- `Window.ACTIVITY_NONE`, `ACTIVITY_MESSAGE`, `ACTIVITY_HIGHLIGHT`
- Tab/tree title color changes: `new_message` color for messages, `highlight` color for nick mentions
- Cleared when window becomes active
- **`set_activity()` is never gated on a pending history replay.** Only live IRC
  events reach it — replayed history goes straight to the `addline_*` methods —
  so a gate there marks nothing safe and instead drops the mark permanently
  (nothing re-applies it when the replay ends). It cost every freshly opened
  query window its red tab, since a query opened by an incoming PM is created
  with a replay already pending. See `tests/test_activity_replay.py`.

### Live-output hold-back during replay (`window.py`)

A window whose backlog is still loading holds live output in `_replay_queue` and
renders it afterwards:

- `begin_replay_queue()` — opens the queue. **Idempotent, and must stay that
  way**: the handler that creates the window opens it, and the drip-feed loop
  opens it again when it reaches that window; assigning a fresh list in the
  second call discards whatever arrived in between (for a PM-opened query, the
  message itself). Called from `joined()`, `_find_or_create_query()`,
  `_history_replay()` and `qtpyrc._bg_replay_loop()`.
- `_queue_if_replaying()` / `queue_replay_callback()` — hold one call.
- `_flush_replay_queue()` — render everything held, then reopen the window to
  live output. **Every path that ends a replay must call it**, including
  `qtpyrc._bg_replay_drop()`, which is reached when a window turns out to have
  nothing to replay; leaving the queue open makes the window mute for good.
- `headless.StubWindow` implements the same protocol as no-ops (headless prints
  as lines arrive, so it never holds anything back).

**The backlog must stop where the queue starts.** A held-back line is also
written to the history table as it arrives, so a replay bounded only by "newest
row" renders it *and* the flush renders it again. `begin_replay_queue()`
snapshots `state.historydb.current_max_id()` into `Window._replay_cutoff_id`, and
every replay read passes it as `cutoff_id` (`history._id_cap` turns it into
`AND id <= ?`): `db.get_last` / `db.replay_bounds` on the synchronous path,
`reader.replay_bounds` on the drip-feed path (whose resulting `bg['max_id']` then
bounds every `get_chunk`). `HistoryDB` keeps `_max_id` current from `add()`, so
reading the cutoff costs nothing. `_flush_replay_queue()` clears it so the next
hold-back takes its own snapshot. Any new replay read needs the same treatment;
the symptom of missing it is a doubled message, not a crash.

Covered by `tests/test_activity_replay.py`.
