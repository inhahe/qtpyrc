# commands.py - Commands class and docommand()

import traceback

import state
from config import (_modify_list_entry, split_mask, expand_mask,
                    list_all_entries, remove_entry_everywhere, scope_label,
                    LIST_ADDED, LIST_ALREADY, LIST_REMOVED, LIST_NOT_FOUND,
                    LIST_NO_NETWORK)


def _parse_list_flags(text, known):
  """Split *text* into `(flags, positional)`.

  Raises `ValueError(token)` if a `-`-prefixed token is not made up entirely of
  known flag letters.  `--` ends the flags; everything after it is positional,
  which is how a value that really does begin with `-` is passed.

  **An unrecognised flag is an error, never a value.** The parser this replaces
  kept any `-x` whose letters were not all alphabetic as a *positional*
  argument, so `/aop -?` -- someone looking for a usage line -- was read as
  "add an auto-op entry for the mask `-?`", written to the config, and answered
  with `[Added auto-op (network undernet): -?]`. It is still in the config that
  prompted this fix. Because `?` is a wildcard, that entry then auto-opped
  every two-character nick beginning with `-`.
  """
  flags = set()
  positional = []
  end_of_flags = False
  for a in text.split():
    if end_of_flags or not a.startswith('-') or a == '-':
      positional.append(a)
      continue
    if a == '--':
      end_of_flags = True
      continue
    body = a[1:]
    if [c for c in body if c not in known]:
      raise ValueError(a)
    flags.update(body)
  return flags, positional


def _breadth_warning(mask, cmd):
  """What does *mask* actually grant, if that is broader than it looks? Or None.

  Used only where the list confers a *privilege* -- i.e. `/aop` -- because
  there the gap between what a mask appears to say and what it matches is the
  difference between naming one person and naming a set you did not choose.
  Two gaps are worth naming, and they want different advice:

  - **Every component pure `*`.** `*!*@*` ops the entire channel.  Deliberately
    not triggered by a mask that is merely broad in the *host*: `bob!*@*` is
    the ordinary way to write "bob, wherever he connects from", and a warning
    that fires on the ordinary case stops being read.
  - **No host component at all** (`hegemon`, `bob*`).  A nick is not an
    identity: it is released on quit, is free for the taking during a
    netsplit, and belongs to whoever grabs it first if it is not registered.
    So a nick-only auto-op means "op whoever holds this name", which is not
    what it looks like it means -- the user who prompted this fix read exactly
    such an entry as "hegemon, from hegemon's host".
  """
  def anything(part):
    return part != '' and set(part) <= set('*')
  nick, ident, host = split_mask(mask)
  everyone = ('%s matches EVERY user. Every person who joins will be opped. '
              'Remove it with: /%s -r %s' % (mask, cmd, mask))
  if nick is None:
    if anything(mask):
      return everyone
    return ('%s is a nick with no host, so it ops whoever holds the nick "%s" '
            'from ANY host -- including someone who takes it during a netsplit '
            'or after a quit. Anchor it to a host: /%s %s!*@<host>'
            % (mask, mask, cmd, mask))
  if anything(nick) and anything(ident) and anything(host):
    return everyone
  return None


def _show_all_entries(window, list_key, title, annotate=None, expand=True):
  """Print every entry for *list_key* at every scope, labelled with its scope.

  **Deliberately ignores the current window, and has no narrowing flag.** The
  list this replaces called `get_auto_ops(network_key, channel)` -- the same
  context-sensitive collector the auto-op *check* uses -- and additionally
  honoured `-w`, which does not mean "all networks" but "the global scope
  only". So `/aop -lw`, the natural way to spell "show me everything", answered
  "[Auto-op list is empty]" from *every* window, including the channel window
  of the channel whose three live entries were opping people. A list you cannot
  trust to be complete is worse than no list, because it is used to conclude
  that something is *not* configured.

  Every list command in this file goes through here, `/notify` included, so
  that the fix cannot be half-applied -- `/notify -l` had exactly the `-w` bug
  above, still live, after `/aop -l` was fixed.

  `annotate(network_key, channel, entry)` adds trailing text per entry (used
  for `/notify`'s online/offline state).  `expand=False` turns off the
  `nick!ident@host` expansion note for lists whose entries are not masks.
  """
  entries = list_all_entries(list_key)
  if not entries:
    window.redmessage('[%s is empty (checked every network and channel)]' % title)
    return
  window.redmessage('[%s -- %d entr%s across all scopes:]'
                    % (title, len(entries), 'y' if len(entries) == 1 else 'ies'))
  width = max(len(scope_label(nk, ch)) for nk, ch, _ in entries)
  mwidth = max(len(m) for _, _, m in entries)
  for nk, ch, mask in entries:
    note = ''
    if expand:
      full = expand_mask(mask)
      if full != mask:
        note = '  (matches %s)' % full
    if annotate:
      note += annotate(nk, ch, mask)
    window.redmessage('[    %-*s  %-*s%s]'
                      % (width, scope_label(nk, ch), mwidth if note else 0,
                         mask, note))


def _mask_list_command(window, text, list_key, title, noun, warn_broad=False):
  """The shared body of /ignore and /aop.

  Both manage a wildcard-mask list that exists at three scopes and is *read*
  additively across all three, so both had the same three bugs: a list that
  showed only the scopes visible from the current window, a remove aimed at one
  guessed scope, and a success message printed without asking whether anything
  had changed. Keeping one implementation is what stops them diverging again --
  and `/aop` is the one where the consequence of a stale entry is someone else
  holding operator status in your channel.
  """
  try:
    flags, positional = _parse_list_flags(text, 'lrw')
  except ValueError as e:
    window.redmessage('[Unknown option %s. Options: -l list, -r remove, '
                      '-w global. Use -- before a value starting with "-".]'
                      % e.args[0])
    return

  # List mode: everything, everywhere.  There is no narrowing flag on purpose;
  # the whole failure this replaces was a list that showed a subset.  `-w` used
  # to narrow it to the global scope, which is how the reported "[Auto-op list
  # is empty]" was produced while four entries were live -- so it is now
  # inert here, and *says* it is inert.  Silently ignoring a flag the user
  # typed would reproduce the original fault in the other direction: an answer
  # given to a question other than the one asked, without saying so.
  if 'l' in flags or not positional:
    if 'w' in flags:
      window.redmessage('[-w does not narrow the list; every scope is shown, '
                        'each labelled with where it is configured]')
    _show_all_entries(window, list_key, title)
    return

  mask = positional[0]
  remove = 'r' in flags

  # Explicit scope, if the user named one.
  chan_arg = None
  net_arg = None
  for p in positional[1:]:
    if p.startswith('#'):
      chan_arg = p.split(',')[0]
    elif state.config.networks and p in state.config.networks:
      net_arg = p
  explicit_scope = bool(chan_arg) or 'w' in flags or bool(net_arg)

  if remove:
    if not explicit_scope:
      # No scope named: take it out of every scope it is in.  See
      # config.remove_entry_everywhere for why that is the safe default.
      hits = remove_entry_everywhere(list_key, mask)
      if not hits:
        window.redmessage('[No %s entry matching %s anywhere -- nothing removed]'
                          % (noun, mask))
        return
      for nk, ch in hits:
        window.redmessage('[Removed %s (%s): %s]'
                          % (noun, scope_label(nk, ch), mask))
      return
    nk = None if 'w' in flags else (net_arg or _net_key(window))
    ch = None if 'w' in flags else chan_arg
    res = _modify_list_entry(list_key, mask, True, nk, ch)
    if res == LIST_REMOVED:
      window.redmessage('[Removed %s (%s): %s]'
                        % (noun, scope_label(nk, ch), mask))
    else:
      window.redmessage('[No %s entry %s at %s -- nothing removed]'
                        % (noun, mask, scope_label(nk, ch)))
    # Whatever happened, say where else it still lives: a remove that leaves a
    # live copy behind is the exact failure this command is being fixed for.
    left = [(n, c) for n, c, m in list_all_entries(list_key)
            if m.lower() == mask.lower()]
    for n, c in left:
      window.redmessage('[  still %s at %s -- remove it with: /%s -r %s]'
                        % (noun, scope_label(n, c), _cmd_for(list_key), mask))
    return

  # Add.
  if 'w' in flags:
    nk = ch = None
  else:
    nk = net_arg or _net_key(window)
    ch = chan_arg or (window.channel.name if window.type == 'channel' else None)

  res = _modify_list_entry(list_key, mask, False, nk, ch)
  if res == LIST_NO_NETWORK:
    window.redmessage('[Cannot add %s: network %r is not in the config. '
                      'Use -w to add it globally.]' % (noun, nk))
    return
  if res == LIST_ALREADY:
    window.redmessage('[%s already listed at %s: %s]'
                      % (noun.capitalize(), scope_label(nk, ch), mask))
  else:
    window.redmessage('[Added %s (%s): %s]' % (noun, scope_label(nk, ch), mask))
  # Echo the expansion whenever it differs from what was typed.  `-l` shows it
  # too, but the moment a user forms their idea of what an entry means is the
  # moment they add it, and a two-component mask is genuinely ambiguous on
  # sight: `hegemon@host` is read as *nick*@host, while the same text in a
  # /whois line means *ident*@host.  Saying "matches hegemon!*@host" answers
  # which of the two it was without the user having to go and ask.
  full = expand_mask(mask)
  if full != mask:
    window.redmessage('[  matches %s]' % full)
  if warn_broad:
    warning = _breadth_warning(mask, _cmd_for(list_key))
    if warning:
      window.redmessage('[WARNING: %s]' % warning)


