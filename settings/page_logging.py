from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QLineEdit, QDoubleSpinBox, QLabel,
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

        # --- Freeze detection (hang watchdog) ---
        header = QLabel("<b>Freeze detection</b>")
        layout.addRow(header)

        self.hw_enabled = _ck(QCheckBox(), 'logging.hang_watchdog.enabled')
        layout.addRow("Detect UI freezes:", self.hw_enabled)

        self.hw_threshold = _ck(QDoubleSpinBox(), 'logging.hang_watchdog.threshold')
        self.hw_threshold.setRange(0.25, 120.0)
        self.hw_threshold.setSingleStep(0.5)
        self.hw_threshold.setDecimals(2)
        self.hw_threshold.setSuffix(" s")
        layout.addRow("Freeze threshold:", self.hw_threshold)

        self.hw_file = _ck(QLineEdit(), 'logging.hang_watchdog.file')
        self.hw_file.setMinimumWidth(200)
        layout.addRow("Freeze report file:", self.hw_file)

        self.hw_native = _ck(QCheckBox(), 'logging.hang_watchdog.native_stacks')
        layout.addRow("Native stacks (py-spy):", self.hw_native)

        # --- Duplicate-render audit ---
        header2 = QLabel("<b>Duplicate-message detection</b>")
        layout.addRow(header2)

        self.ra_enabled = _ck(QCheckBox(), 'logging.render_audit.enabled')
        layout.addRow("Detect duplicate lines:", self.ra_enabled)

        self.ra_window = _ck(QDoubleSpinBox(), 'logging.render_audit.window')
        self.ra_window.setRange(1.0, 3600.0)
        self.ra_window.setSingleStep(30.0)
        self.ra_window.setDecimals(0)
        self.ra_window.setSuffix(" s")
        layout.addRow("Look-back window:", self.ra_window)

        self.ra_file = _ck(QLineEdit(), 'logging.render_audit.file')
        self.ra_file.setMinimumWidth(200)
        layout.addRow("Duplicate report file:", self.ra_file)

    def load_from_data(self, data):
        log = data.get('logging') or {}
        self.log_dir.setText(str(log.get('dir', 'logs')))
        self.use_subdirs.setChecked(bool(log.get('use_subdirs', False)))
        self.separate_by_month.setChecked(bool(log.get('separate_by_month', False)))
        self.debug.setChecked(bool(log.get('debug', False)))
        # mm is minutes, MM is the month (config._format_timestamp) -- so the
        # 'HH:MM:SS' this used to offer wrote the month into the minutes field,
        # and did it silently: the dialog fills the box with its default, and
        # saving writes that back, so merely opening this page once put a
        # broken format in the user's config and every log line after it read
        # 11:08:38 in August. Keep in step with config.py's default.
        self.timestamp.setText(str(log.get('timestamp', 'YYYY-MM-DD HH:mm:SS')))

        hw = log.get('hang_watchdog') or {}
        self.hw_enabled.setChecked(bool(hw.get('enabled', True)))
        try:
            self.hw_threshold.setValue(float(hw.get('threshold', 2.0)))
        except (TypeError, ValueError):
            self.hw_threshold.setValue(2.0)
        self.hw_file.setText(str(hw.get('file', 'hangs.log')))
        self.hw_native.setChecked(bool(hw.get('native_stacks', True)))

        ra = log.get('render_audit') or {}
        self.ra_enabled.setChecked(bool(ra.get('enabled', True)))
        try:
            self.ra_window.setValue(float(ra.get('window', 120.0)))
        except (TypeError, ValueError):
            self.ra_window.setValue(120.0)
        self.ra_file.setText(str(ra.get('file', 'renders.log')))

    def save_to_data(self, data):
        from ruamel.yaml.comments import CommentedMap
        if 'logging' not in data or data['logging'] is None:
            data['logging'] = CommentedMap()
        log = data['logging']
        log['dir'] = self.log_dir.text()
        log['use_subdirs'] = self.use_subdirs.isChecked()
        log['separate_by_month'] = self.separate_by_month.isChecked()
        log['debug'] = self.debug.isChecked()
        log['timestamp'] = self.timestamp.text()

        if 'hang_watchdog' not in log or log['hang_watchdog'] is None:
            log['hang_watchdog'] = CommentedMap()
        hw = log['hang_watchdog']
        hw['enabled'] = self.hw_enabled.isChecked()
        hw['threshold'] = float(self.hw_threshold.value())
        hw['file'] = self.hw_file.text()
        hw['native_stacks'] = self.hw_native.isChecked()

        if 'render_audit' not in log or log['render_audit'] is None:
            log['render_audit'] = CommentedMap()
        ra = log['render_audit']
        ra['enabled'] = self.ra_enabled.isChecked()
        ra['window'] = float(self.ra_window.value())
        ra['file'] = self.ra_file.text()
