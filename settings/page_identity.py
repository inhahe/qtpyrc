from PySide6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QPlainTextEdit,
)
from settings.page_general import _ck


class IdentityPage(QWidget):
    """IRC identity settings (nick, user, realname, alt_nicks)."""

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

    def save_to_data(self, data):
        data['nick'] = self.nick.text()
        text = self.alt_nicks.toPlainText().strip()
        data['alt_nicks'] = [line.strip() for line in text.split('\n') if line.strip()] if text else []
        data['user'] = self.user.text()
        data['realname'] = self.realname.text()
