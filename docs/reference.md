# qtpyrc Reference

## Command Line

```
python qtpyrc.py [options]
```

| Option | Description |
|--------|-------------|
| `-c`, `--config FILE` | Path to YAML configuration file |
| `-d`, `--debug LEVEL` | Debug output level (0=silent .. 5=trace) |
| `--startup FILE` | Run this startup script instead of the configured one |
| `--no-startup` | Suppress loading the startup script |
| `-r`, `--run PATTERN` | Run additional command scripts (repeatable, wildcards) |
| `--no-scripts PATTERN` | Suppress autoload scripts matching pattern (repeatable, wildcards) |
| `-p`, `--plugin PATTERN` | Load additional plugins (repeatable, wildcards) |
| `--no-plugins PATTERN` | Suppress autoload plugins matching pattern (repeatable, wildcards) |
| `-e`, `--exec COMMAND` | Execute a /command on startup (repeatable) |
| `--ui PATH` | Trigger a `/ui` path on startup (e.g. `--ui menu.tools.colorpicker`) |
| `--ui-list` | Print all registered `/ui` paths to stdout and exit |
| `-o`, `--override KEY=VALUE` | Override a config option at runtime without saving (dot path, repeatable, e.g. `-o font.size=15`). With `--init`, seeds the value into the new file |
| `--init [PATH]` | Generate a new config file and exit. PATH can be a filename, directory, or dir/filename (default: `config.yaml` in current directory). Errors if file exists. Can combine with `-o` to seed values |

Examples:

```
python qtpyrc.py --no-startup                  # skip startup.rc
python qtpyrc.py --startup alt.rc              # use alt.rc instead of startup.rc
python qtpyrc.py --no-scripts "*"              # suppress all autoload scripts
python qtpyrc.py --no-plugins triviabot        # suppress one plugin
python qtpyrc.py --run extra.rc --run debug.rc # run two extra scripts
python qtpyrc.py --run "*.rc"                  # run all .rc scripts
python qtpyrc.py --plugin "*"                  # load all plugins
python qtpyrc.py -e "/connect libera" -e "/join #test"
python qtpyrc.py -o font.size=18 -o font.family=Consolas  # override config at runtime
python qtpyrc.py --init                        # create config.yaml in current dir
python qtpyrc.py --init myconfig.yaml          # create myconfig.yaml in current dir
python qtpyrc.py --init path/to/dir/           # create config.yaml in path/to/dir/
python qtpyrc.py --init path/to/myconfig.yaml  # create myconfig.yaml in path/to/
python qtpyrc.py --init newdir/ -o logging.dir=logs -o history_file=history.db
python qtpyrc.py --ui menu.tools.colorpicker     # launch app and open color picker
python qtpyrc.py --ui-list                        # print all /ui paths and exit
```

## Slash Commands

All commands are prefixed with the configured command prefix (default `/`).

Parameters that accept arbitrary text (messages, reasons, titles) must be quoted with `"` or `'`. Lookup parameters (nicks, channels, networks) are unquoted. Examples:

```
/quit "See you later"
/alert -t "Warning" "Something happened"
/kick baduser "Spamming the channel"
/msg #channel "Hello everyone"
```

### Connection

| Command | Syntax | Description |
|---------|--------|-------------|
| `/connect` | `/connect <network>` | Connect to a network defined in config |
| `/server` | `/server <host> [port]` | Connect to an IRC server (default port 6667) |
| `/quit` | `/quit [message]` | Disconnect from the server (default message: "Leaving") |
| `/away` | `/away ["message"]` | Set away status with message, or clear away if no message |
| `/nick` | `/nick <newnick>` | Change your nickname |
| `/join` | `/join <channel> [key]` | Join a channel, optionally with a key |
| `/part` | `/part [message]` | Leave the current channel (channel windows only) |
| `/hop` | `/hop` | Part and immediately rejoin the current channel (preserves key) |
| `/msg` | `/msg <target> <message>` | Send a private message to a user or channel |
| `/query` | `/query <nick> ["message"]` | Open a query (PM) window, optionally sending a message |
| `/say` | `/say <message>` | Send a message to the current channel or query |
| `/amsg` | `/amsg <message>` | Send a message to all open channels on the current network |
| `/ctcp` | `/ctcp <nick> <type> [data]` | Send a CTCP query (PING, VERSION, TIME, etc.) |
| `/invite` | `/invite <nick> [#channel]` | Invite a user to a channel (defaults to current) |
| `/raw` | `/raw <line>` | Send a raw IRC command to the server |
| `/quote` | `/quote <line>` | Alias for `/raw` |
| `/echo` | `/echo [-w target] <text>` | Print text to the current window (or target window with `-w`) |
| `/log` | `/log [-w target] "text"` | Write a line to the log file for the current window (or target) |
| `/alert` | `/alert [-t "title"] "message"` | Show a popup message box (default title: "qtpyrc") |
| `/stdout` | `/stdout <text>` | Write text to stdout |
| `/stderr` | `/stderr <text>` | Write text to stderr |

### Channel Moderation

