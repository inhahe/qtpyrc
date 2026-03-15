# exec_system.py - /exec, /timer, /on infrastructure

import re
import os
import asyncio
import time
import traceback
import fnmatch as _fnmatch

from PySide6.QtCore import QTimer

import state
from config import _mask_match


# --- Named timers (/timer) ---

def _timer_fire(name):
  """Called when timer *name* fires."""
  info = state._timers.get(name)
  if not info:
    return
  remaining = info['remaining']
  window = info['window']
  command = info['command']

  # Execute the associated command string
  _exec_command_string(window, command)

  # remaining == 0 means infinite repeats
  if remaining > 0:
    remaining -= 1
    info['remaining'] = remaining
    if remaining == 0:
      # Done — stop and remove
      info['timer'].stop()
      del state._timers[name]

def _exec_command_string(window, cmdstr):
  """Execute a command string as if the user typed it in *window*."""
  from commands import docommand
  cmdstr = cmdstr.strip()
  if cmdstr.startswith(state.config.cmdprefix):
    docommand(window, *(cmdstr[len(state.config.cmdprefix):].split(" ", 1)))
  else:
    # Treat as literal text (say command)
    docommand(window, "say", cmdstr)


# --- Event hooks (/on) ---

# Map from /on event names to internal callback names
_ON_EVENT_MAP = {
  'chanmsg':    'chanmsg',
  'privmsg':    'privmsg',
  'action':     'action',
  'noticed':    'noticed',
  'join':       'userJoined',
  'part':       'userLeft',
  'quit':       'userQuit',
  'kick':       'userKicked',
  'nick':       'userRenamed',
  'topic':      'topicUpdated',
  'mode':       'modeChanged',
  'connect':    'connectionMade',
  'disconnect': 'connectionLost',
  'signon':     'signedOn',
  'motd':       'receivedMOTD',
  'rawcmd':     'irc_unknown',
  'numeric':    'on_numeric',
  'invite':     'invited',
  'ctcpreply':  'ctcpReply',
}

def _on_hook_vars(event, conn, args):
  """Build the $variable dict for an /on event from callback args."""
  v = {}
  if event == 'chanmsg':
    # args: (user, channel, message)
    user, channel, message = (args + (None, None, None))[:3]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$user'] = user or ''
    v['$channel'] = channel or ''
    v['$message'] = message or ''
    v['$text'] = message or ''
  elif event == 'privmsg':
    user, message = (args + (None, None))[:2]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$user'] = user or ''
    v['$message'] = message or ''
    v['$text'] = message or ''
  elif event == 'action':
    user, channel, data = (args + (None, None, None))[:3]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$user'] = user or ''
    v['$channel'] = channel or ''
    v['$data'] = data or ''
    v['$text'] = data or ''
  elif event == 'noticed':
    user, channel, message = (args + (None, None, None))[:3]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$user'] = user or ''
    v['$channel'] = channel or ''
    v['$message'] = message or ''
  elif event in ('join', 'userJoined'):
    nickidhost, channel = (args + (None, None))[:2]
    v['$nick'] = nickidhost.split('!', 1)[0] if nickidhost else ''
    v['$user'] = nickidhost or ''
    v['$channel'] = channel or ''
  elif event in ('part', 'userLeft'):
    user, channel = (args + (None, None))[:2]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$user'] = user or ''
    v['$channel'] = channel or ''
  elif event in ('quit', 'userQuit'):
    user, qmsg = (args + (None, None))[:2]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$user'] = user or ''
    v['$message'] = qmsg or ''
  elif event in ('kick', 'userKicked'):
    kickee, channel, kicker, msg = (args + (None, None, None, None))[:4]
    v['$kickee'] = kickee or ''
    v['$channel'] = channel or ''
    v['$kicker'] = kicker or ''
    v['$nick'] = kicker or ''
    v['$message'] = msg or ''
  elif event in ('nick', 'userRenamed'):
    old, new = (args + (None, None))[:2]
    v['$oldnick'] = old or ''
    v['$newnick'] = new or ''
    v['$nick'] = old or ''
  elif event in ('topic', 'topicUpdated'):
    user, channel, topic = (args + (None, None, None))[:3]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$channel'] = channel or ''
    v['$topic'] = topic or ''
  elif event in ('mode', 'modeChanged'):
    user, channel, set_, modes, margs = (args + (None, None, None, None, None))[:5]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$channel'] = channel or ''
    v['$modes'] = ('%s%s' % ('+' if set_ else '-', modes)) if modes else ''
    v['$args'] = str(margs) if margs else ''
  elif event in ('disconnect', 'connectionLost'):
    reason = args[0] if args else ''
    v['$reason'] = str(reason)
  elif event in ('rawcmd', 'irc_unknown'):
    prefix, command, params = (args + (None, None, None))[:3]
    v['$prefix'] = prefix or ''
    v['$command'] = command or ''
    v['$params'] = ' '.join(params) if isinstance(params, (list, tuple)) else str(params or '')
  elif event in ('numeric', 'on_numeric'):
    command, prefix = (args + (None, None))[:2]
    params = args[2] if len(args) > 2 else ''
    v['$command'] = command or ''
    v['$prefix'] = prefix or ''
    v['$params'] = ' '.join(params) if isinstance(params, (list, tuple)) else str(params or '')
  elif event == 'invite':
    nick, channel = (args + (None, None))[:2]
    v['$nick'] = nick or ''
    v['$channel'] = channel or ''
  elif event in ('motd', 'receivedMOTD'):
    motd = args[0] if args else ''
    v['$motd'] = str(motd)
  elif event in ('ctcpreply', 'ctcpReply'):
    user, tag, data = (args + (None, None, None))[:3]
    v['$nick'] = user.split('!', 1)[0] if user else ''
    v['$user'] = user or ''
    v['$tag'] = tag or ''
    v['$data'] = data or ''
    v['$text'] = '%s %s' % (tag or '', data or '')
  # Always provide $network if we have conn
  v['$network'] = getattr(getattr(conn, 'client', None), 'network_key', '') or ''
  v['$me'] = getattr(conn, 'nickname', '') or ''
  return v

