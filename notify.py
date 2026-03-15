# notify.py - Notification manager and /notify nick watch list

import os
import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from PySide6.QtCore import QTimer, QUrl
try:
  from PySide6.QtMultimedia import QSoundEffect
except ImportError:
  QSoundEffect = None

import state


# ---------------------------------------------------------------------------
# System sound discovery
# ---------------------------------------------------------------------------

_SOUND_EXTS = ('.wav', '.aiff', '.aif', '.ogg', '.mp3')

def _system_sound_dirs():
  """Return a list of directories containing system notification sounds."""
  dirs = []
  if sys.platform == 'win32':
    media = os.path.join(os.environ.get('SYSTEMROOT', r'C:\Windows'), 'Media')
    if os.path.isdir(media):
      dirs.append(media)
  elif sys.platform == 'darwin':
    for d in ('/System/Library/Sounds', os.path.expanduser('~/Library/Sounds')):
      if os.path.isdir(d):
        dirs.append(d)
  else:
    # Linux / freedesktop
    for d in ('/usr/share/sounds', '/usr/share/sounds/freedesktop/stereo',
              '/usr/share/sounds/gnome/default/alerts',
              '/usr/share/sounds/ubuntu/stereo'):
      if os.path.isdir(d):
        dirs.append(d)
  return dirs

def get_system_sounds():
  """Return a sorted list of (name, path) tuples for available system sounds."""
  sounds = {}
  for d in _system_sound_dirs():
    for fname in os.listdir(d):
      base, ext = os.path.splitext(fname)
      if ext.lower() in _SOUND_EXTS:
        path = os.path.join(d, fname)
        if base not in sounds:
          sounds[base] = path
  return sorted(sounds.items(), key=lambda x: x[0].lower())

def resolve_sound_name(name):
  """Resolve a sound name to a file path.

  If *name* is already a file path (absolute or relative that exists), return it.
  Otherwise look up the name in system sound directories.
  Returns the path, or None if not found.
  """
  if not name or name in ('none', 'beep', 'default'):
    return None
  # Already a path?
  if os.path.isabs(name):
    return name if os.path.isfile(name) else None
  # Relative path that exists?
  base = os.path.dirname(os.path.abspath(__file__))
  rel = os.path.join(base, name)
  if os.path.isfile(rel):
    return rel
  # Look up by name in system sounds
  for d in _system_sound_dirs():
    for ext in _SOUND_EXTS:
      path = os.path.join(d, name + ext)
      if os.path.isfile(path):
        return path
  return None


def show_sound_browser(select=False):
  """Show a dialog listing all system sounds with preview buttons.

  If *select* is True, run as a modal dialog with a Select button and
  return the chosen sound name (or None if cancelled).  Otherwise show
  a non-modal browser.
  """
  from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
                                  QListWidgetItem, QPushButton, QLabel, QLineEdit)
  from PySide6.QtCore import Qt

  sounds = get_system_sounds()

  dlg = QDialog(state.app.mainwin)
  dlg.setWindowTitle("System Sounds (%d)" % len(sounds))
  dlg.resize(420, 500)
  layout = QVBoxLayout(dlg)

  # Search filter
  filter_row = QHBoxLayout()
  filter_row.addWidget(QLabel("Filter:"))
  filter_edit = QLineEdit()
  filter_edit.setPlaceholderText("type to filter...")
  filter_row.addWidget(filter_edit, 1)
  layout.addLayout(filter_row)

  # Sound list
  sound_list = QListWidget()
  for name, path in sounds:
    item = QListWidgetItem(name)
    item.setData(Qt.ItemDataRole.UserRole, path)
    item.setToolTip(path)
    sound_list.addItem(item)
  layout.addWidget(sound_list, 1)

  # Filter handler
  def _filter(text):
    text = text.lower()
    for i in range(sound_list.count()):
      item = sound_list.item(i)
      item.setHidden(text not in item.text().lower())
  filter_edit.textChanged.connect(_filter)

  # Buttons
  btn_row = QHBoxLayout()
  play_btn = QPushButton("Play")
  copy_btn = QPushButton("Copy Name")
  btn_row.addWidget(play_btn)
  btn_row.addWidget(copy_btn)
  if select:
    select_btn = QPushButton("Select")
    btn_row.addWidget(select_btn)
  btn_row.addStretch(1)
  layout.addLayout(btn_row)

  # Preview effect (shared, not cached)
  _preview = [None]

  def _play():
    item = sound_list.currentItem()
    if not item:
      return
    path = item.data(Qt.ItemDataRole.UserRole)
    if not QSoundEffect:
      QApplication.beep()
      return
    if _preview[0]:
      _preview[0].stop()
    effect = QSoundEffect()
    effect.setSource(QUrl.fromLocalFile(path))
    effect.play()
    _preview[0] = effect

  def _copy():
    item = sound_list.currentItem()
    if item:
      QApplication.clipboard().setText(item.text())

  play_btn.clicked.connect(_play)
  copy_btn.clicked.connect(_copy)
  sound_list.doubleClicked.connect(lambda _: _play())

  # Source directories info
  dirs = _system_sound_dirs()
  if dirs:
    info = QLabel("Source: " + ", ".join(dirs))
    info.setWordWrap(True)
    info.setStyleSheet("color: gray; font-size: 11px;")
    layout.addWidget(info)

  if select:
    _result = [None]
    def _select():
      item = sound_list.currentItem()
      if item:
        _result[0] = item.text()
        dlg.accept()
    select_btn.clicked.connect(_select)
    sound_list.doubleClicked.connect(lambda _: _select())
    dlg.exec()
    return _result[0]
  else:
    dlg.setModal(False)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    # Keep a reference so it's not garbage collected
    state.app.mainwin._sound_browser = dlg