| Command | Syntax | Description |
|---------|--------|-------------|
| `/kick` | `/kick <nick> [reason]` | Kick a user from the current channel |
| `/ban` | `/ban <nick\|mask>` | Ban a user (nicks are expanded to `nick!*@*`) |
| `/kban` | `/kban <nick> [reason]` | Ban and kick a user in one command |
| `/op` | `/op <nick>` | Give operator status (+o) |
| `/deop` | `/deop <nick>` | Remove operator status (-o) |
| `/halfop` | `/halfop <nick>` | Give halfop status (+h) |
| `/dehalfop` | `/dehalfop <nick>` | Remove halfop status (-h) |
| `/voice` | `/voice <nick>` | Give voice (+v) |
| `/devoice` | `/devoice <nick>` | Remove voice (-v) |
| `/quiet` | `/quiet <nick>` | Quiet a user (+q) |
| `/unquiet` | `/unquiet <nick>` | Remove quiet (-q) |

### User Info

| Command | Syntax | Description |
|---------|--------|-------------|
| `/whois` | `/whois <nick>` | Query information about a user |

### Ignore & Auto-Op

| Command | Syntax | Description |
|---------|--------|-------------|
| `/ignore` | `/ignore [-lrw] [mask] [#channel] [network]` | Add/remove/list ignore masks |
| `/aop` | `/aop [-lrw] <on\|off\|nick\|address> [#channels] [type] [network]` | Manage auto-op entries |

Flags for both commands:

- **`-l`** — List current entries. For `/ignore -l`, lists all ignores. For `/aop -l`, lists all auto-op entries. Shows entries at all levels (global, network, channel).
- **`-r`** — Remove an entry instead of adding it. E.g. `/ignore -r spammer` removes the mask.
- **`-w`** — Operate at the global (top-level) scope, regardless of which network you're connected to. Without `-w`, entries are scoped to the current network. Combine with `#channel` to scope to a specific channel.

Ignore and auto-op lists are **additive**: channel-level entries add to network-level, which add to global. A user matching any level is considered ignored/auto-opped.

### Highlights

| Command | Syntax | Description |
|---------|--------|-------------|
| `/highlight` | `/highlight [-lrw] [pattern]` | Add/remove/list custom highlight patterns |

Flags: same as `/ignore` (`-l` list, `-r` remove, `-w` global scope).

Patterns: plain strings are case-insensitive substring matches. Use `/regex/` for regex with optional flags: `i` (case-insensitive), `m` (multiline — `^`/`$` match line boundaries), `s` (dotall — `.` matches newlines). Example: `/regex/i`, `/regex/ims`. Use `{nick}` to refer to your current nickname (escaped properly in regex). The default config includes `{nick}`; removing it disables nick-mention highlighting. Unknown `{name}` references produce a one-time warning. Use `\{` and `\}` for literal braces, `\\` for a literal backslash (so a literal `\{` requires `\\{`). Regex quantifiers like `{3}` and `{1,5}` are unaffected.

Highlights are **additive** (global + network + channel). Set `highlights: false` at the channel level in config to disable all highlights. Set `highlight_notify: false` to suppress beep/desktop notifications while still coloring highlights.

### Notify

| Command | Syntax | Description |
|---------|--------|-------------|
| `/notify` | `/notify [-lrw] [nick]` | Add/remove/list nicks on the watch list |

Flags:

- **`-l`** — List notify nicks with their online/offline status.
- **`-r`** — Remove a nick from the list.
- **`-w`** — Operate on the global list instead of the current network's list.

Nicks are checked via server-side MONITOR when supported (instant push notifications), falling back to periodic ISON polling. When a watched nick signs on or off, a notification is shown in the server window (and optionally a sound/desktop alert per config). Use `/on notify_online` and `/on notify_offline` for custom per-nick actions.

Examples:

```
/ignore spammer!*@*              Add ignore on current network
/ignore -w spammer!*@*           Add global ignore (all networks)
/ignore spammer #channel         Add ignore only in #channel
/ignore -r spammer               Remove ignore from current network
/ignore -l                       List all ignores
/aop trusted!*@* #chan1,#chan2    Auto-op in specific channels
/aop -l                          List all auto-op entries
```

### Scripting

