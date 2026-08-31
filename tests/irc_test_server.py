"""Minimal IRC server for automated testing of qtpyrc /on events.

Speaks enough of the IRC protocol to:
  - Accept a client connection and complete registration (NICK/USER -> 001-005 + MOTD)
  - Let the client JOIN a channel, see another user, and receive all
    event types that qtpyrc /on hooks can listen for.
  - Expose a simple command interface so the test runner can trigger
    events on demand via a control socket.

Usage:
  python irc_test_server.py [--port 6699] [--control-port 6700]

The control socket accepts one-line text commands:
  CHANMSG <nick> <channel> <text>     — channel message from <nick>
  PRIVMSG <nick> <text>               — private message from <nick>
  ACTION <nick> <channel> <text>      — CTCP ACTION (/me) in channel
  NOTICE <nick> <channel> <text>      — NOTICE to a channel or to the client
  SERVERNOTICE <text>                 — NOTICE from the server itself (no
                                        nick!ident@host prefix)
  BATCH <ref> [type] [params]         — open a batch (default type
                                        znc.in/playback); every line sent until
                                        ENDBATCH is tagged as part of it
  ENDBATCH [ref]                      — close the open batch
  JOIN <nick> <channel>               — <nick> joins channel
  PART <nick> <channel> [reason]      — <nick> parts channel
  QUIT <nick> [reason]                — <nick> quits
  KICK <kicker> <channel> <target> [reason] — kick a user
  NICK <oldnick> <newnick>            — nick change
  TOPIC <nick> <channel> <text>       — topic change
  MODE <nick> <channel> <modes> [args]— mode change
  INVITE <nick> <channel>             — invite the client
  CTCPREPLY <nick> <tag> [data]       — CTCP reply from nick
  RAWCMD <prefix> <command> <params>  — raw unknown command
  NUMERIC <num> <params>              — raw numeric to client
  RECEIVED [command]                  — every line the client has sent, " | "
                                        separated; optionally only those with
                                        the given first word (e.g. "JOIN")
  REJECT <channel> [num] [text]       — refuse JOINs to <channel> with that
                                        numeric (default 474, +b) instead of
                                        letting the client in
  DISCONNECT                          — drop the client connection
  SHUTDOWN                            — stop the server
"""

import asyncio
import sys
import time

SERVER_NAME = "test.irc.local"
SERVER_VERSION = "qtpyrc-test-0.1"
NETWORK_NAME = "TestNet"

# Capabilities this server advertises in CAP LS and will ACK.  `batch` is the
# one that matters: without it the client never enters a playback batch, and
# everything qtpyrc suppresses during playback is unreachable from a test.
OFFERED_CAPS = ("batch", "server-time")

# Fake users in the test environment
FAKE_USERS = {
    "alice":   "alice!alice@alice.test.local",
    "bob":     "bob!bob@bob.test.local",
    "chanbot": "chanbot!bot@services.test.local",
}


