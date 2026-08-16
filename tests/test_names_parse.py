"""Unit tests for parse_names_token (RPL_NAMREPLY / 353 token parsing).

Covers multi-prefix and userhost-in-names, including the Ergo case where a
mode-prefixed token carries a full "nick!ident@host" hostmask.

Usage:
  python tests/test_names_parse.py      # from the qtpyrc root directory
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from irc_client import parse_names_token as p


CASES = [
    # (token, prefix_symbols, expected)
    ("alice",                 "@%+",   ("",   "alice", None,   None)),
    ("@bob",                  "@%+",   ("@",  "bob",   None,   None)),
    ("@+carol",               "~&@%+", ("@+", "carol", None,   None)),
    # userhost-in-names, no mode prefix
    ("dave!~did@host.example","@%+",   ("",   "dave",  "~did", "host.example")),
    # userhost-in-names WITH a mode prefix -- the reported Ergo bug
    ("@erin!eid@1.2.3.4",     "@%+",   ("@",  "erin",  "eid",  "1.2.3.4")),
    # owner '~' as a real mode prefix must not be confused with a '~' ident
    ("~frank!~fid@h",         "~&@%+", ("~",  "frank", "~fid", "h")),
    # '~' NOT in the server's prefix set -> leading '~' belongs to the nick/ident
    ("grace!~gid@h",          "@%+",   ("",   "grace", "~gid", "h")),
    # malformed: ident present, host missing
    ("heidi!hid",             "@%+",   ("",   "heidi", "hid",  None)),
    ("",                      "@%+",   ("",   "",      None,   None)),
]


def main():
    failures = 0
    for token, syms, expected in CASES:
        got = p(token, syms)
        ok = got == expected
        if not ok:
            failures += 1
            print("FAIL  token=%r syms=%r\n   expected %r\n   got      %r"
                  % (token, syms, expected, got))
        else:
            print("ok    %r -> %r" % (token, got))
    if failures:
        print("\n%d test(s) FAILED" % failures)
        sys.exit(1)
    print("\nAll %d parse_names_token tests passed" % len(CASES))


if __name__ == "__main__":
    main()
