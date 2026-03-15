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
  __slots__ = ('time', 'user', 'nick', 'text', 'type')
  def __init__(self, user, nick, text, msg_type='message'):
    self.time = datetime.now()
    self.user = user    # User object (may be None for server messages)
    self.nick = nick    # nick string (kept even if User is unavailable)
    self.text = text
    self.type = msg_type  # 'message', 'action', 'notice', 'join', 'part', 'quit', 'kick'
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
    self.key = None      # channel key (+k), from config/join/mode
    self.client = client
    self.name = name
    self.active = True   # False when kicked/disconnected but window kept open
    self._tree_item = None
    # Late import to avoid circular dependency
    from window import Channelwindow
    self.window = Channelwindow(client, self)
    self.window.setWindowTitle(name)
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.add_channel(client, self)

  def addnick(self, nick, user=None):
    self.nicks.add(nick)
    if user:
      lnick = nick.lower()
      self.users[lnick] = user
      user.channels.add(self.name)
    from window import NickItem
    self.window.nickslist.addItem(NickItem(nick, user))

  def removenick(self, nick):
    self.nicks.discard(nick)
    lnick = nick.lower()
    user = self.users.pop(lnick, None)
    if user:
      user.channels.discard(self.name)
    nl = self.window.nickslist
    for i in range(nl.count()):
      item = nl.item(i)
      if item and item.text() == nick:
        nl.takeItem(i)
        break

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
    self.history.append(HistoryMessage(my_user, my_nick, message, 'message'))
    self.window.addline_msg(my_nick, message)
    state.irclogger.log_channel(self.client.network, self.name,
                          "<%s> %s" % (my_nick, message))

  def rejoined(self):
    self.active = True
    self.nicks.clear()
    self.users.clear()
    self.window.nickslist.clear()
    self.window.redmessage('[Rejoined %s]' % self.name)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class Query:
  def __init__(self, client, nick):
    self.nick = nick
    self._tree_item = None
    from window import Querywindow
    self.window = Querywindow(client)
    self.window.setWindowTitle(nick)
    tree = getattr(state.app.mainwin, 'network_tree', None)
    if tree:
      tree.add_query(client, self)


# ---------------------------------------------------------------------------
# Client  (one per server connection / server window)
# ---------------------------------------------------------------------------

class Client:
  def __init__(self, network_key=None):
    self.network_key = network_key
    self.channels = {}
    self.queries = {}
    self.users = {}  # irclower(nick) -> User — network-wide user tracking
    self.conn = None
    from window import Serverwindow
    self.window = Serverwindow(self)
    self.network = None
    self.hostname = None
    self.port = 6667
    self.tls = False
    self.tls_verify = True
    self._connect_task = None
    self._intentional_disconnect = False
    self._server_list = []   # list of server dicts from config
    self._server_index = 0   # index into _server_list for cycling

    # If we have a network config, populate connection details
    if network_key:
      self._server_list = state.config.get_servers(network_key)
      if self._server_list:
        self._apply_server(self._server_list[0])
      nick = state.config.resolve(network_key, 'nick')
      self.window.setWindowTitle("[not connected] - %s (%s)" % (nick, network_key))

    tree = getattr(state.app.mainwin, 'network_tree', None) if hasattr(state.app, 'mainwin') else None
    if tree:
      tree.add_client(self)

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

  async def connect_to_server(self):
    if not self.hostname:
      return
    self._intentional_disconnect = False
    self.window.redmessage("[Connecting to %s:%s]" % (self.hostname, self.port))
    from irc_client import IRCClient
    conn = IRCClient(self)
    connected = False
    try:
      await conn.connect(self.hostname, self.port, tls=self.tls, tls_verify=self.tls_verify)
      # connect() returns when the connection is lost (not on failure to connect)
      connected = True
    except Exception as e:
      self.window.redmessage('[Connection failed: %s]' % str(e))
    if self._intentional_disconnect:
      return
    # Reconnect: cycle servers on failure, use longer delay if we were connected
    if connected:
      # Was connected, then lost — try next server or same one
      if len(self._server_list) > 1:
        self._next_server()
        self.window.redmessage('[Reconnecting to %s:%s in 30 seconds...]' % (self.hostname, self.port))
      else:
        self.window.redmessage('[Reconnecting in 30 seconds...]')
      await asyncio.sleep(30)
    else:
      # Failed to connect — cycle faster
      if len(self._server_list) > 1:
        self._next_server()
        self.window.redmessage('[Trying next server %s:%s in 10 seconds...]' % (self.hostname, self.port))
        await asyncio.sleep(10)
      else:
        self.window.redmessage('[Reconnecting in 30 seconds...]')
        await asyncio.sleep(30)
    if not self._intentional_disconnect:
      await self.connect_to_server()


def newclient():
  state.clients.add(Client())
