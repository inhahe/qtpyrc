from PySide6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QSpinBox, QPushButton, QDialog,
    QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QFontComboBox,
    QDialogButtonBox,
)
from PySide6.QtGui import QColor, QPalette
from PySide6.QtCore import Qt, Signal
from settings.page_general import _ck

_COMMON_SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 28, 36, 48, 72]

# Named colors supported by config.py
_NAMED_COLORS = [
    'black', 'white', 'red', 'green', 'blue', 'cyan', 'magenta', 'yellow',
    'darkRed', 'darkGreen', 'darkBlue', 'darkCyan', 'darkMagenta', 'darkYellow',
    'gray', 'darkGray', 'lightGray',
]


class _ColorCombo(QComboBox):
    def showPopup(self):
        super().showPopup()
        self.view().scrollToTop()


class _FontSizeCombo(QComboBox):
    """Editable combo with common font sizes in a dropdown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self._special_text = ''
        self._special_idx = -1
        for s in _COMMON_SIZES:
            self.addItem(str(s))
        self.setMaxVisibleItems(len(_COMMON_SIZES) + 2)  # +2 for special item + margin
        # No validator — special items like "(system default)" need to be selectable
        # and value() handles non-numeric text gracefully

    def value(self):
        if self._special_idx >= 0 and self.currentIndex() == self._special_idx:
            return 0
        text = self.currentText().strip()
        if not text or text == self._special_text:
            return 0
        try:
            return max(1, int(text))
        except ValueError:
            return 0

    def setValue(self, v):
        if (not v or v == 0) and self._special_idx >= 0:
            self.setCurrentIndex(self._special_idx)
        else:
            self.setCurrentText(str(v))

    def setRange(self, low, high):
        pass  # sizes are in the dropdown list

    def setSpecialValueText(self, text):
        """Add or update a special item at the top for value=0."""
        self._special_text = text
        if self._special_idx >= 0:
            self.setItemText(self._special_idx, text)
        else:
            self.insertItem(0, text)
            self._special_idx = 0


class _ColorRow(QWidget):
    """A color picker row: swatch + editable combo (named colors + hex) + Pick button."""
    colorChanged = Signal()

    def __init__(self, default_hint='', default_color=None, parent=None):
        super().__init__(parent)
        self._swatch_pending = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._swatch = QPushButton()
        self._swatch.setFixedSize(20, 20)
        self._swatch.setStyleSheet('border: 1px solid gray;')
        self._swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._swatch.clicked.connect(self._pick)
        layout.addWidget(self._swatch)

        btn = QPushButton('Pick...')
        btn.setStyleSheet('padding: 2px 6px;')
        btn.clicked.connect(self._pick)
        layout.addWidget(btn)

        self._default_hint = default_hint
        self._default_color = default_color  # callable returning QColor, or None
        self._combo = _ColorCombo()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._combo.addItem(default_hint if default_hint else '(none)')
        for name in _NAMED_COLORS:
            self._combo.addItem(name)
        self._combo.addItem('Custom...')
        self._combo.setMinimumWidth(140)
        self._combo.setStyleSheet("QComboBox { padding-left: 4px; }")
        self._combo.currentTextChanged.connect(self._on_text_changed)
        self._combo.activated.connect(self._on_activated)
        self._combo.lineEdit().editingFinished.connect(self.colorChanged.emit)
        layout.addWidget(self._combo, 1)

    def showEvent(self, event):
        super().showEvent(event)
        if self._swatch_pending:
            self._swatch_pending = False
            self._update_swatch()

    def _is_special(self, text):
        return text == 'Custom...' or text == self._combo.itemText(0)

    def _on_activated(self, index):
        text = self._combo.itemText(index)
        if text == 'Custom...':
            self._pick()
        elif text == self._combo.itemText(0):
            # Selected the default item — show the hint text
            self._combo.setCurrentIndex(0)
            self._update_swatch()
            self.colorChanged.emit()

    def _on_text_changed(self, text):
        if self._is_special(text):
            return
        self._update_swatch()

    def _resolve_default_color(self):
        """Try to resolve the default hint to a QColor for the swatch."""
        if self._default_color:
            try:
                return self._default_color()
            except Exception:
                pass
        hint = self._default_hint
        if not hint or not hint.startswith('default: '):
            return None
        name = hint[9:]  # strip "default: "
        if name in ('foreground', 'background'):
            import state
            if state.config:
                return QColor(state.config.fgcolor if name == 'foreground'
                              else state.config.bgcolor)
            return None
        c = QColor(name)
        if c.isValid():
            return c
        from config import _qt_colors
        qt_c = _qt_colors.get(name)
        return QColor(qt_c) if qt_c else None

    def _parse_color(self, text):
        """Try to parse text as a color. Returns a valid QColor or None."""
        if not text:
            return None
        c = QColor(text)
        if c.isValid():
            return c
        from config import _qt_colors
        qt_c = _qt_colors.get(text)
        if qt_c:
            return QColor(qt_c)
        return None

    def _set_valid(self, valid):
        """Set the line edit text color to indicate valid/invalid input."""
        le = self._combo.lineEdit()
        pal = le.palette()
        if valid:
            pal.setColor(QPalette.ColorRole.Text,
                         self.palette().color(QPalette.ColorRole.Text))
        else:
            pal.setColor(QPalette.ColorRole.Text, QColor('#cc0000'))
        le.setPalette(pal)

    def _update_swatch(self):
        if not self.isVisible() and self._default_color:
            self._swatch_pending = True
        text = self._combo.currentText().strip()
        if not text or self._is_special(text):
            c = self._resolve_default_color()
            if c and c.isValid():
                self._swatch.setStyleSheet(
                    'border: 1px solid gray; background-color: %s;' % c.name())
            else:
                self._swatch.setStyleSheet('border: 1px solid gray; background: none;')
            self._set_valid(True)
            return
        c = self._parse_color(text)
        if c:
            self._swatch.setStyleSheet(
                'border: 1px solid gray; background-color: %s;' % c.name())
            self._set_valid(True)
        else:
            self._swatch.setStyleSheet('border: 1px solid gray; background: none;')
            self._set_valid(False)

    def _pick(self):
        from config import _qt_colors
        from dialogs import ColorPickerWidget
        initial = QColor(self.text() or 'black')
        qt_c = _qt_colors.get(self.text())
        if qt_c:
            initial = QColor(qt_c)
        dlg = QDialog(self)
        dlg.setWindowTitle('Pick Color')
        lay = QVBoxLayout(dlg)
        picker = ColorPickerWidget(initial, dlg)
        lay.addWidget(picker)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            picker.add_final_to_history()
            c = picker.color
            # Check if it matches a named color
            for name, qt_color in _qt_colors.items():
                if QColor(qt_color) == c:
                    self.setText(name)
                    self.colorChanged.emit()
                    return
            self.setText(c.name())
            self.colorChanged.emit()

    def text(self):
        t = self._combo.currentText().strip()
        if self._is_special(t) or not t:
            return ''
        # Don't return invalid colors
        if self._parse_color(t) is None:
            return ''
        return t

    def setText(self, value):
        if value:
            self._combo.setCurrentText(str(value))
        else:
            # Show the default hint item
            self._combo.setCurrentIndex(0)
        self._update_swatch()


# ---------------------------------------------------------------------------
# Helper to make optional font family + size widgets
# ---------------------------------------------------------------------------

def _make_optional_font():
    """Return (family_combo, size_combo) for an optional font (0 = system default)."""
    fam = QFontComboBox()
    fam.setEditable(True)
    fam.insertItem(0, "(system default)")
    sz = _FontSizeCombo()
    sz.setSpecialValueText("(system default)")
    return fam, sz


def _load_optional_font(fam_combo, size_spin, family, size):
    if family:
        fam_combo.setCurrentText(str(family))
    else:
        fam_combo.setCurrentIndex(0)
    size_spin.setValue(int(size) if size else 0)


def _save_optional_font(font, fam_key, size_key, fam_combo, size_spin):
    fam = fam_combo.currentText().strip()
    if fam and fam != '(system default)':
        font[fam_key] = fam
    elif fam_key in font:
        del font[fam_key]
    if size_spin.value() > 0:
        font[size_key] = size_spin.value()
    elif size_key in font:
        del font[size_key]


def _save_color_val(section, key, widget):
    val = widget.text()
    if val:
        section[key] = val
    elif key in section:
        del section[key]


def _ensure_section(data, key):
    from ruamel.yaml.comments import CommentedMap
    if key not in data or data[key] is None:
        data[key] = CommentedMap()
    return data[key]


def _ensure_font(data):
    from ruamel.yaml.comments import CommentedMap
    if 'font' not in data or data['font'] is None:
        data['font'] = CommentedMap()
    return data['font']


def _ensure_colors(data):
    from ruamel.yaml.comments import CommentedMap
    if 'colors' not in data or data['colors'] is None:
        data['colors'] = CommentedMap()
    return data['colors']


def _connect_changed(page):
    """Connect all font combos, spin boxes, and color rows to page.changed."""
    def emit(*_args):
        page.changed.emit()
    for child in page.__dict__.values():
        if isinstance(child, (QFontComboBox, QComboBox)):
            child.currentTextChanged.connect(emit)
        elif isinstance(child, QSpinBox):
            child.valueChanged.connect(emit)
        elif isinstance(child, _ColorRow):
            child.colorChanged.connect(emit)


# ---------------------------------------------------------------------------
# Sub-pages
# ---------------------------------------------------------------------------

class BaseColorsPage(QWidget):
    """Global foreground/background colors used as defaults across the app."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.fg_color = _ck(_ColorRow(default_hint='default: black'), 'colors.foreground')
        layout.addRow("Foreground:", self.fg_color)

        self.bg_color = _ck(_ColorRow(default_hint='default: white'), 'colors.background')
        layout.addRow("Background:", self.bg_color)

        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        colors = data.get('colors') or {}
        fg = colors.get('foreground') or font.get('color') or font.get('fg_color', 'black')
        bg = colors.get('background') or data.get('window_color') or font.get('bg_color', 'white')
        self.fg_color.setText(str(fg))
        self.bg_color.setText(str(bg))

    def save_to_data(self, data):
        font = _ensure_font(data)
        # Remove legacy keys
        for k in ('color', 'fg_color', 'bg_color'):
            if k in font:
                del font[k]
        if 'window_color' in data:
            del data['window_color']
        colors = _ensure_colors(data)
        _save_color_val(colors, 'foreground', self.fg_color)
        _save_color_val(colors, 'background', self.bg_color)


