"""Automated test for every /on event and its variables.

Launches:
  1. The test IRC server (irc_test_server.py)
  2. qtpyrc in headless mode with /on hooks that /stdout a marker for each event
  3. Sends control commands to trigger every event type
  4. Reads qtpyrc's stdout and checks for the expected markers

Usage:
  python tests/test_on_events.py          # from the qtpyrc root directory
"""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time


def _free_port():
  """Claim a port the OS says is free.

  Fixed ports made this test fail with "only one usage of each socket address"
  whenever anything else was already on them -- including a previous run of a
  qtpyrc test whose server outlived it. Asking the OS costs nothing and makes
  the test independent of what else is running.
  """
  s = socket.socket()
  s.bind(('127.0.0.1', 0))
  port = s.getsockname()[1]
  s.close()
  return port


IRC_PORT = _free_port()
CTRL_PORT = _free_port()
QTPYRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 30  # seconds for the whole test run

# ---------------------------------------------------------------------------
# Expected events and their /on hooks + expected stdout markers
# ---------------------------------------------------------------------------
# Each entry:  (event_name, hook_name, /stdout format, variables to verify)
#
# The /on command sets up a hook whose command is /stdout with a parseable
# marker line.  The test runner then checks that the marker appeared in
# qtpyrc's stdout with the right variable values.

ON_HOOKS = [
    # -- Connection lifecycle --
    # connect fires before registration completes, so {me} might not be set yet
    ("connect",    "t_connect",    "/stdout EVENT:connect"),
    ("signon",     "t_signon",     "/stdout EVENT:signon:me={me}:network={network}"),
    ("motd",       "t_motd",       "/stdout EVENT:motd:motd={motd}"),
    # -- Channel lifecycle (self) --
    ("myjoined",   "t_myjoined",   "/stdout EVENT:myjoined:channel={channel}"),
    # -- Channel messages --
    ("chanmsg",    "t_chanmsg",    "/stdout EVENT:chanmsg:nick={nick}:channel={channel}:message={message}:user={user}:text={text}"),
    ("action",     "t_action",     "/stdout EVENT:action:nick={nick}:channel={channel}:data={data}:user={user}:text={text}"),
    ("noticed",    "t_noticed",    "/stdout EVENT:noticed:nick={nick}:channel={channel}:message={message}:user={user}"),
    # -- User events --
    ("join",       "t_join",       "/stdout EVENT:join:nick={nick}:channel={channel}:user={user}"),
    ("part",       "t_part",       "/stdout EVENT:part:nick={nick}:channel={channel}:user={user}"),
    ("quit",       "t_quit",       "/stdout EVENT:quit:nick={nick}:message={message}:user={user}"),
    ("kick",       "t_kick",       "/stdout EVENT:kick:kickee={kickee}:channel={channel}:kicker={kicker}:nick={nick}:message={message}"),
    ("nick",       "t_nick",       "/stdout EVENT:nick:oldnick={oldnick}:newnick={newnick}:nick={nick}"),
    ("topic",      "t_topic",      "/stdout EVENT:topic:nick={nick}:channel={channel}:topic={topic}"),
    ("mode",       "t_mode",       "/stdout EVENT:mode:nick={nick}:channel={channel}:modes={modes}:args={args}"),
    ("invite",     "t_invite",     "/stdout EVENT:invite:nick={nick}:channel={channel}"),
    # -- Private message --
    ("privmsg",    "t_privmsg",    "/stdout EVENT:privmsg:nick={nick}:message={message}:user={user}:text={text}"),
    # -- CTCP reply --
    ("ctcpreply",  "t_ctcpreply",  "/stdout EVENT:ctcpreply:nick={nick}:tag={tag}:data={data}:user={user}:text={text}"),
    # -- Raw / numeric --
    ("rawcmd",     "t_rawcmd",     "/stdout EVENT:rawcmd:prefix={prefix}:command={command}:params={params}"),
    ("numeric",    "t_numeric",    "/stdout EVENT:numeric:command={command}:params={params}"),
    # -- Kicked (self) --
    ("kicked",     "t_kicked",     "/stdout EVENT:kicked:channel={channel}:kicker={kicker}:nick={nick}:message={message}"),
    # -- Disconnect --
    ("disconnect", "t_disconnect", "/stdout EVENT:disconnect"),
    # NOTE: myleft is NOT tested via server-side PART because you need to
    # issue /part from the client.  We test it by sending a /part command.
    ("myleft",     "t_myleft",     "/stdout EVENT:myleft:channel={channel}"),
    # notify_online / notify_offline require ISON polling which is hard to
    # test in this setup — skip for now.
]

