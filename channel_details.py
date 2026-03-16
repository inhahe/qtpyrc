# channel_details.py - Channel details dialog (modes, bans, topic, etc.)

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QTreeWidget, QTreeWidgetItem, QCheckBox,
    QDialogButtonBox, QPushButton, QHeaderView, QPlainTextEdit,
    QLineEdit, QGroupBox, QFormLayout, QGridLayout,
    QInputDialog, QStyleFactory,
)
from PySide6.QtCore import Qt

import state


# Common channel mode descriptions
_MODE_DESCRIPTIONS = {
    'i': 'Invite only',
    'm': 'Moderated',
    'n': 'No external messages',
    's': 'Secret',
    'p': 'Private',
    't': 'Ops set topic',
    'c': 'No colors',
    'C': 'No CTCPs',
    'g': 'Free invite',
    'r': 'Registered only',
    'R': 'Registered speak',
    'S': 'SSL only',
    'z': 'SSL only',
    'D': 'Delayed join',
    'u': 'Auditorium',
    'O': 'Opers only',
    'f': 'Flood protection',
    'F': 'Enable forwarding',
    'Q': 'No kicks',
    'N': 'No nick changes',
    'b': 'Bans',
    'e': 'Ban exceptions',
    'I': 'Invite exceptions',
    'q': 'Quiets',
    'k': 'Channel key',
    'l': 'User limit',
    'j': 'Join throttle',
    'o': 'Operator',
    'v': 'Voice',
    'h': 'Half-op',
    'a': 'Admin',
}

_LIST_MODES = [
    ('b', 'Bans'),
    ('e', 'Excepts'),
    ('I', 'Invites'),
    ('q', 'Quiets'),
]


def _user_is_op(channel, conn):
    """Check if we have op (or higher) status in the channel."""
    if not conn or not channel:
        return False
    my_nick = conn.nickname
    if not my_nick:
        return False
    lnick = conn.irclower(my_nick)
    user = channel.client.users.get(lnick)
    if not user:
        return False
    chnlower = conn.irclower(channel.name)
    prefix = user.prefix.get(chnlower, '')
    return prefix in ('@', '~', '&')


