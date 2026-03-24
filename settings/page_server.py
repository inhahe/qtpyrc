from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QSpinBox,
    QCheckBox, QPushButton, QListWidget, QGroupBox,
)


class ServerPage(QWidget):
    """Server connection settings for a network (supports multiple servers)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # Server list
        list_layout = QHBoxLayout()
        self._server_list = QListWidget()
        self._server_list.currentRowChanged.connect(self._on_row_changed)
        list_layout.addWidget(self._server_list, 1)

        btn_layout = QVBoxLayout()
        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_server)
        btn_layout.addWidget(self._add_btn)
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(self._remove_server)
        btn_layout.addWidget(self._remove_btn)
        self._up_btn = QPushButton("Up")
        self._up_btn.clicked.connect(self._move_up)
        btn_layout.addWidget(self._up_btn)
        self._down_btn = QPushButton("Down")
        self._down_btn.clicked.connect(self._move_down)
        btn_layout.addWidget(self._down_btn)
        btn_layout.addStretch()
        list_layout.addLayout(btn_layout)
        layout.addLayout(list_layout)

        # Edit form for selected server
        self._edit_group = QGroupBox("Server details")
        form = QFormLayout(self._edit_group)
        self.host = QLineEdit()
        form.addRow("Host:", self.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(6667)
        form.addRow("Port:", self.port)
        self.tls = QCheckBox()
        form.addRow("Use TLS:", self.tls)
        self.tls_verify = QCheckBox()
        self.tls_verify.setChecked(True)
        form.addRow("Verify TLS certificate:", self.tls_verify)
        layout.addWidget(self._edit_group)

        # Connect edits to update the backing data
        self.host.textChanged.connect(self._update_current)
        self.port.valueChanged.connect(self._update_current)
        self.tls.toggled.connect(self._update_current)
        self.tls_verify.toggled.connect(self._update_current)

        self._servers = []  # list of dicts
        self._current_row = -1
        self._updating = False  # guard against re-entrant updates

    def _label_for(self, srv):
        host = srv.get('host', '(no host)')
        port = srv.get('port', 6667)
        tls = ' [TLS]' if srv.get('tls') else ''
        return '%s:%s%s' % (host, port, tls)

    def _refresh_list(self):
        self._server_list.blockSignals(True)
        row = self._server_list.currentRow()
        self._server_list.clear()
        for srv in self._servers:
            self._server_list.addItem(self._label_for(srv))
        if row >= 0 and row < len(self._servers):
            self._server_list.setCurrentRow(row)
        elif self._servers:
            self._server_list.setCurrentRow(0)
        self._server_list.blockSignals(False)

    def _on_row_changed(self, row):
        self._current_row = row
        if row < 0 or row >= len(self._servers):
            self._edit_group.setEnabled(False)
            return
        self._edit_group.setEnabled(True)
        srv = self._servers[row]
        self._updating = True
        self.host.setText(str(srv.get('host', '')))
        self.port.setValue(int(srv.get('port', 6667)))
        self.tls.setChecked(bool(srv.get('tls', False)))
        self.tls_verify.setChecked(bool(srv.get('tls_verify', True)))
        self._updating = False

    def _update_current(self):
        if self._updating:
            return
        row = self._current_row
        if row < 0 or row >= len(self._servers):
            return
        srv = self._servers[row]
        srv['host'] = self.host.text()
        srv['port'] = self.port.value()
        srv['tls'] = self.tls.isChecked()
        srv['tls_verify'] = self.tls_verify.isChecked()
        # Update list label
        item = self._server_list.item(row)
        if item:
            item.setText(self._label_for(srv))

    def _add_server(self):
        srv = {'host': '', 'port': 6667, 'tls': False, 'tls_verify': True}
        self._servers.append(srv)
        self._refresh_list()
        self._server_list.setCurrentRow(len(self._servers) - 1)

    def _remove_server(self):
        row = self._server_list.currentRow()
        if row < 0 or row >= len(self._servers):
            return
        self._servers.pop(row)
        self._refresh_list()

    def _move_up(self):
        row = self._server_list.currentRow()
        if row <= 0:
            return
        self._servers[row], self._servers[row - 1] = self._servers[row - 1], self._servers[row]
        self._refresh_list()
        self._server_list.setCurrentRow(row - 1)

    def _move_down(self):
        row = self._server_list.currentRow()
        if row < 0 or row >= len(self._servers) - 1:
            return
        self._servers[row], self._servers[row + 1] = self._servers[row + 1], self._servers[row]
        self._refresh_list()
        self._server_list.setCurrentRow(row + 1)

    def load_from_data(self, net_data):
        self._servers = []
        # Support both 'servers' (list) and 'server' (single dict)
        servers = net_data.get('servers')
        if servers and isinstance(servers, list):
            for s in servers:
                if isinstance(s, dict):
                    self._servers.append(dict(s))
        else:
            srv = net_data.get('server') or {}
            if srv.get('host'):
                self._servers.append(dict(srv))
        self._refresh_list()
        if self._servers:
            self._server_list.setCurrentRow(0)
        else:
            self._edit_group.setEnabled(False)

    def save_to_data(self, net_data):
        from ruamel.yaml.comments import CommentedMap
        # Clean up: remove both old keys
        if 'server' in net_data:
            del net_data['server']
        if 'servers' in net_data:
            del net_data['servers']
        # Save based on count
        live = [s for s in self._servers if s.get('host')]
        if len(live) == 0:
            pass
        elif len(live) == 1:
            # Single server: use the simpler 'server:' key
            srv = CommentedMap()
            for k, v in live[0].items():
                srv[k] = v
            net_data['server'] = srv
        else:
            # Multiple servers: use 'servers:' list
            out = []
            for s in live:
                entry = CommentedMap()
                for k, v in s.items():
                    entry[k] = v
                out.append(entry)
            net_data['servers'] = out
