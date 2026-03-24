from PySide6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QComboBox, QCheckBox,
)


class SASLPage(QWidget):
    """SASL authentication settings for a network."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.enabled = QCheckBox()
        layout.addRow("Enable SASL:", self.enabled)

        self.mechanism = QComboBox()
        self.mechanism.addItems(["PLAIN", "EXTERNAL"])
        layout.addRow("Mechanism:", self.mechanism)

        self.username = QLineEdit()
        layout.addRow("Username:", self.username)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Password:", self.password)

    def load_from_data(self, net_data):
        sasl = net_data.get('sasl') or {}
        has_sasl = bool(sasl.get('mechanism') or sasl.get('username'))
        self.enabled.setChecked(has_sasl)
        idx = self.mechanism.findText(str(sasl.get('mechanism', 'PLAIN')))
        if idx >= 0:
            self.mechanism.setCurrentIndex(idx)
        self.username.setText(str(sasl.get('username', '')))
        self.password.setText(str(sasl.get('password', '')))

    def save_to_data(self, net_data):
        if not self.enabled.isChecked():
            if 'sasl' in net_data:
                del net_data['sasl']
            return
        if 'sasl' not in net_data or net_data['sasl'] is None:
            from ruamel.yaml.comments import CommentedMap
            net_data['sasl'] = CommentedMap()
        sasl = net_data['sasl']
        sasl['mechanism'] = self.mechanism.currentText()
        sasl['username'] = self.username.text()
        sasl['password'] = self.password.text()
