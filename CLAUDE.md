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
| `logger.py` | File logging (IRCLogger). Computes the path and stamps the line; the writing itself is `bgwriter` |
| `bgwriter.py` | One background thread that appends to log files, so no filesystem syscall lands on the GUI thread |
| `hang_watchdog.py` | GUI-thread stall detector; Python stacks, and (gated — it freezes the process) py-spy native stacks |
| `render_audit.py` | Duplicate-render detector; wraps Window's `addline_*` and reports a line drawn twice, with both stacks |
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

**The page's fallback in `load_from_data` must be the same value `config.py`
uses when the key is absent.** A page default is not merely what the user is
*shown*: `save_to_data` writes the widget back verbatim, so opening the dialog
and pressing OK turns the page's answer into the user's configuration. A page
that disagrees with `config.py` silently rewrites the config into something that
behaves differently from the same config before the dialog was opened — and it
does it to people who never touched the setting. Four had accumulated:
`logging.timestamp` (offered `HH:MM:SS`, but `MM` is the month and `mm` the
minutes, so log lines recorded the month in the minutes field), `auto_connect`
(off in `config.py`, on in the page), `history_replay.queries` (`backscroll_limit`
in `config.py`, `0` — which that spin box renders as "disabled" — in the page),
and the identity fields (`config.py` derives `user`/`realname` from `nick`; the
page showed blanks and wrote them over the fallback).

Where a value is only a *suggestion* — the conventional filename for an optional
feature, say — put it in `setPlaceholderText`, never in the value. An empty
`popups_file`/`toolbar_file`/`variables_file` means the feature is off, so
prefilling the box turned all three on and overrode a deliberately-emptied
setting.

`tests/test_settings_defaults.py` enforces this by behaviour rather than by
comparing literals (the defaults YAML doubles as an example, so its identity
fields hold placeholders no page should adopt): it round-trips an empty config
through every global page and requires the resulting `AppConfig` to be
indistinguishable from one built from nothing.

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

### Startup scripts: a file runs once, however many ways name it

There are four ways to ask for a command script at startup — `--startup`,
`scripts.startup`, `scripts.auto_load` and `--run` — and nothing stops two of
them naming the same file. The shipped config points `scripts.startup` at
`startup.rc`, so listing `startup.rc` in `auto_load` as well is the obvious thing
to do, and it silently ran the script twice.

`qtpyrc._load_scripts_and_plugins` therefore funnels all four through
`run_script_once()`, which is **keyed on the resolved path, not the name** — the
same file is reached under several spellings (`startup`, `startup.rc`, an
absolute path), and keying on the name would pass a test while still running it
twice in the field. `_resolve_file` is the same resolution `run_script` itself
does; an unresolvable name is still passed through so `run_script` keeps
ownership of the `[Script not found]` message.

Why it went unnoticed for so long: **the damage depends entirely on what is in
the file.** The declarations are keyed by name and merely overwrite themselves
(`state._on_hooks[event][name]`, `state._timers[name]`, aliases), so a second run
of a file full of `/on` and `/alias` leaves the same state behind and only prints
its confirmation lines twice. Everything with a side effect happens twice for
real: `/exec` runs its Python again, `/msg`, `/join` and `/server` do it again,
`/run` pulls in another script again. What finally caught it was the
duplicate-render audit, reporting one `[Added hook: …]` line drawn twice from two
different lines of the same function.

Covered by `tests/test_startup_scripts.py`, which names one file all four ways
and has it append to a tally file via `/exec` — the invariant tested is "the file
ran once", not "some particular command happened to be idempotent".

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

### Registration: `registered()`, not `isupport()`

**`isupport()` fires once per 005 line, and a server sends two or three.**
ISUPPORT does not fit in one 512-byte message, so every real network splits it.
Anything in `isupport()` therefore happens two or three times per connection.

`IRCClient.registered()` is the once-per-connection hook, run by
`asyncirc._fire_registered()` from `irc_RPL_ENDOFMOTD` / `irc_ERR_NOMOTD` —
every server ends registration with one of the two, and both come after the 005
burst, so `CASEMAPPING`, `PREFIX` and `NETWORK` are all parsed by the time it
runs. `_registered_fired` guards it; `_ensure_connected()` (the 2s timer armed
from `RPL_WELCOME`) calls it too, as a backstop for a server that sends neither
terminator.

**The autojoin loop and the NickServ IDENTIFY both lived in `isupport()`**, so
every autojoin channel was JOINed two or three times and the account password
was put on the wire two or three times. It hid for a long time because on a
channel you *can* join it is invisible: the JOIN echo comes back, `joined()`
strips the still-queued copies out of the flood queue, and a JOIN to a channel
you are already in is a no-op at the server anyway. It only shows on a channel
you *cannot* join — +b, or +k with the wrong key — where no `joined()` ever
arrives to clean up and each JOIN earns its own error reply. That is the whole
visible symptom: a doubled `#ops Cannot join channel (+b)` in the server window,
which is what the render audit caught and what led here.

Two rules follow, and they are what this section is for:

- **Idempotent state assignment may stay in `isupport()`; wire traffic may
  not.** Marking `client.connected` and refreshing the titlebar are still there
  deliberately — they put nothing on the wire and want to happen as soon as the
  network has a name, not after the MOTD.
- **The test server must be as badly behaved as a real one.** `irc_test_server`
  sent a single tidy 005 line, which is exactly why none of its tests ever saw
  this. It now splits ISUPPORT across two. It also grew `RECEIVED [command]`
  (what the client actually sent — the only way to see a message sent twice,
  since the second copy leaves no trace client-side) and `REJECT <channel>`
  (refuse a JOIN, so the no-`joined()` path is reachable at all).

Covered by `tests/test_register_once.py`, which asserts on the wire and uses
two rejected channels for that reason — an all-joinable version of it passes
against the broken code.

### Sending a message: `commands.send_message` is the only way

**Every path that sends a PRIVMSG on the user's behalf goes through
`commands.send_message(window, conn, target, text, display_window=None)`.** It
is one operation — put it on the wire, show it, log it, save it, preview its
links, tell the plugins — and it had been written five times, in `say`'s channel
branch, `say`'s query branch, `/msg`, `/query <nick> <msg>` and `/amsg`. Nothing
made them disagree on purpose; they disagreed because five copies of anything
drift. What each one had lost:

| path | lost |
|---|---|
| `/msg <nick>` | log line, history row |
| `/query <nick> <msg>` | log line, history row, **chunking** — anything past 512 bytes was truncated by the server |
| `/amsg` | `_own_messages.record` (so a bouncer echo drew and stored every line twice, in every channel), link preview, plugin dispatch |
| `/msg`, `/query`, `/amsg` | link preview — a URL was previewed only if you typed it in a window |

The reported symptom was the first row: a conversation held partly in a query
window and partly through `/msg` came back, when the window was next opened,
with only the in-window half in it. The `/msg` half was displayed and then
dropped on the floor.

Two rules keep it consolidated:

