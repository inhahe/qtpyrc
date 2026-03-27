from PySide6.QtWidgets import (
    QWidget, QFormLayout, QPlainTextEdit, QLabel,
)
from settings.page_general import _ck


class ListsPage(QWidget):
    """Additive lists: ignores, auto-ops, highlights, notify.
    Used for both the global page and per-network sub-pages."""

    def __init__(self, level='global', parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        if level == 'network':
            note_text = ("Network-level lists are additive: entries here are\n"
                         "combined with the global lists. One entry per line.")
        else:
            note_text = ("Global lists are additive: network and channel levels\n"
                         "add to these, they don't replace them. One entry per line.")
        note = QLabel(note_text)
        note.setWordWrap(True)
        from settings import SETTINGS_NOTE_STYLE
        note.setStyleSheet(SETTINGS_NOTE_STYLE)
        layout.addRow(note)

        from settings import SETTINGS_LIST_STYLE as _ls

        self.ignores = _ck(QPlainTextEdit(), 'ignores')
        self.ignores.setPlaceholderText("nick!*@host masks, one per line")
        self.ignores.setStyleSheet(_ls)
        layout.addRow("Ignores:", self.ignores)

        self.auto_ops = _ck(QPlainTextEdit(), 'auto_ops')
        self.auto_ops.setPlaceholderText("nick!*@host masks, one per line")
        self.auto_ops.setStyleSheet(_ls)
        layout.addRow("Auto-ops:", self.auto_ops)

        self.highlights = _ck(QPlainTextEdit(), 'highlights')
        self.highlights.setPlaceholderText("words or /regex/ patterns, one per line")
        self.highlights.setStyleSheet(_ls)
        layout.addRow("Highlights:", self.highlights)

        self.notify_list = _ck(QPlainTextEdit(), 'notify')
        self.notify_list.setPlaceholderText("nicks to watch, one per line")
        self.notify_list.setStyleSheet(_ls)
        layout.addRow("Notify:", self.notify_list)

    def load_from_data(self, data):
        self.ignores.setPlainText('\n'.join(data.get('ignores') or []))
        self.auto_ops.setPlainText('\n'.join(data.get('auto_ops') or []))
        self.highlights.setPlainText('\n'.join(data.get('highlights') or []))
        self.notify_list.setPlainText('\n'.join(data.get('notify') or []))

    def save_to_data(self, data):
        def _save_list(key):
            widget = getattr(self, key if key != 'notify' else 'notify_list')
            text = widget.toPlainText().strip()
            if text:
                data[key] = [e.strip() for e in text.splitlines() if e.strip()]
            elif key in data:
                del data[key]
        _save_list('ignores')
        _save_list('auto_ops')
        _save_list('highlights')
        _save_list('notify')
