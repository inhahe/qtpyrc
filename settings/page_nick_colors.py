"""Settings page for nick color palette."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox,
    QLabel, QScrollArea, QLineEdit, QDialog, QDialogButtonBox,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt


class NickColorsPage(QWidget):
    """Nick color palette editor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.enabled = QCheckBox("Enable nick colors")
        self.enabled.setToolTip("Assign random colors to nicks in channel messages")
        layout.addWidget(self.enabled)

        layout.addWidget(QLabel("Click a swatch to open the color picker."))

        # Scrollable list of color rows
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._list_widget)
        layout.addWidget(self._scroll, 1)

        # Buttons
        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_color)
        btn_row.addWidget(add_btn)
        remove_btn = QPushButton("Remove Last")
        remove_btn.clicked.connect(self._remove_last)
        btn_row.addWidget(remove_btn)
        reset_btn = QPushButton("Reset to Default")
        reset_btn.clicked.connect(self._reset_default)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._rows = []  # list of (row_widget, swatch_btn, hex_input)

    def _add_row(self, color_str):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        idx = len(self._rows)

        del_btn = QPushButton('\u2715')
        del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet('color: red; border: none; font-weight: bold;')
        del_btn.setToolTip('Remove this color')
        del_btn.clicked.connect(lambda checked, i=idx: self._remove_at(i))
        row_layout.addWidget(del_btn)

        swatch = QPushButton()
        swatch.setFixedSize(20, 20)
        swatch.setStyleSheet("background-color: %s; border: 1px solid #888;" % color_str)
        swatch.setToolTip("Click to pick a color")
        row_layout.addWidget(swatch)

        hex_input = QLineEdit(color_str)
        _char = hex_input.fontMetrics().horizontalAdvance('0')
        hex_input.setFixedWidth(_char * 10)
        hex_input.setMaxLength(7)
        hex_input.textChanged.connect(lambda text, i=idx: self._on_hex_typing(i))
        row_layout.addWidget(hex_input)

        row_layout.addStretch()
        swatch.clicked.connect(lambda checked, i=idx: self._pick_color(i))
        hex_input.editingFinished.connect(lambda i=idx: self._on_hex_changed(i))

        self._list_layout.addWidget(row)
        self._rows.append((row, swatch, hex_input))

    def _rebuild(self, colors):
        # Clear
        for row, swatch, hex_input in self._rows:
            row.deleteLater()
        self._rows.clear()
        for c in colors:
            self._add_row(c)

    def _get_colors(self):
        return [hex_input.text().strip() for _, _, hex_input in self._rows
                if hex_input.text().strip()]

    def _pick_color(self, index):
        if index >= len(self._rows):
            return
        _, swatch, hex_input = self._rows[index]
        current = QColor(hex_input.text().strip() or '#000000')
        from dialogs import ColorPickerWidget
        dlg = QDialog(self)
        dlg.setWindowTitle("Pick nick color")
        layout = QVBoxLayout(dlg)
        picker = ColorPickerWidget(current, parent=dlg)
        layout.addWidget(picker)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            c = picker.color
            hex_input.setText(c.name())
            swatch.setStyleSheet("background-color: %s; border: 1px solid #888;" % c.name())
            swatch.setToolTip(c.name())

    def _on_hex_typing(self, index):
        """Live validation while typing — red background for invalid hex."""
        if index >= len(self._rows):
            return
        _, swatch, hex_input = self._rows[index]
        text = hex_input.text().strip()
        c = QColor(text)
        if c.isValid():
            hex_input.setStyleSheet('')
            swatch.setStyleSheet("background-color: %s; border: 1px solid #888;" % c.name())
            swatch.setToolTip(c.name())
        else:
            hex_input.setStyleSheet('color: red;')

    def _on_hex_changed(self, index):
        if index >= len(self._rows):
            return
        _, swatch, hex_input = self._rows[index]
        c = QColor(hex_input.text().strip())
        if c.isValid():
            hex_input.setStyleSheet('')
            swatch.setStyleSheet("background-color: %s; border: 1px solid #888;" % c.name())
            swatch.setToolTip(c.name())

    def _add_color(self):
        self._add_row('#cc0000')
        # Force layout update twice (widget size then scroll range), then scroll
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        QApplication.processEvents()
        self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum())

    def _remove_last(self):
        if self._rows:
            row, swatch, hex_input = self._rows.pop()
            row.deleteLater()

    def _remove_at(self, index):
        if 0 <= index < len(self._rows):
            colors = self._get_colors()
            colors.pop(index)
            self._rebuild(colors)

    def _reset_default(self):
        self._rebuild([
            '#cc0000', '#0066cc', '#009900', '#9933cc', '#cc6600',
            '#00999e', '#cc0066', '#6633cc', '#008844', '#aa4400',
            '#2255aa', '#cc3399', '#337700', '#7744aa', '#006688',
            '#994400', '#2266cc', '#880044', '#448800', '#663399',
        ])

    def load_from_data(self, data):
        nc = data.get('nick_colors') or {}
        if isinstance(nc, bool):
            nc = {'enabled': nc}
        self.enabled.setChecked(bool(nc.get('enabled', False)))
        colors = nc.get('palette') or [
            '#cc0000', '#0066cc', '#009900', '#9933cc', '#cc6600',
            '#00999e', '#cc0066', '#6633cc', '#008844', '#aa4400',
            '#2255aa', '#cc3399', '#337700', '#7744aa', '#006688',
            '#994400', '#2266cc', '#880044', '#448800', '#663399',
        ]
        self._rebuild(colors)

    def save_to_data(self, data):
        from ruamel.yaml.comments import CommentedMap
        nc = data.get('nick_colors')
        if nc is None or isinstance(nc, bool):
            nc = CommentedMap()
            data['nick_colors'] = nc
        nc['enabled'] = self.enabled.isChecked()
        nc['palette'] = self._get_colors()
