"""The auto-op list you are shown is the auto-op list that fires.

The report: "someone showed me in a log that qtpyrc auto-opped his nick, which
resulted in a channel takeover.  Yet /aop -l told me the list was empty.  And
/aop -r <nick> said it removed it, but it says that whether it's there or not."

Faults in the gap between what the auto-op *check* reads and what the list and
remove commands touch:

  1. `is_auto_op()` reads global + network + channel entries additively, but
     `/aop -l` called `get_auto_ops(network_key, channel)` -- context-sensitive
     -- and additionally honoured `-w`, which does **not** mean "all networks"
     but "the global scope only".  So `/aop -lw`, the obvious way to spell
     "show me everything", printed "[Auto-op list is empty]" from *every*
     window, the reporter's channel window included, while four entries were
     live.  (The reporter confirms they ran it in the channel window, not the
     server window; and note that plain `/aop -l` from a server window would
     have printed the network-scoped entry rather than "empty", so `-w` is the
     only spelling that produces the message they saw.)
  2. `/aop -r <mask>` wrote to one scope guessed from the current window.  From
     a server window that is the network scope, so it never touched the
     channel-scoped entry that was doing the opping.
  3. `_modify_list_entry` returned nothing and `/aop` printed "Removed"
     unconditionally, so (2) was indistinguishable from success.  It also had a
     silent do-nothing path when the network key was absent from the config.
  4. From the reporter's config: `auto_ops: ['-?']` at network scope.  The flag
     parser kept any `-x` whose letters were not all alphabetic as a positional
     argument, so `/aop -?` was read as "add the mask `-?`".  `?` is a wildcard,
     so the entry auto-opped every two-character nick beginning with `-`.
  5. A mask was matched as one flat string, so a component the user *omitted*
     behaved like one that was *impossible*: `hegemon@lakitu.undernet.org` was
     matched against `hegemon!~heg@lakitu.undernet.org` and could not match,
     because nothing absorbed the `!~heg`.  The entry named a person and opped
     nobody.  Conversely a nick-only entry -- `HEGEMON`, which is what the
     reporter's config actually held -- ops whoever holds that nick, from any
     host, which is the "hegemon@anything else" they were worried about.
  6. That flat match was `fnmatch`, which honours `[...]` character classes.
     `[` and `]` are legal (and conventional) IRC nick characters, so
     `/ignore bob[away]!*@*` matched `boba`, `bobw` and `boby` and not
     `bob[away]`.

This test drives the real config helpers and the real command bodies against a
fake window, and asserts the properties that matter: **for any entry at any
scope, `/aop -l` shows it and `/aop -r` removes it, from any window**, and **a
mask grants exactly what it names -- no more, and no less.**

  python tests/test_aop_list.py
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

CONFIG = """\
nick: tester
auto_ops:
- globalguy
networks:
  undernet:
    nick: tester
    channels:
      '#forum':
        auto_ops:
        - HEGEMON
        - '*@lakitu.users.undernet.org'
      '#other':
        auto_ops:
        - HEGEMON
    auto_ops:
    - netguy
    server:
      host: 127.0.0.1
      port: 6667
  efnet:
    nick: tester
    server:
      host: 127.0.0.1
      port: 6667