class ChatFontPage(QWidget):
    """Chat window font and message type colors."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.family = _ck(QFontComboBox(), 'font.family')
        layout.addRow("Font family:", self.family)

        self.size = _ck(_FontSizeCombo(), 'font.size')
        layout.addRow("Font size:", self.size)

        self.system_color = _ck(_ColorRow(default_hint='default: red'), 'colors.system')
        layout.addRow("System:", self.system_color)

        self.info_color = _ck(_ColorRow(default_hint='default: darkGreen'), 'colors.info')
        layout.addRow("Info:", self.info_color)

        self.action_color = _ck(_ColorRow(default_hint='default: darkMagenta'), 'colors.action')
        layout.addRow("Action:", self.action_color)

        self.notice_color = _ck(_ColorRow(default_hint='default: darkCyan'), 'colors.notice')
        layout.addRow("Notice:", self.notice_color)

        self.link_color = _ck(_ColorRow(default_hint='default: #0066cc'), 'colors.link')
        layout.addRow("Link:", self.link_color)

        self.highlight_color = _ck(_ColorRow(default_hint='default: red'), 'colors.highlight')
        layout.addRow("Highlight:", self.highlight_color)

        self.newmsg_color = _ck(_ColorRow(default_hint='default: blue'), 'colors.new_message')
        layout.addRow("New message:", self.newmsg_color)

        self.search_bg = _ck(_ColorRow(default_hint='default: yellow'), 'colors.search_bg')
        layout.addRow("Search bg:", self.search_bg)

        self.search_fg = _ck(_ColorRow(default_hint='default: black'), 'colors.search_fg')
        layout.addRow("Search fg:", self.search_fg)

        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        colors = data.get('colors') or {}
        self.family.setCurrentFont(self.family.currentFont())
        self.family.setCurrentText(str(font.get('family', 'Fixedsys')))
        self.size.setValue(int(font.get('size', font.get('font', 15))))
        self.system_color.setText(str(colors.get('system', '')))
        self.info_color.setText(str(colors.get('info', '')))
        self.action_color.setText(str(colors.get('action', '')))
        self.notice_color.setText(str(colors.get('notice', '')))
        self.link_color.setText(str(colors.get('link', '')))
        self.highlight_color.setText(str(colors.get('highlight', '')))
        self.newmsg_color.setText(str(colors.get('new_message', '')))
        self.search_bg.setText(str(colors.get('search_bg', '')))
        self.search_fg.setText(str(colors.get('search_fg', '')))

    def save_to_data(self, data):
        font = _ensure_font(data)
        font['family'] = self.family.currentText()
        font['size'] = self.size.value()
        colors = _ensure_colors(data)
        for key, widget in [('system', self.system_color), ('info', self.info_color),
                            ('action', self.action_color), ('notice', self.notice_color),
                            ('link', self.link_color),
                            ('highlight', self.highlight_color), ('new_message', self.newmsg_color),
                            ('search_bg', self.search_bg), ('search_fg', self.search_fg)]:
            _save_color_val(colors, key, widget)


class TabFontPage(QWidget):
    """Tab bar font and color settings."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.tab_family, self.tab_size = _make_optional_font()
        layout.addRow("Font family:", self.tab_family)
        layout.addRow("Font size:", self.tab_size)
        layout.addRow("", QLabel(""))  # spacer
        layout.addRow(QLabel("Tab colors:"))
        self.active_fg = _ColorRow(default_hint='default: background')
        layout.addRow("Active foreground:", self.active_fg)
        self.active_bg = _ColorRow(default_hint='default: foreground')
        layout.addRow("Active background:", self.active_bg)
        self.normal_fg = _ColorRow(default_hint='default: foreground')
        layout.addRow("Normal foreground:", self.normal_fg)
        self.normal_bg = _ColorRow(default_hint='default: background')
        layout.addRow("Normal background:", self.normal_bg)
        self.skipped_fg = _ColorRow(default_hint='default: gray')
        layout.addRow("Skipped foreground:", self.skipped_fg)
        self.skipped_bg = _ColorRow(default_hint='default: light gray')
        layout.addRow("Skipped background:", self.skipped_bg)
        self.bar_bg = _ColorRow(default_hint='default: normal background')
        layout.addRow("Bar background:", self.bar_bg)
        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        _load_optional_font(self.tab_family, self.tab_size,
                            font.get('tab_family'), font.get('tab_size'))
        tabs = (data.get('colors') or {}).get('tabs') or {}
        self.active_fg.setText(str(tabs.get('active_fg', '')))
        self.active_bg.setText(str(tabs.get('active_bg', '')))
        self.normal_fg.setText(str(tabs.get('normal_fg', '')))
        self.normal_bg.setText(str(tabs.get('normal_bg', '')))
        self.skipped_fg.setText(str(tabs.get('skipped_fg', '')))
        self.skipped_bg.setText(str(tabs.get('skipped_bg', '')))
        self.bar_bg.setText(str(tabs.get('bar_bg', '')))

    def save_to_data(self, data):
        font = _ensure_font(data)
        _save_optional_font(font, 'tab_family', 'tab_size',
                            self.tab_family, self.tab_size)
        colors = _ensure_colors(data)
        tabs = _ensure_section(colors, 'tabs')
        _save_color_val(tabs, 'active_fg', self.active_fg)
        _save_color_val(tabs, 'active_bg', self.active_bg)
        _save_color_val(tabs, 'normal_fg', self.normal_fg)
        _save_color_val(tabs, 'normal_bg', self.normal_bg)
        _save_color_val(tabs, 'skipped_fg', self.skipped_fg)
        _save_color_val(tabs, 'skipped_bg', self.skipped_bg)
        _save_color_val(tabs, 'bar_bg', self.bar_bg)
        if not tabs:
            del colors['tabs']


