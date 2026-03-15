from PySide6.QtWidgets import (
    QWidget, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox, QLineEdit,
)


class LinkPreviewPage(QWidget):
    """Link preview settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        self.link_preview = QCheckBox()
        self.link_preview.setToolTip("Show inline previews for URLs posted in channels.\n"
                                     "Off by default — fetching URLs reveals your IP.")
        layout.addRow("Enable:", self.link_preview)

        self.lp_timeout = QDoubleSpinBox()
        self.lp_timeout.setRange(1.0, 30.0)
        self.lp_timeout.setDecimals(1)
        self.lp_timeout.setSuffix(" s")
        layout.addRow("Timeout:", self.lp_timeout)

        self.lp_max_size = QSpinBox()
        self.lp_max_size.setRange(4096, 1048576)
        self.lp_max_size.setSuffix(" bytes")
        self.lp_max_size.setSingleStep(4096)
        layout.addRow("Max download:", self.lp_max_size)

        self.lp_width = QSpinBox()
        self.lp_width.setRange(100, 800)
        self.lp_width.setSuffix(" px")
        layout.addRow("Width:", self.lp_width)

        self.lp_height = QSpinBox()
        self.lp_height.setRange(40, 400)
        self.lp_height.setSuffix(" px")
        layout.addRow("Height:", self.lp_height)

        self.lp_proxy = QLineEdit()
        self.lp_proxy.setPlaceholderText("e.g. socks5://127.0.0.1:9050")
        self.lp_proxy.setToolTip("Route preview fetches through a proxy to hide your IP.\n"
                                 "Supports http://, https://, socks5:// URLs.\n"
                                 "Leave blank for direct connection.")
        layout.addRow("Proxy:", self.lp_proxy)

    def load_from_data(self, data):
        lp = data.get('link_preview') or {}
        if isinstance(lp, bool):
            lp = {'enabled': lp}
        self.link_preview.setChecked(bool(lp.get('enabled', False)))
        self.lp_timeout.setValue(float(lp.get('timeout', 10.0)))
        self.lp_max_size.setValue(int(lp.get('max_size', 262144)))
        self.lp_width.setValue(int(lp.get('width', 400)))
        self.lp_height.setValue(int(lp.get('height', 120)))
        self.lp_proxy.setText(str(lp.get('proxy', '')))

    def save_to_data(self, data):
        from ruamel.yaml.comments import CommentedMap
        lp = data.get('link_preview')
        if lp is None or isinstance(lp, bool):
            lp = CommentedMap()
            data['link_preview'] = lp
        lp['enabled'] = self.link_preview.isChecked()
        lp['timeout'] = self.lp_timeout.value()
        lp['max_size'] = self.lp_max_size.value()
        lp['width'] = self.lp_width.value()
        lp['height'] = self.lp_height.value()
        proxy = self.lp_proxy.text().strip()
        if proxy:
            lp['proxy'] = proxy
        elif 'proxy' in lp:
            del lp['proxy']
