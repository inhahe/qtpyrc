"""Import chat that Wicket has and qtpyrc does not, into qtpyrc's history.

Why this exists
---------------
Until 2026-09-04 qtpyrc recorded nothing that arrived inside a bouncer playback
batch. That rule was written for ZNC, which replays a fixed tail of every
channel on each reconnect -- but Wicket replays *what you missed*, so for it the
replay was the only time those lines ever reached the client, and suppressing
it lost them. The reporter's database has no rows at all for 2026-09-04
06:00, nor 08:00 through 12:00: five hours across some thirty channels, drawn
on screen and written nowhere.

Going forward that is fixed (IRCClient._should_record). This fills the hole
already made, from Wicket's own `messages` table, which still has every line.

What it does and does not do
----------------------------
* Imports **PRIVMSG and NOTICE only** -- messages, CTCP ACTIONs and notices.
  Joins, parts, quits and modes are not chat and qtpyrc's replay does not miss
  them in a way anyone notices.
* Imports only what qtpyrc does **not already have**, matched on
  (nick, text) within a few seconds. It does not match on an exact timestamp:
  qtpyrc stamped live rows with its own receive time, not the server's, so the
  same line can sit a second or two either side in the two databases.
* Rewrites history.db in **timestamp order**. This is the part that makes it a
  migration rather than an insert: qtpyrc reads a channel's backlog by row id
  (`ORDER BY id`), so rows appended with new ids would show up as the newest
  messages regardless of when they were said. Every row is renumbered by time,
  which also repairs any ordering already skewed.
* Merges the same lines into the text logs, in place, by timestamp.
* **Backs up everything it touches** first, and does nothing at all without
  --apply.

Usage
-----
  python tools/import_wicket_history.py                   # dry run, reports
  python tools/import_wicket_history.py --apply           # do it
  python tools/import_wicket_history.py --days 60 --apply

Close qtpyrc first. It holds history.db open, and this rewrites it.
"""

import argparse
import datetime
import os
import re
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_WICKET = r'D:\visual studio projects\irc bouncer\Wicket.db'

# How far apart two copies of the same line may sit and still be one line.
# qtpyrc stamped live rows with its receive time and Wicket with its own, so a
# few seconds of skew is normal; a person repeating themselves verbatim inside
# that window is not.
DEDUP_WINDOW = 15


def parse_line(raw):
    """(nick, command, target, text) from a raw IRC line, or None."""
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        # Wicket stores raw_line as a BLOB: it is the wire form, and the wire
        # has no encoding. Decode leniently -- a line with a stray byte is
        # still worth importing, and 'replace' is what qtpyrc does everywhere
        # else it reads text off the network.
        raw = bytes(raw).decode('utf-8', 'replace')
    line = raw.strip()
    if line.startswith('@'):                 # strip IRCv3 tags
        sp = line.find(' ')
        if sp < 0:
            return None
        line = line[sp + 1:].lstrip()
    if not line.startswith(':'):
        return None
    sp = line.find(' ')
    if sp < 0:
        return None
    prefix, rest = line[1:sp], line[sp + 1:]
    nick = prefix.split('!', 1)[0].split('@', 1)[0]
    parts = rest.split(' :', 1)
    head = parts[0].split()
    if not head:
        return None
    command = head[0].upper()
    target = head[1] if len(head) > 1 else ''
    text = parts[1] if len(parts) > 1 else (head[2] if len(head) > 2 else '')
    return nick, command, target, text


def classify(command, text):
    """(qtpyrc history type, text) -- unwrapping CTCP ACTION."""
    if command == 'NOTICE':
        return 'notice', text
    if text.startswith('\x01ACTION') and text.endswith('\x01'):
        return 'action', text[len('\x01ACTION'):-1].strip()
    if text.startswith('\x01') and text.endswith('\x01'):
        return None, text                    # other CTCP: not chat
    return 'message', text


def hist_key(target):
    """qtpyrc's history key for a Wicket target.

    Channels are stored lowercased; a PM is stored under "=nick", which is
    what irc_client._query_history_key builds. Wicket's "*" pseudo-target
    holds NICK/QUIT and is not chat.
    """
    if not target or target == '*':
        return None
    if target[0] in '#&!+':
        return target.lower()
    return '=' + target.split('!', 1)[0].lower()


def load_qtpyrc(hist_db, since_ts):
    """Existing rows: {(network, channel): [(epoch, nick, text)]} plus the
    per-(channel) network spellings, so imports land where the history already
    is. qtpyrc has stored the same network under several spellings (see the
    open entry in known-issues.md), and a row filed under the other one is
    invisible to the replay."""
    con = sqlite3.connect('file:%s?mode=ro' % hist_db.replace('\\', '/'), uri=True)
    rows = con.execute(
        "SELECT network, channel, ts, nick, text FROM history "
        "WHERE type IN ('message','action','notice')").fetchall()
    con.close()
    existing = {}
    spellings = {}
    for net, chan, ts, nick, text in rows:
        try:
            epoch = time.mktime(time.strptime(ts, '%Y-%m-%d %H:%M:%S'))
        except (ValueError, TypeError):
            continue
        existing.setdefault((net, chan), []).append((epoch, nick or '', text or ''))
        spellings.setdefault(chan, {}).setdefault(net, 0)
        spellings[chan][net] += 1
    return existing, spellings


