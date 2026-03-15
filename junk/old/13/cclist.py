#!/usr/bin/env python3
"""List all Claude Code conversations with their dates, project names, and paths.

Usage:  python cclist.py [--json]
"""

import json, os, sys, platform
from datetime import datetime, timezone


def _claude_projects_dir():
    """Return the Claude Code projects directory."""
    if platform.system() == 'Windows':
        home = os.environ.get('USERPROFILE', os.path.expanduser('~'))
    else:
        home = os.path.expanduser('~')
    return os.path.join(home, '.claude', 'projects')


def _read_timestamps_and_cwd(path):
    """Read first/last timestamps and cwd from a JSONL file."""
    first_ts = None
    cwd = None
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not first_ts and obj.get('timestamp'):
                first_ts = obj['timestamp']
            if not cwd and obj.get('cwd'):
                cwd = obj['cwd']
            if first_ts and cwd:
                break

    # Read last timestamp from the tail of the file
    last_ts = None
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 50000))
        tail = f.read().decode('utf-8', errors='replace')
    for line in reversed(tail.strip().split('\n')):
        try:
            obj = json.loads(line)
            if obj.get('timestamp'):
                last_ts = obj['timestamp']
                break
        except json.JSONDecodeError:
            continue

    return first_ts, last_ts, cwd


def _parse_ts(ts_str):
    """Parse an ISO timestamp string to a datetime."""
    if not ts_str:
        return None
    # Handle trailing Z
    ts_str = ts_str.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return None


def _format_ts(dt):
    """Format a datetime for display in local time."""
    if not dt:
        return '?'
    local = dt.astimezone()
    return local.strftime('%Y-%m-%d %H:%M')


def _project_name(dirname):
    """Derive a short project name from the directory name."""
    # Directory names are like D--visual-studio-projects-qtpyrc
    # Strip the drive letter prefix and convert dashes to path separators
    # to find the last component
    parts = dirname.split('-')
    # The last part is usually the project name
    return parts[-1] if parts else dirname


def list_conversations():
    """Collect all conversations and return as a sorted list of dicts."""
    base = _claude_projects_dir()
    if not os.path.isdir(base):
        print('Claude Code projects directory not found: %s' % base, file=sys.stderr)
        sys.exit(1)

    conversations = []
    for proj_dir_name in sorted(os.listdir(base)):
        proj_path = os.path.join(base, proj_dir_name)
        if not os.path.isdir(proj_path):
            continue
        for fname in sorted(os.listdir(proj_path)):
            if not fname.endswith('.jsonl'):
                continue
            # Skip agent sub-conversations
            if fname.startswith('agent-'):
                continue
            fpath = os.path.join(proj_path, fname)
            first_ts, last_ts, cwd = _read_timestamps_and_cwd(fpath)
            first_dt = _parse_ts(first_ts)
            last_dt = _parse_ts(last_ts)
            session_id = os.path.splitext(fname)[0]
            conversations.append({
                'session_id': session_id,
                'project': _project_name(proj_dir_name),
                'project_dir': proj_dir_name,
                'cwd': cwd or '?',
                'start': first_dt,
                'end': last_dt,
                'path': fpath,
            })

    # Sort by start time
    conversations.sort(key=lambda c: c['start'] or datetime.min.replace(tzinfo=timezone.utc))
    return conversations


def main():
    as_json = '--json' in sys.argv

    convos = list_conversations()

    if as_json:
        out = []
        for c in convos:
            out.append({
                'session_id': c['session_id'],
                'project': c['project'],
                'cwd': c['cwd'],
                'start': _format_ts(c['start']),
                'end': _format_ts(c['end']),
                'path': c['path'],
            })
        print(json.dumps(out, indent=2))
        return

    if not convos:
        print('No conversations found.')
        return

    # Column widths
    proj_w = max(len(c['project']) for c in convos)
    cwd_w = max(len(c['cwd']) for c in convos)
    proj_w = max(proj_w, 7)  # "Project"
    cwd_w = min(max(cwd_w, 4), 50)  # cap width

    hdr = '%-*s  %-16s  %-16s  %s' % (proj_w, 'Project', 'Start', 'End', 'CWD')
    print(hdr)
    print('-' * len(hdr))

    for c in convos:
        print('%-*s  %-16s  %-16s  %s' % (
            proj_w, c['project'],
            _format_ts(c['start']),
            _format_ts(c['end']),
            c['cwd'],
        ))
        print('  %s' % c['path'])

    print('\n%d conversations' % len(convos))


if __name__ == '__main__':
    main()
