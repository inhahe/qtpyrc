# dialogs.py - Color picker, font picker, settings dialog

from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *

from ruamel.yaml.comments import CommentedMap
import math

import state
from state import dbg, LOG_INFO, LOG_WARN
from config import _qt_colors, _parse_color, _color_to_config


# ---- Color space conversions (internal) ----

def _srgb_to_linear(c):
  if c <= 0.04045:
    return c / 12.92
  return ((c + 0.055) / 1.055) ** 2.4

def _linear_to_srgb(c):
  if c <= 0.0031308:
    return 12.92 * c
  return 1.055 * c ** (1.0 / 2.4) - 0.055

_D65_X, _D65_Y, _D65_Z = 0.95047, 1.0, 1.08883

def _lab_f(t):
  if t > 0.008856:
    return t ** (1.0 / 3.0)
  return 7.787037 * t + 16.0 / 116.0

def _lab_f_inv(t):
  if t > 6.0 / 29.0:
    return t ** 3
  return 3.0 * (6.0 / 29.0) ** 2 * (t - 4.0 / 29.0)


# ---- Per-model to_rgb / from_rgb functions ----
# to_rgb(v0, v1, v2) -> (r, g, b) floats (may be out of 0-255 for perceptual spaces)
# from_rgb(r, g, b)   -> (v0, v1, v2) model values

def _hsb_to_rgb(h, s, v):
  c = QColor.fromHsv(int(h) % 360, max(0, min(255, int(s))), max(0, min(255, int(v))))
  return (c.red(), c.green(), c.blue())

def _rgb_to_hsb(r, g, b):
  c = QColor(int(r), int(g), int(b))
  return (c.hsvHue() % 360, c.hsvSaturation(), c.value())

def _hsl_to_rgb(h, s, l):
  c = QColor.fromHsl(int(h) % 360, max(0, min(255, int(s))), max(0, min(255, int(l))))
  return (c.red(), c.green(), c.blue())

def _rgb_to_hsl(r, g, b):
  c = QColor(int(r), int(g), int(b))
  return (c.hslHue() % 360, c.hslSaturation(), c.lightness())

def _lab_to_rgb(L, a, b2):
  fy = (L + 16.0) / 116.0
  fx = a / 500.0 + fy
  fz = fy - b2 / 200.0
  x = _D65_X * _lab_f_inv(fx)
  y = _D65_Y * _lab_f_inv(fy)
  z = _D65_Z * _lab_f_inv(fz)
  rl =  3.2404542 * x - 1.5371385 * y - 0.4985314 * z
  gl = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
  bl =  0.0556434 * x - 0.2040259 * y + 1.0572252 * z
  return (_linear_to_srgb(max(0.0, rl)) * 255.0,
          _linear_to_srgb(max(0.0, gl)) * 255.0,
          _linear_to_srgb(max(0.0, bl)) * 255.0)

def _rgb_to_lab(r, g, b):
  rl = _srgb_to_linear(r / 255.0)
  gl = _srgb_to_linear(g / 255.0)
  bl = _srgb_to_linear(b / 255.0)
  x = 0.4124564 * rl + 0.3575761 * gl + 0.1804375 * bl
  y = 0.2126729 * rl + 0.7151522 * gl + 0.0721750 * bl
  z = 0.0193339 * rl + 0.1191920 * gl + 0.9503041 * bl
  fx = _lab_f(x / _D65_X)
  fy = _lab_f(y / _D65_Y)
  fz = _lab_f(z / _D65_Z)
  return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))

def _lch_to_rgb(L, C, h):
  a = C * math.cos(math.radians(h))
  b = C * math.sin(math.radians(h))
  return _lab_to_rgb(L, a, b)

def _rgb_to_lch(r, g, b):
  L, a, b2 = _rgb_to_lab(r, g, b)
  C = math.sqrt(a * a + b2 * b2)
  h = math.degrees(math.atan2(b2, a)) % 360
  return (L, C, h)


