from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QLineEdit, QComboBox,
    QSpinBox, QDoubleSpinBox,
)
from PySide6.QtCore import Qt


class GeneralPage(QWidget):
    """General application settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.input_lines = QSpinBox()
        self.input_lines.setRange(1, 10)
        self.input_lines.setMaximumWidth(60)
        layout.addRow("Input lines:", self.input_lines)

        self.command_prefix = QLineEdit()
        self.command_prefix.setMaximumWidth(60)
        layout.addRow("Command prefix:", self.command_prefix)

        self.window_mode = QComboBox()
        self.window_mode.addItems(["Maximized", "Normal"])
        layout.addRow("Window mode:", self.window_mode)

        self.view_mode = QComboBox()
        self.view_mode.addItems(["Tabbed", "MDI"])
        self.view_mode.setToolTip("Tabbed: one window at a time with tabs. MDI: free-floating tiled/cascaded windows.")
        layout.addRow("View mode:", self.view_mode)

        self.nickname = QLineEdit()
        layout.addRow("Nickname (display):", self.nickname)

        self.timestamp_display = QLineEdit()
        self.timestamp_display.setPlaceholderText("HH:mm")
        self.timestamp_display.setToolTip(
            "Tokens: YYYY YY MM MON DD DOW HH hh mm MI SS AP ap\n"
            "MM=month, mm=minutes, MON=month name, DOW=weekday name\n"
            "Examples: HH:mm  |  HH:mm:SS  |  hh:mm ap  |  DOW HH:mm"
        )
        layout.addRow("Display timestamp:", self.timestamp_display)

        self.navigation = QComboBox()
        self.navigation.addItems(["Tabs", "Tree", "Both"])
        self.navigation.setToolTip("Show tabs bar, tree view, or both")
        layout.addRow("Navigation:", self.navigation)

        self.close_on_kick = QCheckBox()
        self.close_on_kick.setToolTip("Close channel windows when kicked from a channel")
        layout.addRow("Close on kick:", self.close_on_kick)

        self.close_on_disconnect = QCheckBox()
        self.close_on_disconnect.setToolTip("Close channel windows when disconnected from the server")
        layout.addRow("Close on disconnect:", self.close_on_disconnect)

        self.show_mode_prefix = QCheckBox()
        self.show_mode_prefix.setToolTip("Show mode prefixes (@, +, %, etc.) before nicks in messages, events, and the nick list")
        layout.addRow("Mode prefixes:", self.show_mode_prefix)

        self.backscroll_limit = QSpinBox()
        self.backscroll_limit.setRange(0, 1000000)
        self.backscroll_limit.setSpecialValueText("unlimited")
        self.backscroll_limit.setToolTip("Maximum lines kept in each window's backscroll. 0 = unlimited.")
        layout.addRow("Backscroll limit:", self.backscroll_limit)

        self.history_replay_channels = QSpinBox()
        self.history_replay_channels.setRange(0, 1000000)
        self.history_replay_channels.setSpecialValueText("disabled")
        self.history_replay_channels.setToolTip("Lines of channel history to reload from database on join. 0 = disabled.")
        layout.addRow("Channel history:", self.history_replay_channels)

        self.history_replay_queries = QSpinBox()
        self.history_replay_queries.setRange(0, 1000000)
        self.history_replay_queries.setSpecialValueText("disabled")
        self.history_replay_queries.setToolTip("Lines of query history to reload when a query opens. 0 = disabled.\nNote: nicks can be reused by different people.")
        layout.addRow("Query history:", self.history_replay_queries)

        self.flood_burst = QSpinBox()
        self.flood_burst.setRange(0, 50)
        self.flood_burst.setSpecialValueText("(default: 5)")
        self.flood_burst.setToolTip("Messages allowed in an initial burst before throttling")
        layout.addRow("Flood burst:", self.flood_burst)

        self.flood_rate = QDoubleSpinBox()
        self.flood_rate.setRange(0.0, 30.0)
        self.flood_rate.setDecimals(1)
        self.flood_rate.setSuffix(" s")
        self.flood_rate.setSpecialValueText("(default: 2.0)")
        self.flood_rate.setToolTip("Seconds between messages after burst is exhausted")
        layout.addRow("Flood rate:", self.flood_rate)

        self.typing_send = QCheckBox()
        self.typing_send.setToolTip("Send typing notifications to channels (IRCv3 +typing)")
        layout.addRow("Send typing:", self.typing_send)

        self.typing_show = QCheckBox()
        self.typing_show.setToolTip("Show typing indicators from other users")
        layout.addRow("Show typing:", self.typing_show)

    def load_from_data(self, data):
        self.input_lines.setValue(max(1, min(10, int(data.get('input_lines', 1)))))
        self.command_prefix.setText(str(data.get('command_prefix', '/')))
        wm = data.get('window_mode', 'maximized')
        idx = self.window_mode.findText(wm, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.window_mode.setCurrentIndex(idx)
        vm = data.get('view_mode', 'tabbed')
        idx = self.view_mode.findText(vm, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.view_mode.setCurrentIndex(idx)
        self.nickname.setText(str(data.get('nickname', '')))
        ts = data.get('timestamps') or {}
        self.timestamp_display.setText(str(ts.get('display', 'HH:mm')))
        nav = data.get('navigation')
        if not nav:
            nav = 'both' if data.get('treeview', False) else 'tabs'
        idx = self.navigation.findText(nav, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.navigation.setCurrentIndex(idx)
        self.close_on_kick.setChecked(bool(data.get('close_on_kick', False)))
        self.close_on_disconnect.setChecked(bool(data.get('close_on_disconnect', False)))
        self.show_mode_prefix.setChecked(bool(data.get('show_mode_prefix', False)))
        self.backscroll_limit.setValue(int(data.get('backscroll_limit', 10000)))
        hr = data.get('history_replay') or {}
        if isinstance(hr, int):
            self.history_replay_channels.setValue(hr)
            self.history_replay_queries.setValue(0)
        else:
            self.history_replay_channels.setValue(int(hr.get('channels', data.get('backscroll_limit', 10000))))
            self.history_replay_queries.setValue(int(hr.get('queries', 0)))
        flood = data.get('flood') or {}
        self.flood_burst.setValue(int(flood.get('burst', 0) or 0))
        self.flood_rate.setValue(float(flood.get('rate', 0.0) or 0.0))
        typing = data.get('typing') or {}
        self.typing_send.setChecked(bool(typing.get('send', True)))
        self.typing_show.setChecked(bool(typing.get('show', True)))

    def save_to_data(self, data):
        data['input_lines'] = self.input_lines.value()
        # Remove legacy key
        if 'multiline_input' in data:
            del data['multiline_input']
        data['command_prefix'] = self.command_prefix.text()
        data['window_mode'] = self.window_mode.currentText().lower()
        data['view_mode'] = self.view_mode.currentText().lower()
        data['nickname'] = self.nickname.text()
        from ruamel.yaml.comments import CommentedMap
        ts = data.get('timestamps')
        if ts is None:
            ts = CommentedMap()
            data['timestamps'] = ts
        ts['display'] = self.timestamp_display.text()
        data['navigation'] = self.navigation.currentText().lower()
        # Remove legacy key
        if 'treeview' in data:
            del data['treeview']
        data['close_on_kick'] = self.close_on_kick.isChecked()
        data['close_on_disconnect'] = self.close_on_disconnect.isChecked()
        data['show_mode_prefix'] = self.show_mode_prefix.isChecked()
        data['backscroll_limit'] = self.backscroll_limit.value()
        from ruamel.yaml.comments import CommentedMap
        hr = data.get('history_replay')
        if not isinstance(hr, dict):
            hr = CommentedMap()
            data['history_replay'] = hr
        hr['channels'] = self.history_replay_channels.value()
        hr['queries'] = self.history_replay_queries.value()
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
        from ruamel.yaml.comments import CommentedMap
        typing = data.get('typing')
        if typing is None:
            typing = CommentedMap()
            data['typing'] = typing
        typing['send'] = self.typing_send.isChecked()
        typing['show'] = self.typing_show.isChecked()
