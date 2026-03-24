# dialogs.py - Color picker, font picker, settings dialog

from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *

from ruamel.yaml.comments import CommentedMap

import state
from state import dbg, LOG_INFO, LOG_WARN
from config import _qt_colors, _parse_color, _color_to_config


class ColorPlane(QWidget):
  """2D color plane that displays two color axes; the third is set by a slider."""

  colorPicked = Signal(QColor)
  pickStarted = Signal()  # emitted on mouse press (start of new pick interaction)

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setMinimumSize(256, 256)
    self.setMaximumSize(256, 256)
    self._mode = 'rgb'       # 'rgb' or 'hsb'
    self._fixed_axis = 0     # which of the 3 channels is the slider (0,1,2)
    self._fixed_value = 0    # current slider value (0-255, or 0-359 for hue)
    self._img = None
    self._marker_x = 128
    self._marker_y = 128
    self._rebuild()

  def set_mode(self, mode, fixed_axis, fixed_value):
    self._mode = mode
    self._fixed_axis = fixed_axis
    self._fixed_value = fixed_value
    self._rebuild()
    self.update()

  def set_fixed_value(self, val):
    self._fixed_value = val
    self._rebuild()
    self.update()

  def set_marker_from_color(self, color):
    """Position the marker to match *color* on the current plane."""
    if self._mode == 'rgb':
      channels = [color.red(), color.green(), color.blue()]
    else:
      channels = [color.hsvHue() % 360, color.hsvSaturation(), color.value()]
    axes = [i for i in range(3) if i != self._fixed_axis]
    self._marker_x = channels[axes[0]] if self._mode == 'rgb' else (
      int(channels[axes[0]] * 255 / 359) if axes[0] == 0 else channels[axes[0]])
    self._marker_y = 255 - (channels[axes[1]] if self._mode == 'rgb' else (
      int(channels[axes[1]] * 255 / 359) if axes[1] == 0 else channels[axes[1]]))
    self.update()

  def _rebuild(self):
    img = QImage(256, 256, QImage.Format.Format_RGB32)
    axes = [i for i in range(3) if i != self._fixed_axis]
    for y in range(256):
      for x in range(256):
        vals = [0, 0, 0]
        vals[self._fixed_axis] = self._fixed_value
        vals[axes[0]] = x
        vals[axes[1]] = 255 - y
        if self._mode == 'rgb':
          c = QColor(vals[0], vals[1], vals[2])
        else:
          # HSB: channel 0=H(0-359), 1=S(0-255), 2=V(0-255)
          h = int(vals[0] * 359 / 255) if True else vals[0]
          s = vals[1]
          v = vals[2]
          c = QColor.fromHsv(h % 360, min(s, 255), min(v, 255))
        img.setPixelColor(x, y, c)
    self._img = img

  def paintEvent(self, event):
    p = QPainter(self)
    if self._img:
      p.drawImage(0, 0, self._img)
    # Draw crosshair marker
    mx, my = self._marker_x, self._marker_y
    p.setPen(QPen(Qt.white, 1))
    p.drawEllipse(mx - 5, my - 5, 10, 10)
    p.setPen(QPen(Qt.black, 1))
    p.drawEllipse(mx - 4, my - 4, 8, 8)

  def _pick(self, pos):
    x = max(0, min(255, pos.x()))
    y = max(0, min(255, pos.y()))
    self._marker_x = x
    self._marker_y = y
    axes = [i for i in range(3) if i != self._fixed_axis]
    vals = [0, 0, 0]
    vals[self._fixed_axis] = self._fixed_value
    vals[axes[0]] = x
    vals[axes[1]] = 255 - y
    if self._mode == 'rgb':
      c = QColor(vals[0], vals[1], vals[2])
    else:
      h = int(vals[0] * 359 / 255)
      c = QColor.fromHsv(h % 360, min(vals[1], 255), min(vals[2], 255))
    self.update()
    self.colorPicked.emit(c)

  def mousePressEvent(self, event):
    if event.button() == Qt.MouseButton.LeftButton:
      self.pickStarted.emit()
      self._pick(event.position().toPoint())

  def mouseMoveEvent(self, event):
    if event.buttons() & Qt.MouseButton.LeftButton:
      self._pick(event.position().toPoint())


