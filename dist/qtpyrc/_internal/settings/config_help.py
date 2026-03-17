"""Extract help text from config.defaults.yaml for settings tooltips.

Parses the YAML file as text and associates comment blocks with the
config key that follows them.

Format conventions in config.defaults.yaml:
  # comment text           — help text (accumulated for the next key)
  key: value               — active config key (help buffer assigned to it)
  #~ key: value            — commented-out config key (same as active key)
  # === or # ---           — section separator (resets help buffer)
"""

import os
import re

_help_cache = None


def _parse_help(path):
    """Parse config.defaults.yaml and return {dotted_key: help_text}.

    A comment block (consecutive # lines) is associated with the next
    key (active or #~ commented-out). Nested keys use dot notation
    (e.g. 'logging.dir', 'titles.server').
    """
    result = {}
    if not os.path.isfile(path):
        return result

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    comment_buf = []
    parents = []  # (indent, key) stack for nested keys
    separator_re = re.compile(r'^\s*#\s*[-=]{3,}')
    # Active key: "  key: value"
    key_re = re.compile(r'^(\s*)([A-Za-z_][\w]*):\s*(.*)')
    # Commented-out key: "#~ key: value" or "#~key: value"
    commented_key_re = re.compile(r'^\s*#~\s*([A-Za-z_][\w]*):\s*(.*)')

    for line in lines:
        stripped = line.rstrip()

        # Blank line — preserve paragraph breaks in help text
        if not stripped:
            if comment_buf and comment_buf[-1] != '':
                comment_buf.append('')
            continue

        # Section separator
        if separator_re.match(stripped):
            comment_buf = []
            continue

        # Commented-out config key (#~ marker)
        m = commented_key_re.match(stripped)
        if m:
            key = m.group(1)
            indent = len(stripped) - len(stripped.lstrip())
            _assign_help(result, parents, indent, key, comment_buf)
            comment_buf = []
            continue

        # Active config key
        m = key_re.match(stripped)
        if m and not stripped.lstrip().startswith('#'):
            indent = len(m.group(1))
            key = m.group(2)

            # Manage parent stack
            while parents and parents[-1][0] >= indent:
                parents.pop()

            _assign_help(result, parents, indent, key, comment_buf)
            comment_buf = []

            # Inline comment as fallback
            rest = m.group(3)
            dotted = '.'.join(p[1] for p in parents) + '.' + key if parents else key
            if dotted not in result and '#' in rest:
                inline = rest[rest.index('#') + 1:].strip()
                if inline:
                    result[dotted] = inline

            # Push as parent if value is empty (mapping/dict)
            value_part = rest.split('#')[0].strip()
            if not value_part:
                parents.append((indent, key))
            continue

        # Regular comment line — accumulate as help text
        if stripped.lstrip().startswith('#'):
            text = stripped.lstrip()
            # Strip the leading # and optional space
            text = text[1:].strip() if len(text) > 1 else ''
            comment_buf.append(text)
            continue

        # Anything else — reset
        comment_buf = []

    return result


def _assign_help(result, parents, indent, key, comment_buf):
    """Assign accumulated comment buffer to a dotted key."""
    # Clean up trailing blanks
    while comment_buf and comment_buf[-1] == '':
        comment_buf.pop()
    if not comment_buf:
        return
    # Build dotted key using parent stack
    # (don't modify parents here — caller handles that for active keys)
    temp_parents = list(parents)
    while temp_parents and temp_parents[-1][0] >= indent:
        temp_parents.pop()
    dotted = '.'.join(p[1] for p in temp_parents) + '.' + key if temp_parents else key
    result[dotted] = '\n'.join(comment_buf)


def get_help(key):
    """Get help text for a config key (e.g. 'logging.dir', 'input_lines').

    Returns the text or '' if not found.
    """
    global _help_cache
    if _help_cache is None:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'defaults', 'config.defaults.yaml')
        _help_cache = _parse_help(path)
    return _help_cache.get(key, '')


def get_all_help():
    """Return the full {key: help_text} dict."""
    get_help('')  # ensure loaded
    return dict(_help_cache)