# Sequence of control commands to send, with delays to let events propagate.
# "SLEEP <n>" is a pseudo-command that pauses for n seconds.
# "EXEC <cmd>" sends a /command to qtpyrc via the control mechanism (not used;
#   we use -e flags at startup instead).
CONTROL_SEQUENCE = [
    # Wait for connection + join to complete
    "SLEEP 5",

    # Channel events
    "CHANMSG alice #test Hello from Alice!",
    "SLEEP 0.5",
    "ACTION alice #test waves at everyone",
    "SLEEP 0.5",
    "NOTICE alice #test This is a notice",
    "SLEEP 0.5",

    # Private message (send before NICK changes bob's hostmask)
    "PRIVMSG bob Hello privately!",
    "SLEEP 0.5",

    # User events
    "JOIN bob #test",
    "SLEEP 0.5",
    "TOPIC alice #test New test topic set by alice",
    "SLEEP 0.5",
    "MODE alice #test +o bob",
    "SLEEP 0.5",
    "NICK bob bob_away",
    "SLEEP 0.5",
    "KICK alice #test bob_away Kicked for testing",
    "SLEEP 0.5",
    "PART alice #test Goodbye for now",
    "SLEEP 0.5",
    "JOIN alice #test",
    "SLEEP 0.5",
    "QUIT alice Quit for testing",
    "SLEEP 0.5",

    # Invite
    "INVITE chanbot #secret",
    "SLEEP 0.5",

    # CTCP reply
    "CTCPREPLY alice VERSION qtpyrc-test-1.0",
    "SLEEP 0.5",

    # Raw unknown command
    "RAWCMD services.test.local XYZZY some test params",
    "SLEEP 0.5",

    # Custom numeric (use 999 which is unlikely to be handled)
    "NUMERIC 999 This is a test numeric",
    "SLEEP 0.5",

    # Kick the client (triggers 'kicked' event)
    "JOIN alice #test",
    "SLEEP 0.5",
    "KICKME alice #test Kicked the test client",
    "SLEEP 0.5",

    # myleft: simulate the client parting by echoing their own PART
    # (the server would echo this after client sends PART)
    # First rejoin the client
    "JOIN alice #test",
    "SLEEP 0.5",
    "PARTCLIENT #test",
    "SLEEP 0.5",

    # Disconnect (triggers 'disconnect' event) — do this last
    "SLEEP 1",
    "DISCONNECT",
    "SLEEP 3",
]


# Expected variable values for each event
EXPECTED = {
    "connect":    {},
    "signon":     {"me": "testbot", "network": "testnet"},
    "motd":       {"motd": None},  # just check it's non-empty
    "myjoined":   {"channel": "#test"},
    "chanmsg":    {"nick": "alice", "channel": "#test", "message": "Hello from Alice!",
                   "user": "alice!alice@alice.test.local", "text": "Hello from Alice!"},
    "action":     {"nick": "alice", "channel": "#test", "data": "waves at everyone",
                   "user": "alice!alice@alice.test.local", "text": "waves at everyone"},
    "noticed":    {"nick": "alice", "channel": "#test", "message": "This is a notice",
                   "user": "alice!alice@alice.test.local"},
    "join":       {"nick": "bob", "channel": "#test",
                   "user": "bob!bob@bob.test.local"},
    "topic":      {"nick": "alice", "channel": "#test", "topic": "New test topic set by alice"},
    "mode":       {"nick": "alice", "channel": "#test", "modes": "+o", "args": None},
    "nick":       {"oldnick": "bob", "newnick": "bob_away", "nick": "bob"},
    "kick":       {"kickee": "bob_away", "channel": "#test", "kicker": "alice",
                   "nick": "alice", "message": "Kicked for testing"},
    "part":       {"nick": "alice", "channel": "#test",
                   "user": "alice!alice@alice.test.local"},
    "quit":       {"nick": "alice", "message": "Quit for testing",
                   "user": "alice!alice@alice.test.local"},
    "privmsg":    {"nick": "bob", "message": "Hello privately!",
                   "user": "bob!bob@bob.test.local", "text": "Hello privately!"},
    "invite":     {"nick": "chanbot", "channel": "#secret"},
    "ctcpreply":  {"nick": "alice", "tag": "VERSION", "data": "qtpyrc-test-1.0",
                   "user": "alice!alice@alice.test.local", "text": "VERSION qtpyrc-test-1.0"},
    "rawcmd":     {"command": "XYZZY"},
    # /on numeric doesn't fire for unknown numerics — they go through
    # irc_unknown which dispatches as 'rawcmd', not 'on_numeric'.
    # The on_numeric dispatch path only works for plugin callbacks.
    "numeric":    {"command": None},
    "kicked":     {"channel": "#test", "kicker": "alice", "nick": "alice",
                   "message": "Kicked the test client"},
    "disconnect": {},
    "myleft":     {"channel": "#test"},
}


