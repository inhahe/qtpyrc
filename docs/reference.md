# qtpyrc Reference

## Command Line

```
python qtpyrc.py [options]
```

| Option | Description |
|--------|-------------|
| `-c`, `--config FILE` | Path to YAML configuration file |
| `-d`, `--debug LEVEL` | Debug output level (0=silent .. 5=trace) |
| `--headless` | Run without GUI (for bots, scripts, headless servers) |
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
| `--profile [PATH]` | Run under `cProfile` and write stats to PATH (default: `qtpyrc.prof` in the config directory) on exit. On exit, the top 30 functions by cumulative time and by total (self) time are printed to stderr. Open the saved file with `python -m pstats <file>` or `snakeviz <file>`. Use this to determine whether slowness is Python-side (per-line work) or Qt-side (little Python time accounted for the wall-clock spent) |
| `--sample-profile [PATH]` | Low-overhead in-process **interaction** profiler. A background daemon thread samples the main (GUI) thread's Python stack ~200x/sec, *and* the QApplication times every event dispatch (`notify()`). Writes Brendan-Gregg *folded* stacks to PATH (default: `qtpyrc.folded` in the config directory) on exit, and prints to stderr: (1) a split of GUI-thread time into **idle / server-driven / UI**, (2) where UI-interaction time went (leaf frames), (3) a worst-first log of **slow interactions** (keypress/click/paint ≥ 30 ms), each correlated with the Python stacks sampled during it, (4) event-dispatch time by event type, and (5) **Qt self-time by (event × widget)** — exclusive time spent inside each widget's event handling, minus nested dispatch. Report (5) is the key one for deciding *toolkit* questions: because the Python sampler can't see into Qt's C++, expensive `QTextEdit`/`QTextDocument` layout+paint is invisible in the folded stacks but shows up here as `Paint`/`LayoutRequest`/`UpdateRequest` self-time on the chat-output widget. This isolates the latency of *your* actions (typing, clicks, scrolling) from background/server work, which is usually what you actually feel. Unlike `--profile` it barely slows the app, and unlike py-spy it needs no cross-process access, so it works reliably on Windows and on brand-new CPython builds. Render the folded file with `flamegraph.pl <file> > out.svg`, or inspect it directly |
| `--timing` | Print a **startup** timing breakdown to the console: how long each startup phase took (Python imports, config load, history DB open, Qt app + main window construction, font validation, clients/notifications, script+plugin loading, window visible, first chat paint), followed by accumulated per-window history-replay cost split into DB query vs. render for channels and queries, slowest first. Startup work is split between synchronous setup and the asynchronous connect/join/replay that follows the window appearing, and this measures both, so use it to find what is actually slow before changing anything. The report prints 15 s after the event loop starts, or on quit, whichever comes first |

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
python qtpyrc.py --profile                     # profile a session; stats printed on exit
python qtpyrc.py --profile run1.prof           # write profile to run1.prof
python qtpyrc.py --sample-profile              # low-overhead sampler; folded stacks on exit
python qtpyrc.py --timing                      # why is startup slow? phase-by-phase breakdown
python qtpyrc.py --sample-profile run1.folded  # write folded stacks to run1.folded
python qtpyrc.py --init                        # create config.yaml in current dir
python qtpyrc.py --init myconfig.yaml          # create myconfig.yaml in current dir
python qtpyrc.py --init path/to/dir/           # create config.yaml in path/to/dir/
python qtpyrc.py --init path/to/myconfig.yaml  # create myconfig.yaml in path/to/
python qtpyrc.py --init newdir/ -o logging.dir=logs -o history_file=history.db
python qtpyrc.py --ui menu.tools.colorpicker     # launch app and open color picker
python qtpyrc.py --ui-list                        # print all /ui paths and exit
```

### Startup command scripts

Four things can ask for a command script to run at startup, and they run in this
order:

1. `--startup FILE`, or failing that the file named by `scripts.startup` in the
   config — unless `--no-startup` is given.
2. Everything matched by `scripts.auto_load`, minus anything matching a
   `--no-scripts` pattern.
3. Everything matched by `--run`.

**A given file runs at most once per startup, however many of them name it.**
Pointing `scripts.startup` at `startup.rc` *and* listing `startup.rc` in
`scripts.auto_load` is a normal thing to write and runs the script once, not
twice. The check is on the file the name resolves to, so the same file named as
`startup`, as `startup.rc` and by an absolute path is still recognised as one
file. The first request to name a file is the one that runs it, so the ordering
above decides where in the sequence it happens.

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
| `/server` | `/server [switches] [host[:[+*]port]]` | Connect to a server (see below for switches) |
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
| `/me` | `/me <action>` | Send a CTCP ACTION (/me) to the current channel or query |
| `/notice` | `/notice <target> <message>` | Send a NOTICE to a user or channel |
| `/ctcp` | `/ctcp <nick> <type> [data]` | Send a CTCP query (PING, VERSION, TIME, etc.) |
| `/dcc` | `/dcc <subcommand> [args]` | DCC file transfer and chat (see below) |
| `/invite` | `/invite <nick> [#channel]` | Invite a user to a channel (defaults to current) |
| `/raw` | `/raw <line>` | Send a raw IRC command to the server |
| `/openurl` | `/openurl <url>` | Open a URL in the system browser |

**Sending a message does the same thing whichever command sends it.** `/msg`,
`/query <nick> <message>`, `/say`, `/amsg` and simply typing in a window all
write the same log line, save the same history row, split messages longer than
the protocol's 512-byte line limit into as many messages as it takes, and
generate link previews. Before 2026-08-31 they did not: `/msg` and `/query`
wrote no log line and no history row, so the half of a conversation you held
through `/msg` was missing when the query window was next opened; `/query
<nick> <message>` did not split, so a long message was truncated by the server;
and only text typed into a window generated a link preview.

`/msg` accepts either a nick or a channel. A message to a channel is shown in
that channel's window and recorded in the channel's log and history — the same
as if you had typed it there — and one to a nick is shown in the query window if
one is open, or as `[-> nick] message` in the current window if not.

`/notice` is partly the exception. It **is** written to the log file — into the
log of the target you sent it to, which is also where an incoming notice *from*
that target is logged, so both halves of a notice conversation end up in one
file. But it is still **shown in the window you typed it in** rather than the
target's window, and it is **not saved to the history database**, so it does not
come back when a window's backlog replays. See `known-issues.md`.

Notices are logged as `-nick- message`, distinct from a message (`<nick> msg`)
and an action (`* nick msg`). An incoming notice is filed under whoever sent it
— the channel for a channel notice, the sender's nick for a private one, and the
server log for a notice from the server itself, which has no user behind it.
| `/clipboard` | `/clipboard <text>` | Copy text to the system clipboard |
| `/quote` | `/quote <line>` | Alias for `/raw` |
| `/echo` | `/echo [-w target] [-s] [-a] <text>` | Print text to current window, target (`-w`), server (`-s`), or active (`-a`) window |
| `/log` | `/log [-w target] "text"` | Write a line to the log file for the current window (or target) |
| `/alert` | `/alert [-t "title"] "message"` | Show a popup message box (default title: "qtpyrc") |
| `/notif` | `/notif [-t "title"] <body>` | Show a desktop notification (pinned to Action Center on Windows) |
| `/stdout` | `/stdout <text>` | Write text to stdout |
| `/stderr` | `/stderr <text>` | Write text to stderr |

### Channel Moderation

| Command | Syntax | Description |
|---------|--------|-------------|
| `/kick` | `/kick <nick> [reason]` | Kick a user from the current channel |
| `/ban` | `/ban <nick\|mask>` | Ban a user (nicks are expanded to `nick!*@*`) |
| `/kban` | `/kban <nick> [reason]` | Ban and kick a user in one command |
| `/chaninfo` | `/chaninfo` | Show channel details dialog (modes, bans, topic) |
| `/list` | `/list [params]` | Open the channel list browser. See ELIST params below. |
| `/op` | `/op <nick>` | Give operator status (+o) |
| `/deop` | `/deop <nick>` | Remove operator status (-o) |
| `/halfop` | `/halfop <nick>` | Give halfop status (+h) |
| `/dehalfop` | `/dehalfop <nick>` | Remove halfop status (-h) |
| `/voice` | `/voice <nick>` | Give voice (+v) |
| `/devoice` | `/devoice <nick>` | Remove voice (-v) |
| `/quiet` | `/quiet <nick>` | Quiet a user (+q) |
| `/unquiet` | `/unquiet <nick>` | Remove quiet (-q) |

#### /list ELIST Parameters

`/list` with no parameters opens the browser without fetching. Any parameters are passed to the server as ELIST filters (server-side filtering, reduces load). Multiple parameters can be combined.

| Parameter | Meaning | Example |
|-----------|---------|---------|
| `>N` | Channels with **more than** N users | `/list >50` — busy channels only |
| `<N` | Channels with **fewer than** N users | `/list <20` — small channels |
| `*mask*` | Channel name matches wildcard | `/list *python*` — channels with "python" in the name |
| `C>N` | Channel **created more than** N minutes ago | `/list C>1440` — channels older than 1 day |
| `C<N` | Channel **created less than** N minutes ago | `/list C<60` — channels created in the last hour |
| `T>N` | **Topic changed more than** N minutes ago | `/list T>10080` — stale topics (>1 week) |
| `T<N` | **Topic changed less than** N minutes ago | `/list T<60` — recently active topics |

Combined example: `/list >10 <500 *chat*` — channels with 10-500 users and "chat" in the name.

Not all servers support all ELIST parameters. The `>N` filter is the most widely supported. The browser also has a client-side text filter for searching results after they arrive.

### User Info

| Command | Syntax | Description |
|---------|--------|-------------|
| `/whois` | `/whois <nick>` | Query information about a user |
| `/who` | `/who [channel\|mask]` | Show WHO list. Defaults to current channel if omitted |
| `/ping` | `/ping <nick>` | CTCP PING a user (measures round-trip time) |

### Ignore & Auto-Op

| Command | Syntax | Description |
|---------|--------|-------------|
| `/ignore` | `/ignore [-lrw] [nick\|mask] [#channel] [network]` | Add/remove/list ignore masks |
| `/aop` | `/aop [-lrw] <nick\|mask> [#chan1,#chan2] [network]` | Auto-op matching users when they join |

#### /ignore and /aop details

Both commands take a **nick or a hostmask** as the target. `/aop` gives `+o` automatically to anyone matching an entry when they join a channel the entry applies to.

**How a mask matches.** A hostmask is `nick!ident@host`, and `*` and `?` are the only wildcards (`?` = one character, `*` = any run of characters). Nothing else is special — brackets and braces are ordinary characters, so `bob[away]!*@*` means the nick `bob[away]` and nothing else.

**Any component you leave out means "anything".** Write as much of the mask as you want to pin down and the rest is filled in with `*`:

| You write | It means | It matches |
|---|---|---|
| `hegemon@lakitu.example.org` | `hegemon!*@lakitu.example.org` | that nick, from that host only |
| `hegemon!~heg` | `hegemon!~heg@*` | that nick with that ident, from anywhere |
| `@lakitu.example.org` | `*!*@lakitu.example.org` | **anyone** on that host |
| `*@lakitu.example.org` | `*!*@lakitu.example.org` | **anyone** on that host |

**`something@host` is read as *nick*@host, not *ident*@host.** This is the one
place the short form is genuinely ambiguous on sight, because the same text in a
`/whois` line (`hegemon is ~heg@lakitu.example.org`) is the *ident*. qtpyrc reads
the part left of `@` as the nick, so `hegemon_@1.2.3.4` matches the nick
`hegemon_` on that host **whatever its ident is**, and does *not* match someone
whose ident happens to be `hegemon_`. That keeps the leftmost component meaning
the same thing whether or not a `!` is present. To pin the ident, write it:
`hegemon_!~heg@1.2.3.4`. Adding an entry echoes what it expands to, so you can
see which reading you got at the moment you add it.

**A bare nick is a different kind of entry, and it is much broader than it looks.** A nick with no `!` and no `@` (e.g. `spammer`) matches on nickname alone, from any host — exactly, or as a wildcard pattern if it contains `*` or `?` (`bob*` matches `bob`, `bob123`, …). Since a nick is released when its owner quits and is free for the taking during a netsplit, a bare-nick **auto-op** means "op whoever holds this name", so `/aop` warns when you add one and suggests anchoring it: `/aop hegemon!*@lakitu.example.org`. For `/ignore` a bare nick is perfectly ordinary and is not warned about.

**Flags** (shared by `/ignore`, `/aop`, `/highlight` and `/notify`):

- **`-l`** — List current entries. **Always lists every entry at every scope — global, network and channel — regardless of which window you run it from and regardless of any other flag**, and labels each one with the scope it lives at. Where an entry's written form differs from what it actually matches, the expansion is shown beside it. `-l` is also the default when you give no target.
- **`-r`** — Remove an entry instead of adding it. E.g. `/aop -r trusted!*@*`. See "Removing" below.
- **`-w`** — Operate at the global (top-level) scope, so the entry applies on every network. Without `-w`, entries are scoped to the current network. **`-w` does not widen a listing** — it never did; it used to *narrow* `-l` to the global scope alone, which is how `/aop -lw` could report an empty list while entries were live. In list mode it now does nothing and says so.

Anything else beginning with `-` is **rejected as an unknown option** rather than being taken as a mask. To pass a value that genuinely begins with `-`, put `--` before it: `/aop -- -weirdnick`.

**Scopes** (broadest to narrowest):

- **Global** — pass `-w`. Applies on all networks.
- **Network** (default) — applies on the current network. To target a different network, pass its network key as the last argument.
- **Channel** — pass one or more channels (comma-separated, **no spaces**) to scope the entry to just those channels, e.g. `/aop trusted!*@* #chan1,#chan2`. If you give no channel while in a channel window, the current channel is used.

Lists are **additive**: channel-level entries add to network-level, which add to global. A user matching at any level is ignored / auto-opped. This is why `-l` never narrows, by window or by flag — an entry at *any* scope can act, so a list that showed only some scopes could report "empty" while entries were live.

**Removing.** `-r` with **no** scope given (no `-w`, no channel, no network key) removes the mask from **every** scope it appears at, and names each one it removed from. That is the safe default: leaving a copy behind at a scope you weren't looking at is how a stale auto-op entry keeps opping someone after you believe you've removed it.

`-r` **with** an explicit scope touches only that scope — and then tells you every other scope where the mask is still live, with the command to remove it there. Either way, if nothing matched, it says `nothing removed` rather than reporting a success.

Adding reports whether the entry was newly added or was already present at that scope. `/aop` additionally prints a **warning** when the mask is broader than it looks: if it would match every user (`*`, `*!*@*`, and the like), or if it is a bare nick with no host. An ordinary broad-in-the-host mask such as `bob!*@*` is not warned about.

Examples:

```
/ignore spammer!*@*              Ignore on the current network
/ignore -w spammer!*@*           Ignore globally (all networks)
/ignore spammer #channel         Ignore only in #channel
/ignore -r spammer               Remove ignore from every scope it's in
/ignore -w -r spammer            Remove only the global entry
/ignore -l                       List all ignores, every scope
/aop trusted!*@host.example      Auto-op that nick from that host only
/aop trusted@host.example        The same thing -- the ident is filled in
/aop trusted                     Auto-op WHOEVER holds the nick (warns)
/aop trusted!*@* #chan1,#chan2   Auto-op a hostmask in specific channels
/aop -r trusted!*@*              Remove an auto-op entry from every scope
/aop -l                          List all auto-op entries, every scope
```

### Highlights

| Command | Syntax | Description |
|---------|--------|-------------|
| `/highlight` | `/highlight [-lrw] [pattern]` | Add/remove/list custom highlight patterns |

#### /highlight details

**Flags:** same as `/ignore` — `-l` list, `-r` remove, `-w` global scope (no `-w` = current network). `-l` lists every pattern at every scope, labelled, regardless of the window or any other flag (`-w` does not narrow it); `-r` without `-w` removes the pattern from every scope it appears at and names each; an unknown `-x` option is an error, and `--` ends the options.

**Patterns:** plain strings are case-insensitive substring matches. Use `/regex/` for a regex with optional trailing flags: `i` (case-insensitive), `m` (multiline — `^`/`$` match line boundaries), `s` (dotall — `.` matches newlines). Example: `/regex/i`, `/regex/ims`. Use `{nick}` to refer to your current nickname (escaped properly inside the regex). The default config includes `{nick}`; removing it disables nick-mention highlighting. Unknown `{name}` references produce a one-time warning. Use `\{` and `\}` for literal braces, `\\` for a literal backslash (so a literal `\{` requires `\\{`). Regex quantifiers like `{3}` and `{1,5}` are unaffected.

Highlights are **additive** (global + network + channel). Set `highlights: false` at the channel level in config to disable all highlights. Set `highlight_notify: false` to suppress beep/desktop notifications while still coloring highlights.

### Notify

| Command | Syntax | Description |
|---------|--------|-------------|
| `/notify` | `/notify [-lrw] [nick]` | Add/remove/list nicks on the watch list |

#### /notify details

**Flags:**

- **`-l`** — List notify nicks with their online/offline status. Lists every scope, labelled, regardless of the current window and of any other flag.
- **`-r`** — Remove a nick from the list. Without `-w`, removes it from every scope it appears at and names each; reports `nothing removed` if it was nowhere.
- **`-w`** — Operate on the global list instead of the current network's list. It does not narrow `-l`.

An unknown `-x` option is rejected rather than treated as a nick; `--` ends the options.

Nicks are checked via server-side MONITOR when supported (instant push notifications), falling back to periodic ISON polling. When a watched nick signs on or off, a notification is shown in the server window (and optionally a sound/desktop alert per config). Use `/on notify_online` and `/on notify_offline` for custom per-nick actions.

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
| `/plugin` | `/plugin <name>` | Load a Python plugin |
| `/plugin` | `/plugin -u <name>` | Unload a plugin |
| `/plugin` | `/plugin -r <name>` | Reload a plugin (unload + load) |
| `/load` | `/load <name>` | Alias for `/plugin` |
| `/unload` | `/unload <name>` | Alias for `/plugin -u` |
| `/plugins` | `/plugins [-l or -a]` | List available plugins, grouped by the directory each was found in (`-l` loaded only, `-a` auto-load only) |
| `/scripts` | `/scripts [-a]` | List available command scripts (`-a` auto-load) |
| `/script` | `/script <filename>` | Run a command script (text file of /commands) |
| `/play` | `/play <filename>` | Send a plain text file to the current window line by line |
| `/alias` | `/alias [name] [command...]` | Define, show, or list command aliases |
| `/alias` | `/alias -r <name>` | Remove an alias |
| `/hotkeys` | `/hotkeys` | List hotkeys bound by plugins (`irc.bind_key`), with their descriptions and owning plugins |
| `/keys` | `/keys` | Alias for `/hotkeys` |
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
| `{replay}` | History replay progress (e.g. ` [history: 12/85]`), empty when not loading. Main titlebar only. |

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
| `/playsound` | `/playsound <name\|path>` | Play a sound by name or arbitrary file path |
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
| `/find` | `/find [text]` | Open the search bar, optionally searching for text. Searching upward (Previous) automatically pulls in older saved history that hasn't been rendered yet, so Find reaches the whole replayable backlog, not just the lines currently on screen |
| `/title` | `/title [text]` | Set a custom window title format (no args to restore default). `-s` targets server window, `-a` targets app titlebar |
| `/tabbed` | `/tabbed` | Switch to tabbed view mode |
| `/mdi` | `/mdi` | Switch to MDI (multi-document) view mode |
| `/tile` | `/tile [v]` | Tile windows horizontally, or vertically with `v`. Skipped windows are left out |
| `/cascade` | `/cascade` | Cascade windows (MDI mode) |
| `/newserver` | `/newserver [args...]` | Alias for `/server -m` |

The workspace shows its windows either one at a time (the tabbed look) or
tiled/cascaded, and the tab bar means the same thing in both:

- **Clicking the active window's tab skips it** — the window leaves the screen
  and the next unskipped one takes its place. When the last one is skipped the
  workspace is empty, in the tab bar's own colour. Clicking a skipped tab brings
  its window back. `Ctrl+Tab` passes over skipped windows.
- **A tiled window's own minimize button does the same thing** as clicking its
  tab, rather than leaving an icon in the corner of the workspace.
- **Maximizing a tiled window returns to the tabbed look**, which is what the
  Window menu's Maximize does; a maximized window fills the workspace either way.

### /server switches

| Switch | Description |
|--------|-------------|
| (no args) | Reconnect to the last server used |
| `<network>` | Connect to a network defined in config |
| `-m` | Create a new server window and connect |
| `-n` | Create a new server window without connecting |
| `-z` | Don't activate the new window (with `-m`/`-n`) |
| `-e` / `+port` | Use TLS (e.g. `/server host:+6697`) |
| `-t` / `*port` | Use STARTTLS (e.g. `/server host:*6667`) |
| `-4` `-6` `-46` | Force IPv4, IPv6, or both |
| `-d` | Set connection details without connecting |
| `-o` | Skip autojoining channels |
| `-c` | Skip on-connect events/notifications |
| `-u` | Bypass STS (Strict Transport Security) |
| `-w <pass>` | Server password |
| `-l <method> [pass]` | Login method: `sasl`, `external`, `msg`, `nickserv` |
| `-nick <nick>` | Override nickname |
| `-altnick <nick>` | Add alternate nick (repeatable) |
| `-user <user>` | Override username |
| `-realname <name>` | Override realname (use quotes for spaces) |

Examples:
```
/server irc.libera.chat:+6697
/server -e -l sasl mypassword irc.libera.chat:6697
/server -m -nick botnick -w serverpass irc.rizon.net:6667
/server Libera
/server
```

### /dcc subcommands

| Subcommand | Description |
|------------|-------------|
| `/dcc send <nick> [filepath]` | Send a file (opens file picker if no path) |
| `/dcc chat <nick>` | Open a DCC chat session |
| `/dcc get <id\|nick>` | Accept a pending file transfer |
| `/dcc cancel <id>` | Cancel a transfer |
| `/dcc close <id>` | Close a transfer or chat |
| `/dcc list` | Show the DCC transfers window |

DCC supports both normal and reverse (passive) mode. When behind NAT, enable
passive DCC in config or qtpyrc will automatically attempt UPnP port forwarding.

`dcc.trusted_hosts` is a list of hostmasks whose transfers skip the get dialog —
an entry there is a standing "yes" to a file from whoever matches it. The masks
work exactly like `/ignore` and `/aop` masks (see
"[/ignore and /aop details](#ignore-and-aop-details)"): `*` and `?` are the only
wildcards, and a component you leave out means "anything", so
`friend@trusted.host` is read as `friend!*@trusted.host`. Always name a host —
a bare nick trusts *whoever* holds that nick, and a nick is released on quit and
free for the taking during a netsplit.

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

## Find in all windows

`Ctrl+Shift+F` (or **Tools → Find in all windows**) opens a dockable panel that searches across every open window — server, channel, and query — and, optionally, the SQLite history database.

- Type a query and press **Enter** (or click **Find**).
- **Case** — case-sensitive matching.
- **Regex** — interpret the query as a regular expression. The doc search uses Qt's `QRegularExpression`; the history-DB search uses Python `re`.
- **History DB** — also search the persistent message history. Useful for finding lines that have scrolled out of the live buffer or are from past sessions. Results from the history DB are tagged `(history)`; if no live window is open for that channel, the entry is marked `[no open window]`.

Results group by window. Double-click a match to activate the target window and highlight the matching line. For history-DB matches, qtpyrc tries to find the corresponding line in the live buffer; if it isn't there (older than the buffer), only the window is activated.

The dock can be dragged to the top or bottom of the main window, or floated as a separate window.

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
| `-x` | Suppress the default handler (event won't appear in window) |
| `-N` | Suppress notifications (sound/desktop/highlight popups) for this event, but still show it |
| `-A` | Suppress tab activity coloring for this event, but still show it |

`-N` and `-A` are independent and can be combined (e.g. `-N -A` to show the event silently with no tab coloring). `-x` already implies both, since the default handler never runs.

- **pattern** — matched against the event's primary text. Supports wildcards (`*`, `?`) or `/regex/` with optional flags: `i` (case-insensitive), `m` (multiline), `s` (dotall). Example: `/regex/i`, `/regex/ims`. Default `*` (match everything).
- **command** — a command string to execute. Optional if action flags (`-s`, `-d`, `-h`) are used. Multiple commands can be separated with ` | ` (space-pipe-space), e.g. `/mode # +b nick!*@* | /kick # nick`. `{variables}` are expanded before execution (`\{` / `\}` for literal braces, `\\` for literal backslash, so a literal `\{` requires `\\{`). If the command starts with `/exec`, variables are available as Python names instead (see below).

### Examples

```
/on chanmsg friend -n friend -s beep -d *
/on chanmsg important -c #important -s beep -d *
/on chanmsg deploy -n ops_bot *deploy* /echo Deploy detected!
/on chanmsg errors /error\s*\d{3}/i -s beep -d
/on chanmsg vip -n boss -s sounds/vip.wav -d *
/on kick kick_alert -s beep -d *
/on mode ban_alert *+b* -s beep -d /echo Ban: {modes} {args}
/on join hide_bots -x -n *bot* *
/on chanmsg kban_spam -n spammer * /mode # +b {nick}!*@* | /kick # {nick} Spam
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
| `kicked` | You are kicked from a channel | — |
| `myjoined` | You join a channel | — |
| `myleft` | You leave a channel | — |
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
| `myjoined` | `{channel}` |
| `myleft` | `{channel}` |
| `kicked` | `{channel}` `{kicker}` `{nick}` `{message}` |
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
| `on` | `on(event, name, pattern, command, *, channel=None, network=None, window=None)` | Register an event hook. `command` can be a string or callable `(vars, conn)` |
| `remove_on` | `remove_on(event, name)` | Remove a hook by event and name |
| `remove_all_hooks` | `remove_all_hooks()` | Remove all hooks registered through this instance |

### Timer Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `timer` | `timer(name, reps, secs, command, *, window=None)` | Create a named timer (0 reps = infinite) |
| `cancel_timer` | `cancel_timer(name)` | Stop and remove a timer |

### Slash Command Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `add_command` | `add_command(name, func, help='')` | Register `/name`. `func(window, text)`. Returns the normalised name |
| `remove_command` | `remove_command(name)` | Unregister a command added with `add_command` |

`name` is written without the command prefix (`'np'`, not `'/np'`) and is
matched case-insensitively, like every built-in. `text` arrives as a
`TokenizedString` with `{variable}` expansion already applied, exactly as a
built-in receives it.

The lookup order is **built-in, then plugin command, then `/alias`**.
`add_command` raises `ValueError` if `name` is already a built-in — a plugin
cannot override one, because registering it and having the built-in win anyway
is a registration that silently never fires. `/alias` warns when it shadows
either. Registering the same name twice from a plugin *is* allowed and replaces
the previous handler, so a reload needs no special case.

An exception raised by a plugin command is reported in the window as
`[/name failed: Type: message]`, with the traceback on the console.

### Hotkey Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `bind_key` | `bind_key(sequence, func, description='')` | Bind an application-wide hotkey. `func()` takes no arguments. Returns the canonical sequence |
| `unbind_key` | `unbind_key(sequence)` | Remove a binding. Returns True if there was one |

`sequence` is anything `QKeySequence` understands: `'F12'`, `'Ctrl+Shift+P'`,
`'Alt+N'`. The binding is application-scoped, so it fires from any window and
while the input box has focus. `bind_key` raises `ValueError` if Qt cannot
parse the sequence, rather than installing a shortcut that can never fire.

Bindings are keyed by the *canonical* form, so `'f12'`, `'F12'` and `'  F12  '`
are one binding rather than three, and re-binding replaces. `/hotkeys` lists
everything currently bound.

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
| `dbg` | `dbg(level, *args)` | Write to console debug log. Levels: `irc.LOG_ERROR` (1), `irc.LOG_WARN` (2), `irc.LOG_INFO` (3), `irc.LOG_DEBUG` (4), `irc.LOG_TRACE` (5) |

### Plugin Config Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_config` | `get_config(plugin_name, key, default=None)` | Get a plugin config value |
| `set_config` | `set_config(plugin_name, key, value)` | Set a plugin config value and save |

Plugins can declare config fields that appear in Settings > Plugin Config:

```python
class MyPlugin(plugin.Callbacks):
    config_fields = [
        ('enabled', bool, True, 'Enable this plugin'),
        ('interval', int, 60, 'Check interval in seconds'),
        ('api_key', str, '', 'API key for the service'),
    ]
```

Supported types: `str`, `int`, `float`, `bool`. Values are stored under
`plugins.<name>:` in the config YAML. A fifth element may be a list of choices,
which is rendered as a combo box.

**Reading a setting when you use it needs nothing else** — `get_config` reads
the current config every time it is called, so it already reflects a settings
change or a Reload Configuration. Override `config_changed(self, irc)` only for
settings that *cannot* be read at the point of use because they were handed to
something else at registration time — a hotkey bound with `bind_key`, or a name
given to `add_command`. Without it, changing either in the settings dialog
appears to work and does nothing.

| Callback | Signature | When |
|----------|-----------|------|
| `config_changed` | `config_changed(self, irc)` | After the settings dialog applies, and after Reload Configuration |

### Where plugins are found

Plugins are looked up on a **search path**, highest priority first:

1. the profile's plugin directory — `plugins.dir` (default `plugins`), resolved
   relative to the config file, so `me/config.yaml` means `me/plugins/`;
2. the `plugins/` directory inside the qtpyrc installation, where the plugins
   that ship with qtpyrc live.

A name matches `<name>.py` first, then `<name>/__init__.py`, in each directory
in turn. Everything that takes a plugin name searches the same path in the same
order: `plugins.auto_load` (including its wildcards, which expand across both
directories), `/plugin`, `--plugin`, and the plugin list in
**Settings > Plugins**.

Two consequences worth knowing:

- **A profile does not need a copy of the shipped plugins.** They are always
  available. Creating a profile no longer copies them in, and if an older
  profile has copies you can delete them — they are forks that receive no
  updates, and the shipped ones will be used instead.
- **A file in your own directory overrides a shipped plugin of the same name.**
  That is how you customise one deliberately. Because it is invisible in the
  name alone, both `/plugins` and the Plugins settings page name the file each
  plugin was found in, and flag any shipped plugin that is being shadowed.

An `auto_load` entry containing a path separator is used as a path rather than a
name, so a plugin can also be loaded from anywhere on disk.

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

For a full working example, see the **triviabot** plugin in `plugins/triviabot/__init__.py`. It demonstrates channel message handling, config fields, timers, fuzzy matching, mIRC colors, and per-channel allow/block lists.

### nowplaying — announce what foobar2000 is playing

`plugins/nowplaying.py`. Load it with `/plugin nowplaying`, or add `nowplaying`
to `plugins.auto_load` to have it load at startup. Configure it in
**Settings > Plugin Config > nowplaying**.

| Trigger | Effect |
|---------|--------|
| `F12` | Announce the current track in the focused channel or query |
| `/np` | The same thing as a command |
| `/np <spec>` | Announce using a one-off foobar2000 title-format spec |
| `/np -l` | Show it to yourself only, without sending it |
| `/np -probe` | Diagnose the connection to foobar2000 |

| Setting | Default | Meaning |
|---------|---------|---------|
| `hotkey` | `F12` | Key that announces. Blank disables it |
| `command` | `np` | Slash command name, without the prefix. Blank disables it |
| `format` | see below | foobar2000 title-format spec |
| `template` | `np: {title}` | The line that gets sent. Placeholders: `{title}` `{elapsed}` `{length}` `{time}` `{state}` |
| `action` | off | Send as an action (`/me`) instead of a message |
| `source` | `auto` | How to reach foobar2000: `auto`, `beefweb` or `com` |
| `beefweb_url` | `http://localhost:8880` | Base URL of the foo_beefweb component |
| `progid` | `Foobar2000.Application.0.7` | The COM ProgID foo_comserver2 publishes |

`{time}` is `elapsed/length` and is empty when either is unknown; `{state}` is
` (paused)` or empty. An unknown placeholder is left as written rather than
raising, so a typo in the settings box produces a visibly odd line rather than
a hotkey press that does nothing.

#### The `format` setting

`format` is a foobar2000 title-format spec. It is not interpreted by qtpyrc at
all — it is handed to foobar2000, which evaluates it and returns the result — so
every field and every `$function()` foobar2000 supports is available, including
multi-argument ones such as `$if(%artist%,%artist%,unknown)`. Whatever it
produces arrives as `{title}` in `template`.

The default announces `Artist - Title [320kbps mp3]`, and every part of it is
conditional:

| Situation | Announced |
|-----------|-----------|
| Ordinary tagged MP3 | `Artist - Title [320kbps mp3]` |
| Lossless (FLAC, WAV, ALAC `.m4a`, WMA Lossless) | `Artist - Title [flac]` — no bitrate, which is meaningless for lossless |
| No artist tag | `Title [128kbps mp3]` — the artist and its separator drop out together |
| Bitrate unknown to foobar2000 (raw `.aac`, `.webm`) | `Artist - Title [aac]` |
| Internet radio | `DI.FM - Progressive` — no brackets at all |

Lossless is detected with `%__bitspersample%` rather than the file extension,
because `.m4a` and `.wma` are containers that hold either lossy or lossless
audio, so the extension cannot tell you which you have.

Two traps if you write your own spec:

- **Square brackets are foobar2000's conditional syntax, not literals.** To
  print an actual `[`, single-quote it: `'['`. An unquoted one is silently
  swallowed along with its contents.
- **A field that is absent renders as a literal `?`** (`%genre%` on an untagged
  track). Guard it with `$if(%genre%,...)` or `$if2(%genre%,fallback)`.

Avoid the colour functions (`$rgb`, `$blend`): they emit a raw `0x03` byte,
which is also the mIRC colour code, so the line arrives garbled.

#### Which source to use

**Install [foo_beefweb](https://www.foobar2000.org/components/view/foo_beefweb)
and leave `source` at `auto`.** That is the answer for every current
foobar2000.

| Source | Component | Works with | Needs |
|--------|-----------|-----------|-------|
| `beefweb` | [foo_beefweb](https://www.foobar2000.org/components/view/foo_beefweb) | foobar2000 v1.6+, **32- and 64-bit**, and remote machines | nothing — stdlib HTTP |
| `com` | foo_comserver2 | 32-bit foobar2000 v1.x only | `pywin32` or `comtypes` |

`auto` tries beefweb first and falls back to COM, so whichever component you
have installed simply works.

**foo_comserver2 cannot work with a 64-bit foobar2000.** It is a 32-bit-only
build of a component last released for foobar2000 0.9 — foobar2000's own
component troubleshooter lists it for repeated crash reports — and a 32-bit DLL
cannot be loaded into a 64-bit process at all. foobar2000 has been 64-bit by
default since v2.0, so on any normal current install this source is unavailable
no matter how it is configured. It is kept only for genuinely old 32-bit v1.x
setups, where it is the only option.

beefweb also needs no Python dependency whatsoever, and because it speaks HTTP
it works when foobar2000 runs on a different machine — point `beefweb_url` at
it (and enable remote connections in the component's settings).

#### Behaviour

The plugin never launches foobar2000. On the COM path it attaches to a running
instance with `GetActiveObject` rather than `Dispatch`, so pressing the hotkey
with no player running reports that instead of starting one. Nothing is
announced unless the player is actually playing; a stopped player with a track
still loaded reports "not playing". All errors are shown locally and never
sent to the channel.

An unrecognised `source` is refused and named, rather than quietly treated as
`auto` — otherwise a typo would leave you testing something other than what you
configured.

If nothing happens, run `/np -probe`. It reports on **every** source under its
own heading — whether beefweb is reachable and what it says, and separately
whether a COM library is installed, whether the ProgID is registered, and
whether a running instance was found — because "nothing happened" has several
different causes, and naming only one sends people to fix the wrong thing.

The query runs on a worker thread, whichever source is used, because it blocks
until the other program answers and foobar2000 can be busy, minimised,
rescanning its library, or showing a modal dialog. One query is allowed in
flight at a time.

### TokenizedString — Pre-parsed Message Parameters

Message strings passed to plugin hooks (`chanmsg`, `privmsg`, `noticed`, `action`) and command handler `text` arguments are `TokenizedString` instances — a `str` subclass with a `.tokens` property that provides quote-aware tokenization.

```python
def chanmsg(self, irc, conn, user, channel, message):
    # message is a regular string: "!play \"song name\" loud"
    # message.tokens is a parsed list: ['!play', 'song name', 'loud']
    tokens = message.tokens
    if tokens and tokens[0] == '!play':
        song = tokens[1] if len(tokens) > 1 else ''
```

Tokenization rules:
- Whitespace separates tokens
- `"quoted strings"` and `'single quoted'` are single tokens with quotes stripped
- `\"` inside quotes produces a literal quote
- `\\` produces a literal backslash
- `.tokens` is lazy — only parsed on first access

This also works with `argparse`:

```python
import argparse
parser = argparse.ArgumentParser(prog='!cmd', exit_on_error=False)
parser.add_argument('target')
parser.add_argument('-v', '--verbose', action='store_true')
try:
    args = parser.parse_args(message.tokens[1:])
except (SystemExit, argparse.ArgumentError):
    return
```

### `irc.on()` — Registering Hooks from Plugins

```python
irc.on(event, name, pattern, command='', *, channel=None, network=None,
       nick_mask=None, sound=None, desktop=False, highlight_tab=False,
       suppress=False, suppress_notify=False, suppress_activity=False,
       window=None)
```

The `command` argument can be:

- A **string** — executed as a slash command. Multiple commands separated by ` | `:
  ```python
  irc.on('chanmsg', 'kban', '*', '/mode # +b {nick}!*@* | /kick # {nick}',
         nick_mask='spammer')
  ```

- A **callable** — receives `(variables_dict, conn)`. Return truthy to suppress the event:
  ```python
  def my_filter(variables, conn):
      if variables['nick'] == 'annoying':
          return True  # suppress
  irc.on('chanmsg', 'filter', '*', my_filter)
  ```

- A **list** of callables and/or strings — executed in order. Any callable returning truthy suppresses:
  ```python
  def log_it(variables, conn):
      print('Join:', variables['nick'])

  irc.on('join', 'multi', '*', [
      log_it,
      '/echo {nick} joined {channel}',
  ])
  ```

Set `suppress=True` to always suppress the event when the hook matches (regardless of command return value). Use `suppress_notify=True` to skip only notifications (sound/desktop/highlight popups) while still showing the event, or `suppress_activity=True` to skip only the tab activity coloring.

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

## Freeze Detection (hang watchdog)

qtpyrc runs its network code on the same thread that draws the window, so
anything slow on that thread freezes the whole UI: keystrokes are ignored and
the window won't repaint or restore from minimised. Windows only shows its grey
"not responding" overlay after about 5 seconds, so shorter freezes normally
leave no trace.

The hang watchdog catches them. A background thread (independent of the frozen
event loop) watches a heartbeat; if it goes stale, the watchdog records **the
Python stack the GUI thread was stuck in** — which is what makes an
intermittent freeze diagnosable after the fact.

Reports go to `hangs.log` next to your config file, and are echoed to the
console. A report looks like:

```
[2026-08-16 03:49:26.355] *** GUI STALL detected: no heartbeat for 1.16s (threshold 1.00s) ***
  GUI thread stack at stall:
    File "...", line 20, in <module>
      app.exec()
    File "...", line 16, in the_blocking_function
      time.sleep(3.5)
  Other threads:
    ...
[2026-08-16 03:49:28.765] *** GUI recovered after 3.52s ***
```

Read it bottom-up: the deepest frame is the call that was blocking. A freeze
lasting longer than 5s is re-sampled, so you also get a second stack — if it
matches the first, the thread is stuck on one call; if it keeps moving, it's
slow-but-progressing work (e.g. rendering a huge backlog). The "Other threads"
section matters when the GUI thread is blocked waiting on another thread (a
lock, a queue, a database handle).

The watchdog starts as soon as the Qt application exists, which is before the
event loop is entered — so the tail end of startup is watched too. A report from
that window carries the note *"heartbeat has not fired since the watchdog
started"*, meaning the GUI thread was busy finishing startup rather than frozen
mid-session.

Configured under `logging.hang_watchdog` (also in Settings → Logging):

| Option | Default | Meaning |
|--------|---------|---------|
| `enabled` | `true` | Turn freeze detection on/off |
| `threshold` | `2.0` | Seconds unresponsive before it counts as a freeze |
| `file` | `hangs.log` | Report file, relative to the config file |
| `native_stacks` | `true` | Use py-spy for the freezes Python can't explain (below) |

### Freezes with no Python stack

Most freezes turn out to have *no* Python frame below the event loop: the
deepest frame is qasync's `run_forever()`, i.e. `QApplication::exec()`. That
means Qt was busy inside its own C++ code and never called into qtpyrc at all,
so there is no Python frame that could name the culprit and the report can only
say "the interface is busy". 239 of the first 315 recorded freezes looked like
that.

For those — and only those — the watchdog runs
[py-spy](https://github.com/benfred/py-spy) (`py-spy dump --native`) against
qtpyrc's own process and records the **native** stack, which names the actual
Qt/Windows call:

```
  (no Python frame below the event loop -- the GUI thread is inside Qt/Win32. Sampling native stack with py-spy...)
  GUI thread native stack (py-spy, 1.42s -- this process was suspended for that long, so it is part of the stall above):
  Thread 40368 (active)
      NtGdiGetGlyphIndicesW (win32u.dll)
      QWindowsFontDatabase::populateFamily (PySide6\Qt6Gui.dll)
      ...
```

Install it with `pip install py-spy`; without it the report says so once and
carries on as before. Reading a native stack suspends qtpyrc for a second or
two, so the line says how long that took — that time is part of the freeze
duration reported above it, not on top of it.

## Duplicate-message detection (render audit)

A chat line can reach the screen by several different routes: as it arrives,
from the saved history that is replayed when a window opens, from the queue that
holds live messages back while that replay is still running, and from the older
history that loads when you scroll up. If two of those ever cover the same
message, you see it twice.

That kind of duplicate is invisible everywhere except on screen — only one of
the two copies is a real incoming message, so the log files and the history
database each contain exactly one. There is nothing to go on after the fact.

The render audit fixes that. It watches every line drawn into a chat view, and
when the same line is drawn into the same window twice within the look-back
window it writes a report naming **where in qtpyrc each of the two copies came
from**:

```
[2026-08-26 12:54:31.526] *** DUPLICATE RENDER #2 in #channel (Channelwindow) -- 0.312s apart ***
  method: addline_msg
  text:   'inhahe the message in question'
  first  render (timestamp_override=None):
      commands.py:109 say
      commands.py:2954 docommand
      window.py:1712 lineinput
  second render (timestamp_override='11:02'):
      irc_client.py:159 _render_history_row
      irc_client.py:211 render_history_rows
      qtpyrc.py:638 _bg_replay_loop
```

Read each stack top-down: the first line is the call that drew the text, and the
ones below it say who asked for it. The pair above says the message was drawn
once by the user typing it and once by the background history replay — which
names the bug precisely.

Two copies of a line are matched on their **text only**. The timestamp is
ignored (the live copy is stamped from the server's time, the replayed copy from
the stored row, which is often how you notice the duplicate in the first place),
as are colours and the `@`/`+` mode prefix. A line that was merely *queued* by
the hold-back mechanism is not counted, so an ordinary held-back message is
never reported against its own flush.

Reports go to `renders.log` next to your config file, and are echoed to the
console. Configured under `logging.render_audit` (also in Settings → Logging):

| Option | Default | Meaning |
|--------|---------|---------|
| `enabled` | `true` | Turn duplicate detection on/off |
| `window` | `120` | Seconds two identical lines may be apart and still count |
| `file` | `renders.log` | Report file, relative to the config file |

Raise `window` if you only notice doubles after scrolling back; lower it if
ordinary repetition (the same join/part, the same short reply) is being
reported. The cost is one dictionary lookup per line drawn, so leaving it on is
cheap.

## Missing fonts

If the font in your config isn't installed, qtpyrc does **not** stop and ask
what to do — it starts with a substitute (Consolas, DejaVu Sans Mono, Liberation
Mono or Courier New, whichever exists) and then offers a font picker in a
non-modal window, along with a message in your open windows saying which font
was substituted. Closing the picker keeps the substitute; you can change it any
time in Settings → Font.

A font you never chose — one still set to the value shipped in
`config.defaults.yaml` — is substituted silently, with only a line in the debug
log. Only a font you actually picked yourself is worth interrupting you about.

### Characters your chat font doesn't have

Chat text is drawn in your chosen font alone. When a message contains a
character that font has no glyph for, Qt borrows one from another installed
font; qtpyrc steers that choice towards **Segoe UI** (for typographic
punctuation — curly quotes, em dashes, ellipses) and then **Segoe UI Symbol**
(for monochrome dingbats and the BMP symbol ranges), so symbols don't come out
as oversized colour emoji or in a mismatched CJK face. Anything still unmatched
falls through to the rest of your installed fonts, which is where real colour
emoji get picked up.

That preference is registered the first time such a character actually turns
up, not at startup. Naming more than one font up front forces Qt to enumerate
every installed font family before it can draw anything — around half a second
with the font files already cached, considerably more without — and that used
to be paid while the first window was still being built, before anything was on
screen. Registering it on demand costs nothing extra, because a character your
font lacks makes Qt search the font database anyway, and if your font covers
everything you read it is never paid at all. When it does happen, open windows
are re-rendered once so they all agree.
