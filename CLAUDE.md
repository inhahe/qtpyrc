# qtpyrc - PySide6 IRC Client

Entrypoint: `qtpyrc.py`

**Do not edit `todo.txt`** — that file is for the user's own use only.

**Always update docs when making changes:**
- `docs/reference.md` — commands, variables, CLI options, scripting API
- `config.example.yaml` — any new or changed config option

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
| `config.example.yaml` | Documents every config option. Update when adding new options |

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

1. **`config.py`**: Add `self.<option> = data.get('<option>', default)` in AppConfig.__init__
2. **`config.example.yaml`**: Document the option with comment
3. **`settings/page_general.py`** (or appropriate page): Add widget in `__init__`, load in `load_from_data`, save in `save_to_data`

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