# ---- Color models registry ----
# Each model: vars (component names), ranges (min/max per component),
# to_rgb (model values -> RGB floats), from_rgb (RGB ints -> model values).
# Button text = dict key.  Slider labels = vars entries.

_MODELS = {
  'RGB': {
    'vars': ['R', 'G', 'B'],
    'ranges': [(0, 255), (0, 255), (0, 255)],
    'to_rgb': lambda r, g, b: (float(r), float(g), float(b)),
    'from_rgb': lambda r, g, b: (float(r), float(g), float(b)),
  },
  'HSB': {
    'vars': ['H', 'S', 'B'],
    'ranges': [(0, 359), (0, 255), (0, 255)],
    'to_rgb': _hsb_to_rgb,
    'from_rgb': _rgb_to_hsb,
  },
  'HSL': {
    'vars': ['H', 'S', 'L'],
    'ranges': [(0, 359), (0, 255), (0, 255)],
    'to_rgb': _hsl_to_rgb,
    'from_rgb': _rgb_to_hsl,
  },
  'L*a*b*': {
    'vars': ['L*', 'a*', 'b*'],
    'ranges': [(0, 100), (-128, 127), (-128, 127)],
    'to_rgb': _lab_to_rgb,
    'from_rgb': _rgb_to_lab,
  },
  'L*C*h*': {
    'vars': ['L*', 'C*', 'h*'],
    'ranges': [(0, 100), (0, 150), (0, 359)],
    'to_rgb': _lch_to_rgb,
    'from_rgb': _rgb_to_lch,
  },
}

# Models whose to_rgb is expensive (render plane at lower resolution)
_SLOW_MODELS = {'L*a*b*', 'L*C*h*'}

def _plane_to_val(pixel, lo, hi):
  """Map 0-255 pixel coordinate to channel value range."""
  return lo + pixel * (hi - lo) / 255.0

def _val_to_plane(val, lo, hi):
  """Map channel value to 0-255 pixel coordinate."""
  if hi == lo:
    return 0
  return max(0, min(255, int(round((val - lo) * 255.0 / (hi - lo)))))

def _clamp_rgb(r, g, b):
  """Clamp float RGB to valid QColor ints; return (QColor, in_gamut)."""
  ok = -0.5 <= r <= 255.5 and -0.5 <= g <= 255.5 and -0.5 <= b <= 255.5
  return QColor(max(0, min(255, int(round(r)))),
                max(0, min(255, int(round(g)))),
                max(0, min(255, int(round(b))))), ok


