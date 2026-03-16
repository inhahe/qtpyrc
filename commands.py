# commands.py - Commands class and docommand()

import traceback

import state
from config import (get_ignores, get_auto_ops,
                    _modify_list_entry, _match_any)


class Commands:

  def join(window, text):
    args = text.split(None)
    if not args:
      window.redmessage('[Usage: /join [-n] <channel> [key]  or  /join <network>/<channel> [key]]')
      return
    # -n flag: don't parse network/ prefix (for channels with / in the name)
    no_net = False
    if args[0] == '-n':
      no_net = True
      args.pop(0)
    if not args:
      window.redmessage('[Usage: /join [-n] <channel> [key]]')
      return
    target = args[0]
    key = args[1] if len(args) > 1 else None
    client = window.client
    # Check for network/channel syntax
    if not no_net and '/' in target and not target.startswith(('#', '&', '!', '+')):
      net_name, chan_part = target.split('/', 1)
      found = _find_client(net_name)
      if found:
        client = found
        target = chan_part
    conn = client.conn if client else None
    if conn:
      chan_name = target if target[0:1] in '#&!+' else '#' + target
      chnlower = conn.irclower(chan_name)
      # If already in the channel, just switch to it
      chan = conn.client.channels.get(chnlower)
      if chan and chan.window:
        state.app.mainwin.workspace.setActiveSubWindow(chan.window.subwindow)
        return
      # Mark as user-initiated so persist_autojoins only fires for /join
      conn._user_joins.add(chnlower)
      if key:
        conn.join(chan_name, key)
      else:
        conn.join(chan_name)
    else:
      window.redmessage('[Not connected]')

  def part(window, text):
    if window.type == "channel":
      text = _unquote(text.strip()) if text.strip() else ''
      conn = window.client.conn
      if conn:
        chnlower = conn.irclower(window.channel.name)
        conn._user_parts.add(chnlower)
        conn.leave(window.channel.name, text if text else None)
      else:
        window.redmessage('[Not connected]')
    else:
      window.redmessage('[Error: /part only works in a channel window]')

  def hop(window, text):
    if window.type == "channel":
      conn = window.client.conn
      if not conn:
        window.redmessage('[Not connected]')
        return
      chan = window.channel.name
      key = window.channel.key
      conn._hopping.add(conn.irclower(chan))
      conn.leave(chan)
      if key:
        conn.join(chan, key)
      else:
        conn.join(chan)
    else:
      window.redmessage('[Error: /hop only works in a channel window]')

  def say(window, text):
    text = _unquote(text)
    if window.type == "server":
      window.redmessage("[Error: Can't talk in a server window]")
    elif window.type == "channel":
      conn = window.client.conn
      conn.say(window.channel.name, text)
      window.addline_msg(conn.nickname, text)
      state.irclogger.log_channel(window.client.network, window.channel.name,
                            "<%s> %s" % (conn.nickname, text))
      if state.historydb:
        state.historydb.add(window.client.network, window.channel.name.lower(),
                            'message', conn.nickname, text)
      from link_preview import check_and_preview
      check_and_preview(window, text)
      # Dispatch to plugin chanmsg hooks for own messages
      from plugins import _dispatch_to_plugins
      user = '%s!%s@%s' % (conn.nickname, conn.username or '', '')
      _dispatch_to_plugins('chanmsg', conn, (user, window.channel.name, text), {})
    elif window.type == "query":
      window.client.conn.say(window.remotenick, text)
      window.addline_msg(window.client.conn.nickname, text)
      if state.historydb and window.query:
        from irc_client import _query_history_key
        state.historydb.add(window.client.network,
                            _query_history_key(window.query.nick, window.query.ident),
                            'message', window.client.conn.nickname, text)
      from link_preview import check_and_preview
      check_and_preview(window, text)

  def amsg(window, text):
    """Send a message to all open channels on the current network."""
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    text = _unquote(text.strip())
    if not text:
      window.redmessage('[Usage: /amsg <message>]')
      return
    for chan in window.client.channels.values():
      conn.say(chan.name, text)
      chan.window.addline_msg(conn.nickname, text)
      state.irclogger.log_channel(window.client.network, chan.name,
                            "<%s> %s" % (conn.nickname, text))
      if state.historydb:
        state.historydb.add(window.client.network, chan.name.lower(),
                            'message', conn.nickname, text)

  def msg(window, text):
    recip, text = text.split(" ", 1)
    text = _unquote(text)
    window.client.conn.msg(recip, text)
    recip = window.client.conn.irclower(recip)
    if recip in window.client.queries:
      window.client.queries[recip].window.addline_msg(window.client.conn.nickname, text)

  def quit(window, text):
    conn = window.client.conn
    if not conn:
      window.redmessage('[Not connected]')
      return
    window.client._intentional_disconnect = True
    msg = _unquote(text.strip()) if text.strip() else 'Leaving'
    conn.quit(msg)

  disconnect = quit

  def server(window, text):
    parts = text.split()
    if not parts:
      window.redmessage('[Error: /server requires a hostname]')
      return
    host = parts[0]
    port = int(parts[1]) if len(parts) > 1 else 6667
    window.client.reconnect(host, port)

  def connect(window, text):
    name = text.strip()
    if not name:
      window.redmessage('[Error: /connect requires a network name]')
      return
    # Find matching network key (case-insensitive)
    networks = state.config.networks or {}
    netkey = None
    for key in networks:
      if key.lower() == name.lower():
        netkey = key
        break
    if not netkey:
      window.redmessage('[Unknown network: %s. Available: %s]' % (
        name, ', '.join(networks.keys()) if networks else 'none'))
      return
    # Check if already connected to this network
    for client in state.clients:
      if client.network_key == netkey:
        window.redmessage('[Already connected to %s]' % netkey)
        return
    import asyncio
    from models import Client
    client = Client(network_key=netkey)
    state.clients.add(client)
    asyncio.ensure_future(client.connect_to_server())

  def ctcp(window, text):
    parts = text.split(None, 2)
    if len(parts) < 2:
      window.redmessage('[Usage: /ctcp <nick> <type> [data]]')
      return
    conn = window.client.conn
    if not conn:
      window.redmessage('[Not connected]')
      return
    target = parts[0]
    tag = parts[1].upper()
    data = _unquote(parts[2]) if len(parts) > 2 else ''
    # For PING, auto-add timestamp if no data given
    if tag == 'PING' and not data:
      import time
      data = str(int(time.time()))
    # Route reply back to this window
    conn.do_ctcp(target, tag, data, window)
    window.addline('[CTCP %s to %s%s]' % (tag, target, (': ' + data) if data else ''))

  def nick(window, text):
    n = text.strip()
    try:
      window.client.conn.setNick(n)
    except Exception as e:
      state.dbg(state.LOG_WARN, '[cmd] /nick failed:', e)
      window.redmessage('[Nick change failed: %s]' % e)

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

  def plugin(window, text):
    """Plugin management.  /plugin <name> — load
    /plugin -u <name> — unload
    /plugin -r <name> — reload (unload + load)"""
    args = text.split()
    if not args:
      window.redmessage("[Usage: /plugin [-u|-r] <name>]")
      return
    flag = ''
    if args[0] in ('-u', '-r'):
      flag = args.pop(0)
    if not args:
      window.redmessage("[Usage: /plugin [-u|-r] <name>]")
      return
    name = args[0]
    if not flag and name in state.activescripts:
      window.redmessage('[Plugin "%s" is already loaded. Use /plugin -r %s to reload.]' % (name, name))
      return
    if flag in ('-u', '-r'):
      # Unload
      if name not in state.activescripts:
        if flag == '-u':
          window.redmessage("[Plugin \"%s\" is not loaded]" % name)
          return
      else:
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
        if flag == '-u':
          window.redmessage("[Unloaded plugin: %s]" % name)
          return
        # -r: fall through to reload
    from plugins import load_script_by_name
    load_script_by_name(name, report_window=window)

  load = plugin  # alias

  def unload(window, text):
    """Unload a Python plugin.  /unload <name>"""
    Commands.plugin(window, '-u ' + text)

  def plugins(window, text):
    """List plugins.  /plugins [-l|-a] — -l loaded only, -a auto-load only."""
    import os
    flag = text.strip()
    if flag == '-l':
      if state.activescripts:
        window.redmessage("[Loaded plugins: %s]" % ', '.join(sorted(state.activescripts.keys())))
      else:
        window.redmessage("[No plugins loaded]")
      return
    if flag == '-a':
      plugins_cfg = state.config._data.get('plugins') or {}
      auto = plugins_cfg.get('auto_load') or []
      if auto:
        window.redmessage("[Auto-load plugins: %s]" % ', '.join(str(a) for a in auto))
      else:
        window.redmessage("[No plugins in auto-load]")
      return
    plugins_cfg = state.config._data.get('plugins') or {}
    plugins_dir = plugins_cfg.get('dir', 'plugins')
    if not os.path.isabs(plugins_dir):
      plugins_dir = os.path.join(os.path.dirname(os.path.abspath(state.config.path)), plugins_dir)
    available = []
    if os.path.isdir(plugins_dir):
      for f in sorted(os.listdir(plugins_dir)):
        path = os.path.join(plugins_dir, f)
        if f.endswith('.py') and not f.startswith('_'):
          name = f[:-3]
        elif os.path.isdir(path) and os.path.isfile(os.path.join(path, '__init__.py')):
          name = f
        else:
          continue
        loaded = '(loaded)' if name in state.activescripts else ''
        available.append(name + (' ' + loaded if loaded else ''))
    if available:
      window.redmessage("[Plugins in %s:]" % plugins_dir)
      for p in available:
        window.redmessage("  %s" % p)
    else:
      window.redmessage("[No plugins in %s]" % plugins_dir)

  def scripts(window, text):
    """List command scripts.  /scripts [-a] — -a for auto-load only."""
    import os
    if text.strip() == '-a':
      scripts_cfg = state.config._data.get('scripts') or {}
      auto = scripts_cfg.get('auto_load') or []
      startup = scripts_cfg.get('startup', '')
      items = ([startup] if startup else []) + list(auto)
      if items:
        window.redmessage("[Auto-load scripts: %s]" % ', '.join(str(a) for a in items))
      else:
        window.redmessage("[No scripts in auto-load]")
      return
    scripts_cfg = state.config._data.get('scripts') or {}
    scripts_dir = scripts_cfg.get('dir', 'scripts')
    if not os.path.isabs(scripts_dir):
      scripts_dir = os.path.join(os.path.dirname(os.path.abspath(state.config.path)), scripts_dir)
    if os.path.isdir(scripts_dir):
      files = sorted(f for f in os.listdir(scripts_dir) if not f.startswith('_'))
      if files:
        window.redmessage("[Scripts in %s: %s]" % (scripts_dir, ', '.join(files)))
      else:
        window.redmessage("[No scripts in %s]" % scripts_dir)
    else:
      window.redmessage("[Scripts directory not found: %s]" % scripts_dir)

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

  def highlight(window, text):
    """Manage highlight patterns.  /highlight [-lrw] [pattern]
    -l list  -r remove  -w top-level (any network)
    Without flags, adds to the current network (or channel if in one).
    Plain strings are case-insensitive. Use /regex/ or /regex/i for regex."""
    from config import get_highlights, modify_highlight_entry
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
      nk = None if 'w' in flags else (window.client.network_key if window.client else None)
      ch = None
      if positional and positional[0].startswith('#'):
        ch = positional[0]
      elif window.type == "channel":
        ch = window.channel.name
      items = get_highlights(nk, ch)
      if items:
        window.redmessage("[Highlights: %s]" % ', '.join(items))
      else:
        window.redmessage("[Highlight list is empty]")
      return

    # The pattern may contain spaces (e.g. /multi word regex/)
    # so rejoin positional args
    pattern = ' '.join(positional)
    remove = 'r' in flags

    if 'w' in flags:
      nk = ch = None
    else:
      nk = window.client.network_key if window.client else None
      ch = window.channel.name if window.type == "channel" else None

    modify_highlight_entry(pattern, remove, nk, ch)
    if remove:
      window.redmessage("[Removed highlight: %s]" % pattern)
    else:
      window.redmessage("[Added highlight: %s]" % pattern)

  def notify(window, text):
    """Manage the nick watch list.  /notify [-lrw] [nick]
    -l list  -r remove  -w global (any network)
    Without flags, adds nick to the current network's notify list."""
    from config import get_notify_list, modify_notify_entry
    args = text.split()
    flags = set()
    positional = []
    for a in args:
      if a.startswith('-') and len(a) > 1 and a[1:].isalpha():
        flags.update(a[1:])
      else:
        positional.append(a)

    nk = None if 'w' in flags else (window.client.network_key if window.client else None)

    # List mode
    if 'l' in flags or not positional:
      nicks = get_notify_list(nk)
      if not nicks:
        window.redmessage("[Notify list is empty]")
        return
      if state.notifications:
        online_state = state.notifications.get_state(nk) if nk else {}
        parts = []
        for n in nicks:
          s = online_state.get(n.lower())
          if s is True:
            parts.append('%s (online)' % n)
          elif s is False:
            parts.append('%s (offline)' % n)
          else:
            parts.append(n)
        window.redmessage("[Notify list: %s]" % ', '.join(parts))
      else:
        window.redmessage("[Notify list: %s]" % ', '.join(nicks))
      return

    nick = positional[0]
    remove = 'r' in flags
    modify_notify_entry(nick, remove, network_key=nk)
    if state.notifications:
      conn = window.client.conn if window.client else None
      state.notifications.sync_list(nk, conn)
      # Trigger immediate ISON check for non-MONITOR servers
      if not remove and conn and not getattr(conn, '_monitor_supported', False):
        state.notifications._poll_ison()
    if remove:
      window.redmessage("[Removed from notify: %s]" % nick)
    else:
      window.redmessage("[Added to notify: %s]" % nick)

  def kick(window, text):
    if window.type != "channel":
      window.redmessage("[Error: /kick only works in a channel window]")
      return
    parts = text.split(None, 1)
    if not parts:
      window.redmessage("[Error: /kick requires a nick]")
      return
    target = parts[0]
    reason = _unquote(parts[1]) if len(parts) > 1 else None
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
    reason = _unquote(parts[1]) if len(parts) > 1 else None
    mask = target + "!*@*"
    window.client.conn.sendLine("MODE %s +b %s" % (window.channel.name, mask))
    if reason:
      window.client.conn.sendLine("KICK %s %s :%s" % (window.channel.name, target, reason))
    else:
      window.client.conn.sendLine("KICK %s %s" % (window.channel.name, target))

  def debuglog(window, text):
    """Toggle debug output logging to a file.
    /debuglog <filename>    — start logging (append)
    /debuglog -o <filename> — start logging (overwrite)
    /debuglog               — stop logging"""
    import os
    args = text.strip()
    if not args:
      if state._dbg_file:
        name = state._dbg_file.name
        state._dbg_file.close()
        state._dbg_file = None
        window.addline('[Debug logging stopped (%s)]' % name)
      else:
        window.redmessage('[Debug logging is not active. Usage: /debuglog [-o] <filename>]')
      return
    mode = 'a'
    if args.startswith('-o '):
      mode = 'w'
      args = args[3:].strip()
    path = args
    if not path:
      window.redmessage('[Usage: /debuglog [-o] <filename>]')
      return
    if state._dbg_file:
      state._dbg_file.close()
      state._dbg_file = None
    try:
      state._dbg_file = open(path, mode, encoding='utf-8')
      window.addline('[Debug logging to %s (%s)]'
                     % (os.path.abspath(path), 'overwrite' if mode == 'w' else 'append'))
    except Exception as e:
      window.redmessage('[Error opening debug log: %s]' % e)

  def chaninfo(window, text):
    """Show channel details dialog (modes, bans, topic)."""
    if window.type != "channel":
      window.redmessage("[Error: /chaninfo only works in a channel window]")
      return
    from channel_details import show_channel_details
    show_channel_details(window.channel, parent=state.app.mainwin)

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
    """/on <event> <name> [options] [pattern] [command]
    /on -r <event> <name>
    /on -l [event]
    Options: -n nick/mask  -c #channel  -k network  -s sound  -d  -h  -p  -x
    -p persists the hook by appending it to the startup script.
    -x suppresses the default handler (event won't appear in window).
    Events: chanmsg privmsg action noticed join part quit kick nick topic
            mode connect disconnect signon motd invite rawcmd numeric ctcpreply"""
    from exec_system import _ON_EVENT_MAP
    args = text.split()
    if not args:
      window.redmessage("[Usage: /on <event> <name> [options] [pattern] [command]"
                        "  |  /on -r <event> <name>  |  /on -l [event]]")
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
          if hinfo.get('nick_mask'):
            filters.append('-n ' + hinfo['nick_mask'])
          if hinfo.get('channel'):
            filters.append('-c ' + hinfo['channel'])
          if hinfo.get('network'):
            filters.append('-k ' + hinfo['network'])
          if hinfo.get('sound'):
            filters.append('-s ' + hinfo['sound'])
          if hinfo.get('desktop'):
            filters.append('-d')
          if hinfo.get('highlight_tab'):
            filters.append('-h')
          if hinfo.get('suppress'):
            filters.append('-x')
          pat = hinfo.get('pattern', '*')
          fstr = (' %s' % ' '.join(filters)) if filters else ''
          cmd = hinfo.get('command', '')
          if cmd:
            window.redmessage('[on %s "%s"%s %s -> %s]' % (ev, hname, fstr, pat, cmd))
          else:
            window.redmessage('[on %s "%s"%s %s]' % (ev, hname, fstr, pat))
          found = True
      if not found:
        window.redmessage("[No /on hooks%s]" % (' for %s' % event_filter if event_filter else ''))
      return

    # Remove hook
    if args[0] == '-r':
      persist = '-p' in args[1:]
      rargs = [a for a in args[1:] if a != '-p']
      if len(rargs) < 2:
        window.redmessage("[Usage: /on -r [-p] <event> <name>]")
        return
      event = rargs[0].lower()
      hookname = rargs[1]
      hooks = state._on_hooks.get(event, {})
      if hookname in hooks:
        del hooks[hookname]
        window.redmessage('[Removed hook "%s" from %s]' % (hookname, event))
      else:
        window.redmessage('[Hook "%s" not found on %s]' % (hookname, event))
      if persist:
        if _remove_from_startup(event, hookname, window):
          _wmsg(window, "Removed from startup script")
      return

    if len(args) < 2:
      window.redmessage("[Usage: /on <event> <name> [options] [pattern] [command]]")
      return

    event = args[0].lower()
    hookname = args[1]

    if event not in _ON_EVENT_MAP:
      window.redmessage("[Unknown event: %s.  Valid: %s]" % (
        event, ' '.join(sorted(_ON_EVENT_MAP.keys()))))
      return

    # Parse options and remaining args
    rest = args[2:]
    channel_filter = None
    network_filter = None
    nick_mask = None
    sound = None
    desktop = False
    highlight_tab = False
    persist = False
    suppress = False

    while rest:
      if rest[0] == '-n' and len(rest) > 1:
        rest.pop(0)
        nick_mask = rest.pop(0)
      elif rest[0] == '-c' and len(rest) > 1:
        rest.pop(0)
        channel_filter = rest.pop(0)
      elif rest[0] == '-k' and len(rest) > 1:
        rest.pop(0)
        network_filter = rest.pop(0)
      elif rest[0] == '-s' and len(rest) > 1:
        rest.pop(0)
        sound = rest.pop(0)
      elif rest[0] == '-d':
        rest.pop(0)
        desktop = True
      elif rest[0] == '-h':
        rest.pop(0)
        highlight_tab = True
      elif rest[0] == '-p':
        rest.pop(0)
        persist = True
      elif rest[0] == '-x':
        rest.pop(0)
        suppress = True
      else:
        break

    # Legacy positional: #channel before options
    if rest and rest[0].startswith('#') and not channel_filter:
      channel_filter = rest.pop(0)
    # Legacy positional: net:key
    if rest and rest[0].startswith('net:') and not network_filter:
      network_filter = rest.pop(0)[4:]

    # Pattern: next token if it contains wildcards or is a /regex/
    pattern = '*'
    if rest:
      tok = rest[0]
      is_wildcard = ('*' in tok or '?' in tok) and len(rest) > 1
      is_regex = tok.startswith('/') and '/' in tok[1:]
      if is_wildcard or is_regex:
        pattern = rest.pop(0)

    command = ' '.join(rest)

    # Must have at least one action
    if not command and not sound and not desktop and not highlight_tab:
      window.redmessage("[Error: no command or action specified (use -s, -d, -h, or a command)]")
      return

    if event not in state._on_hooks:
      state._on_hooks[event] = {}
    state._on_hooks[event][hookname] = {
      'pattern': pattern,
      'command': command,
      'channel': channel_filter,
      'network': network_filter,
      'nick_mask': nick_mask,
      'sound': sound,
      'desktop': desktop,
      'highlight_tab': highlight_tab,
      'suppress': suppress,
      'window': window,
    }
    parts = [event, '"%s"' % hookname]
    if nick_mask:
      parts.append('-n %s' % nick_mask)
    if channel_filter:
      parts.append('-c %s' % channel_filter)
    if network_filter:
      parts.append('-k %s' % network_filter)
    if sound:
      parts.append('-s %s' % sound)
    if desktop:
      parts.append('-d')
    if highlight_tab:
      parts.append('-h')
    if suppress:
      parts.append('-x')
    if pattern != '*':
      parts.append(pattern)
    if command:
      window.redmessage("[Added hook: on %s -> %s]" % (' '.join(parts), command))
    else:
      window.redmessage("[Added hook: on %s]" % ' '.join(parts))

    if persist:
      line = '/on %s %s' % (' '.join(parts), command)
      if _persist_to_startup(line.strip(), window):
        _wmsg(window, "Persisted to startup script")

  # --- /timers: list all active timers (alias) ---
  def timers(window, text):
    Commands.timer(window, '-l')

  # --- /hooks: list all /on hooks (alias) ---
  def hooks(window, text):
    Commands.on(window, '-l')

  # --- /find: open text search bar ---
  def find(window, text):
    window._search_open()
    t = _unquote(text.strip())
    if t:
      window._search_input.setText(t)
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

  def save(window, text):
    """Flush the current configuration to disk."""
    state.config.save()
    window.redmessage("[Configuration saved to %s]" % state.config.path)

  def reload(window, text):
    """Re-read configuration from the current YAML file."""
    from config import loadconfig
    try:
      cfg = loadconfig(state.config.path)
    except Exception as e:
      window.redmessage("[Error reloading config: %s]" % e)
      return
    state.config = cfg
    window.redmessage("[Configuration reloaded from %s]" % cfg.path)

  def raw(window, text):
    """Send a raw IRC command to the server."""
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    line = text.strip()
    if not line:
      window.redmessage('[Usage: /raw <raw IRC command>]')
      return
    conn.sendLine(line)

  quote = raw  # /quote is a common alias

  def echo(window, text):
    """Print text to a window.  /echo [-w target] text"""
    target = window
    if text.startswith('-w '):
      rest = text[3:].lstrip()
      parts = rest.split(None, 1)
      if parts:
        w = _find_window(parts[0], window.client)
        if w:
          target = w
          text = parts[1] if len(parts) > 1 else ''
        else:
          window.redmessage('[No such window: %s]' % parts[0])
          return
    target.addline(_unquote(text), state.infofmt)

  def stdout(window, text):
    """Write text to stdout."""
    import sys
    print(_unquote(text), file=sys.stdout)

  def stderr(window, text):
    """Write text to stderr."""
    import sys
    print(_unquote(text), file=sys.stderr)

  def query(window, text):
    """Open a query window.  /query <nick> ["message"]"""
    parts = text.split(None, 1)
    if not parts:
      window.redmessage("[Usage: /query <nick> [message]]")
      return
    nick = parts[0]
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    # Find or create the query window
    nicklower = conn.irclower(nick)
    user = window.client.users.get(nicklower)
    ident = user.ident if user else None
    host = user.host if user else None
    qkey = (ident, host)
    if qkey not in conn.queries:
      from models import Query
      conn.queries[qkey] = Query(window.client, nick, ident)
    qwin = conn.queries[qkey].window
    # Activate the query window
    ws = state.app.mainwin.workspace
    ws.setActiveSubWindow(qwin.subwindow)
    # Send a message if provided
    if len(parts) > 1:
      msg = _unquote(parts[1])
      conn.msg(nick, msg)
      qwin.addline_msg(conn.nickname, msg)

  def log(window, text):
    """Write a line to the log file for a window.  /log [-w target] "text" """
    if not state.irclogger:
      window.redmessage('[Logging is not enabled]')
      return
    target = window
    if text.startswith('-w '):
      rest = text[3:].lstrip()
      parts = rest.split(None, 1)
      if parts:
        w = _find_window(parts[0], window.client)
        if w:
          target = w
          text = parts[1] if len(parts) > 1 else ''
        else:
          window.redmessage('[No such window: %s]' % parts[0])
          return
    line = _unquote(text)
    client = target.client
    network = client.network if client else ''
    if target.type == 'channel' and target.channel:
      state.irclogger.log_channel(network, target.channel.name, line)
    elif target.type == 'query' and hasattr(target, 'remotenick'):
      state.irclogger.log(network, target.remotenick, line)
    elif target.type == 'server':
      state.irclogger.log_server(network, line)
    else:
      window.redmessage('[No log target for this window]')

  def close(window, text):
    """Close a window.  /close [-f] [target]
    -f forces close without confirmation for server windows."""
    args = text.split()
    force = False
    if args and args[0] == '-f':
      args.pop(0)
      force = True
    target = window
    if args:
      w = _find_window(args[0], window.client)
      if not w:
        window.redmessage('[No such window: %s]' % args[0])
        return
      target = w
    from qtpyrc import _close_window
    _close_window(target, force=force)

  def window(window, text):
    """Switch to a window.  /window <target>  or  /window <network>/<target>
    target can be a channel name, query nick, or 'server'.
    Use -n to disable network/ parsing (for targets with / in the name).
    With just a network name, switches to its server window."""
    args = text.split()
    if not args:
      window.redmessage('[Usage: /window [-n] <target>  or  /window <network>/<target>]')
      return
    no_net = False
    if args[0] == '-n':
      no_net = True
      args.pop(0)
    if not args:
      window.redmessage('[Usage: /window [-n] <target>]')
      return
    target = ' '.join(args)
    # Check for network/target syntax
    if not no_net and '/' in target:
      net_name, tgt = target.split('/', 1)
      client = _find_client(net_name)
      if client:
        if not tgt or tgt.lower() == 'server':
          state.app.mainwin.workspace.setActiveSubWindow(client.window.subwindow)
          return
        w = _find_window(tgt, client)
        # Fallback: try with # prepended
        if not w and tgt[0:1] not in '#&!+':
          w = _find_window('#' + tgt, client)
        if w:
          state.app.mainwin.workspace.setActiveSubWindow(w.subwindow)
        else:
          window.redmessage('[No window: %s/%s]' % (net_name, tgt))
        return
    # No network/ prefix or unknown network — check if it's a network name
    c = _find_client(target)
    if c:
      state.app.mainwin.workspace.setActiveSubWindow(c.window.subwindow)
      return
    # Search current network first, then all
    w = _find_window(target, window.client)
    if not w:
      w = _find_window(target)
    # Fallback: try with # prepended
    if not w and target[0:1] not in '#&!+':
      w = _find_window('#' + target, window.client)
      if not w:
        w = _find_window('#' + target)
    if w:
      state.app.mainwin.workspace.setActiveSubWindow(w.subwindow)
    else:
      window.redmessage('[No window: %s]' % target)

  def help(window, text):
    """Show help for a command or topic.
    /help              — list commands and topics
    /help /command     — help for a slash command
    /help topic        — help for a topic (events, variables, popups, plugin, etc.)
    """
    import os, re
    raw = text.strip()
    # If the user included the command prefix, force command lookup
    force_command = raw.startswith(state.config.cmdprefix)
    cmd = raw.lstrip(state.config.cmdprefix).lower()
    ref_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'docs', 'reference.md')
    try:
      with open(ref_path, 'r', encoding='utf-8') as f:
        ref = f.read()
    except FileNotFoundError:
      window.redmessage('[reference.md not found]')
      return

    if not cmd:
      # List all commands from table rows
      cmds = []
      for m in re.finditer(r'^\| `(/\w+)`', ref, re.MULTILINE):
        c = m.group(1)
        if c not in cmds:
          cmds.append(c)
      window.addline('[Commands: %s]' % ', '.join(cmds))
      window.addline('[Topics: events, variables, popups, plugin, objects, cli]')
      window.addline('[Use /help <command> or /help <topic> for details]')
      return

    # If user typed /help /command, try command lookup first
    if force_command:
      lines_found = []
      for m in re.finditer(
          r'^\| `/%s`\s*\|([^|]*)\|([^|]*)\|' % re.escape(cmd), ref, re.MULTILINE):
        syntax = m.group(1).strip().strip('`').strip()
        desc = m.group(2).strip()
        lines_found.append((syntax, desc))
      if lines_found:
        for syntax, desc in lines_found:
          window.addline('  %s — %s' % (syntax, desc))
        section_pat = re.compile(
            r'^##+ .*/%s\b.*$' % re.escape(cmd), re.MULTILINE | re.IGNORECASE)
        m = section_pat.search(ref)
        if m:
          _show_help_section(window, ref, m.group(0), start_match=m)
        return
      window.redmessage('[No help for: /%s]' % cmd)
      return

    # Check for topic-based help — show compact lists
    if cmd.startswith(('events ', 'event ')):
      # /help events <name> — show details for a specific event
      event_name = cmd.split(None, 1)[1].strip()
      _show_event_help(window, ref, event_name, re)
      return
    if cmd in ('events', 'event', 'on'):
      # Extract event names from the Event Names table
      events = re.findall(r'^\| `(\w+)` \|', ref, re.MULTILINE)
      if events:
        window.addline('[/on events: %s]' % ', '.join(events))
        window.addline('[Use /help events <name> to see variables for an event]')
      return
    if cmd.startswith(('variables ', 'vars ', 'exec ')):
      # /help variables <name> — show details for a specific variable/function
      var_name = cmd.split(None, 1)[1].strip()
      exec_section = ref[ref.index('## /exec Context'):] if '## /exec Context' in ref else ''
      # Search in both Objects and Functions tables
      var_m = re.search(
          r'^\| `%s` \|([^|]*)\|([^|]*)\|' % re.escape(var_name), exec_section, re.MULTILINE)
      if var_m:
        col1 = var_m.group(1).strip().strip('`').strip()
        col2 = var_m.group(2).strip()
        if col1 and col2:
          window.addline('  %s  %s — %s' % (var_name, col1, col2))
        elif col2:
          window.addline('  %s — %s' % (var_name, col2))
        else:
          window.addline('  %s — %s' % (var_name, col1))
      else:
        # Try 2-column table (Objects section)
        var_m2 = re.search(
            r'^\| `%s` \|([^|]*)\|' % re.escape(var_name), exec_section, re.MULTILINE)
        if var_m2:
          window.addline('  %s — %s' % (var_name, var_m2.group(1).strip()))
        else:
          window.redmessage('[Unknown variable: %s]' % var_name)
      return
    if cmd in ('variables', 'vars', 'exec'):
      # Extract variable names from the /exec Context tables
      exec_section = ref[ref.index('## /exec Context'):ref.index('## Popup')] if '## /exec Context' in ref else ''
      var_names = re.findall(r'^\| `(\w+)`', exec_section, re.MULTILINE)
      if var_names:
        window.addline('[/exec variables: %s]' % ', '.join(var_names))
        window.addline('[Use /help variables <name> for details]')
      return
    if cmd in ('popups', 'popup'):
      window.addline('[Popup sections: [nicklist], [channel], [status], [query], [tab]]')
      window.addline('[Syntax: Menu Item:/command   .Child:/command   -  (separator)]')
      window.addline('[Variables: $nick, $me, $chan, $network, $server, $$1, $?="prompt"]')
      window.addline('[See Help > Reference Manual for full popup syntax]')
      return
    if cmd in ('plugin', 'plugins', 'api'):
      # Extract method names from the plugin.irc tables
      methods = re.findall(r'^\| `(\w+)` \|', ref[ref.index('## plugin.irc'):] if '## plugin.irc' in ref else '', re.MULTILINE)
      if methods:
        window.addline('[plugin.irc methods: %s]' % ', '.join(methods))
        window.addline('[Use irc.method(args) in plugins and /exec]')
      return
    if cmd in ('objects', 'object'):
      window.addline('[Objects: conn (IRCClient), User, Channel, Query, Client, Network]')
      window.addline('[See Help > Reference Manual for attributes and methods]')
      return
    if cmd in ('cli', 'commandline'):
      # Extract CLI flags from the table
      flags = re.findall(r'^\| `([^`]+)`', ref[ref.index('## Command Line'):ref.index('## Slash')] if '## Command Line' in ref else '', re.MULTILINE)
      if flags:
        window.addline('[CLI options: %s]' % ', '.join(flags))
      return

    # Find table rows for this command
    lines_found = []
    for m in re.finditer(
        r'^\| `/%s`\s*\|([^|]*)\|([^|]*)\|' % re.escape(cmd), ref, re.MULTILINE):
      syntax = m.group(1).strip().strip('`').strip()
      desc = m.group(2).strip()
      lines_found.append((syntax, desc))

    if not lines_found:
      window.redmessage('[No help for: /%s]' % cmd)
      return

    for syntax, desc in lines_found:
      window.addline('  %s — %s' % (syntax, desc))

    # Check for a detailed section (## /cmd or ### heading that mentions /cmd)
    section_pat = re.compile(
        r'^##+ .*/%s\b.*$' % re.escape(cmd), re.MULTILINE | re.IGNORECASE)
    m = section_pat.search(ref)
    if m:
      _show_help_section(window, ref, m.group(0), start_match=m)

  def alert(window, text):
    """Show a popup message box.  /alert [-t "title"] "message" """
    title = 'qtpyrc'
    if text.startswith('-t '):
      rest = text[3:].lstrip()
      title, rest = _split_quoted(rest)
      text = rest
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information(None, title, _unquote(text))

  def script(window, text):
    name = _unquote(text.strip())
    if not name:
      window.redmessage("[Error: /script requires a filename]")
      return
    run_script(name, window)

  def play(window, text):
    """Send a plain text file to the current window, line by line."""
    name = _unquote(text.strip())
    if not name:
      window.redmessage("[Error: /play requires a filename]")
      return
    path = _resolve_file(name)
    if not path:
      window.redmessage("[File not found: %s]" % name)
      return
    try:
      with open(path, 'r', encoding='utf-8') as f:
        for line in f:
          line = line.rstrip('\r\n')
          if line:
            docommand(window, 'say', line)
    except Exception as e:
      window.redmessage("[Error reading file: %s]" % e)

  # --- /alias ---

  def alias(window, text):
    """Define, list, or remove command aliases.

    /alias                       — list all aliases
    /alias <name> <command...>   — define alias (e.g. /alias j /join {-})
    /alias -r <name>             — remove alias
    """
    text = text.strip()
    if not text:
      # List aliases
      if not state._aliases:
        window.redmessage('[No aliases defined]')
        return
      for name, cmd in sorted(state._aliases.items()):
        window.redmessage('  /%s = %s' % (name, cmd))
      return
    parts = text.split(None, 1)
    if parts[0] == '-r':
      # Remove
      if len(parts) < 2:
        window.redmessage('[Usage: /alias -r <name>]')
        return
      name = parts[1].strip().lower().lstrip('/')
      if name in state._aliases:
        del state._aliases[name]
        window.redmessage('[Alias /%s removed]' % name)
      else:
        window.redmessage('[No alias named /%s]' % name)
      return
    if len(parts) < 2:
      # Show single alias
      name = parts[0].lower().lstrip('/')
      cmd = state._aliases.get(name)
      if cmd:
        window.redmessage('  /%s = %s' % (name, cmd))
      else:
        window.redmessage('[No alias named /%s]' % name)
      return
    name = parts[0].lower().lstrip('/')
    cmd = parts[1]
    state._aliases[name] = cmd
    window.redmessage('[Alias /%s = %s]' % (name, cmd))

  def set(window, text):
    """Define or list persistent user variables (saved to variables.ini).

    /set                       — list all variables (persistent + temporary)
    /set <name>                — show one variable
    /set <name> <value>        — set a persistent variable
    /set -r <name>             — remove a persistent variable

    Variables are expanded as {name} in commands, aliases, toolbar, and popups.
    See also: /var (temporary variables), /unset
    """
    text = text.strip()
    if not text:
      if not state._variables:
        window.redmessage('[No variables defined]')
        return
      for name, val in sorted(state._variables.items()):
        src = ' (temp)' if name in state._temp_vars else ''
        window.redmessage('  {%s} = %s%s' % (name, val, src))
      return
    parts = text.split(None, 1)
    if parts[0] == '-r':
      if len(parts) < 2:
        window.redmessage('[Usage: /set -r <name>]')
        return
      name = parts[1].strip()
      if name in state._persistent_vars:
        del state._persistent_vars[name]
        state._merge_variables()
        state.save_variables()
        window.redmessage('[Variable {%s} removed]' % name)
      else:
        window.redmessage('[No persistent variable named {%s}]' % name)
      return
    if len(parts) < 2:
      name = parts[0]
      val = state._variables.get(name)
      if val is not None:
        src = ' (temp)' if name in state._temp_vars else ''
        window.redmessage('  {%s} = %s%s' % (name, val, src))
      else:
        window.redmessage('[No variable named {%s}]' % name)
      return
    name = parts[0]
    val = parts[1]
    state._persistent_vars[name] = val
    state._merge_variables()
    state.save_variables()
    window.redmessage('[Set {%s} = %s]' % (name, val))

  def var(window, text):
    """Define a temporary variable (memory only, lost on exit).

    /var <name> <value>        — set a temporary variable
    /var -r <name>             — remove a temporary variable

    See also: /set (persistent variables)
    """
    text = text.strip()
    if not text:
      if not state._temp_vars:
        window.redmessage('[No temporary variables defined]')
        return
      for name, val in sorted(state._temp_vars.items()):
        window.redmessage('  {%s} = %s (temp)' % (name, val))
      return
    parts = text.split(None, 1)
    if parts[0] == '-r':
      if len(parts) < 2:
        window.redmessage('[Usage: /var -r <name>]')
        return
      name = parts[1].strip()
      if name in state._temp_vars:
        del state._temp_vars[name]
        state._merge_variables()
        window.redmessage('[Temp variable {%s} removed]' % name)
      else:
        window.redmessage('[No temp variable named {%s}]' % name)
      return
    if len(parts) < 2:
      window.redmessage('[Usage: /var <name> <value>]')
      return
    name = parts[0]
    val = parts[1]
    state._temp_vars[name] = val
    state._merge_variables()
    window.redmessage('[Temp {%s} = %s]' % (name, val))

  def unset(window, text):
    """Remove a variable (persistent or temporary).  /unset <name>"""
    name = text.strip()
    if not name:
      window.redmessage('[Usage: /unset <name>]')
      return
    removed = False
    if name in state._persistent_vars:
      del state._persistent_vars[name]
      state.save_variables()
      removed = True
    if name in state._temp_vars:
      del state._temp_vars[name]
      removed = True
    if removed:
      state._merge_variables()
      window.redmessage('[Variable {%s} removed]' % name)
    else:
      window.redmessage('[No variable named {%s}]' % name)

  def popups(window, text):
    """Reload the popups.ini file."""
    import popups as _popups_mod
    _popups_mod.load()
    window.redmessage('[Popups reloaded]')

  def settings(window, text):
    """Open the settings dialog.  /settings [page]"""
    from dialogs import open_settings
    page = text.strip().lower() if text.strip() else None
    open_settings(page=page)

  def ui(window, text):
    """Trigger a menu action or open a settings page.
    /ui <path>          — trigger the action at path
    /ui                 — list all registered paths
    /ui menu            — list menu.* paths
    /ui settings        — list settings.* paths
    /ui toolbar         — list toolbar.* paths
    Paths: menu.file.settings, menu.tools.colorpicker, settings.general, etc.
    Prefixes: menu.*, settings.*, toolbar.*"""
    from PySide6.QtGui import QAction
    path = text.strip().lower()
    reg = state.ui_registry
    # Exact match
    if path in reg:
      action = reg[path]
      if isinstance(action, QAction):
        if not action.isEnabled():
          window.redmessage('[%s is currently disabled]' % path)
          return
        action.trigger()
      elif callable(action):
        action()
      return
    # Prefix match — list everything matching the prefix (or all if empty)
    desc = state.ui_descriptions

    def _fmt(keys):
      if not keys:
        return
      width = max(len(k) for k in keys)
      for k in keys:
        d = desc.get(k, '')
        if d:
          window.addline('  %-*s  %s' % (width, k, d), state.defaultformat)
        else:
          window.addline('  ' + k, state.defaultformat)

    if not path:
      _fmt(sorted(reg.keys()))
      return
    matches = sorted(k for k in reg if k.startswith(path + '.')
                     or k.startswith(path))
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for m in matches:
      if m not in seen:
        seen.add(m)
        unique.append(m)
    if unique:
      _fmt(unique)
    else:
      window.redmessage('[Unknown UI path: %s]' % path)

  def config(window, text):
    """View or change a config option.  /config [-e] <key.path> [value]
    -e expands {variables} in the value before storing."""
    text = text.strip()
    if not text:
      window.redmessage('[Usage: /config [-e] <key.path> [value]]')
      window.redmessage('[Examples: /config font.family, /config font.size 15]')
      return
    expand = False
    if text.startswith('-e '):
      expand = True
      text = text[3:].lstrip()
    parts = text.split(None, 1)
    key_path = parts[0]
    value_str = parts[1] if len(parts) > 1 else None
    if expand and value_str:
      from config import _expand_vars
      variables = _window_context_vars(window)
      variables.update(state._variables)
      value_str = _expand_vars(value_str, variables)
    path_parts = key_path.split('.')
    cfg = state.config

    if value_str is None:
      # Show current value
      node = cfg._data
      for p in path_parts:
        if isinstance(node, dict) and p in node:
          node = node[p]
        else:
          window.redmessage('[%s is not set]' % key_path)
          return
      if isinstance(node, dict):
        for k, v in node.items():
          if isinstance(v, dict):
            window.redmessage('  %s.%s: {...}' % (key_path, k))
          elif isinstance(v, list):
            window.redmessage('  %s.%s: [%d items]' % (key_path, k, len(v)))
          else:
            window.redmessage('  %s.%s: %s' % (key_path, k, v))
      else:
        window.redmessage('[%s = %s]' % (key_path, node))
      return

    # Parse value using YAML for correct typing
    from io import StringIO
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
    yaml = YAML()
    try:
      parsed = yaml.load(StringIO(value_str))
    except Exception:
      parsed = value_str

    # Validate by applying to a deep copy
    import copy
    from config import AppConfig
    test_data = copy.deepcopy(cfg._data)
    node = test_data
    for p in path_parts[:-1]:
      if not isinstance(node, dict):
        window.redmessage('[Error: %s is not a section]' % p)
        return
      if p not in node or not isinstance(node.get(p), dict):
        node[p] = CommentedMap()
      node = node[p]
    node[path_parts[-1]] = parsed
    try:
      AppConfig(cfg.path, test_data, cfg._yaml)
    except Exception as e:
      window.redmessage('[Error: invalid value — %s]' % e)
      return

    # Apply to real config
    node = cfg._data
    for p in path_parts[:-1]:
      if p not in node or not isinstance(node.get(p), dict):
        node[p] = CommentedMap()
      node = node[p]
    node[path_parts[-1]] = parsed

    # Re-initialize config and update text formats
    AppConfig.__init__(cfg, cfg.path, cfg._data, cfg._yaml)
    from config import _update_text_formats
    _update_text_formats(cfg)
    cfg.save()
    window.redmessage('[Set %s = %s]' % (key_path, parsed))

  def title(window, text):
    """Set or clear a custom window title.
    /title [text]        — set current window title (no args to clear)
    /title -s [text]     — set server window title
    /title -a [text]     — set app window title (not saved to config)"""
    text = text.strip()
    if text.startswith('-s'):
      # Target the server window
      target = window.client.window if window.client else window
      text = _unquote(text[2:].lstrip())
      if text:
        target.set_custom_title(text)
      else:
        target.clear_custom_title()
    elif text.startswith('-a'):
      # Target the app main window — stored as a runtime override
      text = _unquote(text[2:].lstrip())
      if text:
        state.app.mainwin._custom_titlebar = text
      else:
        state.app.mainwin._custom_titlebar = None
      from qtpyrc import _update_all_titles
      _update_all_titles()
    elif text:
      window.set_custom_title(_unquote(text))
    else:
      window.clear_custom_title()

  def newserver(window, text):
    """Open a new server window."""
    from models import newclient
    newclient()

  def toolbar(window, text):
    """Reload the toolbar from toolbar.ini."""
    from toolbar import reload_toolbar
    reload_toolbar()
    window.redmessage('[Toolbar reloaded]')

  def icons(window, text):
    """Browse available toolbar icons."""
    from toolbar import show_icon_browser
    show_icon_browser()

  def sounds(window, text):
    """List and preview system sounds.

    /sounds          — open the sound browser dialog
    /sounds <name>   — play a sound by name
    """
    text = _unquote(text.strip())
    if text:
      # Play a sound by name
      from notify import resolve_sound_name
      path = resolve_sound_name(text)
      if path and state.notifications:
        state.notifications._play_sound(text)
      else:
        window.redmessage('[Sound not found: %s]' % text)
      return
    from notify import show_sound_browser
    show_sound_browser()

  def urls(window, text):
    """Open the URL catcher dialog.  /urls"""
    from url_catcher import show_url_catcher
    show_url_catcher()

  urlcatcher = urls

  def away(window, text):
    """Set or clear away status.  /away [message]"""
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    msg = _unquote(text.strip())
    if msg:
      conn.sendLine('AWAY :%s' % msg)
    else:
      conn.sendLine('AWAY')


