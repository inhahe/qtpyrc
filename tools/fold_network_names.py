"""Fold the several spellings of one network into one key, in history and logs.

Why this exists
---------------
`IRCClient._log_network` -- the key a line is filed under -- used to read

    return self.client.network or self.client.network_key or ...

`client.network` is the ISUPPORT `NETWORK=` value, which does not exist until
the 005 burst arrives. So everything written before registration was filed
under the config key and everything after it under the server's spelling, on
every connect. The same channel becomes two buckets, interleaved by hours:

    UnderNet/#anxiety   1002 rows   09-12 12:23 .. 09-16 13:24
    undernet/#anxiety   1007 rows   09-11 05:33 .. 09-17 06:03

A replay reads one key, so half the backscroll is invisible -- and
`backscroll_limit` prunes each bucket to 1000 independently, so the halves stay
balanced and nothing looks wrong. On the reporter's database: 23 channels and
queries double-booked, five spellings of Undernet, three of EFnet.

The code is fixed going forward. This folds what is already there.

How the mapping is decided
--------------------------
**Only two rules, and neither of them guesses.**

  1. A spelling that equals a configured network key case-insensitively maps to
     that key. `UnderNet` -> `undernet`, `EFNet`/`EFnet` -> `efnet`.
  2. Anything else must be named explicitly with `--map OLD=NEW`.

Everything else is left alone and reported. That is deliberate: two automatic
heuristics were tried and both were wrong on this very database. Overlapping
channel sets mapped `Libera.Chat` (41,334 rows) onto `efnet` on the strength of
one shared channel, because the denominator was the smaller set. Matching
against the config's autojoin lists resolved nothing, because the config has no
autojoin lists -- the channels come from the bouncer. A rule that is right four
times and silently wrong the fifth is worse here than no rule, because the
fifth is a table rewrite.

For the reporter's setup the remaining mappings came from the bouncer's own
config and its 005 traffic, which is evidence rather than inference:

    libera   irc.libera.chat    NETWORK=Libera.Chat
    undernet irc.undernet.org   NETWORK=UnderNet
    efnet    irc.prison.net     NETWORK=EFNet
    dalnet   irc.dal.net        NETWORK=DALnet
    binias   irc.binkiewka.org  NETWORK=Binkiewka-Labs

  python tools/fold_network_names.py --map Libera.Chat=libera \\
      --map Binkiewka-Labs=binias --map irc.undernet.org=undernet --apply

What it does
------------
* Rewrites `history.network`, then **renumbers every row into timestamp
  order**. That is not cosmetic: qtpyrc reads a channel's backlog by row id
  (`ORDER BY id`), so merging two id ranges without renumbering interleaves two
  conversations by when they were *written* rather than when they were said.
* Drops rows that become exact duplicates of each other (same timestamp, type,
  nick and text in the same channel) -- a line recorded once live and once from
  a replay under the other spelling.
* Rewrites `urls.network` too. Those are read by `(network, ts)`, so they need
  the key fixed but not the renumbering.
* Merges the matching log files by timestamp, in place.
* **Backs up history.db first**, and does nothing at all without `--apply`.

Close qtpyrc first. It holds history.db open, and this rewrites it.
"""

import argparse
import io
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOG_LINE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')


def safename(s):
    """The same transform logger.IRCLogger._safename applies to a path part."""
    return re.sub(r'[<>:"/\\|?*]', '_', s or 'unknown')


def config_network_keys(cfg_path):
    try:
        from ruamel.yaml import YAML
        with io.open(cfg_path, encoding='utf-8') as f:
            data = YAML().load(f) or {}
    except Exception as e:
        sys.exit('could not read %s: %s' % (cfg_path, e))
    return list((data.get('networks') or {}).keys())


def history_is_in_use(hist_db):
    """True if something else has history.db open for writing.

    Asked of the database rather than of the process list -- that is the thing
    that actually matters, and this machine runs a tray minimiser whose
    *arguments* name qtpyrc.py, so matching command lines would refuse always.
    """
    try:
        con = sqlite3.connect(hist_db, timeout=1)
        try:
            con.execute('BEGIN EXCLUSIVE')
            con.execute('ROLLBACK')
            return False
        finally:
            con.close()
    except sqlite3.OperationalError:
        return True
    except Exception:
        return False