class MenuFontPage(QWidget):
    """Menu font and color settings."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.menu_family, self.menu_size = _make_optional_font()
        layout.addRow("Font family:", self.menu_family)
        layout.addRow("Font size:", self.menu_size)
        self.menu_fg = _ColorRow(default_hint='default: foreground')
        layout.addRow("Foreground:", self.menu_fg)
        self.menu_bg = _ColorRow(default_hint='default: background')
        layout.addRow("Background:", self.menu_bg)
        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        _load_optional_font(self.menu_family, self.menu_size,
                            font.get('menu_family'), font.get('menu_size'))
        menu = (data.get('colors') or {}).get('menu') or {}
        self.menu_fg.setText(str(menu.get('foreground', '')))
        self.menu_bg.setText(str(menu.get('background', '')))

    def save_to_data(self, data):
        font = _ensure_font(data)
        _save_optional_font(font, 'menu_family', 'menu_size',
                            self.menu_family, self.menu_size)
        colors = _ensure_colors(data)
        menu = _ensure_section(colors, 'menu')
        _save_color_val(menu, 'foreground', self.menu_fg)
        _save_color_val(menu, 'background', self.menu_bg)
        if not menu:
            del colors['menu']


class TreeFontPage(QWidget):
    """Network tree sidebar font and color settings."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.tree_family, self.tree_size = _make_optional_font()
        layout.addRow("Font family:", self.tree_family)
        layout.addRow("Font size:", self.tree_size)
        self.tree_fg = _ColorRow(default_hint='default: foreground')
        layout.addRow("Foreground:", self.tree_fg)
        self.tree_bg = _ColorRow(default_hint='default: background')
        layout.addRow("Background:", self.tree_bg)
        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        _load_optional_font(self.tree_family, self.tree_size,
                            font.get('tree_family'), font.get('tree_size'))
        tree = (data.get('colors') or {}).get('tree') or {}
        self.tree_fg.setText(str(tree.get('foreground', '')))
        self.tree_bg.setText(str(tree.get('background', '')))

    def save_to_data(self, data):
        font = _ensure_font(data)
        _save_optional_font(font, 'tree_family', 'tree_size',
                            self.tree_family, self.tree_size)
        colors = _ensure_colors(data)
        tree = _ensure_section(colors, 'tree')
        _save_color_val(tree, 'foreground', self.tree_fg)
        _save_color_val(tree, 'background', self.tree_bg)
        if not tree:
            del colors['tree']


