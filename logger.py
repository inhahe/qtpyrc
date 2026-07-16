# logger.py - IRC Chat Logger

import os, re
from datetime import datetime

from config import _format_timestamp
from state import dbg, LOG_ERROR


class IRCLogger:
  def __init__(self, cfg, base_path):
    self.cfg = cfg
    self._base = os.path.join(base_path, cfg.log_dir) if not os.path.isabs(cfg.log_dir) else cfg.log_dir
    # Caches so we don't hit the filesystem on every logged line. Previously
    # log() called os.makedirs() and open()+close() per line, which was ~3% of
    # GUI-thread time. Now we makedirs a directory at most once, and keep the
    # log file open between writes (flushed each write, closed on shutdown).
    self._dirs_made = set()      # directories we've already created
    self._handles = {}           # path -> open file object

  def _safename(self, s):
    return re.sub(r'[<>:"/\\|?*]', '_', s or 'unknown')

  def _path(self, network, target):
    snet = self._safename(network)
    stgt = self._safename(target)
    month = datetime.now().strftime('%Y-%m') if self.cfg.log_separate_by_month else None

    if self.cfg.log_use_subdirs:
      if stgt == '_server_':
        d = os.path.join(self._base, snet)
        fn = ('server_%s.log' % month) if month else 'server.log'
      else:
        d = os.path.join(self._base, snet, stgt)
        fn = ('%s.log' % month) if month else 'log.log'
    else:
      d = self._base
      name = '%s_%s' % (snet, stgt)
      fn = ('%s_%s.log' % (name, month)) if month else ('%s.log' % name)
    return os.path.join(d, fn)

  def _handle(self, p):
    """Return an open append handle for path *p*, creating the directory (once)
    and opening the file (once) as needed. Handles are cached and reused."""
    f = self._handles.get(p)
    if f is not None:
      return f
    d = os.path.dirname(p)
    if d not in self._dirs_made:
      os.makedirs(d, exist_ok=True)
      self._dirs_made.add(d)
    f = open(p, 'a', encoding='utf-8')
    self._handles[p] = f
    return f

  def log(self, network, target, line):
    p = self._path(network, target)
    try:
      f = self._handle(p)
      ts = _format_timestamp(self.cfg.log_timestamp_format)
      f.write('[%s] %s\n' % (ts, line))
      f.flush()
    except Exception:
      dbg(LOG_ERROR, 'Log write failed:', p)
      # Drop a possibly-broken handle so we retry cleanly next time.
      bad = self._handles.pop(p, None)
      if bad is not None:
        try:
          bad.close()
        except Exception:
          pass

  def close(self):
    """Close all cached log file handles. Call on shutdown."""
    for f in self._handles.values():
      try:
        f.close()
      except Exception:
        pass
    self._handles.clear()

  def log_server(self, network, line):
    self.log(network, '_server_', line)

  def log_channel(self, network, channel, line):
    self.log(network, channel, line)