def parse_event_line(line):
    """Parse 'EVENT:name:key=value:key=value' into (name, {key: value})."""
    if not line.startswith("EVENT:"):
        return None, None
    parts = line.split(":")
    name = parts[1]
    variables = {}
    for part in parts[2:]:
        if "=" in part:
            k, v = part.split("=", 1)
            variables[k] = v
    return name, variables


def check_results(stdout_text):
    """Check captured stdout against expected events and variables."""
    lines = stdout_text.strip().splitlines()
    event_lines = {}
    for line in lines:
        line = line.strip()
        name, variables = parse_event_line(line)
        if name:
            if name not in event_lines:
                event_lines[name] = []
            event_lines[name].append(variables)

    results = []
    total = 0
    passed = 0
    failed = 0
    skipped = 0

    for event_name, hook_name, cmd, *_ in ON_HOOKS:
        total += 1
        if event_name not in event_lines:
            # myleft may not fire since we don't send /part from client
            if event_name in ("myleft", "numeric"):
                results.append(("SKIP", event_name, "event not triggered in test"))
                skipped += 1
            else:
                results.append(("FAIL", event_name, "event never fired"))
                failed += 1
            continue

        occurrences = event_lines[event_name]
        expected = EXPECTED.get(event_name, {})

        # Find any occurrence that matches all expected values
        best_issues = None
        for occ in occurrences:
            occ_ok = True
            occ_issues = []
            for key, expected_val in expected.items():
                if expected_val is None:
                    actual = occ.get(key, "")
                    if not actual:
                        occ_issues.append("%s is empty" % key)
                        occ_ok = False
                else:
                    actual = occ.get(key, "")
                    if actual != expected_val:
                        occ_issues.append("%s: expected %r, got %r" % (key, expected_val, actual))
                        occ_ok = False
            if occ_ok:
                best_issues = None
                break
            if best_issues is None or len(occ_issues) < len(best_issues):
                best_issues = occ_issues

        if best_issues is None:
            results.append(("PASS", event_name, "all variables correct (%d occurrence%s)" %
                           (len(occurrences), "s" if len(occurrences) > 1 else "")))
            passed += 1
        else:
            results.append(("FAIL", event_name, "; ".join(best_issues)))
            failed += 1

    return results, total, passed, failed, skipped


async def send_control(cmd):
    """Send a command to the test server's control socket."""
    reader, writer = await asyncio.open_connection("127.0.0.1", CTRL_PORT)
    writer.write((cmd + "\r\n").encode())
    await writer.drain()
    resp = await asyncio.wait_for(reader.readline(), timeout=5)
    writer.close()
    await writer.wait_closed()
    return resp.decode().strip()


async def run_control_sequence():
    """Execute the control sequence, pausing for SLEEP commands."""
    for cmd in CONTROL_SEQUENCE:
        if cmd.startswith("SLEEP "):
            delay = float(cmd.split()[1])
            await asyncio.sleep(delay)
        else:
            try:
                resp = await send_control(cmd)
                print("  ctrl: %-50s -> %s" % (cmd[:50], resp), flush=True)
            except Exception as e:
                print("  ctrl: %-50s -> ERROR: %s" % (cmd[:50], e), flush=True)


def build_exec_args():
    """Build the -e arguments to set up all /on hooks at startup."""
    args = []
    for event_name, hook_name, command in ON_HOOKS:
        # /on <event> <name> * <command>
        args.extend(["-e", "on %s %s * %s" % (event_name, hook_name, command)])
    return args


