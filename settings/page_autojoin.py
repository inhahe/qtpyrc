from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QGroupBox, QFormLayout, QLineEdit, QPlainTextEdit, QCheckBox, QLabel,
    QInputDialog, QMenu, QSizePolicy,
)
from PySide6.QtCore import Qt
from functools import partial


class AutoJoinPage(QWidget):
    """Auto-join channels and per-channel settings for a network."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)

        # Channel list (left)
        list_col = QVBoxLayout()
        self._chan_list = QListWidget()
        self._chan_list.currentRowChanged.connect(self._on_row_changed)
        self._chan_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._chan_list.customContextMenuRequested.connect(self._chan_context_menu)
        list_col.addWidget(self._chan_list, 1)
        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_channel)
        list_col.addWidget(self._add_btn)
        layout.addLayout(list_col, 1)

        # Edit panel (right)
        self._edit_group = QGroupBox("Channel settings")
        self._edit_group.setMinimumWidth(0)
        self._edit_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        edit_layout = QVBoxLayout(self._edit_group)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Key:"))
        self._key = QLineEdit()
        self._key.setPlaceholderText("(none)")
        self._key.setToolTip("Channel key (password) for joining")
        key_row.addWidget(self._key)
        self._highlight_notify = QCheckBox("Highlight notify")
        self._highlight_notify.setChecked(True)
        self._highlight_notify.setToolTip("Uncheck to suppress highlight beep/desktop "
                                          "notifications for this channel.")
        key_row.addWidget(self._highlight_notify)
        edit_layout.addLayout(key_row)

        note = QLabel("Per-channel lists are additive (combined with network "
                      "and global). One entry per line.")
        from settings import SETTINGS_NOTE_STYLE
        note.setStyleSheet(SETTINGS_NOTE_STYLE)
        edit_layout.addWidget(note)

        edit_layout.addWidget(QLabel("Ignores:"))
        from settings import SETTINGS_LIST_STYLE as _list_style
        self._ignores = QPlainTextEdit()
        self._ignores.setPlaceholderText("nick!*@host masks, one per line")
        self._ignores.setStyleSheet(_list_style)
        edit_layout.addWidget(self._ignores, 1)

        edit_layout.addWidget(QLabel("Auto-ops:"))
        self._auto_ops = QPlainTextEdit()
        self._auto_ops.setPlaceholderText("nick!*@host masks, one per line")
        self._auto_ops.setStyleSheet(_list_style)
        edit_layout.addWidget(self._auto_ops, 1)

        edit_layout.addWidget(QLabel("Highlights:"))
        self._highlights = QPlainTextEdit()
        self._highlights.setPlaceholderText("words or /regex/ patterns, one per line")
        self._highlights.setStyleSheet(_list_style)
        edit_layout.addWidget(self._highlights, 1)

        self._edit_group.setEnabled(False)
        layout.addWidget(self._edit_group, 1)

        # Internal data
        self._channels = []  # list of channel names (preserves order)
        self._keys = {}      # channel -> key string
        self._chan_settings = {}  # channel -> dict of per-channel overrides
        self._current_chan = None
        self._updating = False

        # Auto-save edits
        self._key.textChanged.connect(self._save_current)
        self._ignores.textChanged.connect(self._save_current)
        self._auto_ops.textChanged.connect(self._save_current)
        self._highlights.textChanged.connect(self._save_current)
        self._highlight_notify.toggled.connect(self._save_current)

    def _on_row_changed(self, row):
        if self._updating:
            return
        self._current_chan = None
        if row < 0 or row >= len(self._channels):
            self._edit_group.setEnabled(False)
            return
        self._edit_group.setEnabled(True)
        chan = self._channels[row]
        self._current_chan = chan
        self._updating = True
        self._key.setText(self._keys.get(chan, '') or '')
        cs = self._chan_settings.get(chan, {})
        self._ignores.setPlainText('\n'.join(cs.get('ignores') or []))
        self._auto_ops.setPlainText('\n'.join(cs.get('auto_ops') or []))
        self._highlights.setPlainText('\n'.join(cs.get('highlights') or []))
        self._highlight_notify.setChecked(cs.get('highlight_notify', True))
        self._updating = False

    def _save_current(self):
        if self._updating or not self._current_chan:
            return
        chan = self._current_chan
        self._keys[chan] = self._key.text().strip() or None
        cs = self._chan_settings.setdefault(chan, {})
        def _list_from(widget):
            text = widget.toPlainText().strip()
            return [e.strip() for e in text.splitlines() if e.strip()] if text else []
        cs['ignores'] = _list_from(self._ignores)
        cs['auto_ops'] = _list_from(self._auto_ops)
        cs['highlights'] = _list_from(self._highlights)
        cs['highlight_notify'] = self._highlight_notify.isChecked()

    def _add_channel(self):
        name, ok = QInputDialog.getText(self, "Add Channel", "Channel name:")
        if ok and name.strip():
            name = name.strip()
            if not name.startswith(('#', '&', '!', '+')):
                name = '#' + name
            if name not in self._channels:
                self._channels.append(name)
                self._keys[name] = None
            self._refresh_list()
            for i in range(self._chan_list.count()):
                if self._chan_list.item(i).text() == name:
                    self._chan_list.setCurrentRow(i)
                    break

    def _remove_channel(self, row=None):
        if row is None:
            row = self._chan_list.currentRow()
        if row < 0 or row >= len(self._channels):
            return
        name = self._channels.pop(row)
        self._keys.pop(name, None)
        self._chan_settings.pop(name, None)
        self._current_chan = None
        self._refresh_list()

    def _chan_context_menu(self, pos):
        item = self._chan_list.itemAt(pos)
        if not item:
            return
        row = self._chan_list.row(item)
        menu = QMenu(self)
        menu.addAction('Remove', partial(self._remove_channel, row))
        menu.exec(self._chan_list.viewport().mapToGlobal(pos))

    def _refresh_list(self):
        self._updating = True
        row = self._chan_list.currentRow()
        self._chan_list.clear()
        for i, ch in enumerate(self._channels):
            from PySide6.QtWidgets import QListWidgetItem
            # Custom row widget: [X] channel_name
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(2, 0, 2, 0)
            row_layout.setSpacing(4)
            del_btn = QPushButton('\u00d7')
            del_btn.setFixedSize(20, 20)
            from settings import SETTINGS_DELETE_STYLE
            del_btn.setStyleSheet(SETTINGS_DELETE_STYLE)
            del_btn.setToolTip('Remove %s' % ch)
            del_btn.clicked.connect(partial(self._remove_channel, i))
            row_layout.addWidget(del_btn)
            label = QLabel(ch)
            from settings import SETTINGS_LIST_STYLE
            label.setStyleSheet(SETTINGS_LIST_STYLE)
            row_layout.addWidget(label, 1)
            item = QListWidgetItem()
            item.setSizeHint(row_widget.sizeHint())
            self._chan_list.addItem(item)
            self._chan_list.setItemWidget(item, row_widget)
        if 0 <= row < self._chan_list.count():
            self._chan_list.setCurrentRow(row)
        elif self._chan_list.count() > 0:
            self._chan_list.setCurrentRow(0)
        self._updating = False
        if self._chan_list.currentRow() >= 0:
            self._on_row_changed(self._chan_list.currentRow())
        else:
            self._edit_group.setEnabled(False)

    def load_from_data(self, net_data):
        self._channels = []
        self._keys = {}
        self._chan_settings = {}
        # Load auto-join list
        aj = net_data.get('auto_join') or {}
        for ch, key in aj.items():
            self._channels.append(str(ch))
            self._keys[str(ch)] = str(key) if key else None
        # Load per-channel settings
        channels = net_data.get('channels') or {}
        for ch, settings in channels.items():
            if ch not in self._channels:
                self._channels.append(ch)
            if isinstance(settings, dict):
                self._chan_settings[ch] = dict(settings)
        self._refresh_list()

    def save_to_data(self, net_data):
        from ruamel.yaml.comments import CommentedMap
        # Save auto-join
        aj = CommentedMap()
        for ch in self._channels:
            key = self._keys.get(ch)
            aj[ch] = key if key else None
        net_data['auto_join'] = aj
        # Save per-channel settings
        channels = CommentedMap()
        for ch in self._channels:
            cs = self._chan_settings.get(ch, {})
            entry = CommentedMap()
            for key in ('ignores', 'auto_ops', 'highlights'):
                val = cs.get(key)
                if val:
                    entry[key] = val
            hn = cs.get('highlight_notify', True)
            if not hn:
                entry['highlight_notify'] = False
            if entry:
                channels[ch] = entry
        if channels:
            net_data['channels'] = channels
        elif 'channels' in net_data:
            del net_data['channels']
