# dcc_ui.py - DCC UI components: transfers window, accept dialog, chat window

import os
from functools import partial

from PySide6.QtWidgets import (
  QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget,
  QTreeWidgetItem, QHeaderView, QFileDialog, QMessageBox, QLabel,
  QAbstractItemView,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

import state
from dcc import Direction, Status


# ---------------------------------------------------------------------------
# DCC Transfers Window
# ---------------------------------------------------------------------------

class DCCTransfersWindow(QDialog):
  """Non-modal dialog showing all DCC transfers."""

  _instance = None

  @classmethod
  def show_instance(cls):
    if cls._instance and not cls._instance.isVisible():
      cls._instance = None
    if not cls._instance:
      cls._instance = cls(getattr(state.app, 'mainwin', None))
    cls._instance.show()
    cls._instance.raise_()
    cls._instance.activateWindow()
    return cls._instance

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setWindowTitle('DCC Transfers')
    self.setMinimumSize(700, 300)
    self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)

    layout = QVBoxLayout(self)

    self._tree = QTreeWidget()
    self._tree.setHeaderLabels([
      'ID', 'Dir', 'Nick', 'Filename', 'Size', 'Transferred',
      'Progress', 'Speed', 'Status'
    ])
    self._tree.setRootIsDecorated(False)
    self._tree.setAlternatingRowColors(True)
    self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    header = self._tree.header()
    header.setStretchLastSection(True)
    header.resizeSection(0, 40)   # ID
    header.resizeSection(1, 50)   # Dir
    header.resizeSection(2, 100)  # Nick
    header.resizeSection(3, 200)  # Filename
    header.resizeSection(4, 80)   # Size
    header.resizeSection(5, 80)   # Transferred
    header.resizeSection(6, 70)   # Progress
    header.resizeSection(7, 80)   # Speed
    layout.addWidget(self._tree)

    # Buttons
    btn_row = QHBoxLayout()
    self._btn_accept = QPushButton('Accept')
    self._btn_accept.clicked.connect(self._on_accept)
    btn_row.addWidget(self._btn_accept)
    self._btn_cancel = QPushButton('Cancel')
    self._btn_cancel.clicked.connect(self._on_cancel)
    btn_row.addWidget(self._btn_cancel)
    self._btn_clear = QPushButton('Clear Completed')
    self._btn_clear.clicked.connect(self._on_clear)
    btn_row.addWidget(self._btn_clear)
    self._btn_open = QPushButton('Open Folder')
    self._btn_open.clicked.connect(self._on_open_folder)
    btn_row.addWidget(self._btn_open)
    btn_row.addStretch()
    layout.addLayout(btn_row)

    # Refresh timer
    self._timer = QTimer(self)
    self._timer.timeout.connect(self._refresh)
    self._timer.start(500)
    self._refresh()

  def _refresh(self):
    mgr = state.dcc_manager
    if not mgr:
      return
    # Build a set of existing IDs
    existing = {}
    for i in range(self._tree.topLevelItemCount()):
      item = self._tree.topLevelItem(i)
      existing[int(item.text(0))] = item

    for xfer in mgr.transfers.values():
      if xfer.id in existing:
        item = existing.pop(xfer.id)
      else:
        item = QTreeWidgetItem()
        item.setText(0, str(xfer.id))
        self._tree.addTopLevelItem(item)
      item.setText(1, 'Send' if xfer.direction == Direction.SEND else 'Recv')
      item.setText(2, xfer.nick)
      item.setText(3, xfer.filename)
      item.setText(4, _format_size(xfer.filesize))
      item.setText(5, _format_size(xfer.transferred))
      item.setText(6, '%d%%' % int(xfer.progress * 100))
      item.setText(7, _format_speed(xfer.speed) if xfer.status == Status.ACTIVE else '')
      item.setText(8, xfer.status.value)

    # Remove stale items
    for xid, item in existing.items():
      idx = self._tree.indexOfTopLevelItem(item)
      if idx >= 0:
        self._tree.takeTopLevelItem(idx)

  def _selected_id(self):
    item = self._tree.currentItem()
    if item:
      return int(item.text(0))
    return None

  def _on_accept(self):
    xid = self._selected_id()
    if xid and state.dcc_manager:
      xfer = state.dcc_manager.transfers.get(xid)
      if xfer and xfer.status == Status.PENDING and xfer.direction == Direction.RECEIVE:
        import asyncio
        asyncio.ensure_future(state.dcc_manager.accept_receive(xfer))

  def _on_cancel(self):
    xid = self._selected_id()
    if xid and state.dcc_manager:
      state.dcc_manager.cancel(xid)

  def _on_clear(self):
    if not state.dcc_manager:
      return
    done = [xid for xid, x in state.dcc_manager.transfers.items()
            if x.status in (Status.COMPLETE, Status.FAILED, Status.CANCELLED)]
    for xid in done:
      del state.dcc_manager.transfers[xid]
    self._refresh()

  def _on_open_folder(self):
    xid = self._selected_id()
    if xid and state.dcc_manager:
      xfer = state.dcc_manager.transfers.get(xid)
      if xfer and xfer.file_path:
        folder = os.path.dirname(xfer.file_path)
        if os.path.isdir(folder):
          from PySide6.QtGui import QDesktopServices
          from PySide6.QtCore import QUrl
          QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


