"""Right-click context menu for settings widgets: Reset to Default, Help."""

from PySide6.QtWidgets import (
    QMenu, QCheckBox, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QFontComboBox, QPlainTextEdit, QMessageBox,
    QApplication,
)
from PySide6.QtCore import Qt, QEvent, QObject


def set_default(widget, value):
    """Store a default value on a widget for later reset."""
    widget.setProperty('_settings_default', value)


def set_help(widget, text):
    """Store help text on a widget (falls back to tooltip if not set)."""
    widget.setProperty('_settings_help', text)


def _get_default(widget):
    """Get the stored default, or None if not set."""
    val = widget.property('_settings_default')
    return val  # may be None


def _get_help(widget):
    """Get help text: explicit help property, then tooltip."""
    h = widget.property('_settings_help')
    if h:
        return h
    return widget.toolTip() or ''


def _get_label(widget):
    """Try to find the form label for this widget."""
    parent = widget.parent()
    if not parent:
        return ''
    from PySide6.QtWidgets import QFormLayout
    for child in parent.children():
        if isinstance(child, QFormLayout):
            for row in range(child.rowCount()):
                field = child.itemAt(row, QFormLayout.ItemRole.FieldRole)
                if field and field.widget() is widget:
                    label_item = child.itemAt(row, QFormLayout.ItemRole.LabelRole)
                    if label_item and label_item.widget():
                        return label_item.widget().text().rstrip(':').strip()
    return ''


def _reset_widget(widget, value):
    """Reset a widget to a value based on its type."""
    if isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
    elif isinstance(widget, QDoubleSpinBox):
        widget.setValue(float(value))
    elif isinstance(widget, QSpinBox):
        widget.setValue(int(value))
    elif hasattr(widget, 'setValue') and isinstance(widget, QComboBox):
        # _FontSizeCombo and similar — use setValue which handles special values
        widget.setValue(int(value))
    elif isinstance(widget, QFontComboBox):
        widget.setCurrentText(str(value))
    elif isinstance(widget, QComboBox):
        idx = widget.findText(str(value), Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            widget.setCurrentIndex(idx)
        else:
            widget.setCurrentText(str(value))
    elif isinstance(widget, QPlainTextEdit):
        widget.setPlainText(str(value))
    elif isinstance(widget, QLineEdit):
        widget.setText(str(value))


def show_widget_context_menu(widget, pos):
    """Show the right-click context menu for a settings widget."""
    menu = QMenu(widget)

    default = _get_default(widget)
    help_text = _get_help(widget)
    label = _get_label(widget)

    if default is not None:
        def _do_reset(d=default):
            _reset_widget(widget, d)
        menu.addAction('Reset to default', _do_reset)

    if help_text:
        def _do_help(h=help_text, l=label):
            QMessageBox.information(widget.window(), l or 'Help', h)
        menu.addAction('Help', _do_help)

    if menu.actions():
        menu.exec(widget.mapToGlobal(pos))


class SettingsContextFilter(QObject):
    """Event filter that adds right-click context menus to settings widgets.

    Handles compound widgets like _ColorRow by walking up to find
    the parent that has the config_key property.
    """

    def eventFilter(self, obj, event):
        from PySide6.QtCore import Qt as _Qt
        # Intercept right-click via mouse press (more reliable than ContextMenu
        # which editable QComboBox/QLineEdit may consume)
        if (event.type() == QEvent.Type.MouseButtonPress
                and event.button() == _Qt.MouseButton.RightButton):
            target = obj
            while target:
                default = _get_default(target)
                help_text = _get_help(target)
                if default is not None or help_text:
                    show_widget_context_menu(target, event.pos())
                    return True
                target = target.parent()
        return False
