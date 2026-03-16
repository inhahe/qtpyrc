# channel_list.py - Channel list browser dialog

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QLineEdit,
    QSpinBox, QCheckBox,
)
from PySide6.QtCore import Qt

import state


class ChannelListDialog(QDialog):
    """Channel list browser — shows results from LIST command."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.conn = client.conn
        self.setWindowTitle('Channel List — %s' % (client.network or client.hostname or '?'))
        self.resize(650, 450)

        layout = QVBoxLayout(self)

        # Filters
        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel('Min users:'))
        self.min_users = QSpinBox()
        self.min_users.setRange(0, 99999)
        self.min_users.setValue(2)
        self.min_users.setMaximumWidth(70)
        filter_layout.addWidget(self.min_users)

        filter_layout.addWidget(QLabel('Filter:'))
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText('filter by name or topic...')
        self.filter_text.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.filter_text, 1)

        self.fetch_btn = QPushButton('Fetch List')
        self.fetch_btn.clicked.connect(self._fetch)
        filter_layout.addWidget(self.fetch_btn)

        layout.addLayout(filter_layout)

        # Status
        self.status = QLabel('Click "Fetch List" to request channels from the server.')
        layout.addWidget(self.status)

        # Results tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Channel', 'Users', 'Topic'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        self.tree.setColumnWidth(0, 180)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.tree, 1)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        join_btn = QPushButton('Join Selected')
        join_btn.clicked.connect(self._join_selected)
        btn_layout.addWidget(join_btn)
        btn_layout.addStretch()
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # Data storage
        self._all_entries = []  # (channel, users_int, topic)
        self._fetching = False

        # Register for updates
        client._list_dialog = self

    def _fetch(self, params=None):
        """Send LIST command to the server.

        *params* is an optional string of ELIST parameters (e.g. '>50',
        '<100', 'C>60', '*python*').  If None, uses the min_users spinbox.
        """
        if not self.conn:
            self.status.setText('Not connected.')
            return
        self._all_entries = []
        self.tree.clear()
        self._fetching = True
        self.fetch_btn.setEnabled(False)
        self.status.setText('Requesting channel list...')

        if params:
            self.conn.sendLine('LIST %s' % params)
        else:
            min_u = self.min_users.value()
            if min_u > 0:
                self.conn.sendLine('LIST >%d' % min_u)
            else:
                self.conn.sendLine('LIST')

    def add_entry(self, channel, users, topic):
        """Called by irc_RPL_LIST for each channel entry."""
        self._all_entries.append((channel, users, topic))
        # Update count periodically
        if len(self._all_entries) % 50 == 0:
            self.status.setText('Receiving... %d channels so far' % len(self._all_entries))

    def list_end(self):
        """Called by irc_RPL_LISTEND when the list is complete."""
        self._fetching = False
        self.fetch_btn.setEnabled(True)
        self._apply_filter()
        self.status.setText('%d channels found.' % len(self._all_entries))

    def _apply_filter(self):
        """Filter and populate the tree from stored entries."""
        self.tree.clear()
        self.tree.setSortingEnabled(False)
        filt = self.filter_text.text().strip().lower()
        min_u = self.min_users.value()
        count = 0
        for channel, users, topic in self._all_entries:
            if users < min_u:
                continue
            if filt and filt not in channel.lower() and filt not in topic.lower():
                continue
            item = QTreeWidgetItem()
            item.setText(0, channel)
            item.setData(1, Qt.ItemDataRole.DisplayRole, users)
            item.setText(2, topic)
            self.tree.addTopLevelItem(item)
            count += 1
        self.tree.setSortingEnabled(True)
        if not self._fetching:
            self.status.setText('%d channels shown (of %d total).' % (count, len(self._all_entries)))

    def _on_double_click(self, item, column):
        """Double-click to join a channel."""
        channel = item.text(0)
        if self.conn:
            self.conn.join(channel)

    def _join_selected(self):
        """Join all selected channels."""
        if not self.conn:
            return
        for item in self.tree.selectedItems():
            self.conn.join(item.text(0))

    def reject(self):
        if hasattr(self.client, '_list_dialog'):
            del self.client._list_dialog
        super().reject()

    def closeEvent(self, event):
        if hasattr(self.client, '_list_dialog'):
            del self.client._list_dialog
        super().closeEvent(event)


def show_channel_list(client, parent=None):
    """Show (or raise) the channel list dialog."""
    existing = getattr(client, '_list_dialog', None)
    if existing:
        existing.raise_()
        existing.activateWindow()
        return existing
    dlg = ChannelListDialog(client, parent)
    dlg.show()
    return dlg