# ---------------------------------------------------------------------------
# DCC Accept Dialog
# ---------------------------------------------------------------------------

def show_accept_dialog_nonblocking(xfer, mgr):
  """Show a non-blocking dialog asking the user to accept/reject a DCC SEND."""
  import asyncio
  from PySide6.QtWidgets import QApplication
  from dcc import Status
  parent = QApplication.activeWindow()
  msg = '%s wants to send you:\n\n%s (%s)\n\nFrom: %s:%s' % (
    xfer.nick, xfer.filename, _format_size(xfer.filesize),
    xfer.host, xfer.port)
  if state.config.dcc_max_filesize and xfer.filesize > state.config.dcc_max_filesize * 1024 * 1024:
    msg += '\n\nWARNING: File exceeds maximum size limit (%d MB)' % state.config.dcc_max_filesize
  box = QMessageBox(parent)
  box.setWindowTitle('DCC SEND from %s' % xfer.nick)
  box.setText(msg)
  box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
  def _on_finished(result):
    if result == QMessageBox.StandardButton.Yes:
      asyncio.ensure_future(mgr.accept_receive(xfer))
    else:
      xfer.status = Status.CANCELLED
      if xfer.client and xfer.client.window:
        xfer.client.window.addline('[DCC SEND from %s rejected]' % xfer.nick)
  box.finished.connect(_on_finished)
  box.setModal(False)
  box.show()


async def show_exists_dialog_async(filename, existing_size, new_size):
  """Show a non-blocking dialog asking what to do when a file already exists.
  Returns 'resume', 'overwrite', 'rename', or 'cancel'."""
  import asyncio
  from PySide6.QtWidgets import QApplication
  parent = QApplication.activeWindow()
  msg = '"%s" already exists.\n\nExisting: %s\nIncoming: %s' % (
    filename, _format_size(existing_size), _format_size(new_size))
  if existing_size < new_size:
    msg += '\n\nThe existing file is smaller — it may be a partial download.'
  future = asyncio.get_event_loop().create_future()
  box = QMessageBox(parent)
  box.setWindowTitle('File Exists')
  box.setText(msg)
  btn_resume = box.addButton('Resume', QMessageBox.ButtonRole.AcceptRole)
  btn_overwrite = box.addButton('Overwrite', QMessageBox.ButtonRole.DestructiveRole)
  btn_rename = box.addButton('Save As New', QMessageBox.ButtonRole.ActionRole)
  btn_cancel = box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
  if existing_size >= new_size:
    btn_resume.setEnabled(False)
  def _on_finished(_result):
    clicked = box.clickedButton()
    if clicked is btn_resume:
      future.set_result('resume')
    elif clicked is btn_overwrite:
      future.set_result('overwrite')
    elif clicked is btn_rename:
      future.set_result('rename')
    else:
      future.set_result('cancel')
  box.finished.connect(_on_finished)
  box.setModal(False)
  box.show()
  return await future


# ---------------------------------------------------------------------------
# DCC Chat Window
# ---------------------------------------------------------------------------

class DCCChatWindow:
  """Simple DCC chat window using the Window base class."""

  def __init__(self, chat):
    from window import Window
    self._chat = chat
    # Create a basic Window with the chat's client
    self._window = Window(chat.client)
    self._window.type = 'dcc_chat'
    self._window.setWindowTitle('DCC Chat: %s' % chat.nick)

    # Override lineinput to send via DCC instead of IRC
    orig_lineinput = self._window.lineinput
    def dcc_lineinput(text):
      self._window.input.setText('')
      if text.strip():
        if not self._window.inputhistory or self._window.inputhistory[-1] != text:
          self._window.inputhistory.append(text)
        import asyncio
        asyncio.ensure_future(chat.send_line(text))
        conn = chat.client.conn
        nick = conn.nickname if conn else 'me'
        self._window.addline_msg(nick, text)
    self._window.lineinput = dcc_lineinput

  def addline(self, text):
    if hasattr(self._window, 'addline'):
      self._window.addline(text)

  def addline_msg(self, nick, text):
    if hasattr(self._window, 'addline_msg'):
      self._window.addline_msg(nick, text)

  def redmessage(self, text):
    if hasattr(self._window, 'redmessage'):
      self._window.redmessage(text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_size(size):
  if not size:
    return '0 B'
  for unit in ('B', 'KB', 'MB', 'GB'):
    if abs(size) < 1024:
      if unit == 'B':
        return '%d %s' % (size, unit)
      return '%.1f %s' % (size, unit)
    size /= 1024.0
  return '%.1f TB' % size


def _format_speed(bps):
  if bps <= 0:
    return ''
  return '%s/s' % _format_size(bps)
