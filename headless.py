# headless.py - Stub window classes for headless (no-GUI) mode
#
# These replace the real Qt window classes when running with --headless.
# They accept all the same method calls but do nothing visual.

import state
from collections import deque


class _NullWidget:
    """Stub for any Qt widget reference."""
    def __getattr__(self, name):
        return _null_func

def _null_func(*args, **kwargs):
    return None


class StubOutput:
    """Stub for ChatOutput (QTextEdit)."""
    def __init__(self):
        self._doc_char_count = 0

    def document(self):
        return self

    def characterCount(self):
        self._doc_char_count += 1
        return self._doc_char_count

    def setFont(self, f): pass
    def font(self): return None
    def viewport(self): return self
    def width(self): return 800
    def height(self): return 600
    def __getattr__(self, name):
        return _null_func


class StubInput:
    """Stub for input QPlainTextEdit."""
    def setText(self, t): pass
    def setPlainText(self, t): pass
    def toPlainText(self): return ''
    def setFont(self, f): pass
    def setFixedHeight(self, h): pass
    def setFocus(self): pass
    def textCursor(self): return _NullWidget()
    def __getattr__(self, name):
        return _null_func


class StubNicksList:
    """Stub for NicksList (QListWidget)."""
    def __init__(self):
        self._items = {}

    def clear(self): self._items.clear()
    def addItem(self, item): pass
    def setFont(self, f): pass
    def count(self): return 0
    def sortItems(self, *a): pass
    def takeItem(self, row): return None
    def row(self, item): return -1
    def findItems(self, *a): return []
    def __getattr__(self, name):
        return _null_func


class StubWindow:
    """Base stub window replacing Window/Channelwindow/Querywindow/Serverwindow."""

    ACTIVITY_NONE = 0
    ACTIVITY_MESSAGE = 1
    ACTIVITY_HIGHLIGHT = 2

    def __init__(self, client=None):
        self.client = client
        self.type = 'server'
        self.channel = None
        self.query = None
        self.output = StubOutput()
        self.input = StubInput()
        self.inputhistory = []
        self._history_index = -1
        self._history_popup = None
        self._search_bar = _NullWidget()
        self._activity = self.ACTIVITY_NONE
        self.subwindow = _NullWidget()
        # Cursor stub
        self.cur = _NullWidget()
        # Scrollbar stub (used by Client._window_alive() to test for C++ deletion)
        self.vs = type('StubScrollBar', (), {'value': lambda self: 0})()
        # Deferred replay
        self._deferred_replay = None

    def addline(self, text, fmt=None, timestamp_override=None):
        """Log to console in headless mode."""
        if state.debug_level >= state.LOG_INFO:
            # Strip mIRC color codes for console output
            import re
            clean = re.sub(r'[\x02\x03\x0F\x16\x1D\x1F]|\x03\d{0,2}(?:,\d{0,2})?', '', str(text))
            print(clean)

    def addline_msg(self, nick, message, timestamp_override=None):
        self.addline('<%s> %s' % (nick, message), timestamp_override=timestamp_override)

    def addline_nick(self, parts, fmt=None, timestamp_override=None):
        text = ''
        for p in parts:
            if isinstance(p, tuple):
                text += p[0]
            else:
                text += str(p)
        self.addline(text, timestamp_override=timestamp_override)

    def redmessage(self, text):
        self.addline('[%s]' % text)

    def add_separator(self, label=''):
        pass

    def setWindowTitle(self, title):
        pass

    def refresh_custom_title(self):
        pass

    def clear_activity(self):
        self._activity = self.ACTIVITY_NONE

    def set_activity(self, level):
        if level > self._activity:
            self._activity = level

    def _widget_alive(self):
        return True

    def _is_active_window(self):
        return False

    def queue_replay_callback(self, cb):
        """In headless mode, callbacks always run immediately (no replay queue)."""
        return False

    # Headless never holds output back: there is no window to render into
    # later, lines go straight to the console as they arrive. The replay-queue
    # protocol is still implemented so the shared join/query/replay code can
    # call it unconditionally.
    _replay_queue = None
    # Never holding anything back means the replay never needs to stop short of
    # the newest row either -- None reads the whole backlog (history._id_cap).
    _replay_cutoff_id = None

    def begin_replay_queue(self):
        pass

    def _queue_if_replaying(self, method_name, args, kwargs):
        return False

    def _flush_replay_queue(self):
        pass

    # render_history_rows() -- the one renderer both the on-open replay and the
    # background drip-feed go through -- turns auto-scroll off around a replay
    # and puts it back afterwards, on whatever window it is handed. It *reads*
    # the flag to save it, so leaving it off the stub is not a missing no-op
    # but an AttributeError that kills the drip-feed task for every channel.
    # A console is always at the bottom, so True is also the honest answer.
    _auto_scroll = True
    _in_replay = False
    _history_more = None      # lazy scroll-up state; nothing here scrolls up

    def _scroll_to_bottom(self):
        pass

    def isActiveWindow(self):
        return False

    def _updateBottomAlign(self):
        pass

    def show(self):
        pass

    def close(self):
        pass

    def lineinput(self, text):
        """Process input as if the user typed it."""
        if text.strip():
            if text.startswith(state.config.cmdprefix):
                from commands import docommand
                docommand(self, *(text[len(state.config.cmdprefix):].split(" ", 1)))
            else:
                from commands import docommand
                docommand(self, "say", text)


