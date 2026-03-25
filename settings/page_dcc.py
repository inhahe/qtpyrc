from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QSpinBox, QComboBox, QLineEdit,
    QHBoxLayout, QPushButton, QFileDialog, QLabel, QPlainTextEdit,
)
from settings.page_general import _ck
from settings import SETTINGS_NOTE_STYLE, SETTINGS_LIST_STYLE


_MAX_FIELD_W = 150  # max width for small fields (combos, spinboxes, line edits)
_PORT_W = 115       # width for port spinboxes
_LIST_HEIGHT = 120  # height for list text areas


class DCCPage(QWidget):
    """DCC (Direct Client Connection) settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        note = QLabel("DCC enables direct file transfers and chat between users.\n"
                       "UPnP/NAT-PMP automatically opens ports on your router.")
        note.setWordWrap(True)
        note.setStyleSheet(SETTINGS_NOTE_STYLE)
        layout.addRow(note)

        # Download directory
        dir_row = QHBoxLayout()
        self.download_dir = _ck(QLineEdit(), 'dcc.download_dir')
        self.download_dir.setPlaceholderText("(prompt each time)")
        dir_row.addWidget(self.download_dir)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)
        layout.addRow("Download directory:", dir_row)

        # Port range
        port_row = QHBoxLayout()
        self.port_min = _ck(QSpinBox(), 'dcc.port_min')
        self.port_min.setRange(1024, 65535)
        self.port_min.setMinimumWidth(_PORT_W)
        self.port_min.setMaximumWidth(_PORT_W)
        port_row.addWidget(self.port_min)
        port_row.addWidget(QLabel("to"))
        self.port_max = _ck(QSpinBox(), 'dcc.port_max')
        self.port_max.setRange(1024, 65535)
        self.port_max.setMinimumWidth(_PORT_W)
        self.port_max.setMaximumWidth(_PORT_W)
        port_row.addWidget(self.port_max)
        port_row.addStretch()
        layout.addRow("Port range:", port_row)

        # Auto-accept
        self.auto_accept = _ck(QComboBox(), 'dcc.auto_accept')
        self.auto_accept.addItems(["never", "known", "trusted", "always"])
        self.auto_accept.setToolTip(
            "never: always prompt\n"
            "known: auto-accept from users in your channels or open queries\n"
            "trusted: auto-accept only from trusted hostmasks (reject all others)\n"
            "always: auto-accept from everyone")
        self.auto_accept.setMaximumWidth(_MAX_FIELD_W)
        layout.addRow("Auto-accept transfers:", self.auto_accept)

        # Max file size
        self.max_filesize = _ck(QSpinBox(), 'dcc.max_filesize')
        self.max_filesize.setRange(0, 999999)
        self.max_filesize.setSuffix(" MB")
        self.max_filesize.setSpecialValueText("(no limit)")
        self.max_filesize.setMaximumWidth(_MAX_FIELD_W)
        layout.addRow("Max file size:", self.max_filesize)

        # On file exists
        self.on_exists = _ck(QComboBox(), 'dcc.on_exists')
        self.on_exists.addItems(["ask", "resume", "rename", "overwrite"])
        self.on_exists.setToolTip(
            "What to do when a received file already exists")
        self.on_exists.setMaximumWidth(_MAX_FIELD_W)
        layout.addRow("If file exists:", self.on_exists)

        # NAT traversal
        self.nat_traversal = _ck(QComboBox(), 'dcc.nat_traversal')
        self.nat_traversal.addItems(["auto", "upnp", "natpmp", "disabled"])
        self.nat_traversal.setToolTip(
            '"auto" tries UPnP first, then NAT-PMP')
        self.nat_traversal.setMaximumWidth(_MAX_FIELD_W)
        layout.addRow("NAT traversal:", self.nat_traversal)

        # IP override
        self.dcc_ip = _ck(QLineEdit(), 'dcc.ip')
        self.dcc_ip.setPlaceholderText("(auto-detect)")
        self.dcc_ip.setToolTip(
            "Manual IP for DCC offers. Leave empty to auto-detect.\n"
            "Set your public IP if auto-detect fails, or 127.0.0.1 for local testing.")
        self.dcc_ip.setMaximumWidth(_MAX_FIELD_W)
        layout.addRow("DCC IP:", self.dcc_ip)

        # Passive mode
        self.passive = _ck(QCheckBox(), 'dcc.passive')
        self.passive.setToolTip(
            "Checked: always use passive DCC (never listen for connections).\n"
            "Unchecked: try active first (listen), fall back to passive on timeout.")
        layout.addRow("Passive only:", self.passive)

        # Timeout
        self.timeout = _ck(QSpinBox(), 'dcc.timeout')
        self.timeout.setRange(10, 600)
        self.timeout.setSuffix(" seconds")
        self.timeout.setMaximumWidth(_MAX_FIELD_W + 30)
        layout.addRow("Connection timeout:", self.timeout)

        # Show get dialog
        self.show_get_dialog = _ck(QCheckBox(), 'dcc.show_get_dialog')
        self.show_get_dialog.setToolTip(
            "Show accept/reject dialog for transfers not auto-accepted")
        layout.addRow("Show get dialog:", self.show_get_dialog)

        layout.addRow(QLabel(""))  # spacer

        # Trusted hosts
        self.trusted_hosts = _ck(QPlainTextEdit(), 'dcc.trusted_hosts')
        self.trusted_hosts.setPlaceholderText("nick!ident@host masks, one per line")
        self.trusted_hosts.setStyleSheet(SETTINGS_LIST_STYLE)
        self.trusted_hosts.setMinimumHeight(_LIST_HEIGHT)
        layout.addRow("Trusted hosts:", self.trusted_hosts)

        # File filter
        self.file_filter_mode = _ck(QComboBox(), 'dcc.file_filter_mode')
        self.file_filter_mode.addItems(["disabled", "whitelist", "blacklist"])
        self.file_filter_mode.setToolTip(
            "whitelist: only accept listed extensions.\n"
            "blacklist: reject listed extensions.")
        self.file_filter_mode.setMaximumWidth(_MAX_FIELD_W)
        layout.addRow("File type filter:", self.file_filter_mode)

        self.file_filter = _ck(QPlainTextEdit(), 'dcc.file_filter')
        self.file_filter.setPlaceholderText("extensions, one per line (e.g. .exe)")
        self.file_filter.setStyleSheet(SETTINGS_LIST_STYLE)
        self.file_filter.setMinimumHeight(_LIST_HEIGHT)
        layout.addRow("Filter list:", self.file_filter)

    def _browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select download directory")
        if path:
            self.download_dir.setText(path)

    def load_from_data(self, data):
        dcc = data.get('dcc') or {}
        self.download_dir.setText(str(dcc.get('download_dir', '')))
        self.port_min.setValue(int(dcc.get('port_min', 1024)))
        self.port_max.setValue(int(dcc.get('port_max', 65535)))
        aa = str(dcc.get('auto_accept', 'never'))
        idx = self.auto_accept.findText(aa)
        if idx >= 0:
            self.auto_accept.setCurrentIndex(idx)
        self.max_filesize.setValue(int(dcc.get('max_filesize', 0)))
        nat = str(dcc.get('nat_traversal', 'auto'))
        idx = self.nat_traversal.findText(nat)
        if idx >= 0:
            self.nat_traversal.setCurrentIndex(idx)
        self.dcc_ip.setText(str(dcc.get('ip', '')))
        self.passive.setChecked(bool(dcc.get('passive', False)))
        self.timeout.setValue(int(dcc.get('timeout', 120)))
        oe = str(dcc.get('on_exists', 'ask'))
        idx = self.on_exists.findText(oe)
        if idx >= 0:
            self.on_exists.setCurrentIndex(idx)
        self.show_get_dialog.setChecked(bool(dcc.get('show_get_dialog', True)))
        self.trusted_hosts.setPlainText('\n'.join(dcc.get('trusted_hosts') or []))
        fm = str(dcc.get('file_filter_mode', 'disabled'))
        idx = self.file_filter_mode.findText(fm)
        if idx >= 0:
            self.file_filter_mode.setCurrentIndex(idx)
        self.file_filter.setPlainText('\n'.join(dcc.get('file_filter') or []))

    def save_to_data(self, data):
        from ruamel.yaml.comments import CommentedMap
        dcc = data.get('dcc')
        if dcc is None:
            dcc = CommentedMap()
            data['dcc'] = dcc
        dd = self.download_dir.text().strip()
        if dd:
            dcc['download_dir'] = dd
        elif 'download_dir' in dcc:
            del dcc['download_dir']
        dcc['port_min'] = self.port_min.value()
        dcc['port_max'] = self.port_max.value()
        dcc['auto_accept'] = self.auto_accept.currentText()
        dcc['max_filesize'] = self.max_filesize.value()
        dcc['on_exists'] = self.on_exists.currentText()
        dcc['nat_traversal'] = self.nat_traversal.currentText()
        ip = self.dcc_ip.text().strip()
        if ip:
            dcc['ip'] = ip
        elif 'ip' in dcc:
            del dcc['ip']
        dcc['passive'] = self.passive.isChecked()
        dcc['timeout'] = self.timeout.value()
        dcc['show_get_dialog'] = self.show_get_dialog.isChecked()
        # Trusted hosts
        th = self.trusted_hosts.toPlainText().strip()
        if th:
            dcc['trusted_hosts'] = [e.strip() for e in th.splitlines() if e.strip()]
        elif 'trusted_hosts' in dcc:
            del dcc['trusted_hosts']
        dcc['file_filter_mode'] = self.file_filter_mode.currentText()
        ff = self.file_filter.toPlainText().strip()
        if ff:
            dcc['file_filter'] = [e.strip() for e in ff.splitlines() if e.strip()]
        elif 'file_filter' in dcc:
            del dcc['file_filter']
