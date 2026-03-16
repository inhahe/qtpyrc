# setup_wizard.py - First-run setup wizard for new users

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QPushButton,
    QPlainTextEdit, QGroupBox, QScrollArea, QWidget,
    QStackedWidget, QMessageBox,
)
from PySide6.QtCore import Qt


# Common IRC networks with sensible defaults
_NETWORKS = [
    ('Libera Chat', 'irc.libera.chat', 6697, True),
    ('OFTC', 'irc.oftc.net', 6697, True),
    ('EFnet', 'irc.efnet.org', 6667, False),
    ('Undernet', 'us.undernet.org', 6667, False),
    ('DALnet', 'irc.dal.net', 6697, True),
    ('IRCnet', 'open.ircnet.net', 6667, False),
    ('QuakeNet', 'irc.quakenet.org', 6667, False),
    ('Rizon', 'irc.rizon.net', 6697, True),
    ('Custom...', '', 6697, True),
]

# Popular channels per network (curated list)
_POPULAR_CHANNELS = {
    'Libera Chat': [
        ('#libera', 'Libera Chat support'),
        ('#linux', 'Linux discussion'),
        ('#python', 'Python programming'),
        ('#javascript', 'JavaScript'),
        ('#networking', 'Networking & sysadmin'),
        ('#security', 'Information security'),
        ('#web', 'Web development'),
        ('#music', 'Music discussion'),
        ('##programming', 'General programming'),
        ('##chat', 'General chat'),
        ('##english', 'English conversation'),
        ('##philosophy', 'Philosophy'),
        ('#git', 'Git version control'),
        ('#vim', 'Vim editor'),
        ('#emacs', 'Emacs editor'),
    ],
    'OFTC': [
        ('#oftc', 'OFTC support'),
        ('#debian', 'Debian Linux'),
        ('#spi', 'Software in the Public Interest'),
        ('#tor', 'Tor project'),
        ('#llvm', 'LLVM compiler'),
    ],
    'EFnet': [
        ('#chat', 'General chat'),
        ('#linux', 'Linux discussion'),
        ('#help', 'IRC help'),
    ],
    'Undernet': [
        ('#chat', 'General chat'),
        ('#help', 'IRC help'),
        ('#philosophy', 'Philosophy'),
        ('#music', 'Music'),
    ],
    'DALnet': [
        ('#help', 'DALnet help'),
        ('#chat', 'General chat'),
        ('#coding', 'Programming'),
        ('#music', 'Music'),
    ],
    'IRCnet': [
        ('#ircnet', 'IRCnet discussion'),
        ('#linux', 'Linux'),
    ],
    'QuakeNet': [
        ('#quakenet', 'QuakeNet support'),
        ('#help.quakenet', 'QuakeNet help'),
    ],
    'Rizon': [
        ('#rizon', 'Rizon support'),
        ('#chat', 'General chat'),
        ('#anime', 'Anime discussion'),
    ],
}


