# irc_client.py - IRCClient subclass

import asyncio
import base64
import re
import time
import traceback
from datetime import datetime, timezone

import asyncirc
from PySide6.QtCore import QTimer

import state
from config import is_ignored, is_auto_op, is_highlight, get_highlight_notify
from models import (User, Channel, HistoryMessage, HistoryModeChange,
                    HistoryTopicChange, usersplit)
from window import Window

# Batch types that indicate playback (suppress DB saving, show separator on end)
_PLAYBACK_BATCH_TYPES = frozenset({'chathistory', 'znc.in/playback'})

# URL extraction for URL catcher
_MIRC_STRIP_RE = re.compile(
    r'\x02|\x1d|\x1f|\x16|\x0f|\x03(?:\d{1,2}(?:,\d{1,2})?)?')
_URL_RE = re.compile(r'https?://[^\s<>\x00-\x1f]+', re.IGNORECASE)


def _format_size(size):
  if not size: return '0 B'
  for unit in ('B', 'KB', 'MB', 'GB'):
    if abs(size) < 1024: return '%.1f %s' % (size, unit) if unit != 'B' else '%d %s' % (size, unit)
    size /= 1024.0
  return '%.1f TB' % size


def _extract_urls(text):
  """Extract URLs from message text (after stripping mIRC codes)."""
  clean = _MIRC_STRIP_RE.sub('', text)
  urls = []
  for url in _URL_RE.findall(clean):
    # Strip trailing punctuation unlikely to be part of the URL
    while url and url[-1] in '.,;:!?\'"':
      url = url[:-1]
    while url.endswith(')') and url.count(')') > url.count('('):
      url = url[:-1]
    if url:
      urls.append(url)
  return urls


def _save_urls(network, channel, nick, host, text):
  """Extract and save URLs from a message to the history DB."""
  db = state.historydb
  if not db:
    return
  for url in _extract_urls(text):
    db.add_url(network or '', channel.lower(), nick, host, url)


def _query_history_key(nick, ident):
  """Build a DB key for query history: =nick:ident (~ prefix stripped)."""
  ident = ident.lstrip('~') if ident else ''
  return '=%s:%s' % (nick.lower(), ident.lower())


def _find_or_create_query(conn, nick, ident, host):
  """Find an existing query by nick or create a new one.
  If an existing query was opened without ident/host (e.g. via /query),
  re-keys it with the proper (ident, host) tuple.
  Returns (query, is_new)."""
  from models import Query
  key = (ident, host)
  if key in conn.queries:
    return conn.queries[key], False
  # Check for query under a different key (e.g. (None, None) from /query)
  for qk, qv in list(conn.queries.items()):
    if qv.nick and conn.irclower(qv.nick) == conn.irclower(nick):
      q = conn.queries.pop(qk)
      q.ident = ident
      conn.queries[key] = q
      return q, False
  # Create new
  q = Query(conn.client, nick, ident)
  conn.queries[key] = q
  qhkey = _query_history_key(nick, ident)
  _history_replay(q.window, conn.client.network,
                  qhkey, limit=state.config.history_replay_queries)
  return q, True


def _history_save(network, channel, event_type, nick=None, text=None, prefix=''):
  """Save an event to the history database if available."""
  db = state.historydb
  if db:
    db.add(network, channel.lower(), event_type, nick, text, prefix)


def _history_replay(window, network, channel, limit=None, chan_obj=None):
  """Load saved history into a window."""
  db = state.historydb
  if limit is None:
    limit = state.config.history_replay_channels
  if not db or limit <= 0:
    return
  rows = db.get_last(network, channel.lower(), limit)
  if not rows:
    return
  window._replay_queue = []  # queue live messages during replay
  window._in_replay = True
  show_prefix = state.config.show_mode_prefix_messages
  history = chan_obj.history if chan_obj else None
  for ts, etype, nick, text, prefix in rows:
    # Show timestamp from DB instead of current time
    ts_short = ts[11:16]  # HH:MM from "YYYY-MM-DD HH:MM:SS"
    pn = (prefix + nick) if (show_prefix and prefix and nick) else nick
    if etype == 'message':
      window.addline_msg(pn, text, timestamp_override=ts_short)
    elif etype == 'action':
      window.addline_nick(["* ", (pn,), " %s" % text], state.actionformat,
                          timestamp_override=ts_short)
    elif etype == 'notice':
      window.addline_nick(["-", (pn,), "- %s" % text], state.noticeformat,
                          timestamp_override=ts_short)
    elif etype == 'join':
      window.addline_nick(["* ", (pn,), " has joined %s" % (text or channel)],
                          state.infoformat, timestamp_override=ts_short)
    elif etype == 'part':
      window.addline_nick(["* ", (pn,), " has left %s" % (text or channel)],
                          state.infoformat, timestamp_override=ts_short)
    elif etype == 'quit':
      window.addline_nick(["* ", (pn,), " has quit (%s)" % (text or "")],
                          state.infoformat, timestamp_override=ts_short)
    elif etype == 'kick':
      window.addline(text or '', state.infoformat, timestamp_override=ts_short)
    elif etype == 'nick':
      old, new = (nick, text) if text else (nick, '?')
      pold = (prefix + old) if (show_prefix and prefix) else old
      window.addline_nick(["* ", (pold,), " is now known as ", (new,)],
                          state.infoformat, timestamp_override=ts_short)
    elif etype == 'mode':
      window.addline_nick(["* ", (pn,), " %s" % (text or '')],
                          state.infoformat, timestamp_override=ts_short)
    elif etype == 'topic':
      window.addline_nick(["* ", (pn,), " changed the topic to: %s" % (text or '')],
                          state.infoformat, timestamp_override=ts_short)
    # Populate channel history buffer from DB rows
    if history is not None and nick and etype in (
        'message', 'action', 'notice', 'join', 'part', 'quit', 'kick'):
      msg = HistoryMessage(None, nick, text, etype, prefix=prefix or '')
      try:
        msg.time = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
      except (ValueError, TypeError):
        pass
      history.append(msg)
  window._in_replay = False
  window.add_separator(" End of saved history ")
  window._flush_replay_queue()


