from PySide6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QPlainTextEdit, QLabel,
)
from settings.page_general import _ck


class IdentityPage(QWidget):
    """IRC identity settings (nick, user, realname, alt_nicks, CTCP responses)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        from PySide6.QtGui import QFontMetrics
        _fm = QFontMetrics(self.font())
        _short_w = _fm.horizontalAdvance('M' * 20) + _fm.height() * 2
        _long_w = _fm.horizontalAdvance('M' * 35) + _fm.height() * 2

        self.nick = _ck(QLineEdit(), 'nick')
        self.nick.setFixedWidth(_short_w)
        layout.addRow("Nick:", self.nick)

        self.alt_nicks = _ck(QPlainTextEdit(), 'alt_nicks')
        self.alt_nicks.setMaximumHeight(80)
        self.alt_nicks.setFixedWidth(_short_w)
        layout.addRow("Alt nicks (one per line):", self.alt_nicks)

        self.user = _ck(QLineEdit(), 'user')
        self.user.setFixedWidth(_short_w)
        layout.addRow("Username:", self.user)

        self.realname = _ck(QLineEdit(), 'realname')
        self.realname.setFixedWidth(_long_w)
        layout.addRow("Real name:", self.realname)

        layout.addRow(QLabel(""))  # spacer

        self.ctcp_version = _ck(QLineEdit(), 'ctcp.version')
        self.ctcp_version.setFixedWidth(_long_w)
        self.ctcp_version.setPlaceholderText("(default: qtpyrc version info)")
        self.ctcp_version.setToolTip("Response to CTCP VERSION requests.\n{app_version} expands to the version from config.")
        layout.addRow("CTCP version:", self.ctcp_version)

        self.ctcp_finger = _ck(QLineEdit(), 'ctcp.finger')
        self.ctcp_finger.setFixedWidth(_long_w)
        self.ctcp_finger.setPlaceholderText("(default: same as version)")
        self.ctcp_finger.setToolTip("Response to CTCP FINGER requests.\n{app_version} expands to the version from config.")
        layout.addRow("CTCP finger:", self.ctcp_finger)

        self.auto_connect = QLineEdit()
        self.auto_connect.setReadOnly(True)
        self.auto_connect.setVisible(False)

    def load_from_data(self, data):
        self.nick.setText(str(data.get('nick', '')))
        alts = data.get('alt_nicks') or []
        self.alt_nicks.setPlainText('\n'.join(str(a) for a in alts))
        self.user.setText(str(data.get('user', '')))
        self.realname.setText(str(data.get('realname', '')))
        self.realname.setCursorPosition(0)
        ctcp = data.get('ctcp') or {}
        self.ctcp_version.setText(str(ctcp.get('version', '')))
        self.ctcp_finger.setText(str(ctcp.get('finger', '')))

    def save_to_data(self, data):
        data['nick'] = self.nick.text()
        text = self.alt_nicks.toPlainText().strip()
        data['alt_nicks'] = [line.strip() for line in text.split('\n') if line.strip()] if text else []
        data['user'] = self.user.text()
        data['realname'] = self.realname.text()
        from ruamel.yaml.comments import CommentedMap
        ctcp = data.get('ctcp')
        if ctcp is None:
            ctcp = CommentedMap()
            data['ctcp'] = ctcp
        v = self.ctcp_version.text().strip()
        if v:
            ctcp['version'] = v
        elif 'version' in ctcp:
            del ctcp['version']
        f = self.ctcp_finger.text().strip()
        if f:
            ctcp['finger'] = f
        elif 'finger' in ctcp:
            del ctcp['finger']