class SetupWizard(QDialog):
    """First-run setup wizard for new users."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Welcome to qtpyrc')
        self.setMinimumWidth(480)
        self.setMinimumHeight(400)
        self.result_data = None

        layout = QVBoxLayout(self)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        # --- Page 1: Identity + Network ---
        page1 = QWidget()
        p1_layout = QVBoxLayout(page1)

        welcome = QLabel(
            '<h2>Welcome to qtpyrc!</h2>'
            '<p>Let\'s get you connected to IRC. '
            'Fill in the basics below, or click <b>Skip</b> to '
            'configure everything manually in Settings.</p>')
        welcome.setWordWrap(True)
        p1_layout.addWidget(welcome)

        # Identity
        id_group = QGroupBox('Your Identity')
        id_form = QFormLayout(id_group)
        self.nick = QLineEdit()
        self.nick.setPlaceholderText('e.g. mynick')
        self.nick.textChanged.connect(lambda: self.nick.setStyleSheet(''))
        id_form.addRow('Nickname:', self.nick)
        self.user = QLineEdit()
        self.user.setPlaceholderText('(same as nickname)')
        id_form.addRow('Username:', self.user)
        p1_layout.addWidget(id_group)

        # Network
        net_group = QGroupBox('Network')
        net_form = QFormLayout(net_group)
        self.network = QComboBox()
        for name, host, port, tls in _NETWORKS:
            self.network.addItem(name)
        self.network.currentIndexChanged.connect(self._on_network_changed)
        net_form.addRow('Network:', self.network)
        self.host = QLineEdit()
        self.host.textChanged.connect(lambda: self.host.setStyleSheet(''))
        net_form.addRow('Server:', self.host)
        self.port = QLineEdit()
        self.port.setMaximumWidth(80)
        net_form.addRow('Port:', self.port)
        self.tls = QCheckBox('Use TLS/SSL encryption')
        net_form.addRow('', self.tls)
        p1_layout.addWidget(net_group)

        # SASL
        self.sasl_check = QCheckBox('I have a NickServ/SASL account')
        p1_layout.addWidget(self.sasl_check)
        self.sasl_group = QGroupBox('SASL Authentication')
        sasl_form = QFormLayout(self.sasl_group)
        self.sasl_user = QLineEdit()
        self.sasl_user.setPlaceholderText('(same as nickname)')
        sasl_form.addRow('Account name:', self.sasl_user)
        self.sasl_pass = QLineEdit()
        self.sasl_pass.setEchoMode(QLineEdit.EchoMode.Password)
        sasl_form.addRow('Password:', self.sasl_pass)
        self.sasl_group.setVisible(False)
        p1_layout.addWidget(self.sasl_group)
        def _toggle_sasl(checked):
            self.sasl_group.setVisible(checked)
        self.sasl_check.toggled.connect(_toggle_sasl)

        p1_layout.addStretch(1)
        self.stack.addWidget(page1)

        # --- Page 2: Channels ---
        page2 = QWidget()
        p2_layout = QVBoxLayout(page2)

        p2_layout.addWidget(QLabel(
            '<h3>Choose some channels to join</h3>'
            '<p>Select from the popular channels below, or type your own.</p>'))

        # Popular channels
        self._chan_group = QGroupBox('Popular Channels')
        chan_group_layout = QVBoxLayout(self._chan_group)
        self._chan_scroll = QScrollArea()
        self._chan_scroll.setWidgetResizable(True)
        self._chan_container = QWidget()
        self._chan_grid = QVBoxLayout(self._chan_container)
        self._chan_grid.setContentsMargins(4, 4, 4, 4)
        self._chan_grid.setSpacing(2)
        self._chan_scroll.setWidget(self._chan_container)
        chan_group_layout.addWidget(self._chan_scroll)
        self._chan_checkboxes = []
        p2_layout.addWidget(self._chan_group, 1)

        # Custom channels
        chan_custom = QGroupBox('Additional Channels')
        chan_custom_layout = QVBoxLayout(chan_custom)
        chan_custom_layout.addWidget(QLabel(
            'One channel per line. # is added automatically if missing.'))
        self.channels = QPlainTextEdit()
        chan_custom_layout.addWidget(self.channels)
        p2_layout.addWidget(chan_custom, 1)

        self.stack.addWidget(page2)

        # --- Buttons ---
        self._btn_layout = QHBoxLayout()
        self.skip_btn = QPushButton('Skip')
        self.skip_btn.clicked.connect(self.reject)
        self._btn_layout.addWidget(self.skip_btn)
        self._btn_layout.addStretch()
        self.back_btn = QPushButton('Back')
        self.back_btn.clicked.connect(self._go_back)
        self.back_btn.setVisible(False)
        self._btn_layout.addWidget(self.back_btn)
        self.next_btn = QPushButton('Next')
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._go_next)
        self._btn_layout.addWidget(self.next_btn)
        layout.addLayout(self._btn_layout)

        # Select first network
        self._on_network_changed(0)

    def _go_next(self):
        if self.stack.currentIndex() == 0:
            # Validate page 1
            nick = self.nick.text().strip()
            if not nick:
                self.nick.setFocus()
                self.nick.setStyleSheet('border: 1px solid red;')
                return
            host = self.host.text().strip()
            if not host:
                self.host.setFocus()
                self.host.setStyleSheet('border: 1px solid red;')
                return
            # Go to page 2
            self.stack.setCurrentIndex(1)
            self.back_btn.setVisible(True)
            self.next_btn.setText('Connect')
        else:
            # Page 2 — connect
            self._on_connect()

    def _go_back(self):
        self.stack.setCurrentIndex(0)
        self.back_btn.setVisible(False)
        self.next_btn.setText('Next')

    def _on_network_changed(self, index):
        if index < 0 or index >= len(_NETWORKS):
            return
        name, host, port, tls = _NETWORKS[index]
        is_custom = (name == 'Custom...')
        self.host.setText(host)
        self.host.setReadOnly(not is_custom)
        self.port.setText(str(port))
        self.port.setReadOnly(not is_custom)
        self.tls.setChecked(tls)
        self.tls.setEnabled(is_custom)
        self._populate_channels(name)

    def _populate_channels(self, network_name):
        """Populate the popular channels checkboxes for the selected network."""
        for cb in self._chan_checkboxes:
            cb.setParent(None)
        self._chan_checkboxes = []

        channels = _POPULAR_CHANNELS.get(network_name, [])
        if channels:
            self._chan_group.setVisible(True)
            for chan, desc in channels:
                cb = QCheckBox('%s  —  %s' % (chan, desc))
                cb.setProperty('_channel', chan)
                self._chan_grid.addWidget(cb)
                self._chan_checkboxes.append(cb)
        else:
            self._chan_group.setVisible(False)

    def _on_connect(self):
        nick = self.nick.text().strip()
        host = self.host.text().strip()

        # Build config data
        net_name = self.network.currentText()
        if net_name == 'Custom...':
            net_key = host.split('.')[0] if '.' in host else host
        else:
            net_key = net_name.lower().replace(' ', '')

        # Check for duplicate network
        import state
        if state.config and state.config._data.get('networks'):
            existing = state.config._data['networks']
            if net_key in existing:
                reply = QMessageBox.question(
                    self, 'Network Exists',
                    'A network "%s" is already configured.\n\n'
                    'Do you want to update it with these settings?' % net_key,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
            else:
                for ek, ev in existing.items():
                    srv = ev.get('server') or {}
                    if isinstance(srv, dict) and srv.get('host', '').lower() == host.lower():
                        reply = QMessageBox.question(
                            self, 'Similar Network',
                            'Network "%s" already connects to %s.\n\n'
                            'Add "%s" as a separate network anyway?' % (ek, host, net_key),
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                        if reply != QMessageBox.StandardButton.Yes:
                            return
                        break

        channels = []
        for cb in self._chan_checkboxes:
            if cb.isChecked():
                channels.append(cb.property('_channel'))
        for line in self.channels.toPlainText().strip().splitlines():
            ch = line.strip()
            if ch:
                if not ch.startswith('#') and not ch.startswith('&'):
                    ch = '#' + ch
                if ch not in channels:
                    channels.append(ch)

        self.result_data = {
            'nick': nick,
            'user': self.user.text().strip() or nick,
            'net_key': net_key,
            'net_name': net_name if net_name != 'Custom...' else '',
            'host': host,
            'port': int(self.port.text()) if self.port.text().isdigit() else 6667,
            'tls': self.tls.isChecked(),
            'channels': channels,
            'sasl': None,
        }

        if self.sasl_check.isChecked() and self.sasl_pass.text().strip():
            self.result_data['sasl'] = {
                'mechanism': 'PLAIN',
                'username': self.sasl_user.text().strip() or nick,
                'password': self.sasl_pass.text().strip(),
            }

        self.accept()


def apply_wizard_result(config, data):
    """Apply the wizard result to the config and save."""
    from ruamel.yaml.comments import CommentedMap

    config._data['nick'] = data['nick']
    config._data['user'] = data['user']
    config._data['realname'] = data['nick']

    # Create network
    networks = config._data.get('networks')
    if networks is None:
        networks = CommentedMap()
        config._data['networks'] = networks

    net = CommentedMap()
    if data['net_name']:
        net['name'] = data['net_name']
    net['auto_connect'] = True

    # Server
    server = CommentedMap()
    server['host'] = data['host']
    server['port'] = data['port']
    server['tls'] = data['tls']
    net['server'] = server

    # SASL
    if data['sasl']:
        sasl = CommentedMap()
        sasl['mechanism'] = data['sasl']['mechanism']
        sasl['username'] = data['sasl']['username']
        sasl['password'] = data['sasl']['password']
        net['sasl'] = sasl

    # Channels
    if data['channels']:
        auto_join = CommentedMap()
        for ch in data['channels']:
            auto_join[ch] = None
        net['auto_join'] = auto_join

    networks[data['net_key']] = net
    config.save()

    # Re-init config from the updated data
    config.__init__(config.path, config._data, config._yaml)


def should_show_wizard(config):
    """Return True if the wizard should be shown (no networks configured)."""
    networks = config._data.get('networks') or {}
    return len(networks) == 0