"""

tmpdir = tempfile.mkdtemp(prefix='qtpyrc-aop-')
cfg = os.path.join(tmpdir, 'config.yaml')
with open(cfg, 'w', encoding='utf-8') as f:
  f.write(CONFIG)

import state
import config as config_mod

state.config = config_mod.loadconfig(cfg)

import commands
from commands import Commands
from config import is_auto_op, list_all_entries

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


class FakeChannel:
  def __init__(self, name):
    self.name = name


class FakeClient:
  network_key = 'undernet'
  def __init__(self):
    self.channels = {}


class FakeWindow:
  """Just enough Window for the list commands: a type, a client, redmessage."""

  def __init__(self, wtype, channel=None):
    self.type = wtype
    self.client = FakeClient()
    self.channel = FakeChannel(channel) if channel else None
    self.lines = []

  def redmessage(self, text):
    self.lines.append(text)

  @property
  def out(self):
    return '\n'.join(self.lines)


def fresh(wtype, channel=None):
  return FakeWindow(wtype, channel)


# --- 1. The list shows every entry from every window, under every spelling ---
#
# This is the reported symptom.  `-lw` is the spelling that produced it: the
# reporter was in the channel window, where plain `-l` did work, and `-w`
# narrowed the list to the (empty) global scope rather than widening it.

ALL_MASKS = ['globalguy', 'netguy', 'HEGEMON', '*@lakitu.users.undernet.org']

for label, win in (('server window', fresh('server')),
                   ('query window', fresh('query')),
                   ('an unrelated channel window', fresh('channel', '#anxiety')),
                   ('the #forum window itself', fresh('channel', '#forum'))):
  for spelling in ('-l', '-lw', '-w', ''):
    win = fresh(win.type, win.channel.name if win.channel else None)
    Commands.aop(win, spelling)
    for mask in ALL_MASKS:
      check(mask in win.out,
            '/aop %r run from %s does not list %r -- an entry that can op '
            'someone must be visible from every window under every spelling, '
            'or an empty-looking list is used to conclude auto-op is not '
            'configured.\ngot:\n%s' % (spelling, label, mask, win.out))
    check('is empty' not in win.out.lower(),
          '/aop %r run from %s called the list empty' % (spelling, label))

# -w is inert in list mode, and says so rather than silently doing nothing --
# an answer to a question other than the one asked is the original fault.
win = fresh('channel', '#forum')
Commands.aop(win, '-lw')
check('-w does not narrow' in win.out,
      '/aop -lw ignored -w without saying so: %s' % win.out)

# The scope has to be named, or a visible entry still cannot be found and
# removed by hand.
win = fresh('server')
Commands.aop(win, '-l')
check('#forum' in win.out,
      '/aop -l does not say which channel a channel-scoped entry belongs to')
check('global' in win.out.lower(),
      '/aop -l does not label the global-scope entry')


# --- 2. The auto-op check and the list agree ---------------------------------
#
# The property, stated directly: anything the checker would op is in the list.

check(is_auto_op('HEGEMON!x@y', 'undernet', '#forum'),
      'test premise broken: HEGEMON should be auto-opped in #forum')
listed = [m for _nk, _ch, m in list_all_entries('auto_ops')]
check('HEGEMON' in listed,
      'is_auto_op() ops HEGEMON but list_all_entries() does not report him')


# --- 3. A mistyped flag is an error, never a new entry -----------------------
#
# This is how 'auto_ops: [-?]' got into the reporter's live config.

before = list_all_entries('auto_ops')
win = fresh('server')
Commands.aop(win, '-?')
after = list_all_entries('auto_ops')
check(before == after,
      '/aop -? changed the auto-op list. A mistyped flag must never be stored '
      'as a mask: this is how the literal entry %r reached a live config, '
      'where its wildcard opped every 2-character nick starting with "-".'
      % '-?')
check('unknown option' in win.out.lower(),
      '/aop -? did not report an unknown option; it said: %s' % win.out)

# -- ends the flags, so a value that really does start with '-' still works.
win = fresh('server')
Commands.aop(win, '-w -- -weirdnick')
check('-weirdnick' in [m for _n, _c, m in list_all_entries('auto_ops')],
      '/aop -w -- -weirdnick did not add the mask after --')
Commands.aop(fresh('server'), '-r -- -weirdnick')


# --- 4. Remove tells the truth, and reaches every scope ----------------------

# 4a. Removing something that is not there says so, every time.
for attempt in (1, 2, 3):
  win = fresh('server')
  Commands.aop(win, '-r nosuchperson')
  check('nothing removed' in win.out.lower(),
        'attempt %d of /aop -r nosuchperson reported success for a mask that '
        'is not in any list. Repeating the command must not keep claiming to '
        'remove it -- that is what made the list impossible to trust.\ngot: %s'
        % (attempt, win.out))
  check('Removed' not in win.out,
        'attempt %d of /aop -r nosuchperson said "Removed"' % attempt)

# The same for a remove aimed at an explicit scope, which takes the other code
# path -- one _modify_list_entry call whose result is reported.  This is the
# path that used to print "Removed" from a return value it never looked at.
for attempt in (1, 2, 3):
  win = fresh('server')
  Commands.aop(win, '-w -r nosuchperson')
  check('nothing removed' in win.out.lower(),
        'attempt %d of /aop -w -r nosuchperson claimed to remove a mask that '
        'is not in the global list.\ngot: %s' % (attempt, win.out))

# 4b. Removing from a server window reaches a channel-scoped entry -- the exact
#     thing that failed for the reporter.
win = fresh('server')
Commands.aop(win, '-r HEGEMON')
check('Removed' in win.out,
      '/aop -r HEGEMON from a server window did not remove the channel-scoped '
      'entry: %s' % win.out)
check(not is_auto_op('HEGEMON!x@y', 'undernet', '#forum'),
      'HEGEMON is still auto-opped in #forum after /aop -r HEGEMON reported '
      'that it had been removed -- this is the reported bug exactly')
check(not is_auto_op('HEGEMON!x@y', 'undernet', '#other'),
      'HEGEMON was removed from #forum but is still auto-opped in #other: a '
      'remove that leaves a live copy behind is the failure being fixed')
check('#forum' in win.out and '#other' in win.out,
      '/aop -r HEGEMON did not name both scopes it removed from: %s' % win.out)

# And a second attempt now correctly reports nothing.
win = fresh('server')
Commands.aop(win, '-r HEGEMON')
check('nothing removed' in win.out.lower(),
      'the second /aop -r HEGEMON still claimed to remove something: %s'
      % win.out)

# 4c. A scoped remove that misses still reports where the mask really is.
win = fresh('server')
Commands.aop(win, '-w netguy')          # global scope now also has netguy
win = fresh('server')
Commands.aop(win, '-w -r netguy')       # remove only the global one
check('still' in win.out.lower() and 'network undernet' in win.out.lower(),
      'a scoped /aop -r that leaves the mask live at another scope did not say '
      'so: %s' % win.out)


# --- 5. Adding reports what actually happened --------------------------------

win = fresh('server')
Commands.aop(win, 'netguy')             # already at network scope
check('already' in win.out.lower(),
      'adding a mask that is already listed reported it as newly added: %s'
      % win.out)

win = fresh('channel', '#forum')
Commands.aop(win, 'freshnick')
check('#forum' in win.out,
      'adding from a channel window did not say which scope it went to: %s'
      % win.out)
check(is_auto_op('freshnick!x@y', 'undernet', '#forum'),
      'a mask added from the #forum window is not auto-opped in #forum')


# --- 6. A mask that ops everyone is called out ------------------------------

win = fresh('channel', '#forum')
Commands.aop(win, '*!*@*')
check('WARNING' in win.out,
      "/aop '*!*@*' -- which ops every single person who joins -- was added "
      'without a warning: %s' % win.out)
Commands.aop(fresh('server'), '-r *!*@*')

# An ordinary broad-in-the-host mask is not a takeover and must not cry wolf.
win = fresh('channel', '#forum')
Commands.aop(win, 'bob!*@*')
check('WARNING' not in win.out,
      "/aop 'bob!*@*' is an ordinary mask and must not be warned about, or the "
      'warning stops being read: %s' % win.out)
Commands.aop(fresh('server'), '-r bob!*@*')


# --- 7. A mask grants exactly what it names ----------------------------------
#
# "Can you make sure that /aop hegemon@lakitu.undernet.org will not accidentally
# op hegemon@anything else?"  Two halves, and the old code got both wrong in
# opposite directions: the host-anchored mask matched nobody at all, and the
# bare nick the reporter actually had in their config matched hegemon anywhere.

MATCH_CASES = [
  # mask                            user                                     want
  ('hegemon@lakitu.undernet.org', 'hegemon!~heg@lakitu.undernet.org',       True,
   'a nick@host mask does not match the person it names. Omitting the ident '
   'must mean "any ident", not "impossible": matched flat, there is nothing in '
   'the pattern to absorb the "!~heg" and the entry is dead.'),
  ('hegemon@lakitu.undernet.org', 'hegemon!~heg@evil.example.com',          False,
   'a nick@host mask opped that nick from a DIFFERENT host -- this is the '
   '"hegemon@anything else" the reporter asked about'),
  ('hegemon@lakitu.undernet.org', 'hegemon!~heg@lakitu.users.undernet.org', False,
   'a nick@host mask matched a host that merely contains the named one as a '
   'substring; the host component must be anchored'),
  ('hegemon@lakitu.undernet.org', 'notheg!~x@lakitu.undernet.org',          False,
   'a nick@host mask opped a different nick on the right host'),
  ('hegemon!*@lakitu.undernet.org', 'hegemon!~heg@lakitu.undernet.org',     True,
   'the fully-spelled-out form of the same mask stopped matching'),
  ('hegemon!ident',              'hegemon!ident@anywhere',                  True,
   'a nick!ident mask with the host omitted matches nobody'),
  ('hegemon!ident',              'hegemon!other@anywhere',                  False,
   'a nick!ident mask ignored the ident it names'),
  # `x@y` is nick@host, not ident@host.  The short form is the one genuinely
  # ambiguous spelling -- the same text in a /whois line is the ident -- so the
  # reading is pinned here rather than left to whoever next edits split_mask.
  ('hegemon_@127.0.0.1',         'hegemon_!totallydifferent@127.0.0.1',     True,
   'a nick@host mask stopped matching when the ident differed from the nick. '
   'The ident is omitted, so it means "any ident"; requiring it to equal the '
   'nick would make the entry match almost nobody.'),
  ('hegemon_@127.0.0.1',         'someoneelse!hegemon_@127.0.0.1',          False,
   'a nick@host mask matched on the IDENT rather than the nick -- the part '
   'left of @ is the nick, and reading it as the ident would let a stranger '
   'holding that ident be opped'),
  # The nick-only entry from the reporter's real config.
  ('HEGEMON',                    'hegemon!~heg@lakitu.undernet.org',        True,
   'a nick-only entry stopped matching its own nick'),
  ('HEGEMON',                    'hegemon!~heg@somewhere.else.entirely',    True,
   'test premise: a nick-only entry matches from any host, which is exactly '
   'why adding one is warned about'),
  # Host-only, as in the reporter's config: ops anyone on that host.
  ('*@lakitu.users.undernet.org', 'anyone!~x@lakitu.users.undernet.org',    True,
   'a *@host mask stopped matching -- this is a live entry in a real config '
   'and its behaviour must not change silently'),
  ('*@lakitu.users.undernet.org', 'anyone!~x@lakitu.undernet.org',          False,
   'a *@host mask matched a host it does not name'),
  # fnmatch character classes.  [ and ] are legal, conventional nick characters.
  ('bob[away]!*@*',              'bob[away]!~b@h.example',                  True,
   'a mask containing [ ] does not match the nick it spells out: fnmatch reads '
   'them as a character class, so the entry names one person and matches four '
   'others'),
  ('bob[away]!*@*',              'boba!~b@h.example',                       False,
   'a mask containing [ ] matched a nick it does not name, via the character '
   'class fnmatch read out of it'),
  # A user we know only by nick must not satisfy a mask that asserts a host.
  ('bob!*@trusted.org',          'bob',                                     False,
   'a mask that names a host was satisfied by a bare nick, whose host we do '
   'not know -- for an auto-op list that hands out ops on a guess'),
]

for mask, user, want, why in MATCH_CASES:
  got = config_mod._match_any(user, [mask])
  check(got is want,
        'mask %r vs user %r: expected %s, got %s -- %s'
        % (mask, user, want, got, why))

# The list spells the expansion out, so the difference between the two kinds of
# entry is visible rather than something you have to know.
win = fresh('channel', '#forum')
Commands.aop(win, '-l')
check('matches *!*@lakitu.users.undernet.org' in win.out,
      '/aop -l does not show what a partially-spelled mask actually matches, '
      'which is the whole difference between naming a person and naming a '
      'host: %s' % win.out)

# And a nick-only auto-op is warned about, because a nick is not an identity.
win = fresh('channel', '#forum')
Commands.aop(win, 'nickonlyguy')
check('WARNING' in win.out and 'any' in win.out.lower(),
      '/aop <nick> with no host was added without warning that it ops whoever '
      'holds that nick, from anywhere -- the reporter read exactly such an '
      'entry as naming one person: %s' % win.out)
Commands.aop(fresh('server'), '-r nickonlyguy')

# A precise host-anchored mask is not broad and must not be warned about.
win = fresh('channel', '#forum')
Commands.aop(win, 'hegemon@lakitu.undernet.org')
check('WARNING' not in win.out,
      '/aop hegemon@lakitu.undernet.org names one nick on one host and must '
      'not be warned about, or the warning stops being read: %s' % win.out)
check(is_auto_op('hegemon!~heg@lakitu.undernet.org', 'undernet', '#forum'),
      'the mask just added does not op the person it names')
check(not is_auto_op('hegemon!~heg@elsewhere.example', 'undernet', '#forum'),
      'the mask just added ops hegemon from a host it does not name')
# ...and adding it says what it expands to.  The reporter added a `nick@host`
# mask, watched it op a nick whose ident was something else, and had to ask
# whether that was a bug -- because the only place the reading was visible was
# `-l`, which you have to already suspect something to go and run.
check('matches hegemon!*@lakitu.undernet.org' in win.out,
      'adding a partially-spelled mask does not echo what it expands to, so '
      'the nick@host vs ident@host reading is invisible at the one moment the '
      'user forms their idea of what the entry means: %s' % win.out)
Commands.aop(fresh('server'), '-r hegemon@lakitu.undernet.org')

# A fully-spelled mask has nothing to add, and must not be echoed at itself.
win = fresh('channel', '#forum')
Commands.aop(win, 'exact!~i@some.host')
check('matches' not in win.out,
      'a fully-spelled mask was echoed back as "matches <itself>", which is '
      'noise that trains the user to skip the line that matters: %s' % win.out)
Commands.aop(fresh('server'), '-r exact!~i@some.host')


# --- 8. The sibling list commands are not half-fixed -------------------------
#
# `/notify -l` still had the exact `-w` bug after `/aop -l` was fixed, because
# the two listed separately.  A bug found in one of four commands that share a
# concept is a bug in all four until each is shown otherwise.

state.notifications = None

# Both entries go in at *network* scope, which is what -w hides.
Commands.notify(fresh('server'), 'netwatched')
Commands.highlight(fresh('server'), 'nethighlight')

for cmd, name, entry in ((Commands.notify, '/notify', 'netwatched'),
                         (Commands.highlight, '/highlight', 'nethighlight')):
  for spelling in ('-l', '-lw', '-w'):
    win = fresh('channel', '#forum')
    cmd(win, spelling)
    check(entry in win.out,
          '%s %r does not show the network-scoped entry %r. -w narrows the '
          'list to the global scope instead of widening it, which is exactly '
          'how /aop -lw reported "empty" while entries were live.\ngot:\n%s'
          % (name, spelling, entry, win.out))

# A /notify remove with no scope reaches every scope, like /aop -r.
win = fresh('server')
Commands.notify(win, '-r netwatched')
check('Removed' in win.out,
      '/notify -r did not remove a network-scoped nick: %s' % win.out)
win = fresh('server')
Commands.notify(win, '-r netwatched')
check('nothing removed' in win.out.lower(),
      'a second /notify -r claimed to remove something again -- the same '
      '"reports success whatever happened" fault /aop had: %s' % win.out)
Commands.highlight(fresh('server'), '-r nethighlight')

# A highlight is not a hostmask: one containing '@' must not be annotated as
# matching some nick!ident@host.
Commands.highlight(fresh('server'), 'mail@example.com')
win = fresh('server')
Commands.highlight(win, '-l')
check('matches' not in win.out,
      'a highlight pattern containing "@" was reported as expanding to a '
      'hostmask; highlights are substrings and regexes, not masks: %s'
      % win.out)
Commands.highlight(fresh('server'), '-r mail@example.com')


# --- report -----------------------------------------------------------------

if failures:
  print('\nFAILED (%d):' % len(failures))
  for f in failures:
    print('  - %s' % f)
  sys.exit(1)

print('the auto-op list shows every entry from every window under every '
      'spelling, remove reaches every scope and reports what it did, a '
      'mistyped flag is an error rather than a new entry, and a mask grants '
      'exactly the nick and host it names.')
sys.exit(0)
