from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QLineEdit,
)
from settings.page_general import _ck


class LoggingPage(QWidget):
    """Logging settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self.log_dir = _ck(QLineEdit(), 'logging.dir')
        self.log_dir.setMinimumWidth(200)
        layout.addRow("Log directory:", self.log_dir)

        self.use_subdirs = _ck(QCheckBox(), 'logging.use_subdirs')
        layout.addRow("Use subdirectories:", self.use_subdirs)

        self.separate_by_month = _ck(QCheckBox(), 'logging.separate_by_month')
        layout.addRow("Separate by month:", self.separate_by_month)

        self.debug = _ck(QCheckBox(), 'logging.debug')
        layout.addRow("Debug logging:", self.debug)

        self.timestamp = _ck(QLineEdit(), 'logging.timestamp')
        self.timestamp.setMinimumWidth(200)
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