def _unquote(s):
  """Strip matching quotes from a string."""
  if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
    return s[1:-1]
  return s


def _split_quoted(s):
  """Extract the first quoted or unquoted token from *s*.
  Returns (token, rest) where token has quotes stripped."""
  s = s.lstrip()
  if s and s[0] in ('"', "'"):
    q = s[0]
    end = s.find(q, 1)
    if end >= 0:
      return s[1:end], s[end + 1:].lstrip()
  parts = s.split(None, 1)
  return (parts[0] if parts else '', parts[1] if len(parts) > 1 else '')


def _show_help_section(window, ref, heading, start_match=None):
  """Display a section from reference.md in the window."""
  import re as _re
  if start_match:
    m = start_match
  else:
    m = _re.search(_re.escape(heading), ref, _re.MULTILINE)
  if not m:
    return
  level = m.group(0).count('#', 0, m.group(0).index(' '))
  rest = ref[m.end():]
  end_pat = _re.compile(r'^#{1,%d} [^#]' % level, _re.MULTILINE)
  end_m = end_pat.search(rest)
  section = rest[:end_m.start()] if end_m else rest
  section_lines = []
  in_code = False
  for line in section.splitlines():
    if line.strip().startswith('```'):
      in_code = not in_code
      continue
    clean = line.rstrip()
    clean = _re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
    clean = _re.sub(r'`(.+?)`', r'\1', clean)
    if clean.strip():
      section_lines.append('  ' + clean)
  if len(section_lines) > 40:
    section_lines = section_lines[:40]
    truncated = True
  else:
    truncated = False
  for line in section_lines:
    window.addline(line)
  if truncated:
    window.addline('  ... (Help > Reference Manual for full details)')


