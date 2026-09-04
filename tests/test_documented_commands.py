"""Every command the documentation uses must exist.

`/mode` was documented for a long time and never implemented. It appeared in
three places in `docs/reference.md` -- the `Kick+Ban` popup example, the
kick-ban `/on` example, and the `plugin.irc.on` example -- and since
`docommand` has no raw pass-through (an unrecognised name gets
"[Unknown command: /%s]"), anyone who copied those examples got a line that did
nothing at all.

Nothing could have caught that except a check of this shape, because the
failure is an *absence*: there is no code to review, no test to fail, and the
documentation is the only place the command is mentioned. So this walks the
reference the way a reader does -- every `/word` in it -- and asks whether the
client would find something to run.

Deliberately not restricted to the command tables. `/mode` was never in one;
it only ever appeared inside examples, which is exactly the case that needs
catching. That means a handful of `/word`-shaped strings are not commands at
all, and each one is listed below with the reason it is not, rather than
silently skipped -- an allowlist nobody has to justify is how the next real
one gets waved through.

Usage:
  python tests/test_documented_commands.py    # from the qtpyrc root directory
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

REFERENCE = os.path.join(ROOT, 'docs', 'reference.md')

# Not commands. Each needs a reason, not just a name.
NOT_COMMANDS = {
    # "Register `/name`" in the plugin API section: a placeholder for whatever
    # name the plugin chooses, not a command called "name".
    'name': 'placeholder in the add_command docs',
    # Registered at runtime by plugins/nowplaying.py via irc.add_command, so it
    # is never an attribute of Commands. If the plugin API is ever documented
    # as providing built-ins, this entry stops being true.
    'np': 'a plugin command (plugins/nowplaying.py), not a built-in',
    # From "/regex/" -- the highlight pattern syntax, where the slashes are
    # delimiters rather than a command prefix.
    'regex': 'the /regex/ highlight pattern syntax, not a command',
}

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


def documented_commands(text):
  """Every /word the reference mentions, with the lines it appears on."""
  found = {}
  patterns = (
      re.compile(r'`/([A-Za-z_][A-Za-z0-9_]*)'),      # `/mode` in prose/tables
      re.compile(r'^\s*/([A-Za-z_][A-Za-z0-9_]*)', re.M),   # a fenced example
      re.compile(r'\|\s*/([A-Za-z_][A-Za-z0-9_]*)'),  # "Kick+Ban:/mode ... | /kick"
  )
  for pat in patterns:
    for m in pat.finditer(text):
      found.setdefault(m.group(1).lower(), set())
  for i, line in enumerate(text.split('\n'), 1):
    for name in found:
      if '/' + name in line.lower():
        found[name].add(i)
  return found


def main():
  text = io.open(REFERENCE, encoding='utf-8').read()
  found = documented_commands(text)
  check(len(found) > 50,
        'only %d commands were found in the reference -- the extraction is '
        'broken, so this test proves nothing' % len(found))

  from commands import Commands
  missing = []
  for name, lines in sorted(found.items()):
    if name in NOT_COMMANDS:
      continue
    # docommand rewrites 'exec' to 'exec_' before the lookup, so the reference
    # spelling and the attribute name differ for exactly this one.
    attr = 'exec_' if name == 'exec' else name
    if not hasattr(Commands, attr):
      where = ', '.join('line %d' % n for n in sorted(lines)[:3])
      missing.append('/%s (%s)' % (name, where or 'unknown line'))

  check(not missing,
        'the reference documents %d command(s) that do not exist, so anyone '
        'following it gets "[Unknown command]": %s'
        % (len(missing), '; '.join(missing)))

  # The allowlist must stay honest in the other direction too: an entry that
  # names a command which *does* exist is stale, and a stale entry is how a
  # future removal goes unnoticed.
  from commands import Commands as C
  stale = [n for n in NOT_COMMANDS if hasattr(C, 'exec_' if n == 'exec' else n)]
  check(not stale,
        'NOT_COMMANDS lists %r, but they exist on Commands -- remove them, or '
        'the next command that disappears will be hidden by this list' % stale)

  # ...and an allowlist entry for something the reference no longer mentions is
  # equally stale.
  unused = [n for n in NOT_COMMANDS if n not in found]
  check(not unused,
        'NOT_COMMANDS lists %r, which the reference does not mention any more'
        % unused)

  if failures:
    print('FAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('all %d commands the reference uses exist (%d documented non-commands '
        'accounted for).' % (len(found) - len(NOT_COMMANDS), len(NOT_COMMANDS)))
  return 0


if __name__ == '__main__':
  sys.exit(main())
