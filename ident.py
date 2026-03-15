# ident.py - Identd server

import asyncio

import state
from state import dbg, LOG_INFO, LOG_WARN


class IdentdProtocol(asyncio.Protocol):
  def connection_made(self, transport):
    self.transport = transport
  def data_received(self, data):
    response = data.decode('utf-8', errors='replace').strip() + " : USERID : UNIX : " + state.config.identid + "\r\n"
    self.transport.write(response.encode())

async def runidentd():
  if not state.config.ident_enabled:
    return None
  loop = asyncio.get_event_loop()
  try:
    server = await loop.create_server(IdentdProtocol, state.config.ident_host, state.config.ident_port)
    dbg(LOG_INFO, 'Identd listening on %s:%s' % (state.config.ident_host, state.config.ident_port))
    return server
  except Exception:
    dbg(LOG_WARN, 'Could not run identd server.')
    return None
