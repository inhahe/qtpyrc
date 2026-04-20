from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QLineEdit, QComboBox,
    QSpinBox, QDoubleSpinBox, QLabel, QFrame,
)
from PySide6.QtCore import Qt


def _separator(layout, text):
    """Add a labeled separator line to a form layout."""
    lbl = QLabel('<b>%s</b>' % text)
    lbl.setStyleSheet('margin-top: 8px;')
    layout.addRow(lbl)


def _ck(widget, key):
    """Tag a widget with its config.defaults.yaml key for auto-tooltip/defaults."""
    widget.setProperty('config_key', key)
    return widget


class GeneralPage(QWidget):
    """General behavior settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self.input_lines = _ck(QSpinBox(), 'input_lines')
        self.input_lines.setRange(1, 10)
        layout.addRow("Input lines:", self.input_lines)

        self.command_prefix = _ck(QLineEdit(), 'command_prefix')
        layout.addRow("Command prefix:", self.command_prefix)

        self.plugin_prefix = _ck(QLineEdit(), 'plugin_prefix')
        self.plugin_prefix.setToolTip("Prefix for plugin channel commands (e.g. !trivia, !chess)")
        layout.addRow("Plugin prefix:", self.plugin_prefix)

        self.timestamp_display = _ck(QLineEdit(), 'timestamps.display')
        layout.addRow("Display timestamp:", self.timestamp_display)

        _separator(layout, 'Behavior')
        self.close_on_kick = _ck(QCheckBox(), 'close_on_kick')
        layout.addRow("Close on kick:", self.close_on_kick)

        self.close_on_disconnect = _ck(QCheckBox(), 'close_on_disconnect')
        layout.addRow("Close on disconnect:", self.close_on_disconnect)

        self.show_mode_prefix_nicklist = _ck(QCheckBox(), 'show_mode_prefix_nicklist')
        layout.addRow("Mode prefixes (nick list):", self.show_mode_prefix_nicklist)

        self.show_mode_prefix_messages = _ck(QCheckBox(), 'show_mode_prefix_messages')
        layout.addRow("Mode prefixes (messages):", self.show_mode_prefix_messages)

        self.auto_copy_selection = _ck(QCheckBox(), 'auto_copy_selection')
        layout.addRow("Auto-copy selection:", self.auto_copy_selection)

        self.whois_on_query = _ck(QCheckBox(), 'whois_on_query')
        layout.addRow("Whois on new query:", self.whois_on_query)

        self.tab_complete_age = _ck(QSpinBox(), 'tab_complete_age')
        self.tab_complete_age.setRange(0, 86400)
        self.tab_complete_age.setSpecialValueText("(no limit)")
        self.tab_complete_age.setSuffix(" s")
        layout.addRow("Tab complete age:", self.tab_complete_age)

        self.auto_connect = _ck(QCheckBox(), 'auto_connect')
        layout.addRow("Auto-connect:", self.auto_connect)

        self.persist_autojoins = _ck(QCheckBox(), 'persist_autojoins')
        layout.addRow("Persist auto-joins:", self.persist_autojoins)

        _separator(layout, 'History')
        self.backscroll_limit = _ck(QSpinBox(), 'backscroll_limit')
        self.backscroll_limit.setRange(0, 1000000)
        self.backscroll_limit.setSpecialValueText("unlimited")
        layout.addRow("Backscroll limit:", self.backscroll_limit)

        self.history_replay_channels = _ck(QSpinBox(), 'history_replay.channels')
        self.history_replay_channels.setRange(0, 1000000)
        self.history_replay_channels.setSpecialValueText("disabled")
        layout.addRow("Channel history:", self.history_replay_channels)

        self.history_replay_queries = _ck(QSpinBox(), 'history_replay.queries')
        self.history_replay_queries.setRange(0, 1000000)
        self.history_replay_queries.setSpecialValueText("disabled")
        layout.addRow("Query history:", self.history_replay_queries)

        self.bg_replay_enabled = _ck(QCheckBox(), 'history_replay.bg_enabled')
        layout.addRow("Background replay:", self.bg_replay_enabled)

        self.bg_chunk = _ck(QSpinBox(), 'history_replay.bg_chunk')
        self.bg_chunk.setRange(10, 1000)
        layout.addRow("  Chunk size:", self.bg_chunk)

        self.bg_interval = _ck(QSpinBox(), 'history_replay.bg_interval')
        self.bg_interval.setRange(10, 2000)
        self.bg_interval.setSuffix(" ms")
        layout.addRow("  Chunk interval:", self.bg_interval)

        self.bg_replay_enabled.toggled.connect(self.bg_chunk.setEnabled)
        self.bg_replay_enabled.toggled.connect(self.bg_interval.setEnabled)

        _separator(layout, 'Flood Control')
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

        _separator(layout, 'Typing Notifications')
        self.typing_send = _ck(QCheckBox(), 'typing.send')
        layout.addRow("Send typing:", self.typing_send)

        self.typing_show = _ck(QCheckBox(), 'typing.show')
        layout.addRow("Show typing:", self.typing_show)

    def load_from_data(self, data):
        self.input_lines.setValue(max(1, min(10, int(data.get('input_lines', 1)))))
        self.command_prefix.setText(str(data.get('command_prefix', '/')))
        self.plugin_prefix.setText(str(data.get('plugin_prefix', '!')))
        ts = data.get('timestamps') or {}
        self.timestamp_display.setText(str(ts.get('display', 'HH:mm')))
        self.close_on_kick.setChecked(bool(data.get('close_on_kick', False)))
        self.close_on_disconnect.setChecked(bool(data.get('close_on_disconnect', False)))
        # Legacy migration: old single bool -> two new bools
        if 'show_mode_prefix' in data and 'show_mode_prefix_nicklist' not in data:
            val = bool(data.get('show_mode_prefix', False))
            self.show_mode_prefix_nicklist.setChecked(val)
            self.show_mode_prefix_messages.setChecked(val)
        else:
            self.show_mode_prefix_nicklist.setChecked(bool(data.get('show_mode_prefix_nicklist', True)))
            self.show_mode_prefix_messages.setChecked(bool(data.get('show_mode_prefix_messages', True)))
        self.auto_copy_selection.setChecked(bool(data.get('auto_copy_selection', False)))
        self.whois_on_query.setChecked(bool(data.get('whois_on_query', False)))
        self.tab_complete_age.setValue(int(data.get('tab_complete_age', 0) or 0))
        self.auto_connect.setChecked(bool(data.get('auto_connect', True)))
        self.persist_autojoins.setChecked(bool(data.get('persist_autojoins', False)))
        self.backscroll_limit.setValue(int(data.get('backscroll_limit', 10000)))
        hr = data.get('history_replay') or {}
        if isinstance(hr, int):
            self.history_replay_channels.setValue(hr)
            self.history_replay_queries.setValue(0)
        else:
            self.history_replay_channels.setValue(int(hr.get('channels', data.get('backscroll_limit', 10000))))
            self.history_replay_queries.setValue(int(hr.get('queries', 0)))
        if isinstance(hr, dict):
            self.bg_replay_enabled.setChecked(bool(hr.get('bg_enabled', True)))
            self.bg_chunk.setValue(int(hr.get('bg_chunk', 50)))
            self.bg_interval.setValue(int(hr.get('bg_interval', 100)))
        else:
            self.bg_replay_enabled.setChecked(True)
            self.bg_chunk.setValue(50)
            self.bg_interval.setValue(100)
        self.bg_chunk.setEnabled(self.bg_replay_enabled.isChecked())
        self.bg_interval.setEnabled(self.bg_replay_enabled.isChecked())
        flood = data.get('flood') or {}
        self.flood_burst.setValue(int(flood.get('burst', 0) or 0))
        self.flood_rate.setValue(float(flood.get('rate', 0.0) or 0.0))
        typing = data.get('typing') or {}
        self.typing_send.setChecked(bool(typing.get('send', True)))
        self.typing_show.setChecked(bool(typing.get('show', True)))

    def save_to_data(self, data):
        from ruamel.yaml.comments import CommentedMap
        data['input_lines'] = self.input_lines.value()
        if 'multiline_input' in data:
            del data['multiline_input']
        data['command_prefix'] = self.command_prefix.text()
        data['plugin_prefix'] = self.plugin_prefix.text()
        ts = data.get('timestamps')
        if ts is None:
            ts = CommentedMap()
            data['timestamps'] = ts
        ts['display'] = self.timestamp_display.text()
        data['close_on_kick'] = self.close_on_kick.isChecked()
        data['close_on_disconnect'] = self.close_on_disconnect.isChecked()
        data.pop('show_mode_prefix', None)  # remove legacy key
        data['show_mode_prefix_nicklist'] = self.show_mode_prefix_nicklist.isChecked()
        data['show_mode_prefix_messages'] = self.show_mode_prefix_messages.isChecked()
        data['auto_copy_selection'] = self.auto_copy_selection.isChecked()
        data['whois_on_query'] = self.whois_on_query.isChecked()
        tca = self.tab_complete_age.value()
        if tca > 0:
            data['tab_complete_age'] = tca
        elif 'tab_complete_age' in data:
            del data['tab_complete_age']
        data['auto_connect'] = self.auto_connect.isChecked()
        data['persist_autojoins'] = self.persist_autojoins.isChecked()
        data['backscroll_limit'] = self.backscroll_limit.value()
        hr = data.get('history_replay')
        if not isinstance(hr, dict):
            hr = CommentedMap()
            data['history_replay'] = hr
        hr['channels'] = self.history_replay_channels.value()
        hr['queries'] = self.history_replay_queries.value()
        hr['bg_enabled'] = self.bg_replay_enabled.isChecked()
        hr['bg_chunk'] = self.bg_chunk.value()
        hr['bg_interval'] = self.bg_interval.value()
        fb = self.flood_burst.value()
        fr = self.flood_rate.value()
        if fb > 0 or fr > 0:
            flood = data.get('flood')
            if flood is None:
                flood = CommentedMap()
                data['flood'] = flood
            if fb > 0:
                flood['burst'] = fb
            elif 'burst' in flood:
                del flood['burst']
            if fr > 0:
                flood['rate'] = fr
            elif 'rate' in flood:
                del flood['rate']
        elif 'flood' in data:
            del data['flood']
        typing = data.get('typing')
        if typing is None:
            typing = CommentedMap()
            data['typing'] = typing
        typing['send'] = self.typing_send.isChecked()
        typing['show'] = self.typing_show.isChecked()


class InterfacePage(QWidget):
    """Interface / layout settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self.window_mode = _ck(QComboBox(), 'window_mode')
        self.window_mode.addItems(["Maximized", "Normal", "Minimized", "Remember"])
        self.window_mode.setToolTip(
            "Maximized: always start maximized\n"
            "Normal: always start non-maximized (at last saved size/position)\n"
            "Minimized: always start minimized to taskbar\n"
            "Remember: start in whatever state it was last closed in")
        layout.addRow("Window mode:", self.window_mode)

        self.navigation = _ck(QComboBox(), 'navigation')
        self.navigation.addItems(["Tabs", "Tree", "Both"])
        layout.addRow("Navigation:", self.navigation)

        self.tab_rows = _ck(QSpinBox(), 'tab_rows')
        self.tab_rows.setRange(0, 20)
        self.tab_rows.setSpecialValueText("(auto)")
        layout.addRow("Tab rows:", self.tab_rows)

        self.new_tab_state = _ck(QComboBox(), 'new_tab_state')
        self.new_tab_state.addItems(["active", "normal", "skipped"])
        layout.addRow("New tab state:", self.new_tab_state)

        self.show_toolbar = _ck(QCheckBox(), 'show_toolbar')
        layout.addRow("Show toolbar:", self.show_toolbar)

        self.toolbar_icon_size = _ck(QSpinBox(), 'toolbar_icon_size')
        self.toolbar_icon_size.setRange(0, 64)
        self.toolbar_icon_size.setSpecialValueText("(default)")
        layout.addRow("Toolbar icon size:", self.toolbar_icon_size)

    def _load_combo(self, combo, value):
        idx = combo.findText(str(value), Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def load_from_data(self, data):
        self._load_combo(self.window_mode, data.get('window_mode', 'remember'))
        nav = data.get('navigation')
        if not nav:
            nav = 'both' if data.get('treeview', False) else 'tabs'
        self._load_combo(self.navigation, nav)
        self.tab_rows.setValue(int(data.get('tab_rows', 0) or 0))
        self._load_combo(self.new_tab_state, data.get('new_tab_state', 'active'))
        self.show_toolbar.setChecked(bool(data.get('show_toolbar', True)))
        self.toolbar_icon_size.setValue(int(data.get('toolbar_icon_size', 0) or 0))

    def save_to_data(self, data):
        data['window_mode'] = self.window_mode.currentText().lower()
        data['navigation'] = self.navigation.currentText().lower()
        if 'treeview' in data:
            del data['treeview']
        tr = self.tab_rows.value()
        if tr > 0:
            data['tab_rows'] = tr
        elif 'tab_rows' in data:
            del data['tab_rows']
        data['new_tab_state'] = self.new_tab_state.currentText()
        data['show_toolbar'] = self.show_toolbar.isChecked()
        tis = self.toolbar_icon_size.value()
        if tis > 0:
            data['toolbar_icon_size'] = tis
        elif 'toolbar_icon_size' in data:
            del data['toolbar_icon_size']


class TitlesPage(QWidget):
    """Window title format settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.titlebar_format = _ck(QLineEdit(), 'titlebar_format')
        layout.addRow("Titlebar format:", self.titlebar_format)

        self.titlebar_interval = _ck(QDoubleSpinBox(), 'titlebar_interval')
        self.titlebar_interval.setRange(0.1, 60.0)
        self.titlebar_interval.setDecimals(1)
        self.titlebar_interval.setSuffix(" s")
        layout.addRow("Titlebar interval:", self.titlebar_interval)

        _separator(layout, 'Window Titles')
        layout.addRow(QLabel("Leave blank for default. Variables: {me} {channel} {topic} {network_label} {query_nick}"))

        self.title_server = _ck(QLineEdit(), 'titles.server')
        layout.addRow("Server:", self.title_server)

        self.title_server_dc = _ck(QLineEdit(), 'titles.server_disconnected')
        layout.addRow("Server (disconn.):", self.title_server_dc)

        self.title_channel = _ck(QLineEdit(), 'titles.channel')
        layout.addRow("Channel:", self.title_channel)

        self.title_query = _ck(QLineEdit(), 'titles.query')
        layout.addRow("Query:", self.title_query)

    def _save_optional(self, data, key, widget):
        val = widget.text().strip()
        if val:
            data[key] = val
        elif key in data:
            del data[key]

    def load_from_data(self, data):
        self.titlebar_format.setText(str(data.get('titlebar_format', '') or ''))
        self.titlebar_interval.setValue(float(data.get('titlebar_interval', 1.0) or 1.0))
        titles = data.get('titles') or {}
        self.title_server.setText(str(titles.get('server', '') or ''))
        self.title_server_dc.setText(str(titles.get('server_disconnected', '') or ''))
        self.title_channel.setText(str(titles.get('channel', '') or ''))
        self.title_query.setText(str(titles.get('query', '') or ''))

    def save_to_data(self, data):
        from ruamel.yaml.comments import CommentedMap
        self._save_optional(data, 'titlebar_format', self.titlebar_format)
        data['titlebar_interval'] = self.titlebar_interval.value()
        titles = data.get('titles')
        if titles is None:
            titles = CommentedMap()
            data['titles'] = titles
        self._save_optional(titles, 'server', self.title_server)
        self._save_optional(titles, 'server_disconnected', self.title_server_dc)
        self._save_optional(titles, 'channel', self.title_channel)
        self._save_optional(titles, 'query', self.title_query)


class FilesPage(QWidget):
    """File path settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.history_file = _ck(QLineEdit(), 'history_file')
        layout.addRow("History file:", self.history_file)

        self.popups_file = _ck(QLineEdit(), 'popups_file')
        layout.addRow("Popups file:", self.popups_file)

        self.toolbar_file = _ck(QLineEdit(), 'toolbar_file')
        layout.addRow("Toolbar file:", self.toolbar_file)

        self.variables_file = _ck(QLineEdit(), 'variables_file')
        layout.addRow("Variables file:", self.variables_file)

    def _save_optional(self, data, key, widget):
        val = widget.text().strip()
        if val:
            data[key] = val
        elif key in data:
            del data[key]

    def load_from_data(self, data):
        self.history_file.setText(str(data.get('history_file', 'history.db') or 'history.db'))
        self.popups_file.setText(str(data.get('popups_file', 'popups.ini') or 'popups.ini'))
        self.toolbar_file.setText(str(data.get('toolbar_file', 'toolbar.ini') or 'toolbar.ini'))
        self.variables_file.setText(str(data.get('variables_file', 'variables.ini') or 'variables.ini'))

    def save_to_data(self, data):
        self._save_optional(data, 'history_file', self.history_file)
        self._save_optional(data, 'popups_file', self.popups_file)
        self._save_optional(data, 'toolbar_file', self.toolbar_file)
        self._save_optional(data, 'variables_file', self.variables_file)