class NicklistFontPage(QWidget):
    """Nick list font and color settings."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.nicks_family, self.nicks_size = _make_optional_font()
        self.nicks_family.lineEdit().setPlaceholderText("(use chat font)")
        self.nicks_size.setSpecialValueText("(use chat font)")
        layout.addRow("Font family:", self.nicks_family)
        layout.addRow("Font size:", self.nicks_size)
        self.nicks_fg = _ColorRow(default_hint='default: foreground')
        layout.addRow("Foreground:", self.nicks_fg)
        self.nicks_bg = _ColorRow(default_hint='default: background')
        layout.addRow("Background:", self.nicks_bg)
        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        _load_optional_font(self.nicks_family, self.nicks_size,
                            font.get('nicklist_family'), font.get('nicklist_size'))
        nicklist = (data.get('colors') or {}).get('nicklist') or {}
        self.nicks_fg.setText(str(nicklist.get('foreground', '')))
        self.nicks_bg.setText(str(nicklist.get('background', '')))

    def save_to_data(self, data):
        font = _ensure_font(data)
        _save_optional_font(font, 'nicklist_family', 'nicklist_size',
                            self.nicks_family, self.nicks_size)
        colors = _ensure_colors(data)
        nicklist = _ensure_section(colors, 'nicklist')
        _save_color_val(nicklist, 'foreground', self.nicks_fg)
        _save_color_val(nicklist, 'background', self.nicks_bg)
        if not nicklist:
            del colors['nicklist']


class ToolbarFontPage(QWidget):
    """Toolbar font and icon color settings."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.toolbar_family, self.toolbar_size = _make_optional_font()
        layout.addRow("Font family:", self.toolbar_family)
        layout.addRow("Font size:", self.toolbar_size)
        self.toolbar_fg = _ColorRow(default_hint='default: foreground')
        layout.addRow("Icon color:", self.toolbar_fg)
        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        _load_optional_font(self.toolbar_family, self.toolbar_size,
                            font.get('toolbar_family') or data.get('toolbar_font_family'),
                            font.get('toolbar_size') or data.get('toolbar_font_size'))
        toolbar = (data.get('colors') or {}).get('toolbar') or {}
        self.toolbar_fg.setText(str(
            toolbar.get('foreground') or data.get('toolbar_foreground_color', '')))

    def save_to_data(self, data):
        font = _ensure_font(data)
        _save_optional_font(font, 'toolbar_family', 'toolbar_size',
                            self.toolbar_family, self.toolbar_size)
        # Remove legacy top-level keys
        for k in ('toolbar_font_family', 'toolbar_font_size',
                   'toolbar_foreground_color', 'toolbar_separator_color'):
            if k in data:
                del data[k]
        colors = _ensure_colors(data)
        toolbar = _ensure_section(colors, 'toolbar')
        _save_color_val(toolbar, 'foreground', self.toolbar_fg)
        if not toolbar:
            del colors['toolbar']


