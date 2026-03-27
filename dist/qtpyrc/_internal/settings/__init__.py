# Settings dialog shared style constants
#
# These read from config at import time. If config isn't loaded yet,
# they fall back to defaults.


def _get_sizes():
    """Read settings element sizes from config, with defaults."""
    try:
        import state
        cfg = state.config
        if cfg:
            return (cfg.settings_title_size, cfg.settings_label_size,
                    cfg.settings_list_size, cfg.settings_note_size,
                    cfg.settings_hint_size, cfg.settings_delete_size)
    except Exception:
        pass
    return (13, 0, 0, 0, 0, 0)


def get_styles():
    """Return a dict of style strings, reading current config values."""
    title, label, lst, note, hint, delete = _get_sizes()
    def _sz(v):
        return 'font-size: %dpt;' % v if v else ''

    styles = {
        'title': ('font-weight: bold; ' + _sz(title)) if title else 'font-weight: bold;',
        'label': _sz(label),
        'list': _sz(lst),
        'note': 'color: #666; ' + _sz(note) if note else 'color: #666;',
        'hint': 'color: gray; ' + _sz(hint) if hint else 'color: gray;',
        'delete': (
            'QPushButton { color: #cc0000; border: none; font-weight: bold;'
            + (' font-size: %dpt;' % delete if delete else '')
            + ' padding: 0; }'
            'QPushButton:hover { background: #ffcccc; border-radius: 2px; }'
        ),
    }
    return styles


# Convenience accessors — evaluated at import time, so they use whatever
# config is loaded at that point.  For dynamic access use get_styles().
_s = get_styles()
SETTINGS_TITLE_STYLE = _s['title']
SETTINGS_LABEL_STYLE = _s['label']
SETTINGS_LIST_STYLE = _s['list']
SETTINGS_NOTE_STYLE = _s['note']
SETTINGS_HINT_STYLE = _s['hint']
SETTINGS_DELETE_STYLE = _s['delete']