def _show_event_help(window, ref, event_name, re):
  """Show help for a specific /on event."""
  event_m = re.search(
      r'^\| `%s` \|([^|]*)\|([^|]*)\|' % re.escape(event_name), ref, re.MULTILINE)
  if not event_m:
    window.redmessage('[Unknown event: %s]' % event_name)
    return
  fires = event_m.group(1).strip()
  match_text = event_m.group(2).strip()
  window.addline('  %s — fires: %s, matches: %s' % (event_name, fires, match_text))
  var_m = re.search(
      r'^\| `%s` \|([^|]*)\|' % re.escape(event_name),
      ref[ref.index('{Variables} by'):] if '{Variables} by' in ref else '',
      re.MULTILINE)
  if var_m:
    window.addline('  variables: %s' % var_m.group(1).strip())
  window.addline('  (all events also have {network} and {me})')


def _find_client(name):
  """Find a Client by network key, network name, or hostname (case-insensitive)."""
  name_lower = name.lower()
  for c in state.clients:
    if (name_lower == (c.network_key or '').lower()
        or name_lower == (c.network or '').lower()
        or name_lower == (getattr(c, 'hostname', '') or '').lower()):
      return c
  return None


def _find_window(name, client=None):
  """Find an open window by name.  Searches channels, queries, and server
  windows across all clients (or just the given client).
  Returns the window object or None."""
  clients = [client] if client else state.clients
  for c in clients:
    if not c.conn:
      continue
    lower = c.conn.irclower(name)
    # Channel
    if lower in c.channels and c.channels[lower].window:
      return c.channels[lower].window
    # Query (with or without = prefix)
    qname = lower.lstrip('=')
    for qkey, q in c.queries.items():
      if qkey.split(':')[0].lstrip('=') == qname and q.window:
        return q.window
    # Server window (match network key or hostname)
    if c.window:
      nk = (c.network_key or '').lower()
      hn = (c.hostname or '').lower()
      nn = (c.network or '').lower()
      if name.lower() in (nk, hn, nn):
        return c.window
  return None