class ColorPickerWidget(QWidget):
  """Full color picker with RGB/HSB sliders, 2D plane, hex input,
  color history, and Qt named color presets."""

  colorChanged = Signal(QColor)

  _MAX_HISTORY = 16
  _MAX_SAVED = 16

  def __init__(self, initial_color=None, parent=None):
    super().__init__(parent)
    self._color = initial_color or QColor(0, 0, 0)
    self._updating = False  # prevent signal loops
    self._history = []

    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)

    # --- Mode toggle ---
    mode_row = QHBoxLayout()
    self._rgb_btn = QPushButton("RGB")
    self._hsb_btn = QPushButton("HSB")
    self._rgb_btn.setCheckable(True)
    self._hsb_btn.setCheckable(True)
    self._rgb_btn.setChecked(True)
    self._rgb_btn.clicked.connect(lambda: self._set_mode('rgb'))
    self._hsb_btn.clicked.connect(lambda: self._set_mode('hsb'))
    mode_row.addWidget(self._rgb_btn)
    mode_row.addWidget(self._hsb_btn)
    mode_row.addStretch()

    # Current color swatch + named color indicator
    self._swatch = QLabel()
    self._swatch.setFixedSize(40, 24)
    self._swatch.setFrameStyle(QLabel.Shape.Box)
    mode_row.addWidget(QLabel("Current:"))
    mode_row.addWidget(self._swatch)
    layout.addLayout(mode_row)

    # --- Plane + sliders ---
    plane_row = QHBoxLayout()

    self._plane = ColorPlane()
    self._plane.colorPicked.connect(self._on_plane_pick)
    self._plane.pickStarted.connect(self._on_plane_start)
    plane_row.addWidget(self._plane)

    # Sliders column
    slider_col = QVBoxLayout()
    self._slider_labels = []
    self._sliders = []
    self._slider_inputs = []
    self._slider_radios = []
    for i in range(3):
      row = QHBoxLayout()
      radio = QPushButton()
      radio.setCheckable(True)
      radio.setFixedWidth(30)
      radio.clicked.connect(lambda checked, idx=i: self._set_fixed_axis(idx))
      self._slider_radios.append(radio)
      row.addWidget(radio)

      lbl = QLabel()
      lbl.setFixedWidth(14)
      self._slider_labels.append(lbl)
      row.addWidget(lbl)

      sl = QSlider(Qt.Orientation.Horizontal)
      sl.setRange(0, 255)
      sl.valueChanged.connect(lambda val, idx=i: self._on_slider(idx, val))
      self._sliders.append(sl)
      row.addWidget(sl, 1)

      inp = QLineEdit()
      inp.setFixedWidth(45)
      inp.setMaxLength(3)
      inp.editingFinished.connect(lambda idx=i: self._on_slider_input(idx))
      self._slider_inputs.append(inp)
      row.addWidget(inp)

      slider_col.addLayout(row)

    # Hex input
    hex_row = QHBoxLayout()
    hex_row.addWidget(QLabel("Hex:"))
    self._hex_input = QLineEdit()
    self._hex_input.setFixedWidth(80)
    self._hex_input.setMaxLength(7)
    self._hex_input.setPlaceholderText("#000000")
    self._hex_input.editingFinished.connect(self._on_hex_input)
    hex_row.addWidget(self._hex_input)
    self._name_label = QLabel()
    self._name_label.setStyleSheet("color: gray; font-style: italic;")
    hex_row.addWidget(self._name_label)
    hex_row.addStretch()
    slider_col.addLayout(hex_row)
    slider_col.addStretch()

    plane_row.addLayout(slider_col, 1)
    layout.addLayout(plane_row)

    # --- Color history ---
    hist_lbl = QLabel("Recent:")
    layout.addWidget(hist_lbl)
    self._history_row = QHBoxLayout()
    self._history_widgets = []
    for i in range(self._MAX_HISTORY):
      btn = QPushButton()
      btn.setFixedSize(20, 20)
      btn.setVisible(False)
      btn.clicked.connect(lambda checked, idx=i: self._pick_history(idx))
      self._history_widgets.append(btn)
      self._history_row.addWidget(btn)
    self._history_row.addStretch()
    layout.addLayout(self._history_row)

    # --- Saved colors ---
    saved_header = QHBoxLayout()
    saved_header.addWidget(QLabel("Saved:"))
    self._save_btn = QPushButton("Save")
    self._save_btn.setFixedWidth(40)
    self._save_btn.setToolTip("Save current color to the bar below")
    self._save_btn.clicked.connect(self._save_color)
    saved_header.addWidget(self._save_btn)
    saved_header.addStretch()
    layout.addLayout(saved_header)
    self._saved_row = QHBoxLayout()
    self._saved_widgets = []
    self._saved_colors = []
    for i in range(self._MAX_SAVED):
      btn = QPushButton()
      btn.setFixedSize(20, 20)
      btn.setVisible(False)
      btn.clicked.connect(lambda checked, idx=i: self._pick_saved(idx))
      btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
      btn.customContextMenuRequested.connect(lambda pos, idx=i: self._remove_saved(idx))
      self._saved_widgets.append(btn)
      self._saved_row.addWidget(btn)
    self._saved_row.addStretch()
    layout.addLayout(self._saved_row)

    # --- Qt named colors ---
    named_lbl = QLabel("Named colors:")
    layout.addWidget(named_lbl)
    named_grid = QHBoxLayout()
    named_grid.setSpacing(2)
    for name, qt_color in _qt_colors.items():
      btn = QPushButton()
      btn.setFixedSize(20, 20)
      c = QColor(qt_color)
      btn.setStyleSheet("background-color: %s; border: 1px solid #888;" % c.name())
      btn.setToolTip(name)
      btn.clicked.connect(lambda checked, clr=c: self.set_color(clr))
      named_grid.addWidget(btn)
    named_grid.addStretch()
    layout.addLayout(named_grid)

    # Init mode
    self._mode = 'rgb'
    self._fixed_axis = 0
    self._slider_radios[0].setChecked(True)
    self._update_labels()
    self._sync_ui_from_color()
    self._load_saved_colors()
    self._load_recent_colors()

  def _set_mode(self, mode):
    self._mode = mode
    self._rgb_btn.setChecked(mode == 'rgb')
    self._hsb_btn.setChecked(mode == 'hsb')
    self._fixed_axis = 0
    self._slider_radios[0].setChecked(True)
    self._slider_radios[1].setChecked(False)
    self._slider_radios[2].setChecked(False)
    if mode == 'hsb':
      self._sliders[0].setRange(0, 359)
    else:
      self._sliders[0].setRange(0, 255)
    self._sliders[1].setRange(0, 255)
    self._sliders[2].setRange(0, 255)
    self._update_labels()
    self._sync_ui_from_color()

  def _set_fixed_axis(self, idx):
    self._fixed_axis = idx
    for i, r in enumerate(self._slider_radios):
      r.setChecked(i == idx)
    self._sync_ui_from_color()

  def _update_labels(self):
    if self._mode == 'rgb':
      names = ['R', 'G', 'B']
    else:
      names = ['H', 'S', 'V']
    for i, lbl in enumerate(self._slider_labels):
      lbl.setText(names[i])
    for i, r in enumerate(self._slider_radios):
      r.setText(names[i])

  def _sync_ui_from_color(self):
    if self._updating:
      return
    self._updating = True
    c = self._color
    if self._mode == 'rgb':
      vals = [c.red(), c.green(), c.blue()]
    else:
      vals = [c.hsvHue() % 360, c.hsvSaturation(), c.value()]
    for i in range(3):
      self._sliders[i].setValue(vals[i])
      self._slider_inputs[i].setText(str(vals[i]))
    self._hex_input.setText(c.name())
    self._swatch.setStyleSheet("background-color: %s;" % c.name())
    self._update_name_label(c)
    self._plane.set_mode(self._mode, self._fixed_axis, vals[self._fixed_axis])
    self._plane.set_marker_from_color(c)
    self._updating = False

  def _update_name_label(self, color):
    """Show the Qt named color name if the color matches one, else clear."""
    for name, qt_color in _qt_colors.items():
      if QColor(qt_color) == color:
        self._name_label.setText(name)
        return
    self._name_label.setText('')

  def _on_slider(self, idx, val):
    if self._updating:
      return
    self._updating = True
    self._slider_inputs[idx].setText(str(val))
    # Auto-select this slider as the fixed axis for the 2D plane
    if idx != self._fixed_axis:
      self._fixed_axis = idx
      for i, r in enumerate(self._slider_radios):
        r.setChecked(i == idx)
    # Rebuild color from all sliders
    vals = [self._sliders[i].value() for i in range(3)]
    if self._mode == 'rgb':
      self._color = QColor(vals[0], vals[1], vals[2])
    else:
      self._color = QColor.fromHsv(vals[0] % 360, min(vals[1], 255), min(vals[2], 255))
    self._hex_input.setText(self._color.name())
    self._swatch.setStyleSheet("background-color: %s;" % self._color.name())
    self._update_name_label(self._color)
    self._plane.set_mode(self._mode, self._fixed_axis, vals[self._fixed_axis])
    self._plane.set_marker_from_color(self._color)
    self._updating = False
    self.colorChanged.emit(self._color)

  def _on_slider_input(self, idx):
    try:
      val = int(self._slider_inputs[idx].text())
      val = max(0, min(self._sliders[idx].maximum(), val))
      self._sliders[idx].setValue(val)
    except ValueError:
      pass

  def _on_hex_input(self):
    txt = self._hex_input.text().strip()
    if not txt.startswith('#'):
      txt = '#' + txt
    c = QColor(txt)
    if c.isValid():
      self.set_color(c, add_history=True)

  def _on_plane_start(self):
    """Save the current color to recent when starting a new plane pick."""
    self._add_to_history(self._color)

  def _on_plane_pick(self, color):
    if self._updating:
      return
    self._color = color
    self._updating = True
    if self._mode == 'rgb':
      vals = [color.red(), color.green(), color.blue()]
    else:
      vals = [color.hsvHue() % 360, color.hsvSaturation(), color.value()]
    for i in range(3):
      if i != self._fixed_axis:
        self._sliders[i].setValue(vals[i])
        self._slider_inputs[i].setText(str(vals[i]))
    self._hex_input.setText(color.name())
    self._swatch.setStyleSheet("background-color: %s;" % color.name())
    self._update_name_label(color)
    self._updating = False
    self.colorChanged.emit(self._color)

  def set_color(self, color, add_history=False):
    if add_history:
      self._add_to_history(self._color)
    self._color = QColor(color)
    self._sync_ui_from_color()
    self.colorChanged.emit(self._color)

  @property
  def color(self):
    return QColor(self._color)

  def _load_recent_colors(self):
    try:
      recent = state.ui_state.recent_colors if state.ui_state else []
      self._history = [QColor(s) for s in recent if QColor(s).isValid()]
    except Exception:
      self._history = []
    self._update_history_ui()

  def _persist_recent_colors(self):
    if state.ui_state:
      state.ui_state.recent_colors = [c.name() for c in self._history]

  def _add_to_history(self, color):
    # Don't add duplicates
    cname = color.name()
    for h in self._history:
      if h.name() == cname:
        return
    self._history.insert(0, QColor(color))
    if len(self._history) > self._MAX_HISTORY:
      self._history.pop()
    self._update_history_ui()
    self._persist_recent_colors()

  def add_final_to_history(self):
    """Add the current color to recent history (call on dialog accept)."""
    self._add_to_history(self._color)

  def _update_history_ui(self):
    for i, btn in enumerate(self._history_widgets):
      if i < len(self._history):
        c = self._history[i]
        btn.setStyleSheet("background-color: %s; border: 1px solid #888;" % c.name())
        btn.setToolTip(c.name())
        btn.setVisible(True)
      else:
        btn.setVisible(False)

  def _pick_history(self, idx):
    if idx < len(self._history):
      self.set_color(self._history[idx])

  # --- Saved colors ---

  def _load_saved_colors(self):
    try:
      saved = state.ui_state.saved_colors if state.ui_state else []
      self._saved_colors = [QColor(s) for s in saved if QColor(s).isValid()]
    except Exception:
      self._saved_colors = []
    self._update_saved_ui()

  def _save_color(self):
    c = QColor(self._color)
    for s in self._saved_colors:
      if s.name() == c.name():
        return
    self._saved_colors.append(c)
    if len(self._saved_colors) > self._MAX_SAVED:
      self._saved_colors.pop(0)
    self._update_saved_ui()
    self._persist_saved_colors()

  def _remove_saved(self, idx):
    if idx < len(self._saved_colors):
      self._saved_colors.pop(idx)
      self._update_saved_ui()
      self._persist_saved_colors()

  def _persist_saved_colors(self):
    if state.ui_state:
      state.ui_state.saved_colors = [c.name() for c in self._saved_colors]

  def _update_saved_ui(self):
    for i, btn in enumerate(self._saved_widgets):
      if i < len(self._saved_colors):
        c = self._saved_colors[i]
        btn.setStyleSheet("background-color: %s; border: 1px solid #888;" % c.name())
        btn.setToolTip(c.name() + " (right-click to remove)")
        btn.setVisible(True)
      else:
        btn.setVisible(False)

  def _pick_saved(self, idx):
    if idx < len(self._saved_colors):
      self.set_color(self._saved_colors[idx])


