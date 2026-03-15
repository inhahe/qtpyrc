"""
qtpyrc plugin API.

Plugins are Python modules that define a class inheriting from
``plugin.Callbacks``.  The class overrides whichever IRC event methods it
wants to handle.  At the bottom of the module, set ``Class = MyPlugin``
so the loader knows which class to instantiate.

Example plugin (scripts/hello.py):

    import plugin

    class Hello(plugin.Callbacks):
        def chanmsg(self, irc, user, channel, message):
            nick = user.split('!', 1)[0]
            if message.strip().lower() == '!hello':
                irc.msg(channel, 'Hello, %s!' % nick)

    Class = Hello

Lifecycle:
    1. The loader imports the module.
    2. It finds ``module.Class`` and calls ``Class(irc_proxy)``.
    3. ``irc_proxy`` is an ``IrcProxy`` instance — a snapshot of the
       application state that stays current (attributes are live references).
    4. Whenever an IRC event fires, the hook dispatcher calls the matching
       method on every loaded plugin.  If a handler returns a truthy value
       the event is swallowed (default handlers are skipped).
"""


class IrcProxy:
    """Object passed to plugin __init__ — provides access to the full client.

    Attributes (all live references, always up-to-date):
        clients     set of Client instances
        config      the AppConfig instance
        app         the QApplication

    Convenience methods delegate to whichever IRCClient is relevant.
    Plugins that need a specific connection should use the ``conn``
    argument that every callback receives.
    """

    def __init__(self, *, clients, config, app, get_active_window, get_networks):
        self.clients = clients
        self.config = config
        self.app = app
        self._get_active_window = get_active_window
        self._get_networks = get_networks

    @property
    def active_window(self):
        """The window that currently has focus (or *None*)."""
        return self._get_active_window()

    @property
    def networks(self):
        """Dict of network_key -> {'client', 'channels', 'users', 'conn'}.

        ``channels`` maps channel name -> Channel object.
        ``users`` is the network-wide user dict (irclower(nick) -> User).
        ``conn`` is the IRCClient connection (may be None).
        """
        return self._get_networks()

    # --- shortcut methods (operate on a specific connection) ---

    @staticmethod
    def users(conn):
        """Return the network-wide users dict for *conn*'s client.

        Keys are lowercased nicks, values are User objects with attributes:
        nick, ident, host, realname, account, server, channels, prefix.
        """
        return conn.client.users

    @staticmethod
    def get_user(conn, nick):
        """Look up a User by nick.  Returns None if not found."""
        return conn.client.users.get(conn.irclower(nick))

    @staticmethod
    def channel_history(conn, channel):
        """Return the history deque for *channel*, or None.

        Entries are HistoryMessage, HistoryModeChange, or HistoryTopicChange
        objects with a `time` attribute (datetime) and type-specific fields.
        """
        ch = conn.client.channels.get(conn.irclower(channel))
        return ch.history if ch else None

    @staticmethod
    def irclower(conn, text):
        """Lowercase *text* using *conn*'s network casemapping rules."""
        return conn.irclower(text)

    @staticmethod
    def irceq(conn, a, b):
        """Case-insensitive IRC string comparison using *conn*'s casemapping."""
        return conn.irclower(a) == conn.irclower(b)

    @staticmethod
    def msg(conn, target, message):
        """Send a PRIVMSG via *conn*."""
        conn.say(target, message)

    @staticmethod
    def notice(conn, target, message):
        conn.sendLine("NOTICE %s :%s" % (target, message))

    @staticmethod
    def sendLine(conn, line):
        conn.sendLine(line)

    @staticmethod
    def join(conn, channel, key=None):
        conn.join(channel, key)

    @staticmethod
    def part(conn, channel, reason=None):
        conn.leave(channel, reason)

    @staticmethod
    def kick(conn, channel, nick, reason=None):
        if reason:
            conn.sendLine("KICK %s %s :%s" % (channel, nick, reason))
        else:
            conn.sendLine("KICK %s %s" % (channel, nick))

    @staticmethod
    def mode(conn, channel, modestring):
        conn.sendLine("MODE %s %s" % (channel, modestring))

    @staticmethod
    def nick(conn):
        """Return *conn*'s current nickname."""
        return conn.nickname

    @staticmethod
    def network_key(conn):
        """Return the config network key for *conn* (may be *None*)."""
        return conn.client.network_key


class Callbacks:
    """Base class for plugins.  Override any method you want to handle.

    Every callback receives ``irc`` (the `IrcProxy`) as the first argument
    after ``self``, followed by ``conn`` (the `IRCClient` that received the
    event), followed by the event-specific arguments.

    Return a truthy value from a callback to suppress the client's default
    handler for that event.
    """

    def __init__(self, irc):
        """Called once when the plugin is loaded.

        *irc* is an `IrcProxy` — store it if you need it later.
        """
        self.irc = irc

    # --- connection lifecycle ---
    def connectionMade(self, irc, conn):                          pass
    def connectionLost(self, irc, conn, reason):                  pass
    def signedOn(self, irc, conn, message):                       pass

    # --- channel events ---
    def joined(self, irc, conn, channel):                         pass
    def left(self, irc, conn, channel):                           pass
    def names(self, irc, conn, channel, names):                   pass
    def endofnames(self, irc, conn, channel):                     pass
    def userJoined(self, irc, conn, nickidhost, channel):         pass
    def userLeft(self, irc, conn, user, channel):                 pass
    def userQuit(self, irc, conn, user, quitMessage):             pass
    def userKicked(self, irc, conn, kickee, channel, kicker, message): pass
    def kickedFrom(self, irc, conn, channel, kicker, message):    pass
    def topicUpdated(self, irc, conn, user, channel, newTopic):   pass
    def modeChanged(self, irc, conn, user, channel, set_, modes, args): pass

    # --- messages ---
    def privmsg(self, irc, conn, user, message):                  pass
    def chanmsg(self, irc, conn, user, channel, message):         pass
    def noticed(self, irc, conn, user, channel, message):         pass
    def action(self, irc, conn, user, channel, data):             pass

    # --- nick ---
    def nickChanged(self, irc, conn, nick):                       pass
    def userRenamed(self, irc, conn, oldname, newname):           pass

    # --- other ---
    def receivedMOTD(self, irc, conn, motd):                      pass
    def bounce(self, irc, conn, info):                            pass
    def isupport(self, irc, conn, options):                       pass
    def irc_unknown(self, irc, conn, prefix, command, params):    pass
    def networkChanged(self, irc, conn, networkname):              pass

    # --- invites ---
    def invited(self, irc, conn, nick, channel):                 pass

    # --- CTCP replies ---
    def ctcpReply(self, irc, conn, user, tag, data):             pass

    # --- raw numerics (catch-all for any irc_XXX handler) ---
    def on_numeric(self, irc, conn, command, prefix, params):
        """Called for any irc_* handler not explicitly listed above.

        *command* is the symbolic name (e.g. ``'RPL_WHOISUSER'``).
        Return truthy to suppress default handling.
        """
        pass
