import os
import glob as _glob

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QFormLayout, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QHBoxLayout,
    QFileDialog, QInputDialog, QCheckBox, QLabel, QComboBox,
)
from PySide6.QtCore import Qt


_ROLE_EXTERNAL = Qt.ItemDataRole.UserRole + 10
_ROLE_VALUE = Qt.ItemDataRole.UserRole + 11
_ROLE_EXT_DISPLAY = Qt.ItemDataRole.UserRole + 12
_ROLE_EXT_TOOLTIP = Qt.ItemDataRole.UserRole + 13
_ROLE_EXT_CHECKED = Qt.ItemDataRole.UserRole + 14


class _ReorderableList(QListWidget):
    """QListWidget that restores external-item widgets after drag-and-drop."""

    def dropEvent(self, event):
        # Save checked state of external items before the move destroys widgets
        for i in range(self.count()):
            item = self.item(i)
            if item.data(_ROLE_EXTERNAL):
                w = self.itemWidget(item)
                if w and hasattr(w, '_checkbox'):
                    item.setData(_ROLE_EXT_CHECKED, w._checkbox.isChecked())
        super().dropEvent(event)
        # Rebuild widgets on external items that lost them
        page = self.parent()
        while page and not isinstance(page, ScriptsPage):
            page = page.parent()
        if not page:
            return
        for i in range(self.count()):
            item = self.item(i)
            if item.data(_ROLE_EXTERNAL) and not self.itemWidget(item):
                page._restore_external_widget(self, item)


