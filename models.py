# models.py - Data models: User, Channel, Query, Client, etc.

import re
import asyncio
import traceback
from collections import deque
from datetime import datetime

import state

# ---------------------------------------------------------------------------
# mIRC color parsing
# ---------------------------------------------------------------------------

mircre = re.compile(r"""
                      (
                        (?:
                          \x03
                          (?:
                            (\d\d?)
                            (?:,(\d\d?))?
                          )?
                        )
                        |\x02|\x1D|\x1F|\x16|\x0F|^
                      )
                      ([^\x02\x1D\x1F\x16\x03\x0F]*)
                    """, re.VERBOSE)

usersplit = re.compile("(?P<nick>.*?)!(?P<ident>.*?)@(?P<host>.*)").match

# ---------------------------------------------------------------------------
# Extended mIRC 99-color palette (RGB tuples)
# ---------------------------------------------------------------------------

irccolors = (
  (255,255,255), (0,0,0), (0,0,127), (0,147,0), (255,0,0), (127,0,0), (156,0,156), (252,127,0),
  (255,255,0), (0,252,0), (0,147,147), (0,255,255), (0,0,252), (255,0,255), (127,127,127), (210,210,210),
  (71,0,0), (71,33,0), (71,71,0), (50,71,0), (0,71,0), (0,71,44), (0,71,71), (0,39,71), (0,0,71), (46,0,71), (71,0,71), (71,0,42),
  (116,0,0), (116,58,0), (116,116,0), (81,116,0), (0,116,0), (0,116,73), (0,116,116), (0,64,116), (0,0,116), (75,0,116), (116,0,116), (116,0,69),
  (181,0,0), (181,99,0), (181,181,0), (125,181,0), (0,181,0), (0,181,113), (0,181,181), (0,99,181), (0,0,181), (117,0,181), (181,0,181), (181,0,107),
  (255,0,0), (255,140,0), (255,255,0), (178,255,0), (0,255,0), (0,255,160), (0,255,255), (0,140,255), (0,0,255), (165,0,255), (255,0,255), (255,0,152),
  (255,89,89), (255,180,89), (255,255,113), (207,255,96), (111,255,111), (111,255,201), (109,255,255), (89,180,255), (89,89,255), (196,89,255), (255,102,255), (255,89,188),
  (255,156,156), (255,211,156), (255,255,156), (226,255,156), (156,255,156), (156,255,219), (156,255,255), (156,211,255), (156,156,255), (220,156,255), (255,156,255), (255,148,211),
  (0,0,0), (19,19,19), (40,40,40), (54,54,54), (77,77,77), (101,101,101), (129,129,129), (159,159,159), (188,188,188), (226,226,226), (255,255,255),
)

def _vlinear(v):
  if v <= .04045:
    return v / 12.92
  return ((v + .055) / 1.055) ** 2.4

def _luminance(r, g, b):
  return _vlinear(r) * .2126 + _vlinear(g) * .7152 + _vlinear(b) * .0722

def perceivedbrightness(r, g, b):
  r, g, b = r / 255, g / 255, b / 255
  y = _luminance(r, g, b)
  if y <= 216 / 24389:
    return y * 24389 / 27
  return y ** (1/3) * 116 - 16

# ---------------------------------------------------------------------------
# Network-wide User tracking
# ---------------------------------------------------------------------------

class User:
  """Tracks a single IRC user across the network.

  Attributes populated incrementally as data arrives (JOIN, WHOIS, WHO, etc.).
  """
  __slots__ = ('nick', 'ident', 'host', 'realname', 'account',
               'server', 'channels', 'prefix')

  def __init__(self, nick, ident=None, host=None, realname=None):
    self.nick = nick
    self.ident = ident       # user part of nick!user@host
    self.host = host         # host part
    self.realname = realname # GECOS / realname
    self.account = None      # NickServ/SASL account name
    self.server = None       # IRC server the user is connected to
    self.channels = set()    # channel names this user is in
    self.prefix = {}         # channel_lower -> mode prefix string ("@", "+", etc.)

  @property
  def hostmask(self):
    """Return nick!ident@host (or as much as known)."""
    return '%s!%s@%s' % (self.nick, self.ident or '*', self.host or '*')

  def __repr__(self):
    return '<User %s>' % self.hostmask

