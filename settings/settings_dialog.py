from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QStackedWidget, QPushButton, QDialogButtonBox, QInputDialog,
    QMessageBox, QMenu, QLabel, QWidget, QFrame, QScrollArea,
    QStyledItemDelegate, QStyle, QLineEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QAbstractSpinBox,
)
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QShortcut, QKeySequence, QPalette, QColor, QPen, QBrush
import copy
from ruamel.yaml.comments import CommentedMap
import state

from settings.page_general import GeneralPage
from settings.page_identity import IdentityPage
from settings.page_font import (BaseColorsPage, ChatFontPage, TabFontPage,
    MenuFontPage, TreeFontPage, NicklistFontPage, ToolbarFontPage,
    SettingsFontPage, EditorFontPage)
from settings.page_ident_server import IdentServerPage
from settings.page_logging import LoggingPage
from settings.page_network import NetworkPage
from settings.page_server import ServerPage
from settings.page_sasl import SASLPage
from settings.page_autojoin import AutoJoinPage
from settings.page_lists import ListsPage
from settings.page_plugin_config import SinglePluginConfigPage, get_plugin_names
from settings.page_link_preview import LinkPreviewPage
from settings.page_nick_colors import NickColorsPage
from settings.page_notifications import NotificationsPage
from settings.page_scripts import ScriptsPage
from settings.page_file_editor import FileEditorPage


# Item data role for storing page type / network key
ROLE_PAGE = Qt.ItemDataRole.UserRole
ROLE_NETKEY = Qt.ItemDataRole.UserRole + 1

