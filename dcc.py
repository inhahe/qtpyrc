# dcc.py - DCC (Direct Client Connection) protocol engine
#
# Handles DCC SEND, GET, CHAT, RESUME/ACCEPT, and reverse (passive) DCC.
# Uses asyncio for all network I/O.

import asyncio
import os
import struct
import socket
import time as _time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import state


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLOCK_SIZE = 4096    # bytes per DCC SEND chunk
ACK_SIZE = 4         # 4-byte big-endian acknowledge


class Direction(Enum):
  SEND = 'send'
  RECEIVE = 'receive'


class Status(Enum):
  PENDING = 'pending'       # waiting for user accept
  CONNECTING = 'connecting'
  ACTIVE = 'active'
  COMPLETE = 'complete'
  FAILED = 'failed'
  CANCELLED = 'cancelled'


# ---------------------------------------------------------------------------
# IP helpers
# ---------------------------------------------------------------------------

def ip_to_long(ip_str):
  """Convert dotted-quad IP to 32-bit unsigned integer for DCC."""
  return struct.unpack('!I', socket.inet_aton(ip_str))[0]


def long_to_ip(ip_long):
  """Convert 32-bit unsigned integer back to dotted-quad IP."""
  return socket.inet_ntoa(struct.pack('!I', ip_long))


def _sanitize_filename(name):
  """Strip path components and dangerous characters from a filename."""
  name = os.path.basename(name)
  # Remove any remaining path separators and null bytes
  for ch in '/\\:\x00':
    name = name.replace(ch, '_')
  return name or 'unnamed'


# ---------------------------------------------------------------------------
# DCC Transfer
# ---------------------------------------------------------------------------

class DCCTransfer:
  _next_id = 1

  def __init__(self, client, nick, filename, filesize, direction,
               host=None, port=None, token=None, file_path=None):
    self.id = DCCTransfer._next_id
    DCCTransfer._next_id += 1
    self.client = client
    self.nick = nick
    self.filename = _sanitize_filename(filename)
    self.filesize = filesize
    self.transferred = 0
    self.direction = direction
    self.status = Status.PENDING
    self.host = host
    self.port = port
    self.token = token        # for reverse DCC
    self.resume_pos = 0
    self.file_path = file_path
    self.start_time = None
    self.speed = 0.0          # bytes/sec
    self._task = None         # asyncio task
    self._server = None       # asyncio server (for listening)
    self._writer = None       # for cleanup on cancel
    self._last_speed_time = 0
    self._last_speed_bytes = 0
    self.error = ''

  @property
  def progress(self):
    if self.filesize and self.filesize > 0:
      return self.transferred / self.filesize
    return 0.0

  @property
  def eta(self):
    if self.speed > 0 and self.filesize > 0:
      remaining = self.filesize - self.transferred
      return remaining / self.speed
    return 0

  def _update_speed(self):
    now = _time.monotonic()
    elapsed = now - self._last_speed_time
    if elapsed >= 1.0:
      delta = self.transferred - self._last_speed_bytes
      self.speed = delta / elapsed
      self._last_speed_time = now
      self._last_speed_bytes = self.transferred


# ---------------------------------------------------------------------------
# DCC Chat
# ---------------------------------------------------------------------------

class DCCChat:
  _next_id = 1

  def __init__(self, client, nick, host=None, port=None):
    self.id = DCCChat._next_id
    DCCChat._next_id += 1
    self.client = client
    self.nick = nick
    self.host = host
    self.port = port
    self.status = Status.PENDING
    self.window = None
    self._task = None
    self._reader = None
    self._writer = None
    self._server = None

  async def send_line(self, text):
    if self._writer and not self._writer.is_closing():
      self._writer.write((text + '\n').encode('utf-8', errors='replace'))
      await self._writer.drain()


# ---------------------------------------------------------------------------
# DCC Manager
# ---------------------------------------------------------------------------