def _resolve_cmdscripts_dir():
  """Return the absolute path to the command scripts directory."""
  import os
  d = state.config.cmdscripts_dir
  if os.path.isabs(d):
    return d
  return os.path.join(os.path.dirname(os.path.abspath(state.config.path)), d)


def _resolve_file(name, search_dir=None):
  """Find a file by name: try as absolute/relative path, then in search_dir,
  then in search_dir with .rc extension appended."""
  import os
  if os.path.isfile(name):
    return name
  if search_dir:
    path = os.path.join(search_dir, name + '.rc')
    if os.path.isfile(path):
      return path
    path = os.path.join(search_dir, name)
    if os.path.isfile(path):
      return path
  return None


def _remove_from_startup(event, hookname, window=None):
  """Remove an /on line for the given event and hookname from the startup script.
  Returns True if a line was removed."""
  import os, re
  from qtpyrc import _startup_path
  path = _startup_path()
  if not path:
    _wmsg(window, "No startup script configured")
    return False
  if not os.path.isfile(path):
    _wmsg(window, "Startup script not found: %s" % path)
    return False
  try:
    with open(path, 'r', encoding='utf-8') as f:
      lines = f.readlines()
    # Match /on <event> <hookname> or /on <event> "<hookname>" at start of line
    pat = re.compile(
      r'^\s*/on\s+%s\s+(?:"%s"|\'%s\'|%s)\b' % (
        re.escape(event), re.escape(hookname),
        re.escape(hookname), re.escape(hookname)),
      re.IGNORECASE)
    new_lines = [l for l in lines if not pat.match(l)]
    if len(new_lines) == len(lines):
      _wmsg(window, 'Hook "%s" not found in startup script' % hookname)
      return False
    with open(path, 'w', encoding='utf-8') as f:
      f.writelines(new_lines)
    return True
  except Exception as e:
    _wmsg(window, "Error updating startup script: %s" % e)
    return False