class _ListTab(QWidget):
    """Tab showing a mode list (bans, excepts, invites, quiets)."""

    def __init__(self, mode_char, channel, conn, is_op, parent=None):
        super().__init__(parent)
        self.mode_char = mode_char
        self.channel = channel
        self.conn = conn
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.status = QLabel('Requesting...')
        layout.addWidget(self.status)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Mask', 'Set by', 'Time'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.tree)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton('Add...')
        self.add_btn.clicked.connect(self._add)
        self.add_btn.setEnabled(is_op)
        btn_row.addWidget(self.add_btn)
        self.remove_btn = QPushButton('Remove')
        self.remove_btn.clicked.connect(self._remove)
        self.remove_btn.setEnabled(is_op)
        btn_row.addWidget(self.remove_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    def set_editable(self, editable):
        self.add_btn.setEnabled(editable)
        self.remove_btn.setEnabled(editable)

    def update_list(self, entries):
        self.tree.clear()
        if entries:
            self.status.setText('%d entries:' % len(entries))
            for mask, setter, ts in entries:
                self.tree.addTopLevelItem(QTreeWidgetItem([mask, setter, ts]))
        else:
            self.status.setText('No entries.')

    def _add(self):
        mask, ok = QInputDialog.getText(
            self, 'Add %s' % _MODE_DESCRIPTIONS.get(self.mode_char, 'Entry'),
            'Mask (e.g. nick!*@*):')
        if ok and mask.strip() and self.conn:
            self.conn.sendLine('MODE %s +%s %s' % (
                self.channel.name, self.mode_char, mask.strip()))

    def _remove(self):
        items = self.tree.selectedItems()
        if not items or not self.conn:
            return
        for item in items:
            mask = item.text(0)
            self.conn.sendLine('MODE %s -%s %s' % (
                self.channel.name, self.mode_char, mask))


class ChannelDetailsDialog(QDialog):
    """Dialog showing channel details: topic, modes, bans, etc."""

    def __init__(self, channel, parent=None):
        super().__init__(parent)
        self.channel = channel
        self.conn = channel.client.conn
        self._is_op = _user_is_op(channel, self.conn)
        self.setWindowTitle('Channel Details - %s' % channel.name)
        self.resize(550, 450)
        self._applying_modes = False

        # Override the app palette/stylesheet so the dialog is readable
        # regardless of the IRC window color scheme
        _dis_fg = '#777'
        _dis_bg = '#e0e0e0'
        self.setStyleSheet(
            "ChannelDetailsDialog { background-color: #f0f0f0; color: #1a1a1a; }"
            "QTabBar::tab { padding: 4px 12px; background: #e0e0e0; color: #1a1a1a;"
            "  border: 1px solid #999; border-bottom: none; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #f0f0f0; font-weight: bold; }"
            "QTabWidget::pane { border: 1px solid #999; }"
            "QGroupBox { color: #1a1a1a; }"
            "QCheckBox { color: #1a1a1a; spacing: 4px; }"
            "QCheckBox:disabled { color: %(dfg)s; }"
            "QCheckBox::indicator { width: 13px; height: 13px;"
            "  border: 2px solid #666; background: #ffffff; border-radius: 2px; }"
            "QCheckBox::indicator:hover { border: 2px solid #444; }"
            "QCheckBox::indicator:checked { background: #3a7bd5; border: 2px solid #2a5faa; }"
            "QCheckBox::indicator:checked:disabled { background: #a0b8d8; border: 2px solid #8899aa; }"
            "QCheckBox::indicator:disabled { background: %(dbg)s; border: 2px solid #aaa; }"
            "QLabel { color: #1a1a1a; }"
            "QTreeWidget { background-color: #ffffff; color: #1a1a1a; }"
            "QPlainTextEdit { background-color: #ffffff; color: #1a1a1a; }"
            "QPlainTextEdit:disabled { background-color: %(dbg)s; color: %(dfg)s; }"
            "QLineEdit { background-color: #ffffff; color: #1a1a1a; }"
            "QLineEdit:disabled { background-color: %(dbg)s; color: %(dfg)s; }"
            "QPushButton { background-color: #e0e0e0; color: #1a1a1a;"
            "  padding: 3px 10px; border: 1px solid #999; }"
            "QPushButton:disabled { color: %(dfg)s; }"
            % {'dfg': _dis_fg, 'dbg': _dis_bg}
        )

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # --- Modes tab ---
        self._build_modes_tab()

        # --- List tabs ---
        self._list_tabs = {}
        chanmodes_raw = getattr(self.conn, '_chanmodes_raw', '') if self.conn else ''
        parts = chanmodes_raw.split(',') if chanmodes_raw else []
        list_modes = parts[0] if parts else 'beIq'
        for mode_char, label in _LIST_MODES:
            if mode_char in list_modes:
                tab = _ListTab(mode_char, channel, self.conn, self._is_op)
                self._list_tabs[mode_char] = tab
                self.tabs.addTab(tab, label)

        # --- Info tab ---
        self._build_info_tab()

        # --- Op status ---
        if not self._is_op:
            op_label = QLabel('You are not a channel operator. Settings are read-only.')
            op_label.setStyleSheet('color: #888; font-style: italic; padding: 2px;')
            layout.addWidget(op_label)

        # --- Buttons ---
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        refresh_btn = QPushButton('Refresh')
        refresh_btn.clicked.connect(self._refresh)
        btn_box.addButton(refresh_btn, QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(btn_box)

        channel._details_dialog = self
        self._request_data()

    def _build_modes_tab(self):
        modes_widget = QWidget()
        modes_layout = QVBoxLayout(modes_widget)
        modes_layout.setContentsMargins(4, 4, 4, 4)

        self.modes_label = QLabel('Requesting modes...')
        modes_layout.addWidget(self.modes_label)

        # Flag checkboxes (type D modes)
        flags_group = QGroupBox('Channel Modes')
        flags_grid = QGridLayout(flags_group)
        flags_grid.setVerticalSpacing(2)
        flags_grid.setHorizontalSpacing(12)
        self._flag_checkboxes = {}

        chanmodes_raw = getattr(self.conn, '_chanmodes_raw', '') if self.conn else ''
        parts = chanmodes_raw.split(',') if chanmodes_raw else []
        type_d = parts[3] if len(parts) > 3 else 'imnpst'
        row, col = 0, 0
        for ch in type_d:
            if ch in ('k', 'l', 'j'):
                continue
            desc = _MODE_DESCRIPTIONS.get(ch, '+%s' % ch)
            cb = QCheckBox('+%s  %s' % (ch, desc))
            cb.setEnabled(self._is_op)
            cb.toggled.connect(
                lambda checked, m=ch: self._on_flag_toggled(m, checked))
            self._flag_checkboxes[ch] = cb
            flags_grid.addWidget(cb, row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        modes_layout.addWidget(flags_group)

        # Key and limit
        all_modes = ''.join(parts) if parts else 'beIklimnpst'
        params_layout = QFormLayout()
        params_layout.setVerticalSpacing(4)

        self.key_edit = None
        if 'k' in all_modes:
            self.key_edit = QLineEdit()
            self.key_edit.setPlaceholderText('(none)')
            self.key_edit.setEnabled(self._is_op)
            self.key_edit.editingFinished.connect(self._on_key_changed)
            params_layout.addRow('Channel key (+k):', self.key_edit)

        self.limit_edit = None
        if 'l' in all_modes:
            self.limit_edit = QLineEdit()
            self.limit_edit.setPlaceholderText('(none)')
            self.limit_edit.setEnabled(self._is_op)
            self.limit_edit.editingFinished.connect(self._on_limit_changed)
            params_layout.addRow('Max users (+l):', self.limit_edit)

        modes_layout.addLayout(params_layout)
        modes_layout.addStretch(1)
        self.tabs.addTab(modes_widget, 'Modes')

    def _build_info_tab(self):
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(4, 4, 4, 4)

        info_layout.addWidget(QLabel('<b>%s</b>' % _esc(self.channel.name)))

        info_layout.addWidget(QLabel('Topic:'))
        self.topic_text = QPlainTextEdit()
        self.topic_text.setMaximumHeight(80)
        self.topic_text.setPlainText(self.channel.topic or '')
        info_layout.addWidget(self.topic_text)

        topic_btn_row = QHBoxLayout()
        self.topic_set_btn = QPushButton('Set Topic')
        self.topic_set_btn.clicked.connect(self._on_set_topic)
        topic_btn_row.addWidget(self.topic_set_btn)
        topic_btn_row.addStretch(1)
        info_layout.addLayout(topic_btn_row)

        self.topic_setter_label = QLabel()
        setter = getattr(self.channel, 'topic_setter', None)
        ts = getattr(self.channel, 'topic_time', None)
        if setter:
            setter_text = 'Set by %s' % setter
            if ts:
                setter_text += ' on %s' % ts
            self.topic_setter_label.setText(setter_text)
        info_layout.addWidget(self.topic_setter_label)

        info_layout.addWidget(QLabel('Users: %d' % len(self.channel.nicks)))
        info_layout.addStretch(1)
        self.tabs.addTab(info_widget, 'Info')
        self._update_topic_editable()

    # --- Editability ---

    def _can_edit_topic(self):
        if self._is_op:
            return True
        modes = self.channel.modes.lstrip('+') if self.channel.modes else ''
        return 't' not in modes

    def _update_topic_editable(self):
        can_edit = self._can_edit_topic()
        self.topic_text.setReadOnly(not can_edit)
        self.topic_set_btn.setEnabled(can_edit)

    def _update_editable(self):
        for cb in self._flag_checkboxes.values():
            cb.setEnabled(self._is_op)
        if self.key_edit:
            self.key_edit.setEnabled(self._is_op)
        if self.limit_edit:
            self.limit_edit.setEnabled(self._is_op)
        for tab in self._list_tabs.values():
            tab.set_editable(self._is_op)

    def _on_set_topic(self):
        if not self.conn:
            return
        new_topic = self.topic_text.toPlainText().replace('\n', ' ').strip()
        self.conn.sendLine('TOPIC %s :%s' % (self.channel.name, new_topic))

    # --- Data requests ---

    def _request_data(self):
        if not self.conn:
            return
        self.modes_label.setText('Requesting modes...')
        # Use _send_raw to bypass the flood queue — these are lightweight
        # queries that shouldn't wait behind 85+ queued JOINs
        self.conn._send_raw('MODE %s' % self.channel.name)
        for mode_char, tab in self._list_tabs.items():
            tab.status.setText('Requesting...')
            self.conn._send_raw('MODE %s +%s' % (self.channel.name, mode_char))

    def _refresh(self):
        self._is_op = _user_is_op(self.channel, self.conn)
        self._update_editable()
        self._update_topic_editable()
        self.topic_text.setPlainText(self.channel.topic or '')
        setter = getattr(self.channel, 'topic_setter', None)
        ts = getattr(self.channel, 'topic_time', None)
        if setter:
            setter_text = 'Set by %s' % setter
            if ts:
                setter_text += ' on %s' % ts
            self.topic_setter_label.setText(setter_text)
        self._request_data()

    # --- Mode callbacks ---

    def _on_flag_toggled(self, mode, checked):
        if self._applying_modes or not self.conn:
            return
        sign = '+' if checked else '-'
        self.conn.sendLine('MODE %s %s%s' % (self.channel.name, sign, mode))

    def _on_key_changed(self):
        if self._applying_modes or not self.conn:
            return
        key = self.key_edit.text().strip()
        if key:
            self.conn.sendLine('MODE %s +k %s' % (self.channel.name, key))
        else:
            self.conn.sendLine('MODE %s -k *' % self.channel.name)

    def _on_limit_changed(self):
        if self._applying_modes or not self.conn:
            return
        text = self.limit_edit.text().strip()
        try:
            val = int(text)
        except (ValueError, TypeError):
            val = 0
        if val > 0:
            self.conn.sendLine('MODE %s +l %d' % (self.channel.name, val))
        else:
            self.conn.sendLine('MODE %s -l' % self.channel.name)

    # --- Updates from IRC handlers ---

    def update_modes(self, mode_string, mode_args):
        state.dbg(state.LOG_DEBUG,
                  '[chandetails] received modes: %s %s' % (mode_string, mode_args))
        self._applying_modes = True
        try:
            self.modes_label.setText('Modes: %s %s' % (
                mode_string, ' '.join(mode_args)))
            for cb in self._flag_checkboxes.values():
                cb.setChecked(False)
            if self.key_edit:
                self.key_edit.clear()
            if self.limit_edit:
                self.limit_edit.clear()

            modes = mode_string.lstrip('+')
            arg_idx = 0
            for ch in modes:
                if ch in self._flag_checkboxes:
                    self._flag_checkboxes[ch].setChecked(True)
                elif ch == 'k' and self.key_edit and arg_idx < len(mode_args):
                    self.key_edit.setText(mode_args[arg_idx])
                    arg_idx += 1
                elif ch == 'l' and self.limit_edit and arg_idx < len(mode_args):
                    self.limit_edit.setText(mode_args[arg_idx])
                    arg_idx += 1
                else:
                    accepts = (self.conn._modeAcceptsArg.get(ch, (False, False))
                               if self.conn else (False, False))
                    if accepts[0] and arg_idx < len(mode_args):
                        arg_idx += 1
        finally:
            self._applying_modes = False
        self._update_topic_editable()

    def update_list(self, mode_char, entries):
        state.dbg(state.LOG_DEBUG,
                  '[chandetails] list +%s: %d entries' % (mode_char, len(entries)))
        tab = self._list_tabs.get(mode_char)
        if tab:
            tab.update_list(entries)

    def update_access_denied(self, channel):
        """Mark any list tabs still in 'Requesting...' as access denied."""
        for tab in self._list_tabs.values():
            if tab.status.text() == 'Requesting...':
                tab.status.setText('Access denied (not op)')

    # --- Lifecycle ---

    def reject(self):
        if hasattr(self.channel, '_details_dialog'):
            del self.channel._details_dialog
        super().reject()

    def closeEvent(self, event):
        if hasattr(self.channel, '_details_dialog'):
            del self.channel._details_dialog
        super().closeEvent(event)


def _esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def show_channel_details(channel, parent=None):
    existing = getattr(channel, '_details_dialog', None)
    if existing:
        existing.raise_()
        existing.activateWindow()
        existing._refresh()
        return existing
    dlg = ChannelDetailsDialog(channel, parent)
    dlg.show()
    return dlg
