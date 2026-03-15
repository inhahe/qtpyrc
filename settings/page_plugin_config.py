"""Settings page for plugin configuration options.

Dynamically builds a form from the plugin's ``config_fields`` list.
Shows raw key/value pairs for plugins that aren't currently loaded.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QCheckBox, QGroupBox, QScrollArea,
)
from PySide6.QtCore import Qt

import state


class PluginConfigPage(QWidget):
    """Auto-generated config page for all plugins with config_fields."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._container = QWidget()
        self._form_layout = QVBoxLayout(self._container)
        self._form_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._container)
        layout.addWidget(scroll)

        self._widgets = {}  # (plugin_name, key) -> widget

    def load_from_data(self, data):
        """Build forms for all plugins that have config data or config_fields."""
        # Clear existing
        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._widgets.clear()

        plugins_data = data.get('plugins') or {}

        # Collect plugin names from loaded plugins and config data
        plugin_names = set()
        for name, loaded in state.activescripts.items():
            cls = getattr(loaded, 'instance', None)
            if cls and getattr(type(cls), 'config_fields', []):
                plugin_names.add(name)
        # Also show plugins with existing config data
        for name, val in plugins_data.items():
            if isinstance(val, dict) and name not in ('dir', 'auto_load'):
                plugin_names.add(name)

        if not plugin_names:
            lbl = QLabel("No plugins with configuration options loaded.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._form_layout.addWidget(lbl)
            return

        for name in sorted(plugin_names):
            group = QGroupBox(name)
            form = QFormLayout(group)

            plugin_data = plugins_data.get(name) or {}
            if isinstance(plugin_data, str):
                plugin_data = {}  # e.g. plugins: {dir: ...}

            # Try to get schema from loaded plugin
            loaded = state.activescripts.get(name)
            cls = getattr(loaded, 'instance', None) if loaded else None
            fields = getattr(type(cls), 'config_fields', []) if cls else []

            if fields:
                for key, typ, default, desc in fields:
                    value = plugin_data.get(key, default)
                    widget = self._make_widget(typ, value, desc)
                    form.addRow(desc + ':', widget)
                    self._widgets[(name, key)] = (widget, typ)
            else:
                # No schema — show raw key/value pairs
                if plugin_data:
                    for key, value in plugin_data.items():
                        widget = QLineEdit(str(value))
                        widget.setToolTip("Raw value (plugin not loaded)")
                        form.addRow(key + ':', widget)
                        self._widgets[(name, key)] = (widget, str)
                else:
                    form.addRow(QLabel("(plugin not loaded, no saved config)"))

            self._form_layout.addWidget(group)

    def _make_widget(self, typ, value, tooltip=''):
        if typ is bool:
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
        """Write plugin config values back to the data dict."""
        from ruamel.yaml.comments import CommentedMap
        plugins_data = data.get('plugins')
        if plugins_data is None:
            plugins_data = CommentedMap()
            data['plugins'] = plugins_data

        # Group by plugin name
        by_plugin = {}
        for (pname, key), (widget, typ) in self._widgets.items():
            by_plugin.setdefault(pname, []).append((key, widget, typ))

        for pname, fields in by_plugin.items():
            plugin_data = plugins_data.get(pname)
            if plugin_data is None or not isinstance(plugin_data, dict):
                plugin_data = CommentedMap()
                plugins_data[pname] = plugin_data
            for key, widget, typ in fields:
                if typ is bool:
                    plugin_data[key] = widget.isChecked()
                elif typ is int:
                    plugin_data[key] = widget.value()
                elif typ is float:
                    plugin_data[key] = widget.value()
                else:
                    plugin_data[key] = widget.text()
