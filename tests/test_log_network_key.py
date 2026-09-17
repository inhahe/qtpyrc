"""The key a line is filed under must not change halfway through a connection.

`IRCClient._log_network` names the bucket every chat line goes into -- the
`network` column of the history table, and the network part of the log file
name. It used to read:

    return self.client.network or self.client.network_key or ...

`client.network` is the ISUPPORT `NETWORK=` value, and **it does not exist
until the 005 burst arrives**. So every line written before registration went
under the config key and every line after it went under the server's spelling,
on every connect. Not a rename -- an alternation, decided per line by whether
005 had landed yet.

Measured on the reporter's database before the fix:

    undernet          22,011 rows        UnderNet          14,530 rows
    Libera.Chat       41,362 rows        libera               770 rows
    EFNet              3,967 rows        EFnet                863 rows

23 channels and queries were double-booked. `UnderNet/#anxiety` held
09-12..09-16 and `undernet/#anxiety` held 09-11..09-17, interleaved by hours.

**Why it stayed invisible for months**: a replay reads one key, and
`backscroll_limit` prunes each bucket to 1000 rows *independently*, so both
halves stayed full and the window still showed a thousand lines. It just showed
a thousand lines with holes in them, and nothing says which ones are missing.

What this pins is the invariant, not the current fallback order: **the value is
the same before and after registration**. Anything derived from the server
fails that by construction, whatever order it is written in.

Usage:
  python tests/test_log_network_key.py     # from the qtpyrc root directory
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


class Client(object):
  def __init__(self, network_key=None, network=None, hostname=None):
    self.network_key = network_key
    self.network = network
    self.hostname = hostname


class Conn(object):
  def __init__(self, client):
    self.client = client


def log_network(client):
  """_log_network is a property; evaluate it against a stub."""
  from irc_client import IRCClient
  return IRCClient._log_network.fget(Conn(client))


def main():
  # --- the invariant: registration must not move the key ------------------
  # Same connection, twice: once before the 005 burst (no NETWORK= yet) and
  # once after. These are the two states every connection passes through.
  before = log_network(Client(network_key='undernet', network=None,
                              hostname='127.0.0.1'))
  after = log_network(Client(network_key='undernet', network='UnderNet',
                             hostname='127.0.0.1'))
  check(before == after,
        'the key changed at registration: %r before the 005 burst and %r '
        'after. Every connect then splits the channel into two buckets, and a '
        'replay reads one of them.' % (before, after))
  check(after == 'undernet',
        'expected the configured key %r, got %r' % ('undernet', after))

  # The same for the other spellings this cost the reporter.
  for key, isupport in (('libera', 'Libera.Chat'), ('efnet', 'EFNet'),
                        ('dalnet', 'DALnet'), ('binias', 'Binkiewka-Labs')):
    a = log_network(Client(network_key=key, network=None, hostname='h'))
    b = log_network(Client(network_key=key, network=isupport, hostname='h'))
    check(a == b == key,
          '%s: %r before 005 and %r after, expected %r both times'
          % (key, a, b, key))

  # --- the fallback chain still works when there is no config key ---------
  # A server added with /server and never named in the config has no key, so
  # the server's own name is the best thing left; then the hostname.
  check(log_network(Client(network_key=None, network='UnderNet',
                           hostname='irc.undernet.org')) == 'UnderNet',
        'with no config key the ISUPPORT name should be used')
  check(log_network(Client(network_key=None, network=None,
                           hostname='irc.undernet.org')) == 'irc.undernet.org',
        'with no config key and no ISUPPORT name the hostname should be used')
  check(log_network(Client()) == 'unknown',
        'with nothing at all the key should be "unknown", never empty -- an '
        'empty network column is a bucket no window ever reads')

  # An empty string is not a name. It reached the database as one: the
  # reporter's history has 9 rows under ''.
  check(log_network(Client(network_key='', network='', hostname='')) ==
        'unknown',
        'empty strings should fall through to "unknown" rather than being '
        'filed under ""')

  # --- the display label is a different question, and stays one -----------
  # _net_label may prefer whatever reads best; it names nothing on disk.
  from irc_client import IRCClient
  label = IRCClient._net_label(Conn(Client(network_key='undernet',
                                           network='UnderNet',
                                           hostname='h')))
  check(isinstance(label, str) and label,
        '_net_label returned %r' % (label,))

  if failures:
    print('FAILED (%d):' % len(failures))
    for f in failures:
      print('  - %s' % f)
    return 1
  print('the history/log key is the same before and after registration.')
  return 0


sys.exit(main())
