"""
qtpyrc plugin API.

Plugins are Python modules that define a class inheriting from
``plugin.Callbacks``.  The class overrides whichever IRC event methods it
wants to handle.  At the bottom of the module, set ``Class = MyPlugin``
so the loader knows which class to instantiate.

Example plugin (scripts/hello.py):

    import plugin

    class Hello(plugin.Callbacks):
        def chanmsg(self, irc, conn, user, channel, message):
            nick = user.split('!', 1)[0]
            if message.strip().lower() == '!hello':
                plugin.irc.msg(conn, channel, 'Hello, %s!' % nick)

    Class = Hello

Lifecycle:
    1. The loader imports the module.
    2. It finds ``module.Class`` and calls ``Class(plugin.irc)``.
    3. ``plugin.irc`` is a module-level singleton initialised at startup.
       Its attributes are live references so they always reflect current state.
    4. Whenever an IRC event fires, the hook dispatcher calls the matching
       method on every loaded plugin.  If a handler returns a truthy value
       the event is swallowed (default handlers are skipped).
"""


class _Irc:
    """Singleton providing access to the IRC client for plugins and /exec.

    Attributes (all live references, always up-to-date):
        clients     set of Client instances
        config      the AppConfig instance
        app         the QApplication
        mainwin     the main application window (MainWindow)

    Convenience methods delegate to whichever IRCClient is relevant.
    Plugins that need a specific connection should use the ``conn``
    argument that every callback receives.
    """

    def __init__(self):
        self.clients = None
        self.config = None
        self.app = None
        self._get_active_window = None
        self._get_networks = None
        self._owned_hooks = []  # (event, name) pairs registered via on()

    def _init(self, *, clients, config, app, get_active_window, get_networks):
        """Called once at startup to wire up live references."""
        self.clients = clients
        self.config = config
        self.app = app
        self._get_active_window = get_active_window
        self._get_networks = get_networks

    @property
    def mainwin(self):
        """The main application window."""
        return self.app.mainwin if self.app else None

    @property
    def active_window(self):
        """The window that currently has focus (or *None*)."""
        return self._get_active_window() if self._get_active_window else None

    @property
    def networks(self):
        """Dict of network_key -> {'client', 'channels', 'users', 'conn'}.

        ``channels`` maps channel name -> Channel object.
        ``users`` is the network-wide user dict (irclower(nick) -> User).
        ``conn`` is the IRCClient connection (may be None).
        """
        return self._get_networks() if self._get_networks else {}

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

    # --- commands ---

    def docommand(self, window, cmd, text=''):
        """Execute a slash command as if the user typed it in *window*."""
        from commands import docommand
        docommand(window, cmd, text)

    # --- /on hooks ---

    def on(self, event, name, pattern, command='', *, channel=None, network=None,
           nick_mask=None, sound=None, desktop=False, highlight_tab=False,
           window=None):
        """Register an /on hook.

        Args:
            event:    Event name (chanmsg, privmsg, join, part, quit, kick,
                      nick, topic, mode, connect, disconnect, signon, etc.)
            name:     Unique name for this hook (used for removal).
            pattern:  Wildcard pattern matched against the event's primary text
                      (message, nick, etc.).  Use ``'*'`` to match everything.
            command:  Command string to execute when the hook fires.
                      Variables like ``{nick}``, ``{channel}``, ``{message}``,
                      ``{network}``, ``{me}`` are expanded before execution.
                      Optional if action flags are used.
            channel:  Optional channel filter (only fire in this channel).
            network:  Optional network filter (only fire on this network).
            nick_mask: Optional nick/hostmask filter (wildcards supported).
            sound:    Sound to play: ``'beep'``, ``'default'``, ``'none'``,
                      or a ``.wav`` path.
            desktop:  If True, show a desktop notification.
            highlight_tab: If True, highlight the channel tab.
            window:   Optional window to run the command in.  If omitted,
                      the active window at fire-time is used.

        Example::

            irc.on('chanmsg', 'greet', '*hello*', '/msg {channel} Hello {nick}!')
            irc.on('chanmsg', 'vip', '*', sound='beep', desktop=True,
                   nick_mask='boss')
            irc.on('kick', 'kick_alert', '*', sound='beep', desktop=True)
        """
        import state
        from exec_system import _ON_EVENT_MAP
        if event not in _ON_EVENT_MAP:
            raise ValueError("Unknown /on event: %s (valid: %s)"
                             % (event, ', '.join(sorted(_ON_EVENT_MAP))))
        if event not in state._on_hooks:
            state._on_hooks[event] = {}
        state._on_hooks[event][name] = {
            'pattern': pattern,
            'command': command,
            'channel': channel,
            'network': network,
            'nick_mask': nick_mask,
            'sound': sound,
            'desktop': desktop,
            'highlight_tab': highlight_tab,
            'window': window,    # None → resolved at fire-time
        }
        self._owned_hooks.append((event, name))

    def remove_on(self, event, name):
        """Remove a previously registered /on hook."""
        import state
        hooks = state._on_hooks.get(event)
        if hooks and name in hooks:
            del hooks[name]
        try:
            self._owned_hooks.remove((event, name))
        except ValueError:
            pass

    def remove_all_hooks(self):
        """Remove all /on hooks registered through this proxy."""
        import state
        for event, name in self._owned_hooks:
            hooks = state._on_hooks.get(event)
            if hooks and name in hooks:
                del hooks[name]
        self._owned_hooks.clear()

    # --- timers ---

    def timer(self, name, reps, secs, command, *, window=None):
        """Create a named timer.

        Args:
            name:    Unique timer name.
            reps:    Number of repetitions (0 = infinite).
            secs:    Interval in seconds (float OK).
            command: Command string to execute each time (e.g. ``'/say hi'``).
            window:  Window to execute in.  If omitted, the active window
                     at fire-time is used.
        """
        from exec_system import _exec_set_timer
        _exec_set_timer(window, name, reps, secs, command)

    def cancel_timer(self, name):
        """Stop and remove a named timer."""
        import state
        info = state._timers.get(name)
        if info:
            info['timer'].stop()
            del state._timers[name]


# Module-level singleton — initialised by plugins.init_irc() at startup
irc = _Irc()

# Backward compat aliases
IrcProxy = _Irc
Irc = _Irc


class Callbacks:
    """Base class for plugins.  Override any method you want to handle.

    Every callback receives ``irc`` (the `plugin.irc` singleton) as the first
    argument after ``self``, followed by ``conn`` (the `IRCClient` that
    received the event), followed by the event-specific arguments.

    Return a truthy value from a callback to suppress the client's default
    handler for that event.
    """

    def __init__(self, irc):
        """Called once when the plugin is loaded.

        *irc* is the `plugin.irc` singleton — store it if you need it later.
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

    # --- lifecycle ---
    def die(self):
        """Called when the plugin is unloaded.  Cleans up /on hooks."""
        self.irc.remove_all_hooks()