class ColorPlane(QWidget):
  """2D color plane that displays two color axes; the third is set by a slider."""

  colorPicked = Signal(QColor)
  pickStarted = Signal()

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setMinimumSize(256, 256)
    self.setMaximumSize(256, 256)
    self._model = 'RGB'
    self._fixed_axis = 0
    self._fixed_value = 0
    self._img = None
    self._marker_x = 128
    self._marker_y = 128
    self._rebuild()

  def set_model(self, model, fixed_axis, fixed_value):
    self._model = model
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
    m = _MODELS[self._model]
    channels = m['from_rgb'](color.red(), color.green(), color.blue())
    ranges = m['ranges']
    axes = [i for i in range(3) if i != self._fixed_axis]
    self._marker_x = _val_to_plane(channels[axes[0]], *ranges[axes[0]])
    self._marker_y = 255 - _val_to_plane(channels[axes[1]], *ranges[axes[1]])
    self.update()

  def _rebuild(self):
    m = _MODELS[self._model]
    to_rgb = m['to_rgb']
    ranges = m['ranges']
    axes = [i for i in range(3) if i != self._fixed_axis]
    res = 128 if self._model in _SLOW_MODELS else 256
    img = QImage(res, res, QImage.Format.Format_RGB32)
    scale = 255.0 / (res - 1) if res > 1 else 1.0
    for y in range(res):
      for x in range(res):
        vals = [0.0, 0.0, 0.0]
        vals[self._fixed_axis] = float(self._fixed_value)
        px = int(x * scale)
        py = int((res - 1 - y) * scale)
        vals[axes[0]] = _plane_to_val(px, *ranges[axes[0]])
        vals[axes[1]] = _plane_to_val(py, *ranges[axes[1]])
        r, g, b = to_rgb(*vals)
        c = QColor(max(0, min(255, int(round(r)))),
                   max(0, min(255, int(round(g)))),
                   max(0, min(255, int(round(b)))))
        img.setPixelColor(x, y, c)
    if res < 256:
      img = img.scaled(256, 256, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    self._img = img

  def paintEvent(self, event):
    p = QPainter(self)
    if self._img:
      p.drawImage(0, 0, self._img)
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
    m = _MODELS[self._model]
    ranges = m['ranges']
    axes = [i for i in range(3) if i != self._fixed_axis]
    vals = [0.0, 0.0, 0.0]
    vals[self._fixed_axis] = float(self._fixed_value)
    vals[axes[0]] = _plane_to_val(x, *ranges[axes[0]])
    vals[axes[1]] = _plane_to_val(255 - y, *ranges[axes[1]])
    r, g, b = m['to_rgb'](*vals)
    c, _ = _clamp_rgb(r, g, b)
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
  """Full color picker with multiple color model sliders, 2D plane, hex input,
  color history, and Qt named color presets."""

  colorChanged = Signal(QColor)

  _MAX_HISTORY = 16
  _MAX_SAVED = 16

  def __init__(self, initial_color=None, parent=None):
    super().__init__(parent)
    self._color = initial_color or QColor(0, 0, 0)
    self._updating = False
    self._history = []
    self._model = 'RGB'

    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)

    _fm = self.fontMetrics()
    _char = _fm.horizontalAdvance('0')
    _w4 = _char * 6   # room for "-128"
    _w7 = _char * 10  # room for "#FFFFFF"
    # Width for min/max labels (fits "-128")
    _wminmax = _fm.horizontalAdvance('-128') + 4

    def _fit_btn(btn):
      w = btn.fontMetrics().horizontalAdvance(btn.text()) + 18
      btn.setFixedWidth(w)

    # --- Plane + sliders ---
    plane_row = QHBoxLayout()

    self._plane = ColorPlane()
    self._plane.colorPicked.connect(self._on_plane_pick)
    self._plane.pickStarted.connect(self._on_plane_start)
    plane_row.addWidget(self._plane)

    slider_col = QVBoxLayout()

    # Model buttons — one per entry in _MODELS, generated from the dict
    mode_row = QHBoxLayout()
    self._model_buttons = {}
    for model_name in _MODELS:
      btn = QPushButton(model_name)
      _fit_btn(btn)
      btn.setCheckable(True)
      btn.clicked.connect(lambda checked, m=model_name: self._set_model(m))
      mode_row.addWidget(btn)
      self._model_buttons[model_name] = btn
    mode_row.addStretch()
    slider_col.addLayout(mode_row)

    # Three slider rows: [radio/varname] [min] [slider] [max] [input]
    self._sliders = []
    self._slider_inputs = []
    self._slider_radios = []
    self._min_labels = []
    self._max_labels = []
    for i in range(3):
      row = QHBoxLayout()

      radio = QPushButton()
      radio.setCheckable(True)
      radio.setFixedWidth(38)
      radio.clicked.connect(lambda checked, idx=i: self._set_fixed_axis(idx))
      self._slider_radios.append(radio)
      row.addWidget(radio)

      min_lbl = QLabel()
      min_lbl.setFixedWidth(_wminmax)
      min_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
      self._min_labels.append(min_lbl)
      row.addWidget(min_lbl)

      sl = QSlider(Qt.Orientation.Horizontal)
      sl.setRange(0, 255)
      sl.valueChanged.connect(lambda val, idx=i: self._on_slider(idx, val))
      self._sliders.append(sl)
      row.addWidget(sl, 1)

      max_lbl = QLabel()
      max_lbl.setFixedWidth(_wminmax)
      self._max_labels.append(max_lbl)
      row.addWidget(max_lbl)

      inp = QLineEdit()
      inp.setFixedWidth(_w4)
      inp.setMaxLength(4)
      inp.editingFinished.connect(lambda idx=i: self._on_slider_input(idx))
      self._slider_inputs.append(inp)
      row.addWidget(inp)

      slider_col.addLayout(row)

    # Hex input + uppercase toggle + copy
    hex_row = QHBoxLayout()
    hex_row.addWidget(QLabel("Hex:"))
    self._hex_input = QLineEdit()
    self._hex_input.setFixedWidth(_w7)
    self._hex_input.setMaxLength(7)
    self._hex_input.setPlaceholderText("#000000")
    self._hex_input.editingFinished.connect(self._on_hex_input)
    hex_row.addWidget(self._hex_input)
    self._hex_upper_btn = QPushButton("AA")
    self._hex_upper_btn.setCheckable(True)
    _fit_btn(self._hex_upper_btn)
    self._hex_upper_btn.setToolTip("Toggle uppercase hex")
    self._hex_upper = bool(state.ui_state and state.ui_state.hex_uppercase)
    self._hex_upper_btn.setChecked(self._hex_upper)
    self._hex_upper_btn.toggled.connect(self._on_hex_upper_toggle)
    hex_row.addWidget(self._hex_upper_btn)
    hex_copy = QPushButton("Copy")
    _fit_btn(hex_copy)
    hex_copy.setToolTip("Copy hex value to clipboard")
    hex_copy.clicked.connect(
        lambda: QApplication.clipboard().setText(self._hex_input.text()))
    hex_row.addWidget(hex_copy)
    hex_row.addStretch()
    slider_col.addLayout(hex_row)

    # Model value fields + copy (label changes with model)
    val_row = QHBoxLayout()
    self._val_label = QLabel("RGB:")
    val_row.addWidget(self._val_label)
    self._val_fields = []
    for ch in range(3):
      fld = QLineEdit()
      fld.setFixedWidth(_w4)
      fld.setMaxLength(4)
      fld.editingFinished.connect(self._on_val_input)
      self._val_fields.append(fld)
      val_row.addWidget(fld)
    val_copy = QPushButton("Copy")
    _fit_btn(val_copy)
    val_copy.setToolTip("Copy all values")
    val_copy.clicked.connect(lambda: QApplication.clipboard().setText(
        ', '.join(f.text() for f in self._val_fields)))
    val_row.addWidget(val_copy)
    val_row.addStretch()
    slider_col.addLayout(val_row)

    # Named color display + copy + eyedropper
    name_row = QHBoxLayout()
    name_row.addWidget(QLabel("Name:"))
    self._name_label = QLabel()
    name_row.addWidget(self._name_label)
    self._name_copy = QPushButton("Copy")
    _fit_btn(self._name_copy)
    self._name_copy.setToolTip("Copy named color to clipboard")
    self._name_copy.clicked.connect(
        lambda: QApplication.clipboard().setText(self._name_label.text()))
    self._name_copy.setVisible(False)
    name_row.addWidget(self._name_copy)
    name_row.addStretch()
    slider_col.addLayout(name_row)

    # Color swatch + eyedropper
    swatch_row = QHBoxLayout()
    self._swatch = QLabel()
    self._swatch.setFrameStyle(QLabel.Shape.Box)
    sp = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    self._swatch.setSizePolicy(sp)
    self._swatch.setMinimumSize(30, 30)
    swatch_row.addWidget(self._swatch)
    self._eyedrop_btn = QPushButton("Eyedropper")
    _fit_btn(self._eyedrop_btn)
    self._eyedrop_btn.setToolTip("Sample a color from anywhere on screen")
    self._eyedrop_btn.clicked.connect(self._start_eyedropper)
    swatch_row.addWidget(self._eyedrop_btn)
    swatch_row.addStretch()
    slider_col.addLayout(swatch_row)

    plane_row.addLayout(slider_col, 1)
    layout.addLayout(plane_row)

    self._swatch.installEventFilter(self)

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

    # Apply initial model
    self._set_model('RGB')
    self._load_saved_colors()
    self._load_recent_colors()

  def eventFilter(self, obj, event):
    if obj is self._swatch and event.type() == event.Type.Resize:
      h = self._swatch.height()
      self._swatch.setFixedWidth(h)
    return super().eventFilter(obj, event)

  # ---- Model switching ----

  def _set_model(self, model_name):
    """Switch to a different color model — updates buttons, sliders, labels, plane."""
    self._model = model_name
    m = _MODELS[model_name]
    for name, btn in self._model_buttons.items():
      btn.setChecked(name == model_name)
    self._fixed_axis = 0
    for i in range(3):
      self._slider_radios[i].setChecked(i == 0)
      self._slider_radios[i].setText(m['vars'][i])
      lo, hi = m['ranges'][i]
      self._sliders[i].setRange(lo, hi)
      self._min_labels[i].setText(str(lo))
      self._max_labels[i].setText(str(hi))
    self._val_label.setText(model_name + ':')
    self._sync_ui_from_color()

  def _set_fixed_axis(self, idx):
    self._fixed_axis = idx
    for i, r in enumerate(self._slider_radios):
      r.setChecked(i == idx)
    self._sync_ui_from_color()

  # ---- UI sync ----

  def _sync_ui_from_color(self):
    if self._updating:
      return
    self._updating = True
    c = self._color
    m = _MODELS[self._model]
    vals = m['from_rgb'](c.red(), c.green(), c.blue())
    for i in range(3):
      self._sliders[i].setValue(int(round(vals[i])))
      self._slider_inputs[i].setText(str(int(round(vals[i]))))
      self._val_fields[i].setText(str(int(round(vals[i]))))
    self._hex_input.setText(self._hex_str(c))
    self._swatch.setStyleSheet("background-color: %s;" % c.name())
    self._update_name_label(c)
    self._plane.set_model(self._model, self._fixed_axis, vals[self._fixed_axis])
    self._plane.set_marker_from_color(c)
    # Gamut indicator — check round-trip through current model
    r2, g2, b2 = m['to_rgb'](*vals)
    _, ok = _clamp_rgb(r2, g2, b2)
    self._update_gamut_indicator(ok)
    self._updating = False

  def _update_name_label(self, color):
    for name, qt_color in _qt_colors.items():
      if QColor(qt_color) == color:
        self._name_label.setText(name)
        self._name_copy.setVisible(True)
        return
    self._name_label.setText('')
    self._name_copy.setVisible(False)

  def _update_gamut_indicator(self, in_gamut):
    if in_gamut:
      self._hex_input.setStyleSheet('')
      self._hex_input.setToolTip('')
    else:
      self._hex_input.setStyleSheet('background-color: #FFE0B0;')
      self._hex_input.setToolTip(
          'Color is outside sRGB gamut \u2014 hex shows clamped value')

  # ---- Slider / input handlers ----

  def _on_slider(self, idx, val):
    if self._updating:
      return
    self._updating = True
    self._slider_inputs[idx].setText(str(val))
    if idx != self._fixed_axis:
      self._fixed_axis = idx
      for i, r in enumerate(self._slider_radios):
        r.setChecked(i == idx)
    m = _MODELS[self._model]
    vals = [self._sliders[i].value() for i in range(3)]
    r, g, b = m['to_rgb'](*[float(v) for v in vals])
    c, ok = _clamp_rgb(r, g, b)
    self._color = c
    self._hex_input.setText(self._hex_str(c))
    # Show actual model values after possible gamut clamping
    actual = m['from_rgb'](c.red(), c.green(), c.blue())
    for i in range(3):
      self._val_fields[i].setText(str(int(round(actual[i]))))
    self._swatch.setStyleSheet("background-color: %s;" % c.name())
    self._update_name_label(c)
    self._update_gamut_indicator(ok)
    self._plane.set_model(self._model, self._fixed_axis, vals[self._fixed_axis])
    self._plane.set_marker_from_color(c)
    self._updating = False
    self.colorChanged.emit(self._color)

  def _on_slider_input(self, idx):
    try:
      val = int(self._slider_inputs[idx].text())
      val = max(self._sliders[idx].minimum(),
                min(self._sliders[idx].maximum(), val))
      self._sliders[idx].setValue(val)
    except ValueError:
      pass

  def _hex_str(self, color):
    s = color.name()
    return s.upper() if self._hex_upper else s

  def _on_hex_upper_toggle(self, checked):
    self._hex_upper = checked
    if state.ui_state:
      state.ui_state.hex_uppercase = checked
    self._hex_input.setText(self._hex_str(self._color))

  def _on_hex_input(self):
    txt = self._hex_input.text().strip()
    if not txt.startswith('#'):
      txt = '#' + txt
    c = QColor(txt)
    if c.isValid():
      self.set_color(c, add_history=True)

  def _on_val_input(self):
    """Handle editing of the model value fields."""
    m = _MODELS[self._model]
    try:
      vals = [int(self._val_fields[i].text()) for i in range(3)]
    except ValueError:
      return
    for i in range(3):
      lo, hi = m['ranges'][i]
      vals[i] = max(lo, min(hi, vals[i]))
    r, g, b = m['to_rgb'](*[float(v) for v in vals])
    c, _ = _clamp_rgb(r, g, b)
    self.set_color(c, add_history=True)

  # ---- Plane interaction ----

  def _on_plane_start(self):
    self._add_to_history(self._color)

  def _on_plane_pick(self, color):
    if self._updating:
      return
    self._color = color
    self._updating = True
    m = _MODELS[self._model]
    vals = m['from_rgb'](color.red(), color.green(), color.blue())
    for i in range(3):
      if i != self._fixed_axis:
        self._sliders[i].setValue(int(round(vals[i])))
        self._slider_inputs[i].setText(str(int(round(vals[i]))))
    for i in range(3):
      self._val_fields[i].setText(str(int(round(vals[i]))))
    self._hex_input.setText(self._hex_str(color))
    self._swatch.setStyleSheet("background-color: %s;" % color.name())
    self._update_name_label(color)
    r2, g2, b2 = m['to_rgb'](*vals)
    _, ok = _clamp_rgb(r2, g2, b2)
    self._update_gamut_indicator(ok)
    self._updating = False
    self.colorChanged.emit(self._color)

  # ---- Public API ----

  def set_color(self, color, add_history=False):
    if add_history:
      self._add_to_history(self._color)
    self._color = QColor(color)
    self._sync_ui_from_color()
    self.colorChanged.emit(self._color)

  @property
  def color(self):
    return QColor(self._color)

  # ---- History ----

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

  # ---- Saved colors ----

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

  # ---- Screen eyedropper ----

  def _start_eyedropper(self):
    self._eyedrop_overlay = _EyedropperOverlay(self._on_eyedrop_pick)
    self._eyedrop_overlay.show()

  def _on_eyedrop_pick(self, color):
    if color and color.isValid():
      self.set_color(color, add_history=True)