def build_mapping(networks, keys, explicit):
    """spelling -> canonical key, for the spellings we can justify."""
    by_lower = {k.lower(): k for k in keys}
    mapping = {}
    for net in networks:
        if net in explicit:
            mapping[net] = explicit[net]
        elif net in keys:
            pass                                  # already canonical
        elif net.lower() in by_lower:
            mapping[net] = by_lower[net.lower()]
    return mapping


def survey(hist_db):
    con = sqlite3.connect('file:%s?mode=ro' % hist_db.replace('?', '%3f'),
                          uri=True)
    rows = Counter()
    chans = defaultdict(Counter)
    for net, chan, n in con.execute(
            'SELECT network, channel, COUNT(*) FROM history '
            'GROUP BY network, channel'):
        rows[net] += n
        chans[net][chan] = n
    urls = Counter()
    try:
        for net, n in con.execute(
                'SELECT network, COUNT(*) FROM urls GROUP BY network'):
            urls[net] += n
    except sqlite3.Error:
        pass
    con.close()
    return rows, chans, urls


def fold_history(hist_db, mapping):
    backup = hist_db + '.pre-fold-%s' % time.strftime('%Y%m%d-%H%M%S')
    shutil.copy2(hist_db, backup)
    print('history backup: %s' % backup)

    con = sqlite3.connect(hist_db)
    con.execute('PRAGMA journal_mode=WAL')
    rows = con.execute('SELECT ts, network, channel, type, nick, text, prefix '
                       'FROM history').fetchall()
    folded = [(ts, mapping.get(net, net), chan, kind, nick, text, prefix)
              for ts, net, chan, kind, nick, text, prefix in rows]

    # A line recorded once live and once from a replay under the other
    # spelling becomes an exact duplicate the moment the spellings agree.
    # Exact match only -- anything looser would eat a line legitimately said
    # twice in the same second.
    seen = set()
    unique = []
    for r in folded:
        k = (r[0], r[1], r[2], r[3], r[4], r[5])
        if k in seen:
            continue
        seen.add(k)
        unique.append(r)
    dropped = len(folded) - len(unique)

    # (ts, then the original order) -- a stable sort keeps same-second lines in
    # the order they were already in rather than shuffling a conversation.
    unique.sort(key=lambda r: r[0])

    con.execute('DROP TABLE IF EXISTS history_fold_tmp')
    con.execute("""CREATE TABLE history_fold_tmp (
                     id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                     network TEXT NOT NULL, channel TEXT NOT NULL,
                     type TEXT NOT NULL, nick TEXT, text TEXT,
                     prefix TEXT DEFAULT '')""")
    con.executemany(
        'INSERT INTO history_fold_tmp (ts,network,channel,type,nick,text,prefix)'
        ' VALUES (?,?,?,?,?,?,?)', unique)
    con.execute('DROP TABLE history')
    con.execute('ALTER TABLE history_fold_tmp RENAME TO history')
    con.execute('CREATE INDEX IF NOT EXISTS idx_history_lookup '
                'ON history (network, channel, id)')

    moved = 0
    try:
        for old, new in mapping.items():
            cur = con.execute('UPDATE urls SET network=? WHERE network=?',
                              (new, old))
            moved += cur.rowcount or 0
    except sqlite3.Error:
        pass

    con.commit()
    con.execute('VACUUM')
    con.close()
    print('history: %d rows, renumbered in timestamp order' % len(unique))
    if dropped:
        print('         %d exact duplicate row(s) dropped' % dropped)
    if moved:
        print('urls:    %d row(s) re-keyed' % moved)


