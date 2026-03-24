import os

from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QSpinBox, QLabel,
    QGridLayout, QGroupBox, QComboBox, QLineEdit, QHBoxLayout,
    QPushButton,
)
from PySide6.QtCore import Qt
from settings.page_general import _ck


class NotificationsPage(QWidget):
    """Notification settings: sound and desktop alerts per event."""

    _EVENTS = [
        ('notice',         'Notice received'),
        ('new_query',      'New private message'),
        ('highlight',      'Highlight matched'),
        ('connect',        'Server connected'),
        ('disconnect',     'Server disconnected'),
        ('notify_online',  'Notify: user online'),
        ('notify_offline', 'Notify: user offline'),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        # Populate system sounds list
        from notify import get_system_sounds
        self._system_sounds = get_system_sounds()

        # Default sound
        default_row = QHBoxLayout()
        self._default_sound = self._make_sound_combo(has_default=False)
        self._default_sound.currentIndexChanged.connect(self._on_default_changed)
        self._default_sound_path = QLineEdit()
        self._default_sound_path.setPlaceholderText("sound name or file path")
        self._default_sound_path.setVisible(False)
        default_browse = QPushButton("...")
        default_browse.setFixedWidth(30)
        default_browse.setToolTip("Browse system sounds")
        default_browse.clicked.connect(
            lambda: self._browse_sound(self._default_sound, self._default_sound_path))
        default_row.addWidget(self._default_sound)
        default_row.addWidget(self._default_sound_path, 1)
        default_row.addWidget(default_browse)
        layout.addRow("Default sound:", default_row)

        # Grid: event rows x (sound, desktop) columns
        group = QGroupBox("Events")
        grid = QGridLayout(group)
        grid.addWidget(QLabel("Sound"), 0, 1)
        grid.addWidget(QLabel("Desktop"), 0, 2)

        self._event_widgets = {}  # key -> (sound_combo, sound_path, desktop_cb)
        _tooltips = {
            'highlight': 'Triggered by patterns in the "highlights" config list.\n'
                         'Use "{me}" in a pattern to match your current nickname.',
        }
        for row, (key, label) in enumerate(self._EVENTS, start=1):
            lbl = QLabel(label)
            if key in _tooltips:
                lbl.setToolTip(_tooltips[key])
            grid.addWidget(lbl, row, 0)

            sound_w = QHBoxLayout()
            combo = self._make_sound_combo(has_default=True)
            path_edit = QLineEdit()
            path_edit.setPlaceholderText("sound name or file path")
            path_edit.setVisible(False)
            combo.currentIndexChanged.connect(
                lambda _, c=combo, p=path_edit: p.setVisible(c.currentData() == "__custom__"))
            browse = QPushButton("...")
            browse.setFixedWidth(30)
            browse.setToolTip("Browse system sounds")
            browse.clicked.connect(
                lambda _, c=combo, p=path_edit: self._browse_sound(c, p))
            sound_w.addWidget(combo)
            sound_w.addWidget(path_edit, 1)
            sound_w.addWidget(browse)

            container = QWidget()
            container.setLayout(sound_w)
            grid.addWidget(container, row, 1)

            desktop_cb = QCheckBox()
            grid.addWidget(desktop_cb, row, 2)
            self._event_widgets[key] = (combo, path_edit, desktop_cb)

        layout.addRow(group)

        # Hint about highlight patterns
        self._highlight_hint = QLabel()
        self._highlight_hint.setWordWrap(True)
        from settings import SETTINGS_HINT_STYLE
        # Use hint style but with warning color instead of gray
        self._highlight_hint.setStyleSheet(SETTINGS_HINT_STYLE.replace('gray', '#996600'))
        self._highlight_hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addRow(self._highlight_hint)

        self.check_interval = _ck(QSpinBox(), 'notifications.check_interval')
        self.check_interval.setRange(10, 600)
        self.check_interval.setSuffix(" s")
        layout.addRow("Notify check interval:", self.check_interval)

        tip = QLabel("For per-nick, per-channel, or per-pattern notifications, "
                     "use /on commands with -s/-d/-h flags. Add them to "
                     "your startup commands file to persist across restarts.")
        tip.setWordWrap(True)
        from settings import SETTINGS_HINT_STYLE
        tip.setStyleSheet(SETTINGS_HINT_STYLE)
        layout.addRow(tip)


    def _make_sound_combo(self, has_default=True):
        """Create a sound combo box with recent and system sounds."""
        import state
        combo = QComboBox()
        if has_default:
            combo.addItem("Default", "default")
        combo.addItem("System beep", "beep")
        combo.addItem("None", "none")
        # Recent sounds
        recent = state.ui_state.recent_sounds if state.ui_state else []
        if recent:
            combo.insertSeparator(combo.count())
            for val in recent:
                label = os.path.basename(val) if os.path.sep in val or '/' in val else val
                combo.addItem("* " + label, val)
        # System sounds
        if self._system_sounds:
            combo.insertSeparator(combo.count())
            for name, path in self._system_sounds:
                combo.addItem(name, name)
        combo.insertSeparator(combo.count())
        combo.addItem("Custom...", "__custom__")
        return combo

    def _browse_sound(self, combo, path_edit):
        """Open the sound browser in select mode and apply the result."""
        from notify import show_sound_browser
        name = show_sound_browser(select=True)
        if name:
            idx = combo.findData(name)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentIndex(combo.findData('__custom__'))
                path_edit.setText(name)
                path_edit.setVisible(True)

    def _on_default_changed(self, _index):
        self._default_sound_path.setVisible(
            self._default_sound.currentData() == "__custom__")

    def _set_sound_combo(self, combo, path_edit, value):
        """Set a sound combo+path from a config value."""
        if value in (True, 'beep'):
            combo.setCurrentIndex(combo.findData('beep'))
        elif value in (False, None, 'none', ''):
            combo.setCurrentIndex(combo.findData('none'))
        elif value == 'default':
            idx = combo.findData('default')
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentIndex(combo.findData('beep'))
        else:
            # Check if it's a system sound name
            idx = combo.findData(str(value))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                # Custom path
                combo.setCurrentIndex(combo.findData('__custom__'))
                path_edit.setText(str(value))
                path_edit.setVisible(True)

    def _get_sound_value(self, combo, path_edit):
        """Get the config value from a sound combo+path."""
        data = combo.currentData()
        if data == '__custom__':
            return path_edit.text().strip() or 'none'
        return data

    def load_from_data(self, data):
        notif = data.get('notifications') or {}

        # Default sound
        default_snd = notif.get('sound', 'beep')
        self._set_sound_combo(self._default_sound, self._default_sound_path,
                              default_snd if default_snd else 'beep')

        defaults = {
            'notice':         ('none', False),
            'new_query':      ('default', True),
            'highlight':      ('default', True),
            'connect':        ('none', False),
            'disconnect':     ('none', False),
            'notify_online':  ('default', True),
            'notify_offline': ('none', False),
        }
        for key, (combo, path_edit, desktop_cb) in self._event_widgets.items():
            sub = notif.get(key) or {}
            sound_def, desktop_def = defaults.get(key, ('none', False))
            # Read sound: 'sound' key takes priority, fall back to legacy 'beep'
            if 'sound' in sub:
                snd = sub['sound']
            elif 'beep' in sub:
                snd = sub['beep']
            else:
                snd = sound_def
            # Normalize legacy bools
            if snd is True:
                snd = 'default'
            elif snd is False:
                snd = 'none'
            self._set_sound_combo(combo, path_edit, snd)
            desktop_cb.setChecked(bool(sub.get('desktop', desktop_def)))

        self.check_interval.setValue(int(notif.get('check_interval', 60)))

        # Update highlight hint
        highlights = data.get('highlights') or []
        has_me = any('{me}' in str(p) or '{nick}' in str(p) for p in highlights)
        if not highlights:
            self._highlight_hint.setText(
                'Note: Your highlights list is empty. "Highlight matched" '
                'notifications will never fire. Add "{me}" to the highlights '
                'list in Settings \u2192 Lists to be notified when your nick '
                'is mentioned.')
        elif not has_me:
            self._highlight_hint.setText(
                'Note: Your highlights list does not contain "{me}". '
                'You will not be notified when your nick is mentioned in channels.')
        else:
            self._highlight_hint.setText('')

    def save_to_data(self, data):
        import state
        from ruamel.yaml.comments import CommentedMap
        notif = data.get('notifications')
        if notif is None:
            notif = CommentedMap()
            data['notifications'] = notif

        # Default sound
        default_snd = self._get_sound_value(
            self._default_sound, self._default_sound_path)
        notif['sound'] = default_snd
        if state.ui_state:
            state.ui_state.add_recent_sound(default_snd)

        for key, (combo, path_edit, desktop_cb) in self._event_widgets.items():
            sub = notif.get(key)
            if sub is None:
                sub = CommentedMap()
                notif[key] = sub
            snd = self._get_sound_value(combo, path_edit)
            sub['sound'] = snd
            if state.ui_state:
                state.ui_state.add_recent_sound(snd)
            # Remove legacy 'beep' key if present
            if 'beep' in sub:
                del sub['beep']
            sub['desktop'] = desktop_cb.isChecked()

        notif['check_interval'] = self.check_interval.value()
