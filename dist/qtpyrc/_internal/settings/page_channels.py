from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QGroupBox, QFormLayout, QPlainTextEdit, QCheckBox, QLabel,
    QMenu, QSizePolicy,
)
from PySide6.QtCore import Qt
from functools import partial


class ChannelsPage(QWidget):
    """Per-channel settings for a network (ignores, auto-ops, highlights, etc.)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)

        # Channel list
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

        # Edit panel
        self._edit_group = QGroupBox("Channel settings")
        form = QFormLayout(self._edit_group)

        note = QLabel("These lists are additive: entries here are combined\n"
                      "with network and global lists, not replacing them.")
        note.setWordWrap(True)
        from settings import SETTINGS_NOTE_STYLE
        note.setStyleSheet(SETTINGS_NOTE_STYLE)
        form.addRow(note)

        from settings import SETTINGS_LIST_STYLE as _list_style
        self.ignores = QPlainTextEdit()
        self.ignores.setMaximumHeight(60)
        self.ignores.setPlaceholderText("nick!*@host masks, one per line")
        self.ignores.setStyleSheet(_list_style)
        form.addRow("Ignores:", self.ignores)

        self.auto_ops = QPlainTextEdit()
        self.auto_ops.setMaximumHeight(60)
        self.auto_ops.setPlaceholderText("nick!*@host masks, one per line")
        self.auto_ops.setStyleSheet(_list_style)
        form.addRow("Auto-ops:", self.auto_ops)

        self.highlights = QPlainTextEdit()
        self.highlights.setMaximumHeight(60)
        self.highlights.setPlaceholderText("words or /regex/ patterns, one per line")
        self.highlights.setStyleSheet(_list_style)
        form.addRow("Highlights:", self.highlights)

        self.highlight_notify = QCheckBox()
        self.highlight_notify.setChecked(True)
        self.highlight_notify.setToolTip("Uncheck to suppress highlight beep/desktop "
                                         "notifications for this channel.")
        form.addRow("Highlight notify:", self.highlight_notify)

        self._edit_group.setEnabled(False)
        self._edit_group.setMinimumWidth(0)
        self._edit_group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._edit_group, 1)

        self._channels = {}  # channel_name -> dict of settings
        self._current_chan = None
        self._updating = False

        # Save edits when fields change
        self.ignores.textChanged.connect(self._save_current)
        self.auto_ops.textChanged.connect(self._save_current)
        self.highlights.textChanged.connect(self._save_current)
        self.highlight_notify.toggled.connect(self._save_current)

    def _on_row_changed(self, row):
        if self._updating:
            return
        self._current_chan = None
        if row < 0 or row >= self._chan_list.count():
            self._edit_group.setEnabled(False)
            return
        self._edit_group.setEnabled(True)
        chan_name = self._chan_names[row]
        self._current_chan = chan_name
        data = self._channels.get(chan_name, {})
        self._updating = True
        self.ignores.setPlainText('\n'.join(data.get('ignores') or []))
        self.auto_ops.setPlainText('\n'.join(data.get('auto_ops') or []))
        self.highlights.setPlainText('\n'.join(data.get('highlights') or []))
        self.highlight_notify.setChecked(data.get('highlight_notify', True))
        self._updating = False

    def _save_current(self):
        if self._updating or not self._current_chan:
            return
        data = self._channels.setdefault(self._current_chan, {})
        def _list_from(widget):
            text = widget.toPlainText().strip()
            return [e.strip() for e in text.splitlines() if e.strip()] if text else []
        data['ignores'] = _list_from(self.ignores)
        data['auto_ops'] = _list_from(self.auto_ops)
        data['highlights'] = _list_from(self.highlights)
        data['highlight_notify'] = self.highlight_notify.isChecked()

    def _add_channel(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add Channel", "Channel name:")
        if ok and name.strip():
            name = name.strip()
            if not name.startswith(('#', '&', '!', '+')):
                name = '#' + name
            if name not in self._channels:
                self._channels[name] = {}
            self._refresh_list()
            # Select the new channel
            for i in range(self._chan_list.count()):
                if self._chan_list.item(i).text() == name:
                    self._chan_list.setCurrentRow(i)
                    break

    def _remove_channel(self, name=None):
        if name is None:
            row = self._chan_list.currentRow()
            if row < 0:
                return
            name = self._chan_names[row]
        self._channels.pop(name, None)
        self._current_chan = None
        self._refresh_list()

    def _chan_context_menu(self, pos):
        item = self._chan_list.itemAt(pos)
        if not item:
            return
        row = self._chan_list.row(item)
        name = self._chan_names[row]
        menu = QMenu(self)
        menu.addAction('Remove', partial(self._remove_channel, name))
        menu.exec(self._chan_list.viewport().mapToGlobal(pos))

    def _refresh_list(self):
        self._updating = True
        row = self._chan_list.currentRow()
        self._chan_list.clear()
        self._chan_names = sorted(self._channels.keys(), key=str.lower)
        for i, ch in enumerate(self._chan_names):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(2, 0, 2, 0)
            row_layout.setSpacing(4)
            del_btn = QPushButton('\u00d7')
            del_btn.setFixedSize(20, 20)
            from settings import SETTINGS_DELETE_STYLE
            del_btn.setStyleSheet(SETTINGS_DELETE_STYLE)
            del_btn.setToolTip('Remove %s' % ch)
            del_btn.clicked.connect(partial(self._remove_channel, ch))
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

    def load_from_data(self, net_data):
        self._channels = {}
        channels = net_data.get('channels') or {}
        for ch, settings in channels.items():
            if isinstance(settings, dict):
                self._channels[ch] = dict(settings)
            else:
                self._channels[ch] = {}
        self._refresh_list()
        if not self._channels:
            self._edit_group.setEnabled(False)

    def save_to_data(self, net_data):
        from ruamel.yaml.comments import CommentedMap
        # Clean empty channel entries
        channels = CommentedMap()
        for ch, data in sorted(self._channels.items(), key=lambda x: x[0].lower()):
            entry = CommentedMap()
            for key in ('ignores', 'auto_ops', 'highlights'):
                val = data.get(key)
                if val:
                    entry[key] = val
            hn = data.get('highlight_notify', True)
            if not hn:
                entry['highlight_notify'] = False
            if entry:
                channels[ch] = entry
        if channels:
            net_data['channels'] = channels
        elif 'channels' in net_data:
            del net_data['channels']
