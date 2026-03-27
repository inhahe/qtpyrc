from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QLineEdit, QSpinBox,
)
from settings.page_general import _ck


class IdentServerPage(QWidget):
    """Ident (RFC 1413) server settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self.enabled = _ck(QCheckBox(), 'ident.enabled')
        layout.addRow("Enable ident server:", self.enabled)

        self.host = _ck(QLineEdit(), 'ident.host')
        self.host.setMinimumWidth(200)
        layout.addRow("Listen host:", self.host)

        self.port = _ck(QSpinBox(), 'ident.port')
        self.port.setRange(1, 65535)
        layout.addRow("Listen port:", self.port)

        self.username = _ck(QLineEdit(), 'ident_username')
        self.username.setMinimumWidth(200)
        layout.addRow("Ident username:", self.username)

    def load_from_data(self, data):
        ident = data.get('ident') or {}
        self.enabled.setChecked(bool(ident.get('enabled', True)))
        self.host.setText(str(ident.get('host', '0.0.0.0')))
        self.port.setValue(int(ident.get('port', 113)))
        self.username.setText(str(data.get('ident_username', 'qtpyrc')))

    def save_to_data(self, data):
        if 'ident' not in data or data['ident'] is None:
            from ruamel.yaml.comments import CommentedMap
            data['ident'] = CommentedMap()
        ident = data['ident']
        ident['enabled'] = self.enabled.isChecked()
        ident['host'] = self.host.text()
        ident['port'] = self.port.value()
        data['ident_username'] = self.username.text()