class DCCManager:
  def __init__(self):
    self.transfers = {}   # id -> DCCTransfer
    self.chats = {}       # id -> DCCChat
    self._upnp_ports = [] # ports we've mapped via UPnP/NAT-PMP
    self._external_ip = None
    # Check NAT traversal availability once
    try:
      from upnp import check_availability
      check_availability()
    except Exception:
      pass

  # --- External IP ---

  async def get_external_ip(self, client=None):
    """Determine our external IP address."""
    if self._external_ip:
      return self._external_ip
    # Manual override from config
    if state.config.dcc_ip:
      self._external_ip = state.config.dcc_ip
      return self._external_ip
    # Try UPnP, then NAT-PMP
    try:
      from upnp import get_external_ip
      ip = await asyncio.to_thread(get_external_ip)
      if ip:
        self._external_ip = ip
        return ip
    except Exception:
      pass
    # Try to get IP from the IRC server (some servers report it in 001)
    if client and client.conn:
      own = client.users.get(client.conn.irclower(client.conn.nickname))
      if own and own.host and not own.host.startswith('gateway/'):
        # Check if it looks like an IP address
        try:
          socket.inet_aton(own.host)
          self._external_ip = own.host
          state.dbg(state.LOG_INFO, '[dcc] Using IP from IRC user host: %s' % own.host)
          return own.host
        except OSError:
          pass
    # Fallback: local machine's LAN IP (works for LAN/same-machine transfers)
    try:
      s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
      s.connect(('8.8.8.8', 80))
      ip = s.getsockname()[0]
      s.close()
      if ip and ip != '0.0.0.0':
        self._external_ip = ip
        state.dbg(state.LOG_INFO, '[dcc] Using local LAN IP: %s' % ip)
        return ip
    except Exception:
      pass
    # Final fallback: None (triggers reverse/passive DCC)
    return None

  # --- Port management ---

  async def _listen(self, port_min, port_max):
    """Find an available port in range and start listening. Returns (server, port)."""
    for port in range(port_min, port_max + 1):
      try:
        server = await asyncio.start_server(lambda r, w: None, '', port)
        return server, port
      except OSError:
        continue
    return None, None

  async def _setup_port(self, port, client=None):
    """Try to set up UPnP/NAT-PMP forwarding for the port."""
    if state.config.dcc_nat_traversal == 'disabled':
      return False
    try:
      from upnp import setup_port_forwarding
      await asyncio.to_thread(setup_port_forwarding, port)
      self._upnp_ports.append(port)
      return True
    except Exception:
      # Availability already logged at startup — just use passive DCC
      return False

  async def _teardown_port(self, port, client=None):
    """Remove UPnP/NAT-PMP forwarding."""
    if port in self._upnp_ports:
      try:
        from upnp import teardown_port_forwarding
        await asyncio.to_thread(teardown_port_forwarding, port)
        state.dbg(state.LOG_INFO, '[dcc] UPnP port mapping removed for port %d' % port)
      except Exception as e:
        state.dbg(state.LOG_INFO, '[dcc] Failed to remove UPnP port mapping %d: %s' % (port, e))
      self._upnp_ports.remove(port)

  # --- Initiate SEND ---

  async def initiate_send(self, client, nick, filepath):
    """Start sending a file to nick."""
    conn = client.conn
    if not conn:
      client.window.redmessage('[Not connected]')
      return None

    if not os.path.isfile(filepath):
      client.window.redmessage('[File not found: %s]' % filepath)
      return None

    filesize = os.path.getsize(filepath)
    filename = os.path.basename(filepath)

    xfer = DCCTransfer(client, nick, filename, filesize, Direction.SEND,
                       file_path=filepath)
    self.transfers[xfer.id] = xfer

    if state.config.dcc_passive:
      # Reverse/passive DCC: send with ip=0 port=0 and a token
      xfer.token = str(xfer.id)
      dcc_str = 'SEND %s 0 0 %d %s' % (
        _quote_filename(filename), filesize, xfer.token)
      conn.ctcpMakeQuery(nick, [('DCC', dcc_str)])
      xfer.status = Status.PENDING
      _notify_transfer(xfer, 'Waiting for %s to connect (passive DCC)' % nick)
    else:
      # Active DCC: listen on a port
      port_min = state.config.dcc_port_min
      port_max = state.config.dcc_port_max
      server, port = await self._listen(port_min, port_max)
      if not server:
        xfer.status = Status.FAILED
        xfer.error = 'No available port in range %d-%d' % (port_min, port_max)
        _notify_transfer(xfer, xfer.error)
        return xfer

      xfer._server = server
      xfer.port = port
      await self._setup_port(port, client)

      ext_ip = await self.get_external_ip(client)
      if not ext_ip:
        # Fall back to passive DCC
        server.close()
        xfer._server = None
        await self._teardown_port(port)
        xfer.token = str(xfer.id)
        dcc_str = 'SEND %s 0 0 %d %s' % (
          _quote_filename(filename), filesize, xfer.token)
        conn.ctcpMakeQuery(nick, [('DCC', dcc_str)])
        xfer.status = Status.PENDING
        _notify_transfer(xfer, 'Waiting for %s to connect (passive DCC)' % nick)
        return xfer

      ip_long = ip_to_long(ext_ip)
      dcc_str = 'SEND %s %d %d %d' % (
        _quote_filename(filename), ip_long, port, filesize)
      conn.ctcpMakeQuery(nick, [('DCC', dcc_str)])

      xfer.status = Status.CONNECTING
      _notify_transfer(xfer, 'Offering %s to %s' % (filename, nick))

      # Wait for connection with timeout
      xfer._task = asyncio.ensure_future(
        self._send_wait_connect(xfer))

    return xfer

  async def _send_wait_connect(self, xfer):
    """Wait for incoming connection on the listening server, then send the file."""
    try:
      connected = asyncio.Event()
      reader = writer = None

      def on_connect(r, w):
        nonlocal reader, writer
        reader, writer = r, w
        connected.set()

      # Replace the dummy callback
      xfer._server.close()
      await xfer._server.wait_closed()
      xfer._server = await asyncio.start_server(
        on_connect, '', xfer.port)

      try:
        await asyncio.wait_for(connected.wait(), timeout=state.config.dcc_timeout)
      except asyncio.TimeoutError:
        xfer._server.close()
        await self._teardown_port(xfer.port)
        # Retry as passive DCC
        _notify_transfer(xfer, 'Active DCC timed out, retrying as passive...')
        xfer.token = str(xfer.id)
        conn = xfer.client.conn
        if conn:
          dcc_str = 'SEND %s 0 0 %d %s' % (
            _quote_filename(xfer.filename), xfer.filesize, xfer.token)
          conn.ctcpMakeQuery(xfer.nick, [('DCC', dcc_str)])
          xfer.status = Status.PENDING
          _notify_transfer(xfer, 'Waiting for %s to connect (passive DCC fallback)' % xfer.nick)
        else:
          xfer.status = Status.FAILED
          xfer.error = 'Connection timed out and not connected to retry'
        return
      finally:
        if xfer._server:
          xfer._server.close()
          await self._teardown_port(xfer.port)

      await self._do_send(xfer, reader, writer)
    except asyncio.CancelledError:
      xfer.status = Status.CANCELLED
    except Exception as e:
      xfer.status = Status.FAILED
      xfer.error = str(e)
      _notify_transfer(xfer, 'DCC SEND to %s failed: %s' % (xfer.nick, e))

  async def _do_send(self, xfer, reader, writer):
    """Send file data over an established connection."""
    xfer._writer = writer
    xfer.status = Status.ACTIVE
    xfer.start_time = _time.monotonic()
    xfer._last_speed_time = xfer.start_time
    xfer._last_speed_bytes = 0
    _notify_transfer(xfer, 'Sending %s to %s%s' % (
      xfer.filename, xfer.nick,
      ' (resuming at %d)' % xfer.resume_pos if xfer.resume_pos else ''))

    try:
      with open(xfer.file_path, 'rb') as f:
        if xfer.resume_pos:
          f.seek(xfer.resume_pos)
          xfer.transferred = xfer.resume_pos

        while True:
          if writer.is_closing():
            raise ConnectionError('Remote side closed connection')
          data = await asyncio.to_thread(f.read, BLOCK_SIZE)
          if not data:
            break
          writer.write(data)
          await writer.drain()
          xfer.transferred += len(data)
          xfer._update_speed()

          # Read any pending ACKs (non-blocking drain)
          # Some clients require the sender to consume ACKs
          while True:
            try:
              ack_data = await asyncio.wait_for(reader.read(ACK_SIZE), timeout=0.01)
              if not ack_data:
                raise ConnectionError('Remote side closed connection')
            except asyncio.TimeoutError:
              break
            except (ConnectionError, OSError):
              raise

      # Wait for final ACK confirming full file received
      acked = xfer.resume_pos or 0
      try:
        deadline = _time.monotonic() + 30
        while acked < xfer.filesize and _time.monotonic() < deadline:
          remaining = deadline - _time.monotonic()
          ack_data = await asyncio.wait_for(reader.read(ACK_SIZE), timeout=max(remaining, 1))
          if not ack_data:
            break
          if len(ack_data) >= ACK_SIZE:
            acked = struct.unpack('!I', ack_data[:ACK_SIZE])[0]
      except (asyncio.TimeoutError, ConnectionError, OSError):
        pass  # some clients don't send final ACK

      xfer.status = Status.COMPLETE
      _notify_transfer(xfer, 'DCC SEND to %s complete: %s' % (xfer.nick, xfer.filename))
    except asyncio.CancelledError:
      xfer.status = Status.CANCELLED
    except Exception as e:
      xfer.status = Status.FAILED
      xfer.error = str(e)
      _notify_transfer(xfer, 'DCC SEND to %s failed: %s' % (xfer.nick, e))
    finally:
      try:
        writer.close()
        await writer.wait_closed()
      except Exception:
        pass

  # --- Accept incoming SEND (receive) ---

  async def accept_receive(self, xfer):
    """Accept an incoming DCC SEND offer and start downloading."""
    if xfer.status != Status.PENDING:
      return

    # Determine save path
    download_dir = state.config.dcc_download_dir
    if not download_dir:
      download_dir = os.path.expanduser('~')
    os.makedirs(download_dir, exist_ok=True)

    save_path = os.path.join(download_dir, xfer.filename)

    # Handle existing file based on on_exists setting
    if os.path.exists(save_path) and not xfer.resume_pos:
      existing_size = os.path.getsize(save_path)
      on_exists = state.config.dcc_on_exists

      if on_exists == 'resume' and 0 < existing_size < xfer.filesize:
        # Auto-resume
        xfer.file_path = save_path
        xfer.resume_pos = existing_size
        conn = xfer.client.conn
        if conn and xfer.port:
          dcc_str = 'RESUME %s %d %d' % (
            _quote_filename(xfer.filename), xfer.port, existing_size)
          conn.ctcpMakeQuery(xfer.nick, [('DCC', dcc_str)])
          _notify_transfer(xfer, 'Requesting resume at %d bytes for %s' % (
            existing_size, xfer.filename))
          return
      elif on_exists == 'overwrite':
        pass  # use save_path as-is, will overwrite
      elif on_exists == 'rename':
        base, ext = os.path.splitext(save_path)
        i = 1
        while os.path.exists('%s_%d%s' % (base, i, ext)):
          i += 1
        save_path = '%s_%d%s' % (base, i, ext)
      elif on_exists == 'ask':
        # Ask the user what to do — use a future to avoid blocking the event loop
        from dcc_ui import show_exists_dialog_async
        choice = await show_exists_dialog_async(xfer.filename, existing_size, xfer.filesize)
        if choice == 'resume' and 0 < existing_size < xfer.filesize:
          xfer.file_path = save_path
          xfer.resume_pos = existing_size
          conn = xfer.client.conn
          if conn and xfer.port:
            dcc_str = 'RESUME %s %d %d' % (
              _quote_filename(xfer.filename), xfer.port, existing_size)
            conn.ctcpMakeQuery(xfer.nick, [('DCC', dcc_str)])
            _notify_transfer(xfer, 'Requesting resume at %d bytes for %s' % (
              existing_size, xfer.filename))
            return
        elif choice == 'overwrite':
          pass
        elif choice == 'rename':
          base, ext = os.path.splitext(save_path)
          i = 1
          while os.path.exists('%s_%d%s' % (base, i, ext)):
            i += 1
          save_path = '%s_%d%s' % (base, i, ext)
        else:
          # Cancel
          xfer.status = Status.CANCELLED
          _notify_transfer(xfer, 'DCC GET from %s cancelled' % xfer.nick)
          return
      else:
        # Default: rename
        base, ext = os.path.splitext(save_path)
        i = 1
        while os.path.exists('%s_%d%s' % (base, i, ext)):
          i += 1
        save_path = '%s_%d%s' % (base, i, ext)

    xfer.file_path = save_path

    if xfer.token and xfer.host == '0' and xfer.port == 0:
      # Reverse DCC: we need to listen and tell the sender where to connect
      xfer._task = asyncio.ensure_future(self._recv_passive(xfer))
    else:
      # Normal DCC: connect to sender
      xfer._task = asyncio.ensure_future(self._recv_active(xfer))

  async def _recv_active(self, xfer):
    """Connect to the sender and download the file."""
    xfer.status = Status.CONNECTING
    _notify_transfer(xfer, 'Connecting to %s for %s' % (xfer.nick, xfer.filename))
    try:
      reader, writer = await asyncio.wait_for(
        asyncio.open_connection(xfer.host, xfer.port),
        timeout=state.config.dcc_timeout)
      await self._do_receive(xfer, reader, writer)
    except asyncio.TimeoutError:
      xfer.status = Status.FAILED
      xfer.error = 'Connection timed out'
      _notify_transfer(xfer, 'DCC GET from %s timed out' % xfer.nick)
    except asyncio.CancelledError:
      xfer.status = Status.CANCELLED
    except Exception as e:
      xfer.status = Status.FAILED
      xfer.error = str(e)
      _notify_transfer(xfer, 'DCC GET from %s failed: %s' % (xfer.nick, e))

  async def _recv_passive(self, xfer):
    """Reverse DCC receive: listen on a port, tell sender to connect, then receive."""
    port_min = state.config.dcc_port_min
    port_max = state.config.dcc_port_max
    server, port = await self._listen(port_min, port_max)
    if not server:
      xfer.status = Status.FAILED
      xfer.error = 'No available port'
      _notify_transfer(xfer, 'DCC GET: no available port')
      return

    await self._setup_port(port, xfer.client)
    ext_ip = await self.get_external_ip()
    if not ext_ip:
      xfer.status = Status.FAILED
      xfer.error = 'Cannot determine external IP'
      server.close()
      await self._teardown_port(port, xfer.client)
      _notify_transfer(xfer, 'DCC GET: cannot determine external IP for reverse DCC')
      return

    # Tell sender where to connect (echo back with our IP/port + same token)
    ip_long = ip_to_long(ext_ip)
    conn = xfer.client.conn
    if conn:
      dcc_str = 'SEND %s %d %d %d %s' % (
        _quote_filename(xfer.filename), ip_long, port, xfer.filesize, xfer.token)
      conn.ctcpMakeQuery(xfer.nick, [('DCC', dcc_str)])

    xfer.status = Status.CONNECTING
    _notify_transfer(xfer, 'Waiting for %s to connect (reverse DCC)' % xfer.nick)

    connected = asyncio.Event()
    reader = writer = None

    def on_connect(r, w):
      nonlocal reader, writer
      reader, writer = r, w
      connected.set()

    server.close()
    await server.wait_closed()
    server = await asyncio.start_server(on_connect, '', port)

    try:
      await asyncio.wait_for(connected.wait(), timeout=state.config.dcc_timeout)
    except asyncio.TimeoutError:
      xfer.status = Status.FAILED
      xfer.error = 'Connection timed out'
      _notify_transfer(xfer, 'DCC GET from %s timed out (reverse)' % xfer.nick)
      return
    finally:
      server.close()
      await self._teardown_port(port)

    try:
      await self._do_receive(xfer, reader, writer)
    except asyncio.CancelledError:
      xfer.status = Status.CANCELLED
    except Exception as e:
      xfer.status = Status.FAILED
      xfer.error = str(e)

  async def _do_receive(self, xfer, reader, writer):
    """Download file data from an established connection."""
    xfer._writer = writer
    xfer.status = Status.ACTIVE
    xfer.start_time = _time.monotonic()
    xfer._last_speed_time = xfer.start_time
    xfer._last_speed_bytes = 0
    _notify_transfer(xfer, 'Receiving %s from %s' % (xfer.filename, xfer.nick))

    try:
      mode = 'ab' if xfer.resume_pos else 'wb'
      with open(xfer.file_path, mode) as f:
        if xfer.resume_pos:
          xfer.transferred = xfer.resume_pos

        while xfer.transferred < xfer.filesize:
          remaining = xfer.filesize - xfer.transferred
          chunk_size = min(BLOCK_SIZE, remaining)
          data = await asyncio.wait_for(reader.read(chunk_size), timeout=60)
          if not data:
            break
          await asyncio.to_thread(f.write, data)
          xfer.transferred += len(data)
          xfer._update_speed()
          # Send ACK
          ack = struct.pack('!I', xfer.transferred & 0xFFFFFFFF)
          writer.write(ack)
          try:
            await writer.drain()
          except ConnectionError:
            break

      if xfer.transferred >= xfer.filesize:
        xfer.status = Status.COMPLETE
        _notify_transfer(xfer, 'DCC GET from %s complete: %s' % (xfer.nick, xfer.filename))
      else:
        xfer.status = Status.FAILED
        xfer.error = 'Transfer incomplete (%d/%d bytes)' % (xfer.transferred, xfer.filesize)
        _notify_transfer(xfer, 'DCC GET from %s incomplete' % xfer.nick)
    except asyncio.CancelledError:
      xfer.status = Status.CANCELLED
    except Exception as e:
      xfer.status = Status.FAILED
      xfer.error = str(e)
      _notify_transfer(xfer, 'DCC GET from %s failed: %s' % (xfer.nick, e))
    finally:
      writer.close()

  # --- DCC CHAT ---

  async def initiate_chat(self, client, nick):
    """Start a DCC CHAT with nick."""
    conn = client.conn
    if not conn:
      client.window.redmessage('[Not connected]')
      return None

    chat = DCCChat(client, nick)
    self.chats[chat.id] = chat

    port_min = state.config.dcc_port_min
    port_max = state.config.dcc_port_max
    server, port = await self._listen(port_min, port_max)
    if not server:
      chat.status = Status.FAILED
      client.window.redmessage('[DCC CHAT: no available port in range %d-%d]' % (port_min, port_max))
      return chat

    chat._server = server
    chat.port = port
    await self._setup_port(port, client)

    ext_ip = await self.get_external_ip(client)
    if not ext_ip:
      server.close()
      await self._teardown_port(port, client)
      client.window.redmessage('[DCC CHAT: cannot determine external IP]')
      chat.status = Status.FAILED
      return chat

    ip_long = ip_to_long(ext_ip)
    dcc_str = 'CHAT chat %d %d' % (ip_long, port)
    conn.ctcpMakeQuery(nick, [('DCC', dcc_str)])

    chat.status = Status.CONNECTING
    client.window.addline('[DCC CHAT request sent to %s]' % nick)

    chat._task = asyncio.ensure_future(self._chat_wait_connect(chat))
    return chat

  async def _chat_wait_connect(self, chat):
    """Wait for incoming DCC CHAT connection."""
    try:
      connected = asyncio.Event()
      reader = writer = None

      def on_connect(r, w):
        nonlocal reader, writer
        reader, writer = r, w
        connected.set()

      chat._server.close()
      await chat._server.wait_closed()
      chat._server = await asyncio.start_server(on_connect, '', chat.port)

      try:
        await asyncio.wait_for(connected.wait(), timeout=state.config.dcc_timeout)
      except asyncio.TimeoutError:
        chat.status = Status.FAILED
        if chat.window:
          chat.window.redmessage('[DCC CHAT timed out]')
        return
      finally:
        chat._server.close()
        await self._teardown_port(chat.port)

      chat._reader = reader
      chat._writer = writer
      chat.status = Status.ACTIVE
      self._create_chat_window(chat)
      await self._chat_read_loop(chat)
    except asyncio.CancelledError:
      chat.status = Status.CANCELLED
    except Exception as e:
      chat.status = Status.FAILED
      if chat.window:
        chat.window.redmessage('[DCC CHAT error: %s]' % e)

  async def accept_chat(self, chat):
    """Accept incoming DCC CHAT and connect."""
    if chat.status != Status.PENDING:
      return
    chat.status = Status.CONNECTING
    chat._task = asyncio.ensure_future(self._chat_connect(chat))

  async def _chat_connect(self, chat):
    """Connect to a DCC CHAT offer."""
    try:
      reader, writer = await asyncio.wait_for(
        asyncio.open_connection(chat.host, chat.port),
        timeout=state.config.dcc_timeout)
      chat._reader = reader
      chat._writer = writer
      chat.status = Status.ACTIVE
      self._create_chat_window(chat)
      await self._chat_read_loop(chat)
    except asyncio.TimeoutError:
      chat.status = Status.FAILED
      if chat.window:
        chat.window.redmessage('[DCC CHAT connection timed out]')
    except asyncio.CancelledError:
      chat.status = Status.CANCELLED
    except Exception as e:
      chat.status = Status.FAILED
      if chat.window:
        chat.window.redmessage('[DCC CHAT error: %s]' % e)

  def _create_chat_window(self, chat):
    """Create a DCC chat window."""
    from dcc_ui import DCCChatWindow
    chat.window = DCCChatWindow(chat)
    if chat.window:
      chat.window.addline('[DCC CHAT connected with %s]' % chat.nick)

  async def _chat_read_loop(self, chat):
    """Read lines from DCC chat connection."""
    try:
      while True:
        line = await chat._reader.readline()
        if not line:
          break
        text = line.decode('utf-8', errors='replace').rstrip('\r\n')
        if chat.window and text:
          chat.window.addline_msg(chat.nick, text)
    except asyncio.CancelledError:
      pass
    except (ConnectionError, OSError):
      pass
    finally:
      chat.status = Status.COMPLETE
      if chat.window:
        chat.window.redmessage('[DCC CHAT closed]')
      if chat._writer and not chat._writer.is_closing():
        chat._writer.close()

  # --- RESUME support ---

  def handle_resume(self, nick, filename, port, position):
    """Handle incoming DCC RESUME request from the receiver."""
    for xfer in self.transfers.values():
      if (xfer.direction == Direction.SEND and
          xfer.nick.lower() == nick.lower() and
          (xfer.port == port or xfer.filename.lower() == filename.lower())):
        xfer.resume_pos = position
        _notify_transfer(xfer, 'Resume requested by %s at %d bytes for %s' % (
          nick, position, filename))
        # Send DCC ACCEPT
        conn = xfer.client.conn
        if conn:
          dcc_str = 'ACCEPT %s %d %d' % (_quote_filename(filename), port, position)
          conn.ctcpMakeQuery(nick, [('DCC', dcc_str)])
        return

  def handle_accept(self, nick, filename, port, position):
    """Handle incoming DCC ACCEPT (response to our RESUME request)."""
    for xfer in self.transfers.values():
      if (xfer.direction == Direction.RECEIVE and
          xfer.nick.lower() == nick.lower() and
          xfer.status == Status.PENDING):
        xfer.resume_pos = position
        asyncio.ensure_future(self.accept_receive(xfer))
        return

  # --- Cancel ---

  def cancel(self, transfer_id):
    """Cancel a transfer or chat."""
    xfer = self.transfers.get(transfer_id)
    if xfer and xfer._task and not xfer._task.done():
      xfer._task.cancel()
      xfer.status = Status.CANCELLED
      _notify_transfer(xfer, 'DCC %s cancelled: %s' % (
        'SEND' if xfer.direction == Direction.SEND else 'GET', xfer.filename))
      return True
    chat = self.chats.get(transfer_id)
    if chat and chat._task and not chat._task.done():
      chat._task.cancel()
      chat.status = Status.CANCELLED
      return True
    return False

  # --- Cleanup ---

  def cleanup(self):
    """Cancel all active transfers and remove port mappings."""
    for xfer in self.transfers.values():
      if xfer._task and not xfer._task.done():
        xfer._task.cancel()
    for chat in self.chats.values():
      if chat._task and not chat._task.done():
        chat._task.cancel()
    for port in list(self._upnp_ports):
      try:
        from upnp import teardown_port_forwarding
        teardown_port_forwarding(port)
      except Exception:
        pass
    self._upnp_ports.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _quote_filename(name):
  """Quote a filename for DCC if it contains spaces."""
  if ' ' in name:
    return '"%s"' % name
  return name


