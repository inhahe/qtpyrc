from PySide6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QPlainTextEdit, QLabel,
)


class IdentityPage(QWidget):
    """IRC identity settings (nick, user, realname, alt_nicks)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.nick = QLineEdit()
        layout.addRow("Nick:", self.nick)

        self.alt_nicks = QPlainTextEdit()
        self.alt_nicks.setMaximumHeight(80)
        layout.addRow("Alt nicks (one per line):", self.alt_nicks)

        self.user = QLineEdit()
        layout.addRow("Username:", self.user)

        self.realname = QLineEdit()
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

    def save_to_data(self, data):
        data['nick'] = self.nick.text()
        text = self.alt_nicks.toPlainText().strip()
        data['alt_nicks'] = [line.strip() for line in text.split('\n') if line.strip()] if text else []
        data['user'] = self.user.text()
        data['realname'] = self.realname.text()