| Command | Syntax | Description |
|---------|--------|-------------|
| `/exec` | `/exec <python code>` | Evaluate Python code (see [/exec context](#exec-context)) |
| `/timer` | `/timer <name> <repeats> <seconds> <command>` | Create a named timer (0 repeats = infinite) |
| `/timer` | `/timer <name> off` | Stop a timer |
| `/timer` | `/timer -l` | List active timers |
| `/timers` | `/timers` | Alias for `/timer -l` |
| `/on` | `/on <event> <name> [#channel] [pattern] <command>` | Register an event hook |
| `/on` | `/on -r [-p] <event> <name>` | Remove a hook (`-p` also removes from startup script) |
| `/on` | `/on -l [event]` | List active hooks |
| `/hooks` | `/hooks` | Alias for `/on -l` |
| `/load` | `/load <script_name>` | Load a Python plugin script |
| `/unload` | `/unload <script_name>` | Unload a Python plugin script |
| `/scripts` | `/scripts` | List loaded Python plugin scripts |
| `/script` | `/script <filename>` | Run a command script (text file of /commands) |
| `/play` | `/play <filename>` | Send a plain text file to the current window line by line |
| `/alias` | `/alias [name] [command...]` | Define, show, or list command aliases |
| `/alias` | `/alias -r <name>` | Remove an alias |
| `/popups` | `/popups` | Reload the popups.ini file |
| `/set` | `/set [name] [value]` | Define/list persistent variables (saved to `variables.ini`) |
| `/set` | `/set -r <name>` | Remove a persistent variable |
| `/var` | `/var <name> <value>` | Define a temporary variable (memory only, lost on exit) |
| `/unset` | `/unset <name>` | Remove a variable (persistent or temporary) |

### Variables

Persistent variables (`/set`) are saved to `variables.ini` (configured via `variables_file` in config.yaml). Temporary variables (`/var`) exist only in memory.

Variables are expanded as `{name}` in all commands, aliases, toolbar entries, and popup menus. In popup and `/exec` contexts, `$name` also resolves user-defined variables (after built-in context variables like `$nick`).

#### Built-in context variables

These are always available and reflect the active window's state. User-defined variables with the same name take priority.

| Variable | Description |
|----------|-------------|
| `{me}` | Your current nickname |
| `{network_key}` | Network config key (e.g. `libera`) |
| `{network_label}` | Display label: network\_key → network\_name → hostname → `unknown` |
| `{network_name}` | Server-reported network name (e.g. `Libera.Chat`) |
| `{network_hostname}` | Connected server hostname |
| `{channel}` | Current channel name or query nick |
| `{query_nick}` | Query peer nick (query windows only, empty otherwise) |
| `{topic}` | Channel topic |
| `{key}` | Channel key (+k), if set |
| `{nicks}` | Number of nicks in current channel |
| `{port}` | Server port |
| `{ident}` | Your ident |
| `{host}` | Your hostname |
| `{address}` | Your full `nick!ident@host` |
| `{realname}` | Your realname / GECOS |
| `{sasl_username}` | Your SASL/NickServ account name |
| `{connected}` | `true` or `false` |
| `{tls}` | `true` or `false` |
| `{window_type}` | `server`, `channel`, or `query` |
| `{networks}` | Number of connected networks |
| `{channels}` | Total channel count across all networks |

### Function calls

`{name}` and `{name()}` are equivalent — both look up the name with no argument. `{name("arg")}` passes an argument. Variables are checked first; if not found, built-in functions are tried. On error, the block is replaced with an empty string. Unknown names are left as-is.

| Function | Description |
|----------|-------------|
| `{eval("expression")}` | Evaluate a Python expression. Namespace includes `state`, `import_module`, `stdin`, `app`, `mainwin`, and `clients`. |
| `{stdin("prompt")}` | Read a line from the terminal (blocking, with readline editing). Prompt is optional. |
| `{input("prompt")}` | Show a GUI input dialog. Returns the entered text, or empty string on cancel. |

Built-in functions are only available in contexts that support them: `titlebar_format`, window title formats (`titles.server`, `titles.channel`, etc.), and `/title` custom titles.

#### Window title formats

The `titles:` config section controls window titles using format strings. All context variables and `{name("...")}` function calls are supported. Defaults depend on `show_network_in_tabs`. Change via `/config titles.channel "{channel} - {topic}"` etc.

| Config key | Default (show\_network\_in\_tabs: true) | Description |
|------------|----------------------------------------|-------------|
| `titles.server` | `{network_label} - {me}` | Connected server window |
| `titles.server_disconnected` | `[not connected] {network_label} - {me}` | Disconnected server window |
| `titles.channel` | `{channel} ({network_label})` | Channel window |
| `titles.query` | `{query_nick} ({network_label})` | Query/PM window |

The `variables.ini` format is compatible with mIRC's `vars.ini` — you can copy your variables file from mIRC. Both formats are supported on load:

```ini
; qtpyrc format
name = value

; mIRC vars.ini format (copied directly from mIRC)
[variables]
n0=%name value
n1=%other value
```

### Configuration

| Command | Syntax | Description |
|---------|--------|-------------|
| `/save` | `/save` | Flush current configuration to disk |
| `/reload` | `/reload` | Re-read configuration from the current YAML file |
| `/config` | `/config [-e] <key.path> [value]` | View or change a config option by YAML path (e.g. `/config font.size 15`). `-e` expands {variables} in value |
| `/settings` | `/settings [page]` | Open the settings dialog. Pages: `general`, `identity`, `font` (or `colors`), `ident_server` (or `ident`), `logging`, `notifications`, `scripts`, `editor`, or `networks.<name>[.server\|sasl\|auto_join]` |
| `/ui` | `/ui [path]` | Trigger any menu action, settings page, or toolbar button by dot-path. With no argument, lists all paths. Prefixes: `menu.*` for menu items (e.g. `menu.tools.colorpicker`, `menu.file.edit.startup`), `settings.*` for settings pages (e.g. `settings.general`, `settings.fonts.chat`, `settings.networks.libera.sasl`), `toolbar.*` for toolbar buttons (derived from tooltip text). Any prefix lists matching paths. Disabled menu actions show a warning |
| `/sounds` | `/sounds [name]` | Browse system sounds, or play one by name |
| `/urls` | `/urls` | Open the URL catcher (browse captured URLs with filters) |
| `/urlcatcher` | `/urlcatcher` | Alias for `/urls` |
| `/toolbar` | `/toolbar` | Reload the toolbar from `toolbar.ini` |
| `/icons` | `/icons` | Browse available toolbar icons |

### Aliases

`/alias <name> <command>` defines a shortcut that expands when you type `/<name>`.

Use `{1}`, `{2}`, etc. for positional arguments and `{-}` for all arguments. If no placeholders are present, all arguments are appended to the command automatically.

Aliases are not persistent — add `/alias` commands to your startup script to recreate them on launch.

```
/alias j /join {1}               /j #channel  →  /join #channel
/alias ns /msg nickserv {-}      /ns identify pass  →  /msg nickserv identify pass
/alias hi /say hello everyone    /hi  →  /say hello everyone
/alias -r j                      Remove the /j alias
/alias                           List all aliases
```

### Window Management

| Command | Syntax | Description |
|---------|--------|-------------|
| `/close` | `/close [-f] [target]` | Close the current window (or target). `-f` skips server confirmation |
| `/find` | `/find [text]` | Open the search bar, optionally searching for text |
| `/title` | `/title [text]` | Set a custom window title format (no args to restore default). `-s` targets server window, `-a` targets app titlebar |
| `/tabbed` | `/tabbed` | Switch to tabbed view mode |
| `/mdi` | `/mdi` | Switch to MDI (multi-document) view mode |
| `/tile` | `/tile [v]` | Tile windows horizontally, or vertically with `v` |
| `/cascade` | `/cascade` | Cascade windows (MDI mode) |
| `/newserver` | `/newserver` | Open a new server window |

---

## /exec Context

When you run `/exec <code>`, the following names are available:

### Objects

| Name | Description |
|------|-------------|
| `irc` | The `plugin.irc` singleton (see below) |
| `window` | The current window |
| `client` | The current Client instance |
| `conn` | The current IRCClient connection (may be None) |
| `config` | The AppConfig instance |
| `clients` | Set of all Client instances |
| `app` | The QApplication |
| `mainwin` | The main application window (MainWindow) |
| `users` | Network-wide user dict (irclower(nick) -> User) |

### Functions

| Name | Signature | Description |
|------|-----------|-------------|
| `say` | `say(msg, target=None)` | Send message to target or current window |
| `msg` | `msg(target, msg)` | Send PRIVMSG |
| `notice` | `notice(target, msg)` | Send NOTICE |
| `raw` | `raw(line)` | Send raw IRC line |
| `join` | `join(ch, key=None)` | Join a channel |
| `part` | `part(ch=None, reason=None)` | Leave a channel |
| `kick` | `kick(nick, reason=None, ch=None)` | Kick a user |
| `mode` | `mode(modestr, ch=None)` | Send MODE command |
| `echo` | `echo(text)` | Print text to the current window |
| `error` | `error(text)` | Print red error text to the current window |
| `nick` | `nick(n=None)` | Get current nick, or set nick if `n` given |
| `me` | `me()` | Get current nickname |
| `channel` | `channel()` | Get current channel/target name |
| `nicks` | `nicks(ch=None)` | Get set of nicks in a channel |
| `user` | `user(nick)` | Look up a User object by nick |
| `history` | `history()` | Get the current channel's history deque |
| `irclower` | `irclower(s, c=None)` | IRC-lowercase a string |
| `irceq` | `irceq(a, b, c=None)` | IRC case-insensitive comparison |
| `network` | `network()` | Get the current network key |
| `networks` | `networks()` | Get dict of all networks (call as function) |
| `docommand` | `docommand(cmd, text="")` | Execute a slash command |
| `timer` | `timer(name, reps, secs, cmd)` | Create a timer |
| `on` | `on(event, name, pattern, cmd, **kw)` | Register an /on hook |
| `stdin` | `stdin(prompt="")` | Read a line from the terminal (blocking, with readline editing) |
| `inputbox` | `inputbox(prompt="Enter value:", title="Input")` | Show a GUI input dialog, return text or empty on cancel |

### Modules

`asyncio`, `re`, `os`, `time`, `fnmatch` are available directly.

---

## Popup Menus

Right-click context menus are defined in `popups.ini` (mIRC-compatible format — you can copy your `popups.ini` directly from mIRC). The file has four sections:

- `[nicklist]` — right-click on a nick in the nick list or chat
- `[channel]` — right-click in a channel window (not on a nick)
- `[status]` — right-click in the server window
- `[query]` — right-click in a query/PM window

### Syntax

```ini
Menu Item:/command              ; item that runs a command
Submenu Header                  ; no command = submenu parent
.Child Item:/command            ; dot prefix = child item
..Grandchild:/command           ; more dots = deeper nesting
-                               ; separator line
; comment                       ; lines starting with ; are ignored
```

Multiple commands can be separated with ` | ` (space-pipe-space):
```ini
Kick+Ban:/mode # +b $$1!*@* | /kick # $$1
```

### Variables

Both mIRC-style and {variable}-style are supported:

| mIRC | {variable} | Description |
|------|------------|-------------|
| `$nick` | `{nick}` | Target nick (nicklist/query) |
| `$me` | `{me}` | Your current nickname |
| `$chan` | `{chan}` | Current channel name |
| `$network` | `{network}` | Network name |
| `$server` | `{server}` | Server hostname |
| `#` | | Current channel (bare `#` in commands) |
| `$$1` | | Selected nick (required — cancels if empty) |
| `$1` | | Selected nick (optional) |
| `$?="prompt"` | | Input dialog (optional) |
| `$$?="prompt"` | | Input dialog (required — cancels if blank) |
| `#$?="prompt"` | | Input dialog, prepends `#` to result |

If no `popups.ini` is found (or a section is missing), built-in default menus are used.

Use `/popups` to reload the file after editing.

---

## /on Events

### Syntax

```
/on <event> <name> [options] [pattern] [command]
/on -r [-p] <event> <name>
/on -l [event]
```

- **event** — one of the event names below.
- **name** — a unique name for this hook (used for removal).

**Options** (before the pattern):

| Flag | Description |
|------|-------------|
| `-n mask` | Filter by sender nick or hostmask (wildcards: `nick`, `nick!*@*`, `*!*@host`) |
| `-c #channel` | Only fire in this channel |
| `-k network` | Only fire on this network |
| `-s sound` | Play a sound: `beep`, `default`, `none`, or a `.wav` path |
| `-d` | Show a desktop notification |
| `-h` | Highlight the channel tab |
| `-p` | Persist by appending to the startup script |

- **pattern** — matched against the event's primary text. Supports wildcards (`*`, `?`) or `/regex/` with optional flags: `i` (case-insensitive), `m` (multiline), `s` (dotall). Example: `/regex/i`, `/regex/ims`. Default `*` (match everything).
- **command** — a command string to execute. Optional if action flags (`-s`, `-d`, `-h`) are used. `{variables}` are expanded before execution (`\{` / `\}` for literal braces, `\\` for literal backslash, so a literal `\{` requires `\\{`). If the command starts with `/exec`, variables are available as Python names instead (see below).

### Examples

```
/on chanmsg friend -n friend -s beep -d *
/on chanmsg important -c #important -s beep -d *
/on chanmsg deploy -n ops_bot *deploy* /echo Deploy detected!
/on chanmsg errors /error\s*\d{3}/i -s beep -d
/on chanmsg vip -n boss -s sounds/vip.wav -d *
/on kick kick_alert -s beep -d *
/on mode ban_alert *+b* -s beep -d /echo Ban: {modes} {args}
/on -r chanmsg friend
/on -l
```

Use `-p` to persist a hook across restarts (appends to the startup script), or add them manually to your startup commands file (configured via `scripts.startup` in config.yaml).

### Event Names

| Event | Fires when | Primary match text |
|-------|-----------|-------------------|
| `chanmsg` | A message is received in a channel | The message |
| `privmsg` | A private message is received | The message |
| `action` | A /me action is received | The action text |
| `noticed` | A NOTICE is received | The message |
| `join` | A user joins a channel | The nick |
| `part` | A user leaves a channel | The nick |
| `quit` | A user quits IRC | The nick |
| `kick` | A user is kicked from a channel | The nick |
| `nick` | A user changes their nick | The old nick |
| `topic` | A channel topic is changed | The nick |
| `mode` | A channel mode is changed | The nick |
| `connect` | Connection to server is established | — |
| `disconnect` | Connection to server is lost | — |
| `signon` | Successfully signed on (registered) | — |
| `motd` | MOTD is received | — |
| `invite` | You are invited to a channel | The nick |
| `rawcmd` | An unknown IRC command is received | — |
| `numeric` | An IRC numeric reply is received | — |
| `ctcpreply` | A CTCP reply is received | The tag + data |
| `notify_online` | A /notify nick signs on (ISON) | The nick |
| `notify_offline` | A /notify nick signs off (ISON) | The nick |

### {Variables} by Event

These variables are expanded in the command string. All events also provide `{network}` and `{me}`.

| Event | Variables |
|-------|----------|
| `chanmsg` | `{nick}` `{user}` `{channel}` `{message}` `{text}` |
| `privmsg` | `{nick}` `{user}` `{message}` `{text}` |
| `action` | `{nick}` `{user}` `{channel}` `{data}` `{text}` |
| `noticed` | `{nick}` `{user}` `{channel}` `{message}` |
| `join` | `{nick}` `{user}` `{channel}` |
| `part` | `{nick}` `{user}` `{channel}` |
| `quit` | `{nick}` `{user}` `{message}` |
| `kick` | `{kickee}` `{channel}` `{kicker}` `{nick}` `{message}` |
| `nick` | `{oldnick}` `{newnick}` `{nick}` |
| `topic` | `{nick}` `{channel}` `{topic}` |
| `mode` | `{nick}` `{channel}` `{modes}` `{args}` |
| `disconnect` | `{reason}` |
| `rawcmd` | `{prefix}` `{command}` `{params}` |
| `numeric` | `{command}` `{prefix}` `{params}` |
| `invite` | `{nick}` `{channel}` |
| `motd` | `{motd}` |
| `ctcpreply` | `{nick}` `{user}` `{tag}` `{data}` `{text}` |
| `notify_online` | `{nick}` |
| `notify_offline` | `{nick}` |

### Using /exec with /on

When an `/on` command starts with `/exec`, `{variables}` are **not** string-substituted (which would break Python syntax). Instead, the exec context includes:

- Everything from the normal `/exec` context (see above)
- `vars` — dict of all `{variables}` (e.g. `vars['{nick}']`)
- `conn` — the connection that fired the event
- Bare shorthand names — `nick`, `channel`, `message`, `network`, `me`, etc.

Example:

```
/on chanmsg greet *hello* /exec irc.msg(conn, channel, "Hello " + nick + "!")
```

---

## plugin.irc Singleton

The `plugin.irc` object is a module-level singleton available to all plugins and `/exec` code. It provides access to the full IRC client.

### Properties

| Property | Description |
|----------|-------------|
| `irc.clients` | Set of all Client instances |
| `irc.config` | The AppConfig instance |
| `irc.app` | The QApplication |
| `irc.mainwin` | The main application window (MainWindow) |
| `irc.active_window` | The window that currently has focus (or None) |
| `irc.networks` | Dict of `network_key -> {'client', 'channels', 'users', 'conn'}` |

### Commands

| Method | Signature | Description |
|--------|-----------|-------------|
| `docommand` | `docommand(window, cmd, text='')` | Execute a slash command as if typed in *window* |

### IRC Methods

All methods below take `conn` (an IRCClient connection) as their first argument.

| Method | Signature | Description |
|--------|-----------|-------------|
| `msg` | `msg(conn, target, message)` | Send a PRIVMSG |
| `notice` | `notice(conn, target, message)` | Send a NOTICE |
| `sendLine` | `sendLine(conn, line)` | Send a raw IRC line |
| `join` | `join(conn, channel, key=None)` | Join a channel |
| `part` | `part(conn, channel, reason=None)` | Leave a channel |
| `kick` | `kick(conn, channel, nick, reason=None)` | Kick a user |
| `mode` | `mode(conn, channel, modestring)` | Send a MODE command |
| `nick` | `nick(conn)` | Get conn's current nickname |
| `network_key` | `network_key(conn)` | Get the config network key for conn |

### Query Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `users` | `users(conn)` | Network-wide user dict (irclower(nick) -> User) |
| `get_user` | `get_user(conn, nick)` | Look up a User by nick, or None |
| `channel_history` | `channel_history(conn, channel)` | Get the history deque for a channel |
| `irclower` | `irclower(conn, text)` | Lowercase using network casemapping |
| `irceq` | `irceq(conn, a, b)` | IRC case-insensitive comparison |

### /on Hook Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `on` | `on(event, name, pattern, command, *, channel=None, network=None, window=None)` | Register an event hook |
| `remove_on` | `remove_on(event, name)` | Remove a hook by event and name |
| `remove_all_hooks` | `remove_all_hooks()` | Remove all hooks registered through this instance |

### Timer Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `timer` | `timer(name, reps, secs, command, *, window=None)` | Create a named timer (0 reps = infinite) |
| `cancel_timer` | `cancel_timer(name)` | Stop and remove a timer |

### UI Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `ui` | `ui(path)` | Trigger a UI action by path (e.g. `'menu.tools.colorpicker'`). Raises `KeyError` if not found |
| `ui_list` | `ui_list()` | Returns `[(path, description), ...]` for all registered UI paths |
| `ui_tree` | `ui_tree()` | Returns a nested dict of the UI path hierarchy (leaf nodes have `'_desc'` key) |

### Convenience Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `say` | `say(conn, target, message)` | Send a message to a channel or nick |
| `channel` | `channel(window)` | Get the channel name or query nick for a window |
| `nicks` | `nicks(conn, channel)` | Get the set of nicks in a channel |
| `me` | `me(conn)` | Get conn's current nickname (alias for `nick()`) |
| `echo` | `echo(window, text)` | Display text in a window |
| `error` | `error(window, text)` | Display red system message in a window |
| `inputbox` | `inputbox(prompt='', title='Input')` | Show input dialog, return text or '' |
| `stdin` | `stdin(prompt='')` | Read a line from stdin (blocking) |

### Plugin Example

```python
import plugin

class MyPlugin(plugin.Callbacks):
    def __init__(self, irc):
        super().__init__(irc)
        # Register a hook via the singleton
        irc.on('join', 'my_welcome', '*',
               '/exec irc.msg(conn, channel, "Welcome, " + nick + "!")',
               channel='#mychan')

    def chanmsg(self, irc, conn, user, channel, message):
        nick = user.split('!', 1)[0]
        if message.strip().lower() == '!ping':
            irc.msg(conn, channel, 'Pong, %s!' % nick)

    def die(self):
        # Automatically cleans up all hooks registered via irc.on()
        super().die()

Class = MyPlugin
```

---

## Object Reference

These objects are available in `/exec`, plugin callbacks, and through the `plugin.irc` singleton.

### conn (IRCClient)

The IRC connection object. Passed to every plugin callback and available in `/exec`. May be `None` if not connected.

| Attribute | Type | Description |
|-----------|------|-------------|
| `conn.nickname` | str | Current nickname on this connection |
| `conn.username` | str | Username (ident) sent during registration |
| `conn.realname` | str | Real name / GECOS sent during registration |
| `conn.client` | Client | The parent Client instance |
| `conn.window` | Window | The server window |
| `conn.channels` | dict | irclower(name) -> Channel (same as `client.channels`) |
| `conn.queries` | dict | irclower(nick) -> Query (same as `client.queries`) |
| `conn.motd` | str | The server's MOTD text |

| Method | Signature | Description |
|--------|-----------|-------------|
| `say` | `say(target, message)` | Send a PRIVMSG to a channel or nick |
| `msg` | `msg(target, message)` | Alias for `say` |
| `notice` | `notice(target, message)` | Send a NOTICE |
| `sendLine` | `sendLine(line)` | Send a raw IRC protocol line |
| `join` | `join(channel, key=None)` | Join a channel |
| `leave` | `leave(channel, reason=None)` | Part a channel |
| `kick` | `kick(channel, nick, reason=None)` | Kick a user |
| `topic` | `topic(channel, topic=None)` | Get or set a channel topic |
| `mode` | `mode(channel, set_, modes, ...)` | Set channel modes |
| `setNick` | `setNick(nickname)` | Change nickname |
| `quit` | `quit(message='')` | Send QUIT to the server |
| `away` | `away(message='')` | Mark yourself as away |
| `back` | `back()` | Mark yourself as no longer away |
| `whois` | `whois(nickname, server=None)` | Send a WHOIS query |
| `me` | `me(channel, action)` | Send a CTCP ACTION (/me) |
| `ctcpMakeQuery` | `ctcpMakeQuery(target, [(tag, data)])` | Send a CTCP query |
| `ctcpMakeReply` | `ctcpMakeReply(target, [(tag, data)])` | Send a CTCP reply |
| `irclower` | `irclower(text)` | Lowercase using the network's casemapping |
| `disconnect` | `disconnect()` | Close the connection |

### Client

Represents one server connection and all its associated state. Access via `conn.client`, `window.client`, or `irc.clients`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `client.network_key` | str or None | Config network key (e.g. `'libera'`) |
| `client.network` | str or None | Server-reported network name (string, for backwards compat) |
| `client.net` | Network | The Network object (see below) |
| `client.hostname` | str or None | Server hostname |
| `client.port` | int | Server port |
| `client.tls` | bool | Whether TLS is enabled |
| `client.conn` | IRCClient or None | The active connection (`None` if disconnected) |
| `client.window` | Serverwindow | The server window |
| `client.channels` | dict | irclower(name) -> Channel |
| `client.queries` | dict | irclower(nick) -> Query |
| `client.users` | dict | irclower(nick) -> User (network-wide) |

| Method | Signature | Description |
|--------|-----------|-------------|
| `reconnect` | `reconnect(hostname=None, port=None)` | Disconnect and reconnect (optionally to a new host) |

### Network

Unified view of a network and its state. Access via `window.network` or `client.net`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `network.key` | str or None | Config network key (e.g. `'libera'`) |
| `network.name` | str or None | Server-reported network name (e.g. `'Libera.Chat'`) |
| `network.config` | ConfigNode | The network's config section (e.g. `network.config.nick`, `network.config.auto_join`) |
| `network.client` | Client | The Client instance |
| `network.conn` | IRCClient or None | The active IRC connection |
| `network.channels` | dict | irclower(name) -> Channel |
| `network.queries` | dict | query key -> Query |
| `network.users` | dict | irclower(nick) -> User (network-wide) |
| `network.hostname` | str or None | Server hostname |
| `network.port` | int | Server port |
| `network.tls` | bool | Whether TLS is enabled |

`str(network)` returns the network name (or key, or empty string), so it can be used directly in string contexts.

### Window

The base GUI window class. Access via `irc.active_window`, `conn.window`, `channel.window`, or `query.window`. All window types (Serverwindow, Channelwindow, Querywindow) inherit from Window.

| Attribute | Type | Description |
|-----------|------|-------------|
| `window.type` | str | Window type: `"server"`, `"channel"`, or `"query"` |
| `window.client` | Client | The Client instance this window belongs to |
| `window.conn` | IRCClient or None | The active IRC connection (shortcut for `window.client.conn`) |
| `window.network` | Network | The Network object (see below). Access `.key`, `.name`, `.channels`, etc. |
| `window.network_key` | str or None | Config network key, e.g. `'libera'` (shortcut for `window.client.network_key`) |
| `window.subwindow` | SubWindowProxy | The tab/subwindow proxy for this window |
| `window.output` | QTextEdit | The chat output widget (read-only) |
| `window.input` | QTextEdit | The text input widget |
| `window.inputhistory` | list | History of lines entered by the user |
| `window.channel` | Channel | The Channel object (Channelwindow only) |
| `window.query` | Query | The Query object (Querywindow only) |
| `window.nickslist` | NicksList | The nick list widget (Channelwindow only) |
| `window.splitter` | QSplitter | Splitter between output and nick list (Channelwindow only) |

| Method | Signature | Description |
|--------|-----------|-------------|
| `addline` | `addline(text, fmt=None)` | Append a line with timestamp and mIRC color parsing. Optional `fmt` sets the base text color (a `QTextCharFormat`). |
| `addline_msg` | `addline_msg(nick, message)` | Append a `<nick> message` line with the nick as a right-clickable anchor |
| `redmessage` | `redmessage(text)` | Append a line in the system color (used for errors, status messages) |
| `addlinef` | `addlinef(text, format)` | Append a line using a specific `QTextCharFormat` (no timestamp, no mIRC parsing) |
| `lineinput` | `lineinput(text)` | Process input as if the user typed it (dispatches commands or sends as message) |
| `setWindowTitle` | `setWindowTitle(title)` | Set the window/tab title |
| `set_activity` | `set_activity(level)` | Set activity level: `Window.ACTIVITY_MESSAGE` or `Window.ACTIVITY_HIGHLIGHT` |
| `clear_activity` | `clear_activity()` | Clear the activity highlight (called automatically when window becomes active) |

Activity level constants:

| Constant | Value | Description |
|----------|-------|-------------|
| `Window.ACTIVITY_NONE` | 0 | No activity |
| `Window.ACTIVITY_MESSAGE` | 1 | New messages (uses `colors.new_message` color) |
| `Window.ACTIVITY_HIGHLIGHT` | 2 | Nick mentioned (uses `colors.highlight` color) |

### Channel

Represents a joined IRC channel. Access via `client.channels[irclower(name)]`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `channel.name` | str | Channel name (e.g. `'#python'`) |
| `channel.nicks` | set | Set of nick strings currently in the channel |
| `channel.users` | dict | irclower(nick) -> User for users in this channel |
| `channel.topic` | str or None | Current channel topic |
| `channel.key` | str or None | Channel key (+k), from config, `/join`, or mode change |
| `channel.active` | bool | `True` if joined, `False` if kicked/disconnected but window kept |
| `channel.history` | deque | Channel history (max 2000 entries, see History below) |
| `channel.client` | Client | The parent Client instance |
| `channel.window` | Channelwindow | The channel's GUI window |

| Method | Signature | Description |
|--------|-----------|-------------|
| `post` | `post(message)` | Send a message to the channel (sends, logs, and displays) |
| `addnick` | `addnick(nick, user=None)` | Add a nick to the channel |
| `removenick` | `removenick(nick)` | Remove a nick from the channel |

### Query

Represents a private message conversation. Access via `client.queries[irclower(nick)]`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `query.nick` | str | The remote user's nick |
| `query.window` | Querywindow | The query's GUI window |

### User

Tracks a single IRC user across the network. Access via `client.users[irclower(nick)]` or `channel.users[irclower(nick)]`.

Attributes are populated incrementally as data arrives (JOIN, WHOIS, WHO, etc.), so some may be `None`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `user.nick` | str | Current nickname |
| `user.ident` | str or None | Username part of `nick!user@host` |
| `user.host` | str or None | Hostname part of `nick!user@host` |
| `user.realname` | str or None | Real name / GECOS |
| `user.account` | str or None | NickServ/SASL account name |
| `user.server` | str or None | IRC server the user is connected to |
| `user.channels` | set | Set of channel name strings the user is in |
| `user.prefix` | dict | channel_lower -> mode prefix string (`"@"`, `"+"`, etc.) |
| `user.hostmask` | str | Property: `nick!ident@host` (uses `*` for unknowns) |

### History Entries

Channel history (`channel.history`) is a deque of these objects:

**HistoryMessage**

| Attribute | Type | Description |
|-----------|------|-------------|
| `time` | datetime | When the message was received |
| `user` | User or None | The User object (None for server messages) |
| `nick` | str | Nick string |
| `text` | str | Message text |
| `type` | str | `'message'`, `'action'`, `'notice'`, `'join'`, `'part'`, `'quit'`, `'kick'` |

**HistoryModeChange**

| Attribute | Type | Description |
|-----------|------|-------------|
| `time` | datetime | When the mode was changed |
| `user` | User or None | Who set the mode |
| `nick` | str | Nick of who set it |
| `mode` | str | Single mode character (e.g. `'b'`, `'o'`, `'k'`) |
| `added` | bool | `True` for +mode, `False` for -mode |
| `param` | str or None | Associated parameter (nick, hostmask, key, etc.) |

**HistoryTopicChange**

| Attribute | Type | Description |
|-----------|------|-------------|
| `time` | datetime | When the topic was changed |
| `user` | User or None | Who changed it |
| `nick` | str | Nick of who changed it |
| `topic` | str | The new topic text |
