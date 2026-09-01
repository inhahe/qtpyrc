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

    def __init__(self, owner=None):
        self._get_active_window = None
        self._get_networks = None
        self._owner = owner
        self._owned_hooks = []  # (event, name) pairs registered via on()
        self._owned_timers = []      # timer names created via timer()
        self._owned_commands = []    # command names registered via add_command()
        self._owned_keys = []        # canonical key sequences via bind_key()

    def for_plugin(self, owner):
        """Return a view of this API that remembers *owner* registered things.

        `plugin.irc` is a module-level singleton, so the registration lists
        above are shared by everything that touches it -- which meant
        `remove_all()` (reached from `Callbacks.die()`) tore down *every*
        plugin's hooks, not just the hooks of the plugin being unloaded.
        Reloading one plugin silently disarmed the others, and nothing said so.

        The loader therefore hands each plugin its own bound view.  It shares
        the live references (they are read from `_core`) and differs only in
        who is recorded as the owner of a registration and thus in what
        `remove_all()` takes away.
        """
        return _PluginIrc(self, owner)

    def _init(self, *, get_active_window, get_networks):
        """Called once at startup to wire up the two callables."""
        self._get_active_window = get_active_window
        self._get_networks = get_networks

    # `clients`, `config` and `app` are read from `state` on every access
    # rather than copied at startup.  `qtpyrc._reload_config` *replaces*
    # `state.config` with a freshly parsed AppConfig, so a copy taken at
    # startup silently goes stale the first time the user reloads the config
    # or saves the settings dialog -- a plugin would then be reading the
    # settings the client had when it launched, with nothing to say so.
    @property
    def clients(self):
        """The live set of `Client` instances."""
        import state
        return state.clients

    @property
    def config(self):
        """The current `AppConfig` (re-read after a config reload)."""
        import state
        return state.config

    @property
    def app(self):
        """The QApplication."""
        import state
        return state.app

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

    def msg(self, conn, target, message):
        """Send a PRIVMSG via *conn*. Also echoes to the local window."""
        self.say(conn, target, message)

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

    # --- UI paths ---

    def ui(self, path):
        """Trigger a UI action by path (e.g. ``'menu.tools.colorpicker'``).

        Raises KeyError if the path is not found.
        """
        import state
        from PySide6.QtGui import QAction
        reg = state.ui_registry
        key = path.strip().lower()
        if key not in reg:
            raise KeyError("Unknown UI path: %s" % path)
        action = reg[key]
        if isinstance(action, QAction):
            action.trigger()
        elif callable(action):
            action()

    def ui_list(self):
        """Return a list of ``(path, description)`` tuples for all registered UI paths."""
        import state
        return sorted((k, state.ui_descriptions.get(k, ''))
                      for k in state.ui_registry)

    def ui_tree(self):
        """Return a nested dict representing the UI path hierarchy.

        Each key is a path segment.  Leaf nodes have a ``'_desc'`` key with
        the description string.  Example::

            {'menu': {'file': {'settings': {'_desc': 'File > Settings'},
                               'close':    {'_desc': 'File > Close'}},
                      'tools': { ... }}}
        """
        import state
        tree = {}
        for path, desc in sorted(state.ui_descriptions.items()):
            parts = path.split('.')
            node = tree
            for part in parts:
                node = node.setdefault(part, {})
            node['_desc'] = desc
        return tree

    # --- commands ---

    def docommand(self, window, cmd, text=''):
        """Execute a slash command as if the user typed it in *window*."""
        from commands import docommand
        docommand(window, cmd, text)

    # --- convenience methods ---

    def say(self, conn, target, message):
        """Send a message to *target* (channel or nick) via *conn*.

        Echoes it, logs it, saves it to history, previews its links and
        dispatches it to plugin hooks -- because it goes through
        `commands.send_message`, which is the one place that knows what
        sending a message means.

        This used to be a sixth hand-written copy of that, and had drifted
        exactly the way the other five did: it never chunked, so a long line
        was silently truncated by the server; it recorded nothing with
        `conn._own_messages`, so on a bouncer that echoes, every message a
        plugin sent came back and was drawn and stored a second time; it
        logged to the channel log but never to a query log; it saved a channel
        row without the mode prefix; and it decided "is this a channel?" by
        looking for the target in `conn.client.channels` rather than asking
        `conn.is_channel()`, so a PM to a nick with no open query window fell
        through the `else` branch and was displayed nowhere at all.
        """
        from commands import send_message
        # The issuing window: send_message resolves the *display* window from
        # the target itself, and uses this one only for `window.client` and as
        # the fallback for a target with no window of its own.  It must
        # therefore belong to `conn`'s client, which the active window need
        # not -- a plugin can be sending to network A while the user is
        # looking at network B.
        window = conn.client.window or self.active_window
        send_message(window, conn, target, message)

    @staticmethod
    def channel(window):
        """Return the channel name or query nick for *window*, or ''."""
        if hasattr(window, 'channel') and window.channel:
            return window.channel.name
        if hasattr(window, 'query') and window.query:
            return window.query.nick
        return ''

    @staticmethod
    def nicks(conn, channel):
        """Return the set of nicks in *channel*, or an empty set."""
        ch = conn.client.channels.get(conn.irclower(channel))
        return set(ch.nicks) if ch else set()

    @staticmethod
    def me(conn):
        """Return *conn*'s current nickname (alias for nick())."""
        return conn.nickname

    @staticmethod
    def echo(window, text):
        """Display *text* in *window* (no formatting)."""
        window.addline(text)

    @staticmethod
    def error(window, text):
        """Display *text* as a red system message in *window*."""
        window.redmessage(text)

    @staticmethod
    def dbg(level, *args):
        """Write to the console debug log at the given level.

        Levels are available as constants on the irc object:
        ``irc.LOG_ERROR``, ``irc.LOG_WARN``, ``irc.LOG_INFO``,
        ``irc.LOG_DEBUG``, ``irc.LOG_TRACE``.

        Example::

            irc.dbg(irc.LOG_DEBUG, 'myplugin:', 'processed', count, 'items')
        """
        import state
        state.dbg(level, *args)

    LOG_SILENT = 0
    LOG_ERROR  = 1
    LOG_WARN   = 2
    LOG_INFO   = 3
    LOG_DEBUG  = 4
    LOG_TRACE  = 5

    @staticmethod
    def inputbox(prompt='', title='Input'):
        """Show an input dialog and return the text (or '' if cancelled)."""
        from PySide6.QtWidgets import QInputDialog, QApplication
        parent = QApplication.activeWindow()
        text, ok = QInputDialog.getText(parent, title, prompt or 'Enter value:')
        return text.strip() if ok else ''

    @staticmethod
    def stdin(prompt=''):
        """Read a line from stdin (blocks until input is provided)."""
        return input(prompt)

    # --- plugin config ---

    def get_config(self, plugin_name, key, default=None):
        """Get a plugin config value.

        Reads from ``plugins.<plugin_name>.<key>`` in the config YAML.
        """
        import state
        plugins_data = state.config._data.get('plugins') or {}
        plugin_data = plugins_data.get(plugin_name) or {}
        return plugin_data.get(key, default)

    def set_config(self, plugin_name, key, value):
        """Set a plugin config value and save to disk.

        Writes to ``plugins.<plugin_name>.<key>`` in the config YAML.
        """
        import state
        from ruamel.yaml.comments import CommentedMap
        plugins_data = state.config._data.get('plugins')
        if plugins_data is None:
            plugins_data = CommentedMap()
            state.config._data['plugins'] = plugins_data
        plugin_data = plugins_data.get(plugin_name)
        if plugin_data is None:
            plugin_data = CommentedMap()
            plugins_data[plugin_name] = plugin_data
        plugin_data[key] = value
        state.config.save()

    # --- /on hooks ---

    def on(self, event, name, pattern, command='', *, channel=None, network=None,
           nick_mask=None, sound=None, desktop=False, highlight_tab=False,
           suppress=False, suppress_notify=False, suppress_activity=False,
           window=None):
        """Register an /on hook.

        Args:
            event:    Event name (chanmsg, privmsg, join, part, quit, kick,
                      nick, topic, mode, connect, disconnect, signon, etc.)
            name:     Unique name for this hook (used for removal).
            pattern:  Wildcard pattern matched against the event's primary text
                      (message, nick, etc.).  Use ``'*'`` to match everything.
            command:  Command string, callable, or list of both.
                      Strings are executed as slash commands with
                      ``{nick}``, ``{channel}``, etc. expanded.  Multiple
                      commands can be separated with `` | `` (space-pipe-space).
                      Callables receive ``(variables_dict, conn)`` where
                      variables_dict has keys like ``'nick'``, ``'channel'``,
                      ``'message'``, etc.  Return truthy from a callable to
                      suppress the event.
                      A list can mix callables and strings, executed in order.
                      Optional if action flags are used.
            channel:  Optional channel filter (only fire in this channel).
            network:  Optional network filter (only fire on this network).
            nick_mask: Optional nick/hostmask filter (wildcards supported).
            sound:    Sound to play: ``'beep'``, ``'default'``, ``'none'``,
                      or a ``.wav`` path.
            desktop:  If True, show a desktop notification.
            highlight_tab: If True, highlight the channel tab.
            suppress: If True, suppress the default handler for the event
                      (the message won't appear in the window).
            window:   Optional window to run the command in.  If omitted,
                      the active window at fire-time is used.

        Example::

            irc.on('chanmsg', 'greet', '*hello*', '/msg {channel} Hello {nick}!')
            irc.on('chanmsg', 'vip', '*', sound='beep', desktop=True,
                   nick_mask='boss')
            irc.on('kick', 'kick_alert', '*', sound='beep', desktop=True)
            # Suppress join messages from bots:
            irc.on('join', 'hide_bots', '*', suppress=True, nick_mask='*bot*')
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
            'suppress': suppress,
            'suppress_notify': suppress_notify,
            'suppress_activity': suppress_activity,
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
        if name not in self._owned_timers:
            self._owned_timers.append(name)

    def cancel_timer(self, name):
        """Stop and remove a named timer."""
        import state
        info = state._timers.get(name)
        if info:
            info['timer'].stop()
            del state._timers[name]
        try:
            self._owned_timers.remove(name)
        except ValueError:
            pass

    # --- slash commands ---

    def add_command(self, name, func, help=''):
        """Register a slash command.  ``func(window, text)``.

        *name* is written without the command prefix (``'np'``, not ``'/np'``)
        and is matched case-insensitively, like every built-in command.  *text*
        arrives as a `commands.TokenizedString` with ``{variable}`` expansion
        already applied, exactly as a built-in receives it.

        Raises ValueError if *name* is already a built-in command.  A plugin
        cannot override a built-in: the alternative -- registering it and
        having the built-in win anyway -- is a registration that silently never
        fires, and this project has paid for that kind of failure repeatedly.
        Registering the same name twice from a plugin *is* allowed and replaces
        the previous one, so reloading a plugin does not need a special case.

        The lookup order is built-in, then plugin command, then `/alias`.
        `/alias` warns when it shadows either of the first two.
        """
        import state
        from commands import Commands
        key = name.strip().lower().lstrip('/')
        if not key:
            raise ValueError('command name is empty')
        if key.startswith('_') or ' ' in key:
            raise ValueError('bad command name: %r' % name)
        if hasattr(Commands, key) or key == 'exec':
            raise ValueError('/%s is a built-in command and cannot be '
                             'overridden by a plugin' % key)
        if not callable(func):
            raise ValueError('command handler for /%s is not callable' % key)
        state.plugin_commands[key] = {
            'func': func, 'help': help or '', 'owner': self._owner}
        if key not in self._owned_commands:
            self._owned_commands.append(key)
        return key

    def remove_command(self, name):
        """Unregister a slash command added with `add_command`."""
        import state
        key = name.strip().lower().lstrip('/')
        state.plugin_commands.pop(key, None)
        try:
            self._owned_commands.remove(key)
        except ValueError:
            pass

    # --- hotkeys ---

    def bind_key(self, sequence, func, description=''):
        """Bind an application-wide hotkey.  ``func()`` takes no arguments.

        *sequence* is anything `QKeySequence` understands: ``'F12'``,
        ``'Ctrl+Shift+P'``, ``'Alt+N'``.  The binding is application-scoped, so
        it fires from any window and while the input box has focus.

        Returns the canonical form of the sequence (which is also the key it is
        stored under, so ``'f12'`` and ``'F12'`` are one binding).  Raises
        ValueError if Qt cannot parse *sequence* -- an unparseable sequence
        yields a shortcut that silently never fires, which is the same
        silent-failure shape `add_command` refuses.

        **The test for "Qt could not parse this" is an empty `toString()`, not
        `isEmpty()`.**  `QKeySequence('not a key')` is *not* empty: it holds one
        key, `Qt.Key_unknown`, and reports `count() == 1`.  Only the round trip
        back to text shows it up, by coming back blank.  Checking `isEmpty()`
        caught a genuinely empty string and nothing else, so every typo in the
        hotkey box installed a live QShortcut bound to a key that does not
        exist -- registered, listed by `/hotkeys`, and unable to fire.

        Binding a sequence that is already bound replaces the old binding, so a
        plugin reload does not accumulate shortcuts.
        """
        import state
        from PySide6.QtGui import QKeySequence, QShortcut
        from PySide6.QtCore import Qt
        if not callable(func):
            raise ValueError('hotkey handler for %r is not callable' % sequence)
        seq = QKeySequence(str(sequence).strip())
        canonical = seq.toString()
        if seq.isEmpty() or not canonical:
            raise ValueError('cannot parse key sequence: %r' % sequence)
        self.unbind_key(canonical)
        parent = self.mainwin
        if parent is None:
            raise RuntimeError('no main window yet -- bind_key needs the GUI')
        sc = QShortcut(seq, parent)
        sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc.activated.connect(func)
        state.plugin_keys[canonical] = {
            'shortcut': sc, 'func': func, 'description': description or '',
            'owner': self._owner}
        if canonical not in self._owned_keys:
            self._owned_keys.append(canonical)
        return canonical

    def unbind_key(self, sequence):
        """Remove a hotkey bound with `bind_key`.  Returns True if there was one."""
        import state
        from PySide6.QtGui import QKeySequence
        canonical = QKeySequence(str(sequence).strip()).toString()
        if not canonical:
            # Unparseable, so it can never have been bound (bind_key refuses
            # it).  Returning early keeps '' from being usable as a lookup key
            # that every unparseable spelling would share.
            return False
        entry = state.plugin_keys.pop(canonical, None)
        try:
            self._owned_keys.remove(canonical)
        except ValueError:
            pass
        if entry is None:
            return False
        sc = entry['shortcut']
        # setEnabled first: the QShortcut outlives this call until Qt gets round
        # to deleting it, and an enabled orphan still fires.
        sc.setEnabled(False)
        sc.setParent(None)
        sc.deleteLater()
        return True

    # --- teardown ---

    def remove_all(self):
        """Remove everything registered through this view: hooks, timers,
        commands and hotkeys.

        Called by the loader on unload and reload -- not from `Callbacks.die()`
        -- so that a plugin which overrides `die()` without chaining up is
        still cleaned up.  A plugin whose command outlives it would raise from
        a dead instance; a hotkey that outlives it would do so invisibly.
        """
        self.remove_all_hooks()
        for name in list(self._owned_timers):
            self.cancel_timer(name)
        for name in list(self._owned_commands):
            self.remove_command(name)
        for seq in list(self._owned_keys):
            self.unbind_key(seq)


class _PluginIrc(_Irc):
    """One plugin's view of `_Irc`: same live state, its own ownership lists.

    Everything not listed here is inherited.  `clients`, `config` and `app` are
    already read from `state` on every access by the base class, so a view
    never goes stale; the two callables wired up by `_init` are read through to
    the singleton below for the same reason.  Only the registration bookkeeping
    is per-plugin.
    """

    def __init__(self, core, owner):
        # Deliberately *not* _Irc.__init__: that assigns
        # `self._get_active_window = None`, which the read-through properties
        # below refuse.
        self._core = core
        self._owner = owner
        self._owned_hooks = []
        self._owned_timers = []
        self._owned_commands = []
        self._owned_keys = []

    _get_active_window = property(lambda self: self._core._get_active_window)
    _get_networks = property(lambda self: self._core._get_networks)

    def _init(self, **kw):
        raise RuntimeError('_init() belongs to the plugin.irc singleton')


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

    To declare configuration options, set a class-level ``config_fields`` list::

        config_fields = [
            ('enabled', bool, True, 'Enable this plugin'),
            ('interval', int, 60, 'Check interval in seconds'),
            ('api_key', str, '', 'API key'),
        ]

    Supported types: ``str``, ``int``, ``float``, ``bool``.
    Values are stored under ``plugins.<name>:`` in the config YAML
    and editable in Settings > Plugins > <name>.

    Access values with ``irc.get_config('name', 'key', default)``.
    """

    config_fields = []  # override in subclass

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
    def config_changed(self, irc):
        """Called after the configuration is reloaded or the settings dialog
        is applied, so a plugin can re-read `irc.get_config(...)`.

        Only override this for settings that cannot be read lazily -- a hotkey
        bound with `bind_key` or a name registered with `add_command` was
        handed to something else at registration time, so it keeps the old
        value until the plugin re-registers it.  Anything read at the point of
        use is already current and needs nothing here.
        """
        pass

    def die(self):
        """Called when the plugin is unloaded.  Cleans up /on hooks."""
        self.irc.remove_all_hooks()