# ---------------------------------------------------------------------------
# Font + Color picker dialog
# ---------------------------------------------------------------------------

class FontPickerDialog(QDialog):
  """Font and color selection dialog with live preview."""
  def __init__(self, current_family, font_size, fg_color=None, bg_color=None,
               parent=None, warn_text=None):
    super().__init__(parent)
    self.setWindowTitle("Font & Color Selection")
    self.resize(900, 650)
    self._selected_family = current_family
    self._font_size = font_size
    self._fg_color = QColor(fg_color) if fg_color else QColor(0, 0, 0)
    self._bg_color = QColor(bg_color) if bg_color else QColor(255, 255, 255)

    main_layout = QVBoxLayout(self)

    # Warning label (if any)
    if warn_text:
      warn = QLabel(warn_text)
      warn.setStyleSheet("color: red; font-weight: bold;")
      main_layout.addWidget(warn)

    # Top section: font list + color picker + preview
    top = QHBoxLayout()

    # --- Font list (left) ---
    font_col = QVBoxLayout()
    font_col.addWidget(QLabel("Font:"))
    self._filter = QLineEdit()
    self._filter.setPlaceholderText("Filter fonts...")
    self._filter.textChanged.connect(self._apply_filter)
    font_col.addWidget(self._filter)

    self._font_list = QListWidget()
    self._all_families = sorted(QFontDatabase.families())
    self._font_list.addItems(self._all_families)
    self._font_list.currentItemChanged.connect(self._on_font_selection)
    font_col.addWidget(self._font_list, 1)
    top.addLayout(font_col, 1)

    # --- Color picker (center) ---
    color_col = QVBoxLayout()
    # Toggle: editing FG or BG
    color_target_row = QHBoxLayout()
    self._fg_radio = QPushButton("Text Color")
    self._bg_radio = QPushButton("Background")
    self._fg_radio.setCheckable(True)
    self._bg_radio.setCheckable(True)
    self._fg_radio.setChecked(True)
    self._fg_radio.clicked.connect(lambda: self._set_color_target('fg'))
    self._bg_radio.clicked.connect(lambda: self._set_color_target('bg'))
    color_target_row.addWidget(self._fg_radio)
    color_target_row.addWidget(self._bg_radio)
    color_col.addLayout(color_target_row)

    self._color_picker = ColorPickerWidget(self._fg_color)
    self._color_picker.colorChanged.connect(self._on_color_changed)
    color_col.addWidget(self._color_picker)
    top.addLayout(color_col, 2)

    main_layout.addLayout(top)

    # --- Preview ---
    main_layout.addWidget(QLabel("Preview:"))
    self._preview = QTextEdit()
    self._preview.setReadOnly(True)
    self._preview.setMaximumHeight(100)
    self._preview_text = (
      "The quick brown fox jumps over the lazy dog.\n"
      "ABCDEFGHIJKLMNOPQRSTUVWXYZ\n"
      "abcdefghijklmnopqrstuvwxyz\n"
      "0123456789 !@#$%^&*()_+-=\n"
      "<nick> Hello, world!\n"
      "* user has joined #channel"
    )
    self._preview.setText(self._preview_text)
    main_layout.addWidget(self._preview)

    # --- Save + buttons ---
    self._save_cb = QCheckBox("Save selection to config file")
    self._save_cb.setChecked(True)
    main_layout.addWidget(self._save_cb)

    buttons = QDialogButtonBox(
      QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(self._on_accept)
    buttons.rejected.connect(self.reject)
    main_layout.addWidget(buttons)

    self._color_target = 'fg'
    self._update_preview()

    # Pre-select font
    self._try_select_font(current_family)

  def _try_select_font(self, family):
    items = self._font_list.findItems(family, Qt.MatchFlag.MatchExactly)
    if items:
      self._font_list.setCurrentItem(items[0])
      self._font_list.scrollToItem(items[0])
      return
    # Try common monospace fonts
    for preferred in ('Consolas', 'Courier New', 'DejaVu Sans Mono', 'Liberation Mono'):
      items = self._font_list.findItems(preferred, Qt.MatchFlag.MatchExactly)
      if items:
        self._font_list.setCurrentItem(items[0])
        self._font_list.scrollToItem(items[0])
        return

  def _apply_filter(self, text):
    text_lower = text.lower()
    for i in range(self._font_list.count()):
      item = self._font_list.item(i)
      item.setHidden(text_lower not in item.text().lower())

  def _on_font_selection(self, current, previous):
    if current:
      self._selected_family = current.text()
      self._update_preview()

  def _set_color_target(self, target):
    self._color_target = target
    self._fg_radio.setChecked(target == 'fg')
    self._bg_radio.setChecked(target == 'bg')
    # Load the appropriate color into the picker
    if target == 'fg':
      self._color_picker.set_color(self._fg_color)
    else:
      self._color_picker.set_color(self._bg_color)

  def _on_color_changed(self, color):
    if self._color_target == 'fg':
      self._fg_color = QColor(color)
    else:
      self._bg_color = QColor(color)
    self._update_preview()

  def _update_preview(self):
    self._preview.setFontFamily(self._selected_family)
    self._preview.setFontPointSize(self._font_size)
    self._preview.setStyleSheet(
      "QTextEdit { color: %s; background-color: %s; }" % (
        self._fg_color.name(), self._bg_color.name()))

  def _on_accept(self):
    self._color_picker.add_final_to_history()
    self.accept()

  @property
  def selected_family(self):
    return self._selected_family

  @property
  def selected_fg_color(self):
    return self._fg_color

  @property
  def selected_bg_color(self):
    return self._bg_color

  @property
  def should_save(self):
    return self._save_cb.isChecked()


_FALLBACK_FONTS = ('Consolas', 'DejaVu Sans Mono', 'Liberation Mono',
                    'Courier New', 'Monospace')


def _validate_font(cfg):
  """Check if the configured font exists.  If not, fall back or show a picker."""
  db_families = {f.lower() for f in QFontDatabase.families()}
  if cfg.fontfamily.lower() in db_families:
    return  # font found, all good

  # If the user never explicitly set a font, silently fall back
  font_data = cfg._data.get('font') or {}
  user_set = 'family' in font_data
  if not user_set:
    for fb in _FALLBACK_FONTS:
      if fb.lower() in db_families:
        cfg.fontfamily = fb
        dbg(LOG_INFO, 'Default font not found, using %s' % fb)
        return
    # Nothing matched — just leave it, Qt will substitute
    return

  # User explicitly configured a missing font — show the picker dialog
  preview_pt = max(8, cfg.fontheight * 3 // 4)
  dlg = FontPickerDialog(
    cfg.fontfamily, preview_pt,
    fg_color=cfg.fgcolor, bg_color=cfg.bgcolor,
    warn_text="The font \"%s\" was not found on this system.\nPlease select a replacement:" % cfg.fontfamily,
  )
  if dlg.exec() == QDialog.DialogCode.Accepted:
    new_family = dlg.selected_family
    cfg.fontfamily = new_family
    cfg.fgcolor = dlg.selected_fg_color
    cfg.bgcolor = dlg.selected_bg_color
    font_data = cfg._data.get('font')
    if font_data is None:
      font_data = {}
      cfg._data['font'] = font_data
    font_data['family'] = new_family
    # Save colors under colors: section
    colors_data = cfg._data.get('colors')
    if colors_data is None:
      colors_data = CommentedMap()
      cfg._data['colors'] = colors_data
    colors_data['foreground'] = _color_to_config(dlg.selected_fg_color)
    colors_data['background'] = _color_to_config(dlg.selected_bg_color)
    if dlg.should_save:
      cfg.save()
    dbg(LOG_INFO, 'Font changed to: %s' % new_family)
  else:
    # User cancelled — fall back to a safe default
    cfg.fontfamily = 'Courier New'
    dbg(LOG_WARN, 'Font "%s" not found, falling back to Courier New' % cfg.fontfamily)


def open_settings(page=None):
  from settings.settings_dialog import SettingsDialog
  dlg = SettingsDialog(state.config, parent=state.app.mainwin)
  if page:
    dlg.select_page(page)
  dlg.exec()
