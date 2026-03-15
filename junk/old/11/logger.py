# logger.py - IRC Chat Logger

import os, re
from datetime import datetime

from config import _format_timestamp
from state import dbg, LOG_ERROR


class IRCLogger:
  def __init__(self, cfg, base_path):
    self.cfg = cfg
    self._base = os.path.join(base_path, cfg.log_dir) if not os.path.isabs(cfg.log_dir) else cfg.log_dir

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

  def log(self, network, target, line):
    p = self._path(network, target)
    try:
      os.makedirs(os.path.dirname(p), exist_ok=True)
      with open(p, 'a', encoding='utf-8') as f:
        ts = _format_timestamp(self.cfg.log_timestamp_format)
        f.write('[%s] %s\n' % (ts, line))
    except Exception:
      dbg(LOG_ERROR, 'Log write failed:', p)

  def log_server(self, network, line):
    self.log(network, '_server_', line)

  def log_channel(self, network, channel, line):
    self.log(network, channel, line)