class SettingsFontPage(QWidget):
    """Settings dialog font and color settings."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.settings_family, self.settings_size = _make_optional_font()
        self.settings_family.setMinimumWidth(200)
        layout.addRow("Font family:", self.settings_family)
        layout.addRow("Font size:", self.settings_size)
        self.settings_fg = _ColorRow(default_hint='default: foreground')
        layout.addRow("Foreground:", self.settings_fg)
        self.settings_bg = _ColorRow(default_hint='default: background')
        layout.addRow("Background:", self.settings_bg)
        def _sys(role):
            from PySide6.QtWidgets import QApplication
            return lambda: QApplication.palette().color(role)
        self.settings_tree_fg = _ColorRow(
            default_hint='default: settings foreground')
        layout.addRow("Tree foreground:", self.settings_tree_fg)
        self.settings_tree_bg = _ColorRow(
            default_hint='default: settings background')
        layout.addRow("Tree background:", self.settings_tree_bg)
        self.settings_tree_sel_fg = _ColorRow(
            default_hint='default: system theme',
            default_color=_sys(QPalette.ColorRole.HighlightedText))
        layout.addRow("Tree selection text:", self.settings_tree_sel_fg)
        self.settings_tree_sel_bg = _ColorRow(
            default_hint='default: system theme',
            default_color=_sys(QPalette.ColorRole.Highlight))
        layout.addRow("Tree selection bg:", self.settings_tree_sel_bg)

        # Element sizes
        def _size_combo(config_key):
            c = _ck(_FontSizeCombo(), config_key)
            c.setSpecialValueText("(base font)")
            from PySide6.QtWidgets import QSizePolicy
            c.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._size_combos.append(c)
            return c
        self._size_combos = []
        self.title_size = _size_combo('font.settings_sizes.title')
        layout.addRow("Title size:", self.title_size)
        self.label_size = _size_combo('font.settings_sizes.label')
        layout.addRow("Label size:", self.label_size)
        self.list_size = _size_combo('font.settings_sizes.list')
        layout.addRow("List/field size:", self.list_size)
        self.note_size = _size_combo('font.settings_sizes.note')
        layout.addRow("Note size:", self.note_size)
        self.hint_size = _size_combo('font.settings_sizes.hint')
        layout.addRow("Hint size:", self.hint_size)
        self.delete_size = _size_combo('font.settings_sizes.delete')
        layout.addRow("Delete btn size:", self.delete_size)

        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        _load_optional_font(self.settings_family, self.settings_size,
                            font.get('settings_family'), font.get('settings_size'))
        colors = data.get('colors') or {}
        settings = colors.get('settings') or {}
        self.settings_fg.setText(str(settings.get('foreground', '')))
        self.settings_bg.setText(str(settings.get('background', '')))
        settings_tree = colors.get('settings_tree') or {}
        self.settings_tree_fg.setText(str(settings_tree.get('foreground', '')))
        self.settings_tree_bg.setText(str(settings_tree.get('background', '')))
        self.settings_tree_sel_fg.setText(str(settings_tree.get('select_fg', '')))
        self.settings_tree_sel_bg.setText(str(settings_tree.get('select_bg', '')))
        sizes = font.get('settings_sizes') or {}
        self.title_size.setValue(int(sizes.get('title', 13)))
        self.label_size.setValue(int(sizes.get('label', 0)))
        self.list_size.setValue(int(sizes.get('list', 0)))
        self.note_size.setValue(int(sizes.get('note', 0)))
        self.hint_size.setValue(int(sizes.get('hint', 0)))
        self.delete_size.setValue(int(sizes.get('delete', 0)))

    def resize_combos(self):
        """Resize font size combos to fit '(base font)' at current font size."""
        from PySide6.QtGui import QFontMetrics
        for c in self._size_combos:
            fm = QFontMetrics(c.font())
            text_w = fm.horizontalAdvance('(base font)')
            # Scale padding with font height (arrow button, margins, etc.)
            padding = fm.height() * 3
            c.setFixedWidth(max(text_w + padding, 100))

    def resize_color_rows(self):
        """Resize color rows to fit the longest default hint at current font."""
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        text_w = fm.horizontalAdvance('default: settings background')
        # Scale padding with font height: swatch + Pick button + arrow + margins
        padding = fm.height() * 6
        w = text_w + padding
        for row in [self.settings_fg, self.settings_bg,
                    self.settings_tree_fg, self.settings_tree_bg,
                    self.settings_tree_sel_fg, self.settings_tree_sel_bg]:
            row.setFixedWidth(max(w, 250))

    def save_to_data(self, data):
        font = _ensure_font(data)
        _save_optional_font(font, 'settings_family', 'settings_size',
                            self.settings_family, self.settings_size)
        colors = _ensure_colors(data)
        settings = _ensure_section(colors, 'settings')
        _save_color_val(settings, 'foreground', self.settings_fg)
        _save_color_val(settings, 'background', self.settings_bg)
        if not settings:
            del colors['settings']
        settings_tree = _ensure_section(colors, 'settings_tree')
        _save_color_val(settings_tree, 'foreground', self.settings_tree_fg)
        _save_color_val(settings_tree, 'background', self.settings_tree_bg)
        _save_color_val(settings_tree, 'select_fg', self.settings_tree_sel_fg)
        _save_color_val(settings_tree, 'select_bg', self.settings_tree_sel_bg)
        if not settings_tree:
            del colors['settings_tree']
        from ruamel.yaml.comments import CommentedMap
        sizes = font.get('settings_sizes')
        if sizes is None:
            sizes = CommentedMap()
            font['settings_sizes'] = sizes
        sizes['title'] = self.title_size.value()
        sizes['label'] = self.label_size.value()
        sizes['list'] = self.list_size.value()
        sizes['note'] = self.note_size.value()
        sizes['hint'] = self.hint_size.value()
        sizes['delete'] = self.delete_size.value()


class EditorFontPage(QWidget):
    """File editor font and color settings."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.editor_family = QFontComboBox()
        layout.addRow("Font family:", self.editor_family)
        self.editor_size = _FontSizeCombo()
        layout.addRow("Font size:", self.editor_size)
        self.editor_fg = _ColorRow(default_hint='default: foreground')
        layout.addRow("Foreground:", self.editor_fg)
        self.editor_bg = _ColorRow(default_hint='default: background')
        layout.addRow("Background:", self.editor_bg)
        _connect_changed(self)

    def load_from_data(self, data):
        font = data.get('font') or {}
        editor = (data.get('colors') or {}).get('editor') or {}
        self.editor_family.setCurrentText(
            str(font.get('editor_family') or editor.get('font_family') or 'Consolas'))
        self.editor_size.setValue(
            int(font.get('editor_size') or editor.get('font_size', 10)))
        self.editor_fg.setText(str(editor.get('foreground', '')))
        self.editor_bg.setText(str(editor.get('background', '')))

    def save_to_data(self, data):
        font = _ensure_font(data)
        font['editor_family'] = self.editor_family.currentText()
        font['editor_size'] = self.editor_size.value()
        colors = _ensure_colors(data)
        editor = _ensure_section(colors, 'editor')
        _save_color_val(editor, 'foreground', self.editor_fg)
        _save_color_val(editor, 'background', self.editor_bg)
        # Remove legacy editor font keys
        for k in ('font_family', 'font_size'):
            if k in editor:
                del editor[k]
        if not editor:
            del colors['editor']
