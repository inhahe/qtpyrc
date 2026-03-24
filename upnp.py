# upnp.py - NAT traversal via UPnP and NAT-PMP
#
# Provides automatic port forwarding for DCC connections.
# UPnP uses miniupnpc (pip install miniupnpc) if available.
# NAT-PMP uses natpmp (pip install NAT-PMP) if available.
# All functions are blocking — call via asyncio.to_thread().

import socket
import state

# ---------------------------------------------------------------------------
# Availability checks (run once, cached)
# ---------------------------------------------------------------------------

_upnp_obj = None
_upnp_checked = False
_natpmp_checked = False
_natpmp_available = False
_availability_logged = False


def check_availability():
  """Check and log availability of UPnP and NAT-PMP. Call once at startup."""
  global _availability_logged
  if _availability_logged:
    return
  _availability_logged = True
  upnp = _get_upnp()
  natpmp = _check_natpmp()
  if upnp:
    state.dbg(state.LOG_INFO, '[nat] UPnP available (gateway: %s)' % upnp.lanaddr)
  elif natpmp:
    state.dbg(state.LOG_INFO, '[nat] NAT-PMP available (UPnP unavailable)')
  else:
    parts = []
    try:
      import miniupnpc
      parts.append('UPnP: no gateway found')
    except ImportError:
      parts.append('UPnP: miniupnpc not installed')
    try:
      import natpmp as _nm
      parts.append('NAT-PMP: no gateway found')
    except ImportError:
      parts.append('NAT-PMP: natpmp not installed')
    state.dbg(state.LOG_INFO, '[nat] No port forwarding available (%s)' % '; '.join(parts))


# ---------------------------------------------------------------------------
# UPnP
# ---------------------------------------------------------------------------

def _get_upnp():
  """Get or create a shared UPnP object."""
  global _upnp_obj, _upnp_checked
  if _upnp_checked:
    return _upnp_obj
  _upnp_checked = True
  try:
    import miniupnpc
    u = miniupnpc.UPnP()
    u.discoverdelay = 2000
    devices = u.discover()
    if devices:
      u.selectigd()
      _upnp_obj = u
      return u
  except ImportError:
    pass
  except Exception:
    pass
  return None


def get_external_ip_upnp():
  """Get external IP address via UPnP. Returns IP string or None."""
  u = _get_upnp()
  if u:
    try:
      ip = u.externalipaddress()
      if ip:
        return ip
    except Exception:
      pass
  return None


def setup_port_upnp(port, protocol='TCP', description='qtpyrc DCC'):
  """Add a UPnP port mapping. Blocking call. Raises on failure."""
  u = _get_upnp()
  if not u:
    raise RuntimeError('UPnP not available')
  result = u.addportmapping(port, protocol, u.lanaddr, port, description, '')
  if result:
    state.dbg(state.LOG_INFO, '[nat] UPnP: mapped port %d' % port)
    return True
  raise RuntimeError('UPnP addportmapping returned False')


def teardown_port_upnp(port, protocol='TCP'):
  """Remove a UPnP port mapping. Blocking call."""
  u = _get_upnp()
  if not u:
    return
  try:
    u.deleteportmapping(port, protocol)
    state.dbg(state.LOG_DEBUG, '[nat] UPnP: removed port %d' % port)
  except Exception:
    pass


# ---------------------------------------------------------------------------
# NAT-PMP
# ---------------------------------------------------------------------------

def _check_natpmp():
  """Check if NAT-PMP is available."""
  global _natpmp_available, _natpmp_checked
  if _natpmp_checked:
    return _natpmp_available
  _natpmp_checked = True
  try:
    import natpmp
    _natpmp_available = True
    return True
  except ImportError:
    _natpmp_available = False
    return False


def get_external_ip_natpmp():
  """Get external IP address via NAT-PMP. Returns IP string or None."""
  if not _check_natpmp():
    return None
  try:
    import natpmp
    response = natpmp.get_public_address()
    ip = response.public_ip_addr
    if isinstance(ip, bytes):
      ip = socket.inet_ntoa(ip)
    return str(ip)
  except Exception:
    return None


def setup_port_natpmp(port, protocol='TCP', lifetime=3600):
  """Add a NAT-PMP port mapping. Blocking call. Raises on failure."""
  if not _check_natpmp():
    raise RuntimeError('NAT-PMP not available')
  import natpmp
  proto = natpmp.NATPMP_PROTOCOL_TCP if protocol == 'TCP' else natpmp.NATPMP_PROTOCOL_UDP
  natpmp.map_port(proto, port, port, lifetime)
  state.dbg(state.LOG_INFO, '[nat] NAT-PMP: mapped port %d' % port)
  return True


def teardown_port_natpmp(port, protocol='TCP'):
  """Remove a NAT-PMP port mapping. Blocking call."""
  if not _check_natpmp():
    return
  try:
    import natpmp
    proto = natpmp.NATPMP_PROTOCOL_TCP if protocol == 'TCP' else natpmp.NATPMP_PROTOCOL_UDP
    natpmp.map_port(proto, port, port, 0)
    state.dbg(state.LOG_DEBUG, '[nat] NAT-PMP: removed port %d' % port)
  except Exception:
    pass


# ---------------------------------------------------------------------------
# Combined interface — tries UPnP first, then NAT-PMP
# ---------------------------------------------------------------------------

def setup_port_forwarding(port, protocol='TCP', description='qtpyrc DCC'):
  """Add a port mapping via UPnP or NAT-PMP. Raises on total failure."""
  method = state.config.dcc_nat_traversal if state.config else 'auto'

  if method == 'upnp':
    return setup_port_upnp(port, protocol, description)
  elif method == 'natpmp':
    return setup_port_natpmp(port, protocol)
  else:
    # Auto: try UPnP first, fall back to NAT-PMP
    if _get_upnp():
      try:
        return setup_port_upnp(port, protocol, description)
      except Exception:
        pass
    if _check_natpmp():
      try:
        return setup_port_natpmp(port, protocol)
      except Exception:
        pass
    raise RuntimeError('No port forwarding available')


def teardown_port_forwarding(port, protocol='TCP'):
  """Remove a port mapping via UPnP or NAT-PMP."""
  method = state.config.dcc_nat_traversal if state.config else 'auto'

  if method == 'upnp':
    teardown_port_upnp(port, protocol)
  elif method == 'natpmp':
    teardown_port_natpmp(port, protocol)
  else:
    teardown_port_upnp(port, protocol)
    teardown_port_natpmp(port, protocol)


def get_external_ip():
  """Get external IP via UPnP or NAT-PMP. Returns string or None."""
  ip = get_external_ip_upnp()
  if ip:
    return ip
  return get_external_ip_natpmp()