# Settings tree structure: (ui_path_suffix, page_id, label, children)
# Used for both building the tree and registering --ui paths.
SETTINGS_PAGES = [
    ('general', 'general', 'General', []),
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
    ('identserver', 'ident_server', 'Ident Server', []),
    ('logging', 'logging', 'Logging', []),
    ('notifications', 'notifications', 'Notifications', []),
    ('linkpreview', 'link_preview', 'Link Previews', []),
    ('scripts', 'scripts', 'Scripts', []),
    ('pluginconfig', 'plugin_config', 'Plugins', []),
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


class _FlatSelectionDelegate(QStyledItemDelegate):
    """Item delegate that draws a flat selection rectangle instead of the
    native rounded one used by the Windows 11 style."""

    def __init__(self, sel_bg=None, sel_fg=None, parent=None):
        super().__init__(parent)
        self._sel_bg = sel_bg
        self._sel_fg = sel_fg

    def set_colors(self, sel_bg, sel_fg):
        self._sel_bg = sel_bg
        self._sel_fg = sel_fg

    def paint(self, painter, option, index):
        # Draw selection background ourselves, then let the base draw text
        if option.state & QStyle.StateFlag.State_Selected:
            bg = self._sel_bg or option.palette.color(QPalette.ColorRole.Highlight)
            fg = self._sel_fg or option.palette.color(QPalette.ColorRole.HighlightedText)
            # Fill the full row width (including indentation area to the left)
            full_rect = option.rect.__class__(option.rect)
            full_rect.setX(0)
            vp = self.parent().viewport() if self.parent() else None
            if vp:
                full_rect.setWidth(vp.width())
            painter.fillRect(full_rect, QBrush(bg))
            # Override the palette so base class draws text in our color
            option.palette.setColor(QPalette.ColorRole.HighlightedText, fg)
            # Remove the State_Selected flag so the base class doesn't
            # draw its own selection background (the rounded rectangle)
            option.state &= ~QStyle.StateFlag.State_Selected
            option.palette.setColor(QPalette.ColorRole.Text, fg)
        super().paint(painter, option, index)


class SettingsDialog(QDialog):
    """Configuration dialog with tree navigation and stacked pages."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(700, 500)
        QShortcut(QKeySequence("Ctrl+F4"), self, self.reject)
        QShortcut(QKeySequence("Ctrl+W"), self, self.reject)
        self.config = config
        # Deep copy the YAML data so we only write back on OK/Apply
        self._original_data = copy.deepcopy(config._data)
        self._data = copy.deepcopy(config._data)
        self._applied = False  # True when Apply used without Save

        # --- layout ---
        main_layout = QHBoxLayout(self)

        # Apply settings dialog colors
        self._apply_colors()

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(180)
        self.tree.setMaximumWidth(220)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
        self._tree_delegate = _FlatSelectionDelegate(parent=self.tree)
        self.tree.setItemDelegate(self._tree_delegate)
        self._apply_tree_colors()
        self.tree.setFrameShape(QFrame.Shape.StyledPanel)
        main_layout.addWidget(self.tree)

        # Right side: stacked pages + buttons
        right = QVBoxLayout()
        self.page_title = QLabel()
        self.page_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        right.addWidget(self.page_title)

        self.stack = QStackedWidget()
        right.addWidget(self.stack)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        self._btn_apply = QPushButton("Apply")
        self._btn_apply.setToolTip("Apply changes to the running UI (does not save to disk)")
        buttons.addButton(self._btn_apply, QDialogButtonBox.ButtonRole.ApplyRole)
        self._btn_apply.clicked.connect(self._on_apply)
        self._btn_save_apply = QPushButton("Save && Apply")
        self._btn_save_apply.setToolTip("Save to disk and apply changes")
        buttons.addButton(self._btn_save_apply, QDialogButtonBox.ButtonRole.ApplyRole)
        self._btn_save_apply.clicked.connect(self._on_save_and_apply)
        right.addWidget(buttons)

        main_layout.addLayout(right, 1)

        # --- pages ---
        self._pages = {}        # page_id -> widget (actual page for save/load)
        self._stack_widgets = {} # page_id -> widget in the stack (may be a scroll area)
        self._net_pages = {}    # (net_key, sub) -> widget   sub in ('net','server','sasl','autojoin')

        from dialogs import install_input_focus_handler
        install_input_focus_handler(self)

        self._build_global_pages()
        self._build_network_tree()
        self._build_plugin_config_tree()

        self.tree.currentItemChanged.connect(self._on_tree_select)
        # Select first item
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))



    def _apply_colors(self):
        """Apply settings dialog colors and font if explicitly configured."""
        cfg = self.config
        colors = (cfg._data.get('colors') or {})
        settings = colors.get('settings') or {}
        parts = []
        if settings.get('foreground') or settings.get('background'):
            if cfg.settings_bgcolor:
                parts.append("background-color: %s;" % cfg.settings_bgcolor.name())
            if cfg.settings_fgcolor:
                parts.append("color: %s;" % cfg.settings_fgcolor.name())
        if cfg.settings_font_family:
            parts.append("font-family: '%s';" % cfg.settings_font_family)
        if cfg.settings_font_size:
            parts.append("font-size: %dpt;" % cfg.settings_font_size)
        if parts:
            self.setStyleSheet("SettingsDialog { %s }" % ' '.join(parts))

    def _apply_tree_colors(self):
        """Apply settings tree colors and selection colors."""
        cfg = self.config
        parts = []
        # Base tree colors
        tree_css = []
        if cfg.settings_tree_bgcolor:
            tree_css.append("background-color: %s;" % cfg.settings_tree_bgcolor.name())
        if cfg.settings_tree_fgcolor:
            tree_css.append("color: %s;" % cfg.settings_tree_fgcolor.name())
        if tree_css:
            parts.append("QTreeWidget { %s }" % ' '.join(tree_css))
        if parts:
            self.tree.setStyleSheet(' '.join(parts))
        # Selection colors via delegate (bypasses native rounded rectangle)
        sel_bg = QColor(cfg.settings_tree_sel_bgcolor if cfg.settings_tree_sel_bgcolor else '#3875d7')
        sel_fg = QColor(cfg.settings_tree_sel_fgcolor if cfg.settings_tree_sel_fgcolor else '#ffffff')
        self._tree_delegate.set_colors(sel_bg, sel_fg)

    # ----- global pages -----

    def _add_page(self, page_id, label, widget, stack_widget=None):
        self._pages[page_id] = widget
        if stack_widget:
            sw = stack_widget
        else:
            # Wrap in a top-aligned container to prevent vertical stretching
            sw = QWidget()
            wrapper = QVBoxLayout(sw)
            wrapper.setContentsMargins(0, 0, 0, 0)
            wrapper.addWidget(widget)
            wrapper.addStretch(1)
        self._stack_widgets[page_id] = sw
        self.stack.addWidget(sw)
        return widget

    def _build_global_pages(self):
        general = GeneralPage()
        self._add_page('general', 'General', general)
        self._add_page('identity', 'Identity', IdentityPage())
        self._add_page('lists', 'Lists', ListsPage())
        # Font / Colors parent — base foreground/background colors
        base_colors = BaseColorsPage()
        self._add_page('font_root', 'Font / Colors', base_colors)
        # Font / Colors sub-pages
        for pid, cls in [('font_chat', ChatFontPage), ('font_tab', TabFontPage),
                         ('font_menu', MenuFontPage), ('font_tree', TreeFontPage),
                         ('font_nicklist', NicklistFontPage),
                         ('font_toolbar', ToolbarFontPage), ('font_settings', SettingsFontPage),
                         ('font_editor', EditorFontPage)]:
            page = cls()
            self._add_page(pid, '', page)
        self._add_page('ident_server', 'Ident Server', IdentServerPage())
        self._add_page('logging', 'Logging', LoggingPage())
        self._add_page('notifications', 'Notifications', NotificationsPage())

        for pid, page in self._pages.items():
            page.load_from_data(self._data)

        # Scripts / Plugins page
        import os
        config_dir = os.path.dirname(os.path.abspath(self.config.path))

        scripts_page = ScriptsPage()
        scripts_page.setup(config_dir)
        scripts_page.load_from_data(self._data)
        self._add_page('scripts', 'Scripts', scripts_page, stack_widget=scripts_page)

        # Plugins — show only the plugins section of the scripts page
        # Scripts — show only the scripts section
        self._plugin_config_pages = {}
        # plugins_group was built by ScriptsPage but not added to its layout
        pc_widget = QWidget()
        pc_layout = QVBoxLayout(pc_widget)
        pc_layout.setContentsMargins(0, 0, 0, 0)
        pc_layout.addWidget(scripts_page.plugins_group, 1)
        pc_layout.addWidget(QLabel("Select a plugin below for per-plugin settings."))
        self._stack_widgets['plugin_config'] = pc_widget
        self._pages['plugin_config'] = scripts_page  # save still goes through scripts_page
        self.stack.addWidget(pc_widget)

        link_preview_page = LinkPreviewPage()
        link_preview_page.load_from_data(self._data)
        self._add_page('link_preview', 'Link Previews', link_preview_page)

        nick_colors_page = NickColorsPage()
        nick_colors_page.load_from_data(self._data)
        self._add_page('nick_colors', 'Nick Colors', nick_colors_page, stack_widget=nick_colors_page)

        # File editor page (handles config, startup, popups, toolbar, variables)
        config_path = os.path.abspath(self.config.path)

        def _resolve(key):
            name = self._data.get(key, '')
            if not name:
                return ''
            if os.path.isabs(name):
                return name
            return os.path.join(config_dir, name)

        def _resolve_startup():
            scripts_cfg = self._data.get('scripts') or {}
            name = scripts_cfg.get('startup', '')
            if not name:
                return ''
            if os.path.isabs(name):
                return name
            scripts_dir = scripts_cfg.get('dir', 'scripts')
            if not os.path.isabs(scripts_dir):
                scripts_dir = os.path.join(config_dir, scripts_dir)
            return os.path.join(scripts_dir, name)

        self._editor_page = FileEditorPage()
        self._editor_page.setup(config_dir, config_path,
                          startup_path=_resolve_startup(),
                          popups_path=_resolve('popups_file'),
                          toolbar_path=_resolve('toolbar_file'),
                          variables_path=_resolve('variables_file'))
        self._add_page('editor', 'File Editor', self._editor_page, stack_widget=self._editor_page)

        # Tree items from SETTINGS_PAGES structure
        def _build_tree(parent, pages):
            for suffix, pid, label, children in pages:
                item = QTreeWidgetItem(parent, [label])
                item.setData(0, ROLE_PAGE, pid)
                if children:
                    item.setExpanded(True)
                    _build_tree(item, children)
        _build_tree(self.tree, SETTINGS_PAGES)

    # ----- network tree -----

    def _build_network_tree(self):
        self._networks_root = QTreeWidgetItem(self.tree, ["Networks"])
        self._networks_root.setData(0, ROLE_PAGE, '__networks_root__')
        self._networks_root.setExpanded(True)

        networks = self._data.get('networks') or {}
        for netkey in networks:
            self._add_network_node(netkey)

    def _add_network_node(self, netkey):
        net_data = (self._data.get('networks') or {}).get(netkey)
        if net_data is None:
            net_data = CommentedMap()
            self._data['networks'][netkey] = net_data

        node = QTreeWidgetItem(self._networks_root, [netkey])
        node.setData(0, ROLE_PAGE, 'network')
        node.setData(0, ROLE_NETKEY, netkey)
        node.setExpanded(True)

        # Sub-pages
        for sub_id, sub_label in NETWORK_SUB_PAGES:
            child = QTreeWidgetItem(node, [sub_label])
            child.setData(0, ROLE_PAGE, sub_id)
            child.setData(0, ROLE_NETKEY, netkey)

        # Create page widgets
        net_page = NetworkPage()
        net_page.load_from_data(net_data)
        self._net_pages[(netkey, 'net')] = net_page
        self.stack.addWidget(net_page)

        srv_page = ServerPage()
        srv_page.load_from_data(net_data)
        self._net_pages[(netkey, 'server')] = srv_page
        self.stack.addWidget(srv_page)

        sasl_page = SASLPage()
        sasl_page.load_from_data(net_data)
        self._net_pages[(netkey, 'sasl')] = sasl_page
        self.stack.addWidget(sasl_page)

        aj_page = AutoJoinPage()
        aj_page.load_from_data(net_data)
        self._net_pages[(netkey, 'autojoin')] = aj_page
        self.stack.addWidget(aj_page)

        lists_page = ListsPage(level='network')
        lists_page.load_from_data(net_data)
        self._net_pages[(netkey, 'lists')] = lists_page
        self.stack.addWidget(lists_page)

        return node

    # ----- tree selection -----

    # Map YAML config paths to settings page IDs
    _PAGE_ALIASES = {
        'ident': 'ident_server',
        'colors': 'font_root',
        'font': 'font_root',
    }
    _SUB_ALIASES = {
        'auto_join': 'autojoin',
    }

    def select_page(self, page_id):
        """Select a page by its id.

        Supports both simple ids ('general', 'font') and network paths
        like 'networks.libera' or 'networks.libera.sasl'.
        Also accepts YAML config paths (e.g. 'ident' for 'ident_server',
        'auto_join' for 'autojoin', 'colors' for 'font').
        """
        # Handle network paths: networks.<key>[.<subpage>]
        if page_id.startswith('networks.'):
            parts = page_id.split('.', 2)  # ['networks', key] or ['networks', key, sub]
            netkey = parts[1] if len(parts) >= 2 else None
            sub = parts[2] if len(parts) >= 3 else None
            if sub:
                sub = self._SUB_ALIASES.get(sub, sub)
            if netkey:
                # Find the network node in the tree
                for i in range(self._networks_root.childCount()):
                    node = self._networks_root.child(i)
                    if node.data(0, ROLE_NETKEY) == netkey:
                        if sub and sub in ('server', 'sasl', 'autojoin'):
                            for j in range(node.childCount()):
                                child = node.child(j)
                                if child.data(0, ROLE_PAGE) == sub:
                                    self.tree.setCurrentItem(child)
                                    return
                        else:
                            self.tree.setCurrentItem(node)
                            return
            return

        page_id = self._PAGE_ALIASES.get(page_id, page_id)
        def _find(parent, pid):
            for i in range(parent.childCount() if hasattr(parent, 'childCount') else parent.topLevelItemCount()):
                item = parent.child(i) if hasattr(parent, 'child') else parent.topLevelItem(i)
                if item.data(0, ROLE_PAGE) == pid:
                    return item
                found = _find(item, pid)
                if found:
                    return found
            return None
        item = _find(self.tree, page_id)
        if item:
            self.tree.setCurrentItem(item)

    def _build_plugin_config_tree(self):
        """Build Plugin Config parent node with per-plugin child pages."""
        plugin_names = get_plugin_names(self._data)
        if not plugin_names:
            return
        # Find the Plugin Config tree item
        root = None
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, ROLE_PAGE) == 'plugin_config':
                root = item
                break
        if not root:
            return
        root.setExpanded(True)
        for pname in plugin_names:
            child = QTreeWidgetItem(root, [pname])
            page_id = 'plugin_config_' + pname
            child.setData(0, ROLE_PAGE, page_id)
            page = SinglePluginConfigPage(pname)
            page.load_from_data(self._data)
            self._plugin_config_pages[pname] = page
            self._pages[page_id] = page
            self._stack_widgets[page_id] = page
            self.stack.addWidget(page)

    def _on_tree_select(self, current, previous):
        if not current:
            return
        page_id = current.data(0, ROLE_PAGE)
        netkey = current.data(0, ROLE_NETKEY)

        self.page_title.setText(current.text(0))

        if page_id in self._stack_widgets:
            self.stack.setCurrentWidget(self._stack_widgets[page_id])
        elif page_id == 'network' and netkey:
            w = self._net_pages.get((netkey, 'net'))
            if w:
                self.stack.setCurrentWidget(w)
        elif page_id in ('server', 'sasl', 'autojoin', 'lists') and netkey:
            w = self._net_pages.get((netkey, page_id))
            if w:
                self.stack.setCurrentWidget(w)
        elif page_id == '__networks_root__':
            # Show a placeholder
            lbl = self._pages.get('__net_placeholder__')
            if not lbl:
                lbl = QLabel("Right-click to add a network.\nSelect a network to edit it.")
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._pages['__net_placeholder__'] = lbl
                self.stack.addWidget(lbl)
            self.stack.setCurrentWidget(lbl)
        self._refresh_page_swatches()

    def _refresh_page_swatches(self):
        """Update color swatches on the currently visible page."""
        from settings.page_font import _ColorRow
        w = self.stack.currentWidget()
        if w:
            for row in w.findChildren(_ColorRow):
                row._update_swatch()

    # ----- context menu -----

    def _tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        page_id = item.data(0, ROLE_PAGE)
        netkey = item.data(0, ROLE_NETKEY)

        menu = QMenu(self)

        if page_id == '__networks_root__':
            menu.addAction("Add network", self._add_network)
        elif page_id == 'network' and netkey:
            menu.addAction("Rename", lambda: self._rename_network(item, netkey))
            menu.addAction("Delete", lambda: self._delete_network(item, netkey))

        if menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _add_network(self):
        name, ok = QInputDialog.getText(self, "Add Network", "Network key:")
        if not ok or not name.strip():
            return
        name = name.strip()
        nets = self._data.get('networks')
        if nets is None:
            self._data['networks'] = CommentedMap()
            nets = self._data['networks']
        if name in nets:
            QMessageBox.warning(self, "Duplicate", "Network '%s' already exists." % name)
            return
        nets[name] = CommentedMap()
        node = self._add_network_node(name)
        self.tree.setCurrentItem(node)

    def _rename_network(self, item, old_key):
        new_key, ok = QInputDialog.getText(self, "Rename Network", "New key:", text=old_key)
        if not ok or not new_key.strip() or new_key.strip() == old_key:
            return
        new_key = new_key.strip()
        nets = self._data['networks']
        if new_key in nets:
            QMessageBox.warning(self, "Duplicate", "Network '%s' already exists." % new_key)
            return
        # Move data
        net_data = nets[old_key]
        nets[new_key] = net_data
        del nets[old_key]
        # Update tree
        item.setText(0, new_key)
        item.setData(0, ROLE_NETKEY, new_key)
        for i in range(item.childCount()):
            item.child(i).setData(0, ROLE_NETKEY, new_key)
        # Re-key page widgets
        for sub in ('net', 'server', 'sasl', 'autojoin', 'lists'):
            w = self._net_pages.pop((old_key, sub), None)
            if w:
                self._net_pages[(new_key, sub)] = w

    def _delete_network(self, item, netkey):
        r = QMessageBox.question(self, "Delete Network",
                                  "Delete network '%s'?" % netkey,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        nets = self._data['networks']
        if netkey in nets:
            del nets[netkey]
        # Remove page widgets
        for sub in ('net', 'server', 'sasl', 'autojoin', 'lists'):
            w = self._net_pages.pop((netkey, sub), None)
            if w:
                self.stack.removeWidget(w)
                w.deleteLater()
        # Remove tree node
        parent = item.parent()
        if parent:
            parent.removeChild(item)

    # ----- save / apply -----

    def _collect_all(self):
        """Save all page widgets back into self._data."""
        for pid, page in self._pages.items():
            if hasattr(page, 'save_to_data'):
                page.save_to_data(self._data)

        networks = self._data.get('networks') or {}
        for netkey in list(networks.keys()):
            net_data = networks[netkey]
            for sub, method in [('net', 'save_to_data'), ('server', 'save_to_data'),
                                ('sasl', 'save_to_data'), ('autojoin', 'save_to_data'),
                                ('lists', 'save_to_data')]:
                w = self._net_pages.get((netkey, sub))
                if w:
                    w.save_to_data(net_data if sub == 'net' else net_data)

    def _apply_to_ui(self, data, visible_only=True):
        """Re-init config from data and refresh the running UI (no disk save)."""
        from qtpyrc import (_build_app_stylesheet, _apply_palette,
                            _refresh_all_window_fonts,
                            _get_message_colors, _recolor_chat_text,
                            _refresh_navigation)
        # Snapshot old message colors before reinit
        old_colors = _get_message_colors()
        self.config._data = data
        self.config.__init__(self.config.path, data, self.config._yaml)
        from config import _update_text_formats
        _update_text_formats(self.config)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(_build_app_stylesheet())
        _apply_palette()
        _refresh_all_window_fonts()
        _refresh_navigation()
        _recolor_chat_text(old_colors, visible_only=visible_only)
        from tabbar import TabbedWorkspace
        ws = state.app.mainwin.workspace
        if isinstance(ws, TabbedWorkspace):
            ws._load_colors()
            for entry in ws._tabs:
                ws._style_tab(entry)
        from toolbar import reload_toolbar
        reload_toolbar()
        # Refresh live client connection settings from updated config
        for client in state.clients:
            client.refresh_server_config()

    def _on_apply(self):
        """Apply changes to the running UI without saving to disk."""
        self._collect_all()
        self._apply_to_ui(self._data, visible_only=False)
        self._applied = True
        # Refresh settings dialog's own appearance
        self._apply_colors()
        self._apply_tree_colors()

    def _on_save_and_apply(self):
        """Save to disk and apply changes to the running UI."""
        self._on_apply()
        self.config.save()
        self._original_data = copy.deepcopy(self._data)
        self._applied = False  # saved — nothing to revert
        import popups
        popups.load()
        # Refresh network settings paths in the /ui registry
        from qtpyrc import _register_settings_paths
        _register_settings_paths()

    def _on_ok(self):
        if not self._editor_page._check_unsaved():
            return
        self._on_save_and_apply()
        self.accept()

    def _has_unsaved_changes(self):
        """Check if any settings have been modified from the original."""
        self._collect_all()
        return self._data != self._original_data

    def reject(self):
        """Handle Cancel/Ctrl+F4."""
        if not self._editor_page._check_unsaved():
            return
        if self._has_unsaved_changes():
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "Settings have been modified. Save before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel)
            if reply == QMessageBox.StandardButton.Save:
                self._on_save_and_apply()
                self.accept()
                return
            elif reply == QMessageBox.StandardButton.Cancel:
                return
        # Revert UI if Apply was used without Save
        if self._applied:
            self._apply_to_ui(self._original_data, visible_only=False)
        super().reject()