class NotificationManager:
  """Handles sound and desktop notifications for IRC events,
  and manages the /notify nick watch list with ISON polling."""

  # Event types -> config attribute names (tuples of (sound, desktop))
  _EVENT_ATTRS = {
    'notice':         'notif_notice',
    'new_query':      'notif_new_query',
    'highlight':      'notif_highlight',
    'connect':        'notif_connect',
    'disconnect':     'notif_disconnect',
    'notify_online':  'notif_notify_online',
    'notify_offline': 'notif_notify_offline',
  }

  def __init__(self):
    # Per-network notify state: {network_key: {nick_lower: True/False/None}}
    # True = online, False = offline, None = unknown (initial)
    self._notify_state = {}
    self._ison_timer = QTimer()
    self._ison_timer.timeout.connect(self._poll_ison)
    self._sound_cache = {}  # path -> QSoundEffect

  def start_polling(self):
    """Start the ISON polling timer."""
    interval = max(state.config.notify_check_interval, 10) * 1000
    self._ison_timer.start(interval)

  def stop_polling(self):
    self._ison_timer.stop()

  def _play_sound(self, sound):
    """Play a sound.  *sound* is 'none', 'default', 'beep', a name, or a path."""
    if sound == 'none':
      return
    # Resolve 'default' to the global default sound setting
    if sound == 'default':
      sound = state.config.notif_default_sound
    if sound == 'none':
      return
    if sound == 'beep' or sound is True:
      QApplication.beep()
      return
    # Track in recent sounds
    if state.ui_state:
      state.ui_state.add_recent_sound(sound)
    # Resolve name or path
    if not QSoundEffect:
      QApplication.beep()
      return
    path = resolve_sound_name(sound)
    if not path:
      QApplication.beep()
      return
    effect = self._sound_cache.get(path)
    if not effect:
      effect = QSoundEffect()
      effect.setSource(QUrl.fromLocalFile(path))
      self._sound_cache[path] = effect
    effect.play()

  def fire(self, event_type, title, message):
    """Fire a notification (sound and/or desktop) for the given event type."""
    attr = self._EVENT_ATTRS.get(event_type)
    if not attr:
      return
    sound, desktop = getattr(state.config, attr, ('none', False))
    self._play_sound(sound)
    if desktop and state.tray_icon:
      state.tray_icon.showMessage(title, message,
                                  QSystemTrayIcon.MessageIcon.Information, 5000)

  # --- /notify watch list ---

  def get_state(self, network_key):
    """Return {nick_lower: online_bool_or_None} for a network."""
    return dict(self._notify_state.get(network_key, {}))

  def sync_list(self, network_key, conn=None):
    """Sync internal state with the config notify list for a network.
    Adds new nicks as unknown, removes nicks no longer in the list.
    If *conn* supports MONITOR, sends MONITOR +/- to keep the server in sync."""
    from config import get_notify_list
    nicks = {n.lower() for n in get_notify_list(network_key)}
    current = self._notify_state.setdefault(network_key, {})
    # Find added/removed
    removed = [n for n in current if n not in nicks]
    added = [n for n in nicks if n not in current]
    # Remove nicks no longer in list
    for n in removed:
      del current[n]
    # Add new nicks as unknown
    for n in added:
      current[n] = None
    # Update MONITOR on the server if supported
    if conn and getattr(conn, '_monitor_supported', False):
      if removed:
        conn._send_raw('MONITOR - %s' % ','.join(removed))
      if added:
        conn._send_raw('MONITOR + %s' % ','.join(added))

  def sync_monitor(self, conn):
    """Send the full MONITOR + list on connect (for servers that support it)."""
    nk = conn.client.network_key
    self.sync_list(nk, conn)
    nicks = self._notify_state.get(nk, {})
    if nicks:
      # Send in chunks respecting ~500 byte line limit
      parts = list(nicks.keys())
      chunk = []
      length = 10  # "MONITOR + "
      for n in parts:
        if length + len(n) + 1 > 500 and chunk:
          conn._send_raw('MONITOR + %s' % ','.join(chunk))
          chunk = []
          length = 10
        chunk.append(n)
        length += len(n) + 1
      if chunk:
        conn._send_raw('MONITOR + %s' % ','.join(chunk))

  def _poll_ison(self):
    """Send ISON for each connected network's notify list.
    Skips networks where MONITOR is active (server pushes updates)."""
    if not state.clients:
      return
    for client in state.clients:
      conn = client.conn
      if not conn or not client.hostname:
        continue
      # Skip ISON polling if server supports MONITOR
      if getattr(conn, '_monitor_supported', False):
        continue
      nk = client.network_key
      self.sync_list(nk)
      nicks = self._notify_state.get(nk, {})
      if not nicks:
        continue
      # ISON takes space-separated nicks, up to ~500 chars
      line = 'ISON ' + ' '.join(nicks.keys())
      if len(line.encode('utf-8')) > 500:
        parts = list(nicks.keys())
        chunk = []
        length = 5  # "ISON "
        for n in parts:
          if length + len(n) + 1 > 500 and chunk:
            conn._send_raw('ISON ' + ' '.join(chunk))
            chunk = []
            length = 5
          chunk.append(n)
          length += len(n) + 1
        if chunk:
          conn._send_raw('ISON ' + ' '.join(chunk))
      else:
        conn._send_raw(line)

  # --- MONITOR handlers ---

  def handle_monitor_online(self, conn, nicks):
    """Handle RPL_MONONLINE (730). Server reports monitored nicks are online."""
    nk = conn.client.network_key
    current = self._notify_state.get(nk, {})
    for nick in nicks:
      nick_lower = nick.lower()
      if nick_lower not in current:
        continue
      was_online = current[nick_lower]
      if was_online is None:
        current[nick_lower] = True
        self.notify_already_online(conn, nick_lower)
      elif not was_online:
        current[nick_lower] = True
        self.notify_signon(conn, nick_lower)

  def handle_monitor_offline(self, conn, nicks):
    """Handle RPL_MONOFFLINE (731). Server reports monitored nicks are offline."""
    nk = conn.client.network_key
    current = self._notify_state.get(nk, {})
    for nick in nicks:
      nick_lower = nick.lower()
      if nick_lower not in current:
        continue
      was_online = current[nick_lower]
      if was_online is None:
        current[nick_lower] = False
      elif was_online:
        current[nick_lower] = False
        self.notify_signoff(conn, nick_lower)

  def notify_signon(self, conn, nick):
    """Called when a /notify nick is detected as online.

    Fires config-based notifications, /on hooks, and prints to the server window.
    Called from ISON polling and (future) server-side MONITOR/WATCH."""
    from exec_system import _dispatch_on_hooks
    nk = conn.client.network_key
    self.fire('notify_online', 'Signon',
              '%s is now online (%s)' % (nick, conn.client.network or nk))
    _dispatch_on_hooks('notify_online', conn, (nick,))
    if conn.client.window:
      conn.client.window.addline(
        '[Notify] %s is now online' % nick, state.infoformat)

  def notify_signoff(self, conn, nick):
    """Called when a /notify nick is detected as offline.

    Fires config-based notifications, /on hooks, and prints to the server window.
    Called from ISON polling and (future) server-side MONITOR/WATCH."""
    from exec_system import _dispatch_on_hooks
    nk = conn.client.network_key
    self.fire('notify_offline', 'Signoff',
              '%s is now offline (%s)' % (nick, conn.client.network or nk))
    _dispatch_on_hooks('notify_offline', conn, (nick,))
    if conn.client.window:
      conn.client.window.addline(
        '[Notify] %s is now offline' % nick, state.infoformat)

  def notify_already_online(self, conn, nick):
    """Called when a newly added /notify nick is already online."""
    if conn.client.window:
      conn.client.window.addline(
        '[Notify] %s is already online' % nick, state.infoformat)

  def handle_ison_reply(self, conn, online_nicks):
    """Process an ISON reply. Compare with previous state, fire notifications."""
    nk = conn.client.network_key
    current = self._notify_state.get(nk, {})
    if not current:
      return
    online_lower = {n.lower() for n in online_nicks}
    for nick_lower, was_online in list(current.items()):
      is_online = nick_lower in online_lower
      if was_online is None:
        current[nick_lower] = is_online
        if is_online:
          self.notify_already_online(conn, nick_lower)
      elif is_online and not was_online:
        current[nick_lower] = True
        self.notify_signon(conn, nick_lower)
      elif not is_online and was_online:
        current[nick_lower] = False
        self.notify_signoff(conn, nick_lower)