class StubChannelWindow(StubWindow):
    def __init__(self, client, channel):
        super().__init__(client)
        self.type = 'channel'
        self.channel = channel
        self.nickslist = StubNicksList()
        self.nicklist = self.nickslist
        self.splitter = _NullWidget()
        self._typing_nicks = {}
        self._typing_send_time = 0
        self._typing_bar = _NullWidget()

    def _build_layout(self):
        pass

    def set_nick_typing(self, nick, typing):
        pass

    def _update_nick_typing(self, nick, typing):
        pass

    # Called on a part/quit by someone who was typing. Nothing populates
    # _typing_nicks here (set_nick_typing does nothing), so today the guard in
    # userLeft/userQuit never lets these run -- but they are part of the window
    # contract the shared handlers call unconditionally, and the cost of the
    # stub being one no-op short is a crash in the middle of a QUIT.
    def _clear_nick_typing(self, nick):
        self._typing_nicks.pop(nick, None)

    def _update_typing_bar(self):
        pass


class StubQueryWindow(StubWindow):
    def __init__(self, client):
        super().__init__(client)
        self.type = 'query'
        self._typing_send_time = 0


class StubServerWindow(StubWindow):
    def __init__(self, client):
        super().__init__(client)
        self.type = 'server'


# --- Stub workspace and main window ---

class StubWorkspace:
    """Stub for QStackedWidget / TabbedWorkspace / QMdiArea."""
    def activeSubWindow(self):
        return None
    def setActiveSubWindow(self, w):
        pass
    def addSubWindow(self, w):
        return _NullWidget()
    def set_tabs_visible(self, v):
        pass
    def set_disconnected(self, sw, disc):
        pass
    def __getattr__(self, name):
        return _null_func


class StubNetworkTree:
    """Stub for NetworkTree."""
    def add_channel(self, client, channel): pass
    def add_query(self, client, query): pass
    def remove_channel(self, client, channel): pass
    def remove_query(self, client, query): pass
    def update_client_label(self, client): pass
    def setVisible(self, v): pass
    def __getattr__(self, name):
        return _null_func


class StubMainWindow:
    """Stub for QMainWindow."""
    def __init__(self):
        self.workspace = StubWorkspace()
        self.network_tree = StubNetworkTree()
        self._custom_titlebar = None
        self._replay_status = ''
        self._tree_user_set = False
        self._tree_target_tw = 0
        self._toolbar = None
        self._titlebar_timer = _NullWidget()
        self._net_actions = {}

    def setWindowTitle(self, t):
        pass

    def menuBar(self):
        return _NullWidget()

    def addToolBar(self, tb):
        pass

    def statusBar(self):
        return _NullWidget()

    def resize(self, w, h):
        pass

    def show(self):
        pass

    def showMaximized(self):
        pass

    def isVisible(self):
        return False

    def __getattr__(self, name):
        return _null_func


def install_headless():
    """Replace window classes with stubs for headless mode."""
    import window
    import models

    # Replace window classes in the window module
    # (models.py uses late imports: from window import Channelwindow)
    window.Channelwindow = StubChannelWindow
    window.Querywindow = StubQueryWindow
    window.Serverwindow = StubServerWindow
    window.NetworkTree = StubNetworkTree

    # Also replace NickItem with a stub
    window.NickItem = type('NickItem', (), {
        '__init__': lambda self, *a, **kw: None,
        '__lt__': lambda self, other: False,
    })
