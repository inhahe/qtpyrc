from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QLabel,
    QHBoxLayout, QPushButton, QPlainTextEdit,
)
from settings.page_general import _ck


class NetworkPage(QWidget):
    """Per-network settings (overrides global identity/connection settings)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        note = QLabel("Settings left blank inherit from the global defaults.\n"
                      "Identity (nick, user, realname, alt nicks): network overrides global.\n"
                      "Server password: per-server, with network and global fallback.\n"
                      "Flood control: network overrides global.\n"
                      "Ignores, auto-ops, highlights, notify: additive (combined with global\n"
                      "and per-channel lists, not replacing them).")
        note.setWordWrap(True)
        from settings import SETTINGS_NOTE_STYLE
        note.setStyleSheet(SETTINGS_NOTE_STYLE + " margin-bottom: 4px;")
        layout.addRow(note)

        from PySide6.QtGui import QFontMetrics
        _fm = QFontMetrics(self.font())
        _field_w = _fm.horizontalAdvance('(global default) One per line') + _fm.height() * 2
        _field_w = max(_field_w, 200)

        self.name = _ck(QLineEdit(), 'name')
        self.name.setPlaceholderText("(auto-detected)")
        self.name.setFixedWidth(_field_w)
        layout.addRow("Network name:", self.name)

        self.nick = _ck(QLineEdit(), 'nick')
        self.nick.setPlaceholderText("(global default)")
        self.nick.setFixedWidth(_field_w)
        layout.addRow("Nick:", self.nick)

        self.alt_nicks = _ck(QPlainTextEdit(), 'alt_nicks')
        self.alt_nicks.setMaximumHeight(90)
        self.alt_nicks.setPlaceholderText("(global default) One per line")
        self.alt_nicks.setFixedWidth(_field_w)
        layout.addRow("Alt nicks:", self.alt_nicks)

        self.user = _ck(QLineEdit(), 'user')
        self.user.setPlaceholderText("(global default)")
        self.user.setFixedWidth(_field_w)
        layout.addRow("Username:", self.user)

        self.realname = _ck(QLineEdit(), 'realname')
        self.realname.setPlaceholderText("(global default)")
        self.realname.setFixedWidth(_field_w)
        layout.addRow("Real name:", self.realname)

        pw_row = QHBoxLayout()
        self.password = _ck(QLineEdit(), 'password')
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setFixedWidth(_field_w)
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

        self.auto_connect = _ck(QComboBox(), 'auto_connect')
        self.auto_connect.addItems(['Inherit', 'On', 'Off'])
        layout.addRow("Auto-connect:", self.auto_connect)

        self.persist_autojoins = _ck(QComboBox(), 'persist_autojoins')
        self.persist_autojoins.addItems(['Inherit', 'On', 'Off'])
        layout.addRow("Persist auto-joins:", self.persist_autojoins)

        # Make both combos the same width
        _combo_w = _fm.horizontalAdvance('Inherit (off) ') + _fm.height() * 3
        self.auto_connect.setFixedWidth(_combo_w)
        self.persist_autojoins.setFixedWidth(_combo_w)

        self.flood_burst = _ck(QSpinBox(), 'flood.burst')
        self.flood_burst.setRange(0, 50)
        self.flood_burst.setSpecialValueText("(default: 5)")
        layout.addRow("Flood burst:", self.flood_burst)

        self.flood_rate = _ck(QDoubleSpinBox(), 'flood.rate')
        self.flood_rate.setRange(0.0, 30.0)
        self.flood_rate.setDecimals(1)
        self.flood_rate.setSuffix(" s")
        self.flood_rate.setSpecialValueText("(default: 2.0)")
        layout.addRow("Flood rate:", self.flood_rate)


    def load_from_data(self, net_data, global_data=None):
        """Load from a single network's CommentedMap.

        *global_data* is the top-level config data dict for resolving
        global defaults for tri-state checkboxes.
        """
        self._global_data = global_data or {}
        self.name.setText(str(net_data.get('name', '')))
        self.nick.setText(str(net_data.get('nick', '')))
        alt = net_data.get('alt_nicks')
        self.alt_nicks.setPlainText('\n'.join(alt) if alt else '')
        self.user.setText(str(net_data.get('user', '')))
        self.realname.setText(str(net_data.get('realname', '')))
        self.password.setText(str(net_data.get('password', '')))
        def _load_override(combo, net_data, key, global_default):
            if key not in net_data:
                combo.setCurrentIndex(0)  # Inherit
                # Update the Inherit label to show effective value
                inherited = 'on' if global_default else 'off'
                combo.setItemText(0, 'Inherit (%s)' % inherited)
            elif net_data[key]:
                combo.setCurrentIndex(1)  # On
                combo.setItemText(0, 'Inherit')
            else:
                combo.setCurrentIndex(2)  # Off
                combo.setItemText(0, 'Inherit')

        _load_override(self.auto_connect, net_data, 'auto_connect',
                       bool(self._global_data.get('auto_connect', True)))
        _load_override(self.persist_autojoins, net_data, 'persist_autojoins',
                       bool(self._global_data.get('persist_autojoins', False)))
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

        def _save_override(combo, net_data, key):
            idx = combo.currentIndex()
            if idx == 0:  # Inherit
                if key in net_data:
                    del net_data[key]
            elif idx == 1:  # On
                net_data[key] = True
            else:  # Off
                net_data[key] = False

        _save_override(self.auto_connect, net_data, 'auto_connect')
        _save_override(self.persist_autojoins, net_data, 'persist_autojoins')


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