class IRCTestServer:
    def __init__(self, port=6699, control_port=6700):
        self.port = port
        self.control_port = control_port
        self.clients = {}  # nick -> (reader, writer)
        self.client_nick = None  # the qtpyrc client's nick
        self._writer = None
        self._reader = None
        self._running = True
        self._registered = asyncio.Event()
        # Every line the client has sent, in order.  A test can then assert on
        # what actually went out on the wire rather than on the client's own
        # account of it -- which is the only way to catch a message being sent
        # twice, since the second copy usually leaves no trace on the client
        # side.  Read it with the RECEIVED control command.
        self.received = []
        # Channels this server refuses to let the client into: lowercase name ->
        # (numeric, text).  A channel you *cannot* join behaves very differently
        # from one you can -- no JOIN echo ever comes back, so none of the
        # client's join bookkeeping is cleaned up -- and several qtpyrc bugs
        # live only on that path.  Set with the REJECT control command, before
        # the client connects if it is to apply to an autojoin.
        self.rejected = {}
        # Reference of the batch currently open, or None.  While one is open
        # every line sent to the client carries `@batch=<ref>` -- see
        # _send_from.  A bouncer replaying your backlog is the ordinary way a
        # client meets a batch, and code that runs "once per incoming message"
        # behaves differently inside one (history saves and log writes are
        # suppressed, notifications are not fired), so a server that can never
        # open one cannot test any of it.
        self._batch_ref = None

    # ------------------------------------------------------------------
    # Sending helpers
    # ------------------------------------------------------------------

    def _send(self, line):
        """Send a raw IRC line to the connected client."""
        if self._writer and not self._writer.is_closing():
            self._writer.write((line + "\r\n").encode("utf-8"))

    def _send_from(self, prefix, command, *params):
        """Send a prefixed IRC message.

        While a batch is open every message is tagged as part of it, except the
        BATCH delimiters themselves -- which is what a real server does, and is
        why the rule lives here rather than in each control command.
        """
        parts = [":%s" % prefix, command]
        if self._batch_ref and command != "BATCH":
            parts.insert(0, "@batch=%s" % self._batch_ref)
        for i, p in enumerate(params):
            if i == len(params) - 1 and (" " in p or p.startswith(":")):
                parts.append(":%s" % p)
            else:
                parts.append(p)
        self._send(" ".join(parts))

    def _send_numeric(self, num, *params):
        """Send a numeric reply to the client."""
        nick = self.client_nick or "*"
        all_params = [nick] + list(params)
        self._send_from(SERVER_NAME, str(num).zfill(3), *all_params)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def _handle_client(self, reader, writer):
        self._reader = reader
        self._writer = writer
        nick = None
        user_done = False

        try:
            while self._running:
                data = await reader.readline()
                if not data:
                    break
                line = data.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                self.received.append(line)

                parts = line.split()
                cmd = parts[0].upper()

                if cmd == "CAP":
                    # Offer the capabilities qtpyrc asks for and ACK them, the
                    # way a real server does.  This used to advertise nothing
                    # and NAK everything, which meant `batch` was never
                    # negotiated -- so no test could ever put the client inside
                    # a playback batch, and every "suppressed during playback"
                    # rule in irc_client.py was untestable.
                    if len(parts) >= 2:
                        sub = parts[1].upper()
                        if sub == "LS":
                            self._send_from(SERVER_NAME, "CAP", nick or "*",
                                            "LS", " ".join(OFFERED_CAPS))
                        elif sub == "REQ":
                            asked = " ".join(parts[2:]).lstrip(":").split()
                            ok = [c for c in asked if c in OFFERED_CAPS]
                            bad = [c for c in asked if c not in OFFERED_CAPS]
                            if ok:
                                self._send_from(SERVER_NAME, "CAP", nick or "*",
                                                "ACK", " ".join(ok))
                            if bad:
                                self._send_from(SERVER_NAME, "CAP", nick or "*",
                                                "NAK", " ".join(bad))
                        elif sub == "END":
                            pass  # nothing to do

                elif cmd == "NICK":
                    nick = parts[1] if len(parts) > 1 else nick
                    if nick and user_done:
                        self.client_nick = nick
                        self._do_registration(nick)

                elif cmd == "USER":
                    user_done = True
                    if nick:
                        self.client_nick = nick
                        self._do_registration(nick)

                elif cmd == "PING":
                    token = parts[1] if len(parts) > 1 else SERVER_NAME
                    self._send_from(SERVER_NAME, "PONG", SERVER_NAME, token)

                elif cmd == "JOIN":
                    channel = parts[1] if len(parts) > 1 else "#test"
                    reject = self.rejected.get(channel.lower())
                    if reject:
                        num, text = reject
                        self._send_numeric(num, channel, text)
                    else:
                        self._do_client_join(nick, channel)

                elif cmd == "PART":
                    channel = parts[1] if len(parts) > 1 else "#test"
                    reason = _trailing(line, 2)
                    self._send_from("%s!test@test.local" % nick, "PART", channel, reason or "Leaving")

                elif cmd == "QUIT":
                    reason = _trailing(line, 1)
                    self._send("ERROR :Closing link: %s (%s)" % (nick, reason or "Quit"))
                    break

                elif cmd == "PRIVMSG":
                    # Client sending a message — echo back if needed
                    pass

                elif cmd == "MODE":
                    # Respond to MODE #channel with empty modes
                    if len(parts) >= 2:
                        target = parts[1]
                        if target.startswith("#"):
                            self._send_numeric(324, target, "+nt")
                            self._send_numeric(329, target, str(int(time.time())))

                elif cmd == "WHO":
                    if len(parts) >= 2:
                        self._send_numeric(315, parts[1], "End of /WHO list.")

                elif cmd == "WHOIS":
                    pass  # ignore

                elif cmd == "USERHOST":
                    pass  # ignore

                else:
                    pass  # ignore unknown from client

        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            self._writer = None
            self._reader = None

    def _do_registration(self, nick):
        """Send the welcome burst to the client."""
        self._send_numeric(1, "Welcome to the %s IRC Network %s!test@test.local" % (NETWORK_NAME, nick))
        self._send_numeric(2, "Your host is %s, running version %s" % (SERVER_NAME, SERVER_VERSION))
        self._send_numeric(3, "This server was created just now")
        self._send_numeric(4, SERVER_NAME, SERVER_VERSION, "iowghraAsORTVSxNCWqBzvdHtGpI", "lvhopsmntikrRcaqOALQbSeIKVfMCuzNTGjZ")
        # ISUPPORT, deliberately split across two 005 lines.  Real servers do
        # this -- the token list does not fit in 512 bytes -- and a client that
        # treats `isupport()` as a once-per-connection hook does everything in
        # it twice.  Sending one tidy 005 here hid exactly that bug (autojoin
        # and the NickServ password were both sent per-005) for as long as this
        # server was better behaved than the real thing.
        self._send_numeric(5, "CHANTYPES=#", "PREFIX=(ohv)@%%+",
                           "CHANMODES=eIbq,k,flj,CFLMPQScgimnprstuz",
                           "are supported by this server")
        self._send_numeric(5, "NETWORK=%s" % NETWORK_NAME, "CASEMAPPING=rfc1459",
                           "NICKLEN=30", "CHANNELLEN=50", "TOPICLEN=390",
                           "are supported by this server")
        # MOTD
        self._send_numeric(375, "- %s Message of the day -" % SERVER_NAME)
        self._send_numeric(372, "- Welcome to the test IRC server.")
        self._send_numeric(372, "- This server exists for automated testing.")
        self._send_numeric(376, "End of /MOTD command.")
        self._registered.set()

    def _do_client_join(self, nick, channel):
        """Handle the client joining a channel."""
        # Send JOIN echo
        self._send_from("%s!test@test.local" % nick, "JOIN", channel)
        # Topic
        self._send_numeric(332, channel, "Welcome to the test channel")
        self._send_numeric(333, channel, "chanbot!bot@services.test.local", str(int(time.time())))
        # NAMES — include the client plus two fake users
        self._send_numeric(353, "=", channel, "@%s alice bob" % nick)
        self._send_numeric(366, channel, "End of /NAMES list.")

    # ------------------------------------------------------------------
    # Control socket — test runner triggers events here
    # ------------------------------------------------------------------

    async def _handle_control(self, reader, writer):
        try:
            while self._running:
                data = await reader.readline()
                if not data:
                    break
                line = data.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    resp = self._process_control(line)
                    writer.write(("OK %s\r\n" % resp).encode())
                except Exception as e:
                    writer.write(("ERR %s\r\n" % e).encode())
                await writer.drain()
                # Drain the IRC writer too so data is actually sent
                if self._writer and not self._writer.is_closing():
                    await self._writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    def _process_control(self, line):
        """Process a control command and send the corresponding IRC event."""
        parts = line.split(None, 1)
        cmd = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""

        if not self._writer or self._writer.is_closing():
            # RECEIVED is a question about the past, so it stays answerable
            # after the client has gone -- which is when a test asks it.
            # REJECT configures the server, and is most useful *before* a
            # client connects, so that it applies to the autojoin burst.
            if cmd not in ("SHUTDOWN", "WAIT", "RECEIVED", "REJECT"):
                return "no client connected"

        client_nick = self.client_nick or "*"

        if cmd == "REJECT":
            # REJECT <channel> [numeric] [text]
            bits = rest.split(None, 2)
            if not bits:
                raise ValueError("REJECT needs a channel")
            chan = bits[0]
            num = int(bits[1]) if len(bits) > 1 else 474
            text = bits[2] if len(bits) > 2 else "Cannot join channel (+b)"
            self.rejected[chan.lower()] = (num, text)
            return "will reject %s with %d" % (chan, num)

        if cmd == "RECEIVED":
            # RECEIVED             -> every line the client has sent
            # RECEIVED <COMMAND>   -> only those whose first word matches
            # Answered as one line with the entries separated by " | " so it
            # fits the control protocol's one-line "OK <text>" reply.
            lines = self.received
            if rest:
                want = rest.strip().upper()
                lines = [l for l in lines
                         if l.split(None, 1)[0].upper() == want]
            return " | ".join(lines)

        if cmd == "SHUTDOWN":
            self._running = False
            if self._writer:
                self._send("ERROR :Server shutting down")
                self._writer.close()
            return "shutting down"

        if cmd == "DISCONNECT":
            if self._writer:
                self._send("ERROR :Connection closed by test")
                self._writer.close()
                self._writer = None
            return "disconnected"

        if cmd == "CHANMSG":
            src, channel, text = rest.split(None, 2)
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "PRIVMSG", channel, text)
            return "chanmsg sent"

        if cmd == "PRIVMSG":
            src, text = rest.split(None, 1)
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "PRIVMSG", client_nick, text)
            return "privmsg sent"

        if cmd == "ACTION":
            src, channel, text = rest.split(None, 2)
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "PRIVMSG", channel, "\x01ACTION %s\x01" % text)
            return "action sent"

        if cmd == "NOTICE":
            src, channel, text = rest.split(None, 2)
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "NOTICE", channel, text)
            return "notice sent"

        if cmd == "SERVERNOTICE":
            # A NOTICE straight from the server: the prefix is a server name,
            # so it has no nick!ident@host to attribute it to.  Deliberately a
            # separate command from NOTICE, because that difference is the
            # whole reason the two are filed in different log files.
            self._send_from(SERVER_NAME, "NOTICE", client_nick, rest)
            return "server notice sent"

        if cmd == "BATCH":
            # BATCH <ref> <type> [params...] -- open a batch.  Everything sent
            # until ENDBATCH is tagged as part of it.
            args = rest.split()
            if not args:
                raise ValueError("BATCH needs a reference")
            ref = args[0]
            btype = args[1] if len(args) > 1 else "znc.in/playback"
            self._send_from(SERVER_NAME, "BATCH", "+%s" % ref, btype, *args[2:])
            self._batch_ref = ref
            return "batch %s opened" % ref

        if cmd == "ENDBATCH":
            ref = rest.strip() or self._batch_ref
            if not ref:
                raise ValueError("no batch is open")
            self._batch_ref = None
            self._send_from(SERVER_NAME, "BATCH", "-%s" % ref)
            return "batch %s closed" % ref

        if cmd == "JOIN":
            src, channel = rest.split(None, 1)
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "JOIN", channel)
            return "join sent"

        if cmd == "PART":
            args = rest.split(None, 2)
            src = args[0]
            channel = args[1] if len(args) > 1 else "#test"
            reason = args[2] if len(args) > 2 else "Leaving"
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "PART", channel, reason)
            return "part sent"

        if cmd == "QUIT":
            args = rest.split(None, 1)
            src = args[0]
            reason = args[1] if len(args) > 1 else "Quit"
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "QUIT", reason)
            return "quit sent"

        if cmd == "KICK":
            args = rest.split(None, 3)
            kicker = args[0]
            channel = args[1] if len(args) > 1 else "#test"
            target = args[2] if len(args) > 2 else "bob"
            reason = args[3] if len(args) > 3 else "Kicked"
            prefix = FAKE_USERS.get(kicker, "%s!%s@test.local" % (kicker, kicker))
            self._send_from(prefix, "KICK", channel, target, reason)
            return "kick sent"

        if cmd == "NICK":
            old, new = rest.split(None, 1)
            prefix = FAKE_USERS.get(old, "%s!%s@test.local" % (old, old))
            self._send_from(prefix, "NICK", new)
            # Update our fake user mapping
            if old in FAKE_USERS:
                hostmask = FAKE_USERS.pop(old)
                FAKE_USERS[new] = hostmask.replace(old, new, 1)
            return "nick sent"

        if cmd == "TOPIC":
            src, channel, text = rest.split(None, 2)
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "TOPIC", channel, text)
            return "topic sent"

        if cmd == "MODE":
            args = rest.split(None, 3)
            src = args[0]
            channel = args[1] if len(args) > 1 else "#test"
            modes = args[2] if len(args) > 2 else "+o"
            mode_args = args[3] if len(args) > 3 else ""
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            if mode_args:
                self._send_from(prefix, "MODE", channel, modes, mode_args)
            else:
                self._send_from(prefix, "MODE", channel, modes)
            return "mode sent"

        if cmd == "INVITE":
            src, channel = rest.split(None, 1)
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "INVITE", client_nick, channel)
            return "invite sent"

        if cmd == "CTCPREPLY":
            args = rest.split(None, 2)
            src = args[0]
            tag = args[1] if len(args) > 1 else "VERSION"
            data = args[2] if len(args) > 2 else "test-reply"
            prefix = FAKE_USERS.get(src, "%s!%s@test.local" % (src, src))
            self._send_from(prefix, "NOTICE", client_nick,
                            "\x01%s %s\x01" % (tag, data))
            return "ctcpreply sent"

        if cmd == "RAWCMD":
            # Send an arbitrary unknown command: RAWCMD <prefix> <command> <params...>
            args = rest.split(None, 2)
            prefix = args[0]
            command = args[1] if len(args) > 1 else "FOOBAR"
            params = args[2] if len(args) > 2 else ""
            self._send_from(prefix, command, client_nick, params)
            return "rawcmd sent"

        if cmd == "NUMERIC":
            # Send a raw numeric: NUMERIC <num> <trailing text>
            args = rest.split(None, 1)
            num = args[0]
            text = args[1] if len(args) > 1 else "test numeric"
            self._send_numeric(int(num), text)
            return "numeric sent"

        if cmd == "KICKME":
            # Kick the client from a channel
            args = rest.split(None, 2)
            kicker = args[0]
            channel = args[1] if len(args) > 1 else "#test"
            reason = args[2] if len(args) > 2 else "You have been kicked"
            prefix = FAKE_USERS.get(kicker, "%s!%s@test.local" % (kicker, kicker))
            self._send_from(prefix, "KICK", channel, client_nick, reason)
            return "kickme sent"

        if cmd == "PARTCLIENT":
            # Simulate the client leaving a channel by echoing PART from client's nick
            channel = rest.strip() or "#test"
            self._send_from("%s!test@test.local" % client_nick, "PART", channel, "Test part")
            return "partclient sent"

        if cmd == "WAIT":
            return "ok"

        return "unknown command: %s" % cmd

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        irc_server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", self.port)
        ctrl_server = await asyncio.start_server(
            self._handle_control, "127.0.0.1", self.control_port)
        print("IRC server on 127.0.0.1:%d, control on 127.0.0.1:%d" %
              (self.port, self.control_port), flush=True)
        try:
            while self._running:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        irc_server.close()
        ctrl_server.close()


def _trailing(line, skip):
    """Extract trailing parameter from an IRC line after *skip* space-separated tokens."""
    idx = 0
    for _ in range(skip):
        idx = line.find(" ", idx)
        if idx == -1:
            return ""
        idx += 1
    rest = line[idx:]
    return rest.lstrip(":") if rest.startswith(":") else rest


async def main():
    port = 6699
    cport = 6700
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])
        elif arg == "--control-port" and i < len(sys.argv) - 1:
            cport = int(sys.argv[i + 1])
    server = IRCTestServer(port=port, control_port=cport)
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())