- **Ask `conn.is_channel(target)`; never test for a leading `#`.** The channel
  prefixes are per-network — ISUPPORT `CHANTYPES`, parsed into
  `asyncirc._chantypes`, with the module-level `CHANNEL_PREFIXES` only as the
  fallback for a server that has not said. `/msg` accepts a channel target, so
  `send_message` has both shapes in it, and they differ in four places at once:
  the log function (`log_channel` vs `log`), the history key (`#chan` vs
  `_query_history_key(nick)`), the displayed nick (with mode prefix vs bare) and
  whether `_own_messages.record` and the plugin `chanmsg` dispatch happen. Get
  the shape wrong and nothing throws: the message is logged to the wrong file
  and saved under `=#channel`, a key no window ever reads, and you find out when
  the channel's backlog replays without it.
- **The PM half deliberately does not `record()` for self-echo suppression.**
  That is safe only while an echoed PM never reaches a window — see the
  "PMs sent from another client attached to the same bouncer" entry in
  `known-issues.md`. Whoever fixes that routing adds the `record()` here in the
  same change, or turns a missing message into a doubled one.

`display_window` is where the echo goes. Passed explicitly by the callers that
already know (the window the user typed in; the query window `/query` just
created); left `None` by `/msg`, which then looks up the channel's or nick's
window and falls back to `[-> target] text` in the issuing window when there is
none.

Covered by `tests/test_msg_history.py`, which sends by all six routes and checks
both the history table and the log tree — including that nothing about the
channel landed under the query key.

### Logging a chat line: `IRCClient._log_chat`, and the file is the partner

**Every incoming chat line is logged through `_log_chat(target, line)` /
`_log_chat_server(line)`, and the reason is the playback gate.** A bouncer
replays the tail of each channel on every reconnect. History writes have been
gated on `_in_playback_batch()` since they were written; the `irclogger` calls
sat on the line above them, ungated — so each reconnect appended a duplicate
copy of the tail of every channel to the log files. Nothing surfaces that: the
duplicate is in a file nobody diffs, at a timestamp that looks plausible.

**The file is named after the conversation partner, never after the window the
line was shown in.** Channel traffic → the channel; private traffic → the other
nick; a line with no user behind it (server notices, MOTD) → the server log.
This is not cosmetic: an incoming private notice is displayed in *whichever
window happened to be active*, and an outgoing `/notice` in *whichever window
you typed it in*, so filing by window would scatter one conversation across
several files and put two notices from the same person in two different ones.

Notices were the case that made this explicit — they were not logged at all,
in either direction. The two directions live in different modules
(`irc_client.noticed`, `commands.Commands.notice`) and must agree on both the
file and the wording, so the wording is `irc_client.notice_log_line()`, imported
by the command. That is the `/msg` lesson applied one level down: two copies of
"how a sent line is recorded" drift, and here they would drift into two halves
of one conversation sitting in two files.

Log line shapes, all distinguishable on sight: `<nick> text` (message),
`* nick text` (action), `-nick- text` (notice).

Left deliberately outside `_log_chat`: `connectionMade` / `connectionLost` call
`irclogger.log_server` directly. Those are not chat, cannot arrive inside a
batch, and must be recorded even if one is open.

Covered by `tests/test_notice_log.py`, which needed `irc_test_server` to grow
CAP ACK (it NAKed everything, so `batch` was never negotiated and *nothing* the
client suppresses during playback was reachable from a test), `BATCH`/`ENDBATCH`
control commands, and `SERVERNOTICE`.

### Scoped mask lists: reading is additive, writing is not

`ignores`, `auto_ops`, `highlights` and `notify` all exist at three scopes —
global, network, channel — and are **read additively** across all three, so an
entry at any one of them can act. Every command that maintains one of these
lists must write with that asymmetry in mind, and `/aop` is where getting it
wrong cost a channel: an entry the user could not see opped someone, who took
the channel over.

Five faults, all of them the same shape — the command answered from a narrower
view of the config than the checker used, and never said so:

1. **A list command must not be built on a context-sensitive collector, and
   must have no narrowing flag.** `/aop -l` called
   `get_auto_ops(network_key, channel)` with the channel taken from the *current
   window*, and additionally honoured `-w`, which does **not** mean "all
   networks" but "the global scope only". So `/aop -lw` — the obvious way to
   spell "show me everything" — printed "[Auto-op list is empty]" from *every*
   window, including the channel window of the channel whose entries were doing
   the opping. `config.list_all_entries(key)` is the one that answers
   unconditionally — every entry at every scope, each paired with its
   `(network_key, channel)` — and `_show_all_entries` prints them labelled with
   `config.scope_label`. `-w` is now inert in list mode and *says* it is inert,
   because silently ignoring a flag the user typed is the same fault in the
   other direction: an answer to a question other than the one asked. **An
   incomplete list is worse than no list**, because it is used to conclude
   something is not configured.

   The reporter ran it in the **channel** window, not the server window, and
   that detail is what identified `-w`: plain `/aop -l` from a server window
   would have printed the network-scoped entry rather than "empty", so `-w` is
   the only spelling that produces the message they saw. An earlier version of
   this section guessed "server window" and was wrong — the symptom disambiguates
   the path, and it is worth running the old code against the reporter's real
   config to find out *which* path rather than picking a plausible one.
2. **An unscoped remove removes from every scope.** `/aop -r <mask>` used to
   write to one scope guessed from the current window (network scope from a
   server window), so it never touched the channel entry that was doing the
   opping. `config.remove_entry_everywhere` takes it out of all of them and
   returns the list of scopes it hit, which the command names one per line.
   Over-removal is visible and recoverable; under-removal is silent, and is what
   happened. A remove that *does* name a scope still reports every scope where
   the mask remains, with the command to remove it there.
3. **A mutation helper that returns nothing forces its callers to lie.**
   `_modify_list_entry` returned `None` and had a silent do-nothing path when the
   network key was absent from the config; all three callers printed "Removed"
   unconditionally, so a remove that did nothing was indistinguishable from one
   that worked — run it five times and it claims five removals. It now returns
   `LIST_ADDED` / `LIST_ALREADY` / `LIST_REMOVED` / `LIST_NOT_FOUND` /
   `LIST_NO_NETWORK`, and every caller reports the code it got.
4. **An unrecognised flag is an error, never a value.** The old parser kept any
   `-x` whose letters were not all alphabetic as a *positional* argument, so
   `/aop -?` — someone looking for a usage line — was read as "add the mask
   `-?`" and written to the config. `?` is an fnmatch wildcard, so that entry
   auto-opped every two-character nick beginning with `-`. `_parse_list_flags`
   raises on any unknown flag letter and supports `--` for the rare value that
   genuinely starts with `-`.
