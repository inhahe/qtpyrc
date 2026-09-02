# logger.py - IRC Chat Logger

import os, re
from datetime import datetime

import bgwriter
from config import _format_timestamp


class IRCLogger:
  """Append chat lines to per-conversation log files.

  **No method here touches the filesystem.** log() works out the path and
  stamps the line -- both of which have to happen now, while the caller knows
  what time it is -- and hands the result to the shared bgwriter thread, which
  does the makedirs, the open, the write and the flush.

  It used to do all of that inline. An early version paid os.makedirs() plus
  open()+close() per line (~3% of GUI-thread time); caching the handles removed
  most of that but left the part that actually hurts, because the cost of a
  write is not the bookkeeping around it -- it is the flush, which is a
  WriteFile syscall, and a syscall against a loaded filesystem blocks for as
  long as the filesystem feels like. On the send path that sat between putting
  the line on the wire and saving it to history, so a busy disk reached the
  user as the client freezing for several seconds after they pressed Enter.
  See bgwriter.py for the rest of the reasoning.
  """

  def __init__(self, cfg, base_path):
    self.cfg = cfg
    self._base = os.path.join(base_path, cfg.log_dir) if not os.path.isabs(cfg.log_dir) else cfg.log_dir
    self._writer = bgwriter.shared()

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
    """Queue one line for *target*'s log file. Does no filesystem work.

    The timestamp is taken here rather than by the writer thread: it records
    when the line happened, which is not when the disk got round to accepting
    it, and under exactly the load this class exists to survive those two are
    seconds apart.
    """
    ts = _format_timestamp(self.cfg.log_timestamp_format)
    self._writer.write(self._path(network, target), '[%s] %s' % (ts, line))

  def flush(self, timeout=5.0):
    """Block until every queued line has been written. Tests and shutdown only.

    Never call this from the chat path -- waiting on the disk there is the one
    thing this class was rearranged to stop doing.
    """
    return self._writer.flush(timeout)

  def close(self):
    """Flush every queued line. Call on shutdown.

    Flushes rather than closes: the writer thread is shared with the render
    audit (bgwriter.shared()), so it is not this object's to shut down. The one
    place that owns it is process shutdown, which calls bgwriter.close_shared()
    after everything that might still want to log has stopped.
    """
    self.flush()

  def log_server(self, network, line):
    self.log(network, '_server_', line)

  def log_channel(self, network, channel, line):
    self.log(network, channel, line)
