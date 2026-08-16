# qtpyrc - PySide6 IRC Client

## Code Quality

- **Always do the proper fix.** Never put off a correct fix in favor of a quick hack "for now." Quick fixes accumulate as tech debt and create bugs that are harder to diagnose later. If a fix requires a large refactor, do the refactor.

Entrypoint: `qtpyrc.py`

**Do not edit `todo.txt`** — that file is for the user's own use only.

**Always update docs when making changes:**
- `docs/reference.md` — commands, variables, CLI options, scripting API
- `config.defaults.yaml` — any new or changed config option (in `defaults/`)
- `copy_to_github.bat` — add new files to the copy list

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

### View modes

- **Tabbed**: TabbedWorkspace (tabbar.py) - multi-row tab bar + QStackedWidget
- **MDI**: QMdiArea with free-floating subwindows
- **Navigation**: tabs bar, tree sidebar, or both (configurable)

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
