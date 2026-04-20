# rotate.py — String rotation cipher plugin for qtpyrc
#
# Rotates the alphanumeric characters of a string while preserving
# special character positions and the original case pattern.
#
# Commands:
#   /secret <text>                — show all rotations in a popup, pick one to send
#   /secret -n <max> <text>      — limit to max N rotations
#   /secret -o <offset> <text>   — rotate by specific offset, send immediately
#   /secret -r <min> <max> <text> — show rotations for offsets min through max

import plugin
from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QLabel
from PySide6.QtCore import Qt


def _rotate(text, offset):
  """Rotate alphanumeric characters by offset, preserving specials and case."""
  specials = []
  nonspecials = []
  for i, ch in enumerate(text):
    if ch.isalnum():
      nonspecials.append(ch)
    else:
      specials.append((i, ch))

  if not nonspecials:
    return text

  # Rotate
  o = offset % len(nonspecials) if nonspecials else 0
  rotated = list(nonspecials[-o:] + nonspecials[:-o]) if o else list(nonspecials)

  # Reinsert specials at original positions
  for i, ch in specials:
    rotated.insert(i, ch)

  # Restore original case pattern
  output = []
  for i, ch in enumerate(text):
    r = rotated[i] if i < len(rotated) else ch
    if ch.isupper():
      output.append(r.upper())
    elif ch.islower():
      output.append(r.lower())
    else:
      output.append(r)

  return ''.join(output)


def _get_offset_range(nonspecial_count):
  """Return the default (min_offset, max_offset) range like rotate.py."""
  half = min(11, nonspecial_count // 2)
  return -half, half + nonspecial_count % 2 - 1


class RotateDialog(QDialog):
  """Popup showing rotation options for the user to pick."""

  def __init__(self, text, rotations, parent=None):
    super().__init__(parent)
    self.setWindowTitle('Rotate: pick a rotation to send')
    self.setMinimumWidth(400)
    self.result_text = None

    layout = QVBoxLayout(self)
    layout.addWidget(QLabel('Original: %s' % text))

    self._list = QListWidget()
    self._list.setSpacing(0)
    self._list.setUniformItemSizes(True)
    # Use monospace font for alignment
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QStyledItemDelegate
    mono = QFont('Consolas', 9)
    mono.setStyleHint(QFont.StyleHint.Monospace)
    self._list.setFont(mono)
    # Compact row height via custom delegate
    class _CompactDelegate(QStyledItemDelegate):
      def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        fm = option.fontMetrics
        sh.setHeight(fm.height() + 2)
        return sh
    self._list.setItemDelegate(_CompactDelegate(self._list))
    # Pad offset labels to equal width
    max_label = max(len('%+d' % o) for o, _ in rotations)
    for offset, rotated in rotations:
      label = ('%+d' % offset).rjust(max_label)
      self._list.addItem('[%s] %s' % (label, rotated))
    self._list.itemDoubleClicked.connect(self._on_pick)
    layout.addWidget(self._list, 1)

    layout.addWidget(QLabel('Double-click or Enter to send, Escape to cancel'))
    self._rotations = rotations

  def _on_pick(self, item):
    idx = self._list.row(item)
    if 0 <= idx < len(self._rotations):
      self.result_text = self._rotations[idx][1]
    self.accept()

  def keyPressEvent(self, event):
    if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
      idx = self._list.currentRow()
      if 0 <= idx < len(self._rotations):
        self.result_text = self._rotations[idx][1]
      self.accept()
      return
    super().keyPressEvent(event)


class Rotate(plugin.Callbacks):
  pass


def _cmd_secret(window, text):
  """Handle /secret command."""
  from commands import _tokenize, docommand
  import state
  pfx = state.config.cmdprefix if state.config else '/'

  tokens = _tokenize(text)
  if not tokens:
    window.redmessage('[Usage: %ssecret [-n max | -o offset | -r min max] <text>]' % pfx)
    return

  mode = 'all'  # all, count, offset, range
  max_count = None
  specific_offset = None
  range_min = None
  range_max = None

  i = 0
  while i < len(tokens) and tokens[i].startswith('-'):
    flag = tokens[i]
    if flag == '-n' and i + 1 < len(tokens):
      mode = 'count'
      i += 1
      try:
        max_count = int(tokens[i])
      except ValueError:
        window.redmessage('[Invalid number: %s]' % tokens[i])
        return
    elif flag == '-o' and i + 1 < len(tokens):
      mode = 'offset'
      i += 1
      try:
        specific_offset = int(tokens[i])
      except ValueError:
        window.redmessage('[Invalid offset: %s]' % tokens[i])
        return
    elif flag == '-r' and i + 2 < len(tokens):
      mode = 'range'
      i += 1
      try:
        range_min = int(tokens[i])
        i += 1
        range_max = int(tokens[i])
      except ValueError:
        window.redmessage('[Invalid range]')
        return
    else:
      break
    i += 1

  phrase = ' '.join(tokens[i:])
  if not phrase:
    window.redmessage('[Usage: %ssecret [-n max | -o offset | -r min max] <text>]' % pfx)
    return

  # Count nonspecials for offset range calculation
  nonspecials = [ch for ch in phrase if ch.isalnum()]
  lennon = len(nonspecials)

  if lennon == 0:
    window.redmessage('[No alphanumeric characters to rotate]')
    return

  # Specific offset mode: rotate and send immediately
  if mode == 'offset':
    result = _rotate(phrase, specific_offset)
    docommand(window, 'say', result)
    return

  # Build rotation list
  default_min, default_max = _get_offset_range(lennon)

  if mode == 'range':
    off_min = range_min
    off_max = range_max
  else:
    off_min = default_min
    off_max = default_max

  rotations = []
  for o in range(off_min, off_max + 1):
    rotated = _rotate(phrase, o)
    rotations.append((o, rotated))

  if mode == 'count' and max_count is not None:
    # Trim to max_count, centered around offset 0
    if len(rotations) > max_count:
      center = len(rotations) // 2
      half = max_count // 2
      start = max(0, center - half)
      rotations = rotations[start:start + max_count]

  if not rotations:
    window.redmessage('[No rotations in that range]')
    return

  # Show popup
  from PySide6.QtWidgets import QApplication
  dlg = RotateDialog(phrase, rotations, QApplication.activeWindow())
  if dlg.exec() and dlg.result_text:
    docommand(window, 'say', dlg.result_text)


# Register as a slash command via the plugin
Class = Rotate


# Register /rotate command
def _register():
  from commands import Commands
  if not hasattr(Commands, 'secret'):
    Commands.secret = staticmethod(lambda window, text: _cmd_secret(window, text))

_register()