def _dispatch_on_hooks(internal_event, conn, args):
  """Fire /on hooks for *internal_event*.  Returns True if any hook fired."""
  # Find which /on event names map to this internal event
  matched_events = []
  for on_name, internal_name in _ON_EVENT_MAP.items():
    if internal_name == internal_event:
      matched_events.append(on_name)
  if not matched_events:
    return False

  fired = False
  for on_name in matched_events:
    hooks = state._on_hooks.get(on_name)
    if not hooks:
      continue
    variables = _on_hook_vars(on_name, conn, args)
    net = variables.get('$network', '')
    chan = variables.get('$channel', '')
    match_text = variables.get('$text', variables.get('$message', variables.get('$nick', '')))

    for hname, hinfo in list(hooks.items()):
      # Network filter
      if hinfo.get('network') and hinfo['network'].lower() != net.lower():
        continue
      # Channel filter
      if hinfo.get('channel') and hinfo['channel'].lower() != chan.lower():
        continue
      # Pattern filter (fnmatch against match_text)
      pattern = hinfo.get('pattern', '*')
      if pattern != '*' and not _mask_match(match_text, pattern):
        continue
      # Expand variables in command string
      cmd = hinfo['command']
      for var, val in variables.items():
        cmd = cmd.replace(var, str(val))
      # Execute
      window = hinfo.get('window')
      if window:
        _exec_command_string(window, cmd)
      fired = True
  return fired


# --- /exec evaluation context ---