class ScriptsPage(QWidget):
    """Settings page for Python plugins and command scripts auto-loading."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Plugins section ---
        self.plugins_group = pg = QGroupBox("Python Plugins")
        pl = QVBoxLayout(pg)
        pl.setContentsMargins(4, 4, 4, 4)

        dir_row = QHBoxLayout()
        self._plugins_dir_label = QLabel("Directory:")
        dir_row.addWidget(self._plugins_dir_label)
        self.plugins_dir = QLineEdit()
        self.plugins_dir.setPlaceholderText("plugins%s (default)" % os.sep)
        self.plugins_dir.setToolTip(
            "Directory for Python plugins (relative to config file)")
        dir_row.addWidget(self.plugins_dir)
        pl.addLayout(dir_row)

        hint = QLabel("Checked = auto-loaded on startup.")
        from settings import SETTINGS_HINT_STYLE
        hint.setStyleSheet(SETTINGS_HINT_STYLE)
        pl.addWidget(hint)

        self.plugins_list = self._make_reorderable_list()
        self.plugins_list.itemChanged.connect(
            lambda item: self._style_local_item(item))
        pl.addWidget(self.plugins_list, 1)

        btns = QHBoxLayout()
        btn = QPushButton("Refresh")
        btn.setToolTip("Rescan the plugins directory")
        btn.clicked.connect(self._scan_plugins)
        btns.addWidget(btn)
        btn = QPushButton("Edit")
        btn.setToolTip("Open the selected plugin file in an editor")
        btn.clicked.connect(
            lambda: self._edit_selected(self.plugins_list, is_plugin=True))
        btns.addWidget(btn)
        btn = QPushButton("Add External...")
        btn.setToolTip("Add a plugin by module name (must be importable)")
        btn.clicked.connect(self._add_plugin)
        btns.addWidget(btn)
        btns.addStretch(1)
        btn = QPushButton("\u25b2")
        btn.setFixedWidth(28)
        btn.setToolTip("Move selected item up")
        btn.clicked.connect(lambda: self._move_item(self.plugins_list, -1))
        btns.addWidget(btn)
        btn = QPushButton("\u25bc")
        btn.setFixedWidth(28)
        btn.setToolTip("Move selected item down")
        btn.clicked.connect(lambda: self._move_item(self.plugins_list, 1))
        btns.addWidget(btn)
        pl.addLayout(btns)

        # plugins_group is NOT added to this layout — it gets moved to the
        # Plugins page by the settings dialog.

        # --- Command Scripts section ---
        self.scripts_group = sg = QGroupBox("Command Scripts")
        sl = QVBoxLayout(sg)
        sl.setContentsMargins(4, 4, 4, 4)

        dir_row2 = QHBoxLayout()
        self._scripts_dir_label = QLabel("Directory:")
        dir_row2.addWidget(self._scripts_dir_label)
        self.scripts_dir = QLineEdit()
        self.scripts_dir.setPlaceholderText("scripts%s (default)" % os.sep)
        self.scripts_dir.setToolTip(
            "Directory for command scripts (relative to config file)")
        dir_row2.addWidget(self.scripts_dir)
        sl.addLayout(dir_row2)

        hint = QLabel("Checked = auto-run on startup. The startup script is set in\n"
                      "config.yaml under scripts.startup (edit via File Editor).")
        from settings import SETTINGS_HINT_STYLE
        hint.setStyleSheet(SETTINGS_HINT_STYLE)
        sl.addWidget(hint)

        self.scripts_list = self._make_reorderable_list()
        self.scripts_list.itemChanged.connect(
            lambda item: self._style_local_item(item))
        sl.addWidget(self.scripts_list, 1)

        btns = QHBoxLayout()
        btn = QPushButton("Refresh")
        btn.setToolTip("Rescan the scripts directory")
        btn.clicked.connect(self._scan_scripts)
        btns.addWidget(btn)
        btn = QPushButton("Edit")
        btn.setToolTip("Open the selected script file in an editor")
        btn.clicked.connect(
            lambda: self._edit_selected(self.scripts_list, is_plugin=False))
        btns.addWidget(btn)
        btn = QPushButton("Add External...")
        btn.setToolTip("Add a script by file path")
        btn.clicked.connect(self._add_script)
        btns.addWidget(btn)
        btns.addStretch(1)
        btn = QPushButton("\u25b2")
        btn.setFixedWidth(28)
        btn.setToolTip("Move selected item up")
        btn.clicked.connect(lambda: self._move_item(self.scripts_list, -1))
        btns.addWidget(btn)
        btn = QPushButton("\u25bc")
        btn.setFixedWidth(28)
        btn.setToolTip("Move selected item down")
        btn.clicked.connect(lambda: self._move_item(self.scripts_list, 1))
        btns.addWidget(btn)
        sl.addLayout(btns)

        layout.addWidget(sg, 1)

        # --- Editor selection ---
        editor_row = QHBoxLayout()
        editor_row.addWidget(QLabel("Open with:"))
        self._editor_combo = QComboBox()
        self._editor_combo.setMinimumWidth(200)
        editor_row.addWidget(self._editor_combo, 1)
        layout.addLayout(editor_row)
        self._populate_editors()

        self._config_dir = ''
        self._startup_file = ''
        self._auto_load_plugins = set()
        self._auto_load_plugins_ordered = []
        self._auto_load_scripts = set()
        self._auto_load_scripts_ordered = []

    def _make_reorderable_list(self):
        """Create a QListWidget with drag-and-drop reordering."""
        lw = _ReorderableList(self)
        lw.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        lw.setDefaultDropAction(Qt.DropAction.MoveAction)
        return lw

    def _move_item(self, listwidget, direction):
        """Move the selected item up (-1) or down (+1)."""
        row = listwidget.currentRow()
        if row < 0:
            return
        new_row = row + direction
        if new_row < 0 or new_row >= listwidget.count():
            return
        item = listwidget.takeItem(row)
        # Save checked state for external items before widget is lost
        is_ext = item.data(_ROLE_EXTERNAL)
        if is_ext:
            old_w = listwidget.itemWidget(item)
            if old_w and hasattr(old_w, '_checkbox'):
                item.setData(_ROLE_EXT_CHECKED, old_w._checkbox.isChecked())
        listwidget.insertItem(new_row, item)
        if is_ext:
            self._restore_external_widget(listwidget, item)
        listwidget.setCurrentRow(new_row)

    def _restore_external_widget(self, listwidget, item):
        """Rebuild the checkbox + X widget on an external item."""
        value = item.data(_ROLE_VALUE)
        display = item.data(_ROLE_EXT_DISPLAY) or value
        tooltip = item.data(_ROLE_EXT_TOOLTIP) or ''
        checked = bool(item.data(_ROLE_EXT_CHECKED))
        if listwidget is self.plugins_list:
            on_delete = lambda v=value: self._remove_plugin_by_value(v)
        else:
            on_delete = lambda v=value: self._remove_script_by_value(v)
        widget = self._make_external_row(display, tooltip, checked, on_delete)
        item.setSizeHint(widget.sizeHint())
        listwidget.setItemWidget(item, widget)

    def setup(self, config_dir):
        self._config_dir = config_dir

    def _update_dir_status(self, label, text_field, default):
        """Update a directory label to indicate directory status."""
        configured = bool(text_field.text().strip())
        d = self._abs_dir(text_field.text(), default)
        exists = os.path.isdir(d)
        if configured and exists:
            label.setText("Directory:")
            label.setStyleSheet("")
        elif configured and not exists:
            label.setText("Directory (not found):")
            label.setStyleSheet("color: red;")
        elif not configured and exists:
            label.setText("Directory (default):")
            label.setStyleSheet("")
        else:
            label.setText("Directory (default, not found):")
            label.setStyleSheet("color: red;")

    def _abs_dir(self, text, default=''):
        """Resolve a directory path relative to config dir."""
        d = text.strip() or default
        if not d:
            return self._config_dir
        if os.path.isabs(d):
            return d
        return os.path.join(self._config_dir, d)

    def _expand_patterns(self, names, directory, is_plugin=False):
        """Expand glob patterns in an auto_load list to concrete names."""
        result = set()
        for entry in names:
            entry = str(entry).strip()
            if not entry:
                continue
            if any(c in entry for c in ('*', '?', '[')):
                if not os.path.isdir(directory):
                    continue
                if is_plugin:
                    pat = entry if entry.endswith('.py') else entry + '.py'
                    for path in _glob.glob(os.path.join(directory, pat)):
                        base = os.path.basename(path)
                        if not base.startswith('_') and os.path.isfile(path):
                            result.add(base[:-3])
                    for path in _glob.glob(os.path.join(directory, entry)):
                        base = os.path.basename(path)
                        if (not base.startswith('_') and os.path.isdir(path)
                                and os.path.isfile(
                                    os.path.join(path, '__init__.py'))):
                            result.add(base)
                else:
                    for path in _glob.glob(os.path.join(directory, entry)):
                        base = os.path.basename(path)
                        if not base.startswith('_') and os.path.isfile(path):
                            result.add(base)
            else:
                result.add(entry)
        return result

    def _is_in_dir(self, name, directory, is_plugin=False):
        """Check whether a name corresponds to a file in the directory."""
        if os.path.isabs(name) or os.sep in name or '/' in name:
            return False
        if not os.path.isdir(directory):
            return False
        if is_plugin:
            py = os.path.join(directory, name + '.py')
            pkg = os.path.join(directory, name, '__init__.py')
            return os.path.isfile(py) or os.path.isfile(pkg)
        return os.path.isfile(os.path.join(directory, name))

    # -- styling --

    def _style_local_item(self, item):
        """Bold checked local items, normal unchecked ones."""
        if item.data(_ROLE_EXTERNAL):
            return
        checked = item.checkState() == Qt.CheckState.Checked
        font = item.font()
        font.setBold(checked)
        item.setFont(font)

    def _apply_bold(self, checkbox, checked):
        """Bold checked external checkboxes."""
        font = checkbox.font()
        font.setBold(checked)
        checkbox.setFont(font)

    # -- helpers --

    def _is_item_checked(self, listwidget, item):
        """Get check state from either a plain item or a widget-item."""
        w = listwidget.itemWidget(item)
        if w and hasattr(w, '_checkbox'):
            return w._checkbox.isChecked()
        return item.checkState() == Qt.CheckState.Checked

    def _set_item_checked(self, listwidget, item, checked):
        w = listwidget.itemWidget(item)
        if w and hasattr(w, '_checkbox'):
            w._checkbox.setChecked(checked)
        else:
            item.setCheckState(
                Qt.CheckState.Checked if checked
                else Qt.CheckState.Unchecked)

    def _get_checked(self, listwidget):
        """Return the set of currently checked values."""
        checked = set()
        for i in range(listwidget.count()):
            item = listwidget.item(i)
            if self._is_item_checked(listwidget, item):
                checked.add(item.data(_ROLE_VALUE))
        return checked

    def _make_external_row(self, display, tooltip, checked, on_delete):
        """Create a widget with checkbox + X button for an external entry."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(4)
        cb = QCheckBox(display)
        cb.setChecked(checked)
        self._apply_bold(cb, checked)
        cb.toggled.connect(lambda c: self._apply_bold(cb, c))
        if tooltip:
            cb.setToolTip(tooltip)
        h.addWidget(cb, 1)
        btn = QPushButton("\u00d7")
        btn.setFixedWidth(22)
        btn.setFlat(True)
        btn.setToolTip("Remove from list")
        btn.clicked.connect(on_delete)
        h.addWidget(btn)
        w._checkbox = cb
        return w

    def _add_external_item(self, listwidget, value, display, tooltip,
                           checked, on_delete):
        """Add an external entry with checkbox + X button to a list."""
        item = QListWidgetItem()
        item.setData(_ROLE_EXTERNAL, True)
        item.setData(_ROLE_VALUE, value)
        item.setData(_ROLE_EXT_DISPLAY, display)
        item.setData(_ROLE_EXT_TOOLTIP, tooltip)
        listwidget.addItem(item)
        widget = self._make_external_row(
            display, tooltip, checked, on_delete)
        item.setSizeHint(widget.sizeHint())
        listwidget.setItemWidget(item, widget)
        return item

    # -- edit --

    def _resolve_edit_path(self, value, is_plugin):
        """Resolve a list entry value to an editable file path."""
        if os.path.isabs(value) and os.path.isfile(value):
            return value
        if is_plugin:
            pdir = self._abs_dir(self.plugins_dir.text(), 'plugins')
            py = os.path.join(pdir, value + '.py')
            if os.path.isfile(py):
                return py
            init = os.path.join(pdir, value, '__init__.py')
            if os.path.isfile(init):
                return init
        else:
            sdir = self._abs_dir(self.scripts_dir.text(), 'scripts')
            path = os.path.join(sdir, value)
            if os.path.isfile(path):
                return path
        # Try as-is (relative with path separators)
        if os.path.isfile(value):
            return os.path.abspath(value)
        return None

    def _populate_editors(self):
        """Fill the editor dropdown with detected editors."""
        from settings.page_file_editor import get_available_editors
        self._editor_combo.clear()
        self._editor_combo.addItem("(system default)", "__system__")
        for name, exe in get_available_editors():
            self._editor_combo.addItem(name, exe)
        # Select the configured external editor if it's in the list
        import state
        current = getattr(state.config, 'external_editor', '') if state.config else ''
        if current:
            idx = self._editor_combo.findData(current)
            if idx >= 0:
                self._editor_combo.setCurrentIndex(idx)

    def _edit_selected(self, listwidget, is_plugin):
        """Open the selected item's file in the chosen editor."""
        item = listwidget.currentItem()
        if not item:
            return
        value = item.data(_ROLE_VALUE)
        path = self._resolve_edit_path(value, is_plugin)
        if not path:
            return
        editor = self._editor_combo.currentData()
        if editor and editor != '__system__':
            import subprocess
            try:
                subprocess.Popen([editor, path])
            except Exception:
                pass
        else:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # -- checklist population --

    @staticmethod
    def _build_auto_load_order(raw_entries, expanded):
        """Build an ordered list from raw config auto_load entries.

        Explicit (non-glob) entries keep their config position.
        Glob-expanded items that weren't explicitly listed are appended
        sorted alphabetically.
        """
        ordered = []
        seen = set()
        has_globs = False
        for entry in raw_entries:
            s = str(entry).strip()
            if any(c in s for c in ('*', '?', '[')):
                has_globs = True
            elif s in expanded and s not in seen:
                ordered.append(s)
                seen.add(s)
        if has_globs:
            for n in sorted(expanded):
                if n not in seen:
                    ordered.append(n)
                    seen.add(n)
        return ordered

    @staticmethod
    def _apply_order(names, auto_load_order, saved_order):
        """Order *names*: auto_load items first (in config order),
        then remaining items in ui.yaml saved order, then new items
        alphabetically.
        """
        name_set = set(names)
        auto_set = set(auto_load_order)
        result = []
        seen = set()
        # 1. Auto-load items in config order
        for n in auto_load_order:
            if n in name_set and n not in seen:
                result.append(n)
                seen.add(n)
        # 2. Remaining items in saved ui.yaml order
        for n in saved_order:
            if n in name_set and n not in seen:
                result.append(n)
                seen.add(n)
        # 3. New items not in either list, alphabetically
        for n in sorted(names):
            if n not in seen:
                result.append(n)
                seen.add(n)
        return result

    def _scan_plugins(self):
        import state
        self._update_dir_status(self._plugins_dir_label, self.plugins_dir, 'plugins')
        checked = self._get_checked(self.plugins_list)
        self.plugins_list.clear()
        pdir = self._abs_dir(self.plugins_dir.text(), 'plugins')

        # Local entries from directory
        local = []
        if os.path.isdir(pdir):
            for entry in sorted(os.listdir(pdir)):
                if entry.startswith('_') or entry.startswith('.'):
                    continue
                full = os.path.join(pdir, entry)
                if os.path.isfile(full) and entry.endswith('.py'):
                    local.append(entry[:-3])
                elif (os.path.isdir(full)
                      and os.path.isfile(os.path.join(full, '__init__.py'))):
                    local.append(entry)
        local_set = set(local)

        # External entries: auto_load items not in dir + recently used
        external = []
        seen = set()
        for name in self._auto_load_plugins:
            if name not in local_set and name not in seen:
                external.append(name)
                seen.add(name)
        if state.ui_state:
            import importlib.util
            for name in list(state.ui_state.recent_plugin_names):
                if name not in local_set and name not in seen:
                    try:
                        if importlib.util.find_spec(name) is None:
                            state.ui_state.remove_recent_plugin_name(name)
                            continue
                    except (ModuleNotFoundError, ValueError):
                        state.ui_state.remove_recent_plugin_name(name)
                        continue
                    external.append(name)
                    seen.add(name)

        # Apply order: auto_load first (config order), then saved ui order
        saved = state.ui_state.plugins_order if state.ui_state else []
        auto_order = list(self._auto_load_plugins_ordered)
        local = self._apply_order(local, auto_order, saved)
        external = self._apply_order(external, auto_order, saved)

        # Local items (plain checkable items, bold if checked)
        for name in local:
            is_checked = name in checked or name in self._auto_load_plugins
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(_ROLE_EXTERNAL, False)
            item.setData(_ROLE_VALUE, name)
            item.setCheckState(
                Qt.CheckState.Checked if is_checked
                else Qt.CheckState.Unchecked)
            font = item.font()
            font.setBold(is_checked)
            item.setFont(font)
            self.plugins_list.addItem(item)

        # External items (checkbox + X button)
        for name in external:
            is_checked = name in checked or name in self._auto_load_plugins
            self._add_external_item(
                self.plugins_list, name,
                name + "  (external)",
                "External plugin \u2014 not in plugins directory",
                is_checked,
                lambda n=name: self._remove_plugin_by_value(n))

    def _scan_scripts(self):
        import state
        self._update_dir_status(self._scripts_dir_label, self.scripts_dir, 'scripts')
        checked = self._get_checked(self.scripts_list)
        self.scripts_list.clear()
        sdir = self._abs_dir(self.scripts_dir.text(), 'scripts')

        startup_name = self._startup_file or ''

        local = []
        if os.path.isdir(sdir):
            for entry in sorted(os.listdir(sdir)):
                if entry.startswith('_') or entry.startswith('.'):
                    continue
                full = os.path.join(sdir, entry)
                if os.path.isfile(full):
                    local.append(entry)
        local_set = set(local)

        external = []
        seen = set()
        for name in self._auto_load_scripts:
            if name not in local_set and name not in seen:
                external.append(name)
                seen.add(name)
        if state.ui_state:
            for path in list(state.ui_state.recent_script_paths):
                if path not in local_set and path not in seen:
                    if not os.path.isfile(path):
                        state.ui_state.remove_recent_script_path(path)
                        continue
                    external.append(path)
                    seen.add(path)

        # Apply order: auto_load first (config order), then saved ui order
        saved = state.ui_state.scripts_order if state.ui_state else []
        auto_order = list(self._auto_load_scripts_ordered)
        local = self._apply_order(local, auto_order, saved)
        external = self._apply_order(external, auto_order, saved)

        for name in local:
            if name == startup_name:
                item = QListWidgetItem(name + '  (startup script)')
                item.setFlags(
                    (item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    & ~Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Checked)
                item.setData(_ROLE_EXTERNAL, False)
                item.setData(_ROLE_VALUE, name)
                item.setToolTip(
                    'Runs automatically on startup.\n'
                    'Configured via scripts.startup in config.')
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                self.scripts_list.addItem(item)
                continue
            is_checked = name in checked or name in self._auto_load_scripts
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(_ROLE_EXTERNAL, False)
            item.setData(_ROLE_VALUE, name)
            item.setCheckState(
                Qt.CheckState.Checked if is_checked
                else Qt.CheckState.Unchecked)
            font = item.font()
            font.setBold(is_checked)
            item.setFont(font)
            self.scripts_list.addItem(item)

        for path in external:
            display = (os.path.basename(path)
                       if (os.sep in path or '/' in path) else path)
            is_checked = path in checked or path in self._auto_load_scripts
            self._add_external_item(
                self.scripts_list, path,
                display + "  (external)", path,
                is_checked,
                lambda p=path: self._remove_script_by_value(p))

    # -- remove external entries --

    def _remove_plugin_by_value(self, name):
        import state
        if state.ui_state:
            state.ui_state.remove_recent_plugin_name(name)
        self._auto_load_plugins.discard(name)
        for i in range(self.plugins_list.count()):
            if self.plugins_list.item(i).data(_ROLE_VALUE) == name:
                self.plugins_list.takeItem(i)
                return

    def _remove_script_by_value(self, path):
        import state
        if state.ui_state:
            state.ui_state.remove_recent_script_path(path)
        self._auto_load_scripts.discard(path)
        for i in range(self.scripts_list.count()):
            if self.scripts_list.item(i).data(_ROLE_VALUE) == path:
                self.scripts_list.takeItem(i)
                return

    # -- add external entries --

    def _add_plugin(self):
        name, ok = QInputDialog.getText(
            self, "Add Plugin", "Plugin module name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        for i in range(self.plugins_list.count()):
            if self.plugins_list.item(i).data(_ROLE_VALUE) == name:
                self._set_item_checked(
                    self.plugins_list, self.plugins_list.item(i), True)
                return
        import state
        if state.ui_state:
            state.ui_state.add_recent_plugin_name(name)
        self._add_external_item(
            self.plugins_list, name,
            name + "  (external)",
            "External plugin \u2014 not in plugins directory",
            True,
            lambda: self._remove_plugin_by_value(name))

    def _add_script(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Add Script", self._config_dir, "All files (*)")
        if not path:
            return
        for i in range(self.scripts_list.count()):
            if self.scripts_list.item(i).data(_ROLE_VALUE) == path:
                self._set_item_checked(
                    self.scripts_list, self.scripts_list.item(i), True)
                return
        import state
        if state.ui_state:
            state.ui_state.add_recent_script_path(path)
        display = os.path.basename(path)
        self._add_external_item(
            self.scripts_list, path,
            display + "  (external)", path,
            True,
            lambda: self._remove_script_by_value(path))

    # -- load / save --

    def load_from_data(self, data):
        import state
        plugins_cfg = data.get('plugins') or {}
        self.plugins_dir.setText(str(plugins_cfg.get('dir', '') or ''))
        raw_plugins = list(plugins_cfg.get('auto_load') or [])
        pdir = self._abs_dir(self.plugins_dir.text(), 'plugins')
        self._auto_load_plugins = self._expand_patterns(
            raw_plugins, pdir, is_plugin=True)
        self._auto_load_plugins_ordered = self._build_auto_load_order(
            raw_plugins, self._auto_load_plugins)

        # Persist external auto_load entries to recent list
        if state.ui_state:
            for name in self._auto_load_plugins:
                if not self._is_in_dir(name, pdir, is_plugin=True):
                    state.ui_state.add_recent_plugin_name(name)

        scripts_cfg = data.get('scripts') or {}
        self.scripts_dir.setText(str(scripts_cfg.get('dir', '') or ''))
        self._startup_file = str(scripts_cfg.get('startup', '') or '')
        raw_scripts = list(scripts_cfg.get('auto_load') or [])
        sdir = self._abs_dir(self.scripts_dir.text(), 'scripts')
        self._auto_load_scripts = self._expand_patterns(
            raw_scripts, sdir, is_plugin=False)
        self._auto_load_scripts_ordered = self._build_auto_load_order(
            raw_scripts, self._auto_load_scripts)

        if state.ui_state:
            for name in self._auto_load_scripts:
                if not self._is_in_dir(name, sdir, is_plugin=False):
                    state.ui_state.add_recent_script_path(name)

        self._scan_plugins()
        self._scan_scripts()

    def save_to_data(self, data):
        from ruamel.yaml.comments import CommentedMap

        # Plugins
        plugins_cfg = data.get('plugins')
        if plugins_cfg is None:
            plugins_cfg = CommentedMap()
            data['plugins'] = plugins_cfg
        d = self.plugins_dir.text().strip()
        if d:
            plugins_cfg['dir'] = d
        elif 'dir' in plugins_cfg:
            del plugins_cfg['dir']
        auto = []
        all_plugins = []
        for i in range(self.plugins_list.count()):
            item = self.plugins_list.item(i)
            all_plugins.append(item.data(_ROLE_VALUE))
            if self._is_item_checked(self.plugins_list, item):
                auto.append(item.data(_ROLE_VALUE))
        if auto:
            plugins_cfg['auto_load'] = auto
        elif 'auto_load' in plugins_cfg:
            plugins_cfg['auto_load'] = None

        # Save full list order to ui.yaml
        import state
        if state.ui_state:
            state.ui_state.plugins_order = all_plugins

        # Scripts
        scripts_cfg = data.get('scripts')
        if scripts_cfg is None:
            scripts_cfg = CommentedMap()
            data['scripts'] = scripts_cfg
        d = self.scripts_dir.text().strip()
        if d:
            scripts_cfg['dir'] = d
        elif 'dir' in scripts_cfg:
            del scripts_cfg['dir']
        auto = []
        all_scripts = []
        for i in range(self.scripts_list.count()):
            item = self.scripts_list.item(i)
            all_scripts.append(item.data(_ROLE_VALUE))
            if self._is_item_checked(self.scripts_list, item):
                auto.append(item.data(_ROLE_VALUE))
        if auto:
            scripts_cfg['auto_load'] = auto
        elif 'auto_load' in scripts_cfg:
            scripts_cfg['auto_load'] = None

        if state.ui_state:
            state.ui_state.scripts_order = all_scripts
