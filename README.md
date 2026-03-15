# qtpyrc

A full-featured IRC client written in Python with PySide6. Written mostly with the help of Claude Code.

It's a little sluggish, I guess because of PySide6, maybe partly Claude's fault, too.

I haven't tested all the features, so please submit an issue if something doesn't work right.

It should be cross-platform, but I haven't tested that yet either. 

## Features

**IRC Protocol**
- IRCv3 support: CAP negotiation, SASL (PLAIN/EXTERNAL), message tags, server-time, typing notifications, BATCH
- Token-bucket flood protection with configurable burst and rate
- Multiple server support per network with automatic cycling on failure
- Bouncer-friendly: handles playback batches (ZNC, chathistory)

**Interface**
- Tabbed or MDI (free-floating window) view modes
- Multi-row tab bar with network grouping and activity indicators
- Network tree sidebar (tabs, tree, or both)
- Configurable toolbar with SVG icons
- Color picker with RGB, HSB, HSL, L\*a\*b\*, and L\*C\*h\* color spaces, eyedropper, and saved colors
- Font picker with live preview
- mIRC color code rendering (16 + extended 99-color palette)
- Searchable chat output (Ctrl+F) with regex support

**Configuration**
- YAML config with 3-level cascading: global > network > server/channel
- Settings dialog with live preview for fonts and colors
- Integrated file editor for config, toolbar, popups, startup scripts, and variables
- Per-network identity, flood control, SASL, and auto-join overrides
- Per-channel ignores, auto-ops, highlights, and notification control

**Scripting & Automation**
- Python plugin API with full access to IRC events and client state
- `/exec` for inline Python evaluation
- `/on` event hooks with pattern matching, sounds, and desktop notifications
- `/timer` for recurring commands
- Startup command scripts
- mIRC-compatible popup menus (right-click context menus for nicks, channels, tabs)

**Notifications**
- Desktop notifications and sound alerts per event type (highlights, queries, notices, connect/disconnect)
- Nick notify list with ISON polling (online/offline alerts)
- Highlight patterns with regex support and {me} substitution
- Per-channel notification suppression

**Other**
- Channel history replay from SQLite database
- IRC log files with optional per-network subdirectories and monthly rotation
- URL catcher
- Built-in ident server
- Persistent user variables (/set)
- Tab-completion for nicks with recency sorting

## Requirements

- Python 3.10+
- PySide6
- ruamel.yaml
- qasync

```
pip install -r requirements.txt
```

## Quick Start

```bash
# Create a new config directory
python qtpyrc.py --init myconfig/

# Edit myconfig/config.yaml to add your networks, then run:
python qtpyrc.py -c myconfig/config.yaml
```

See the [Reference Manual](docs/reference.md) for commands, variables, CLI options, and the scripting API.
