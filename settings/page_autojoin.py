from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView,
)


class AutoJoinPage(QWidget):
    """Auto-join channels for a network."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Channel", "Key"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.remove_btn = QPushButton("Remove")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.add_btn.clicked.connect(self._add_row)
        self.remove_btn.clicked.connect(self._remove_row)

    def _add_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem("#"))
        self.table.setItem(row, 1, QTableWidgetItem(""))

    def _remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def load_from_data(self, net_data):
        aj = net_data.get('auto_join') or {}
        self.table.setRowCount(0)
        for ch, key in aj.items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(ch)))
            self.table.setItem(row, 1, QTableWidgetItem(str(key) if key else ""))

    def save_to_data(self, net_data):
        from ruamel.yaml.comments import CommentedMap
        aj = CommentedMap()
        for row in range(self.table.rowCount()):
            ch_item = self.table.item(row, 0)
            key_item = self.table.item(row, 1)
            ch = ch_item.text().strip() if ch_item else ""
            key = key_item.text().strip() if key_item else ""
            if ch:
                aj[ch] = key if key else None
        net_data['auto_join'] = aj
