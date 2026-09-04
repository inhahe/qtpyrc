"""The settings tree's shape, and nothing else.

Split out of `settings_dialog` so that *naming* the settings pages does not
import them. `qtpyrc._register_settings_paths` populates `state.ui_registry`
with every `settings.*` path at startup, and it only ever needed these two
tables and the generator below -- but reaching them through `settings_dialog`
pulled in all seventeen page modules with it, because that module imports every
page class at the top. Measured with `python -X importtime` on the reporter's
machine: **2.57 seconds of startup**, to build a list of strings that nothing
had yet asked for.

Keep this module cheap. It must import nothing beyond the standard library; the
one thing it needs from a page module (`get_plugin_names`) is imported inside
the function that uses it, so an unused path costs nothing.
"""

# Settings tree structure: (ui_path_suffix, page_id, label, children)
# Used for both building the tree and registering --ui paths.
SETTINGS_PAGES = [
    ('general', 'general', 'General', [
        ('general.interface', 'interface', 'Interface', []),
        ('general.titles', 'titles', 'Titles', []),
        ('general.identserver', 'ident_server', 'Ident Server', []),
        ('general.logging', 'logging', 'Logging', []),
        ('general.linkpreview', 'link_preview', 'Link Previews', []),
        ('general.files', 'files', 'Files', []),
    ]),
    ('identity', 'identity', 'Identity', []),
    ('lists', 'lists', 'Lists', []),
    ('fonts', 'font_root', 'Font / Colors', [
        ('fonts.chat', 'font_chat', 'Chat', []),
        ('fonts.tab', 'font_tab', 'Tab Bar', []),
        ('fonts.menu', 'font_menu', 'Menus', []),
        ('fonts.tree', 'font_tree', 'Network Tree', []),
        ('fonts.nicklist', 'font_nicklist', 'Nick List', []),
        ('fonts.toolbar', 'font_toolbar', 'Toolbar', []),
        ('fonts.settings', 'font_settings', 'Settings Dialog', []),
        ('fonts.editor', 'font_editor', 'File Editor', []),
        ('fonts.nickcolors', 'nick_colors', 'Nick Colors', []),
    ]),
    ('notifications', 'notifications', 'Notifications', []),
    ('dcc', 'dcc', 'DCC', []),
    ('scripts', 'scripts', 'Scripts', []),
    ('plugins', 'plugin_config', 'Plugins', []),
    ('editor', 'editor', 'File Editor', []),
]


NETWORK_SUB_PAGES = [
    ('server', 'Servers'),
    ('sasl', 'SASL'),
    ('autojoin', 'Channels'),
    ('lists', 'Lists'),
]


def get_settings_ui_paths(config_data=None):
    """Yield (ui_path, page_id, label) for all settings pages.

    If *config_data* is provided, also yields network-specific paths.
    """
    def _walk(pages, prefix='settings'):
        for suffix, pid, label, children in pages:
            path = prefix + '.' + suffix
            yield path, pid, label
            if children:
                yield from _walk(children, prefix)
    yield from _walk(SETTINGS_PAGES)
    # Network pages (dynamic from config)
    if config_data:
        networks = config_data.get('networks') or {}
        for netkey in networks:
            base = 'settings.networks.' + netkey.lower()
            yield base, 'networks.' + netkey, 'Networks > %s' % netkey
            for sub, label in NETWORK_SUB_PAGES:
                yield base + '.' + sub, 'networks.%s.%s' % (netkey, sub), 'Networks > %s > %s' % (netkey, label)
    # Plugin config pages (dynamic from loaded plugins + saved config).
    # Imported here rather than at module level: it is the one thing this file
    # needs from a page module, and hoisting it would put the cost back.
    if config_data:
        from settings.page_plugin_config import get_plugin_names
        for pname in get_plugin_names(config_data):
            path = 'settings.plugins.' + pname.lower()
            yield path, 'plugin_config_' + pname, 'Plugins > %s' % pname
