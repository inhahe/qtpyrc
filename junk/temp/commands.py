# commands.py - Commands class and docommand()

import traceback

import state
from config import (get_ignores, get_auto_ops, _modify_list_entry,
                    _match_any)


class Commands:

  def join(window, text):
    params = text.split(None)
    if 1 <= len(params) <= 2:
      window.client.conn.join(*params)
    else:
      window.redmessage('[Error: /join takes 1 or 2 parameters]')

  def part(window, text):
    if window.type == "channel":
      window.client.conn.leave(window.channel.name, text if text.strip() else None)
    else:
      window.redmessage('[Error: /part only works in a channel window]')

  def say(window, text):
    if window.type == "server":
      window.redmessage("[Error: Can't talk in a server window]")
    elif window.type == "channel":
      window.client.conn.say(window.channel.name, text)
      window.addline_msg(window.client.conn.nickname, text)
      state.irclogger.log_channel(window.client.network, window.channel.name,
                            "<%s> %s" % (window.client.conn.nickname, text))
    elif window.type == "query":
      window.client.conn.say(window.remotenick, text)
      window.addline_msg(window.client.conn.nickname, text)

  def msg(window, text):
    recip, text = text.split(" ", 1)
    window.client.conn.msg(recip, text)
    recip = window.client.conn.irclower(recip)
    if recip in window.client.queries:
      window.client.queries[recip].window.addline_msg(window.client.conn.nickname, text)

  def server(window, text):
    parts = text.split()
    if not parts:
      window.redmessage('[Error: /server requires a hostname]')
      return
    host = parts[0]
    port = int(parts[1]) if len(parts) > 1 else 6667
    window.client.reconnect(host, port)

  def nick(window, text):
    n = text.strip()
    try:
      window.client.conn.setNick(n)
    except Exception:
      pass

  def whois(window, text):
    target = text.strip()
    if not target:
      window.redmessage("[Error: /whois requires a nick]")
      return
    if not window.client.conn:
      window.redmessage("[Error: not connected]")
      return
    window.client.conn.do_whois(target, window)

  def invite(window, text):
    """/invite <nick> [#channel]
    If no channel given, uses the current channel."""
    parts = text.split()
    if not parts:
      window.redmessage("[Error: /invite requires a nick]")
      return
    if not window.client.conn:
      window.redmessage("[Error: not connected]")
      return
    target = parts[0]
    if len(parts) > 1:
      channel = parts[1]
    elif window.type == "channel":
      channel = window.channel.name
    else:
      window.redmessage("[Error: /invite requires a channel name when not in a channel window]")
      return
    window.client.conn.sendLine("INVITE %s %s" % (target, channel))
    window.addline("[Invited %s to %s]" % (target, channel))

  def load(window, text):
    name = text.strip()
    if not name:
      window.redmessage("[Error: /load requires a script name]")
      return
    from plugins import load_script_by_name
    load_script_by_name(name, report_window=window)

  def unload(window, text):
    name = text.strip()
    if not name:
      window.redmessage("[Error: /unload requires a script name]")
      return
    if name not in state.activescripts:
      window.redmessage("[Script \"%s\" is not loaded]" % name)
      return
    old = state.activescripts.pop(name)
    if hasattr(old, 'instance') and hasattr(old.instance, 'die'):
      try:
        old.instance.die()
      except Exception:
        traceback.print_exc()
    elif hasattr(old, 'script') and hasattr(old.script, 'die'):
      try:
        old.script.die()
      except Exception:
        traceback.print_exc()
    window.redmessage("[Unloaded script: %s]" % name)

  def scripts(window, text):
    """List loaded scripts."""
    if state.activescripts:
      window.redmessage("[Loaded scripts: %s]" % ', '.join(sorted(state.activescripts.keys())))
    else:
      window.redmessage("[No scripts loaded]")

  def ignore(window, text):
    """Toggle or list ignores.  /ignore [-lrw] [mask] [#channel] [network]
    -l list  -r remove  -w top-level (any network)
    Without flags, adds to the current network level (or channel if in one)."""
    args = text.split()
    flags = set()
    positional = []
    for a in args:
      if a.startswith('-') and len(a) > 1 and a[1:].isalpha():
        flags.update(a[1:])
      else:
        positional.append(a)

    # List mode
    if 'l' in flags or not positional:
      nk = None if 'w' in flags else window.client.network_key
      ch = None
      if positional and positional[0].startswith('#'):
        ch = positional[0]
      elif window.type == "channel":
        ch = window.channel.name
      items = get_ignores(nk, ch)
      if items:
        window.redmessage("[Ignore list: %s]" % ', '.join(items))
      else:
        window.redmessage("[Ignore list is empty]")
      return

    mask = positional[0]
    remove = 'r' in flags

    # Determine level
    if 'w' in flags:
      nk = ch = None
    else:
      nk = window.client.network_key
      ch_arg = positional[1] if len(positional) > 1 and positional[1].startswith('#') else None
      if ch_arg:
        ch = ch_arg
      elif window.type == "channel":
        ch = window.channel.name
      else:
        ch = None

    _modify_list_entry('ignores', mask, remove, nk, ch)
    if remove:
      window.redmessage("[Removed ignore: %s]" % mask)
    else:
      window.redmessage("[Added ignore: %s]" % mask)

  def kick(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /kick only works in a channel window]")
      return
    parts = text.split(None, 1)
    if not parts:
      window.redmessage("[Error: /kick requires a nick]")
      return
    target = parts[0]
    reason = parts[1] if len(parts) > 1 else None
    if reason:
      window.client.conn.sendLine("KICK %s %s :%s" % (window.channel.name, target, reason))
    else:
      window.client.conn.sendLine("KICK %s %s" % (window.channel.name, target))

  def ban(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /ban only works in a channel window]")
      return
    mask = text.strip()
    if not mask:
      window.redmessage("[Error: /ban requires a nick or mask]")
      return
    if '!' not in mask and '@' not in mask:
      mask = mask + "!*@*"
    window.client.conn.sendLine("MODE %s +b %s" % (window.channel.name, mask))

  def kban(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /kban only works in a channel window]")
      return
    parts = text.split(None, 1)
    if not parts:
      window.redmessage("[Error: /kban requires a nick]")
      return
    target = parts[0]
    reason = parts[1] if len(parts) > 1 else None
    mask = target + "!*@*"
    window.client.conn.sendLine("MODE %s +b %s" % (window.channel.name, mask))
    if reason:
      window.client.conn.sendLine("KICK %s %s :%s" % (window.channel.name, target, reason))
    else:
      window.client.conn.sendLine("KICK %s %s" % (window.channel.name, target))

  def op(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /op only works in a channel window]")
      return
    target = text.strip()
    if not target:
      window.redmessage("[Error: /op requires a nick]")
      return
    window.client.conn.sendLine("MODE %s +o %s" % (window.channel.name, target))

  def deop(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /deop only works in a channel window]")
      return
    target = text.strip()
    if not target:
      window.redmessage("[Error: /deop requires a nick]")
      return
    window.client.conn.sendLine("MODE %s -o %s" % (window.channel.name, target))

  def halfop(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /halfop only works in a channel window]")
      return
    target = text.strip()
    if not target:
      window.redmessage("[Error: /halfop requires a nick]")
      return
    window.client.conn.sendLine("MODE %s +h %s" % (window.channel.name, target))

  def dehalfop(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /dehalfop only works in a channel window]")
      return
    target = text.strip()
    if not target:
      window.redmessage("[Error: /dehalfop requires a nick]")
      return
    window.client.conn.sendLine("MODE %s -h %s" % (window.channel.name, target))

  def voice(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /voice only works in a channel window]")
      return
    target = text.strip()
    if not target:
      window.redmessage("[Error: /voice requires a nick]")
      return
    window.client.conn.sendLine("MODE %s +v %s" % (window.channel.name, target))

  def devoice(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /devoice only works in a channel window]")
      return
    target = text.strip()
    if not target:
      window.redmessage("[Error: /devoice requires a nick]")
      return
    window.client.conn.sendLine("MODE %s -v %s" % (window.channel.name, target))

  def quiet(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /quiet only works in a channel window]")
      return
    target = text.strip()
    if not target:
      window.redmessage("[Error: /quiet requires a nick]")
      return
    window.client.conn.sendLine("MODE %s +q %s" % (window.channel.name, target))

  def unquiet(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /unquiet only works in a channel window]")
      return
    target = text.strip()
    if not target:
      window.redmessage("[Error: /unquiet requires a nick]")
      return
    window.client.conn.sendLine("MODE %s -q %s" % (window.channel.name, target))

  def aop(window, text):
    """/aop [-lrw] <on|off|nick|address> [#channel1,#channel2,...] [type] [network]
    -r remove  -l list  -w any network (top-level)"""
    args = text.split()
    flags = set()
    positional = []
    for a in args:
      if a.startswith('-') and len(a) > 1 and a[1:].isalpha():
        flags.update(a[1:])
      else:
        positional.append(a)

    # List mode
    if 'l' in flags or not positional:
      nk = None if 'w' in flags else window.client.network_key
      ch = None
      if positional and positional[0].startswith('#'):
        ch = positional[0].split(',')[0]
      elif window.type == "channel":
        ch = window.channel.name
      items = get_auto_ops(nk, ch)
      if items:
        window.redmessage("[Auto-op list: %s]" % ', '.join(items))
      else:
        window.redmessage("[Auto-op list is empty]")
      return

    action = positional[0]

    # on/off — enable/disable auto-op globally (just informational)
    if action.lower() == 'on':
      window.redmessage("[Auto-op is always active when entries exist]")
      return
    if action.lower() == 'off':
      window.redmessage("[Remove entries with -r to disable auto-op for specific masks]")
      return

    mask = action
    remove = 'r' in flags

    # Parse optional channels and network
    channels = []
    network_override = None
    for p in positional[1:]:
      if p.startswith('#'):
        channels.extend(p.split(','))
      elif not network_override:
        # Could be "type" (ignored for compat) or network key
        # If it matches a known network key, use it
        if state.config.networks and p in state.config.networks:
          network_override = p

    if 'w' in flags:
      # Top-level — applies to any network
      _modify_list_entry('auto_ops', mask, remove)
      label = "Removed" if remove else "Added"
      window.redmessage("[%s auto-op (global): %s]" % (label, mask))
      return

    nk = network_override or window.client.network_key

    if not channels:
      if window.type == "channel":
        channels = [window.channel.name]

    if channels:
      for ch in channels:
        ch = ch.strip()
        if ch:
          _modify_list_entry('auto_ops', mask, remove, nk, ch)
      label = "Removed" if remove else "Added"
      window.redmessage("[%s auto-op on %s: %s]" % (label, ','.join(channels), mask))
    else:
      # Network level
      _modify_list_entry('auto_ops', mask, remove, nk)
      label = "Removed" if remove else "Added"
      window.redmessage("[%s auto-op (network %s): %s]" % (label, nk or '?', mask))

  # --- /exec: evaluate arbitrary Python ---
  def exec_(window, text):
    """/exec <python expression or statement>
    Evaluates Python code with full IRC context available.
    Available: say(), msg(), raw(), join(), part(), kick(), mode(), echo(),
    nick(), me(), channel(), nicks(), timer(), on(), docommand(),
    window, client, conn, config, clients, app, irc"""
    code = text.strip()
    if not code:
      window.redmessage("[Error: /exec requires Python code]")
      return
    from exec_system import _build_exec_context
    ctx = _build_exec_context(window)
    try:
      # Try eval first (expressions), fall back to exec (statements)
      try:
        result = eval(code, ctx)
        if result is not None:
          window.addline(str(result))
      except SyntaxError:
        exec(code, ctx)
    except Exception as e:
      window.redmessage("[exec error: %s]" % e)
      traceback.print_exc()

  # --- /timer: named timers ---
  def timer(window, text):
    """/timer <name> <repeats> <interval_secs> <command>
    /timer <name> off
    /timer -l
    repeats=0 means infinite.  The command is executed as if typed."""
    from PySide6.QtCore import QTimer
    from exec_system import _timer_fire
    args = text.split()
    if not args:
      window.redmessage("[Usage: /timer <name> <repeats> <interval> <command>  |  /timer <name> off  |  /timer -l]")
      return

    # List timers
    if args[0] == '-l':
      if not state._timers:
        window.redmessage("[No active timers]")
      else:
        for tname, tinfo in sorted(state._timers.items()):
          rem = tinfo['remaining']
          rem_str = 'infinite' if rem == 0 else str(rem)
          window.redmessage("[Timer \"%s\": %s reps left, every %.1fs — %s]" % (
            tname, rem_str, tinfo['interval_ms'] / 1000, tinfo['command']))
      return

    name = args[0]

    # Stop timer
    if len(args) >= 2 and args[1].lower() == 'off':
      if name in state._timers:
        state._timers[name]['timer'].stop()
        del state._timers[name]
        window.redmessage("[Timer \"%s\" stopped]" % name)
      else:
        window.redmessage("[Timer \"%s\" not found]" % name)
      return

    if len(args) < 4:
      window.redmessage("[Usage: /timer <name> <repeats> <interval_secs> <command>]")
      return

    try:
      reps = int(args[1])
      interval = float(args[2])
    except ValueError:
      window.redmessage("[Error: repeats must be int, interval must be a number]")
      return

    command = ' '.join(args[3:])

    # Stop existing timer with same name
    if name in state._timers:
      state._timers[name]['timer'].stop()

    t = QTimer()
    t.setInterval(int(interval * 1000))
    t.timeout.connect(lambda: _timer_fire(name))
    state._timers[name] = {
      'timer': t,
      'remaining': reps,
      'command': command,
      'window': window,
      'interval_ms': int(interval * 1000),
    }
    t.start()
    rep_str = 'infinite' if reps == 0 else str(reps)
    window.redmessage("[Timer \"%s\" started: %s reps, every %.1fs]" % (name, rep_str, interval))

  # --- /on: event hooks ---
  def on(window, text):
    """/on [-r] <event> <hookname> [#channel] [pattern] <command>
    /on -l [event]
    Events: chanmsg privmsg action noticed join part quit kick nick topic
            mode connect disconnect signon motd invite rawcmd numeric"""
    from exec_system import _ON_EVENT_MAP
    from config import _mask_match
    args = text.split()
    if not args:
      window.redmessage("[Usage: /on <event> <hookname> [#channel] [pattern] <command>  |  /on -r <event> <hookname>  |  /on -l [event]]")
      return

    # List hooks
    if args[0] == '-l':
      event_filter = args[1].lower() if len(args) > 1 else None
      found = False
      for ev in sorted(state._on_hooks.keys()):
        if event_filter and ev != event_filter:
          continue
        hooks = state._on_hooks[ev]
        for hname, hinfo in sorted(hooks.items()):
          filters = []
          if hinfo.get('channel'):
            filters.append(hinfo['channel'])
          if hinfo.get('network'):
            filters.append('net:' + hinfo['network'])
          pat = hinfo.get('pattern', '*')
          fstr = (' [%s]' % ' '.join(filters)) if filters else ''
          window.redmessage("[on %s \"%s\"%s pattern=\"%s\" -> %s]" % (
            ev, hname, fstr, pat, hinfo['command']))
          found = True
      if not found:
        window.redmessage("[No /on hooks%s]" % (' for %s' % event_filter if event_filter else ''))
      return

    # Remove hook
    if args[0] == '-r':
      if len(args) < 3:
        window.redmessage("[Usage: /on -r <event> <hookname>]")
        return
      event = args[1].lower()
      hookname = args[2]
      hooks = state._on_hooks.get(event, {})
      if hookname in hooks:
        del hooks[hookname]
        window.redmessage("[Removed hook \"%s\" from %s]" % (hookname, event))
      else:
        window.redmessage("[Hook \"%s\" not found on %s]" % (hookname, event))
      return

    if len(args) < 3:
      window.redmessage("[Usage: /on <event> <hookname> [#channel] [pattern] <command>]")
      return

    event = args[0].lower()
    hookname = args[1]

    if event not in _ON_EVENT_MAP:
      window.redmessage("[Unknown event: %s.  Valid: %s]" % (
        event, ' '.join(sorted(_ON_EVENT_MAP.keys()))))
      return

    # Parse remaining: optional #channel, optional pattern, then command
    rest = args[2:]
    channel_filter = None
    network_filter = None
    pattern = '*'

    # Check for #channel
    if rest and rest[0].startswith('#'):
      channel_filter = rest.pop(0)

    # Check for network= prefix
    if rest and rest[0].startswith('net:'):
      network_filter = rest.pop(0)[4:]

    # Check for pattern (quoted or single word before the command)
    # If the next token starts with *, it's a pattern
    if rest and len(rest) > 1 and ('*' in rest[0] or '?' in rest[0]):
      pattern = rest.pop(0)

    command = ' '.join(rest)
    if not command:
      window.redmessage("[Error: no command specified]")
      return

    if event not in state._on_hooks:
      state._on_hooks[event] = {}
    state._on_hooks[event][hookname] = {
      'pattern': pattern,
      'command': command,
      'channel': channel_filter,
      'network': network_filter,
      'window': window,
    }
    parts = [event, '"%s"' % hookname]
    if channel_filter:
      parts.append(channel_filter)
    if pattern != '*':
      parts.append('pattern="%s"' % pattern)
    window.redmessage("[Added hook: on %s -> %s]" % (' '.join(parts), command))

  # --- /timers: list all active timers (alias) ---
  def timers(window, text):
    Commands.timer(window, '-l')

  # --- /hooks: list all /on hooks (alias) ---
  def hooks(window, text):
    Commands.on(window, '-l')

  # --- /find: open text search bar ---
  def find(window, text):
    window._search_open()
    if text.strip():
      window._search_input.setText(text.strip())
      window._search_do(forward=False)

  # --- window layout commands ---
  def tile(window, text):
    from qtpyrc import _apply_view_mode, _tile_vertically
    t = text.strip().lower()
    _apply_view_mode('mdi')
    if t.startswith('v'):
      _tile_vertically()
    else:
      state.app.mainwin.workspace.tileSubWindows()

  def cascade(window, text):
    from qtpyrc import _apply_view_mode
    _apply_view_mode('mdi')
    state.app.mainwin.workspace.cascadeSubWindows()

  def tabbed(window, text):
    from qtpyrc import _apply_view_mode
    _apply_view_mode('tabbed')

  def mdi(window, text):
    from qtpyrc import _apply_view_mode
    _apply_view_mode('mdi')


def docommand(window, command, text=""):
  command = command.lower()
  # Map keywords that can't be method names
  if command == 'exec':
    command = 'exec_'
  if hasattr(Commands, command) and not command.startswith("_"):
    getattr(Commands, command)(window, text)