def _build_exec_context(window):
  """Build the globals dict for /exec evaluation."""
  from commands import docommand
  from plugins import _make_irc_proxy
  conn = window.client.conn if window.client else None
  ctx = {
    # Core objects
    'window': window,
    'client': window.client,
    'conn': conn,
    'config': state.config,
    'clients': state.clients,
    'networks': _get_networks,  # call as networks() to get network_key -> info dict
    'app': state.app,
    'irc': _make_irc_proxy(),
    # Convenience functions
    'say': lambda msg, target=None: conn.say(target or _exec_target(window), msg) if conn else None,
    'msg': lambda target, msg: conn.say(target, msg) if conn else None,
    'notice': lambda target, msg: conn.sendLine("NOTICE %s :%s" % (target, msg)) if conn else None,
    'raw': lambda line: conn.sendLine(line) if conn else None,
    'join': lambda ch, key=None: conn.join(ch, key) if conn else None,
    'part': lambda ch=None, reason=None: conn.leave(ch or _exec_target(window), reason) if conn else None,
    'kick': lambda nick, reason=None, ch=None: conn.sendLine(
      "KICK %s %s :%s" % (ch or _exec_target(window), nick, reason) if reason
      else "KICK %s %s" % (ch or _exec_target(window), nick)) if conn else None,
    'mode': lambda modestr, ch=None: conn.sendLine("MODE %s %s" % (ch or _exec_target(window), modestr)) if conn else None,
    'echo': lambda text: window.addline(str(text)),
    'error': lambda text: window.redmessage(str(text)),
    'nick': lambda n=None: conn.setNick(n) if n and conn else (conn.nickname if conn else None),
    'me': lambda: conn.nickname if conn else None,
    'channel': lambda: _exec_target(window),
    'nicks': lambda ch=None: _exec_nicks(window, ch),
    'users': window.client.users,
    'user': lambda nick: window.client.users.get(conn.irclower(nick) if conn else nick.lower()) if nick else None,
    'history': lambda: window.channel.history if hasattr(window, 'channel') and window.channel else None,
    'irclower': lambda s, c=None: (c or conn).irclower(s) if (c or conn) else s.lower(),
    'irceq': lambda a, b, c=None: ((c or conn).irclower(a) == (c or conn).irclower(b)) if (c or conn) else a.lower() == b.lower(),
    'network': lambda: window.client.network_key,
    'docommand': lambda cmd, text="": docommand(window, cmd, text),
    # Timer and hook shortcuts
    'timer': lambda name, reps, secs, cmd: _exec_set_timer(window, name, reps, secs, cmd),
    'on': lambda event, name, pattern, cmd, **kw: _exec_set_on(window, event, name, pattern, cmd, **kw),
    # Modules available
    'asyncio': asyncio,
    're': re,
    'os': os,
    'time': time,
    'fnmatch': _fnmatch,
  }
  return ctx

def _exec_target(window):
  """Return the current channel/nick target for a window."""
  if window.type == "channel":
    return window.channel.name
  elif window.type == "query":
    return window.remotenick
  return ''

def _exec_nicks(window, ch=None):
  """Return the nick set for a channel."""
  if ch:
    for c in window.client.channels.values():
      if c.name.lower() == ch.lower():
        return set(c.nicks)
    return set()
  if window.type == "channel":
    return set(window.channel.nicks)
  return set()

def _exec_set_timer(window, name, reps, secs, cmd):
  """Create a timer from /exec: timer('foo', 0, 5, '/say hi')."""
  # Stop existing timer
  if name in state._timers:
    state._timers[name]['timer'].stop()
    del state._timers[name]
  t = QTimer()
  t.setInterval(int(secs * 1000))
  t.timeout.connect(lambda: _timer_fire(name))
  state._timers[name] = {
    'timer': t,
    'remaining': reps,
    'command': cmd,
    'window': window,
    'interval_ms': int(secs * 1000),
  }
  t.start()

def _exec_set_on(window, event, name, pattern, cmd, channel=None, network=None):
  """Create an /on hook from /exec: on('chanmsg', 'greet', '*hello*', '/say hi there')."""
  if event not in _ON_EVENT_MAP:
    window.redmessage("[Unknown event: %s]" % event)
    return
  if event not in state._on_hooks:
    state._on_hooks[event] = {}
  state._on_hooks[event][name] = {
    'pattern': pattern,
    'command': cmd,
    'channel': channel,
    'network': network,
    'window': window,
  }


def _get_networks():
  """Return a dict of network_key -> {'client': Client, 'channels': {name: Channel}}.

  Clients without a network_key are listed under None.
  """
  nets = {}
  for c in state.clients:
    key = c.network_key
    nets[key] = {
      'client': c,
      'channels': {ch.name: ch for ch in c.channels.values()},
      'users': c.users,
      'conn': c.conn,
    }
  return nets
