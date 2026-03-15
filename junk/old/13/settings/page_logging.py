from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QLineEdit,
)


class LoggingPage(QWidget):
    """Logging settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.log_dir = QLineEdit()
        layout.addRow("Log directory:", self.log_dir)

        self.use_subdirs = QCheckBox()
        layout.addRow("Use subdirectories:", self.use_subdirs)

        self.separate_by_month = QCheckBox()
        layout.addRow("Separate by month:", self.separate_by_month)

        self.debug = QCheckBox()
        layout.addRow("Debug logging:", self.debug)

        self.timestamp = QLineEdit()
        self.timestamp.setPlaceholderText("YYYY-MM-DD HH:MM:SS")
        self.timestamp.setToolTip(
            "Tokens: YYYY YY MM DD HH hh MI SS AP ap DOW MON"
        )
        layout.addRow("Log timestamp:", self.timestamp)

    def load_from_data(self, data):
        log = data.get('logging') or {}
        self.log_dir.setText(str(log.get('dir', 'logs')))
        self.use_subdirs.setChecked(bool(log.get('use_subdirs', False)))
        self.separate_by_month.setChecked(bool(log.get('separate_by_month', False)))
        self.debug.setChecked(bool(log.get('debug', False)))
        self.timestamp.setText(str(log.get('timestamp', 'YYYY-MM-DD HH:MM:SS')))

    def save_to_data(self, data):
        if 'logging' not in data or data['logging'] is None:
            from ruamel.yaml.comments import CommentedMap
            data['logging'] = CommentedMap()
        log = data['logging']
        log['dir'] = self.log_dir.text()
        log['use_subdirs'] = self.use_subdirs.isChecked()
        log['separate_by_month'] = self.separate_by_month.isChecked()
        log['debug'] = self.debug.isChecked()
        log['timestamp'] = self.timestamp.text()