5. **A component the user omits means "anything", not "impossible".** See the
   next section — it is the same fault (answering from a narrower reading than
   the user's) applied to the mask instead of to the config.

`commands._mask_list_command` is the single body of `/ignore` and `/aop` for
that reason: five identical bugs living in two copies is what having two copies
buys. `/highlight` and `/notify` share the parser, the lister
(`_show_all_entries`), `remove_entry_everywhere` and the `LIST_*` codes.

**Sharing the pieces is not optional, and the reason is `/notify`.** It kept its
own list branch — `get_notify_list(nk)` with `nk=None` for `-w` — through the
first round of this fix, so it still had fault 1 *exactly*, unchanged, after
`/aop` was fixed and documented. Nobody looked, because the bug had a name and
the name was "/aop". A fault found in one of four commands that share a concept
is present in all four until each is shown otherwise. `_show_all_entries` grew
`annotate` (for `/notify`'s online/offline column) and `expand=False` (a
highlight is a substring or a `/regex/`, not a hostmask, and must not be
reported as expanding to one) so that no command has a reason to list on its
own.

Note that `docs/reference.md` already claimed `-l` "shows all scopes" before any
of this was true. The documentation was right and the code silently disagreed,
which means reading the docs *reinforced* the wrong conclusion that the list was
complete. A behavioural test is the only thing that would have caught it.

Covered by `tests/test_aop_list.py`, which drives the real command bodies
against the reporter's config shape and asserts the property that matters: for
any entry at any scope, `-l` shows it and `-r` removes it, from any window.

### Masks: an omitted component means "anything", and only `*` and `?` are wildcards

A mask is `nick!ident@host`, and `config.split_mask` / `config.expand_mask` fill
in whatever the user left out with `*` **before** matching. `_match_any` matches
against the expansion, never against the mask as written.

Matching the mask flat — which is what `_match_any` used to do — makes an
*omitted* component behave like an *impossible* one, because there is nothing in
the pattern to absorb the separator and the text around it:
`hegemon@lakitu.undernet.org` was matched against
`hegemon!~heg@lakitu.undernet.org` and could not match. **The failure mode is
silent and inverted**: the entry names a person and grants nothing, so the user
believes a privilege is configured that in fact is not, and the only way to find
out is that it never fires.

**`x@y` is `nick@host`, never `ident@host`, and every add echoes the
expansion.** This is the one spelling a user cannot resolve by looking at it —
the same text in a `/whois` line is the *ident* — so the second reader of
`/aop hegemon_@1.2.3.4` was the reporter, asking whether it was a bug that it
opped a nick whose ident was something else. It is not: the leftmost component
is the nick whether or not a `!` is present, which is the only reading under
which `hegemon`, `hegemon!~heg` and `hegemon@host` all say something about the
same person. Guessing from the text instead would be worse than either fixed
rule, since an ident and a nick are frequently the same string and the guess
would land differently for different users of the same command.

The fix for the *ambiguity*, as opposed to the reading, is that
`_mask_list_command` prints `[  matches hegemon_!*@1.2.3.4]` under the add
confirmation whenever the expansion differs from what was typed. `-l` already
showed it, but **`-l` is the wrong place to answer this on its own: you have to
already suspect something to go and run it.** The moment a user forms their idea
of what an entry means is the moment they add it, which is the same reason
`_breadth_warning` fires there. The echo is deliberately silent when the mask is
fully spelled out — `matches <itself>` is noise, and noise is what trains someone
to skip the line that matters.

The opposite gap is the one that actually cost the channel. A **nick-only** entry
(`HEGEMON`, which is what the reporter's config held) has no host component at
all, so it ops whoever holds that nick — after a quit, during a netsplit, or
because the nick was never registered. That is the "hegemon@anything else" case,
and it is why the two shapes must not be conflated: `hegemon` and
`hegemon@lakitu.undernet.org` differ by an entire host, and only one of them
names a person. `commands._breadth_warning` reports it on add, for `/aop` only —
`/ignore` confers nothing, and a warning that fires where it does not matter
stops being read. It is deliberately silent for `bob!*@*`, the ordinary way to
write "bob, wherever he connects from"; the strong "matches EVERY user" warning
needs *every* component pure `*`.

`_show_all_entries` prints the expansion beside any entry whose written form
differs from it, so the difference is visible rather than something you have to
know. That is the one place a user can see that `*@some.host` is `*!*@some.host`
— an entry that ops *anyone* on that host, not one person.

**`fnmatch` is the wrong matcher and is no longer used.** It honours `[...]`
character classes, and `[`, `]`, `\`, `^`, `_`, `{`, `|`, `}` are all legal IRC
nick characters — `bob[away]` is the conventional away marker, not an exotic
nick. So `/ignore bob[away]!*@*` matched `boba`, `bobw` and `boby` and did not
match `bob[away]`: the entry pointed at four people, none of them the one named
in it. `config._mask_regex` compiles the mask itself, `re.escape`-ing everything
except `*` → `.*` and `?` → `.`, cached per mask string.

One asymmetry worth keeping: `_match_any` also accepts a bare nick as the
*subject*, and then a mask asserting anything about the ident or host cannot
match it. We do not know those, and for an auto-op list a guess in favour of a
match hands out operator status on the strength of a nick alone.

**Every place that matches a user against a configured mask goes through
`_match_any`.** Two did not, and had both bugs: `irc_client._is_trusted_host`
(`dcc.trusted_hosts` — a match there *skips the "accept this file?" dialog*)
and the `nick_mask` filter in `exec_system._dispatch_on_hooks` (`/on`). Neither
is a mask list command, which is why neither was looked at when `/ignore` and
`/aop` were fixed; both are mask *matching*, which is the thing that was wrong.
`_mask_match` remains for genuinely non-mask wildcard text (`/on`'s message
`pattern`), and even that is better off without fnmatch's character classes.

Covered by `tests/test_aop_list.py` section 7.

### Self-echo suppression (`irc_client.SelfEchoTracker`)

qtpyrc draws its own messages locally as it sends them and does not negotiate
`echo-message`, so an echo coming back from a bouncer would draw them a second
time. `conn._own_messages` / `conn._own_actions` record what to expect
(`record(target_lower, text)`, from `Commands.say` and the `/me` path) and the
incoming handler drops the line when `claim()` matches.

**The match is not byte-exact, on purpose**, and that is the whole point of the
class existing instead of a list and an `==`. The echo is the *server's* copy of
the line — what everyone else on the channel saw — and a server may normalise it
on the way through. Two transformations are routine and neither is visible by
eye:

- **Trailing whitespace is stripped.** Libera does this. This was the
  long-lived "some of my messages appear twice" bug: the echo matched nothing,
  so it was drawn *and saved* on top of the local copy. It took so long to find
  because a trailing space is invisible in a log, in a paste, and to every
  duplicate scan that compares text for equality — which is why the database
  looked clean when it was not. Anything that compares a sent line to its echo
  needs the same tolerance; the bouncer's `_match_pending_echo` needed fixing
  too.
- **The line is truncated** to fit the 512-byte protocol limit, computed by the
  server from *its* idea of our hostmask. A client cannot know that length, so
  it must accept a shortened echo — but only one that keeps a healthy prefix
  (`MIN_TRUNCATED = 40`), or a short common line ("ok", "yes") would claim an
  unrelated longer one that happens to start the same way.

Entries expire (`WAIT_SECS`, `MAX_ENTRIES`), which the old plain lists did not:
on a network that never echoes — the ordinary case — every message the user sent
was appended and never removed, so the list grew for the whole session. Covered
by `tests/test_self_echo.py`.

**Only the channel paths are wired up** (`chanmsg` claims from `_own_messages`,
`action` from `_own_actions`). The query path records nothing and consults
nothing, and that is *currently* harmless only by accident — see the
"PMs sent from another client attached to the same bouncer never appear" entry in
`known-issues.md`. Anything that changes how an echoed PM is routed has to add
the `record()`/`claim()` pair in the same change, or it turns a missing message
into a doubled one.

### Mode prefixes

`_pnick(nick, channel)` prepends mode symbol when `show_mode_prefix` enabled. `_nick_prefix(nick, channel)` returns just the symbol. Stored in `User.prefix[irclower(channel)]`, updated by NAMES reply and MODE changes. Saved to history DB `prefix` column for replay.

**The prefix belongs to a membership, so it dies with one.** `User` objects
live in `client.users` for the whole session and are shared by every channel,
so an entry left in `User.prefix` outlives the thing it described: someone
parts as an op and rejoins still wearing the "@", and `userJoined` stamps that
stale symbol into the join line and the history row. `Channel.removenick` and
`Channel.rejoined` now drop it (`Channel._chnlower()` is the key, `irclower`
like everywhere else), and **`names()` assigns unconditionally** — a NAMES
token *without* a symbol has to clear one, because NAMES is the authority on
who holds what and the absence of a prefix is as much a statement as its
presence. Setting only when non-empty, as it used to, meant nothing could ever
clear a prefix except an explicit `-o` that arrived while you were watching.

**A client cannot distinguish a stale prefix from a current one, so do not try
to.** The "i'm in #ops and it doesn't show me as having ops" report was Wicket
(`irc bouncer`) never applying MODE to its own member table and replaying a
months-old NAMES on attach. The fix belongs there; re-issuing NAMES from
qtpyrc on attach would hide the bug rather than fix it, and would hide the next
one too. See `known-issues.md`.

### History DB

- Channel key: `"#channel"` (lowercase)
- Query key: `"=nick:ident"` (lowercase, ~ stripped from ident)
- `_history_save()` / `_history_replay()` in irc_client.py
- Replay inserts lines then `add_separator(" End of saved history ")`
- Bouncer playback shows separate start/end separators

**Every read returns rows in one shape: `(id, ts, type, nick, text, prefix)`.**
`get_last` / `get_before` / `get_chunk` and their `HistoryReader` counterparts
all agree, and `irc_client._render_history_row` is the single function that turns
one of those tuples into a visible line (used by `render_history_rows`, the lazy
scroll-up prepend and the drip-feed's tail flush alike — the last of those used
to inline its own copy of the loop and silently skipped filling the channel's
history buffer).

**The id is not decoration.** Two consumers need it. The lazy scroll-up loader
and the drip-feed walk the table by id and used to be handed it separately,
alongside the rows (`oldest_id` / `last_id`) — `_history_replay` no longer runs a
second `replay_bounds` query for what `rows[0][0]` already says. And
`render_audit` uses it as the identity of a rendered line: see its section below
for why nothing else it could compare on is unambiguous.

**Three connections, and the rule is that the GUI thread never writes.**
`HistoryDB._wconn` belongs to the writer thread and does every INSERT, DELETE
and WAL checkpoint; `HistoryDB._rconn` belongs to the GUI thread and is
`query_only`; `HistoryReader` owns a third, also `query_only`, on its own thread
for the drip-feed replay.

**No filesystem work on the GUI thread, and "bounded" is not the same as
"fast".** `add()` used to run the INSERT and the commit inline, on the argument
that one indexed insert plus a WAL commit is bounded work. It is bounded in
*rows touched*; it is not bounded in *time*, because the commit is a `WriteFile`
against the WAL and a syscall against a loaded filesystem takes as long as the
filesystem takes. That is the reported bug — "it hangs for a few seconds before
reacting after I hit enter on a post... I think it may be when the filesystem is
under load" — and on the send path this sat between putting the line on the wire
and drawing it.

It was not even the only wait. Two write connections existed (the GUI thread's
and the maintenance thread's), WAL allows one writer, so the two were serialised
by `busy_timeout` — set to **15000**. Every 500 inserts the maintenance pass took
a write transaction to prune and checkpoint, and until it let go the GUI thread's
next insert blocked. By design, for up to fifteen seconds. **One writer thread
removes that rather than tuning it**: there is no second writer left to wait for,
and `busy_timeout` stops being load-bearing (it now covers only an outside
writer — a second qtpyrc, a `sqlite3` shell).

**The visibility requirement is real, and it is satisfied by ordering the read,
not by blocking the write.** A replay bounded by `current_max_id()` must be able
to see the row that id names, or a line is written to the table, excluded from
its own backlog by the cutoff, and never drawn again — a message that vanishes
when the window is next opened. So:

- **Ids are allocated on the calling thread**, before the write is queued, and
  `current_max_id()` answers from that counter. One writer means they still
  reach the table in order, and an explicit id on an `AUTOINCREMENT` column
  keeps the sequence in step.
- **Every read drains the queue first** (`flush_pending()`, a no-op job on the
  single-worker FIFO executor — waiting on it *is* "the table has caught up").
  Normally the queue is empty and it is free. When it is not — exactly when the
  filesystem is misbehaving — a *read* waits, where the old code made every
  *message* wait. Reads happen on a join, on opening a window and on scrolling
  to the top; writes happen on every line of traffic. `HistoryReader` pays the
  same barrier on its own thread, so the drip-feed never charges it to the GUI.
- **Anything reading outside the named methods goes through `read_conn()`**,
  which applies the barrier and hands back the `query_only` connection. Find in
  All Windows is the one such caller; it used to reach into `db._conn` directly,
  which after this change would have been the writer's connection, on the wrong
  thread.

`query_only` on the GUI connection is not decoration either: it is what turns a
future "just one little write here" into an exception at the call site instead of
a silent reintroduction of the second writer.

**What stays true from before**, and still matters:

- `PRAGMA wal_autocheckpoint=0` on the writing connection. Otherwise SQLite runs
  the checkpoint — and its fsync — inside whichever `commit()` pushes the WAL
  past 1000 pages. That used to land on the GUI thread mid-message (0.96s per
  500 inserts warm, seconds when cold); it would now land in the middle of the
  write queue, which is better but still not chosen. `_maintain()` takes the
  checkpoint (`PASSIVE`, so it never waits for a reader), and `close()` does a
  final `TRUNCATE` so the WAL doesn't survive the session.
- Pruning is **proportional**: `add()` records `(network, channel)` in
  `_dirty`, and a pass looks only at those. Per channel it is one indexed probe
  for the id of the keep-th newest row; if there is none the channel is under
  the limit and nothing is read, written or committed. The old pass found
  channels with `SELECT DISTINCT network, channel` (full index scan) and pruned
  each with `DELETE ... WHERE id NOT IN (SELECT id ... LIMIT keep)`, which
  materialises up to *keep* ids into an ephemeral index — on the real database
  that was 188 channels, none of them over the limit, so the entire pass
  deleted nothing.
- `close()` **drains, never cancels.** What is queued has already been shown to
  the user, so dropping it makes the backlog disagree with what they read.

Net: 500 messages cost 1.12s of GUI thread before the maintenance split and
0.06s after it; none of it is on the GUI thread now. Covered by
`tests/test_history_maint.py`, which stalls the writer deliberately — a
benchmark against a healthy disk cannot fail, and would have passed against the
code that produced the report.

### Never put a filesystem syscall on the GUI thread (`bgwriter.py`)

The general form of the bug above, and the reason `bgwriter.py` exists. qtpyrc
runs asyncio on the Qt GUI thread, so **anything that thread waits for is a
freeze of the whole client**, and a file write is such a wait. It reads as free
because a buffered `write()` to an open handle is microseconds — but the
`flush()` after it is a syscall, and the syscall is the part that stops.

`BackgroundWriter` is one thread and one FIFO queue; `bgwriter.shared()` is the
process-wide instance. `IRCLogger.log()` and `render_audit._write()` work out
their path and their text and hand it over. Four things about it are
load-bearing:

- **One thread, not a pool.** A log file is read by a human as a transcript, so
  submission order is the order the lines must appear in. A single consumer
  gives that for every file at once, with no locking.
- **Flush when the queue drains, not per line.** Idle — when an unexpected crash
  is most likely to cost something — the queue empties after every line, so this
  is exactly the old per-line durability. A burst batches, which is the case
  where per-line flushing bought little anyway.
- **`open()` and `os.makedirs()` are filesystem calls too.** `IRCLogger` cached
  its handles, so that cost read as "once per file, negligible" — but once per
  file means once per conversation partner, once per file *per month* under
  `logging.separate_by_month`, and once more after every write error, since the
  recovery path drops the handle so the next line reopens. All of it is on the
  writer thread now.
- **The queue is bounded and drops are reported.** A filesystem that has stopped
  answering must not also become unbounded memory. Past the bound, lines are
  dropped and counted, and the count is written into the file — a silent hole in
  a log is how someone concludes a conversation never happened.

Two rules for anything that joins it:

- **The timestamp is taken by the caller, never by the writer thread.** It
  records when the line happened, not when the disk accepted it, and under
  exactly the load this exists to survive those two are seconds apart.
- **Nothing on the chat path may call `flush()`.** It is for shutdown and for
  tests. `IRCLogger.close()` only flushes; the shared writer is owned by process
  shutdown (`bgwriter.close_shared()`, last in both shutdown paths in
  `qtpyrc.py`), because the render audit shares it.

Covered by `tests/test_bgwriter.py`, which stalls the writer and asserts that
writing 200 lines still returns in milliseconds and that no `open`/`makedirs`
runs on the caller's thread. It found two real bugs in the first version:
`flush()` and `close()` used `put_nowait`, so both failed precisely when the
queue was full — the one moment either is most needed.

**`tests/test_msg_history.py` ties both halves to the path the user takes.** It
already drives all six send routes against a live client; it now also records
which thread `BackgroundWriter._emit`, `HistoryDB._w_insert_history` and
`_w_insert_url` actually ran on, and fails if any of them is the GUI thread.
Watching where the write *lands* rather than how long the send took is what
makes it hold on a fast disk — a latency assertion there would pass against the
broken code every time the filesystem happened to be idle.

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

**Removing the cursor move had a consequence, and it is a rule of its own: use
`setExtraSelections()` to mark text, never `setTextCursor()`.** Chat lines are
appended through `Window.cur`, a *separate* `QTextCursor`, so the widget's own
cursor is moved by nothing in normal operation — `moveCursor(End)` was the only
thing dragging it back to the bottom, by accident. Once that was gone, a cursor
parked by a click or by either find path (`SearchBar._apply_found`,
`find_in_all._apply_highlight`) stayed parked for the session, and
`ChatOutput.contextMenuEvent` — which selected the nick under the pointer and
restored the previous cursor when its popup closed — began scrolling the view
there on every right-click. Reported as "every time i right-click whois
someone, the channel window scrolls way up".

`setTextCursor()` is not a way to highlight text. It moves the caret, and
scrolling to it and replacing the selection are side effects, not options. The
second of those was the quieter bug in the same three lines: the popup's Copy
item exists *because* the user has a selection, and `popups.show_popup`
implements it as `output.copy()` — so selecting the nick made Copy copy the
nick instead of what they had picked. `_highlight_anchor_at` /
`_clear_anchor_highlight` now use an extra selection, which draws and does
nothing else, and which is *appended* to the list rather than replacing it so a
find result survives a right-click.

Covered by `tests/test_autoscroll.py`, whose sections 6a and 6b are deliberately
two separate windows: making a selection moves the cursor to it, near the
bottom, which is exactly the state in which the scroll bug does not happen. The
first version did both in one window and passed against the broken code.

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

**Arrange against the domain the arrangement will land in, not the one it
starts from.** The MDI area's scroll bars are `AsNeeded`, and every arranger
measures `viewport()` — whose size those bars decide. So a bar left up by the
previous arrangement (a cascade deliberately overflows) shrinks the viewport the
next tile measures, and the tile then removes the bar it just made room for,
leaving its rows one scroll bar extent short of the workspace. `_arranging()`
therefore pins both policies to `ScrollBarAlwaysOff` for the duration and
restores them afterwards, so `AsNeeded` recomputes against the finished layout.
This is why it wraps *all three* of `tileSubWindows` / `cascadeSubWindows` /
`tileVertically` rather than living in the one that had the visible symptom.

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
  live output. **Every path that ends a replay must call it — including the ones
  that end it by never starting it.** The queue is opened by whoever *creates*
  the window (`joined()`, `_find_or_create_query()`), long before anyone asks how
  much there is to replay, so a later "nothing to replay after all" is a path out
  of an already-open queue, and leaving it open makes the window mute for good.
  Two such paths exist, and both were once that bug:
  `qtpyrc._bg_replay_drop()`, reached when the drip-feed finds a window with no
  backlog or with replay disabled for it; and `irc_client._history_replay()`'s
  early return (`if not db or limit <= 0`) on the synchronous path — `limit <= 0`
  is exactly what `history_replay.queries: 0` means, and the settings dialog used
  to write that into every config that was opened.
- `headless.StubWindow` implements the same protocol as no-ops (headless prints
  as lines arrive, so it never holds anything back). **A missing member there is
  not a missing no-op, it is an `AttributeError` in shared code** — and the
  shared renderer `render_history_rows()` *reads* `window._auto_scroll` to save
  it around the replay, so leaving it off the stub killed the whole drip-feed
  task for every channel in headless mode. `_auto_scroll`, `_in_replay`,
  `_history_more`, `_scroll_to_bottom`, `_clear_nick_typing` and
  `_update_typing_bar` are part of that contract for exactly this reason.

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

### Plugins live on a search path, and a profile copy is the failure

`plugins.plugin_search_path()` is the profile's plugin directory (`plugins.dir`,
resolved against the *config file's* directory — `me/config.yaml` means
`me/plugins/`) followed by the application's own `plugins/`, where the shipped
plugins live. `find_plugin(name)` walks it in order and returns a `PluginFile`
saying which file won; `available_plugins()` returns the union, first-wins;
`_import_script` executes that file. **Nothing loads a plugin by importing its
bare name any more**, because two directories can offer the same name and only
`find_plugin` gets to decide which one is meant.

Before this there was one directory, and the way that was papered over was to
**copy the shipped plugins into the profile** when the profile was created. That
copy is the bug, not the workaround for it: it forks every shipped plugin at
creation time, and from then on the profile silently runs a version that
receives no fixes. It had already happened — the reporter's `me/plugins/`
carried a six-month-old `triviabot` with no `plugin_prefix` support, and nothing
anywhere said so. `qtpyrc._create_profile` therefore no longer copies plugins.

The two reported symptoms were both downstream of the single directory, which is
worth recording because neither one names it:

- **A plugin in `auto_load` but only in the application's directory had no
  settings page.** `page_plugin_config.get_plugin_names()` lists a plugin only
  if it *loaded* and declared `config_fields`. It never loaded, so the settings
  tree simply did not mention it — the failure mode this file keeps paying for,
  where the code does something reasonable-looking and says nothing.
- **Its row in the Plugins page was indented.** `page_scripts` classified a name
  that was in `auto_load` but not in the directory as *external* and drew it
  with `_add_external_item` (a checkbox+X composite widget, hence the extra
  margin) instead of a plain `QListWidgetItem`. The indentation was the only
  visible trace of the real fault.

So: **anything that answers "which plugins are there?" asks `plugins`, never
`os.listdir` of one directory.** `page_scripts._scan_plugins`,
`_resolve_edit_path`, `_expand_patterns`, `_is_in_dir` and `Commands.plugins`
all go through `available_plugins` / `find_plugin` / `search_path_for`, so what
is listed, what Edit opens, and what loads cannot disagree. `search_path_for`
takes the profile directory as an argument for exactly one reason: the settings
dialog must be able to ask about the directory the user is *currently typing*,
not the one that was loaded.

**A search path makes the origin of a plugin invisible, so it has to be shown.**
An override is a legitimate thing to want, and a stale copy is
indistinguishable from one — the difference is intent, which only the user
knows. Both `/plugins` and the settings page therefore name the file each
plugin was found in and flag anything it shadows. `_update_dir_status` also
stops painting a missing plugin directory red when it was not configured: with
a fallback that works, red there is a false alarm, and false alarms are how a
user learns to ignore the colour that matters.

Two smaller things fell out and are worth not re-breaking:

- **Modules are registered under `_MODULE_PREFIX + name`, not their bare name,
  and executed from an explicit file path.** The old package-style import
  (`<basename-of-dir>.<name>`) went through the *repo root*, which contains both
  `plugins.py` and `plugins/` — a module and a directory of the same name, whose
  resolution order is a Python detail and not a decision anyone made. Executing
  from the path also means reload has nothing to reload: a plugin that moved
  between directories is picked up from wherever it is now.
- **`_expand_auto_load` takes a list of directories.** Command scripts pass
  `[cmdscripts_dir]` (they have one directory, not a search path) — the list is
  not decoration, it is what stops the plugin path leaking into the script path.

Covered by `tests/test_plugin_search_path.py`, which writes a plugin into each
directory plus one name into both, and asserts on resolution order, the union,
wildcard expansion across both, and that a reload replaces registrations rather
than accumulating them. Verified to fail against a one-entry search path, with
"a plugin present only in the application directory was not found" among the
eight failures.

### Plugin registration: it fires, or it refuses — never neither

A plugin can register a slash command (`irc.add_command`), an application-wide
hotkey (`irc.bind_key`), an `/on` hook (`irc.on`) and a timer (`irc.timer`).
All four are stored in `state`, all four are owned by the plugin that made
them, and all four obey one rule: **a registration that cannot possibly fire is
refused at registration time, loudly, rather than accepted and then ignored.**
That is not a stylistic preference — it is the failure mode this codebase keeps
paying for, in four different subsystems already: a config entry the lister
would not show, a JOIN sent three times because the hook ran per-005, an `/aop`
mask that expanded to something other than what the user read, and a `-w` flag
that answered a narrower question than the one asked. In every case the code
did something reasonable-looking and said nothing, and the user concluded the
opposite of the truth.

So:

- **`add_command` refuses a built-in name.** The lookup order in `docommand` is
  built-in → plugin command → `/alias`, so a plugin registering `msg` would be
  shadowed by `Commands.msg` forever. It also refuses `exec`, which
  `hasattr(Commands, ...)` does *not* catch: `docommand` rewrites `exec` to
  `exec_` on the line above the lookup, so the check has to name it explicitly.
  `/alias` warns when it shadows a built-in *or* a plugin command, since an
  alias is looked up last and would otherwise be defined successfully and never
  run.
- **`bind_key` refuses a sequence Qt cannot parse — and the test for that is an
  empty `toString()`, not `isEmpty()`.** `QKeySequence('not a key')` is not
  empty: it holds one key, `Qt.Key_unknown`, and reports `count() == 1`. Only
  the round trip back to text shows it up, by coming back blank. The first
  version of this checked `isEmpty()`, which caught a literally empty string and
  nothing else — so every typo in the hotkey settings box installed a live
  QShortcut bound to a key that does not exist, registered and listed by
  `/hotkeys` and unable to fire. Bindings are keyed by the canonical
  `toString()`, so `f12`, `F12` and `  F12  ` are one binding rather than three
  (which is also what stops a reload accumulating them).
- **`/hotkeys` exists because an application-wide hotkey is otherwise
  invisible.** The only other way to find out what F12 does is to press it.

**Ownership is per-plugin, and teardown lives in the loader.** `plugin.irc` is
a module-level singleton, so its `_owned_*` lists were shared by everything
that touched it — `remove_all()` tore down *every* plugin's hooks, and
reloading one plugin silently disarmed the others. `_Irc.for_plugin(owner)`
returns a `_PluginIrc` view: same live state (read through to the singleton and
to `state`), its own registration lists. The loader gives each plugin its own
view and calls `view.remove_all()` in `plugins.unload_plugin` — *not* from
`Callbacks.die()`, because overriding `die()` without chaining up is both easy
and common, and a plugin whose command outlives it raises from a dead instance
while a hotkey that outlives it does so with no visible cause at all.

**`irc.clients` / `irc.config` / `irc.app` are read from `state` on every
access.** They used to be copied in `_init` at startup, and
`qtpyrc._reload_config` *replaces* `state.config` with a freshly parsed
`AppConfig` — so from the first Reload Configuration onward every plugin was
reading the settings the client launched with, with nothing to say so.

**`config_changed(self, irc)` is for the settings that cannot be read lazily,
and only those.** `irc.get_config()` reads the current config each time it is
called, so a plugin that looks a setting up at the point of use is already
current and needs no notification. A hotkey is a live `QShortcut` and a command
name is a key in a registry: both were handed to something else at registration
time, so changing them in the settings dialog did nothing at all until the
plugin was reloaded — the setting appeared to save and had no effect.
`plugins.dispatch_config_changed()` is called from `settings_dialog._apply_to_ui`
and from `qtpyrc._reload_config`, which are the two places the configuration
changes under a running client.

Covered by `tests/test_plugin_commands.py`.

### nowplaying (`plugins/nowplaying.py`): two sources, because the obvious one is dead

Announces the track foobar2000 is playing. It was written against
**foo_comserver2**, the component everyone means by "foobar2000 COM" — and that
component **cannot work on any current foobar2000, by construction**. It is a
32-bit-only build last released for foobar2000 0.9 (foobar2000's own
troubleshooter lists it for repeated crash reports), foobar2000 has been 64-bit
by default since v2.0, and a 32-bit DLL cannot load into a 64-bit process. No
amount of configuration reaches it. The reporter's machine was the ordinary
case, not an unlucky one: foobar2000 v2.26 x64, no `Foobar2000.*` ProgID
registered in HKCR/HKCU/HKLM at all, and an orphaned 32-bit `amip.dll` sitting
unloaded in `components/` as the fossil of the 32-bit install it came from.

**The lesson is about verification, not about foobar2000.** The COM API shape
was taken from the component's documentation and every layer in front of it was
tested against a fake, so the tests were green and the plugin was undeliverable.
A fake proves the code does what you told it to; it cannot tell you that you are
talking to something that does not exist. Where an integration cannot be
exercised, **check that the target is real before trusting a green suite** — the
five-minute registry scan that settled this was available the whole time.

So there are two sources, and `_Source` (`fetch` + `probe`) is the seam:

| source | component | works with | needs |
|---|---|---|---|
| `beefweb` | foo_beefweb | v1.6+, 32- **and** 64-bit, and remote hosts | nothing (stdlib HTTP+JSON) |
| `com` | foo_comserver2 | 32-bit v1.x only | pywin32 / comtypes |

`source: auto` tries beefweb then COM. COM is kept rather than deleted because
it is the only option on a 32-bit v1.x install, and deleting a working path for
a shrinking group is not a fix.

Four things are load-bearing rather than incidental:

- **A stopped player is an answer, not a failure.** `fetch` returns
  `playing: False`; only a source that cannot answer at all raises
  `NotRunning`. Get this backwards and `auto` falls through from a perfectly
  healthy beefweb to COM, and the announcement reports some other component's
  idea of the truth.
- **beefweb's `columns` parameter is comma-separated with backslash escapes**
  (boost `escaped_list_separator`, via `tryParseValueListStrict` in
  `cpp/server/parsing.hpp`). Commas in a title-format spec are not exotic —
  `$if(%artist%,%artist%,unknown)` is how everyone writes a fallback — and an
  unescaped one does not arrive wrong, it arrives as *three separate columns*
  and the announcement becomes the fragment `<$if(%artist%>`. Backslash needs
  escaping too, for a different reason: boost expands `\n` and **throws** on an
  escape it does not recognise, so a stray backslash is an HTTP 400 rather than
  a bad string. `_beefweb_escape` handles both; the test un-escapes with its own
  independent implementation, because a test that undoes the escaping with the
  same code that applied it agrees with itself no matter what the server wants.
- **An unrecognised `source` is refused, not silently read as `auto`.** Same
  rule as the rest of this file: answering a narrower or different question than
  the one asked, in silence, is the failure mode this codebase keeps paying for.
- **`GetActiveObject`, never `Dispatch`** (COM path). `Dispatch` *launches* the
  application, so a hotkey pressed by accident would start foobar2000. "Nothing
  is playing" must not become "something is now playing".

**`DEFAULT_FORMAT` is entirely conditional, and every branch of it was measured
against a real library rather than reasoned about.** It announces
`Artist - Title [320kbps mp3]`. The obvious spelling —
`%artist% - %title% [%bitrate%kbps %codec%]` — is wrong for most of a real
collection, in four separate ways, each found by querying beefweb's playlist
API over a 9,000-track sample of one 32,483-track library (which reads other
people's tracks without touching playback, so it costs the user nothing):

| case | share | naive result |
|---|---|---|
| no artist tag | **39%** | `? - Title` — an absent field renders as a literal `?` |
| lossless | flac/wav/ALAC/WMA-L | `950kbps flac` — a bitrate that means nothing |
| bitrate unknown to foobar2000 | raw `.aac`, `.webm` | `?kbps aac` |
| internet radio | — | `Title []` — no filename, so no extension |

Two of those are worth stating as rules:

- **Lossless is detected with `%__bitspersample%`, never with the extension.**
  `.m4a` holds *either* lossy AAC or lossless ALAC and that library has both
  (267 / 180), as it does for `.wma`. So "is it FLAC?" is not merely a special
  case in the sense this codebase usually means — it gets the answer *wrong*
  for 180 files. The bits-per-sample test is both the general rule and the
  correct one.
- **A missing bitrate cannot be computed, so it is omitted.** Raw `.aac` has no
  `%length_seconds%` either, so deriving it from `%filesize%` yields 19018; for
  `.webm` the arithmetic would count the video stream. `[aac]` is the honest
  answer.

And two syntax traps, both invisible until run:

- **`[` and `]` are foobar2000's *conditional* syntax, not literals.** They must
  be single-quoted (`' ['`, `']'`) or they are silently swallowed — the first
  version printed `Artist - Title 320kbps mp3` with no brackets at all.
- **The default now depends on `_beefweb_escape`** for its `$if`/`$puts`/
  `$ifgreater` argument commas. The old default (`%filename_ext%`) had none and
  so was immune; this one fragments into 11 columns and announces `<$puts(i>`
  if the escaping regresses. `test_default_format_survives_escaping` guards it,
  and asserts the default *contains* commas first — otherwise simplifying the
  default would leave the test passing vacuously.

`%title%` needs no guard: foobar2000 falls back to the filename for it (present
for all 9,000 sampled). `%encoding%`, `%path_raw%`, `%list_index%` and
`%queue_index%` are *not* available through beefweb and return `?` — do not
reach for them.

**The query runs on a worker thread whichever source is used**, with the result
returned through a `QObject`/`Signal` created on the GUI thread. Both an
out-of-process COM call and an HTTP request block until the other program
answers, and foobar2000 can be busy, minimised, rescanning its library or
showing a modal dialog. On the GUI thread that is a freeze of the whole client —
this project already tracks GUI-thread stalls as a bug class of its own
(`hang_watchdog.py`, the history maintenance thread), and one caused by
*another program* would be the hardest of them to attribute.

**`/np -probe` reports every source, not the selected one.** The failure this
plugin hands a user is "nothing happened", and the causes are many and
unrelated: no component, the wrong component for their foobar2000's
architecture, the player not running, a wrong URL or ProgID, a missing COM
library. Probing only what is configured is how someone ends up installing
pywin32 to fix a missing foo_beefweb.

Sending goes through `commands.send_message` / `commands.send_action`, per the
rule above; errors are always local and never reach the channel. The target
window is captured when the key is pressed, not when the answer arrives, and
`window._widget_alive()` (the same question `link_preview` asks) decides
whether there is still anywhere to put it.

Covered by `tests/test_plugin_commands.py`. The beefweb half runs end-to-end
against a stdlib `HTTPServer` — escaping, JSON mapping, stopped/paused, negative
position/duration, malformed body, HTTP error — because it speaks a protocol a
test can actually reproduce. The COM half is still only a fake, which is exactly
the limitation described above.

**Tests must stub a *source*, never the module-level `_fetch`.** The originals
assigned `mod._fetch` and never restored it, so every later test in the file ran
against whichever stub was installed last — which is how the first version of
the beefweb tests "passed" without making a single HTTP request. `stub_source()`
registers an entry in `SOURCES` and removes it in a `finally`, which is both
undoable and more faithful: it leaves the real selection, fallback and error
handling in the path under test.

### Hang watchdog (`hang_watchdog.py`): the instrument must not be the fault

A QTimer on the GUI thread bumps a monotonic heartbeat; a plain daemon thread
(deliberately *not* an asyncio task — that would live on the very loop that is
blocked) samples it, and a gap past `logging.hang_watchdog.threshold` is a
stall. It records the GUI thread's Python stack via `sys._current_frames()`,
which names the blocker even when the thread is deep inside C/Qt, because the
last Python frame is whatever called in.

**When the Python stack has nothing to say**, `logging.hang_watchdog.
native_stacks` lets it shell out to `py-spy dump --native` against its own pid.
That is the part with the trap in it, and the trap sprung: **the watchdog became
the largest single source of the freezes it exists to find.**

- py-spy freezes *every thread in the target process* while it walks them, and
  the target is us. Measured across `me/hangs.log`: **429 seconds of
  self-inflicted suspension over 109 samples.** Stalls where it ran took a
  median 6.6s to recover; stalls where it did not, 3.7s.
- **There is no cheap version.** `--native` and `--nonblocking` are mutually
  exclusive ("Can't get native stack traces with the --nonblocking option"), so
  the only lever is *when* to pay, never how much.
- **The cost cannot be capped from inside.** `subprocess.run(timeout=...)` is
  enforced by the calling thread, and that thread is one of the ones py-spy
  suspends, so it cannot fire while the sample it was meant to bound is
  running. Ten samples beat the 10s timeout; the worst ran **50.5s**.
- **Most of it bought nothing.** 68 of the 91 samples caught the GUI thread
  `idle`, 62 of those parked in `NtUserMsgWaitForMultipleObjectsEx` — the event
  loop waiting for work, which is not a stall but the absence of one. The
  sample was taken *after* the Python stacks had been written to disk, and on a
  loaded filesystem that write is itself slow, so the GUI had usually recovered
  in the meantime.

Hence `_maybe_write_native()` has four gates, and the second is the one that
matters: the Python stack must have nothing to say; **the heartbeat is re-read
at the instant of sampling and the stall must still be happening**; it must have
lasted `_NATIVE_MIN_STALL` (5s — freezing an already-frozen application is a far
smaller sin than freezing a responsive one, but a 2.1s blip is not worth it);
and not more often than `_NATIVE_MIN_INTERVAL` (300s, up from 30s — `me/hangs.log`
has a 20-minute stretch on 2026-09-02 spending more time measured than running).

The general rule, which the duplicate-render audit obeys for the same reason:
**an instrument that perturbs the thing it measures must be gated on the
measurement still being live, not on it having started.** Every stall still gets
its free Python stack regardless — that gate is only on the expensive sample.

Note also what the `active` samples said once the noise was gone: they are
`QTextDocumentLayout::ensureLayouted`, `QTextEngine::shapeTextWithHarfbuzzNG`,
`QRasterPaintEngine`, `QBackingStore::flush`, DWrite. That is the `ChatOutput`
paint cost in `known-issues.md`, not a filesystem stall — the two look identical
from the heartbeat and are told apart only here.

### Duplicate-render audit (`render_audit.py`)

The instrumentation for "I keep seeing some of my messages twice", which is what
found the self-echo bug above — and, incidentally, the startup script that ran
four times. Its whole job is to name the path that drew the second copy, and it
is worth having permanently because the alternative is narrowing by elimination:
the reasoning that the duplicate must be a *render*-only path (since
`Commands.say`, `chanmsg()` and `privmsg()` all log unconditionally, so the logs
would show it) was sound and still pointed at the wrong half of the program.

`install()` wraps `Window`'s render methods at class level, from one tuple
(`ENTRY_POINTS`), so `window.py` has no audit code in it and a future entry point
is covered by adding its name there. Installed from `qtpyrc.py` right after the
config loads — it creates no Qt object, so unlike the hang watchdog it can go
ahead of the first window rather than behind it. Config:
`logging.render_audit.*` (`enabled`, `window`, `file`), settings UI on the
Logging page.

**It is off by default, and that is the point of it being config at all.** It
had shipped `enabled: true` in all three places that hold a default, so every
user ran it permanently without ever asking for it — an instrument that wraps
every render method, keys and retains every line drawn, and appends to a log
file for the whole session (1.8 MB in a day on the reporter's machine). An
instrument that ships switched on is one nobody remembers to switch off. Note
that `tests/test_settings_defaults.py` did not catch this and could not: it
checks that `config.py` and the settings page *agree*, and they did — on the
wrong answer.

Three rules decide whether it produces signal or noise, and all three are easy to
get backwards:

- **A call that draws nothing is not a render.** The wrapper compares
  `output.document().characterCount()` before and after, and only records when it
  grew. An `addline_*` that put itself on the replay queue drew nothing, and
  counting it would flag every held-back line against its own flush — the
  ordinary, correct case — burying the real bug in false positives. Asking the
  document is also what keeps the audit from re-implementing (and then drifting
  from) the hold-back rules.
- **The key is content, not appearance.** The live and replayed copies of one
  line carry different timestamps (server tag vs stored row), may differ in mode
  prefix (`/pnick` decorates one from live state, the other from the stored
  `prefix` column), and pick their `QTextCharFormat` independently. So
  `render_key()` drops `timestamp_override`, flattens args recursively keeping
  only strings, and `lstrip`s `~&@%+` from each — one uniform rule rather than
  per-method special cases. It does **not** strip trailing whitespace from the
  text, which is deliberate now rather than accidental: the whole self-echo bug
  was a trailing space, so a key that normalised it away would have hidden the
  thing it was installed to find.
- **Identical text is not enough to make two renders the same line.** This is
  what decides whether the log is readable at all: before it, 992 of the first
  1000 reports in `me/renders.log` were false, and the two real findings were
  buried. Two tests, in order of authority:
  - **The history row id.** Whoever draws a stored row names it, via the
    `render_audit.source_id(row_id)` context manager that
    `irc_client._render_history_row` wraps its whole body in. Two renders that
    name two *different* rows are two different lines, full stop — this is
    exact, and it is the only exact identity a rendered line has. (Cost: two dict
    stores per row, and nothing at all when the audit is off — affordable in the
    inner loop of a several-thousand-row replay.)
  - **Failing that** — a live render names no row — **the minute each was shown
    at, within one minute.** The stored row's stamp on one side; on the other,
    the wall clock, because that is what a live line is stamped with. Treating
    "no `timestamp_override`" as "matches anything" made a live rejoin pair with
    every identical join in the backlog. The one-minute slack is because a live
    line at 19:44:59 and its replayed twin two seconds later name different
    minutes. This is only a fallback: HH:MM means a line said daily at the same
    minute still collides with itself, which is exactly how one report — a
    "hello people" posted at ~17:00 every day, stored three times — survived it
    until the row-id test arrived.

Per-window `OrderedDict` of key → list of (time, stack, timestamp, row id),
stored on the window so it dies with it, evicted by age and by `_MAX_KEYS`. A
*list* per key rather than one entry, because the identity tests mean several
distinct lines can share a key and each needs its own candidate. Stacks are
walked via
`frame.f_back` rather than `traceback.extract_stack()`, which opens the source
file per frame — unaffordable once per line during a multi-thousand-line replay
on the GUI thread. `stop()` disables reporting but leaves the wrappers in place;
unwrapping a class method something else may have wrapped since is the more
dangerous half.

Covered by `tests/test_render_audit.py`.