class _EyedropperOverlay(QWidget):
  """Full-screen transparent overlay that samples a pixel on click."""

  def __init__(self, callback, parent=None):
    super().__init__(parent, Qt.WindowType.FramelessWindowHint
                     | Qt.WindowType.WindowStaysOnTopHint)
    self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    self._callback = callback
    self._zoom_size = 9  # pixels captured around cursor
    self._zoom_scale = 12  # magnification per pixel
    self.setMouseTracking(True)
    # Cover the full virtual desktop
    geo = QApplication.primaryScreen().virtualGeometry()
    self.setGeometry(geo)
    self.showFullScreen()
    # Custom high-visibility crosshair cursor
    sz = 32
    mid = sz // 2
    pm = QPixmap(sz, sz)
    pm.fill(Qt.GlobalColor.transparent)
    cp = QPainter(pm)
    cp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    # Black outer layer (visible on bright backgrounds)
    cp.setPen(QPen(Qt.black, 3))
    cp.drawLine(mid, 0, mid, mid - 4)
    cp.drawLine(mid, mid + 4, mid, sz - 1)
    cp.drawLine(0, mid, mid - 4, mid)
    cp.drawLine(mid + 4, mid, sz - 1, mid)
    # Red inner layer (visible on dark backgrounds)
    cp.setPen(QPen(QColor(255, 0, 0), 1))
    cp.drawLine(mid, 1, mid, mid - 4)
    cp.drawLine(mid, mid + 4, mid, sz - 2)
    cp.drawLine(1, mid, mid - 4, mid)
    cp.drawLine(mid + 4, mid, sz - 2, mid)
    cp.end()
    self.setCursor(QCursor(pm, mid, mid))

  def paintEvent(self, event):
    # Semi-transparent dark overlay
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(0, 0, 0, 1))
    # Magnifier loupe at cursor position
    pos = QCursor.pos()
    local = self.mapFromGlobal(pos)
    self._draw_loupe(p, pos, local)
    p.end()

  def _draw_loupe(self, painter, screen_pos, widget_pos):
    """Draw a magnified preview of pixels around the cursor."""
    n = self._zoom_size
    s = self._zoom_scale
    half = n // 2
    screen = QApplication.screenAt(screen_pos)
    if not screen:
      return
    px = screen.grabWindow(0,
                           screen_pos.x() - half, screen_pos.y() - half,
                           n, n)
    if px.isNull():
      return
    loupe_size = n * s
    lx = widget_pos.x() + 20
    ly = widget_pos.y() + 20
    # Keep loupe on-screen
    if lx + loupe_size + 4 > self.width():
      lx = widget_pos.x() - loupe_size - 20
    if ly + loupe_size + 4 > self.height():
      ly = widget_pos.y() - loupe_size - 20
    dest = QRect(lx, ly, loupe_size, loupe_size)
    painter.drawPixmap(dest, px)
    painter.setPen(QPen(QColor(255, 255, 255), 1))
    painter.drawRect(dest)
    # Crosshair on center pixel
    cx = lx + half * s
    cy = ly + half * s
    painter.setPen(QPen(QColor(255, 0, 0), 1))
    painter.drawRect(cx, cy, s, s)

  def mouseMoveEvent(self, event):
    self.update()

  def mousePressEvent(self, event):
    if event.button() == Qt.MouseButton.LeftButton:
      pos = QCursor.pos()
      screen = QApplication.screenAt(pos)
      if screen:
        px = screen.grabWindow(0, pos.x(), pos.y(), 1, 1)
        img = px.toImage()
        color = QColor(img.pixel(0, 0))
        self._callback(color)
    self.close()

  def keyPressEvent(self, event):
    if event.key() == Qt.Key.Key_Escape:
      self.close()


def show_color_picker(parent=None):
  """Show the color picker as a standalone tool window."""
  mw = parent or (state.app.mainwin if state.app else None)
  dlg = QDialog(mw)
  dlg.setWindowTitle("Color Picker")
  layout = QVBoxLayout(dlg)
  picker = ColorPickerWidget(parent=dlg)
  layout.addWidget(picker)
  dlg.setModal(False)
  dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
  dlg.resize(dlg.sizeHint())
  dlg.show()
  # Keep reference so it isn't garbage collected
  if mw:
    mw._color_picker_dlg = dlg


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
