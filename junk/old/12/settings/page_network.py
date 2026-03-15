from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QLineEdit, QSpinBox, QDoubleSpinBox, QLabel,
    QHBoxLayout, QPushButton, QPlainTextEdit,
)


class NetworkPage(QWidget):
    """Per-network settings (overrides global identity/connection settings)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        note = QLabel("Settings left blank inherit from the global defaults.\n"
                      "Identity (nick, user, realname, alt nicks): network overrides global.\n"
                      "Server password: per-server, with network and global fallback.\n"
                      "Flood control: network overrides global.\n"
                      "Ignores, auto-ops, highlights, notify: additive (combined with global\n"
                      "and per-channel lists, not replacing them).")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 9pt; margin-bottom: 4px;")
        layout.addRow(note)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Reported network name (for matching)")
        layout.addRow("Network name:", self.name)

        self.nick = QLineEdit()
        self.nick.setPlaceholderText("(global default)")
        layout.addRow("Nick:", self.nick)

        self.alt_nicks = QPlainTextEdit()
        self.alt_nicks.setMaximumHeight(60)
        self.alt_nicks.setPlaceholderText("(global default) One per line")
        self.alt_nicks.setToolTip("Alternative nicks, one per line. Overrides the global alt nicks list.")
        layout.addRow("Alt nicks:", self.alt_nicks)

        self.user = QLineEdit()
        self.user.setPlaceholderText("(global default)")
        layout.addRow("Username:", self.user)

        self.realname = QLineEdit()
        self.realname.setPlaceholderText("(global default)")
        layout.addRow("Real name:", self.realname)

        pw_row = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        pw_row.addWidget(self.password)
        self._pw_show = QPushButton("Show")
        self._pw_show.setFixedWidth(50)
        self._pw_show.setCheckable(True)
        self._pw_show.toggled.connect(lambda on: (
            self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password),
            self._pw_show.setText("Hide" if on else "Show")))
        pw_row.addWidget(self._pw_show)
        layout.addRow("Server password:", pw_row)

        self.auto_connect = QCheckBox()
        layout.addRow("Auto-connect:", self.auto_connect)

        self.persist_autojoins = QCheckBox()
        layout.addRow("Persist auto-joins:", self.persist_autojoins)

        self.flood_burst = QSpinBox()
        self.flood_burst.setRange(0, 50)
        self.flood_burst.setSpecialValueText("(default: 5)")
        self.flood_burst.setToolTip("Messages allowed in an initial burst before throttling")
        layout.addRow("Flood burst:", self.flood_burst)

        self.flood_rate = QDoubleSpinBox()
        self.flood_rate.setRange(0.0, 30.0)
        self.flood_rate.setDecimals(1)
        self.flood_rate.setSuffix(" s")
        self.flood_rate.setSpecialValueText("(default: 2.0)")
        self.flood_rate.setToolTip("Seconds between messages after burst is exhausted")
        layout.addRow("Flood rate:", self.flood_rate)


    def load_from_data(self, net_data):
        """Load from a single network's CommentedMap."""
        self.name.setText(str(net_data.get('name', '')))
        self.nick.setText(str(net_data.get('nick', '')))
        alt = net_data.get('alt_nicks')
        self.alt_nicks.setPlainText('\n'.join(alt) if alt else '')
        self.user.setText(str(net_data.get('user', '')))
        self.realname.setText(str(net_data.get('realname', '')))
        self.password.setText(str(net_data.get('password', '')))
        self.auto_connect.setChecked(bool(net_data.get('auto_connect', False)))
        self.persist_autojoins.setChecked(bool(net_data.get('persist_autojoins', False)))
        flood = net_data.get('flood') or {}
        self.flood_burst.setValue(int(flood.get('burst', 0) or 0))
        self.flood_rate.setValue(float(flood.get('rate', 0.0) or 0.0))

    def save_to_data(self, net_data):
        def _set_or_del(key, val, empty=''):
            if val and val != empty:
                net_data[key] = val
            elif key in net_data:
                del net_data[key]

        _set_or_del('name', self.name.text())
        _set_or_del('nick', self.nick.text())
        alt_text = self.alt_nicks.toPlainText().strip()
        if alt_text:
            net_data['alt_nicks'] = [n.strip() for n in alt_text.splitlines() if n.strip()]
        elif 'alt_nicks' in net_data:
            del net_data['alt_nicks']
        _set_or_del('user', self.user.text())
        _set_or_del('realname', self.realname.text())
        _set_or_del('password', self.password.text())

        if self.auto_connect.isChecked():
            net_data['auto_connect'] = True
        elif 'auto_connect' in net_data:
            del net_data['auto_connect']

        if self.persist_autojoins.isChecked():
            net_data['persist_autojoins'] = True
        elif 'persist_autojoins' in net_data:
            del net_data['persist_autojoins']


        fb = self.flood_burst.value()
        fr = self.flood_rate.value()
        if fb > 0 or fr > 0:
            from ruamel.yaml.comments import CommentedMap
            flood = net_data.get('flood')
            if flood is None:
                flood = CommentedMap()
                net_data['flood'] = flood
            if fb > 0:
                flood['burst'] = fb
            elif 'burst' in flood:
                del flood['burst']
            if fr > 0:
                flood['rate'] = fr
            elif 'rate' in flood:
                del flood['rate']
        elif 'flood' in net_data:
            del net_data['flood']

