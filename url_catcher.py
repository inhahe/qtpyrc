# url_catcher.py - URL catcher dialog

import fnmatch
import os
import re
import webbrowser

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QComboBox, QLineEdit, QDateEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QCheckBox,
)
from PySide6.QtCore import Qt, QDate

import state


class URLCatcherDialog(QDialog):
    """Dialog for browsing captured URLs with filtering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("URL Catcher")
        self.resize(800, 500)
        from dialogs import install_input_focus_handler
        install_input_focus_handler(self)

        layout = QVBoxLayout(self)

        # --- Filter area ---
        filt = QGroupBox("Filters")
        fl = QFormLayout(filt)

        row1 = QHBoxLayout()
        self._network = QComboBox()
        self._network.setMinimumWidth(140)
        self._network.currentIndexChanged.connect(self._on_network_changed)
        row1.addWidget(self._network, 1)
        row1.addSpacing(12)
        self._channel = QComboBox()
        self._channel.setEditable(True)
        self._channel.setMinimumWidth(140)
        row1.addWidget(self._channel, 1)
        fl.addRow("Network / Channel:", row1)

        row2 = QHBoxLayout()
        self._nick = QLineEdit()
        self._nick.setPlaceholderText("nick")
        row2.addWidget(self._nick, 1)
        row2.addSpacing(12)
        self._host = QLineEdit()
        self._host.setPlaceholderText("ident@host (wildcards: * ?)")
        row2.addWidget(self._host, 1)
        fl.addRow("Nick / Host:", row2)

        self._url_pattern = QLineEdit()
        self._url_pattern.setPlaceholderText(
            "e.g. *youtube* or /regex/i")
        self._url_pattern.setToolTip(
            "Filter URLs by pattern.\n"
            "Plain text: wildcard match (* = any chars, ? = single char)\n"
            "Wrap in /slashes/ for regex (/pattern/i for case-insensitive)")
        fl.addRow("URL pattern:", self._url_pattern)

        row3 = QHBoxLayout()
        self._use_dates = QCheckBox("Filter by date")
        row3.addWidget(self._use_dates)
        row3.addSpacing(8)
        self._date_from = QDateEdit()
        self._date_from.setCalendarPopup(True)
        self._date_from.setDate(QDate.currentDate().addMonths(-1))
        self._date_from.setEnabled(False)
        row3.addWidget(self._date_from, 1)
        row3.addSpacing(8)
        self._date_to = QDateEdit()
        self._date_to.setCalendarPopup(True)
        self._date_to.setDate(QDate.currentDate())
        self._date_to.setEnabled(False)
        row3.addWidget(self._date_to, 1)
        self._use_dates.toggled.connect(self._date_from.setEnabled)
        self._use_dates.toggled.connect(self._date_to.setEnabled)
        fl.addRow("Date range:", row3)

        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._search)
        fl.addRow(search_btn)

        layout.addWidget(filt)

        # --- Results table ---
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Time", "Network", "Channel", "Nick", "URL"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self._table, 1)

        # --- Buttons ---
        btns = QHBoxLayout()
        copy_btn = QPushButton("Copy URL")
        copy_btn.clicked.connect(self._copy_url)
        btns.addWidget(copy_btn)
        open_btn = QPushButton("Open in Browser")
        open_btn.clicked.connect(self._open_selected)
        btns.addWidget(open_btn)
        btns.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        self._populate_filters()
        self._search()

    def _populate_filters(self):
        db = state.historydb
        if not db:
            return
        self._network.clear()
        self._network.addItem("(all)", "")
        for net in db.url_networks():
            self._network.addItem(net, net)
        self._channel.clear()
        self._channel.addItem("(all)")
        for ch in db.url_channels():
            self._channel.addItem(ch)

    def _on_network_changed(self, _index):
        db = state.historydb
        if not db:
            return
        net = self._network.currentData()
        self._channel.clear()
        self._channel.addItem("(all)")
        for ch in db.url_channels(net or None):
            self._channel.addItem(ch)

    @staticmethod
    def _compile_url_filter(pattern):
        """Compile a URL filter pattern to a regex.

        Supports:
          *youtube*        — wildcard (fnmatch-style, case-insensitive)
          /regex/          — regex
          /regex/i         — regex, case-insensitive
        Returns a compiled regex or None.
        """
        if not pattern:
            return None
        # /regex/ or /regex/flags
        m = re.match(r'^/(.+)/([imsx]*)$', pattern)
        if m:
            flags = 0
            for ch in m.group(2):
                flags |= {'i': re.IGNORECASE, 'm': re.MULTILINE,
                           's': re.DOTALL, 'x': re.VERBOSE}.get(ch, 0)
            try:
                return re.compile(m.group(1), flags)
            except re.error:
                return None
        # Wildcard pattern — fnmatch-style (* and ? wildcards)
        try:
            return re.compile(fnmatch.translate(pattern), re.IGNORECASE)
        except re.error:
            return None

    def _search(self):
        db = state.historydb
        if not db:
            return
        net = self._network.currentData() or None
        ch_text = self._channel.currentText().strip()
        channel = ch_text if ch_text and ch_text != "(all)" else None
        nick = self._nick.text().strip() or None
        host = self._host.text().strip() or None
        date_from = None
        date_to = None
        if self._use_dates.isChecked():
            date_from = self._date_from.date().toString("yyyy-MM-dd")
            date_to = self._date_to.date().toString("yyyy-MM-dd")

        url_filter = self._compile_url_filter(
            self._url_pattern.text().strip())

        rows = db.search_urls(
            network=net, channel=channel, nick=nick,
            host=host, date_from=date_from, date_to=date_to)

        # Apply URL pattern filter client-side
        if url_filter:
            rows = [r for r in rows if url_filter.search(r[5])]

        self._table.setRowCount(len(rows))
        for i, (ts, network, channel, nick, host, url) in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(ts))
            self._table.setItem(i, 1, QTableWidgetItem(network))
            self._table.setItem(i, 2, QTableWidgetItem(channel))
            self._table.setItem(i, 3, QTableWidgetItem(nick))
            item = QTableWidgetItem(url)
            item.setToolTip(url)
            self._table.setItem(i, 4, item)

    def _selected_url(self):
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 4)
        return item.text() if item else None

    def _copy_url(self):
        url = self._selected_url()
        if url:
            QApplication.clipboard().setText(url)

    def _open_selected(self):
        url = self._selected_url()
        if url:
            webbrowser.open(url)


_dialog = None

def show_url_catcher(parent=None):
    """Show the URL catcher dialog (singleton)."""
    global _dialog
    if _dialog is None or not _dialog.isVisible():
        _dialog = URLCatcherDialog(parent or
                                   getattr(state.app, 'mainwin', None))
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
