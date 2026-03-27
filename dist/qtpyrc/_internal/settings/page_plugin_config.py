"""Settings pages for plugin configuration options.

Dynamically builds a form page per plugin from its ``config_fields`` list.
Shows raw key/value pairs for plugins that aren't currently loaded.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QScrollArea,
)
from PySide6.QtCore import Qt

import state


class SinglePluginConfigPage(QWidget):
    """Config page for a single plugin."""

    def __init__(self, plugin_name, parent=None):
        super().__init__(parent)
        self._plugin_name = plugin_name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._container = QWidget()
        self._form = QFormLayout(self._container)
        self._form.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._container)
        layout.addWidget(scroll)

        self._widgets = {}  # key -> (widget, typ)

    def load_from_data(self, data):
        plugins_data = data.get('plugins') or {}
        plugin_data = plugins_data.get(self._plugin_name) or {}
        if isinstance(plugin_data, str):
            plugin_data = {}

        # Try to get schema from loaded plugin
        loaded = state.activescripts.get(self._plugin_name)
        cls = getattr(loaded, 'instance', None) if loaded else None
        fields = getattr(type(cls), 'config_fields', []) if cls else []

        if fields:
            for field in fields:
                key, typ, default, desc = field[:4]
                choices = field[4] if len(field) > 4 else None
                value = plugin_data.get(key, default)
                widget = self._make_widget(typ, value, desc, choices)
                self._form.addRow(desc + ':', widget)
                self._widgets[key] = (widget, typ, choices)
        else:
            # No schema — show raw key/value pairs
            if plugin_data:
                for key, value in plugin_data.items():
                    widget = QLineEdit(str(value))
                    widget.setToolTip("Raw value (plugin not loaded)")
                    self._form.addRow(key + ':', widget)
                    self._widgets[key] = (widget, str, None)
            else:
                self._form.addRow(QLabel("(plugin not loaded, no saved config)"))

    def _make_widget(self, typ, value, tooltip='', choices=None):
        if choices:
            w = QComboBox()
            for c in choices:
                w.addItem(str(c))
            idx = w.findText(str(value))
            if idx >= 0:
                w.setCurrentIndex(idx)
            else:
                w.setCurrentText(str(value))
            if tooltip:
                w.setToolTip(tooltip)
            return w
        elif typ is bool:
            w = QCheckBox()
            w.setChecked(bool(value))
            if tooltip:
                w.setToolTip(tooltip)
            return w
        elif typ is int:
            w = QSpinBox()
            w.setRange(-999999, 999999)
            w.setValue(int(value))
            if tooltip:
                w.setToolTip(tooltip)
            return w
        elif typ is float:
            w = QDoubleSpinBox()
            w.setRange(-999999.0, 999999.0)
            w.setDecimals(2)
            w.setValue(float(value))
            if tooltip:
                w.setToolTip(tooltip)
            return w
        else:
            w = QLineEdit(str(value))
            if tooltip:
                w.setToolTip(tooltip)
            return w

    def save_to_data(self, data):
        from ruamel.yaml.comments import CommentedMap
        plugins_data = data.get('plugins')
        if plugins_data is None:
            plugins_data = CommentedMap()
            data['plugins'] = plugins_data

        plugin_data = plugins_data.get(self._plugin_name)
        if plugin_data is None or not isinstance(plugin_data, dict):
            plugin_data = CommentedMap()
            plugins_data[self._plugin_name] = plugin_data

        for key, entry in self._widgets.items():
            widget, typ = entry[0], entry[1]
            choices = entry[2] if len(entry) > 2 else None
            if choices:
                plugin_data[key] = widget.currentText()
            elif typ is bool:
                plugin_data[key] = widget.isChecked()
            elif typ is int:
                plugin_data[key] = widget.value()
            elif typ is float:
                plugin_data[key] = widget.value()
            else:
                plugin_data[key] = widget.text()


def get_plugin_names(data):
    """Return sorted list of plugin names that have config fields or saved config."""
    names = set()
    # Loaded plugins with config_fields
    for name, loaded in (state.activescripts or {}).items():
        cls = getattr(loaded, 'instance', None)
        if cls and getattr(type(cls), 'config_fields', []):
            names.add(name)
    # Plugins with saved config data
    plugins_data = data.get('plugins') or {}
    for name, val in plugins_data.items():
        if isinstance(val, dict) and name not in ('dir', 'auto_load'):
            names.add(name)
    return sorted(names)
