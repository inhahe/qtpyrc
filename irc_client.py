# irc_client.py - IRCClient subclass

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
    db.add_url(network, channel.lower(), nick, host, url)


def _query_history_key(nick, ident):
  """Build a DB key for query history: =nick:ident (~ prefix stripped)."""
  ident = ident.lstrip('~') if ident else ''
  return '=%s:%s' % (nick.lower(), ident.lower())


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
  show_prefix = state.config.show_mode_prefix
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
  window.add_separator(" End of saved history ")


class IRCClient(asyncirc.IRCClient):

  def __init__(self, client):
    super().__init__()
    self.client = client
    self.window = client.window
    self.channels = client.channels
    self.queries = client.queries

    nk = client.network_key
    self.nickname = state.config.resolve(nk, 'nick')
    self.username = state.config.resolve(nk, 'user')
    self.realname = state.config.resolve(nk, 'realname')
    self.password = state.config.resolve_server(nk, 'password')
    self._alt_nicks = list(state.config.resolve(nk, 'alt_nicks') or [])
    self._alt_nick_idx = 0
    self._whois_windows = {}  # lowercased nick -> Window to display results in
    self._ctcp_windows = {}   # lowercased nick -> Window that sent CTCP request
    self._pending_keys = {}   # irclower(channel) -> key used in JOIN
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
    # SASL if configured for this network
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
      nk = self.client.network_key
      net = state.config._net(nk) if nk else {}
      sasl = net.get('sasl') or {}
      mechanism = (sasl.get('mechanism') or 'PLAIN').upper()
      self._sasl_mechanism = mechanism
      self._sasl_username = sasl.get('username', '')
      self._sasl_password = sasl.get('password', '')
      self._sasl_active = True
      self._send_raw("AUTHENTICATE %s" % mechanism)

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
    """Return nick with mode prefix if show_mode_prefix is enabled."""
    if not state.config.show_mode_prefix or not channel:
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
    state.irclogger.log_server(self.client.network or self.client.hostname,
                         'Connected to %s' % self.client.hostname)
    if state.notifications:
      state.notifications.fire('connect', 'Connected',
                               'Connected to %s' % self.client.hostname)

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
    state.irclogger.log_server(self.client.network or self.client.hostname,
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

  def irc_unknown(self, prefix, command, params):
    self.window.addline(' '.join(params[1:]))

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
    w.addline("[%s] channels: %s" % (nick, channels))

  def irc_RPL_ENDOFWHOIS(self, prefix, params):
    # params: [me, nick, "End of /WHOIS list"]
    w = self._whois_window(params)
    nick = params[1] if len(params) > 1 else '?'
    w.addline("[%s] End of WHOIS" % nick)
    # Clean up tracking
    self._whois_windows.pop(self.irclower(nick), None)

  # Also handle 330 (logged in as) and 671 (using secure connection) which
  # are common non-standard WHOIS numerics — they arrive as irc_unknown
  # since they're not in the symbolic map, so we intercept in handleCommand.

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

    # If client has no network_key yet (e.g. manual /server), try to match
    if not self.client.network_key:
      found = state.config.find_network_key(network)
      if found:
        self.client.network_key = found
        state.dbg(state.LOG_INFO, 'Matched network %r to config key %r' % (network, found))

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

    # Autojoin channels
    autojoins = state.config.get_autojoins(self.client.network_key)
    for channel, key in autojoins.items():
      self.join(channel, key)

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
  })
  # Non-standard WHOIS numerics that aren't in the symbolic map and arrive
  # as raw number strings:  330 = logged-in-as, 338 = actually-using-host,
  # 671 = is using a secure connection
  _WHOIS_RAW_NUMERICS = frozenset({'330', '338', '671'})

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
                   '730', '731', '732', '733', '734'):
      return
    if command in ('RPL_WELCOME', 'RPL_YOURHOST', 'RPL_CREATED', 'RPL_MYINFO',
                   'RPL_USERHOST', 'RPL_LUSERCLIENT', 'RPL_LUSERUNKNOWN', 'RPL_LUSERME',
                   'RPL_ADMINME', 'RPL_ADMINLOC', 'RPL_STANTSONLINE', 'RPL_TRYAGAIN', 'ERROR', '265', '266',
                   'RPL_MOTD', 'RPL_ENDOFMOTD', 'RPL_LUSEROP', 'RPL_LUSERCHANNELS', 'RPL_MOTDSTART',
                   'RPL_ISUPPORT'):
      text = ' '.join(params[1:])
      self.window.addline(text)
      state.irclogger.log_server(self.client.network or self.client.hostname, text)
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
      chan.window.addline_nick(["-", (pn,), "- %s" % message], state.noticeformat,
                              timestamp_override=ts)
      chan.window.set_activity(Window.ACTIVITY_MESSAGE)
      if not self._in_playback_batch():
        _history_save(self.client.network, channel, 'notice', nick, message,
                      prefix=self._nick_prefix(nick, channel))
        _save_urls(self.client.network, channel, nick, notice_host, message)
    else:
      self.window.addline_nick(["-", (nick,), "- %s" % message], state.noticeformat,
                              timestamp_override=ts)
    if not self._in_playback_batch() and state.notifications:
      state.notifications.fire('notice', 'Notice from %s' % nick, message)

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
      state.irclogger.log_channel(self.client.network, channel,
                            "* %s %s" % (nick, data))
      if not playback:
        _history_save(self.client.network, channel, 'action', nick, data,
                      prefix=pfx)
        _save_urls(self.client.network, channel,
                   nick, '%s@%s' % (ident, host), data)
      nk = self.client.network_key
      if is_highlight(data, self.nickname, nk, channel):
        chan.window.set_activity(Window.ACTIVITY_HIGHLIGHT)
        if not playback and state.notifications:
          if get_highlight_notify(nk, channel):
            state.notifications.fire('highlight', '%s in %s' % (nick, channel), data)
      else:
        chan.window.set_activity(Window.ACTIVITY_MESSAGE)
    else:
      # Private action
      from models import Query
      if (ident, host) not in self.queries:
        self.queries[ident, host] = Query(self.client, nick, ident)
        qkey = _query_history_key(nick, ident)
        _history_replay(self.queries[ident, host].window, self.client.network,
                        qkey, limit=state.config.history_replay_queries)
      self.queries[ident, host].window.addline_nick(["* ", (nick,), " %s" % data], state.actionformat,
                                                    timestamp_override=ts)
      self.queries[ident, host].window.set_activity(Window.ACTIVITY_HIGHLIGHT)
      if not playback:
        _history_save(self.client.network, _query_history_key(nick, ident), 'action', nick, data)
        _save_urls(self.client.network, _query_history_key(nick, ident),
                   nick, '%s@%s' % (ident, host), data)

  def join(self, channel, key=None):
    if key:
      self._pending_keys[self.irclower(channel)] = key
    super().join(channel, key)

  def joined(self, chname):
    chnlower = self.irclower(chname)
    self._hopping.discard(chnlower)
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
      # Replay saved history into the new channel window
      _history_replay(chan.window, self.client.network, chname, chan_obj=chan)
    ts = self._get_server_time()
    chan.window.addline_nick(["* ", (self.nickname,), " has joined %s" % chname], state.infoformat,
                            timestamp_override=ts)
    if not self._in_playback_batch():
      _history_save(self.client.network, chname, 'join', self.nickname, chname,
                    prefix=self._nick_prefix(self.nickname, chname))
    # persist autojoin (include key)
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
      _history_save(self.client.network, channel, 'part', self.nickname, channel,
                    prefix=self._nick_prefix(self.nickname, channel))
    if chnlower in self._hopping:
      self._deactivate_channel(chnlower)
    else:
      self._close_channel(chnlower)
      # persist autojoin removal
      if state.config.resolve(self.client.network_key, 'persist_autojoins'):
        state.config.update_autojoin(self.client.network_key, channel, remove=True)

  def kickedFrom(self, channel, kicker, message):
    chnlower = self.irclower(channel)
    chan = self.channels.get(chnlower)
    if chan:
      chan.window.redmessage('[Kicked from %s by %s (%s)]' % (channel, self._pnick(kicker, channel), message))
      state.irclogger.log_channel(self.client.network or '', channel,
                            'Kicked by %s (%s)' % (kicker, message))
      if not self._in_playback_batch():
        _history_save(self.client.network, channel, 'kick', kicker,
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
    from models import Query
    new_query = (ident, host) not in self.queries
    if new_query:
      self.queries[ident, host] = Query(self.client, nick, ident)
      qkey = _query_history_key(nick, ident)
      _history_replay(self.queries[ident, host].window, self.client.network,
                      qkey, limit=state.config.history_replay_queries)
    qwin = self.queries[ident, host].window
    qwin.addline_msg(nick, message, timestamp_override=ts)
    qwin.set_activity(Window.ACTIVITY_HIGHLIGHT)
    if hasattr(qwin, '_typing_timer') and qwin._typing_timer is not None:
      qwin.set_nick_typing(nick, False)
    if not self._in_playback_batch():
      _history_save(self.client.network, _query_history_key(nick, ident), 'message', nick, message)
      _save_urls(self.client.network, _query_history_key(nick, ident),
                 nick, '%s@%s' % (ident, host), message)
      if state.notifications:
        if new_query:
          state.notifications.fire('new_query', 'Message from %s' % nick, message)

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
      state.irclogger.log_channel(self.client.network, channel,
                            "<%s> %s" % (nick, message))
      if not self._in_playback_batch():
        _history_save(self.client.network, channel, 'message', nick, message,
                      prefix=pfx)
        _save_urls(self.client.network, channel,
                   nick, '%s@%s' % (ident, host), message)
      # Activity: highlight if our nick or custom patterns match
      nk = self.client.network_key
      if is_highlight(message, self.nickname, nk, channel):
        chan.window.set_activity(Window.ACTIVITY_HIGHLIGHT)
        if not self._in_playback_batch() and state.notifications:
          if get_highlight_notify(nk, channel):
            state.notifications.fire('highlight', '%s in %s' % (nick, channel), message)
      else:
        chan.window.set_activity(Window.ACTIVITY_MESSAGE)

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
        _history_save(self.client.network, channel, 'join', nick, channel,
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
      pn = (pfx + nick) if (state.config.show_mode_prefix and pfx) else nick
      chan.window.addline_nick(["* ", (pn,), " has left %s" % channel], state.infoformat,
                              timestamp_override=ts)
      if not self._in_playback_batch():
        _history_save(self.client.network, channel, 'part', nick, channel, prefix=pfx)

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
        pn = (pfx + nick) if (state.config.show_mode_prefix and pfx) else nick
        chan.window.addline_nick(["* ", (pn,), " has quit (%s)" % (quitMessage or "")], state.infoformat,
                                timestamp_override=ts)
        if not playback:
          _history_save(self.client.network, chnlower, 'quit', nick, quitMessage or '', prefix=pfx)
    # Remove from network-wide user list
    self.client.users.pop(lnick, None)

  def nickChanged(self, nick):
    super().nickChanged(nick)
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
          _history_save(self.client.network, chnlower, 'nick', oldname, newname,
                        prefix=self._nick_prefix(oldname, chan.name))
    # Update queries
    if (self.client.network, loldname) in self.client.queries:
      self.client.queries[self.client.network, lnewname] = self.client.queries.pop((self.client.network, loldname))
      self.client.queries[self.client.network, lnewname].nick = newname

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
        _history_save(self.client.network, channel, 'mode', nick,
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
      entry = HistoryTopicChange(setter, nick, newTopic)
      chan.history.append(entry)
      chan.window.addline_nick(["* ", (self._pnick(nick, channel),), " changed the topic to: %s" % newTopic], state.infoformat,
                              timestamp_override=ts)
      if not self._in_playback_batch():
        _history_save(self.client.network, channel, 'topic', nick, newTopic,
                      prefix=self._nick_prefix(nick, channel))