def _notify_transfer(xfer, message):
  """Show a transfer status message in the active window for this network,
  falling back to the server window."""
  target = None
  if xfer.client:
    active_sub = state.app.mainwin.workspace.activeSubWindow()
    if active_sub:
      aw = active_sub.widget()
      if aw and getattr(aw, 'client', None) is xfer.client:
        target = aw
    if not target:
      target = xfer.client.window
  if target:
    target.redmessage('[%s]' % message)
  state.dbg(state.LOG_INFO, '[dcc] %s' % message)


# ---------------------------------------------------------------------------
# DCC CTCP request parser
# ---------------------------------------------------------------------------

def parse_dcc_request(data):
  """Parse a DCC CTCP data string.

  Returns a dict with keys: type, filename, host, port, filesize, token.
  Returns None if unparseable.
  """
  parts = []
  rest = data.strip()
  # Handle quoted filenames
  while rest:
    if rest[0] in ('"', "'"):
      q = rest[0]
      end = rest.find(q, 1)
      if end >= 0:
        parts.append(rest[1:end])
        rest = rest[end + 1:].lstrip()
        continue
    sp = rest.find(' ')
    if sp >= 0:
      parts.append(rest[:sp])
      rest = rest[sp:].lstrip()
    else:
      parts.append(rest)
      rest = ''

  if len(parts) < 2:
    return None

  result = {'type': parts[0].upper()}

  if result['type'] == 'SEND':
    if len(parts) < 5:
      return None
    result['filename'] = parts[1]
    try:
      ip_long = int(parts[2])
      result['host'] = long_to_ip(ip_long) if ip_long else '0'
      result['port'] = int(parts[3])
      result['filesize'] = int(parts[4])
    except (ValueError, struct.error):
      return None
    result['token'] = parts[5] if len(parts) > 5 else None

  elif result['type'] == 'CHAT':
    if len(parts) < 4:
      return None
    try:
      ip_long = int(parts[2])
      result['host'] = long_to_ip(ip_long) if ip_long else '0'
      result['port'] = int(parts[3])
    except (ValueError, struct.error):
      return None

  elif result['type'] in ('RESUME', 'ACCEPT'):
    if len(parts) < 4:
      return None
    result['filename'] = parts[1]
    try:
      result['port'] = int(parts[2])
      result['position'] = int(parts[3])
    except ValueError:
      return None

  else:
    return None

  return result