def _cmd_for(list_key):
  return {'auto_ops': 'aop', 'ignores': 'ignore'}.get(list_key, list_key)


def _net_key(window):
  return window.client.network_key if window.client else None


class Commands:

  def join(window, text):
    args = text.split(None)
    if not args:
      window.redmessage('[Usage: /join [-n] [-z] <channel> [key]  or  /join <network>/<channel> [key]]')
      return
    # Parse flags
    no_net = False
    no_activate = False
    while args and args[0].startswith('-'):
      flag = args.pop(0)
      for ch in flag[1:]:
        if ch == 'n':
          no_net = True
        elif ch == 'z':
          no_activate = True
    if not args:
      window.redmessage('[Usage: /join [-n] [-z] <channel> [key]]')
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
        if not no_activate:
          state.app.mainwin.workspace.setActiveSubWindow(chan.window.subwindow)
        return
      # Mark as user-initiated so persist_autojoins only fires for /join
      conn._user_joins.add(chnlower)
      if not no_activate:
        conn._activate_on_join.add(chnlower)
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
    # Don't _unquote here — plain messages typed in the input field route
    # through `say`, and stripping surrounding quotes mangles things like
    # `"hello"` into `hello`. Quoting only matters for commands that take
    # named arguments.
    if window.type == "server":
      window.redmessage("[Error: Can't talk in a server window]")
    elif window.type in ("channel", "query"):
      conn = window.client.conn if window.client else None
      if not conn:
        window.redmessage('[Not connected]')
        return
      target = window.channel.name if window.type == "channel" else window.remotenick
      # display_window is this window explicitly: the user typed here, so the
      # echo belongs here even in the odd case where the target resolves to some
      # other window.
      send_message(window, conn, target, text, display_window=window)

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
    for chan in list(window.client.channels.values()):
      send_message(window, conn, chan.name, text, display_window=chan.window)

  def msg(window, text):
    parts = text.split(" ", 1)
    recip = parts[0]
    if len(parts) < 2 or not parts[1]:
      # No message text — open/focus the query window
      Commands.query(window, recip)
      return
    text = _unquote(parts[1])
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    send_message(window, conn, recip, text)

  def me(window, text):
    """/me <action> — send a CTCP ACTION to the current channel or query."""
    text = _unquote(text).strip()
    if not text:
      window.redmessage('[Usage: /me <action>]')
      return
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    send_action(window, conn, text)
  action = me

  def notice(window, text):
    """/notice <target> <message> — send a NOTICE."""
    parts = text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
      window.redmessage('[Usage: /notice <target> <message>]')
      return
    target = parts[0]
    msg_text = _unquote(parts[1])
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    from irc_client import notice_log_line
    chunks = conn.split_message(target, msg_text)
    for chunk in chunks:
      conn.notice(target, chunk)
      window.addline_nick(["-", (conn.nickname,), "- %s" % chunk], state.noticeformat)
      # Logged under *target*, which is the same file the reply will land in
      # (irc_client.noticed files an incoming notice under its sender).  Both
      # directions of one notice conversation therefore end up in one file, in
      # order -- the thing the /msg bug got wrong.  Note this is deliberately
      # NOT the window the line was drawn in: /notice echoes into whatever
      # window you typed it in, which is not a stable place to file anything.
      state.irclogger.log(conn._log_network, target,
                          notice_log_line(conn.nickname, chunk))

  def quit(window, text):
    conn = window.client.conn
    if not conn:
      window.redmessage('[Not connected]')
      return
    window.client._intentional_disconnect = True
    msg = _unquote(text.strip()) if text.strip() else 'Leaving'
    conn.quit(msg)

  disconnect = quit

  def exit(window, text):
    """Exit the application. In GUI mode, closes the main window.
    In headless mode, stops the event loop.
    /exit [quit_message]"""
    # Disconnect all clients first
    msg = _unquote(text.strip()) if text.strip() else 'Leaving'
    for client in (state.clients or []):
      if client.conn:
        client._intentional_disconnect = True
        client.conn.quit(msg)
    # Exit
    import asyncio
    loop = asyncio.get_event_loop()
    if hasattr(state.app, 'mainwin') and hasattr(state.app.mainwin, 'close'):
      from PySide6.QtWidgets import QApplication
      QApplication.instance().quit()
    else:
      # Headless
      loop.call_soon(loop.stop)

  def server(window, text):
    """/server [switches] [host[:[+*]port]] [password]
    Switches: -m -n -z -e -t -d -o -c -u -4 -6 -46
      -l <method> [password]  -nick <n>  -altnick <n>  -user <u>
      -realname <name>  -w <password>
    See /help server for details."""
    opts = _parse_server_args(text)
    if opts.get('_error'):
      window.redmessage('[/server: %s]' % opts['_error'])
      return

    flags = opts.get('flags', set())

    # -m or -n: create new server window
    if 'm' in flags or 'n' in flags:
      from models import Client
      netkey = opts.get('network_key')
      client = Client(network_key=netkey)
      state.clients.add(client)
      if 'z' not in flags:
        ws = state.app.mainwin.workspace
        ws.setActiveSubWindow(client.window.subwindow)
    else:
      client = window.client

    # If host looks like a network key, resolve it
    host = opts.get('host')
    if host and not opts.get('port'):
      networks = state.config.networks or {}
      for key in networks:
        if key.lower() == host.lower():
          opts['network_key'] = key
          # If there's already a client for this network, use it instead of
          # repurposing the current client (which would create a hidden
          # duplicate connection).
          if 'm' not in flags and 'n' not in flags:
            for c in (state.clients or []):
              if c.network_key and c.network_key.lower() == key.lower():
                client = c
                break
          client.net.key = key
          client._server_list = state.config.get_servers(key)
          if client._server_list:
            client._apply_server(client._server_list[0])
          host = None
          opts['host'] = None
          break

    # No host and no network: reconnect to last server
    if not host and not opts.get('network_key') and 'n' not in flags:
      if client.hostname:
        client.reconnect()
      elif 'm' not in flags:
        window.redmessage('[Usage: /server [switches] <host>[:<port>] | /server <network>]')
      return

    # -d: set details without connecting
    if 'd' in flags:
      if host:
        client.hostname = host
      if opts.get('port'):
        client.port = opts['port']
      if 'e' in flags or opts.get('tls'):
        client.tls = True
      return

    # -n: don't connect
    if 'n' in flags:
      if host:
        client.hostname = host
      if opts.get('port'):
        client.port = opts['port']
      return

    # Build overrides dict
    overrides = {}
    if opts.get('tls') is not None:
      overrides['tls'] = opts['tls']
    if opts.get('starttls'):
      overrides['starttls'] = True
    if opts.get('ip_version'):
      overrides['ip_version'] = opts['ip_version']
    if opts.get('nick'):
      overrides['nick'] = opts['nick']
    if opts.get('altnicks'):
      overrides['altnicks'] = opts['altnicks']
    if opts.get('user'):
      overrides['user'] = opts['user']
    if opts.get('realname'):
      overrides['realname'] = opts['realname']
    if opts.get('login_method'):
      overrides['login_method'] = opts['login_method']
    if opts.get('login_password'):
      overrides['login_password'] = opts['login_password']
    if 'o' in flags:
      overrides['skip_autojoin'] = True
    if 'c' in flags:
      overrides['skip_on_connect'] = True
    if 'u' in flags:
      overrides['bypass_sts'] = True

    tls_override = opts.get('tls')
    if tls_override is not None:
      client.tls = tls_override

    client.reconnect(
      hostname=host,
      port=opts.get('port'),
      password=opts.get('password'),
      **overrides
    )

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

  def ping(window, text):
    target = text.strip()
    if not target:
      window.redmessage('[Usage: /ping <nick>]')
      return
    Commands.ctcp(window, '%s PING' % target)

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

  def whowas(window, text):
    target = text.strip()
    if not target:
      window.redmessage("[Error: /whowas requires a nick]")
      return
    if not window.client.conn:
      window.redmessage("[Error: not connected]")
      return
    window.client.conn.do_whowas(target, window)

  def who(window, text):
    target = text.strip()
    if not target:
      if window.type == 'channel' and window.channel:
        target = window.channel.name
      else:
        window.redmessage("[Usage: /who <channel|mask>]")
        return
    if not window.client.conn:
      window.redmessage("[Error: not connected]")
      return
    window.client.conn.do_who(target, window)

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
      from plugins import unload_plugin
      if not unload_plugin(name):
        if flag == '-u':
          window.redmessage("[Plugin \"%s\" is not loaded]" % name)
          return
      elif flag == '-u':
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
    # Listed per directory of the search path, and the directory is named on
    # each -- with a search path, "which plugin is this?" has an answer only if
    # you can see where it came from.
    from plugins import plugin_search_path, available_plugins
    dirs = plugin_search_path()
    winners = available_plugins(dirs)
    shown = False
    for d in dirs:
      here = available_plugins([d])
      if not here:
        continue
      shown = True
      window.redmessage("[Plugins in %s:]" % d)
      for name in sorted(here):
        notes = []
        if name in state.activescripts:
          notes.append('loaded')
        if winners[name].path != here[name].path:
          notes.append('shadowed by %s' % winners[name].path)
        window.redmessage(
          "  %s%s" % (name, (' (' + ', '.join(notes) + ')') if notes else ''))
    if not shown:
      window.redmessage("[No plugins in %s]" % ', '.join(dirs))

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
    -l list (every scope)  -r remove  -w global (any network)
    Without flags, adds to the current channel if in one, else the network.
    -r with no scope removes the mask from every scope it is in."""
    _mask_list_command(window, text, 'ignores', 'Ignore list', 'ignore')

  def highlight(window, text):
    """Manage highlight patterns.  /highlight [-lrw] [pattern]
    -l list  -r remove  -w top-level (any network)
    Without flags, adds to the current network (or channel if in one).
    Plain strings are case-insensitive. Use /regex/ or /regex/i for regex."""
    from config import modify_highlight_entry
    try:
      flags, positional = _parse_list_flags(text, 'lrw')
    except ValueError as e:
      window.redmessage('[Unknown option %s. Options: -l list, -r remove, '
                        '-w global. Use -- before a pattern starting with "-".]'
                        % e.args[0])
      return

    # List mode: every scope, for the same reason /aop -l does.  expand=False
    # because a highlight is a substring or a /regex/, not a hostmask -- one
    # containing '@' must not be reported as matching some `nick!ident@host`.
    if 'l' in flags or not positional:
      if 'w' in flags:
        window.redmessage('[-w does not narrow the list; every scope is shown, '
                          'each labelled with where it is configured]')
      _show_all_entries(window, 'highlights', 'Highlights', expand=False)
      return

    # The pattern may contain spaces (e.g. /multi word regex/)
    # so rejoin positional args
    pattern = ' '.join(positional)
    remove = 'r' in flags

    if remove:
      hits = remove_entry_everywhere('highlights', pattern)
      if not hits:
        window.redmessage('[No highlight matching %s anywhere -- nothing '
                          'removed]' % pattern)
        return
      for nk, ch in hits:
        window.redmessage('[Removed highlight (%s): %s]'
                          % (scope_label(nk, ch), pattern))
      return

    if 'w' in flags:
      nk = ch = None
    else:
      nk = _net_key(window)
      ch = window.channel.name if window.type == "channel" else None

    res = modify_highlight_entry(pattern, remove, nk, ch)
    if res == LIST_NO_NETWORK:
      window.redmessage('[Cannot add highlight: network %r is not in the '
                        'config. Use -w to add it globally.]' % nk)
    elif res == LIST_ALREADY:
      window.redmessage('[Highlight already listed at %s: %s]'
                        % (scope_label(nk, ch), pattern))
    else:
      window.redmessage('[Added highlight (%s): %s]'
                        % (scope_label(nk, ch), pattern))

  def notify(window, text):
    """Manage the nick watch list.  /notify [-lrw] [nick]
    -l list  -r remove  -w global (any network)
    Without flags, adds nick to the current network's notify list."""
    from config import modify_notify_entry
    try:
      flags, positional = _parse_list_flags(text, 'lrw')
    except ValueError as e:
      window.redmessage('[Unknown option %s. Options: -l list, -r remove, '
                        '-w global.]' % e.args[0])
      return

    nk = None if 'w' in flags else _net_key(window)

    # List mode: every scope, for the same reason /aop -l does.  This used to
    # be `get_notify_list(nk)` with `nk = None` for -w, so `/notify -lw` showed
    # only the (usually empty) global list -- the identical bug to the one
    # `/aop -l` was fixed for, still live afterwards because the two commands
    # listed separately.  Hence: one lister, no exceptions.
    if 'l' in flags or not positional:
      if 'w' in flags:
        window.redmessage('[-w does not narrow the list; every scope is shown, '
                          'each labelled with where it is configured]')

      def status(entry_nk, _ch, nick):
        if not state.notifications:
          return ''
        s = state.notifications.get_state(entry_nk).get(nick.lower()) \
            if entry_nk else None
        return '  (online)' if s is True else '  (offline)' if s is False else ''

      _show_all_entries(window, 'notify', 'Notify list',
                        annotate=status, expand=False)
      return

    nick = positional[0]
    remove = 'r' in flags

    def resync():
      if not state.notifications:
        return
      conn = window.client.conn if window.client else None
      state.notifications.sync_list(nk, conn)
      # Trigger immediate ISON check for non-MONITOR servers
      if not remove and conn and not getattr(conn, '_monitor_supported', False):
        state.notifications._poll_ison()

    # An unscoped remove takes the nick out of every scope, as /aop -r and
    # /highlight -r do: removing from one guessed scope leaves a live copy
    # behind and reports success, which is the failure all of these share.
    if remove and 'w' not in flags:
      hits = remove_entry_everywhere('notify', nick)
      resync()
      if not hits:
        window.redmessage('[%s is not on the notify list anywhere -- nothing '
                          'removed]' % nick)
        return
      for hnk, hch in hits:
        window.redmessage('[Removed from notify (%s): %s]'
                          % (scope_label(hnk, hch), nick))
      return

    res = modify_notify_entry(nick, remove, network_key=nk)
    resync()
    if res == LIST_NO_NETWORK:
      window.redmessage("[Cannot change notify: network %r is not in the "
                        "config. Use -w for the global list.]" % nk)
    elif res == LIST_NOT_FOUND:
      window.redmessage("[%s is not on the notify list (%s) -- nothing removed]"
                        % (nick, scope_label(nk, None)))
    elif res == LIST_ALREADY:
      window.redmessage("[%s is already on the notify list (%s)]"
                        % (nick, scope_label(nk, None)))
    elif res == LIST_REMOVED:
      window.redmessage("[Removed from notify (%s): %s]"
                        % (scope_label(nk, None), nick))
    else:
      window.redmessage("[Added to notify (%s): %s]"
                        % (scope_label(nk, None), nick))

  def kick(window, text):
    """/kick [#channel] <nick> [reason]

    The channel is optional and defaults to the window's, which is how this
    has always worked -- but it is *accepted*, because `docs/reference.md`
    documents the mIRC spelling in two places (the `Kick+Ban` popup and the
    kick-ban /on example, both `... | /kick # $$1`). Without it "#chan" was
    read as the nick to kick and the real nick became the reason: the command
    sent `KICK #chan #chan :alice` and reported nothing wrong.
    """
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    channel, rest = _split_channel_arg(window, conn, text, 'kick')
    if channel is None:
      return
    parts = rest.split(None, 1)
    if not parts:
      window.redmessage("[Error: /kick requires a nick]")
      return
    target = parts[0]
    reason = _unquote(parts[1]) if len(parts) > 1 else None
    if reason:
      conn.sendLine("KICK %s %s :%s" % (channel, target, reason))
    else:
      conn.sendLine("KICK %s %s" % (channel, target))

  def ban(window, text):
    """/ban [#channel] <nick|mask> -- +b, expanding a bare nick to nick!*@*."""
    _channel_mode_command(window, text, '+b', 'ban', as_mask=True)

  def unban(window, text):
    """/unban [#channel] <nick|mask> -- -b, the inverse of /ban.

    /ban had no opposite, which made it the only one of these shortcuts you
    could not undo without dropping to /mode -- and the expansion rule is the
    part you have to get right to undo one, since the ban that was set from
    `/ban alice` is on `alice!*@*`.
    """
    _channel_mode_command(window, text, '-b', 'unban', as_mask=True)

  def kban(window, text):
    """/kban [#channel] <nick> [reason] -- ban the nick's mask, then kick.

    Two lines on the wire, in that order: the ban first, so there is no window
    in which the kicked user can rejoin before it lands.
    """
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    channel, rest = _split_channel_arg(window, conn, text, 'kban')
    if channel is None:
      return
    parts = rest.split(None, 1)
    if not parts:
      window.redmessage("[Error: /kban requires a nick]")
      return
    target = parts[0]
    reason = _unquote(parts[1]) if len(parts) > 1 else None
    from config import ban_mask
    conn.sendLine("MODE %s +b %s" % (channel, ban_mask(target)))
    if reason:
      conn.sendLine("KICK %s %s :%s" % (channel, target, reason))
    else:
      conn.sendLine("KICK %s %s" % (channel, target))

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

  def list(window, text):
    """Open the channel list browser.
    /list              — open browser, click Fetch to request
    /list [params]     — open and immediately fetch with ELIST params
    ELIST params (server-side filters, can be combined):
      >N     channels with more than N users
      <N     channels with fewer than N users
      *mask* channel name wildcard
      C>N    created more than N minutes ago
      C<N    created less than N minutes ago
      T>N    topic changed more than N minutes ago
      T<N    topic changed less than N minutes ago
    Example: /list >10 <500 *chat*"""
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    from channel_list import show_channel_list
    dlg = show_channel_list(window.client, parent=state.app.mainwin)
    args = text.strip()
    if args:
      dlg._fetch(args)

  def chaninfo(window, text):
    """Show channel details dialog (modes, bans, topic)."""
    if window.type != "channel":
      window.redmessage("[Error: /chaninfo only works in a channel window]")
      return
    from channel_details import show_channel_details
    show_channel_details(window.channel, parent=state.app.mainwin)

  def mode(window, text):
    """/mode [target] [modes [params]] -- send a raw MODE, or query one.

    The general form that /op, /ban, /voice and the rest are shortcuts for.
    It has to exist because the shortcuts cannot cover the space: there is no
    command for +m, +i, +k, +l, none for a network-specific mode like
    Undernet's +x, and no way to set several at once -- which is also the only
    way to set them atomically.

    Shapes accepted, and how the target is decided:

      /mode                    query the current channel's modes
      /mode +imnt              apply to the current channel
      /mode #chan +o alice     explicit channel
      /mode # +b alice!*@*     '#' is the current channel (see _resolve_hash)
      /mode alice +x           a user mode: the target is a nick

    The first token is the target *unless* it starts with '+' or '-', which is
    what tells `/mode +o alice` (channel implied, alice is a parameter) apart
    from `/mode alice +o` (alice is the target). That is the rule every other
    client uses, and it is unambiguous because no mode string starts with
    anything else.

    Everything past the target is passed through untouched. This command
    deliberately does not parse or validate mode letters: which ones exist is
    per-network (ISUPPORT CHANMODES/PREFIX), so a client that checked them
    would reject exactly the modes this command exists to reach.
    """
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    text = (text or '').strip()

    if not text:
      # No arguments at all: ask what the current channel's modes are. There is
      # nothing to ask about in a server or query window, so say so rather than
      # sending a MODE with no target and letting the server complain.
      chan = _current_channel(window)
      if not chan:
        window.redmessage('[Error: /mode needs a target outside a channel '
                          'window -- try /mode #channel, or /mode %s +x]'
                          % (conn.nickname or 'yournick'))
        return
      conn.sendLine('MODE %s' % chan)
      return

    parts = text.split(None, 1)
    first = parts[0]
    if first[0] in '+-':
      target = _current_channel(window)
      if not target:
        window.redmessage('[Error: /mode %s has no channel to apply to -- run '
                          'it in a channel window, or name one: '
                          '/mode #channel %s]' % (first, text))
        return
      rest = text
    else:
      target = _resolve_hash(window, first)
      if not target:
        window.redmessage("[Error: '#' means the current channel, and this is "
                          "not a channel window]")
        return
      rest = parts[1].strip() if len(parts) > 1 else ''

    # A target and nothing else is a query: /mode #channel, /mode yournick.
    conn.sendLine('MODE %s %s' % (target, rest) if rest else 'MODE %s' % target)

  # The one-target mode shortcuts. Each is `/name [#channel] <target>`; the
  # shared body is commands._channel_mode_command, and everything interesting
  # about them is documented there. `/mode` is the general form when a
  # shortcut will not do -- several modes at once, a mode with no shortcut, or
  # a user mode.
  def op(window, text):
    """/op [#channel] <nick> -- give operator status (+o)."""
    _channel_mode_command(window, text, '+o', 'op')

  def deop(window, text):
    """/deop [#channel] <nick> -- take operator status (-o)."""
    _channel_mode_command(window, text, '-o', 'deop')

  def halfop(window, text):
    """/halfop [#channel] <nick> -- give halfop status (+h)."""
    _channel_mode_command(window, text, '+h', 'halfop')

  def dehalfop(window, text):
    """/dehalfop [#channel] <nick> -- take halfop status (-h)."""
    _channel_mode_command(window, text, '-h', 'dehalfop')

  def voice(window, text):
    """/voice [#channel] <nick> -- give voice (+v)."""
    _channel_mode_command(window, text, '+v', 'voice')

  def devoice(window, text):
    """/devoice [#channel] <nick> -- take voice (-v)."""
    _channel_mode_command(window, text, '-v', 'devoice')

  def quiet(window, text):
    """/quiet [#channel] <nick|mask> -- set +q.

    Passed through unexpanded, unlike /ban, and that is deliberate rather than
    an oversight: +q is a ban-style *mask* mode on Libera and friends but the
    *owner* prefix mode on UnrealIRCd and InspIRCd, where it takes a nick.
    Expanding "alice" to "alice!*@*" would be right on one and wrong on the
    other, so this does what it has always done and passes on what was typed.
    Same rule as /mode: the client does not interpret modes it cannot know the
    meaning of.
    """
    _channel_mode_command(window, text, '+q', 'quiet')

  def unquiet(window, text):
    """/unquiet [#channel] <nick|mask> -- unset +q. See /quiet."""
    _channel_mode_command(window, text, '-q', 'unquiet')

  def aop(window, text):
    """/aop [-lrw] <nick|mask> [#chan1,#chan2,...] [network]
    Auto-op users matching a nick or hostmask when they join.
    -l list (every scope)  -r remove  -w global (all networks)
    With no channel given in a channel window, the current channel is used.
    -r with no scope removes the mask from every scope it is in.
    (Legacy: `on`/`off` are accepted and just print an explanation.)"""
    action = text.split()[0].lower() if text.split() else ''
    if action == 'on':
      window.redmessage("[Auto-op is always active when entries exist]")
      return
    if action == 'off':
      window.redmessage("[Remove entries with -r to disable auto-op for specific masks]")
      return
    _mask_list_command(window, text, 'auto_ops', 'Auto-op list', 'auto-op',
                       warn_broad=True)

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
    Options: -n nick/mask  -c #channel  -k network  -s sound  -d  -h  -p  -x  -N  -A
    -p persists the hook by appending it to the startup script.
    -x suppresses the default handler (event won't appear in window).
    -N suppresses notifications (sound/desktop/highlight) for this event.
    -A suppresses tab activity coloring for this event.
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
          if hinfo.get('suppress_notify'):
            filters.append('-N')
          if hinfo.get('suppress_activity'):
            filters.append('-A')
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
    suppress_notify = False
    suppress_activity = False

    while rest:
      if rest[0] == '-n' and len(rest) > 1:
        rest.pop(0)
        nick_mask = _unquote(rest.pop(0))
      elif rest[0] == '-c' and len(rest) > 1:
        rest.pop(0)
        channel_filter = _unquote(rest.pop(0))
      elif rest[0] == '-k' and len(rest) > 1:
        rest.pop(0)
        network_filter = _unquote(rest.pop(0))
      elif rest[0] == '-s' and len(rest) > 1:
        rest.pop(0)
        sound = _unquote(rest.pop(0))
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
      elif rest[0] == '-N':
        rest.pop(0)
        suppress_notify = True
      elif rest[0] == '-A':
        rest.pop(0)
        suppress_activity = True
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
      'suppress_notify': suppress_notify,
      'suppress_activity': suppress_activity,
      'window': None,  # resolved at event time, not registration time
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
    if suppress_notify:
      parts.append('-N')
    if suppress_activity:
      parts.append('-A')
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
    """/tile [v] — tile windows side by side, or 'v' for stacked rows."""
    ws = state.app.mainwin.workspace
    t = text.strip().lower()
    if t.startswith('v'):
      ws.tileVertically()
    else:
      ws.tileSubWindows()

  def cascade(window, text):
    """/cascade — cascade all windows."""
    state.app.mainwin.workspace.cascadeSubWindows()

  def tabbed(window, text):
    """/tabbed — maximize the active window (return to tabbed look)."""
    state.app.mainwin.workspace.maximizeActive()
  maximize = tabbed

  def mdi(window, text):
    """/mdi — tile all windows side by side."""
    state.app.mainwin.workspace.tileSubWindows()

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

  def openurl(window, text):
    """Open a URL in the system browser.
    /openurl <url>"""
    url = text.strip()
    if not url:
      window.redmessage('[Usage: /openurl <url>]')
      return
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    QDesktopServices.openUrl(QUrl(url))

  def clipboard(window, text):
    """Copy text to the system clipboard.
    /clipboard <text>"""
    if not text:
      window.redmessage('[Usage: /clipboard <text>]')
      return
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().setText(text)

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
    """Print text to a window.  /echo [-w target] [-s] [-a] text
    -s sends to the server/status window for the current network.
    -a sends to the currently active/visible window."""
    target = window
    if text.startswith('-a ') or text == '-a':
      text = text[2:].lstrip()
      sub = state.app.mainwin.workspace.activeSubWindow()
      if sub and sub.widget():
        target = sub.widget()
    elif text.startswith('-s ') or text == '-s':
      text = text[2:].lstrip()
      if window and hasattr(window, 'client') and window.client:
        target = window.client.window
      elif state.clients:
        for c in state.clients:
          if c.window:
            target = c.window
            break
    elif text.startswith('-w '):
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
    target.addline(_unquote(text), state.infoformat)

  def stdout(window, text):
    """Write text to stdout."""
    import sys
    print(_unquote(text), file=sys.stdout, flush=True)

  def stderr(window, text):
    """Write text to stderr."""
    import sys
    print(_unquote(text), file=sys.stderr)

  def query(window, text):
    """Open a query window.  /query [-z] <nick> ["message"]"""
    parts = text.split(None, 1)
    if not parts:
      window.redmessage("[Usage: /query [-z] <nick> [message]]")
      return
    no_activate = False
    if parts[0] == '-z':
      no_activate = True
      text = parts[1] if len(parts) > 1 else ''
      parts = text.split(None, 1)
      if not parts:
        window.redmessage("[Usage: /query [-z] <nick> [message]]")
        return
    nick = parts[0]
    conn = window.client.conn if window.client else None
    if not conn:
      window.redmessage('[Not connected]')
      return
    # Find or create the query window.  Route through _find_or_create_query
    # so we get consistent nick-based keying, dedup against an already-open
    # window, and replay of saved conversation history on first open.
    nicklower = conn.irclower(nick)
    user = window.client.users.get(nicklower)
    ident = user.ident if user else None
    host = user.host if user else None
    from irc_client import _find_or_create_query
    q, _new = _find_or_create_query(conn, nick, ident, host)
    qwin = q.window
    # Activate the query window
    if not no_activate:
      ws = state.app.mainwin.workspace
      ws.setActiveSubWindow(qwin.subwindow)
    # Send a message if provided.  qwin is passed explicitly because it was
    # just created if it did not exist -- send_message's own lookup would find
    # it anyway, but saying so here keeps the two independent of each other.
    if len(parts) > 1:
      send_message(window, conn, nick, _unquote(parts[1]), display_window=qwin)

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
    conn = client.conn if client else None
    network = conn._log_network if conn else (client.network or client.network_key or client.hostname or '')
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
      # Plugin-registered commands are not in reference.md -- they do not exist
      # until a plugin is loaded -- so they are listed from the live registry.
      # A command you cannot discover is a command nobody uses.
      if state.plugin_commands:
        window.addline('[Plugin commands: %s]'
                       % ', '.join('/' + n for n in sorted(state.plugin_commands)))
      window.addline('[Topics: events, variables, popups, plugin, objects, cli]')
      window.addline('[Use /help <command> or /help <topic> for details]')
      return

    # If user typed /help /command, try command lookup first
    if force_command:
      lines_found = _find_command_rows(ref, cmd)
      if lines_found:
        for syntax, desc in lines_found:
          window.addline('  %s — %s' % (syntax, desc))
        section_pat = re.compile(
            r'^##+ .*/%s\b.*$' % re.escape(cmd), re.MULTILINE | re.IGNORECASE)
        m = section_pat.search(ref)
        if m:
          _show_help_section(window, ref, m.group(0), start_match=m)
        return
      if _show_plugin_command_help(window, cmd):
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
    lines_found = _find_command_rows(ref, cmd)

    if not lines_found:
      if _show_plugin_command_help(window, cmd):
        return
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

  def hotkeys(window, text):
    """List hotkeys bound by plugins.  /hotkeys

    Plugin bindings are application-wide and invisible otherwise: the only
    other way to find out what F12 does is to press it.
    """
    if not state.plugin_keys:
      window.redmessage('[No plugin hotkeys bound]')
      return
    width = max(len(k) for k in state.plugin_keys)
    for seq in sorted(state.plugin_keys):
      info = state.plugin_keys[seq]
      window.redmessage('  %-*s  %s%s'
                        % (width, seq, info.get('description') or '(no description)',
                           '  [%s]' % info['owner'] if info.get('owner') else ''))

  keys = hotkeys  # alias

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
    # An alias is looked up last, so one that collides with a built-in or with
    # a plugin command is defined successfully and then never runs.  Say so at
    # the moment it is written -- the alternative is a user editing an alias
    # that was never going to fire and concluding the alias system is broken.
    if hasattr(Commands, name) and not name.startswith('_'):
      window.redmessage('[  WARNING: /%s is a built-in command, which takes '
                        'precedence -- this alias will never run]' % name)
    elif name in state.plugin_commands:
      owner = state.plugin_commands[name].get('owner') or 'a plugin'
      window.redmessage('[  WARNING: /%s is registered by %s, which takes '
                        'precedence -- this alias will never run]'
                        % (name, owner))

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
    tokens = _tokenize(text, max_tokens=3)
    if tokens[0] == '-r':
      if len(tokens) < 2:
        window.redmessage('[Usage: /set -r <name>]')
        return
      name = tokens[1]
      if name in state._persistent_vars:
        del state._persistent_vars[name]
        state._merge_variables()
        state.save_variables()
        window.redmessage('[Variable {%s} removed]' % name)
      else:
        window.redmessage('[No persistent variable named {%s}]' % name)
      return
    if len(tokens) < 2:
      name = tokens[0]
      val = state._variables.get(name)
      if val is not None:
        src = ' (temp)' if name in state._temp_vars else ''
        window.redmessage('  {%s} = %s%s' % (name, val, src))
      else:
        window.redmessage('[No variable named {%s}]' % name)
      return
    name = tokens[0]
    val = tokens[1]
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
    tokens = _tokenize(text, max_tokens=3)
    if tokens[0] == '-r':
      if len(tokens) < 2:
        window.redmessage('[Usage: /var -r <name>]')
        return
      name = tokens[1]
      if name in state._temp_vars:
        del state._temp_vars[name]
        state._merge_variables()
        window.redmessage('[Temp variable {%s} removed]' % name)
      else:
        window.redmessage('[No temp variable named {%s}]' % name)
      return
    if len(tokens) < 2:
      window.redmessage('[Usage: /var <name> <value>]')
      return
    name = tokens[0]
    val = tokens[1]
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
    tokens = _tokenize(text, max_tokens=3)
    if tokens and tokens[0] == '-e':
      expand = True
      tokens.pop(0)
    if not tokens:
      window.redmessage('[Usage: /config [-e] <key.path> [value]]')
      return
    key_path = tokens[0]
    value_str = tokens[1] if len(tokens) > 1 else None
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
    """/newserver — alias for /server -m"""
    Commands.server(window, '-m ' + (text or '').strip())

  def dcc(window, text):
    """/dcc send <nick> [file]  — send a file (opens file picker if no path given)
    /dcc chat <nick>          — open a DCC chat
    /dcc get [id|nick]        — accept a pending transfer
    /dcc cancel <id>          — cancel a transfer
    /dcc list                 — show the transfers window
    /dcc close <id>           — close a transfer or chat"""
    import asyncio
    parts = text.split(None, 1) if text.strip() else []
    if not parts:
      window.redmessage('[Usage: /dcc send|chat|get|cancel|list|close ...]')
      return
    sub = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ''
    mgr = state.dcc_manager
    if not mgr:
      window.redmessage('[DCC not initialized]')
      return

    if sub == 'send':
      nick_file = _tokenize(args, max_tokens=2) if args else []
      if not nick_file:
        window.redmessage('[Usage: /dcc send <nick> [filepath]]')
        return
      nick = nick_file[0]
      if len(nick_file) > 1:
        filepath = nick_file[1]
      else:
        from PySide6.QtWidgets import QFileDialog, QApplication
        filepath, _ = QFileDialog.getOpenFileName(
          QApplication.activeWindow(), 'Select file to send')
        if not filepath:
          return
      import os
      if not os.path.isfile(filepath):
        window.redmessage('[DCC SEND: file not found: %s]' % filepath)
        return
      window.redmessage('[DCC SEND: sending %s to %s (%s)]' % (
        os.path.basename(filepath), nick,
        '%.1f MB' % (os.path.getsize(filepath) / 1048576)))
      async def _do_send():
        try:
          await mgr.initiate_send(window.client, nick, filepath)
        except Exception as e:
          window.redmessage('[DCC SEND error: %s]' % e)
          import traceback; traceback.print_exc()
      asyncio.ensure_future(_do_send())

    elif sub == 'chat':
      nick = args.strip()
      if not nick:
        window.redmessage('[Usage: /dcc chat <nick>]')
        return
      async def _do_chat():
        try:
          await mgr.initiate_chat(window.client, nick)
        except Exception as e:
          window.redmessage('[DCC CHAT error: %s]' % e)
          import traceback; traceback.print_exc()
      asyncio.ensure_future(_do_chat())

    elif sub == 'get':
      # Accept a pending transfer by ID or most recent from nick
      target = args.strip()
      if not target:
        window.redmessage('[Usage: /dcc get <id|nick>]')
        return
      from dcc import Direction, Status
      if target.isdigit():
        xfer = mgr.transfers.get(int(target))
        if xfer and xfer.status == Status.PENDING and xfer.direction == Direction.RECEIVE:
          asyncio.ensure_future(mgr.accept_receive(xfer))
        else:
          window.redmessage('[No pending transfer with ID %s]' % target)
      else:
        # Find most recent pending from nick
        found = None
        for xfer in reversed(list(mgr.transfers.values())):
          if (xfer.nick.lower() == target.lower() and
              xfer.status == Status.PENDING and
              xfer.direction == Direction.RECEIVE):
            found = xfer
            break
        if found:
          asyncio.ensure_future(mgr.accept_receive(found))
        else:
          window.redmessage('[No pending transfer from %s]' % target)

    elif sub in ('cancel', 'close'):
      target = args.strip()
      if not target or not target.isdigit():
        window.redmessage('[Usage: /dcc %s <id>]' % sub)
        return
      tid = int(target)
      if mgr.cancel(tid):
        window.redmessage('[DCC #%d cancelled]' % tid)
      else:
        window.redmessage('[No active DCC with ID %d]' % tid)

    elif sub == 'list':
      from dcc_ui import DCCTransfersWindow
      DCCTransfersWindow.show_instance()

    else:
      window.redmessage('[Unknown DCC subcommand: %s]' % sub)

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

  def playsound(window, text):
    """Play a sound by name or file path.  /playsound <name|path>
    Accepts a system sound name (see /sounds), a relative path, or
    an absolute path to a .wav/.ogg/.mp3/etc file."""
    text = _unquote(text.strip())
    if not text:
      window.redmessage('[Usage: /playsound <name|path>]')
      return
    if not state.notifications:
      window.redmessage('[Notifications not initialized]')
      return
    from notify import resolve_sound_name
    path = resolve_sound_name(text)
    if not path:
      window.redmessage('[Sound not found: %s]' % text)
      return
    state.notifications._play_sound(text)

  def notif(window, text):
    """Send a desktop notification.  /notif [-t "title"] <body>
    Shows a system tray notification (toast). On Windows it will
    appear in the Action Center until dismissed. Default title: "qtpyrc"."""
    tokens = _tokenize(text)
    title = 'qtpyrc'
    i = 0
    while i < len(tokens):
      if tokens[i] == '-t' and i + 1 < len(tokens):
        title = tokens[i + 1]
        i += 2
      else:
        break
    body = ' '.join(tokens[i:])
    if not body:
      window.redmessage('[Usage: /notif [-t "title"] <body>]')
      return
    if state.tray_icon:
      from PySide6.QtWidgets import QSystemTrayIcon
      state.tray_icon.showMessage(title, body,
                                  QSystemTrayIcon.MessageIcon.Information, 5000)
    else:
      window.redmessage('[Notifications: no system tray available]')

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


def _current_channel(window):
  """The channel this window is about, or '' if it is not about one."""
  if getattr(window, 'type', '') == 'channel' and getattr(window, 'channel', None):
    return window.channel.name
  return ''


def _resolve_hash(window, token):
  """Turn a bare '#' argument into the current channel; pass anything else on.

  `docs/reference.md` documents '#' as meaning the current channel *in
  commands*, and `popups.show_popup` substitutes it before running one -- but
  the /on hook path does not, and neither does a line typed by hand. Resolving
  it at the command means the one spelling the documentation promises works
  from all three. Returns '' when there is no current channel to resolve to,
  which the caller must report rather than send.
  """
  if token == '#':
    return _current_channel(window)
  return token


def _split_channel_arg(window, conn, text, name):
  """Split an optional leading channel argument off *text*.

  Returns ``(channel, rest)``, or ``(None, '')`` having already said why -- so
  a caller checks for None and returns, and never has to word the error.

  Accepting a channel is not decoration. `docs/reference.md` documents the
  mIRC spelling (`/kick # $$1`, `/mode # +b ...`) in the popup and /on
  examples, and without this the expanded channel name was read as the *nick*:
  `/kick #chan alice` sent ``KICK #chan #chan :alice`` and reported nothing
  wrong. It is safe to accept on any of these commands because **no valid nick
  can begin with a channel prefix**, so no line that worked before can change
  meaning.
  """
  parts = text.split(None, 1) if text else []
  if parts and (parts[0] == '#' or conn.is_channel(parts[0])):
    channel = _resolve_hash(window, parts[0])
    if not channel:
      window.redmessage("[Error: '#' means the current channel, and this is "
                        "not a channel window]")
      return None, ''
    return channel, (parts[1] if len(parts) > 1 else '')
  channel = _current_channel(window)
  if not channel:
    window.redmessage('[Error: /%s needs a channel -- run it in a channel '
                      'window, or name one: /%s #channel ...]' % (name, name))
    return None, ''
  return channel, text


def _channel_mode_command(window, text, modes, name, as_mask=False):
  """Body of /op, /deop, /voice, /ban and the rest: one MODE, one target.

  These were nine near-identical copies of the same twelve lines, and they
  agreed on everything except the two things that mattered. None checked
  whether there *was* a connection, so every one raised AttributeError on
  ``None.sendLine`` while disconnected instead of saying "[Not connected]" --
  and none accepted the channel argument the documentation hands them. Nine
  copies is how that happens; one body is the fix.

  *modes* is the mode string ('+o', '-v', ...), *name* is the command's own
  name for its error messages, and *as_mask* expands a bare nick to
  ``nick!*@*`` the way /ban has always done.
  """
  conn = window.client.conn if window.client else None
  if not conn:
    window.redmessage('[Not connected]')
    return
  channel, rest = _split_channel_arg(window, conn, text, name)
  if channel is None:
    return
  target = rest.strip()
  if not target:
    window.redmessage('[Error: /%s requires a %s]'
                      % (name, 'nick or mask' if as_mask else 'nick'))
    return
  if as_mask:
    from config import ban_mask
    target = ban_mask(target)
  conn.sendLine('MODE %s %s %s' % (channel, modes, target))


def _unquote(s):
  """Strip matching quotes from a string."""
  if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
    return s[1:-1]
  return s


def _tokenize(s, max_tokens=0):
  r"""Split a string into tokens, respecting "quoted strings" with \" escape.

  Returns a list of strings with quotes stripped.
  If *max_tokens* > 0, stops splitting after that many tokens and returns
  the remainder as the last element (like str.split(None, n)).

  Examples:
    _tokenize('hello world')             -> ['hello', 'world']
    _tokenize('"hello world" foo')        -> ['hello world', 'foo']
    _tokenize(r'say "he said \"hi\""')   -> ['say', 'he said "hi"']
    _tokenize('a b c d', max_tokens=2)   -> ['a', 'b c d']
  """
  tokens = []
  i = 0
  s = s.strip()
  while i < len(s):
    if max_tokens > 0 and len(tokens) == max_tokens - 1:
      # Last token: take everything remaining
      tokens.append(s[i:].strip())
      break
    # Skip whitespace
    while i < len(s) and s[i] in ' \t':
      i += 1
    if i >= len(s):
      break
    if s[i] in ('"', "'"):
      # Quoted token
      q = s[i]
      i += 1
      token = []
      while i < len(s):
        if s[i] == '\\' and i + 1 < len(s) and s[i + 1] in (q, '\\'):
          token.append(s[i + 1])
          i += 2
        elif s[i] == q:
          i += 1
          break
        else:
          token.append(s[i])
          i += 1
      tokens.append(''.join(token))
    else:
      # Unquoted token
      start = i
      while i < len(s) and s[i] not in ' \t':
        i += 1
      tokens.append(s[start:i])
  return tokens


class TokenizedString(str):
  """A string subclass that carries pre-parsed tokens.

  Behaves exactly like a regular str, but also has a .tokens attribute
  containing the result of _tokenize(). Plugins can use msg.tokens to
  get quote-aware parsed parameters without re-parsing.
  """
  __slots__ = ('_tokens',)

  def __new__(cls, s, tokens=None):
    obj = str.__new__(cls, s)
    obj._tokens = tokens
    return obj

  @property
  def tokens(self):
    if self._tokens is None:
      self._tokens = _tokenize(self)
    return self._tokens


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


def _parse_server_args(text):
  """Parse /server command arguments into a dict.

  Returns dict with keys: flags (set), host, port, tls, starttls,
  ip_version, nick, altnicks, user, realname, password,
  login_method, login_password, network_key, _error.
  """
  result = {
    'flags': set(), 'host': None, 'port': None, 'tls': None,
    'starttls': False, 'ip_version': None, 'nick': None,
    'altnicks': [], 'user': None, 'realname': None, 'password': None,
    'login_method': None, 'login_password': None, 'network_key': None,
  }
  # Tokenize respecting quotes (supports \" escapes)
  tokens = _tokenize(text)

  simple_flags = set('mndocuetz')
  valued_args = {'-nick', '-altnick', '-user', '-realname', '-w', '-l'}

  i = 0
  while i < len(tokens):
    tok = tokens[i]
    # Combined IPv4/6 flag
    if tok == '-46':
      result['ip_version'] = '46'
    elif tok == '-4':
      result['ip_version'] = '4'
    elif tok == '-6':
      result['ip_version'] = '6'
    elif tok in ('-e',):
      result['tls'] = True
    elif tok in ('-t',):
      result['starttls'] = True
    elif tok == '-l':
      # -l <method> [password]
      i += 1
      if i < len(tokens):
        result['login_method'] = tokens[i]
        # Check if next token looks like a password (not a flag)
        if i + 1 < len(tokens) and not tokens[i + 1].startswith('-'):
          i += 1
          result['login_password'] = tokens[i]
    elif tok == '-nick':
      i += 1
      if i < len(tokens):
        result['nick'] = tokens[i]
    elif tok == '-altnick':
      i += 1
      if i < len(tokens):
        result['altnicks'].append(tokens[i])
    elif tok == '-user':
      i += 1
      if i < len(tokens):
        result['user'] = tokens[i]
    elif tok == '-realname':
      i += 1
      if i < len(tokens):
        result['realname'] = tokens[i]
    elif tok == '-w':
      i += 1
      if i < len(tokens):
        result['password'] = tokens[i]
    elif tok.startswith('-') and len(tok) > 1 and tok[1:].isalpha():
      # Single-char flags (possibly combined like -mz)
      for ch in tok[1:]:
        if ch in simple_flags:
          result['flags'].add(ch)
        else:
          result['_error'] = 'Unknown flag: -%s' % ch
          return result
    elif tok.startswith('+') and tok[1:].isdigit():
      # +port = TLS
      result['port'] = int(tok[1:])
      result['tls'] = True
    elif tok.startswith('*') and tok[1:].isdigit():
      # *port = STARTTLS
      result['port'] = int(tok[1:])
      result['starttls'] = True
    elif result['host'] is None:
      # First positional: host[:port] or network_key
      host_port = tok
      if ':' in host_port:
        host, port_str = host_port.rsplit(':', 1)
        # Check for +port or *port after colon
        if port_str.startswith('+') and port_str[1:].isdigit():
          result['host'] = host
          result['port'] = int(port_str[1:])
          result['tls'] = True
        elif port_str.startswith('*') and port_str[1:].isdigit():
          result['host'] = host
          result['port'] = int(port_str[1:])
          result['starttls'] = True
        elif port_str.isdigit():
          result['host'] = host
          result['port'] = int(port_str)
        else:
          # Not a port — treat whole thing as host (e.g. IPv6 or weird hostname)
          result['host'] = host_port
      else:
        result['host'] = host_port
    elif result['password'] is None:
      # Second positional: legacy password
      result['password'] = tok
    i += 1

  # -e flag sets TLS
  if 'e' in result['flags'] and result['tls'] is None:
    result['tls'] = True
  # -t flag sets STARTTLS
  if 't' in result['flags']:
    result['starttls'] = True

  return result


def _unescape_md(s):
  """Turn markdown-escaped table-cell text back into plain text."""
  return s.replace('\\|', '|').replace('\\\\', '\\')


def _show_plugin_command_help(window, cmd):
  """Print help for a plugin-registered command.  True if there was one.

  `/help` answers out of `docs/reference.md`, which cannot describe a command
  that does not exist until a plugin is loaded.  Without this, every plugin
  command answered "[No help for: /x]" -- which reads as "no such command"
  rather than "documented elsewhere", and is how a working feature gets
  reported as broken.  The text is whatever the plugin passed to
  `add_command(..., help=...)`, falling back to the handler's docstring.
  """
  entry = state.plugin_commands.get(cmd)
  if not entry:
    return False
  text = entry.get('help') or ''
  if not text:
    text = (getattr(entry['func'], '__doc__', '') or '').strip()
  owner = entry.get('owner')
  window.addline('  /%s — %s' % (cmd, text.strip().splitlines()[0]
                                 if text.strip() else '(no description)'))
  for line in text.strip().splitlines()[1:]:
    window.addline('    %s' % line.strip())
  window.addline('  [registered by plugin: %s]' % (owner or 'unknown'))
  return True


def _find_command_rows(ref, cmd):
  """Find reference.md table rows for /cmd, handling escaped pipes (\\|).

  Returns a list of (syntax, desc) tuples. The cell pattern
  ``(?:[^|\\]|\\.)*`` matches any run of characters that are neither an
  unescaped pipe nor a backslash, plus any backslash-escaped character — so
  ``<on\\|off>`` is captured whole instead of being cut at the first pipe.
  """
  import re as _re
  cell = r'((?:[^|\\]|\\.)*)'
  pat = _re.compile(
      r'^\| `/%s`\s*\|%s\|%s\|' % (_re.escape(cmd), cell, cell), _re.MULTILINE)
  rows = []
  for m in pat.finditer(ref):
    syntax = _unescape_md(m.group(1).strip()).strip('`').strip()
    desc = _unescape_md(m.group(2).strip())
    rows.append((syntax, desc))
  return rows


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


def send_message(window, conn, target, text, display_window=None):
  """Send *text* to *target*, and do everything else that goes with sending it.

  **Every path that sends a PRIVMSG on the user's behalf must call this.**
  There used to be five, and they had drifted into five different answers about
  what sending a message means:

    * `say`'s channel branch (typing in a channel window) chunked the text,
      recorded it for self-echo suppression, displayed it with the mode prefix,
      logged it, saved it with that prefix, previewed links in it and dispatched
      it to plugin `chanmsg` hooks.
    * `say`'s query branch did all of that bar the self-echo record and the
      plugin dispatch, neither of which applies to a PM.
    * `/msg` sent it and displayed it. No log line, no history row -- so a
      conversation held partly in a query window and partly through `/msg` came
      back after a reload with the `/msg` half missing. That is the bug this
      consolidation was written for. It also routed a channel target down the
      query path, so `/msg #chan hi` would have been logged as a PM and saved
      under `=#chan`, a key no window ever reads.
    * `/query <nick> <message>` did the same as `/msg`, and additionally never
      split long messages, so anything over the 512-byte line limit was silently
      truncated by the server.
    * `/amsg` chunked, displayed, logged and saved per channel, but recorded
      nothing for self-echo suppression -- so on a bouncer that echoes, every
      `/amsg` line came back and was drawn and stored a second time in every
      channel -- and previewed no links and dispatched to no plugin.

  None of those differences was intended; they are just what happens when one
  operation is written five times. Link previews show the shape of it: they were
  added to `say` and to nothing else, so a URL sent by `/msg` or `/amsg` was the
  only URL qtpyrc declined to preview.

  Which of the two shapes a target has is decided by `conn.is_channel()`, i.e.
  by ISUPPORT CHANTYPES, and never by testing for a leading '#'.

  The PM path deliberately does *not* record the message with
  `conn._own_messages`. That is correct only as long as an echoed PM never
  reaches a window -- see the "PMs sent from another client attached to the same
  bouncer" entry in `known-issues.md`. Whoever fixes that routing adds the
  `record()` here, in the same change, or turns a missing message into a doubled
  one.

  *display_window* is where the message is echoed to the user. When it is None
  the window for *target* is used if one is open -- the channel's window, or the
  query window for the nick -- and otherwise the line goes to *window* as
  `[-> target] text`, which is what `/msg` to a nick with no open window has
  always done.
  """
  is_chan = conn.is_channel(target)
  tlower = conn.irclower(target)

  if display_window is None:
    if is_chan:
      chan = window.client.channels.get(tlower) if window.client else None
      display_window = chan.window if (chan and chan.window) else None
    else:
      _, q = _find_query(window.client, target)
      display_window = q.window if (q and q.window) else None

  if is_chan:
    # The displayed nick carries the mode prefix; the stored one is the prefix
    # on its own, in the history row's `prefix` column, so a replay can
    # reconstruct it without consulting live state that may have changed since.
    shown_nick = conn._pnick(conn.nickname, target)
    prefix = conn._nick_prefix(conn.nickname, target)
    hist_key = target.lower()
  else:
    shown_nick = conn.nickname
    prefix = None
    # Keyed on the nick alone -- _query_history_key ignores the ident it is
    # handed -- so a PM sent to someone with no open query window, and therefore
    # no known ident, still lands under the key the window will read when it is
    # next opened.  This is what makes the /msg half of a conversation show up
    # in the replay.
    from irc_client import _query_history_key
    hist_key = _query_history_key(target)

  for chunk in conn.split_message(target, text):
    conn.msg(target, chunk)
    if is_chan:
      # Mark for dedup so a bouncer echo doesn't double-display/save it.
      conn._own_messages.record(tlower, chunk)
    # Route any error about this target (ERR_NOSUCHNICK, ERR_CANNOTSENDTOCHAN)
    # back to whoever is showing the message.
    conn._msg_windows[tlower] = display_window or window
    if display_window:
      display_window.addline_msg(shown_nick, chunk)
    else:
      window.redmessage('[-> %s] %s' % (target, chunk))
    if is_chan:
      state.irclogger.log_channel(conn._log_network, target,
                                  "<%s> %s" % (conn.nickname, chunk))
    else:
      state.irclogger.log(conn._log_network, target,
                          "<%s> %s" % (conn.nickname, chunk))
    if state.historydb:
      state.historydb.add(conn._log_network, hist_key,
                          'message', conn.nickname, chunk, prefix=prefix)

  from link_preview import check_and_preview
  check_and_preview(display_window or window, text)

  if is_chan:
    # Plugins see our own channel messages the same way they see everyone
    # else's.  Once per message, not once per chunk: a plugin reacting to what
    # was said should see what was said, not how the protocol had to break it up.
    from plugins import _dispatch_to_plugins
    user = '%s!%s@%s' % (conn.nickname, conn.username or '', '')
    _dispatch_to_plugins('chanmsg', conn, (user, target, text), {})


def send_action(window, conn, text):
  """Send a CTCP ACTION to *window*'s channel or query, and record it.

  The body of `/me`, lifted out the moment it acquired a second caller (the
  nowplaying plugin, which sends an action when configured to).  `/me` keeps
  the parts that belong to *parsing a command line* -- `_unquote`, the empty
  check, the "[Not connected]" message -- and this keeps the parts that belong
  to *sending an action*: chunking with ACTION's 9 bytes of overhead, the
  self-echo record, the echo, the log line and the history row.

  A caller that is not entering a command must not go through `/me` itself:
  `docommand` expands `{...}` in its argument, so a filename or title
  containing braces would be silently rewritten, and `_unquote` would eat the
  quotes off a title that legitimately starts and ends with one.
  """
  if window.type == "channel":
    target = window.channel.name
    pnick = conn._pnick(conn.nickname, target)
    pfx = conn._nick_prefix(conn.nickname, target)
    chnlower = conn.irclower(target)
    # ACTION has extra overhead: \x01ACTION ...\x01 = 9 bytes
    chunks = conn.split_message(target, text, extra_overhead=9)
    for chunk in chunks:
      conn.me(target, chunk)
      conn._own_actions.record(chnlower, chunk)
      window.addline_nick(["* ", (pnick,), " %s" % chunk], state.actionformat)
      state.irclogger.log_channel(conn._log_network, target,
                            "* %s %s" % (conn.nickname, chunk))
      if state.historydb:
        state.historydb.add(conn._log_network, target.lower(),
                            'action', conn.nickname, chunk, prefix=pfx)
  elif window.type == "query":
    target = window.remotenick
    chunks = conn.split_message(target, text, extra_overhead=9)
    for chunk in chunks:
      conn.me(target, chunk)
      window.addline_nick(["* ", (conn.nickname,), " %s" % chunk], state.actionformat)
      state.irclogger.log(conn._log_network, target,
                          "* %s %s" % (conn.nickname, chunk))
      if state.historydb and window.query:
        from irc_client import _query_history_key
        state.historydb.add(conn._log_network,
                            _query_history_key(window.query.nick, window.query.ident),
                            'action', conn.nickname, chunk)
  else:
    window.redmessage("[Error: /me only works in channel or query windows]")


def _find_query(client, nick):
  """Find an existing query by nick (case-insensitive).
  Returns the (key, Query) tuple or (None, None)."""
  conn = client.conn
  if not conn:
    return None, None
  lower = conn.irclower(nick)
  for qkey, q in client.queries.items():
    if q.nick and conn.irclower(q.nick) == lower:
      return qkey, q
  return None, None


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
    # Query
    _, q = _find_query(c, name)
    if q and q.window:
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
  """Run a command script file.  Each line is executed as a command.

  Multiline /exec blocks are supported (when run from a file, not typed)::

      /exec {
        for i in range(5):
          echo("line %d" % i)
      }

  Lines between ``/exec {`` and a closing ``}`` at column 0 are joined
  and executed as one Python block.  Indentation is auto-dedented.
  The closing ``}`` must be unindented (no leading spaces) to avoid
  confusion with Python dict/set braces.
  """
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
  exec_block = None  # accumulator for multiline /exec
  for line in lines:
    stripped = line.rstrip()

    # Accumulating a multiline /exec block
    if exec_block is not None:
      if stripped.strip() == '}' and not stripped.startswith(' ') and not stripped.startswith('\t'):
        # End of block — closing } must be unindented (column 0)
        # Find the common leading whitespace and remove it
        import textwrap
        code = textwrap.dedent('\n'.join(exec_block))
        if win:
          docommand(win, 'exec', code)
        exec_block = None
      else:
        exec_block.append(stripped)
      continue

    line = stripped.strip()
    if not line or line.startswith(';'):
      continue

    # Check for multiline /exec { opener
    prefix = state.config.cmdprefix
    exec_opener = None
    if line.startswith('/exec '):
      exec_opener = line[6:]
    elif prefix != '/' and line.startswith(prefix + 'exec '):
      exec_opener = line[len(prefix) + 5:]
    if exec_opener is not None and exec_opener.strip() == '{':
      exec_block = []
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
  v['networks'] = str(sum(1 for c in (state.clients or []) if c.hostname))
  v['channels'] = str(sum(len(c.channels) for c in (state.clients or [])))
  from qtpyrc import APP_VERSION
  v['app_version'] = APP_VERSION
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
  if text and command not in ('config', 'title', 'on'):
    from config import _expand_vars
    variables = _window_context_vars(window)
    variables.update(state._variables)  # user vars override built-ins
    if variables:
      text = _expand_vars(text, variables)
  # Map keywords that can't be method names
  if command == 'exec':
    command = 'exec_'
  # Wrap text as TokenizedString so command handlers and plugins get .tokens
  if text and not isinstance(text, TokenizedString):
    text = TokenizedString(text)
  if hasattr(Commands, command) and not command.startswith("_"):
    getattr(Commands, command)(window, text)
    return
  # Plugin-registered commands (plugin.irc.add_command).  Between the built-ins
  # and aliases: add_command refuses a built-in name outright, and /alias warns
  # when it shadows either, so no name can be taken twice without somebody
  # being told.
  entry = state.plugin_commands.get(command)
  if entry:
    try:
      entry['func'](window, text)
    except Exception as e:
      traceback.print_exc()
      window.redmessage('[/%s failed: %s: %s]'
                        % (command, type(e).__name__, e))
    return
  # Try user-defined alias
  result = _expand_alias(command, text)
  if result:
    docommand(window, result[0], result[1])
  else:
    window.redmessage("[Unknown command: /%s]" % command)
