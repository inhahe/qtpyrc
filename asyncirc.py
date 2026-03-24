"""
Asyncio-based IRC client protocol.

Drop-in replacement for the Twisted-based irc module, using asyncio streams
instead of Twisted's LineReceiver/ClientFactory. Preserves the same callback
interface (joined, privmsg, chanmsg, names, etc.) and sending methods
(join, say, msg, setNick, etc.) so that the rest of the application code
can remain largely unchanged.

Based on the original Twisted Words IRC implementation with modifications
by inhahe@gmail.com.
"""

import asyncio
import re
import string
import time
import traceback

import state

# --- Constants ---

NUL = '\0'
CR = '\r'
NL = '\n'
LF = NL
SPC = ' '

CHANNEL_PREFIXES = '&#!+'

X_DELIM = '\x01'
M_QUOTE = '\x10'
X_QUOTE = '\\'

# --- IRC case folding ---

_rfc1459_lower = str.maketrans(
    string.ascii_uppercase + '[\\]^',
    string.ascii_lowercase + '{|}~'
)

_ascii_lower = str.maketrans(
    string.ascii_uppercase,
    string.ascii_lowercase
)

_strict_rfc1459_lower = str.maketrans(
    string.ascii_uppercase + '[\\]',
    string.ascii_lowercase + '{|}'
)

_irclower_tables = {
    'ascii': _ascii_lower,
    'rfc1459': _rfc1459_lower,
    'rfc1549': _rfc1459_lower,  # common misspelling in old code
    'strict-rfc1459': _strict_rfc1459_lower,
    'strict-rfc1549': _strict_rfc1459_lower,
}

usersplit = re.compile(r"(?P<nick>.*?)!(?P<ident>.*?)@(?P<host>.*)").match

def irclower(text, mapping='rfc1459'):
    return text.translate(_irclower_tables.get(mapping, _rfc1459_lower))


# --- IRC message parsing ---

class IRCBadMessage(Exception):
    pass

# IRCv3 message tag value unescaping
_TAG_UNESCAPE = {
    ':': ';', 's': ' ', '\\': '\\', 'r': '\r', 'n': '\n',
}