def _persist_to_startup(line, window=None):
  """Append a command line to the startup script. Returns True on success."""
  import os
  from qtpyrc import _startup_path
  path = _startup_path()
  if not path:
    _wmsg(window, "No startup script configured")
    return False
  try:
    # Ensure directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Append with newline, ensuring we start on a new line
    tail = ''
    if os.path.isfile(path):
      with open(path, 'rb') as f:
        f.seek(0, 2)
        if f.tell() > 0:
          f.seek(-1, 2)
          if f.read(1) != b'\n':
            tail = '\n'
    with open(path, 'a', encoding='utf-8') as f:
      f.write('%s%s\n' % (tail, line))
    return True
  except Exception as e:
    _wmsg(window, "Error writing startup script: %s" % e)
    return False


def _wmsg(window, text):
  """Show a warning in a window, or as a popup if no window is available."""
  if window:
    window.redmessage('[%s]' % text)
  else:
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.warning(None, 'qtpyrc', text)


def run_script(name, window=None):
  """Run a command script file.  Each line is executed as a command."""
  import os
  path = _resolve_file(name, _resolve_cmdscripts_dir())
  if not path:
    if window:
      window.redmessage("[Script not found: %s]" % name)
    return False
  try:
    with open(path, 'r', encoding='utf-8') as f:
      lines = f.readlines()
  except Exception as e:
    if window:
      window.redmessage("[Error reading script: %s]" % e)
    return False
  win = window
  for line in lines:
    line = line.strip()
    if not line or line.startswith(';'):
      continue
    # Use the active window at time of execution for each line
    if not win:
      win = getattr(getattr(state, 'app', None), 'mainwin', None)
      if win:
        win = getattr(win, 'workspace', None)
        if win:
          win = getattr(win, 'activeSubWindow', lambda: None)()
          if win:
            win = win.widget()
    if not win:
      continue
    prefix = state.config.cmdprefix
    if line.startswith('/'):
      parts = line[1:].split(' ', 1)
      docommand(win, parts[0], parts[1] if len(parts) > 1 else '')
    elif prefix != '/' and line.startswith(prefix):
      parts = line[len(prefix):].split(' ', 1)
      docommand(win, parts[0], parts[1] if len(parts) > 1 else '')
    else:
      docommand(win, 'say', line)
  return True