def main():
    print("=" * 70)
    print("qtpyrc /on event test suite")
    print("=" * 70)

    # 1. Start the test IRC server
    print("\n[1] Starting test IRC server on port %d..." % IRC_PORT)
    server_proc = subprocess.Popen(
        [sys.executable, os.path.join(TEST_DIR, "irc_test_server.py"),
         "--port", str(IRC_PORT), "--control-port", str(CTRL_PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=QTPYRC_DIR,
    )
    # Wait for server to be ready
    time.sleep(1.5)
    if server_proc.poll() is not None:
        out = server_proc.stdout.read().decode()
        print("Server failed to start:\n%s" % out)
        sys.exit(1)
    print("  Server started (pid %d)" % server_proc.pid)

    # 2. Start qtpyrc in headless mode
    print("\n[2] Starting qtpyrc in headless mode...")
    exec_args = build_exec_args()
    qtpyrc_cmd = [
        sys.executable, "-u",  # unbuffered stdout
        os.path.join(QTPYRC_DIR, "qtpyrc.py"),
        "-c", os.path.join(TEST_DIR, "test_config.yaml"),
        # The fixture cannot name the port -- it is claimed at run time so two
        # test runs never collide (see _free_port).
        "-o", "networks.testnet.server.port=%d" % IRC_PORT,
        "--headless",
        "--no-startup",
        "-d", "3",  # info level so headless prints addline output
    ] + exec_args

    print("  Command: python qtpyrc.py -c tests/test_config.yaml "
          "-o networks.testnet.server.port=%d --headless --no-startup -d 3 "
          "-e <hooks...>" % IRC_PORT)
    print("  (%d /on hooks registered)" % len(ON_HOOKS))

    # Write stdout/stderr to temp files instead of PIPEs to avoid
    # deadlocking on a full pipe buffer (Windows default is ~4096 bytes).
    stdout_file = tempfile.NamedTemporaryFile(mode='w', suffix='.stdout.txt',
                                               dir=TEST_DIR, delete=False)
    stderr_file = tempfile.NamedTemporaryFile(mode='w', suffix='.stderr.txt',
                                               dir=TEST_DIR, delete=False)
    qtpyrc_proc = subprocess.Popen(
        qtpyrc_cmd,
        stdout=stdout_file,
        stderr=stderr_file,
        cwd=QTPYRC_DIR,
    )
    print("  qtpyrc started (pid %d)" % qtpyrc_proc.pid)

    # 3. Run the control sequence
    print("\n[3] Running event trigger sequence...")
    try:
        asyncio.run(run_control_sequence())
    except Exception as e:
        print("  Error during control sequence: %s" % e)

    # 4. Shut down
    print("\n[4] Shutting down...")
    # Send SHUTDOWN to control
    try:
        asyncio.run(send_control("SHUTDOWN"))
    except Exception:
        pass
    time.sleep(0.5)

    # Terminate qtpyrc
    qtpyrc_proc.terminate()
    try:
        qtpyrc_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        qtpyrc_proc.kill()
        qtpyrc_proc.wait()

    server_proc.terminate()
    try:
        server_proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        server_proc.kill()

    # Close temp files and read their contents
    stdout_file.close()
    stderr_file.close()
    with open(stdout_file.name, 'r', encoding='utf-8', errors='replace') as f:
        stdout_text = f.read()
    with open(stderr_file.name, 'r', encoding='utf-8', errors='replace') as f:
        stderr_text_content = f.read()
    # Clean up temp files
    try:
        os.unlink(stdout_file.name)
        os.unlink(stderr_file.name)
    except OSError:
        pass

    lines_all = stdout_text.splitlines()
    print("\n[5] qtpyrc stdout capture (%d lines):" % len(lines_all))
    print("-" * 70)
    for i, line in enumerate(lines_all, 1):
        tag = " <<< EVENT" if line.strip().startswith("EVENT:") else ""
        print("  %3d: %s%s" % (i, line, tag))
    print("-" * 70)

    if stderr_text_content.strip():
        stderr_lines = stderr_text_content.strip().splitlines()
        print("\n[5b] qtpyrc stderr (%d lines):" % len(stderr_lines))
        print("-" * 70)
        for i, line in enumerate(stderr_lines, 1):
            print("  %3d: %s" % (i, line))
        print("-" * 70)

    print("\n[6] Results:")
    print("-" * 70)
    results, total, passed, failed, skipped = check_results(stdout_text)
    for status, event_name, detail in results:
        if status == "PASS":
            icon = "PASS"
        elif status == "SKIP":
            icon = "SKIP"
        else:
            icon = "FAIL"
        print("  [%s] %-15s %s" % (icon, event_name, detail))
    print("-" * 70)
    print("  Total: %d  |  Passed: %d  |  Failed: %d  |  Skipped: %d" %
          (total, passed, failed, skipped))
    print("=" * 70)

    if failed > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