# ---------------------------------------------------------------------------
# Channel history entries
# ---------------------------------------------------------------------------

HISTORY_MAX = 2000

class HistoryMessage:
  """A message in channel history."""
  __slots__ = ('time', 'user', 'nick', 'text', 'type', 'prefix')
  def __init__(self, user, nick, text, msg_type='message', prefix='', time=None):
    self.time = time or datetime.now()
    self.user = user    # User object (may be None for server messages)
    self.nick = nick    # nick string (kept even if User is unavailable)
    self.text = text
    self.type = msg_type  # 'message', 'action', 'notice', 'join', 'part', 'quit', 'kick'
    self.prefix = prefix  # mode prefix symbol (@, +, %, etc.)
  def __repr__(self):
    return '<HistoryMessage %s %s: %s>' % (self.time.strftime('%H:%M'), self.nick, self.text[:40])

class HistoryModeChange:
  """A single mode change in channel history."""
  __slots__ = ('time', 'user', 'nick', 'mode', 'added', 'param')
  def __init__(self, user, nick, mode, added, param=None):
    self.time = datetime.now()
    self.user = user    # User who set the mode
    self.nick = nick    # nick string of who set it
    self.mode = mode    # single mode character (e.g. 'b', 'o', 'k')
    self.added = added  # True = +mode, False = -mode
    self.param = param  # associated string (nick, hostmask, key, etc.) or None
  def __repr__(self):
    sign = '+' if self.added else '-'
    p = ' ' + self.param if self.param else ''
    return '<HistoryModeChange %s %s%s%s by %s>' % (
      self.time.strftime('%H:%M'), sign, self.mode, p, self.nick)

class HistoryTopicChange:
  """A topic change in channel history."""
  __slots__ = ('time', 'user', 'nick', 'topic')
  def __init__(self, user, nick, topic):
    self.time = datetime.now()
    self.user = user   # User who changed the topic
    self.nick = nick
    self.topic = topic
  def __repr__(self):
    return '<HistoryTopicChange %s %s: %s>' % (
      self.time.strftime('%H:%M'), self.nick, self.topic[:40])


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------