class IRCClient(asyncirc.IRCClient):

  # /on hooks (and plugins) can populate this for the duration of one
  # event dispatch via plugins._make_hook. See _hook_notify / _hook_activity.
  _suppress_flags = frozenset()

  def _hook_notify(self, *args, **kwargs):
    """Fire a notification unless the active /on hook suppressed it."""
    if 'notify' in self._suppress_flags:
      return
    if state.notifications:
      state.notifications.fire(*args, **kwargs)

  def _hook_activity(self, window, level):
    """Set window activity unless the active /on hook suppressed it."""
    if 'activity' in self._suppress_flags:
      return
    window.set_activity(level)

  def __init__(self, client):
    super().__init__()
    self.client = client
    # CTCP responses from config — expanded with normal {variables}
    from qtpyrc import APP_NAME, APP_VERSION
    from config import _expand_vars
    ver = state.config.ctcp_version
    if ver:
      variables = {'app_version': APP_VERSION, 'me': self.nickname or '',
                   'network': client.network_key or ''}
      self.versionName = _expand_vars(ver, variables)
      self.versionNum = ''
      self.versionEnv = ''
    else:
      self.versionName = APP_NAME
      self.versionNum = APP_VERSION
      self.versionEnv = 'Python/%s PySide6' % __import__('sys').version.split()[0]
    finger = state.config.ctcp_finger
    if finger:
      variables = {'app_version': APP_VERSION, 'me': self.nickname or '',
                   'network': client.network_key or ''}
      self.fingerReply = _expand_vars(finger, variables)
    else:
      self.fingerReply = None
    self.window = client.window
    self.channels = client.channels
    self.queries = client.queries

    nk = client.network_key
    ov = getattr(client, '_connect_overrides', {})
    self.nickname = ov.get('nick') or state.config.resolve(nk, 'nick')
    self.username = ov.get('user') or state.config.resolve(nk, 'user')
    self.realname = ov.get('realname') or state.config.resolve(nk, 'realname')
    # Password: override > ad-hoc > server-level > network-level (no global fallback)
    self.password = ov.get('password') or getattr(client, '_password', None)
    if not self.password and nk:
      servers = state.config.get_servers(nk)
      if servers and 'password' in servers[0]:
        self.password = servers[0]['password']
      else:
        self.password = state.config._net(nk).get('password')
    self._alt_nicks = list(ov.get('altnicks') or state.config.resolve(nk, 'alt_nicks') or [])
    self._skip_autojoin = ov.get('skip_autojoin', False)
    self._skip_on_connect = ov.get('skip_on_connect', False)
    self._login_method = ov.get('login_method')
    self._login_password = ov.get('login_password')
    self._alt_nick_idx = 0
    self._whois_windows = {}  # lowercased nick -> Window to display results in
    self._ctcp_windows = {}   # lowercased nick -> Window that sent CTCP request
    self._msg_windows = {}    # lowercased nick -> Window that last sent a PRIVMSG
    self._pending_keys = {}   # irclower(channel) -> key used in JOIN
    self._user_joins = set()  # irclower(channel) names from explicit /join
    self._user_parts = set()  # irclower(channel) names from explicit /part
    self._activate_on_join = set()  # irclower(channel) names to activate when joined
    self._hopping = set()     # irclower(channel) names currently being /hopped

    rl = state.config.resolve(nk, 'rate_limit')
    self.lineRate = rl if rl and rl > 0 else None
    global_flood = state.config.flood or {}
    net_flood = (state.config._net(nk).get('flood') or {}) if nk else {}
    fb = net_flood.get('burst', global_flood.get('burst'))
    fr = net_flood.get('rate', global_flood.get('rate'))
    if fb is not None:
      self.floodBurst = int(fb)
      self._flood_tokens = self.floodBurst
    if fr is not None:
      self.floodRate = float(fr)

  # --- IRCv3 capabilities ---

  def _get_desired_caps(self):
    caps = ['batch', 'server-time', 'message-tags']
    # SASL: override login method or config
    if self._login_method in ('sasl', 'external'):
      caps.append('sasl')
    else:
      nk = self.client.network_key
      if nk:
        net = state.config._net(nk)
        sasl = net.get('sasl')
        if sasl and sasl.get('username') and sasl.get('password'):
          caps.append('sasl')
        elif sasl and (sasl.get('mechanism') or '').upper() == 'EXTERNAL':
          caps.append('sasl')
    # ZNC playback
    caps.append('znc.in/playback')
    return caps

  @property
  def _sasl_in_progress(self):
    return getattr(self, '_sasl_active', False)

  def capsAcknowledged(self, caps):
    if 'sasl' in self._cap_enabled:
      # Override login method takes priority
      if self._login_method == 'external':
        self._sasl_mechanism = 'EXTERNAL'
        self._sasl_username = ''
        self._sasl_password = ''
      elif self._login_method == 'sasl' and self._login_password:
        self._sasl_mechanism = 'PLAIN'
        self._sasl_username = self.nickname
        self._sasl_password = self._login_password
      else:
        nk = self.client.network_key
        net = state.config._net(nk) if nk else {}
        sasl = net.get('sasl') or {}
        self._sasl_mechanism = (sasl.get('mechanism') or 'PLAIN').upper()
        self._sasl_username = sasl.get('username', '')
        self._sasl_password = sasl.get('password', '')
      self._sasl_active = True
      self._send_raw("AUTHENTICATE %s" % self._sasl_mechanism)

  def saslAuthenticate(self, data):
    if not getattr(self, '_sasl_active', False):
      return
    if data == '+':
      if self._sasl_mechanism == 'PLAIN':
        auth = '\0%s\0%s' % (self._sasl_username, self._sasl_password)
        encoded = base64.b64encode(auth.encode('utf-8')).decode('ascii')
        self._send_raw("AUTHENTICATE %s" % encoded)
      elif self._sasl_mechanism == 'EXTERNAL':
        self._send_raw("AUTHENTICATE +")

  def irc_RPL_LOGGEDIN(self, prefix, params):
    # params: [nick, nick!user@host, account, "You are now logged in as ..."]
    text = params[-1] if params else 'Logged in'
    self.window.addline('[%s]' % text)

  def irc_RPL_SASLSUCCESS(self, prefix, params):
    self._sasl_active = False
    self.window.addline('[SASL authentication successful]')
    self._end_cap_negotiation()

  def irc_ERR_SASLFAIL(self, prefix, params):
    self._sasl_active = False
    self.window.redmessage('[SASL authentication failed]')
    self._end_cap_negotiation()

  def irc_ERR_SASLTOOLONG(self, prefix, params):
    self._sasl_active = False
    self.window.redmessage('[SASL: credentials too long]')
    self._end_cap_negotiation()

  def irc_ERR_SASLABORTED(self, prefix, params):
    self._sasl_active = False
    self._end_cap_negotiation()

  # --- Typing notifications (IRCv3 +typing) ---

  def tagmsgReceived(self, nick, target, tags):
    if not state.config.typing_show:
      return
    typing = tags.get('+typing') or tags.get('+draft/typing')
    if typing is None:
      return
    active = typing == 'active'
    chnlower = self.irclower(target)
    chan = self.channels.get(chnlower)
    if chan:
      chan.window.set_nick_typing(nick, active)
      return
    # Query: target is our nick, sender is the query partner
    if self.irclower(target) == self.irclower(self.nickname):
      for q in self.queries.values():
        if self.irclower(q.nick) == self.irclower(nick):
          q.window.set_nick_typing(nick, active)
          return

  def _send_typing(self, target, typing_state):
    """Send a typing notification if the cap is enabled and configured."""
    if not state.config.typing_send:
      return
    if 'message-tags' not in self._cap_enabled:
      return
    self._send_raw("@+typing=%s TAGMSG %s" % (typing_state, target))

  # --- ISON / notify ---

  def isonReply(self, online_nicks):
    if state.notifications:
      state.notifications.handle_ison_reply(self, online_nicks)

  def monitorOnline(self, nicks):
    if state.notifications:
      state.notifications.handle_monitor_online(self, nicks)

  def monitorOffline(self, nicks):
    if state.notifications:
      state.notifications.handle_monitor_offline(self, nicks)

  # --- server-time helper ---

  def _nick_prefix(self, nick, channel):
    """Return the mode prefix symbol for nick in channel, or ''."""
    if not channel:
      return ''
    chnlower = self.irclower(channel)
    lnick = self.irclower(nick)
    user = self.client.users.get(lnick)
    if user:
      return user.prefix.get(chnlower, '')
    return ''

  def _pnick(self, nick, channel=None):
    """Return nick with mode prefix if show_mode_prefix_messages is enabled."""
    if not state.config.show_mode_prefix_messages or not channel:
      return nick
    pfx = self._nick_prefix(nick, channel)
    return pfx + nick if pfx else nick

  def _get_server_time(self):
    """Extract timestamp override from server-time tag, or None."""
    time_str = self._current_tags.get('time')
    if not time_str:
      return None
    try:
      dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
      dt_local = dt.astimezone()
      return dt_local.strftime('%H:%M')
    except Exception:
      return None

  # --- Batch (bouncer/ZNC playback) ---

  def _in_playback_batch(self):
    """Return True if the current message belongs to a playback batch."""
    batch_ref = self._current_tags.get('batch')
    if batch_ref and batch_ref in self._batches:
      return self._batches[batch_ref]['type'] in _PLAYBACK_BATCH_TYPES
    return False

  def _playback_window(self, params):
    """Return the window a playback batch targets, or None."""
    target = params[0] if params else None
    if not target:
      return None
    chnlower = self.irclower(target)
    if chnlower in self.channels:
      return self.channels[chnlower].window
    if target == '*' or target == self.nickname:
      return self.window
    return None

  def batchStarted(self, ref, batch_type, params):
    if batch_type not in _PLAYBACK_BATCH_TYPES:
      return
    w = self._playback_window(params)
    if w:
      label = " ZNC playback " if batch_type == 'znc.in/playback' else " Bouncer playback "
      w.add_separator(label)

  def batchEnded(self, ref, batch_type, params):
    if batch_type not in _PLAYBACK_BATCH_TYPES:
      return
    w = self._playback_window(params)
    if w:
      label = " End of ZNC playback " if batch_type == 'znc.in/playback' else " End of bouncer playback "
      w.add_separator(label)

  def _get_user(self, nick, ident=None, host=None):
    """Return the User for *nick*, creating one if needed.  Updates ident/host if given."""
    lnick = self.irclower(nick)
    user = self.client.users.get(lnick)
    if user is None:
      user = User(nick, ident, host)
      self.client.users[lnick] = user
    else:
      user.nick = nick  # keep canonical casing up-to-date
      if ident:
        user.ident = ident
      if host:
        user.host = host
    return user

  def _parse_user(self, hostmask):
    """Parse nick!ident@host and return (User, nick, ident, host)."""
    m = usersplit(hostmask)
    if m:
      nick, ident, host = m.groups()
    else:
      nick, ident, host = hostmask, None, None
    return self._get_user(nick, ident, host), nick, ident, host

  def networkChanged(self, networkname):
    self.client.networkname = networkname

  def connectionMade(self):
    super().connectionMade()
    self.client.conn = self
    self.window.redmessage('[Connected to %s]' % self.client.hostname)
    state.irclogger.log_server(self._log_network,
                         'Connected to %s' % self.client.hostname)
    if state.notifications and not self._skip_on_connect:
      state.notifications.fire('connect', 'Connected',
                               'Connected to %s' % self.client.hostname)

  @property
  def _log_network(self):
    """Network name for log file paths.  Prefers the server-reported name
    (keeps existing log filenames stable) but falls back to the config key
    or hostname so logs never land in 'unknown'."""
    return self.client.network or self.client.network_key or self.client.hostname or 'unknown'

  def _net_label(self):
    """Return the display label for this network, using the same fallback
    chain as channel titles: network_key -> network name -> hostname."""
    return self.client.network_key or self.client.network or self.client.hostname or 'unknown'

  def _update_server_title(self):
    from commands import expand_window_title
    self.client.window.setWindowTitle(
      expand_window_title(state.config.title_server, self.client.window))
    # Also refresh channel and query titles so they stay consistent
    for chan in self.channels.values():
      chan.update_title()
    for query in self.queries.values():
      query.update_title()

  def _ensure_connected(self):
    """Fallback for servers that send no 005 — mark connected after a delay."""
    if not self.client.connected and self.client.conn is self:
      self.client.connected = True
      from qtpyrc import _update_all_titles
      _update_all_titles()

  def connectionLost(self, reason):
    self.client.connected = False
    self.window.redmessage('[Connection lost: %s]' % reason)
    from commands import expand_window_title
    self.window.setWindowTitle(
      expand_window_title(state.config.title_server_disconnected, self.window))
    state.irclogger.log_server(self._log_network,
                         'Connection lost: %s' % reason)
    if state.notifications:
      state.notifications.fire('disconnect', 'Disconnected',
                               'Lost connection to %s' % (self.client.network or self.client.hostname))
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.update_client_label(self.client)
    # Update the main window title bar
    from qtpyrc import _update_all_titles
    _update_all_titles()
    # Handle channel windows on disconnect
    for chnlower in list(self.channels):
      chan = self.channels[chnlower]
      chan.window.redmessage('[Disconnected]')
      if state.config.close_on_disconnect:
        self._close_channel(chnlower)
      else:
        self._deactivate_channel(chnlower)

  def bounce(self, info):
    state.dbg(state.LOG_DEBUG, "bounced!")

  def _route_nick_error(self, params):
    """Route an error about a nick to the window that messaged it,
    falling back to query window, then server window."""
    # params: [my_nick, target_nick, "error text"]
    text = ' '.join(params[1:])
    if len(params) >= 2:
      nick = params[1]
      lnick = self.irclower(nick)
      # Check if a /msg set a reply route
      w = self._msg_windows.pop(lnick, None)
      if w:
        w.redmessage(text)
        return w
      # Fall back to open query window
      from commands import _find_query
      _, q = _find_query(self.client, nick)
      if q and q.window:
        q.window.redmessage(text)
        return q.window
    self.window.addline(text)
    return self.window

  def _route_chan_error(self, params):
    """Route a channel-targeted numeric error to the channel's window,
    falling back to the server window. Expects params shaped like
    [my_nick, #channel, ...message]."""
    text = ' '.join(p for p in params[1:] if p)
    if len(params) >= 2:
      chnlower = self.irclower(params[1])
      chan = self.client.channels.get(chnlower)
      if chan and chan.window:
        chan.window.redmessage(text)
        return chan.window
    self.window.redmessage(text)
    return self.window

  def irc_ERR_CANNOTSENDTOCHAN(self, prefix, params):
    # 404: [me, #channel, "Cannot send to channel ..."]
    self._route_chan_error(params)

  def irc_ERR_NOCHANMODES(self, prefix, params):
    # 477 — modern IRCds reuse this as ERR_NEEDREGGEDNICK
    # ("You need to be identified to a registered nickname")
    self._route_chan_error(params)

  def irc_ERR_NOTONCHANNEL(self, prefix, params):     self._route_chan_error(params)  # 442
  def irc_ERR_CHANNELISFULL(self, prefix, params):    self._route_chan_error(params)  # 471
  def irc_ERR_INVITEONLYCHAN(self, prefix, params):   self._route_chan_error(params)  # 473
  def irc_ERR_BANNEDFROMCHAN(self, prefix, params):   self._route_chan_error(params)  # 474
  def irc_ERR_BADCHANNELKEY(self, prefix, params):    self._route_chan_error(params)  # 475

  def irc_ERR_NOSUCHNICK(self, prefix, params):
    # Suppress for bouncer targets (*status, *perform, etc.)
    if len(params) >= 2 and params[1].startswith('*'):
      return
    self._route_nick_error(params)

  def irc_ERR_NOSUCHSERVER(self, prefix, params):
    self._route_nick_error(params)

  def irc_RPL_AWAY(self, prefix, params):
    # params: [my_nick, target_nick, away_message]
    # During WHOIS, route to the whois window; otherwise route to query
    if len(params) > 1:
      lnick = self.irclower(params[1])
      if lnick in self._whois_windows:
        w = self._whois_windows[lnick]
        w.addline("[%s] is away: %s" % (params[1], params[2] if len(params) > 2 else ''))
        return
    self._route_nick_error(params)

  def irc_CHGHOST(self, prefix, params):
    """IRCv3 CHGHOST — a user's ident or host changed (e.g. cloak set).
    :nick!ident@oldhost CHGHOST newident newhost"""
    m = asyncirc.usersplit(prefix) if prefix else None
    if not m:
      return
    nick = m.group(1)
    new_ident = params[0] if len(params) > 0 else None
    new_host = params[1] if len(params) > 1 else None
    self._get_user(nick, new_ident, new_host)

  def irc_SETNAME(self, prefix, params):
    """IRCv3 SETNAME — a user changed their realname.  Silently ignore."""
    pass

  def irc_ACCOUNT(self, prefix, params):
    """IRCv3 ACCOUNT — a user logged in/out of services.  Update user record."""
    m = asyncirc.usersplit(prefix) if prefix else None
    if not m:
      return
    nick = m.group(1)
    account = params[0] if params else '*'
    user = self._get_user(nick)
    user.account = account if account != '*' else None

  def irc_unknown(self, prefix, command, params):
    text = ' '.join(p for p in params[1:] if p)
    if not text.strip():
      return
    # Try to route to the relevant window based on params[1].
    # Most IRC error/reply numerics use the shape [my_nick, target, ...].
    # If target is a channel we're in, show the error there rather than
    # burying it in the server window where it's easy to miss.
    if len(params) >= 2:
      target = params[1]
      if target and target[0] in '#&!+':
        chnlower = self.irclower(target)
        chan = self.client.channels.get(chnlower)
        if chan and chan.window:
          chan.window.redmessage(text)
          return
      # If target is a nick with an open query, route there
      lnick = self.irclower(target)
      w = self._msg_windows.pop(lnick, None)
      if w:
        w.redmessage(text)
        return
      from commands import _find_query
      _, q = _find_query(self.client, target)
      if q and q.window:
        q.window.redmessage(text)
        return
    self.window.addline(text)

  def invited(self, nick, channel):
    self.window.addline("[%s invited you to %s]" % (nick, channel))

  # --- CTCP request tracking and reply routing ---

  def do_ctcp(self, nick, tag, data, from_window):
    """Send a CTCP query and remember which window to show the reply in."""
    self._ctcp_windows[self.irclower(nick)] = from_window
    if data:
      self.sendLine("PRIVMSG %s :\x01%s %s\x01" % (nick, tag, data))
    else:
      self.sendLine("PRIVMSG %s :\x01%s\x01" % (nick, tag))

  def ctcpReply(self, user, tag, data):
    nick = user.split('!', 1)[0]
    lnick = self.irclower(nick)
    w = self._ctcp_windows.pop(lnick, None) or self.window
    tag_upper = tag.upper()
    if tag_upper == 'PING' and data:
      try:
        sent = int(data)
        elapsed = int(time.time()) - sent
        w.addline("[CTCP PING reply from %s: %ds]" % (nick, elapsed))
      except (ValueError, TypeError):
        w.addline("[CTCP PING reply from %s: %s]" % (nick, data))
    elif tag_upper == 'VERSION':
      w.addline("[CTCP VERSION reply from %s: %s]" % (nick, data or ''))
    elif tag_upper == 'FINGER':
      w.addline("[CTCP FINGER reply from %s: %s]" % (nick, data or ''))
    elif tag_upper == 'TIME':
      w.addline("[CTCP TIME reply from %s: %s]" % (nick, data or ''))
    else:
      w.addline("[CTCP %s reply from %s: %s]" % (tag, nick, data or ''))

  # --- DCC CTCP handling ---

  def ctcp_PrivDCC(self, user, data):
    """Handle incoming DCC CTCP requests (SEND, CHAT, RESUME, ACCEPT)."""
    from dcc import parse_dcc_request, DCCTransfer, DCCChat, Direction, Status
    nick = user.split('!', 1)[0]
    req = parse_dcc_request(data)
    if not req:
      state.dbg(state.LOG_DEBUG, '[dcc] Unparseable DCC request from %s: %s' % (nick, data))
      return

    mgr = state.dcc_manager
    if not mgr:
      return

    dtype = req['type']

    if dtype == 'SEND':
      # Incoming file offer
      filesize = req.get('filesize', 0)
      filename = req.get('filename', '')

      # Check if this is a reverse DCC response (we sent with token, they reply with ip/port/token)
      if req.get('token'):
        for xfer in mgr.transfers.values():
          if (xfer.direction == Direction.SEND and xfer.token == req['token'] and
              xfer.nick.lower() == nick.lower()):
            xfer.host = req['host']
            xfer.port = req['port']
            xfer._task = asyncio.ensure_future(self._dcc_reverse_send_connect(xfer))
            return

      # Check max filesize
      if state.config.dcc_max_filesize and filesize > state.config.dcc_max_filesize * 1024 * 1024:
        self.window.redmessage('[DCC SEND from %s rejected: %s exceeds max filesize (%d MB)]' % (
          nick, filename, state.config.dcc_max_filesize))
        return

      # Check file type filter
      if state.config.dcc_file_filter_mode != 'disabled' and state.config.dcc_file_filter:
        import os
        ext = os.path.splitext(filename)[1].lower()
        filter_list = [e.lower() for e in state.config.dcc_file_filter]
        if state.config.dcc_file_filter_mode == 'blacklist' and ext in filter_list:
          self.window.redmessage('[DCC SEND from %s rejected: %s is a blocked file type]' % (
            nick, filename))
          return
        elif state.config.dcc_file_filter_mode == 'whitelist' and ext not in filter_list:
          self.window.redmessage('[DCC SEND from %s rejected: %s is not an allowed file type]' % (
            nick, filename))
          return

      # Check auto-accept mode
      auto = state.config.dcc_auto_accept
      is_trusted = self._is_trusted_host(user)

      # "trusted" mode: reject non-trusted users entirely
      if auto == 'trusted' and not is_trusted:
        self.window.redmessage('[DCC SEND from %s rejected: not a trusted user]' % nick)
        return

      xfer = DCCTransfer(
        self.client, nick, filename, filesize, Direction.RECEIVE,
        host=req['host'], port=req['port'], token=req.get('token'))
      mgr.transfers[xfer.id] = xfer

      self.window.addline('[DCC SEND from %s: %s (%s)]' % (
        nick, xfer.filename, _format_size(filesize)))

      # Decide whether to accept
      if auto == 'always':
        asyncio.ensure_future(mgr.accept_receive(xfer))
      elif auto == 'trusted' and is_trusted:
        asyncio.ensure_future(mgr.accept_receive(xfer))
      elif auto == 'known' and (is_trusted or self._is_known_user(nick)):
        asyncio.ensure_future(mgr.accept_receive(xfer))
      elif state.config.dcc_show_get_dialog:
        from dcc_ui import show_accept_dialog_nonblocking
        show_accept_dialog_nonblocking(xfer, mgr)
      else:
        # No dialog, not auto-accepted — leave pending
        self.window.addline('[DCC SEND from %s pending — use /dcc get %s to accept]' % (
          nick, xfer.id))

    elif dtype == 'CHAT':
      chat = DCCChat(self.client, nick, host=req['host'], port=req['port'])
      mgr.chats[chat.id] = chat
      self.window.addline('[DCC CHAT request from %s]' % nick)
      # Auto-accept chat
      asyncio.ensure_future(mgr.accept_chat(chat))

    elif dtype == 'RESUME':
      mgr.handle_resume(nick, req['filename'], req['port'], req['position'])

    elif dtype == 'ACCEPT':
      mgr.handle_accept(nick, req['filename'], req['port'], req['position'])

  async def _dcc_reverse_send_connect(self, xfer):
    """Connect to the receiver for a reverse DCC send."""
    from dcc import Status, _notify_transfer
    try:
      xfer.status = Status.CONNECTING
      _notify_transfer(xfer, 'Connecting to %s for reverse DCC SEND' % xfer.nick)
      reader, writer = await asyncio.wait_for(
        asyncio.open_connection(xfer.host, xfer.port),
        timeout=state.config.dcc_timeout)
      await state.dcc_manager._do_send(xfer, reader, writer)
    except asyncio.TimeoutError:
      xfer.status = Status.FAILED
      xfer.error = 'Connection timed out'
      _notify_transfer(xfer, 'DCC SEND to %s timed out (reverse)' % xfer.nick)
    except asyncio.CancelledError:
      xfer.status = Status.CANCELLED
    except Exception as e:
      xfer.status = Status.FAILED
      xfer.error = str(e)
      _notify_transfer(xfer, 'DCC SEND to %s failed: %s' % (xfer.nick, e))

  def _is_known_user(self, nick):
    """Check if nick is in one of our channels or has an open query."""
    lnick = self.irclower(nick)
    for chan in self.client.channels.values():
      if lnick in {self.irclower(n) for n in chan.nicks}:
        return True
    for q in self.client.queries.values():
      if q.nick and self.irclower(q.nick) == lnick:
        return True
    return False

  def _is_trusted_host(self, hostmask):
    """Check if a hostmask matches any trusted_hosts pattern."""
    import fnmatch as _fnmatch
    trusted = state.config.dcc_trusted_hosts
    if not trusted:
      return False
    hostmask_lower = hostmask.lower()
    for pattern in trusted:
      if _fnmatch.fnmatch(hostmask_lower, pattern.lower()):
        return True
    return False

  def irc_ERR_NICKNAMEINUSE(self, prefix, params):
    # Try alt_nicks first, then fall back to appending _
    if self._alt_nick_idx < len(self._alt_nicks):
      newnick = self._alt_nicks[self._alt_nick_idx]
      self._alt_nick_idx += 1
    else:
      tried = getattr(self, '_pending_nick', self.nickname)
      newnick = tried + '_'
    self.setNick(newnick)

  # --- WHOIS routing ---

  def do_whois(self, nick, from_window):
    """Initiate a WHOIS and remember which window to show results in."""
    self._whois_windows[self.irclower(nick)] = from_window
    self.sendLine("WHOIS %s" % nick)

  def _whois_window(self, params):
    """Return the window a WHOIS reply should go to (params[1] is the nick)."""
    if len(params) > 1:
      w = self._whois_windows.get(self.irclower(params[1]))
      if w:
        return w
    return self.window  # fallback to server window

  def irc_RPL_WHOISUSER(self, prefix, params):
    # params: [me, nick, user, host, *, realname]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    ident = params[2] if len(params) > 2 else ''
    host = params[3] if len(params) > 3 else ''
    realname = params[5] if len(params) > 5 else ''
    # Update user tracking
    uobj = self._get_user(nick, ident, host)
    if realname:
      uobj.realname = realname
    w.addline("[%s] (%s@%s): %s" % (nick, ident, host, realname))

  def irc_RPL_WHOISSERVER(self, prefix, params):
    # params: [me, nick, server, serverinfo]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    srv = params[2] if len(params) > 2 else ''
    info = params[3] if len(params) > 3 else ''
    uobj = self._get_user(nick)
    uobj.server = srv
    w.addline("[%s] %s (%s)" % (nick, srv, info))

  def irc_RPL_WHOISOPERATOR(self, prefix, params):
    # params: [me, nick, "is an IRC operator"]
    w = self._whois_window(params)
    w.addline("[%s] %s" % (params[1], ' '.join(params[2:])))

  def irc_RPL_WHOISIDLE(self, prefix, params):
    # params: [me, nick, seconds_idle, signon_time, "seconds idle, signon time"]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    idle = int(params[2]) if len(params) > 2 else 0
    mins, secs = divmod(idle, 60)
    hrs, mins = divmod(mins, 60)
    idle_str = "%dh %dm %ds" % (hrs, mins, secs) if hrs else "%dm %ds" % (mins, secs)
    signon = ''
    if len(params) > 3 and params[3].isdigit():
      signon = " signon: %s" % datetime.fromtimestamp(int(params[3])).strftime('%Y-%m-%d %H:%M:%S')
    w.addline("[%s] idle: %s%s" % (nick, idle_str, signon))

  def irc_RPL_WHOISCHANNELS(self, prefix, params):
    # params: [me, nick, "#chan1 @#chan2 +#chan3"]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    channels = params[2] if len(params) > 2 else ''
    parts = ["[%s] channels: " % nick]
    first = True
    for tok in channels.split():
      if not first:
        parts.append(' ')
      first = False
      # Strip leading mode-prefix symbols (@%+~&) for the join target,
      # but display the original token so ops/voice marks remain visible.
      bare = tok.lstrip('~&@%+')
      if bare.startswith(('#', '&', '!', '+')):
        parts.append((tok, 'chan:' + bare))
      else:
        parts.append(tok)
    w.addline_nick(parts)

  def irc_RPL_ENDOFWHOIS(self, prefix, params):
    # params: [me, nick, "End of /WHOIS list"]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    w.addline("[%s] End of WHOIS" % nick)
    # Delay cleanup — some servers send non-standard numerics (671, 338)
    # after the end-of-whois marker
    lnick = self.irclower(nick)
    QTimer.singleShot(2000, lambda: self._whois_windows.pop(lnick, None))

  # Also handle 330 (logged in as) and 671 (using secure connection) which
  # are common non-standard WHOIS numerics — they arrive as irc_unknown
  # since they're not in the symbolic map, so we intercept in handleCommand.

  # --- WHOWAS routing ---

  def do_whowas(self, nick, from_window):
    """Initiate a WHOWAS and remember which window to show results in."""
    self._whois_windows[self.irclower(nick)] = from_window
    self.sendLine("WHOWAS %s" % nick)

  def irc_RPL_WHOWASUSER(self, prefix, params):
    # params: [me, nick, user, host, *, realname]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    ident = params[2] if len(params) > 2 else ''
    host = params[3] if len(params) > 3 else ''
    realname = params[5] if len(params) > 5 else ''
    w.addline("[%s] was (%s@%s): %s" % (nick, ident, host, realname))

  def irc_RPL_ENDOFWHOWAS(self, prefix, params):
    # params: [me, nick, "End of WHOWAS"]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    w.addline("[%s] End of WHOWAS" % nick)
    lnick = self.irclower(nick)
    QTimer.singleShot(2000, lambda: self._whois_windows.pop(lnick, None))

  def irc_ERR_WASNOSUCHNICK(self, prefix, params):
    # params: [me, nick, "There was no such nickname"]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    msg = params[2] if len(params) > 2 else 'There was no such nickname'
    w.addline("[%s] %s" % (nick, msg))
    self._whois_windows.pop(self.irclower(nick), None)

  # --- Channel details numerics ---

  def irc_RPL_CHANNELMODEIS(self, prefix, params):
    # params: [me, #channel, +modes, arg1, arg2, ...]
    if len(params) < 3:
      return
    channel = params[1]
    mode_string = params[2]
    mode_args = params[3:]
    chnlower = self.irclower(channel)
    chan = self.client.channels.get(chnlower)
    if chan:
      chan.modes = mode_string
      chan.mode_args = list(mode_args)
      dlg = getattr(chan, '_details_dialog', None)
      if dlg:
        dlg.update_modes(mode_string, mode_args)

  def irc_TOPICDATE(self, prefix, params):
    # 333: [me, #channel, setter, timestamp]
    if len(params) < 4:
      return
    channel = params[1]
    setter = params[2]
    try:
      from datetime import datetime
      ts = datetime.fromtimestamp(int(params[3])).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OSError):
      ts = params[3]
    chnlower = self.irclower(channel)
    chan = self.client.channels.get(chnlower)
    if chan:
      chan.topic_setter = setter
      chan.topic_time = ts

  def _list_entry(self, mode_char, params):
    """Generic handler for list mode entry numerics (367, 348, 346, 728)."""
    if len(params) < 3:
      return
    channel = params[1]
    mask = params[2]
    setter = params[3] if len(params) > 3 else ''
    try:
      from datetime import datetime
      ts_raw = params[4] if len(params) > 4 else ''
      ts = datetime.fromtimestamp(int(ts_raw)).strftime('%Y-%m-%d %H:%M:%S') if ts_raw else ''
    except (ValueError, OSError):
      ts = params[4] if len(params) > 4 else ''
    chnlower = self.irclower(channel)
    chan = self.client.channels.get(chnlower)
    if chan:
      pending = chan._pending_lists.setdefault(mode_char, [])
      pending.append((mask, setter, ts))

  def _list_end(self, mode_char, params):
    """Generic handler for end-of-list numerics (368, 349, 347, 729)."""
    if len(params) < 2:
      return
    channel = params[1]
    chnlower = self.irclower(channel)
    chan = self.client.channels.get(chnlower)
    if chan:
      entries = list(chan._pending_lists.pop(mode_char, []))
      dlg = getattr(chan, '_details_dialog', None)
      if dlg:
        dlg.update_list(mode_char, entries)

  # Bans (+b): 367 / 368
  def irc_RPL_BANLIST(self, prefix, params):
    self._list_entry('b', params)

  def irc_RPL_ENDOFBANLIST(self, prefix, params):
    self._list_end('b', params)

  # Ban exceptions (+e): 348 / 349
  def irc_RPL_EXCEPTLIST(self, prefix, params):
    self._list_entry('e', params)

  def irc_RPL_ENDOFEXCEPTLIST(self, prefix, params):
    self._list_end('e', params)

  # Invite exceptions (+I): 346 / 347
  def irc_RPL_INVITELIST(self, prefix, params):
    self._list_entry('I', params)

  def irc_RPL_ENDOFINVITELIST(self, prefix, params):
    self._list_end('I', params)

  # Quiets (+q): 728 / 729 (non-standard, Libera/freenode)
  def irc_RPL_QUIETLIST(self, prefix, params):
    # 728 params: [me, #channel, q, mask, setter, timestamp]
    # The 'q' at index 2 is the mode char; shift params to match standard format
    if len(params) >= 4 and params[2] == 'q':
      shifted = [params[0], params[1]] + list(params[3:])
      self._list_entry('q', shifted)
    else:
      self._list_entry('q', params)

  def irc_RPL_ENDOFQUIETLIST(self, prefix, params):
    self._list_end('q', params)

  def irc_ERR_CHANOPRIVSNEEDED(self, prefix, params):
    # params: [me, #channel, "You're not a channel operator"]
    if len(params) < 2:
      return
    channel = params[1]
    chnlower = self.irclower(channel)
    chan = self.client.channels.get(chnlower)
    if chan:
      dlg = getattr(chan, '_details_dialog', None)
      if dlg:
        dlg.update_access_denied(channel)
        return
    self._route_chan_error(params)

  # --- Channel list numerics ---

  def irc_RPL_LISTSTART(self, prefix, params):
    pass  # 321 — just a header, nothing to do

  def irc_RPL_LIST(self, prefix, params):
    # 322: [me, #channel, visible_count, :topic]
    if len(params) < 3:
      return
    channel = params[1]
    try:
      users = int(params[2])
    except (ValueError, TypeError):
      users = 0
    topic = params[3] if len(params) > 3 else ''
    dlg = getattr(self.client, '_list_dialog', None)
    if dlg:
      dlg.add_entry(channel, users, topic)
    else:
      # No dialog open — show in server window
      self.window.addline('[LIST] %s (%d) %s' % (channel, users, topic))

  def irc_RPL_LISTEND(self, prefix, params):
    # 323: end of LIST
    dlg = getattr(self.client, '_list_dialog', None)
    if dlg:
      dlg.list_end()

  def irc_RPL_WELCOME(self, prefix, params):
    super().irc_RPL_WELCOME(prefix, params)
    # Use network_key as default; ISUPPORT NETWORK= will correct it later if available
    network = self.client.network_key or self.client.hostname or 'unknown'

    if network != self.client.network:
      self.networkChanged(network)
    self.client.network = network
    # Delay marking connected so ISUPPORT NETWORK= resolves first (see isupport()).
    # Fall back here for ancient servers that send no 005 at all.
    QTimer.singleShot(2000, lambda: self._ensure_connected())
    self._update_server_title()
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.update_client_label(self.client)

    # Note: we intentionally do NOT auto-match ad-hoc /server connections
    # to config network keys. Use /connect <network> or /server <network>
    # to get config settings (autojoin, SASL, etc.).

  def isupport(self, options):
    super().isupport(options)
    # If NETWORK= was parsed, update the window title with the correct name
    if self._network_name and self._network_name != self.client.network:
      self.networkChanged(self._network_name)
      self.client.network = self._network_name
      self._update_server_title()
      tree = getattr(state.app.mainwin, 'network_tree', None)
      if tree:
        tree.update_client_label(self.client)

    # Autojoin channels (skip if -o flag was used)
    if not self._skip_autojoin:
      self._autojoin_pending = set()
      autojoins = state.config.get_autojoins(self.client.network_key)
      for channel, key in autojoins.items():
        self._autojoin_pending.add(self.irclower(channel))
        self.join(channel, key)
    else:
      self._autojoin_pending = set()

    # NickServ/MSG login method (post-registration, non-SASL)
    if self._login_method in ('nickserv', 'msg') and self._login_password:
      if self._login_method == 'msg':
        self.msg('NickServ', 'IDENTIFY %s %s' % (self.nickname, self._login_password))
      else:
        self.msg('NickServ', 'IDENTIFY %s' % self._login_password)

    # If MONITOR just became available, send the notify list
    if self._monitor_supported and not getattr(self, '_monitor_synced', False) and state.notifications:
      self._monitor_synced = True
      state.notifications.sync_monitor(self)

    # Mark connected after ISUPPORT so NETWORK= is resolved before titlebar update
    if not self.client.connected:
      self.client.connected = True
      from qtpyrc import _update_all_titles
      _update_all_titles()

  _SASL_NUMERICS = frozenset({
    'RPL_LOGGEDIN', 'RPL_LOGGEDOUT', 'ERR_NICKLOCKED',
    'RPL_SASLSUCCESS', 'ERR_SASLFAIL', 'ERR_SASLTOOLONG',
    'ERR_SASLABORTED', 'ERR_SASLALREADY', 'RPL_SASLMECHS',
  })
  _WHOIS_NUMERICS = frozenset({
    'RPL_WHOISUSER', 'RPL_WHOISSERVER', 'RPL_WHOISOPERATOR',
    'RPL_WHOISIDLE', 'RPL_ENDOFWHOIS', 'RPL_WHOISCHANNELS',
    'RPL_WHOWASUSER', 'RPL_ENDOFWHOWAS', 'ERR_WASNOSUCHNICK',
  })
  # Non-standard WHOIS numerics that aren't in the symbolic map and arrive
  # as raw number strings:  330 = logged-in-as, 338 = actually-using-host,
  # 671 = is using a secure connection
  _WHOIS_RAW_NUMERICS = frozenset({'275', '330', '338', '671'})

  def handleCommand(self, command, prefix, params):
    # Route non-standard WHOIS numerics to the requesting window
    if command in self._WHOIS_RAW_NUMERICS and len(params) > 1:
      w = self._whois_window(params)
      # 330 = "is logged in as" — populate user.account
      if command == '330' and len(params) > 2:
        uobj = self._get_user(params[1])
        uobj.account = params[2]
      w.addline("[%s] %s" % (params[1], ' '.join(params[2:])))
      return
    super().handleCommand(command, prefix, params)
    # Don't echo WHOIS replies to the server window — the irc_RPL_WHOIS*
    # handlers already route them to the correct window.
    if command in self._WHOIS_NUMERICS or command in self._SASL_NUMERICS:
      return
    if command in ('CAP', 'BATCH', 'AUTHENTICATE', 'TAGMSG', 'RPL_ISON',
                   '730', '731', '732', '733', '734',
                   'CHGHOST', 'SETNAME', 'ACCOUNT'):
      return
    if command in ('RPL_WELCOME', 'RPL_YOURHOST', 'RPL_CREATED', 'RPL_MYINFO',
                   'RPL_USERHOST', 'RPL_LUSERCLIENT', 'RPL_LUSERUNKNOWN', 'RPL_LUSERME',
                   'RPL_ADMINME', 'RPL_ADMINLOC', 'RPL_STANTSONLINE', 'RPL_TRYAGAIN', 'ERROR', '265', '266',
                   'RPL_MOTD', 'RPL_ENDOFMOTD', 'RPL_LUSEROP', 'RPL_LUSERCHANNELS', 'RPL_MOTDSTART',
                   'RPL_ISUPPORT'):
      text = ' '.join(p for p in params[1:] if p)
      if not text.strip():
        return  # don't show empty timestamp-only lines
      self.window.addline(text)
      state.irclogger.log_server(self._log_network, text)
      if not self._in_playback_batch():
        from link_preview import check_and_preview
        check_and_preview(self.window, text)
    else:
      state.dbg(state.LOG_TRACE, "irc:", command, params)

  def noticed(self, user, channel, message):
    if is_ignored(user, self.client.network_key):
      return
    nick = user.split('!', 1)[0]
    m = usersplit(user)
    notice_host = '%s@%s' % (m.group('ident'), m.group('host')) if m else ''
    ts = self._get_server_time()
    chnlower = self.irclower(channel)
    if chnlower in self.client.channels:
      chan = self.client.channels[chnlower]
      pn = self._pnick(nick, channel)
      target_win = chan.window
      target_win.addline_nick(["-", (pn,), "- %s" % message], state.noticeformat,
                              timestamp_override=ts)
      self._hook_activity(target_win, Window.ACTIVITY_MESSAGE)
      if not self._in_playback_batch():
        _history_save(self._log_network, channel, 'notice', nick, message,
                      prefix=self._nick_prefix(nick, channel))
        _save_urls(self._log_network, channel, nick, notice_host, message)
    else:
      # Show in active window if it belongs to this network, else server window
      target_win = self.window
      active_sub = state.app.mainwin.workspace.activeSubWindow()
      if active_sub:
        aw = active_sub.widget()
        if aw and getattr(aw, 'client', None) is self.client:
          target_win = aw
      target_win.addline_nick(["-", (nick,), "- %s" % message], state.noticeformat,
                              timestamp_override=ts)
    if not self._in_playback_batch() and not target_win._is_active_window():
      self._hook_notify('notice', 'Notice from %s' % nick, message)
    # Link previews for notices (defer during replay)
    if not self._in_playback_batch():
      from link_preview import check_and_preview
      if not target_win.queue_replay_callback(
          lambda w=target_win, m=message: check_and_preview(w, m)):
        check_and_preview(target_win, message)

  def action(self, user, channel, data):
    if is_ignored(user, self.client.network_key, channel):
      return
    uobj, nick, ident, host = self._parse_user(user)
    ts = self._get_server_time()
    playback = self._in_playback_batch()
    chnlower = self.irclower(channel)
    if chnlower in self.client.channels:
      chan = self.client.channels[chnlower]
      if nick in chan.window._typing_nicks:
        chan.window.set_nick_typing(nick, False)
      pfx = self._nick_prefix(nick, channel)
      chan.history.append(HistoryMessage(uobj, nick, data, 'action', prefix=pfx))
      chan.window.addline_nick(["* ", (self._pnick(nick, channel),), " %s" % data], state.actionformat,
                              timestamp_override=ts)
      state.irclogger.log_channel(self._log_network, channel,
                            "* %s %s" % (nick, data))
      if not playback:
        _history_save(self._log_network, channel, 'action', nick, data,
                      prefix=pfx)
        _save_urls(self._log_network, channel,
                   nick, '%s@%s' % (ident, host), data)
      nk = self.client.network_key
      if is_highlight(data, self.nickname, nk, channel):
        self._hook_activity(chan.window, Window.ACTIVITY_HIGHLIGHT)
        if not playback and not chan.window._is_active_window():
          if get_highlight_notify(nk, channel):
            self._hook_notify('highlight', '%s in %s' % (nick, channel), data)
      else:
        self._hook_activity(chan.window, Window.ACTIVITY_MESSAGE)
    elif chnlower == self.irclower(self.nickname):
      # Private action
      q, _ = _find_or_create_query(self, nick, ident, host)
      q.window.addline_nick(["* ", (nick,), " %s" % data], state.actionformat,
                                                    timestamp_override=ts)
      state.irclogger.log(self._log_network, nick,
                          "* %s %s" % (nick, data))
      self._hook_activity(self.queries[ident, host].window, Window.ACTIVITY_HIGHLIGHT)
      if not playback:
        _history_save(self._log_network, _query_history_key(nick, ident), 'action', nick, data)
        _save_urls(self._log_network, _query_history_key(nick, ident),
                   nick, '%s@%s' % (ident, host), data)

  def join(self, channel, key=None):
    if key:
      self._pending_keys[self.irclower(channel)] = key
    super().join(channel, key)

  def joined(self, chname):
    chnlower = self.irclower(chname)
    self._hopping.discard(chnlower)
    # If this channel was an autojoin and the bouncer already joined us,
    # remove the redundant JOIN from the send queue.
    autojoin_pending = getattr(self, '_autojoin_pending', None)
    if autojoin_pending and chnlower in autojoin_pending:
      autojoin_pending.discard(chnlower)
      self._queue = [line for line in self._queue
                     if not (line.startswith('JOIN ') and
                             self.irclower(line.split()[1]) == chnlower)]
    pending_key = self._pending_keys.pop(chnlower, None)
    if chnlower in self.channels:
      chan = self.channels[chnlower]
      chan.rejoined()
      if pending_key is not None:
        chan.key = pending_key
    else:
      chan = Channel(self.client, chname)
      if pending_key is not None:
        chan.key = pending_key
      self.channels[chnlower] = chan
      # Defer history replay — background drip-feed, or immediate on activation
      # Queue live messages until replay finishes
      chan.window._replay_queue = []
      chan.window._deferred_replay = (self._log_network, chname, chan)
      from qtpyrc import _queue_bg_replay
      _queue_bg_replay(chan.window, self._log_network, chname, chan)
    ts = self._get_server_time()
    chan.window.addline_nick(["* ", (self.nickname,), " has joined %s" % chname], state.infoformat,
                            timestamp_override=ts)
    if not self._in_playback_batch():
      _history_save(self._log_network, chname, 'join', self.nickname, chname,
                    prefix=self._nick_prefix(self.nickname, chname))
    # Activate the window for user-initiated /join (not bouncer/server joins)
    if chnlower in self._activate_on_join:
      self._activate_on_join.discard(chnlower)
      state.app.mainwin.workspace.setActiveSubWindow(chan.window.subwindow)
    # persist autojoin only for user-initiated /join (not bouncer/server joins)
    if chnlower in self._user_joins:
      self._user_joins.discard(chnlower)
      if state.config.resolve(self.client.network_key, 'persist_autojoins'):
        state.config.update_autojoin(self.client.network_key, chname, key=chan.key)

  def _close_channel(self, chnlower):
    """Remove a channel entirely — window, tree node, and Channel object."""
    chan = self.channels.pop(chnlower, None)
    if not chan:
      return
    chan.active = False
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.remove_channel(self.client, chan)
    state.app.mainwin.workspace.removeSubWindow(chan.window.subwindow)

  def _deactivate_channel(self, chnlower):
    """Mark a channel as inactive (kicked/disconnected) but keep the window."""
    chan = self.channels.get(chnlower)
    if chan:
      chan.active = False
      chan.nicks.clear()
      chan.users.clear()
      chan.window.nickslist.clear()
      chan.update_title()

  def left(self, channel):
    chnlower = self.irclower(channel)
    if chnlower not in self.channels:
      return
    chan = self.channels[chnlower]
    ts = self._get_server_time()
    chan.window.addline_nick(["* ", (self.nickname,), " has left %s" % channel], state.infoformat,
                            timestamp_override=ts)
    if not self._in_playback_batch():
      _history_save(self._log_network, channel, 'part', self.nickname, channel,
                    prefix=self._nick_prefix(self.nickname, channel))
    if chnlower in self._hopping:
      self._deactivate_channel(chnlower)
    else:
      self._close_channel(chnlower)
      # persist autojoin removal only for user-initiated /part
      if chnlower in self._user_parts:
        self._user_parts.discard(chnlower)
        if state.config.resolve(self.client.network_key, 'persist_autojoins'):
          state.config.update_autojoin(self.client.network_key, channel, remove=True)

  def kickedFrom(self, channel, kicker, message):
    chnlower = self.irclower(channel)
    chan = self.channels.get(chnlower)
    if chan:
      chan.window.redmessage('[Kicked from %s by %s (%s)]' % (channel, self._pnick(kicker, channel), message))
      state.irclogger.log_channel(self._log_network, channel,
                            'Kicked by %s (%s)' % (kicker, message))
      if not self._in_playback_batch():
        _history_save(self._log_network, channel, 'kick', kicker,
                      'Kicked from %s by %s (%s)' % (channel, kicker, message),
                      prefix=self._nick_prefix(kicker, channel))
    if state.config.close_on_kick:
      self._close_channel(chnlower)
    else:
      self._deactivate_channel(chnlower)

  def names(self, chname, names):
    chnlower = self.irclower(chname)
    chan = self.channels.get(chnlower)
    if not chan:
      return
    for nick in names:
      # Strip mode prefixes (@, +, %, ~, &)
      raw = nick.lstrip('@+%~&')
      prefix = nick[:len(nick) - len(raw)]
      user = self._get_user(raw)
      if prefix:
        user.prefix[chnlower] = prefix
      chan.addnick(raw, user)

  def privmsg(self, user, message):
    if is_ignored(user, self.client.network_key):
      return
    self._parse_user(user)
    nick, ident, host = asyncirc.usersplit(user).groups()
    ts = self._get_server_time()
    q, new_query = _find_or_create_query(self, nick, ident, host)
    qwin = q.window
    qwin.addline_msg(nick, message, timestamp_override=ts)
    self._hook_activity(qwin, Window.ACTIVITY_HIGHLIGHT)
    if hasattr(qwin, '_typing_timer') and qwin._typing_timer is not None:
      qwin.set_nick_typing(nick, False)
    state.irclogger.log(self._log_network, nick,
                        "<%s> %s" % (nick, message))
    if not self._in_playback_batch():
      _history_save(self._log_network, _query_history_key(nick, ident), 'message', nick, message)
      _save_urls(self._log_network, _query_history_key(nick, ident),
                 nick, '%s@%s' % (ident, host), message)
      # Notify for all private messages unless the app is focused and the
      # query is the active window (same behavior as channel highlights).
      app_focused = state.app and state.app.mainwin.isActiveWindow()
      if not app_focused or not qwin._is_active_window():
        self._hook_notify('new_query', 'Message from %s' % nick, message)
      if new_query and state.config.whois_on_query:
        self.do_whois(nick, qwin)
      from link_preview import check_and_preview
      check_and_preview(qwin, message)

  def chanmsg(self, user, channel, message):
    if is_ignored(user, self.client.network_key, channel):
      return
    uobj, nick, ident, host = self._parse_user(user)
    ts = self._get_server_time()
    chnlower = self.irclower(channel)
    if chnlower in self.client.channels:
      chan = self.client.channels[chnlower]
      # Clear typing indicator when they send a message
      if nick in chan.window._typing_nicks:
        chan.window.set_nick_typing(nick, False)
      pfx = self._nick_prefix(nick, channel)
      chan.history.append(HistoryMessage(uobj, nick, message, 'message', prefix=pfx))
      chan.window.addline_msg(self._pnick(nick, channel), message, timestamp_override=ts)
      state.irclogger.log_channel(self._log_network, channel,
                            "<%s> %s" % (nick, message))
      if not self._in_playback_batch():
        _history_save(self._log_network, channel, 'message', nick, message,
                      prefix=pfx)
        _save_urls(self._log_network, channel,
                   nick, '%s@%s' % (ident, host), message)
      # Activity: highlight if our nick or custom patterns match
      nk = self.client.network_key
      if is_highlight(message, self.nickname, nk, channel):
        self._hook_activity(chan.window, Window.ACTIVITY_HIGHLIGHT)
        if not self._in_playback_batch() and not chan.window._is_active_window():
          if get_highlight_notify(nk, channel):
            self._hook_notify('highlight', '%s in %s' % (nick, channel), message)
      else:
        self._hook_activity(chan.window, Window.ACTIVITY_MESSAGE)
      # Link previews (skip during playback; defer during replay)
      if not self._in_playback_batch():
        from link_preview import check_and_preview
        if not chan.window.queue_replay_callback(
            lambda w=chan.window, m=message: check_and_preview(w, m)):
          check_and_preview(chan.window, message)

  def userJoined(self, nickidhost, channel):
    uobj, nick, ident, host = self._parse_user(nickidhost)
    ts = self._get_server_time()
    chnlower = self.irclower(channel)
    if chnlower in self.client.channels:
      chan = self.client.channels[chnlower]
      chan.addnick(nick, uobj)
      pfx = self._nick_prefix(nick, channel)
      chan.history.append(HistoryMessage(uobj, nick, channel, 'join', prefix=pfx))
      chan.window.addline_nick(["* ", (nick,), " has joined %s" % channel], state.infoformat,
                              timestamp_override=ts)
      if not self._in_playback_batch():
        _history_save(self._log_network, channel, 'join', nick, channel,
                      prefix=pfx)
    # Auto-op check
    if is_auto_op(nickidhost, self.client.network_key, channel):
      self.sendLine("MODE %s +o %s" % (channel, nick))

  def userLeft(self, usermask, channel):
    uobj, nick, ident, host = self._parse_user(usermask)
    ts = self._get_server_time()
    chnlower = self.irclower(channel)
    if chnlower in self.client.channels:
      chan = self.client.channels[chnlower]
      if nick in chan.window._typing_nicks:
        chan.window._clear_nick_typing(nick)
        chan.window._update_typing_bar()
      pfx = self._nick_prefix(nick, channel)
      chan.history.append(HistoryMessage(uobj, nick, channel, 'part', prefix=pfx))
      chan.removenick(nick)
      pn = (pfx + nick) if (state.config.show_mode_prefix_messages and pfx) else nick
      chan.window.addline_nick(["* ", (pn,), " has left %s" % channel], state.infoformat,
                              timestamp_override=ts)
      if not self._in_playback_batch():
        _history_save(self._log_network, channel, 'part', nick, channel, prefix=pfx)

  def userQuit(self, usermask, quitMessage):
    uobj, nick, ident, host = self._parse_user(usermask)
    ts = self._get_server_time()
    playback = self._in_playback_batch()
    lnick = self.irclower(nick)
    for chnlower, chan in self.client.channels.items():
      if nick in chan.nicks:
        if nick in chan.window._typing_nicks:
          chan.window._clear_nick_typing(nick)
          chan.window._update_typing_bar()
        pfx = self._nick_prefix(nick, chan.name)
        chan.history.append(HistoryMessage(uobj, nick, quitMessage or '', 'quit', prefix=pfx))
        chan.removenick(nick)
        pn = (pfx + nick) if (state.config.show_mode_prefix_messages and pfx) else nick
        chan.window.addline_nick(["* ", (pn,), " has quit (%s)" % (quitMessage or "")], state.infoformat,
                                timestamp_override=ts)
        if not playback:
          _history_save(self._log_network, chnlower, 'quit', nick, quitMessage or '', prefix=pfx)
    # Remove from network-wide user list
    self.client.users.pop(lnick, None)

  def nickChanged(self, nick):
    oldname = self.nickname  # save before super() updates it
    super().nickChanged(nick)
    # Treat own nick change like any other rename for channel display
    self.userRenamed(oldname, nick)
    self._update_server_title()
    from qtpyrc import _update_all_titles
    _update_all_titles()

  def userRenamed(self, oldname, newname):
    loldname = self.irclower(oldname)
    lnewname = self.irclower(newname)
    ts = self._get_server_time()
    playback = self._in_playback_batch()
    # Update network-wide user tracking
    user = self.client.users.pop(loldname, None)
    if user:
      user.nick = newname
      self.client.users[lnewname] = user
    # Update per-channel nicks and nick list items
    for chnlower, chan in self.client.channels.items():
      if oldname in chan.nicks:
        chan.nicks.discard(oldname)
        chan.nicks.add(newname)
        # Re-key in channel users dict
        u = chan.users.pop(loldname, None)
        if u:
          chan.users[lnewname] = u
        # Update nick list widget
        nl = chan.window.nickslist
        for i in range(nl.count()):
          item = nl.item(i)
          if item and item._nick == oldname:
            item.set_nick(newname)
            if hasattr(item, 'user') and item.user:
              item.user.nick = newname
            break
        chan.window.addline_nick(["* ", (self._pnick(oldname, chan.name),), " is now known as ", (newname,)], state.infoformat,
                                timestamp_override=ts)
        if not playback:
          _history_save(self._log_network, chnlower, 'nick', oldname, newname,
                        prefix=self._nick_prefix(oldname, chan.name))
    # Update queries — keys are (ident, host), so just update the nick field
    for q in self.client.queries.values():
      if q.nick and self.irclower(q.nick) == loldname:
        q.nick = newname
        q.update_title()
        if q.window:
          q.window.addline_nick(
            ["* ", (oldname,), " is now known as ", (newname,)],
            state.infoformat, timestamp_override=ts)

  def modeChanged(self, usermask, channel, set_, modes, args):
    nick = usermask.split('!', 1)[0]
    setter = self._get_user(nick) if nick else None
    ts = self._get_server_time()
    chnlower = self.irclower(channel)
    chan = self.client.channels.get(chnlower)
    # Decompose multi-char mode string into individual ModeChange entries
    arg_idx = 0
    for c in modes:
      # Determine if this mode char takes a parameter
      accepts = self._modeAcceptsArg.get(c, (False, False))
      takes_param = accepts[0] if set_ else accepts[1]
      param = None
      if takes_param and arg_idx < len(args):
        param = args[arg_idx]
        arg_idx += 1
      if chan:
        entry = HistoryModeChange(setter, nick, c, set_, param)
        chan.history.append(entry)
        # Track channel key (+k / -k)
        if c == 'k':
          chan.key = param if set_ else None
        # Update user prefix tracking for prefix modes (o, v, h, etc.)
        if param and c in self._prefix_modes:
          lnick = self.irclower(param)
          target_user = self.client.users.get(lnick)
          if target_user:
            sym_idx = self._prefix_modes.index(c)
            sym = self._prefix_symbols[sym_idx] if sym_idx < len(self._prefix_symbols) else ''
            if set_:
              target_user.prefix[chnlower] = sym
            else:
              target_user.prefix.pop(chnlower, None)
            # Refresh nick list display for the affected user
            if chan:
              nl = chan.window.nickslist
              for i in range(nl.count()):
                item = nl.item(i)
                if item and item._nick == param:
                  item.refresh_prefix()
                  break
    # Display the mode change
    sign = '+' if set_ else '-'
    arg_str = ' ' + ' '.join(args) if args else ''
    if chan:
      chan.window.addline_nick(["* ", (self._pnick(nick, channel),), " sets mode %s%s%s" % (sign, modes, arg_str)], state.infoformat,
                              timestamp_override=ts)
      if not self._in_playback_batch():
        _history_save(self._log_network, channel, 'mode', nick,
                      "sets mode %s%s%s" % (sign, modes, arg_str),
                      prefix=self._nick_prefix(nick, channel))

  def topicUpdated(self, usermask, channel, newTopic):
    nick = usermask.split('!', 1)[0]
    setter = self._get_user(nick) if nick else None
    ts = self._get_server_time()
    chnlower = self.irclower(channel)
    chan = self.client.channels.get(chnlower)
    if chan:
      chan.topic = newTopic
      chan.topic_setter = usermask
      chan.topic_time = ts
      entry = HistoryTopicChange(setter, nick, newTopic)
      chan.history.append(entry)
      chan.window.addline_nick(["* ", (self._pnick(nick, channel),), " changed the topic to: %s" % newTopic], state.infoformat,
                              timestamp_override=ts)
      if not self._in_playback_batch():
        _history_save(self._log_network, channel, 'topic', nick, newTopic,
                      prefix=self._nick_prefix(nick, channel))