def preferred_network(spellings, chan, wicket_net, fallback):
    """The network spelling to file *chan* under.

    Whichever one already holds most of that channel's history, so the import
    joins it rather than starting a second pile beside it.
    """
    counts = spellings.get(chan)
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    return fallback.get(wicket_net) or wicket_net


def history_is_in_use(hist_db):
    """True if something else has history.db open for writing.

    Asked of the database rather than of the process list: that is the thing
    that actually matters, it needs no pattern-matching against command lines
    (this machine runs a tray minimiser whose *arguments* name qtpyrc.py, so
    matching on the string alone would refuse every time), and it is the same
    answer on any platform. A live qtpyrc holds a WAL write lock, so asking for
    an exclusive one fails.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--wicket-db', default=DEFAULT_WICKET)
    ap.add_argument('--history-db', default=os.path.join(ROOT, 'me', 'history.db'))
    ap.add_argument('--log-dir', default=os.path.join(ROOT, 'me', 'logs'))
    ap.add_argument('--days', type=int, default=30,
                    help='how far back to look (default 30)')
    ap.add_argument('--apply', action='store_true',
                    help='actually write; without it nothing is changed')
    ap.add_argument('--no-logs', action='store_true',
                    help='history only, leave the text logs alone')
    args = ap.parse_args()

    for path in (args.wicket_db, args.history_db):
        if not os.path.exists(path):
            sys.exit('not found: %s' % path)

    since = time.time() - args.days * 86400
    print('reading qtpyrc history ...')
    existing, spellings = load_qtpyrc(args.history_db, since)
    print('  %d (network, channel) pairs, %d chat rows'
          % (len(existing), sum(len(v) for v in existing.values())))

    # The spelling to use for a network we have never filed a given channel
    # under: whichever that network is most often called overall.
    per_net = {}
    for chan, counts in spellings.items():
        for net, n in counts.items():
            per_net.setdefault(net.lower(), {}).setdefault(net, 0)
            per_net[net.lower()][net] += n
    fallback = {low: max(v.items(), key=lambda kv: kv[1])[0]
                for low, v in per_net.items()}

    print('reading Wicket (%s, last %d days) ...'
          % (os.path.basename(args.wicket_db), args.days))
    con = sqlite3.connect('file:%s?mode=ro' % args.wicket_db.replace('\\', '/'),
                          uri=True)
    con.execute('PRAGMA query_only=1')
    cur = con.execute(
        "SELECT network, target, timestamp, raw_line FROM messages "
        "WHERE timestamp >= ? AND command IN ('PRIVMSG','NOTICE') "
        "ORDER BY timestamp", (since,))

    oldest = {k: min(e for e, _n, _t in v) for k, v in existing.items()}
    candidates = {}
    scanned = skipped_dup = skipped_pruned = 0
    for net, target, ts, raw in cur:
        scanned += 1
        key = hist_key(target)
        if key is None:
            continue
        parsed = parse_line(raw)
        if not parsed:
            continue
        nick, command, _t, text = parsed
        kind, text = classify(command, text)
        if kind is None or not text:
            continue
        qnet = preferred_network(spellings, key, net, fallback)
        have = existing.get((qnet, key), ())
        # Anything older than the oldest row qtpyrc still has for this channel
        # was *pruned*, not lost: backscroll_limit keeps the newest N per
        # channel and the maintenance pass drops the rest. Re-importing it
        # would balloon the database and then be deleted again on the next
        # pass. Only the gaps inside the window qtpyrc still covers are the
        # bug's doing.
        floor = oldest.get((qnet, key))
        if floor is not None and ts < floor:
            skipped_pruned += 1
            continue
        if any(abs(e - ts) <= DEDUP_WINDOW and n == nick and t == text
               for e, n, t in have):
            skipped_dup += 1
            continue
        candidates.setdefault((qnet, key), []).append((ts, kind, nick, text))
    con.close()

    total = sum(len(v) for v in candidates.values())
    print('  scanned %d Wicket chat rows' % scanned)
    print('  %7d already in qtpyrc' % skipped_dup)
    print('  %7d older than qtpyrc still keeps for that channel (pruned by '
          'backscroll_limit, not lost)' % skipped_pruned)
    print('  %7d to import' % total)
    if not total:
        print('nothing to do.')
        return 0

    print()
    print('  %-14s %-26s %7s  %s' % ('network', 'channel', 'lines', 'range'))
    for (net, chan), rows in sorted(candidates.items(),
                                    key=lambda kv: -len(kv[1]))[:25]:
        lo = datetime.datetime.fromtimestamp(rows[0][0]).strftime('%m-%d %H:%M')
        hi = datetime.datetime.fromtimestamp(rows[-1][0]).strftime('%m-%d %H:%M')
        print('  %-14s %-26s %7d  %s .. %s' % (net, chan[:26], len(rows), lo, hi))
    if len(candidates) > 25:
        print('  ... and %d more targets' % (len(candidates) - 25))

    if not args.apply:
        print()
        print('dry run -- nothing written. Re-run with --apply to do it.')
        return 0

    if history_is_in_use(args.history_db):
        sys.exit('qtpyrc appears to be running: history.db is locked. It holds '
                 'the database open and this rewrites the table, so close it first.')

    print()
    import_history(args.history_db, candidates)
    if not args.no_logs:
        import_logs(args.log_dir, candidates)
    print('done.')
    return 0


def import_history(hist_db, candidates):
    """Merge into history.db, renumbering every row into timestamp order."""
    backup = hist_db + '.pre-import-%s' % time.strftime('%Y%m%d-%H%M%S')
    shutil.copy2(hist_db, backup)
    print('history backup: %s' % backup)

    con = sqlite3.connect(hist_db)
    con.execute('PRAGMA journal_mode=WAL')
    rows = con.execute(
        'SELECT ts, network, channel, type, nick, text, prefix FROM history'
    ).fetchall()
    for (net, chan), items in candidates.items():
        for ts, kind, nick, text in items:
            rows.append((datetime.datetime.fromtimestamp(ts)
                         .strftime('%Y-%m-%d %H:%M:%S'),
                         net, chan, kind, nick, text, ''))
    # (ts, then the original order) -- a stable sort keeps same-second lines in
    # the order they were already in rather than shuffling a conversation.
    rows.sort(key=lambda r: r[0])
    con.execute('DROP TABLE IF EXISTS history_import_tmp')
    con.execute("""CREATE TABLE history_import_tmp (
                     id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                     network TEXT NOT NULL, channel TEXT NOT NULL,
                     type TEXT NOT NULL, nick TEXT, text TEXT,
                     prefix TEXT DEFAULT '')""")
    con.executemany(
        'INSERT INTO history_import_tmp (ts,network,channel,type,nick,text,prefix)'
        ' VALUES (?,?,?,?,?,?,?)', rows)
    con.execute('DROP TABLE history')
    con.execute('ALTER TABLE history_import_tmp RENAME TO history')
    con.execute('CREATE INDEX IF NOT EXISTS idx_history_lookup '
                'ON history (network, channel, id)')
    con.commit()
    con.execute('VACUUM')
    con.close()
    print('history: %d rows, renumbered in timestamp order' % len(rows))


LOG_LINE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')


def import_logs(log_dir, candidates):
    """Merge the same lines into the text logs, in timestamp order.

    Only touches files whose lines already carry a `[YYYY-MM-DD HH:MM:SS]`
    stamp -- that is the reporter's `logging.timestamp` and the only format
    this can merge into without guessing. A file in any other format is left
    alone and reported, because a log half in one format is worse than a log
    with a gap.
    """
    if not os.path.isdir(log_dir):
        print('log dir not found, skipping logs: %s' % log_dir)
        return
    by_file = {}
    for (net, chan), items in candidates.items():
        for ts, kind, nick, text in items:
            dt = datetime.datetime.fromtimestamp(ts)
            safe = re.sub(r'[<>:"/\\|?*]', '_', chan.lstrip('='))
            name = '%s_%s_%s.log' % (re.sub(r'[<>:"/\\|?*]', '_', net),
                                     safe, dt.strftime('%Y-%m'))
            if kind == 'action':
                body = '* %s %s' % (nick, text)
            elif kind == 'notice':
                body = '-%s- %s' % (nick, text)
            else:
                body = '<%s> %s' % (nick, text)
            by_file.setdefault(name, []).append(
                (dt.strftime('%Y-%m-%d %H:%M:%S'), body))

    touched = skipped = 0
    for name, items in sorted(by_file.items()):
        path = os.path.join(log_dir, name)
        old = []
        if os.path.exists(path):
            with open(path, encoding='utf-8', errors='replace') as f:
                old = f.read().splitlines()
            stamped = sum(1 for l in old[:200] if LOG_LINE.match(l))
            if old and stamped < len(old[:200]) * 0.8:
                print('  skipped (unrecognised timestamp format): %s' % name)
                skipped += 1
                continue
            shutil.copy2(path, path + '.pre-import')
        merged = [(LOG_LINE.match(l).group(1) if LOG_LINE.match(l) else '', l)
                  for l in old]
        merged += [(ts, '[%s] %s' % (ts, body)) for ts, body in items]
        merged.sort(key=lambda p: p[0])
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(line for _ts, line in merged) + '\n')
        touched += 1
    print('logs: %d file(s) merged, %d skipped (originals kept as '
          '*.pre-import)' % (touched, skipped))


if __name__ == '__main__':
    sys.exit(main())