class Channel:
  def __init__(self, client, name):
    self.nicks = set()
    self.users = {}  # irclower(nick) -> User — per-channel references
    self.history = deque(maxlen=HISTORY_MAX)
    self.topic = None
    self.topic_setter = None   # nick!user@host who set the topic
    self.topic_time = None     # human-readable time when topic was set
    self.key = None            # channel key (+k), from config/join/mode
    self.modes = ''            # current mode string (e.g. '+nt')
    self.mode_args = []        # mode parameters
    self._pending_lists = {}   # mode_char -> [(mask, setter, ts)] for list numerics
    self.client = client
    self.name = name
    self.active = True   # False when kicked/disconnected but window kept open
    self._tree_item = None
    # Late import to avoid circular dependency
    from window import Channelwindow
    self.window = Channelwindow(client, self)
    self.update_title()
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.add_channel(client, self)

  def addnick(self, nick, user=None):
    self.nicks.add(nick)
    if user:
      lnick = nick.lower()
      self.users[lnick] = user
      user.channels.add(self.name)
    try:
      from window import NickItem
      conn = self.client.conn if self.client else None
      chnlower = conn.irclower(self.name) if conn else self.name.lower()
      self.window.nickslist.addItem(NickItem(nick, user, chnlower))
    except RuntimeError:
      pass  # widget already deleted (shutdown)

  def removenick(self, nick):
    self.nicks.discard(nick)
    lnick = nick.lower()
    user = self.users.pop(lnick, None)
    if user:
      user.channels.discard(self.name)
    try:
      nl = self.window.nickslist
      for i in range(nl.count()):
        item = nl.item(i)
        if item and item._nick == nick:
          nl.takeItem(i)
          break
    except RuntimeError:
      pass  # widget already deleted (shutdown)

  def updatenicklist(self):
    from window import NickItem
    nl = self.window.nickslist
    nl.clear()
    for nick in self.nicks:
      user = self.users.get(nick.lower())
      nl.addItem(NickItem(nick, user))

  def post(self, message):
    conn = self.client.conn
    conn.say(self.name, message)
    my_nick = conn.nickname
    my_user = self.client.users.get(conn.irclower(my_nick))
    pfx = conn._nick_prefix(my_nick, self.name) if hasattr(conn, '_nick_prefix') else ''
    self.history.append(HistoryMessage(my_user, my_nick, message, 'message', prefix=pfx))
    self.window.addline_msg(my_nick, message)
    state.irclogger.log_channel(self.client.network, self.name,
                          "<%s> %s" % (my_nick, message))

  def update_title(self):
    from commands import expand_window_title
    base = expand_window_title(state.config.title_channel, self.window)
    self.window.setWindowTitle(base)
    ws = state.app.mainwin.workspace
    if hasattr(ws, 'set_disconnected') and hasattr(self.window, 'subwindow'):
      ws.set_disconnected(self.window.subwindow, not self.active)

  def rejoined(self):
    self.active = True
    self.nicks.clear()
    self.users.clear()
    self.window.nickslist.clear()
    self.update_title()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class Query:
  def __init__(self, client, nick, ident=None):
    self.nick = nick
    self.ident = ident
    self._tree_item = None
    from window import Querywindow
    self.client = client
    self.window = Querywindow(client)
    self.window.query = self
    self.update_title()
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.add_query(client, self)

  def update_title(self):
    from commands import expand_window_title
    base = expand_window_title(state.config.title_query, self.window)
    self.window.setWindowTitle(base)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Network  (unified view of a network's state)
# ---------------------------------------------------------------------------

class Network:
  """Unified object representing a network and its state.

  Owns the network identity (key and name).  The Client delegates to this
  object via properties so existing code that reads/writes ``client.network``
  and ``client.network_key`` still works.

  Available as ``client.net`` and ``window.network``.
  """
  def __init__(self, client, key=None):
    self._client = client
    self.key = key        # config network key (e.g. 'libera')
    self.name = None      # server-reported name (e.g. 'Libera.Chat')

  @property
  def client(self):
    """The Client instance."""
    return self._client

  @property
  def conn(self):
    """The active IRC connection, or None."""
    return self._client.conn

  @property
  def channels(self):
    """Dict of irclower(name) -> Channel."""
    return self._client.channels

  @property
  def queries(self):
    """Dict of query key -> Query."""
    return self._client.queries

  @property
  def users(self):
    """Dict of irclower(nick) -> User (network-wide)."""
    return self._client.users

  @property
  def config(self):
    """The network's config section as a ConfigNode, or an empty one."""
    return state.config.net(self.key)

  @property
  def hostname(self):
    """Server hostname."""
    return self._client.hostname

  @property
  def port(self):
    """Server port."""
    return self._client.port

  @property
  def tls(self):
    """Whether TLS is enabled."""
    return self._client.tls

  def __str__(self):
    return self.name or self.key or ''

  def __repr__(self):
    return 'Network(%s)' % (self.key or self.name or '?')

  def __bool__(self):
    return True


# ---------------------------------------------------------------------------
# Client  (one per server connection / server window)
# ---------------------------------------------------------------------------

class Client:
  @property
  def network_key(self):
    """Config network key — delegates to self.net.key."""
    return self.net.key

  @network_key.setter
  def network_key(self, value):
    self.net.key = value

  @property
  def network(self):
    """Server-reported network name string — delegates to self.net.name."""
    return self.net.name

  @network.setter
  def network(self, value):
    self.net.name = value

  def __init__(self, network_key=None):
    self.net = Network(self, key=network_key)
    self.channels = {}
    self.queries = {}
    self.users = {}  # irclower(nick) -> User — network-wide user tracking
    self.conn = None
    self.connected = False
    self.hostname = None
    self.port = 6667
    self.tls = False
    self.tls_verify = True
    self._connect_task = None
    self._intentional_disconnect = False
    self._server_list = []   # list of server dicts from config
    self._server_index = 0   # index into _server_list for cycling
    # Create the server window AFTER all attributes are set, because
    # addSubWindow can trigger title updates that access them.
    from window import Serverwindow
    self.window = Serverwindow(self)

    # If we have a network config, populate connection details
    if network_key:
      self._server_list = state.config.get_servers(network_key)
      if self._server_list:
        self._apply_server(self._server_list[0])
      from commands import expand_window_title
      self.window.setWindowTitle(
        expand_window_title(state.config.title_server_disconnected, self.window))

    tree = getattr(state.app.mainwin, 'network_tree', None) if hasattr(state.app, 'mainwin') else None
    if tree:
      tree.add_client(self)

  def refresh_server_config(self):
    """Reload server list from config and apply to current connection settings."""
    if self.network_key:
      self._server_list = state.config.get_servers(self.network_key)
      if self._server_list:
        idx = min(self._server_index, len(self._server_list) - 1)
        self._server_index = idx
        self._apply_server(self._server_list[idx])

  def _apply_server(self, srv):
    """Set connection details from a server dict."""
    self.hostname = srv.get('host')
    self.port = srv.get('port', 6667)
    self.tls = srv.get('tls', False)
    self.tls_verify = srv.get('tls_verify', True)

  def _next_server(self):
    """Advance to the next server in the list (cycling). Returns True if there are servers."""
    if not self._server_list:
      return False
    self._server_index = (self._server_index + 1) % len(self._server_list)
    self._apply_server(self._server_list[self._server_index])
    return True

  def reconnect(self, hostname=None, port=None):
    if hostname is not None:
      self.hostname = hostname
    if port is not None:
      self.port = port
    self._intentional_disconnect = True
    if self.conn:
      self.conn.disconnect()
    self._connect_task = asyncio.ensure_future(self.connect_to_server())

  def _window_alive(self):
    """Return True if the server window's underlying C++ object still exists."""
    try:
      self.window.vs.value()
      return True
    except RuntimeError:
      return False

  async def connect_to_server(self):
    if not self.hostname:
      return
    self._intentional_disconnect = False
    while True:
      if not self._window_alive():
        return
      self.window.redmessage("[Connecting to %s:%s]" % (self.hostname, self.port))
      from irc_client import IRCClient
      conn = IRCClient(self)
      connected = False
      try:
        await conn.connect(self.hostname, self.port, tls=self.tls, tls_verify=self.tls_verify)
        connected = True
      except Exception as e:
        if self._window_alive():
          self.window.redmessage('[Connection failed: %s]' % str(e))
      if self._intentional_disconnect:
        return
      if not self._window_alive():
        return
      # Reconnect: cycle servers on failure, use longer delay if we were connected
      if connected:
        if len(self._server_list) > 1:
          self._next_server()
          self.window.redmessage('[Reconnecting to %s:%s in 30 seconds...]' % (self.hostname, self.port))
        else:
          self.window.redmessage('[Reconnecting in 30 seconds...]')
        await asyncio.sleep(30)
      else:
        if len(self._server_list) > 1:
          self._next_server()
          self.window.redmessage('[Trying next server %s:%s in 10 seconds...]' % (self.hostname, self.port))
          await asyncio.sleep(10)
        else:
          self.window.redmessage('[Reconnecting in 30 seconds...]')
          await asyncio.sleep(30)
      if self._intentional_disconnect:
        return


def newclient():
  state.clients.add(Client())