def _expand_alias(command, text):
  """If *command* is a user alias, expand it and return (cmd, args).
  Returns None if not an alias."""
  alias_body = state._aliases.get(command)
  if alias_body is None:
    return None
  # Split user args for positional substitution
  args = text.split() if text else []
  import re
  has_placeholder = bool(re.search(r'\{(\d+|-)\}', alias_body))
  if has_placeholder:
    def _repl(m):
      tok = m.group(1)
      if tok == '-':
        return text  # all args
      idx = int(tok) - 1  # {1} = first arg
      return args[idx] if 0 <= idx < len(args) else ''
    expanded = re.sub(r'\{(\d+|-)\}', _repl, alias_body)
  else:
    # No placeholders — append all args
    expanded = alias_body + (' ' + text if text else '')
  # The expanded string is a full command line (possibly with prefix)
  prefix = state.config.cmdprefix
  if expanded.startswith(prefix):
    expanded = expanded[len(prefix):]
  parts = expanded.split(None, 1)
  if not parts:
    return None
  return parts[0], parts[1] if len(parts) > 1 else ''


def _window_context_vars(window):
  """Build built-in {variable} dict from the active window context.

  Keys are bare names (e.g. 'me', not '{me}') matching _expand_vars lookups.
  """
  v = {}
  client = getattr(window, 'client', None)
  conn = client.conn if client else None
  # {me}: current nick — resolve per-network when not connected
  if conn:
    v['me'] = conn.nickname
  elif client and client.network_key and state.config:
    v['me'] = state.config.resolve(client.network_key, 'nick') or ''
  else:
    v['me'] = state.config.nick if state.config else ''
  v['network_key'] = (client.network_key or '') if client else ''
  # {network_label}: fallback chain for display — network_key > name > hostname > 'unknown'
  if client:
    v['network_label'] = client.network_key or client.network or getattr(client, 'hostname', '') or 'unknown'
  else:
    v['network_label'] = ''
  if hasattr(window, 'channel') and window.channel:
    v['channel'] = window.channel.name or ''
    v['topic'] = window.channel.topic or ''
    v['nicks'] = str(len(window.channel.nicks))
  elif hasattr(window, 'query') and window.query:
    v['channel'] = window.query.nick or ''
    v['topic'] = ''
    v['nicks'] = ''
  else:
    v['channel'] = ''
    v['topic'] = ''
    v['nicks'] = ''
  # {query_nick}: the query peer nick (only set for query windows)
  if hasattr(window, 'query') and window.query:
    v['query_nick'] = window.query.nick or ''
  else:
    v['query_nick'] = ''
  v['network_hostname'] = (getattr(client, 'hostname', '') or '') if client else ''
  v['port'] = str(getattr(client, 'port', '')) if client else ''
  # Own user info from the server
  if conn:
    own = client.users.get(conn.irclower(conn.nickname))
    if own:
      v['ident'] = own.ident or ''
      v['host'] = own.host or ''
      v['address'] = '%s!%s@%s' % (own.nick, own.ident or '', own.host or '')
    else:
      v['ident'] = ''
      v['host'] = ''
      v['address'] = ''
  else:
    v['ident'] = ''
    v['host'] = ''
    v['address'] = ''
  v['realname'] = conn.realname if conn else (state.config.realname if state.config else '')
  v['sasl_username'] = ''
  if conn:
    own = client.users.get(conn.irclower(conn.nickname))
    if own and own.account:
      v['sasl_username'] = own.account
  v['connected'] = 'true' if (client and client.connected) else 'false'
  v['tls'] = 'true' if (client and client.tls) else 'false'
  v['key'] = ''
  if hasattr(window, 'channel') and window.channel and window.channel.key:
    v['key'] = window.channel.key
  v['network_name'] = getattr(conn, '_network_name', '') or '' if conn else ''
  v['window_type'] = getattr(window, 'type', '')
  v['networks'] = str(sum(1 for c in state.clients if c.hostname))
  v['channels'] = str(sum(len(c.channels) for c in state.clients))
  return v


def expand_window_title(fmt, window):
  """Expand a title format string using context from the given window."""
  from config import _expand_vars
  variables = _window_context_vars(window)
  variables.update(state._variables)
  return _expand_vars(fmt, variables, allow_eval=True, eval_ns={'state': state})


def docommand(window, command, text=""):
  command = command.strip().lower()
  # Expand {variables} in the argument text (skip for /set which stores raw values)
  if text and command not in ('config', 'title'):
    from config import _expand_vars
    variables = _window_context_vars(window)
    variables.update(state._variables)  # user vars override built-ins
    if variables:
      text = _expand_vars(text, variables)
  # Map keywords that can't be method names
  if command == 'exec':
    command = 'exec_'
  if hasattr(Commands, command) and not command.startswith("_"):
    getattr(Commands, command)(window, text)
  else:
    # Try user-defined alias
    result = _expand_alias(command, text)
    if result:
      docommand(window, result[0], result[1])
    else:
      window.redmessage("[Unknown command: /%s]" % command)