def merge_log(src, dst):
    """Append src into dst, keeping both in timestamp order."""
    def read(p):
        with io.open(p, encoding='utf-8', errors='replace') as f:
            return f.read().splitlines()

    merged = []
    for path in (dst, src):
        stamp = ''
        for line in read(path):
            m = LOG_LINE.match(line)
            if m:
                stamp = m.group(1)
            merged.append((stamp, line))
    merged.sort(key=lambda t: t[0])       # stable: keeps each file's own order
    with io.open(dst, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(line for _, line in merged) + '\n')


def fold_logs(log_dir, mapping, apply):
    if not os.path.isdir(log_dir):
        print('no log directory at %s -- skipping logs' % log_dir)
        return
    renamed = merged = 0
    for old, new in sorted(mapping.items()):
        prefix = safename(old) + '_'
        newprefix = safename(new) + '_'
        for fn in sorted(os.listdir(log_dir)):
            if not fn.startswith(prefix) or not fn.endswith('.log'):
                continue
            src = os.path.join(log_dir, fn)
            dst = os.path.join(log_dir, newprefix + fn[len(prefix):])
            if os.path.exists(dst):
                merged += 1
                if apply:
                    merge_log(src, dst)
                    os.remove(src)
            else:
                renamed += 1
                if apply:
                    os.rename(src, dst)
    print('logs:    %d file(s) renamed, %d merged into an existing file'
          % (renamed, merged))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--history-db',
                    default=os.path.join(ROOT, 'me', 'history.db'))
    ap.add_argument('--config', default=os.path.join(ROOT, 'me', 'config.yaml'))
    ap.add_argument('--log-dir', default=os.path.join(ROOT, 'me', 'logs'))
    ap.add_argument('--map', action='append', default=[], metavar='OLD=NEW',
                    help='map a spelling this cannot justify on its own')
    ap.add_argument('--apply', action='store_true',
                    help='actually write; without it nothing is changed')
    ap.add_argument('--no-logs', action='store_true')
    args = ap.parse_args()

    for path in (args.history_db, args.config):
        if not os.path.exists(path):
            sys.exit('not found: %s' % path)

    explicit = {}
    for item in args.map:
        if '=' not in item:
            sys.exit('--map wants OLD=NEW, got %r' % item)
        old, new = item.split('=', 1)
        explicit[old] = new

    keys = config_network_keys(args.config)
    print('config network keys: %s' % ', '.join(keys))
    rows, chans, urls = survey(args.history_db)
    mapping = build_mapping(list(rows) + list(urls), keys, explicit)

    print()
    print('%-20s %8s  %s' % ('spelling', 'rows', 'action'))
    for net in sorted(rows, key=lambda n: -rows[n]):
        if net in mapping:
            why = 'named' if net in explicit else 'same key, different case'
            print('%-20s %8d  -> %-12s (%s)' % (repr(net), rows[net],
                                                mapping[net], why))
        elif net in keys:
            print('%-20s %8d  canonical' % (repr(net), rows[net]))
        else:
            print('%-20s %8d  LEFT ALONE -- no configured key matches, and no '
                  '--map given' % (repr(net), rows[net]))

    merges = []
    for old, new in mapping.items():
        for chan in chans.get(old, {}):
            if chan in chans.get(new, {}):
                merges.append((new, chan, chans[old][chan], chans[new][chan]))
    print()
    print('%d row(s) change key; %d channel(s) merge two buckets into one'
          % (sum(rows[n] for n in mapping if n in rows), len(merges)))
    for new, chan, a, b in sorted(merges, key=lambda m: -(m[2] + m[3]))[:15]:
        print('    %-12s %-22s %d + %d = %d' % (new, chan, a, b, a + b))
    if len(merges) > 15:
        print('    ... and %d more' % (len(merges) - 15))

    if not mapping:
        print('\nnothing to fold.')
        return

    if not args.apply:
        print('\ndry run -- nothing written. Re-run with --apply to do it.')
        return

    if history_is_in_use(args.history_db):
        sys.exit('qtpyrc appears to be running: history.db is locked. It holds '
                 'the database open and this rewrites it -- close it first.')

    print()
    fold_history(args.history_db, mapping)
    if not args.no_logs:
        fold_logs(args.log_dir, mapping, apply=True)
    print('done.')


if __name__ == '__main__':
    main()