def _unescape_tag_value(value):
    """Unescape an IRCv3 message tag value."""
    result = []
    i = 0
    while i < len(value):
        if value[i] == '\\' and i + 1 < len(value):
            result.append(_TAG_UNESCAPE.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            result.append(value[i])
            i += 1
    return ''.join(result)

def parsemsg(s):
    """Break an IRC message into tags, prefix, command, and arguments.

    Returns (tags, prefix, command, args) where tags is a dict of
    IRCv3 message tags (empty dict if none).
    """
    tags = {}
    if s.startswith('@'):
        tag_str, s = s.split(' ', 1)
        for part in tag_str[1:].split(';'):
            if not part:
                continue
            if '=' in part:
                k, v = part.split('=', 1)
                tags[k] = _unescape_tag_value(v)
            else:
                tags[part] = True
    prefix = ''
    if not s:
        raise IRCBadMessage("Empty line.")
    if s[0] == ':':
        prefix, s = s[1:].split(' ', 1)
    if ' :' in s:
        s, trailing = s.split(' :', 1)
        args = s.split()
        args.append(trailing)
    else:
        args = s.split()
    command = args.pop(0)
    return tags, prefix, command, args


# --- CTCP quoting/dequoting ---

mQuoteTable = {
    NUL: M_QUOTE + '0',
    NL:  M_QUOTE + 'n',
    CR:  M_QUOTE + 'r',
    M_QUOTE: M_QUOTE + M_QUOTE,
}

mDequoteTable = {}
for _k, _v in mQuoteTable.items():
    mDequoteTable[_v[-1]] = _k
del _k, _v

mEscape_re = re.compile('%s.' % re.escape(M_QUOTE), re.DOTALL)

def lowQuote(s):
    for c in (M_QUOTE, NUL, NL, CR):
        s = s.replace(c, mQuoteTable[c])
    return s

def lowDequote(s):
    def sub(m):
        c = m.group()[1]
        return mDequoteTable.get(c, c)
    return mEscape_re.sub(sub, s)

xQuoteTable = {
    X_DELIM: X_QUOTE + 'a',
    X_QUOTE: X_QUOTE + X_QUOTE,
}

xDequoteTable = {}
for _k, _v in xQuoteTable.items():
    xDequoteTable[_v[-1]] = _k
del _k, _v

xEscape_re = re.compile('%s.' % re.escape(X_QUOTE), re.DOTALL)

def ctcpQuote(s):
    for c in (X_QUOTE, X_DELIM):
        s = s.replace(c, xQuoteTable[c])
    return s

def ctcpDequote(s):
    def sub(m):
        c = m.group()[1]
        return xDequoteTable.get(c, c)
    return xEscape_re.sub(sub, s)

def ctcpExtract(message):
    """Extract CTCP data from a string."""
    extended_messages = []
    normal_messages = []
    retval = {'extended': extended_messages, 'normal': normal_messages}

    messages = message.split(X_DELIM)
    odd = False
    while messages:
        if odd:
            extended_messages.append(messages.pop(0))
        else:
            normal_messages.append(messages.pop(0))
        odd = not odd

    extended_messages[:] = [x for x in extended_messages if x]
    normal_messages[:] = [x for x in normal_messages if x]
    extended_messages[:] = [ctcpDequote(x) for x in extended_messages]

    for i in range(len(extended_messages)):
        m = extended_messages[i].split(SPC, 1)
        tag = m[0]
        data = m[1] if len(m) > 1 else None
        extended_messages[i] = (tag, data)

    return retval

def ctcpStringify(messages):
    coded = []
    for tag, data in messages:
        if data:
            if not isinstance(data, str):
                try:
                    data = " ".join(str(x) for x in data)
                except TypeError as e:
                    state.dbg(state.LOG_WARN, '[ctcp] stringify failed for tag=%r data=%r: %s' % (tag, data, e))
            m = "%s %s" % (tag, data)
        else:
            m = str(tag)
        m = ctcpQuote(m)
        coded.append("%s%s%s" % (X_DELIM, m, X_DELIM))
    return ''.join(coded)


# --- Numeric-to-symbolic mapping ---

symbolic_to_numeric = {
    "RPL_WELCOME": '001', "RPL_YOURHOST": '002', "RPL_CREATED": '003',
    "RPL_MYINFO": '004', "RPL_BOUNCE": '010', "RPL_ISUPPORT": '005',
    "RPL_USERHOST": '302', "RPL_ISON": '303', "RPL_AWAY": '301',
    "RPL_UNAWAY": '305', "RPL_NOWAWAY": '306',
    "RPL_WHOISUSER": '311', "RPL_WHOISSERVER": '312',
    "RPL_WHOISOPERATOR": '313', "RPL_WHOISIDLE": '317',
    "RPL_ENDOFWHOIS": '318', "RPL_WHOISCHANNELS": '319',
    "RPL_WHOWASUSER": '314', "RPL_ENDOFWHOWAS": '369',
    "RPL_LISTSTART": '321', "RPL_LIST": '322', "RPL_LISTEND": '323',
    "RPL_UNIQOPIS": '325', "RPL_CHANNELMODEIS": '324',
    "RPL_NOTOPIC": '331', "RPL_TOPIC": '332', "TOPICDATE": '333',
    "RPL_INVITING": '341', "RPL_SUMMONING": '342',
    "RPL_INVITELIST": '346', "RPL_ENDOFINVITELIST": '347',
    "RPL_EXCEPTLIST": '348', "RPL_ENDOFEXCEPTLIST": '349',
    "RPL_VERSION": '351', "RPL_WHOREPLY": '352', "RPL_ENDOFWHO": '315',
    "RPL_NAMREPLY": '353', "RPL_ENDOFNAMES": '366',
    "RPL_LINKS": '364', "RPL_ENDOFLINKS": '365',
    "RPL_BANLIST": '367', "RPL_ENDOFBANLIST": '368',
    "RPL_QUIETLIST": '728', "RPL_ENDOFQUIETLIST": '729',
    "RPL_INFO": '371', "RPL_ENDOFINFO": '374',
    "RPL_MOTDSTART": '375', "RPL_MOTD": '372', "RPL_ENDOFMOTD": '376',
    "RPL_YOUREOPER": '381', "RPL_REHASHING": '382',
    "RPL_YOURESERVICE": '383', "RPL_TIME": '391',
    "RPL_USERSSTART": '392', "RPL_USERS": '393',
    "RPL_ENDOFUSERS": '394', "RPL_NOUSERS": '395',
    "RPL_LUSERCLIENT": '251', "RPL_LUSEROP": '252',
    "RPL_LUSERUNKNOWN": '253', "RPL_LUSERCHANNELS": '254',
    "RPL_LUSERME": '255', "RPL_ADMINME": '256', "RPL_ADMINLOC": '258',
    "RPL_ADMINEMAIL": '259', "RPL_TRYAGAIN": '263',
    "ERR_NOSUCHNICK": '401', "ERR_NOSUCHSERVER": '402',
    "ERR_NOSUCHCHANNEL": '403', "ERR_CANNOTSENDTOCHAN": '404',
    "ERR_TOOMANYCHANNELS": '405', "ERR_WASNOSUCHNICK": '406',
    "ERR_TOOMANYTARGETS": '407', "ERR_NOSUCHSERVICE": '408',
    "ERR_NOORIGIN": '409', "ERR_NORECIPIENT": '411',
    "ERR_NOTEXTTOSEND": '412', "ERR_NOTOPLEVEL": '413',
    "ERR_WILDTOPLEVEL": '414', "ERR_BADMASK": '415',
    "ERR_UNKNOWNCOMMAND": '421', "ERR_NOMOTD": '422',
    "ERR_NOADMININFO": '423', "ERR_FILEERROR": '424',
    "ERR_NONICKNAMEGIVEN": '431', "ERR_ERRONEUSNICKNAME": '432',
    "ERR_NICKNAMEINUSE": '433', "ERR_NICKCOLLISION": '436',
    "ERR_UNAVAILRESOURCE": '437', "ERR_USERNOTINCHANNEL": '441',
    "ERR_NOTONCHANNEL": '442', "ERR_USERONCHANNEL": '443',
    "ERR_NOLOGIN": '444', "ERR_SUMMONDISABLED": '445',
    "ERR_USERSDISABLED": '446', "ERR_NOTREGISTERED": '451',
    "ERR_NEEDMOREPARAMS": '461', "ERR_ALREADYREGISTRED": '462',
    "ERR_NOPERMFORHOST": '463', "ERR_PASSWDMISMATCH": '464',
    "ERR_YOUREBANNEDCREEP": '465', "ERR_YOUWILLBEBANNED": '466',
    "ERR_KEYSET": '467', "ERR_CHANNELISFULL": '471',
    "ERR_UNKNOWNMODE": '472', "ERR_INVITEONLYCHAN": '473',
    "ERR_BANNEDFROMCHAN": '474', "ERR_BADCHANNELKEY": '475',
    "ERR_BADCHANMASK": '476', "ERR_NOCHANMODES": '477',
    "ERR_BANLISTFULL": '478', "ERR_NOPRIVILEGES": '481',
    "ERR_CHANOPRIVSNEEDED": '482', "ERR_CANTKILLSERVER": '483',
    "ERR_RESTRICTED": '484', "ERR_UNIQOPPRIVSNEEDED": '485',
    "ERR_NOOPERHOST": '491', "ERR_NOSERVICEHOST": '492',
    "ERR_UMODEUNKNOWNFLAG": '501', "ERR_USERSDONTMATCH": '502',
    # SASL numerics
    "RPL_LOGGEDIN": '900', "RPL_LOGGEDOUT": '901',
    "ERR_NICKLOCKED": '902', "RPL_SASLSUCCESS": '903',
    "ERR_SASLFAIL": '904', "ERR_SASLTOOLONG": '905',
    "ERR_SASLABORTED": '906', "ERR_SASLALREADY": '907',
    "RPL_SASLMECHS": '908',
    # STARTTLS
    "RPL_STARTTLS": '670', "ERR_STARTTLS": '691',
}

numeric_to_symbolic = {v: k for k, v in symbolic_to_numeric.items()}


# --- IRCClient ---

class IRCClient:
    """Asyncio-based IRC client with the same callback interface as the
    Twisted version.

    Usage:
        client = IRCClient()
        client.nickname = 'mynick'
        await client.connect('irc.example.com', 6667)
        # client runs until disconnected
    """

    nickname = 'irc'
    password = None
    realname = None
    username = None
    performLogin = True
    lineRate = None          # legacy simple rate limit (seconds between lines)
    floodBurst = 5            # messages allowed in the initial burst
    floodRate = 2.0           # seconds between messages after burst exhausted

    sourceURL = None
    versionName = None
    versionNum = None
    versionEnv = None
    userinfo = None
    fingerReply = None

    motd = ""

    _modeAcceptsArg = {
        'o': (True, True), 'h': (True, True), 'v': (True, True),
        'b': (True, True), 'l': (True, False), 'k': (True, False),
        't': (False, False), 's': (False, False), 'p': (False, False),
        'i': (False, False), 'm': (False, False), 'n': (False, False),
    }

    def __init__(self):
        self._reader = None
        self._writer = None
        self._queue = []
        self._queueEmptying = False
        self._flood_tokens = self.floodBurst  # available burst tokens
        self._flood_last = 0.0                # time.monotonic() of last token replenish
        self._casemapping = 'rfc1459'
        self._network_name = None        # set by ISUPPORT NETWORK=
        self._read_task = None
        self._prefix_modes = 'ohv'       # updated by ISUPPORT PREFIX
        self._prefix_symbols = '@%+'     # updated by ISUPPORT PREFIX
        self._monitor_supported = False  # set by ISUPPORT MONITOR
        self._monitor_limit = 0          # max targets (0 = unlimited)
        # Make a mutable copy so ISUPPORT can update per-connection
        self._modeAcceptsArg = dict(self.__class__._modeAcceptsArg)
        self._chanmodes_raw = ''  # CHANMODES=A,B,C,D from ISUPPORT
        # IRCv3 state
        self._current_tags = {}           # message tags for the line being processed
        self._cap_negotiating = False
        self._cap_available = {}          # cap_name -> value from CAP LS
        self._cap_enabled = set()         # caps that have been ACK'd
        self._cap_ls_buffer = []          # for multi-line CAP LS
        self._batches = {}                # ref -> {type, params}

    def irclower(self, text):
        return irclower(text, self._casemapping)

    # --- Connection ---

    async def connect(self, host, port=6667, tls=False, tls_verify=True,
                      starttls=False, family=0):
        """Connect to an IRC server. Returns when the connection is lost."""
        self._tls_verify = tls_verify
        self._starttls_requested = starttls
        self._starttls_done = False
        ssl_ctx = None
        if tls and not starttls:
            import ssl
            ssl_ctx = ssl.create_default_context()
            if not tls_verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
        self._reader, self._writer = await asyncio.open_connection(
            host, port, ssl=ssl_ctx, family=family)
        self.connectionMade()
        try:
            await self._read_loop()
        except asyncio.CancelledError:
            state.dbg(state.LOG_INFO, '[irc] connection cancelled:', host)
        except (ConnectionError, OSError) as e:
            state.dbg(state.LOG_INFO, '[irc] connection lost:', host, str(e))
        finally:
            self.connectionLost("Connection closed")
            self._cleanup()

    def _cleanup(self):
        self._queue.clear()
        self._queueEmptying = False
        if self._writer and not self._writer.is_closing():
            self._writer.close()
        self._writer = None
        self._reader = None

    async def _read_loop(self):
        buffer = b''
        while True:
            data = await self._reader.read(4096)
            if not data:
                break
            buffer += data
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                line = line.rstrip(b'\r')
                if line:
                    self._lineReceived(line.decode('utf-8', errors='replace'))

    def disconnect(self):
        """Close the connection."""
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
            except Exception as e:
                state.dbg(state.LOG_DEBUG, '[irc] writer close error:', e)

    # --- Line protocol ---

    def _send_raw(self, line):
        if self._writer is None or self._writer.is_closing():
            return
        raw = (lowQuote(line) + '\r\n').encode('utf-8')
        self._writer.write(raw)

    def _replenish_tokens(self):
        """Refill burst tokens based on elapsed time (token bucket)."""
        import time as _time
        now = _time.monotonic()
        elapsed = now - self._flood_last
        if elapsed > 0 and self.floodRate > 0:
            gained = elapsed / self.floodRate
            self._flood_tokens = min(self.floodBurst, self._flood_tokens + gained)
            self._flood_last = now

    def sendLine(self, line):
        if self._writer is None or self._writer.is_closing():
            return
        if self.floodRate and self.floodRate > 0:
            self._queue.append(line)
            if not self._queueEmptying:
                self._queueEmptying = True
                asyncio.ensure_future(self._drain_queue())
        elif self.lineRate and self.lineRate > 0:
            # Legacy fallback: simple fixed delay
            self._queue.append(line)
            if not self._queueEmptying:
                self._queueEmptying = True
                asyncio.ensure_future(self._drain_queue_legacy())
        else:
            self._send_raw(line)

    async def _drain_queue(self):
        """Token bucket queue drainer — allows bursts, then throttles."""
        import time as _time
        try:
            while self._queue:
                if self._writer is None or self._writer.is_closing():
                    self._queue.clear()
                    break
                self._replenish_tokens()
                if self._flood_tokens >= 1.0:
                    # Have tokens — send immediately
                    line = self._queue.pop(0)
                    self._flood_tokens -= 1.0
                    self._send_raw(line)
                else:
                    # Wait for next token
                    wait = (1.0 - self._flood_tokens) * self.floodRate
                    await asyncio.sleep(wait)
        finally:
            self._queueEmptying = False

    async def _drain_queue_legacy(self):
        """Legacy drainer: fixed delay between every message."""
        try:
            while self._queue:
                if self._writer is None or self._writer.is_closing():
                    self._queue.clear()
                    break
                line = self._queue.pop(0)
                self._send_raw(line)
                if self._queue:
                    await asyncio.sleep(self.lineRate)
        finally:
            self._queueEmptying = False

    def _lineReceived(self, line):
        line = lowDequote(line)
        try:
            tags, prefix, command, params = parsemsg(line)
            if command in numeric_to_symbolic:
                command = numeric_to_symbolic[command]
            self._current_tags = tags
            self.handleCommand(command, prefix, params)
        except IRCBadMessage:
            state.dbg(state.LOG_WARN, '[irc] malformed message:', repr(line[:200]))
        except Exception:
            traceback.print_exc()
        finally:
            self._current_tags = {}

    def handleCommand(self, command, prefix, params):
        method = getattr(self, "irc_%s" % command, None)
        try:
            if method is not None:
                method(prefix, params)
            else:
                self.irc_unknown(prefix, command, params)
        except Exception:
            traceback.print_exc()

    # --- Registration ---

    def register(self, nickname, hostname='foo', servername='bar'):
        if self.password is not None:
            self.sendLine("PASS %s" % self.password)
        self.nickname = nickname  # Set initial nick before registration
        self.setNick(nickname)
        if self.username is None:
            self.username = nickname
        if self.realname is None:
            self.realname = nickname
        self.sendLine("USER %s %s %s :%s" % (
            self.username, hostname, servername, self.realname))

    # --- Sending commands ---

    def setNick(self, nickname):
        self._pending_nick = nickname
        self.sendLine("NICK %s" % nickname)

    def join(self, channel, key=None):
        if channel[0] not in CHANNEL_PREFIXES:
            channel = '#' + channel
        if key:
            self.sendLine("JOIN %s %s" % (channel, key))
        else:
            self.sendLine("JOIN %s" % channel)

    def leave(self, channel, reason=None):
        if channel[0] not in CHANNEL_PREFIXES:
            channel = '#' + channel
        if reason:
            self.sendLine("PART %s :%s" % (channel, reason))
        else:
            self.sendLine("PART %s" % channel)

    part = leave

    def _max_msg_bytes(self, target):
        """Return the max byte length for the text portion of a PRIVMSG to *target*.

        IRC lines are max 512 bytes including \\r\\n.  The server prepends
        :nick!user@host so we must account for our own prefix.
        """
        # Best-effort own prefix length; fall back to generous estimate
        nick = self.nickname or ''
        ident = getattr(self, 'username', '') or nick
        # Try to get our actual host from user tracking
        host = ''
        client = getattr(self, 'client', None)
        if client:
            own = client.users.get(self.irclower(nick))
            if own and own.host:
                host = own.host
        if not host:
            host = 'x' * 63  # max hostname length as safe fallback
        # :nick!ident@host PRIVMSG target :\r\n
        overhead = 1 + len(nick.encode('utf-8')) + 1 + len(ident.encode('utf-8')) + \
                   1 + len(host.encode('utf-8')) + 1 + 8 + len(target.encode('utf-8')) + 2 + 2
        return max(512 - overhead, 100)  # floor at 100 to avoid degenerate splits

    def split_message(self, target, message, extra_overhead=0):
        """Split *message* into chunks that fit within IRC line limits.

        *extra_overhead* accounts for additional bytes consumed by wrapping
        (e.g. 9 for CTCP ACTION: ``\\x01ACTION ...\\x01``).
        Splits on word boundaries when possible.  Returns a list of strings.
        """
        max_bytes = self._max_msg_bytes(target) - extra_overhead
        encoded = message.encode('utf-8')
        if len(encoded) <= max_bytes:
            return [message]
        chunks = []
        while encoded:
            if len(encoded) <= max_bytes:
                chunks.append(encoded.decode('utf-8', errors='replace'))
                break
            # Find a split point at or before max_bytes
            split = max_bytes
            # Don't split in the middle of a UTF-8 sequence
            while split > 0 and (encoded[split] & 0xC0) == 0x80:
                split -= 1
            # Try to split on a space
            space = encoded.rfind(b' ', 0, split + 1)
            if space > max_bytes // 2:  # only use space if it's not too far back
                split = space
            chunk = encoded[:split].decode('utf-8', errors='replace')
            chunks.append(chunk)
            encoded = encoded[split:].lstrip(b' ')  # skip the space at split point
        return chunks

    def say(self, channel, message, length=None):
        if channel[0] not in CHANNEL_PREFIXES:
            channel = '#' + channel
        self.msg(channel, message, length)

    def msg(self, user, message, length=None):
        for chunk in self.split_message(user, message):
            self.sendLine("PRIVMSG %s :%s" % (user, chunk))

    def notice(self, user, message):
        for chunk in self.split_message(user, message):
            self.sendLine("NOTICE %s :%s" % (user, chunk))

    def kick(self, channel, user, reason=None):
        if channel[0] not in CHANNEL_PREFIXES:
            channel = '#' + channel
        if reason:
            self.sendLine("KICK %s %s :%s" % (channel, user, reason))
        else:
            self.sendLine("KICK %s %s" % (channel, user))

    def topic(self, channel, topic=None):
        if channel[0] not in CHANNEL_PREFIXES:
            channel = '#' + channel
        if topic is not None:
            self.sendLine("TOPIC %s :%s" % (channel, topic))
        else:
            self.sendLine("TOPIC %s" % channel)

    def mode(self, chan, set_, modes, limit=None, user=None, mask=None):
        line = 'MODE %s %s%s' % (chan, '+' if set_ else '-', modes)
        if limit is not None:
            line = '%s %d' % (line, limit)
        elif user is not None:
            line = '%s %s' % (line, user)
        elif mask is not None:
            line = '%s %s' % (line, mask)
        self.sendLine(line)

    def away(self, message=''):
        self.sendLine("AWAY :%s" % message)

    def back(self):
        self.away()

    def whois(self, nickname, server=None):
        if server is None:
            self.sendLine('WHOIS ' + nickname)
        else:
            self.sendLine('WHOIS %s %s' % (server, nickname))

    def quit(self, message=''):
        self.sendLine("QUIT :%s" % message)

    def me(self, channel, action):
        if channel[0] not in CHANNEL_PREFIXES:
            channel = '#' + channel
        self.ctcpMakeQuery(channel, [('ACTION', action)])

    def ctcpMakeQuery(self, user, messages):
        self.sendLine("PRIVMSG %s :%s" % (user, ctcpStringify(messages)))

    def ctcpMakeReply(self, user, messages):
        self.sendLine("NOTICE %s :%s" % (user, ctcpStringify(messages)))

    # --- Lifecycle callbacks (override in subclass) ---

    def connectionMade(self):
        import time as _time
        self._queue = []
        self._flood_tokens = self.floodBurst
        self._flood_last = _time.monotonic()
        self._cap_negotiating = True
        self._cap_available = {}
        self._cap_enabled = set()
        self._cap_ls_buffer = []
        self._batches = {}
        self._current_tags = {}
        if self._starttls_requested and not self._starttls_done:
            # Request STARTTLS before anything else
            self._send_raw("STARTTLS")
        else:
            # Start CAP negotiation before registration
            self._send_raw("CAP LS 302")
            if self.performLogin:
                self.register(self.nickname)

    def connectionLost(self, reason):
        pass

    # --- STARTTLS ---

    def irc_RPL_STARTTLS(self, prefix, params):
        """Server is ready for TLS upgrade."""
        asyncio.ensure_future(self._upgrade_tls())

    def irc_ERR_STARTTLS(self, prefix, params):
        """STARTTLS failed — continue unencrypted."""
        msg = ' '.join(params[1:]) if len(params) > 1 else 'STARTTLS failed'
        state.dbg(state.LOG_WARN, '[irc] STARTTLS failed:', msg)
        # Continue with registration
        if self.performLogin:
            self.register(self.nickname)

    async def _upgrade_tls(self):
        """Upgrade the plain connection to TLS after STARTTLS."""
        import ssl
        ssl_ctx = ssl.create_default_context()
        if not self._tls_verify:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        transport = self._writer.transport
        protocol = transport.get_protocol()
        loop = asyncio.get_event_loop()
        new_transport = await loop.start_tls(transport, protocol, ssl_ctx)
        self._writer._transport = new_transport
        self._starttls_done = True
        state.dbg(state.LOG_INFO, '[irc] TLS upgrade complete')
        # Now proceed with CAP negotiation + registration
        self._send_raw("CAP LS 302")
        if self.performLogin:
            self.register(self.nickname)

    # --- Event callbacks (override in subclass) ---

    def signedOn(self, msg):
        pass

    def joined(self, channel):
        pass

    def left(self, channel):
        pass

    def privmsg(self, user, message):
        pass

    def chanmsg(self, user, channel, message):
        pass

    def noticed(self, user, channel, message):
        pass

    def names(self, channel, nicks):
        pass

    def endofnames(self, channel):
        pass

    def userJoined(self, nickidhost, channel):
        pass

    def userLeft(self, user, channel):
        pass

    def userQuit(self, user, quitMessage):
        pass

    def userKicked(self, kickee, channel, kicker, message):
        pass

    def kickedFrom(self, channel, kicker, message):
        pass

    def nickChanged(self, nick):
        self.nickname = nick

    def userRenamed(self, oldname, newname):
        pass

    def modeChanged(self, user, channel, set_, modes, args):
        pass

    def topicUpdated(self, user, channel, newTopic):
        pass

    def action(self, user, channel, data):
        pass

    def receivedMOTD(self, motd):
        pass

    def isupport(self, options):
        pass

    def bounce(self, info):
        pass

    def irc_unknown(self, prefix, command, params):
        pass

    # --- IRCv3 CAP negotiation ---

    def _get_desired_caps(self):
        """Return a list of capability names to request.  Override in subclass."""
        return ['batch', 'server-time']

    def _cap_negotiate(self):
        """Request desired capabilities that the server supports."""
        desired = self._get_desired_caps()
        to_request = [c for c in desired if c in self._cap_available]
        if to_request:
            self._send_raw("CAP REQ :%s" % ' '.join(to_request))
        else:
            self._end_cap_negotiation()

    def _end_cap_negotiation(self):
        if self._cap_negotiating:
            self._cap_negotiating = False
            self._send_raw("CAP END")

    @property
    def _sasl_in_progress(self):
        return False

    def capsAcknowledged(self, caps):
        """Called when capabilities are ACK'd.  Override in subclass for SASL etc."""
        pass

    def capsDenied(self, caps):
        """Called when capabilities are NAK'd."""
        pass

    def batchStarted(self, ref, batch_type, params):
        """Called when a BATCH opens.  Override in subclass."""
        pass

    def batchEnded(self, ref, batch_type, params):
        """Called when a BATCH closes.  Override in subclass."""
        pass

    def saslAuthenticate(self, data):
        """Handle SASL AUTHENTICATE challenge.  Override in subclass."""
        pass

    def irc_CAP(self, prefix, params):
        if len(params) < 2:
            return
        subcmd = params[1].upper()
        if subcmd == 'LS':
            # Multi-line: [*, LS, *, :caps] or final: [*, LS, :caps]
            if len(params) >= 4 and params[2] == '*':
                self._cap_ls_buffer.append(params[3])
            else:
                caps_str = params[-1] if len(params) > 2 else ''
                if self._cap_ls_buffer:
                    self._cap_ls_buffer.append(caps_str)
                    caps_str = ' '.join(self._cap_ls_buffer)
                    self._cap_ls_buffer = []
                self._cap_available = {}
                for cap in caps_str.split():
                    if '=' in cap:
                        name, value = cap.split('=', 1)
                        self._cap_available[name] = value
                    else:
                        self._cap_available[cap] = ''
                self._cap_negotiate()
        elif subcmd == 'ACK':
            caps = params[-1].split() if len(params) > 2 else []
            for cap in caps:
                self._cap_enabled.add(cap.lstrip('-'))
            self.capsAcknowledged(caps)
            if self._cap_negotiating and not self._sasl_in_progress:
                self._end_cap_negotiation()
        elif subcmd == 'NAK':
            caps = params[-1].split() if len(params) > 2 else []
            self.capsDenied(caps)
            if self._cap_negotiating and not self._sasl_in_progress:
                self._end_cap_negotiation()

    def irc_BATCH(self, prefix, params):
        if not params:
            return
        ref = params[0]
        if ref.startswith('+'):
            ref = ref[1:]
            batch_type = params[1] if len(params) > 1 else ''
            batch_params = params[2:] if len(params) > 2 else []
            self._batches[ref] = {'type': batch_type, 'params': batch_params}
            self.batchStarted(ref, batch_type, batch_params)
        elif ref.startswith('-'):
            ref = ref[1:]
            batch = self._batches.pop(ref, None)
            if batch:
                self.batchEnded(ref, batch['type'], batch['params'])

    def irc_TAGMSG(self, prefix, params):
        """Handle IRCv3 TAGMSG (message with tags but no text body)."""
        if not params:
            return
        nick = prefix.split('!', 1)[0]
        target = params[0]
        self.tagmsgReceived(nick, target, self._current_tags)

    def tagmsgReceived(self, nick, target, tags):
        """Called when a TAGMSG is received.  Override in subclass."""
        pass

    def irc_AUTHENTICATE(self, prefix, params):
        self.saslAuthenticate(params[0] if params else '+')

    def irc_RPL_LOGGEDIN(self, prefix, params):
        pass

    def irc_RPL_SASLSUCCESS(self, prefix, params):
        pass

    def irc_ERR_SASLFAIL(self, prefix, params):
        pass

    def irc_ERR_SASLTOOLONG(self, prefix, params):
        pass

    def irc_ERR_SASLABORTED(self, prefix, params):
        pass

    def irc_ERR_SASLALREADY(self, prefix, params):
        pass

    def irc_RPL_SASLMECHS(self, prefix, params):
        pass

    # --- IRC command handlers ---

    def irc_RPL_WELCOME(self, prefix, params):
        # If we get RPL_WELCOME while still negotiating, the server ignored CAP
        if self._cap_negotiating:
            self._cap_negotiating = False
        # Server confirms our nick in params[0]
        if params:
            self.nickname = params[0]
        self.signedOn(params[1] if len(params) > 1 else '')

    def irc_JOIN(self, prefix, params):
        nick = prefix.split('!', 1)[0]
        channel = params[-1]
        if self.irclower(nick) == self.irclower(self.nickname):
            self.joined(channel)
        else:
            self.userJoined(prefix, channel)

    def irc_PART(self, prefix, params):
        nick = prefix.split('!', 1)[0]
        channel = params[0]
        if nick == self.nickname:
            self.left(channel)
        else:
            self.userLeft(prefix, channel)

    def irc_QUIT(self, prefix, params):
        self.userQuit(prefix, params[0] if params else '')

    def irc_MODE(self, prefix, params):
        user, channel = prefix, params[0]
        modes, args = params[1], params[2:]
        if modes[0] not in '+-':
            modes = '+' + modes
        if ((modes[0] == '+' and '-' not in modes[1:]) or
            (modes[0] == '-' and '+' not in modes[1:])):
            set_ = (modes[0] == '+')
            modes = modes[1:].replace('-+'[set_], '')
            self.modeChanged(user, channel, set_, modes, tuple(args))
        else:
            modes2, args2 = ['', ''], [[], []]
            i = 0
            for c in modes:
                if c == '+':
                    i = 0
                elif c == '-':
                    i = 1
                else:
                    modes2[i] += c
                    if args and self._modeAcceptsArg.get(c, (False, False))[i]:
                        args2[i].append(args.pop(0))
            self.modeChanged(user, channel, True, modes2[0], tuple(args2[0]))
            self.modeChanged(user, channel, False, modes2[1], tuple(args2[1]))

    def irc_INVITE(self, prefix, params):
        nick = prefix.split('!', 1)[0]
        channel = params[-1] if params else ''
        self.invited(nick, channel)

    def invited(self, nick, channel):
        """Called when we are invited to a channel. Override in subclass."""
        pass

    def irc_PING(self, prefix, params):
        self.sendLine("PONG %s" % params[-1])

    def irc_PRIVMSG(self, prefix, params):
        dest = params[0]
        message = params[-1]
        if message.startswith(X_DELIM):
            m = ctcpExtract(message)
            if m['extended']:
                self.ctcpQuery(prefix, dest, m['extended'])
            if not m['normal']:
                return
            message = ' '.join(m['normal'])
        if dest == self.nickname:
            self.privmsg(prefix, message)
        else:
            self.chanmsg(prefix, dest, message)

    def irc_NOTICE(self, prefix, params):
        user = prefix
        channel = params[0]
        message = params[-1]
        if message and message[0] == X_DELIM:
            m = ctcpExtract(message)
            if m['extended']:
                for tag, data in m['extended']:
                    self.ctcpReply(user, tag, data)
            if not m['normal']:
                return
            message = ' '.join(m['normal'])
        self.noticed(user, channel, message)

    def ctcpReply(self, user, tag, data):
        """Called when a CTCP reply arrives (via NOTICE).  Override in subclass."""
        pass

    def irc_NICK(self, prefix, params):
        nick = prefix.split('!', 1)[0]
        if self.irclower(nick) == self.irclower(self.nickname):
            self.nickChanged(params[0])
        else:
            self.userRenamed(nick, params[0])

    def irc_KICK(self, prefix, params):
        kicker = prefix.split('!')[0]
        channel = params[0]
        kicked = params[1]
        message = params[-1]
        if kicked.lower() == self.nickname.lower():
            self.kickedFrom(channel, kicker, message)
        else:
            self.userKicked(kicked, channel, kicker, message)

    def irc_TOPIC(self, prefix, params):
        user = prefix.split('!')[0]
        channel = params[0]
        newtopic = params[1]
        self.topicUpdated(user, channel, newtopic)

    def irc_RPL_TOPIC(self, prefix, params):
        user = prefix.split('!')[0]
        channel = params[1]
        newtopic = params[2]
        self.topicUpdated(user, channel, newtopic)

    def irc_RPL_NOTOPIC(self, prefix, params):
        user = prefix.split('!')[0]
        channel = params[1]
        self.topicUpdated(user, channel, "")

    def irc_RPL_MOTDSTART(self, prefix, params):
        msg = params[-1]
        if msg.startswith("- "):
            msg = msg[2:]
        self.motd = [msg]

    def irc_RPL_MOTD(self, prefix, params):
        msg = params[-1]
        if msg.startswith("- "):
            msg = msg[2:]
        if isinstance(self.motd, list):
            self.motd.append(msg)

    def irc_RPL_ENDOFMOTD(self, prefix, params):
        if isinstance(self.motd, list):
            self.receivedMOTD(self.motd)

    def irc_RPL_NAMREPLY(self, prefix, params):
        self.names(params[2], params[3].split())

    def irc_RPL_ENDOFNAMES(self, prefix, params):
        self.endofnames(params[1])

    def irc_RPL_ISON(self, prefix, params):
        online = params[1].split() if len(params) > 1 and params[1].strip() else []
        self.isonReply(online)

    def isonReply(self, online_nicks):
        """Called with the list of online nicks from an ISON reply."""
        pass

    # --- MONITOR numerics (730-734) ---

    def irc_730(self, prefix, params):
        """RPL_MONONLINE — monitored nicks that are online."""
        # :server 730 me :nick1!user@host,nick2!user@host
        targets = params[-1].split(',') if params else []
        nicks = [t.split('!', 1)[0] for t in targets if t]
        self.monitorOnline(nicks)

    def irc_731(self, prefix, params):
        """RPL_MONOFFLINE — monitored nicks that are offline."""
        # :server 731 me :nick1,nick2
        targets = params[-1].split(',') if params else []
        nicks = [t.split('!', 1)[0] for t in targets if t]
        self.monitorOffline(nicks)

    def irc_732(self, prefix, params):
        """RPL_MONLIST — response to MONITOR L."""
        pass

    def irc_733(self, prefix, params):
        """RPL_ENDOFMONLIST — end of MONITOR L response."""
        pass

    def irc_734(self, prefix, params):
        """ERR_MONLISTFULL — monitor list is full."""
        pass

    def monitorOnline(self, nicks):
        """Called when monitored nicks come online."""
        pass

    def monitorOffline(self, nicks):
        """Called when monitored nicks go offline."""
        pass

    def irc_RPL_ISUPPORT(self, prefix, params):
        if len(params) > 0 and params[0].startswith("Try server "):
            self.bounce(params)
        else:
            args = params[1:-1]
            self.isupport(args)
            for arg in args:
                if '=' in arg:
                    key, value = arg.split('=', 1)
                    if key == 'CASEMAPPING' and value in _irclower_tables:
                        self._casemapping = value
                    elif key == 'CHANMODES':
                        self._chanmodes_raw = value
                        self._parseChanModes(value)
                    elif key == 'PREFIX':
                        self._parsePrefix(value)
                    elif key == 'NETWORK':
                        self._network_name = value
                    elif key == 'MONITOR':
                        self._monitor_supported = True
                        self._monitor_limit = int(value) if value.isdigit() else 0
                else:
                    # Bare token (no =)
                    if arg == 'MONITOR':
                        self._monitor_supported = True

    def _parseChanModes(self, value):
        """Parse CHANMODES=A,B,C,D and update _modeAcceptsArg.

        Type A: list modes, always take a parameter (e.g. b, e, I)
        Type B: always take a parameter (e.g. k)
        Type C: take parameter only when being set (e.g. l)
        Type D: never take a parameter (e.g. i, m, n, s, t)
        """
        parts = value.split(',')
        type_a = parts[0] if len(parts) > 0 else ''
        type_b = parts[1] if len(parts) > 1 else ''
        type_c = parts[2] if len(parts) > 2 else ''
        type_d = parts[3] if len(parts) > 3 else ''
        for c in type_a:
            self._modeAcceptsArg[c] = (True, True)
        for c in type_b:
            self._modeAcceptsArg[c] = (True, True)
        for c in type_c:
            self._modeAcceptsArg[c] = (True, False)
        for c in type_d:
            self._modeAcceptsArg[c] = (False, False)

    def _parsePrefix(self, value):
        """Parse PREFIX=(ohv)@%+ and update _modeAcceptsArg.

        All prefix modes always take a parameter (the nick).
        Also store the mapping for later use.
        """
        # Format: (modes)symbols  e.g. (qaohv)~&@%+
        import re
        m = re.match(r'\(([^)]+)\)(.*)', value)
        if m:
            modes, symbols = m.groups()
            self._prefix_modes = modes      # e.g. 'qaohv'
            self._prefix_symbols = symbols   # e.g. '~&@%+'
            for c in modes:
                self._modeAcceptsArg[c] = (True, True)

    def irc_RPL_CREATED(self, prefix, params):
        pass

    def irc_RPL_YOURHOST(self, prefix, params):
        pass

    def irc_RPL_MYINFO(self, prefix, params):
        pass

    def irc_RPL_LUSERCLIENT(self, prefix, params):
        pass

    def irc_RPL_LUSEROP(self, prefix, params):
        pass

    def irc_RPL_LUSERCHANNELS(self, prefix, params):
        pass

    def irc_RPL_LUSERME(self, prefix, params):
        pass

    def irc_ERR_NICKNAMEINUSE(self, prefix, params):
        """Handle 433 (nickname in use) by appending _ to nick and retrying."""
        tried = getattr(self, '_pending_nick', self.nickname)
        self.setNick(tried + '_')

    # --- CTCP ---

    def ctcpQuery(self, orig, dest, messages):
        fnpref = "Priv" if dest == self.nickname else "Chan"
        for tag, data in messages:
            if dest == self.nickname:
                args = (orig, data)
            else:
                args = (orig, dest, data)
            method = getattr(self, "ctcp_%s%s" % (fnpref, tag), None)
            if method is None:
                method = getattr(self, "ctcp_%s" % tag, None)
            if method is not None:
                try:
                    method(*args)
                except Exception:
                    traceback.print_exc()

    def ctcp_PrivACTION(self, user, data):
        self.action(user, self.nickname, data)

    def ctcp_ChanACTION(self, user, channel, data):
        self.action(user, channel, data)

    def ctcp_PrivPING(self, user, data):
        self.ctcpMakeReply(user.split('!')[0], [('PING', data)])

    def ctcp_PrivVERSION(self, user, data):
        if self.versionName:
            self.ctcpMakeReply(user.split('!')[0],
                [('VERSION', '%s:%s:%s' % (
                    self.versionName,
                    self.versionNum or '',
                    self.versionEnv or ''))])
