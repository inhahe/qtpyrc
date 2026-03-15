import os
import sys
import glob as _glob
import shutil
import subprocess

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QLabel,
    QPushButton, QComboBox, QFileDialog, QMessageBox, QInputDialog,
)
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtCore import QFileSystemWatcher

import state


# ---------------------------------------------------------------------------
# Editor detection
# ---------------------------------------------------------------------------

# (display_name, executable_or_command, [optional extra search paths/globs])
# Paths use forward slashes; globs match version-numbered directories.
_EDITOR_CANDIDATES = [
    # --- Lightweight text editors ---
    ('Notepad++', 'notepad++', [
        'C:/Program Files/Notepad++/notepad++.exe',
        'C:/Program Files (x86)/Notepad++/notepad++.exe',
    ]),
    ('Sublime Text', 'subl', [
        'C:/Program Files/Sublime Text/sublime_text.exe',
        'C:/Program Files/Sublime Text 3/sublime_text.exe',
        'C:/Program Files/Sublime Text 4/sublime_text.exe',
        '/opt/sublime_text/sublime_text',
        '/snap/bin/subl',
    ]),
    ('UltraEdit', 'uedit64', [
        'C:/Program Files/IDM Computer Solutions/UltraEdit/uedit64.exe',
        'C:/Program Files (x86)/IDM Computer Solutions/UltraEdit/uedit32.exe',
    ]),
    ('PilotEdit', 'PilotEdit', [
        'C:/Program Files/PilotEdit/PilotEdit.exe',
        'C:/Program Files (x86)/PilotEdit/PilotEdit.exe',
        'C:/Program Files/PilotEdit Lite/PilotEdit.exe',
        'C:/Program Files (x86)/PilotEdit Lite/PilotEdit.exe',
    ]),
    ('TextPad', 'TextPad', [
        'C:/Program Files/TextPad */TextPad.exe',
        'C:/Program Files (x86)/TextPad */TextPad.exe',
    ]),
    ('EditPlus', 'editplus', [
        'C:/Program Files/EditPlus/editplus.exe',
        'C:/Program Files (x86)/EditPlus/editplus.exe',
        'C:/Program Files/EditPlus */editplus.exe',
    ]),
    ('Geany', 'geany', [
        'C:/Program Files/Geany/bin/geany.exe',
        'C:/Program Files (x86)/Geany/bin/geany.exe',
    ]),
    ('Kate', 'kate', []),
    ('KWrite', 'kwrite', []),
    ('Gedit', 'gedit', []),
    ('GNOME Text Editor', 'gnome-text-editor', []),
    ('xed', 'xed', []),
    ('Pluma', 'pluma', []),
    ('Mousepad', 'mousepad', []),
    ('FeatherPad', 'featherpad', []),
    ('Leafpad', 'leafpad', []),
    ('Brackets', 'brackets', [
        'C:/Program Files/Brackets/Brackets.exe',
        'C:/Program Files (x86)/Brackets/Brackets.exe',
    ]),
    ('Atom', 'atom', [
        '%LOCALAPPDATA%/atom/atom.exe',
    ]),
    ('Notepad (Windows)', 'notepad', []),
    ('WordPad', 'wordpad', [
        'C:/Program Files/Windows NT/Accessories/wordpad.exe',
    ]),

    # --- VS Code and variants ---
    ('Visual Studio Code', 'code', [
        '%LOCALAPPDATA%/Programs/Microsoft VS Code/Code.exe',
        '/snap/bin/code',
        '/usr/share/code/code',
    ]),
    ('VS Code Insiders', 'code-insiders', [
        '%LOCALAPPDATA%/Programs/Microsoft VS Code Insiders/Code - Insiders.exe',
    ]),
    ('VSCodium', 'codium', [
        '%LOCALAPPDATA%/Programs/VSCodium/VSCodium.exe',
    ]),
    ('Cursor', 'cursor', [
        '%LOCALAPPDATA%/Programs/cursor/Cursor.exe',
    ]),
    ('Windsurf', 'windsurf', [
        '%LOCALAPPDATA%/Programs/Windsurf/Windsurf.exe',
    ]),

    # --- Terminal editors (if on PATH) ---
    ('Vim', 'vim', []),
    ('Vi', 'vi', []),
    ('GVim', 'gvim', [
        'C:/Program Files/Vim/vim*/gvim.exe',
        'C:/Program Files (x86)/Vim/vim*/gvim.exe',
    ]),
    ('Neovim', 'nvim', []),
    ('Nano', 'nano', []),
    ('Emacs', 'emacs', []),
    ('Micro', 'micro', []),
    ('Joe', 'joe', []),
    ('mcedit', 'mcedit', []),

    # --- JetBrains IDEs ---
    ('PyCharm', 'pycharm', [
        'C:/Program Files/JetBrains/PyCharm */bin/pycharm64.exe',
        'C:/Program Files/JetBrains/PyCharm Community Edition */bin/pycharm64.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/pycharm',
        '/snap/bin/pycharm-*',
    ]),
    ('CLion', 'clion', [
        'C:/Program Files/JetBrains/CLion */bin/clion64.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/clion',
        '/snap/bin/clion',
    ]),
    ('IntelliJ IDEA', 'idea', [
        'C:/Program Files/JetBrains/IntelliJ IDEA */bin/idea64.exe',
        'C:/Program Files/JetBrains/IntelliJ IDEA Community Edition */bin/idea64.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/idea',
        '/snap/bin/intellij-idea-*',
    ]),
    ('WebStorm', 'webstorm', [
        'C:/Program Files/JetBrains/WebStorm */bin/webstorm64.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/webstorm',
        '/snap/bin/webstorm',
    ]),
    ('GoLand', 'goland', [
        'C:/Program Files/JetBrains/GoLand */bin/goland64.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/goland',
    ]),
    ('Rider', 'rider', [
        'C:/Program Files/JetBrains/JetBrains Rider */bin/rider64.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/rider',
    ]),
    ('RubyMine', 'rubymine', [
        'C:/Program Files/JetBrains/RubyMine */bin/rubymine64.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/rubymine',
    ]),
    ('PhpStorm', 'phpstorm', [
        'C:/Program Files/JetBrains/PhpStorm */bin/phpstorm64.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/phpstorm',
    ]),
    ('Fleet', 'fleet', [
        '%LOCALAPPDATA%/JetBrains/Toolbox/apps/Fleet/fleet.exe',
        '$HOME/.local/share/JetBrains/Toolbox/scripts/fleet',
    ]),

    # --- Full IDEs ---
    ('Visual Studio 2022', 'devenv', [
        'C:/Program Files/Microsoft Visual Studio/2022/Enterprise/Common7/IDE/devenv.exe',
        'C:/Program Files/Microsoft Visual Studio/2022/Professional/Common7/IDE/devenv.exe',
        'C:/Program Files/Microsoft Visual Studio/2022/Community/Common7/IDE/devenv.exe',
    ]),
    ('Visual Studio 2019', None, [
        'C:/Program Files (x86)/Microsoft Visual Studio/2019/Enterprise/Common7/IDE/devenv.exe',
        'C:/Program Files (x86)/Microsoft Visual Studio/2019/Professional/Common7/IDE/devenv.exe',
        'C:/Program Files (x86)/Microsoft Visual Studio/2019/Community/Common7/IDE/devenv.exe',
    ]),
    ('Eclipse', 'eclipse', [
        'C:/eclipse/eclipse.exe',
    ]),

    # --- Microsoft Office ---
    ('Microsoft Word', 'winword', [
        'C:/Program Files/Microsoft Office/root/Office*/WINWORD.EXE',
        'C:/Program Files (x86)/Microsoft Office/root/Office*/WINWORD.EXE',
    ]),
]


def _expand_env(path):
  """Expand %ENVVAR% and $ENVVAR in a path."""
  return os.path.expandvars(os.path.expanduser(path))


def _detect_editors():
  """Return a list of (display_name, exe_path) for editors found on the system."""
  found = []
  seen_paths = set()

  for name, cmd, extra_paths in _EDITOR_CANDIDATES:
    exe = None

    # Try known install paths first (more reliable than PATH)
    if extra_paths:
      for pattern in extra_paths:
        pattern = _expand_env(pattern)
        matches = _glob.glob(pattern)
        if matches:
          # Pick the last match (typically the newest version)
          candidate = matches[-1]
          if os.path.isfile(candidate):
            exe = candidate
            break

    # Fall back to PATH
    if not exe and cmd:
      exe = shutil.which(cmd)

    if exe:
      exe = os.path.normpath(exe)
      if exe.lower() not in seen_paths:
        seen_paths.add(exe.lower())
        found.append((name, exe))

  return found


# Cache the results (detection is slow due to filesystem checks)
_cached_editors = None

def get_available_editors():
  """Return cached list of detected editors."""
  global _cached_editors
  if _cached_editors is None:
    _cached_editors = _detect_editors()
  return _cached_editors


# ---------------------------------------------------------------------------
# File Editor page
# ---------------------------------------------------------------------------

class FileEditorPage(QWidget):
    """A settings page with a built-in text editor, quick-access file buttons,
    browse, save, reload, and external editor support."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = None
        self._quick_files = {}
        self._config_dir = ''
        self._loading = False
        self._dirty = False
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- top bar: quick-access buttons + browse ---
        top_bar = QHBoxLayout()

        self._btn_config = QPushButton("Config")
        self._btn_config.setToolTip("Edit the configuration file")
        self._btn_config.clicked.connect(lambda: self._open_quick('config'))
        top_bar.addWidget(self._btn_config)

        self._btn_startup = QPushButton("Startup")
        self._btn_startup.setToolTip("Edit startup commands file")
        self._btn_startup.clicked.connect(lambda: self._open_quick('startup'))
        top_bar.addWidget(self._btn_startup)

        self._btn_popups = QPushButton("Popups")
        self._btn_popups.setToolTip("Edit popups file")
        self._btn_popups.clicked.connect(lambda: self._open_quick('popups'))
        top_bar.addWidget(self._btn_popups)

        self._btn_toolbar = QPushButton("Toolbar")
        self._btn_toolbar.setToolTip("Edit toolbar file")
        self._btn_toolbar.clicked.connect(lambda: self._open_quick('toolbar'))
        top_bar.addWidget(self._btn_toolbar)

        self._btn_variables = QPushButton("Variables")
        self._btn_variables.setToolTip("Edit variables file")
        self._btn_variables.clicked.connect(lambda: self._open_quick('variables'))
        top_bar.addWidget(self._btn_variables)

        self._btn_browse = QPushButton("Browse...")
        self._btn_browse.setToolTip("Open any file")
        self._btn_browse.clicked.connect(self._browse)
        top_bar.addWidget(self._btn_browse)

        self._btn_init = QPushButton("Init Directory...")
        self._btn_init.setToolTip("Create all default config files in a directory")
        self._btn_init.clicked.connect(self._init_directory)
        top_bar.addWidget(self._btn_init)

        self._quick_buttons = {
            'config': self._btn_config,
            'startup': self._btn_startup,
            'popups': self._btn_popups,
            'toolbar': self._btn_toolbar,
            'variables': self._btn_variables,
        }

        top_bar.addStretch(1)
        layout.addLayout(top_bar)

        # --- external editor bar ---
        editor_bar = QHBoxLayout()

        editor_bar.addWidget(QLabel("External editor:"))

        self._editor_combo = QComboBox()
        self._editor_combo.setMinimumWidth(200)
        self._editor_combo.currentIndexChanged.connect(self._on_editor_changed)
        editor_bar.addWidget(self._editor_combo, 1)

        self._btn_browse_editor = QPushButton("...")
        self._btn_browse_editor.setFixedWidth(30)
        self._btn_browse_editor.setToolTip("Browse for an editor executable")
        self._btn_browse_editor.clicked.connect(self._browse_editor)
        editor_bar.addWidget(self._btn_browse_editor)

        layout.addLayout(editor_bar)

        # --- file path label ---
        self._path_label = QLabel("(no file loaded)")
        self._path_label.setStyleSheet("padding: 2px;")
        layout.addWidget(self._path_label)

        # --- editor ---
        self.editor = QPlainTextEdit()
        font_family = state.config.editor_font_family if state.config else 'Consolas'
        font_size = state.config.editor_font_size if state.config else 10
        self.editor.setFont(QFont(font_family, font_size))
        if state.config:
            self.editor.setStyleSheet(
                "QPlainTextEdit { background-color: %s; color: %s; }"
                % (state.config.editor_bgcolor.name(), state.config.editor_fgcolor.name()))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.editor, 1)

        # --- search bar ---
        from window import SearchBar
        self._search_bar = SearchBar(self.editor, on_close_focus=self.editor,
                                     set_cursor=True, parent=self)
        self._search_bar.setVisible(False)
        layout.addWidget(self._search_bar, 0)

        QShortcut(QKeySequence("Ctrl+F"), self, self._search_bar.open_bar)

        # --- bottom bar: restore defaults + save + reload ---
        bottom_bar = QHBoxLayout()

        self._btn_restore = QPushButton("Restore Defaults")
        self._btn_restore.setToolTip("Replace file contents with the default template")
        self._btn_restore.clicked.connect(self._restore_defaults)
        self._btn_restore.setEnabled(False)
        bottom_bar.addWidget(self._btn_restore)

        bottom_bar.addStretch(1)

        self._btn_save = QPushButton("Save")
        self._btn_save.setToolTip("Save the file")
        self._btn_save.clicked.connect(self._save)
        bottom_bar.addWidget(self._btn_save)

        self._btn_apply = QPushButton("Apply")
        self._btn_apply.setToolTip("Apply changes without saving to disk")
        self._btn_apply.clicked.connect(self._apply_only)
        bottom_bar.addWidget(self._btn_apply)

        self._btn_reload = QPushButton("Save && Apply")
        self._btn_reload.setToolTip("Save the file and apply changes to the running application")
        self._btn_reload.clicked.connect(self._save_and_reload)
        bottom_bar.addWidget(self._btn_reload)

        layout.addLayout(bottom_bar)

        # Populate editor dropdown
        self._populate_editors()

    def _populate_editors(self):
        """Fill the external editor dropdown with detected editors."""
        self._editor_combo.blockSignals(True)
        self._editor_combo.clear()
        self._editor_combo.addItem("(built-in editor)", "")
        self._editor_combo.addItem("(system default)", "__system__")

        editors = get_available_editors()
        for name, exe in editors:
            self._editor_combo.addItem(name, exe)

        self._editor_combo.addItem("Custom...", "__custom__")

        # Select the currently configured editor
        current = getattr(state.config, 'external_editor', '') if state.config else ''
        if current:
            idx = self._editor_combo.findData(current)
            if idx >= 0:
                self._editor_combo.setCurrentIndex(idx)
            else:
                # Custom path not in the detected list — add it
                label = os.path.basename(current)
                self._editor_combo.insertItem(
                    self._editor_combo.count() - 1, label, current)
                self._editor_combo.setCurrentIndex(self._editor_combo.count() - 2)

        self._editor_combo.blockSignals(False)

    def _on_editor_changed(self, index):
        """Handle editor dropdown selection."""
        data = self._editor_combo.currentData()
        if data == '__custom__':
            self._browse_editor()
            return

    def _browse_editor(self):
        """Browse for a custom editor executable."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Editor Executable", "",
            "All Files (*.*)" if sys.platform != 'win32'
            else "Executables (*.exe);;All Files (*.*)")
        if not path:
            # Revert to previous selection if cancelled
            current = getattr(state.config, 'external_editor', '') if state.config else ''
            idx = self._editor_combo.findData(current)
            if idx >= 0:
                self._editor_combo.blockSignals(True)
                self._editor_combo.setCurrentIndex(idx)
                self._editor_combo.blockSignals(False)
            return

        path = os.path.normpath(path)
        # Check if already in the list
        idx = self._editor_combo.findData(path)
        if idx >= 0:
            self._editor_combo.setCurrentIndex(idx)
        else:
            label = os.path.basename(path)
            # Insert before "Custom..."
            pos = self._editor_combo.count() - 1
            self._editor_combo.insertItem(pos, label, path)
            self._editor_combo.setCurrentIndex(pos)

    # Default filenames and config keys for each quick-access file type
    _QUICK_DEFAULTS = {
        'startup':   ('startup.rc',   'scripts',      'startup'),
        'popups':    ('popups.ini',    None,           'popups_file'),
        'toolbar':   ('toolbar.ini',   None,           'toolbar_file'),
        'variables': ('variables.ini', None,           'variables_file'),
    }

    def setup(self, config_dir, config_path, startup_path='',
              popups_path='', toolbar_path='', variables_path=''):
        """Set up the quick-access file paths."""
        self._config_dir = config_dir
        self._quick_files = {
            'config': config_path,
            'startup': startup_path,
            'popups': popups_path,
            'toolbar': toolbar_path,
            'variables': variables_path,
        }

    def _current_quick_key(self):
        """Return the quick-access key for the currently loaded file, or None."""
        if not self._path:
            return None
        norm = os.path.normcase(os.path.abspath(self._path))
        for key, qpath in self._quick_files.items():
            if qpath and os.path.normcase(os.path.abspath(qpath)) == norm:
                return key
        return None

    def _update_quick_highlight(self):
        """Highlight the quick-access button for the currently loaded file."""
        current_key = self._current_quick_key()
        for key, btn in self._quick_buttons.items():
            if key == current_key:
                btn.setStyleSheet("font-weight: bold;")
            else:
                btn.setStyleSheet("")
        # Enable restore button for files that have a default template (not config)
        if current_key and current_key != 'config':
            from qtpyrc import _DEFAULT_TEMPLATES
            self._btn_restore.setEnabled(current_key in _DEFAULT_TEMPLATES)
        else:
            self._btn_restore.setEnabled(False)

    def _check_unsaved(self):
        """If there are unsaved changes, ask the user what to do.
        Returns True if it's OK to proceed, False to cancel."""
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self, "Unsaved Changes",
            "The current file has unsaved changes.\n\n"
            "Do you want to save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if reply == QMessageBox.StandardButton.Save:
            return self._save_to_disk()
        if reply == QMessageBox.StandardButton.Discard:
            self._dirty = False
            return True
        return False  # Cancel

    def _load(self, path):
        """Load a file into the editor."""
        # Stop watching the old file
        watched = self._watcher.files()
        if watched:
            self._watcher.removePaths(watched)
        self._path = path
        self._update_quick_highlight()
        if path:
            self._path_label.setText(os.path.abspath(path))
        else:
            self._path_label.setText('(no file loaded)')
        self._loading = True
        if path and os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self.editor.setPlainText(f.read())
            except Exception as e:
                self.editor.setPlainText('')
                QMessageBox.warning(self, "Error", "Could not read file:\n%s" % e)
            self._watcher.addPath(path)
        else:
            self.editor.setPlainText('')
        self._loading = False
        self._dirty = False

    def _on_file_changed(self, path):
        """Called when the currently loaded file is modified externally."""
        if path != self._path:
            return
        # Some editors delete+recreate; re-add to watcher if the file still exists
        if os.path.isfile(path) and path not in self._watcher.files():
            self._watcher.addPath(path)
        # Use a queued call so the file watcher signal finishes before
        # we show a modal dialog (avoids losing window activation)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda p=path: self._prompt_file_changed(p))

    def _prompt_file_changed(self, path):
        """Show dialog for externally modified file."""
        if path != self._path:
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("File Changed")
        msg.setText("The file has been modified outside the editor:\n%s"
                    % os.path.basename(path))
        msg.setInformativeText("What would you like to do?")
        btn_reload = msg.addButton("Reload", QMessageBox.ButtonRole.AcceptRole)
        btn_save_as = msg.addButton("Save As + Reload", QMessageBox.ButtonRole.ActionRole)
        btn_keep = msg.addButton("Keep Mine", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_reload)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == btn_save_as:
            default_backup = self._path + ".bak"
            n = 2
            while os.path.isfile(default_backup):
                default_backup = self._path + ".bak%d" % n
                n += 1
            backup, _ = QFileDialog.getSaveFileName(
                self, "Save current content as", default_backup,
                "All Files (*.*)")
            if backup:
                try:
                    with open(backup, 'w', encoding='utf-8') as f:
                        f.write(self.editor.toPlainText())
                except Exception as e:
                    QMessageBox.warning(self, "Error",
                                        "Could not save backup:\n%s" % e)
                    return
            else:
                return  # cancelled save-as, don't reload
        if clicked == btn_keep:
            # Content differs from disk now
            self._dirty = True
            self._path_label.setText(os.path.abspath(path) + "  (unsaved)")
            return
        if clicked == btn_reload or clicked == btn_save_as:
            self._loading = True
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self.editor.setPlainText(f.read())
                self._path_label.setText(os.path.abspath(path))
                self._dirty = False
            except Exception as e:
                QMessageBox.warning(self, "Error",
                                    "Could not reload file:\n%s" % e)
            self._loading = False

    def _get_external_editor(self):
        """Return the currently selected external editor path, or empty string.
        Returns '__system__' for system default."""
        data = self._editor_combo.currentData()
        if data and data not in ('', '__custom__'):
            return data
        return ''

    def save_to_data(self, data):
        """Save external editor selection to config data."""
        editor = self._get_external_editor()
        data['external_editor'] = editor
        if state.config:
            state.config.external_editor = editor

    def _open_quick(self, key):
        """Open a quick-access file, using external editor if configured."""
        path = self._quick_files.get(key)
        if not path:
            path = self._prompt_create(key)
            if not path:
                return
        if not self._check_unsaved():
            return
        if self._try_external(path):
            return
        self._load(path)
        self.editor.setFocus()

    def _prompt_create(self, key):
        """Prompt the user to create a missing quick-access file.

        If confirmed, creates the file, updates the config yaml, and returns
        the new path.  Returns None if cancelled.
        """
        defaults = self._QUICK_DEFAULTS.get(key)
        if not defaults:
            return None
        default_name, section, config_key = defaults
        from ruamel.yaml.comments import CommentedMap

        # Resolve the scripts dir, offering to create it if needed
        if section == 'scripts':
            scripts_cfg = state.config._data.get('scripts') or {}
            scripts_dir = scripts_cfg.get('dir', '')
            if scripts_dir and not os.path.isabs(scripts_dir):
                scripts_dir = os.path.join(self._config_dir, scripts_dir)
            if not scripts_dir:
                # No scripts dir configured — offer a default
                scripts_dir = os.path.join(self._config_dir, 'scripts')
            if not os.path.isdir(scripts_dir):
                reply = QMessageBox.question(
                    self, "Create Scripts Directory",
                    "The scripts directory does not exist:\n%s\n\n"
                    "Create it?" % scripts_dir,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return None
                os.makedirs(scripts_dir, exist_ok=True)
                # Save the scripts dir to config
                cfg_scripts = state.config._data.get('scripts')
                if cfg_scripts is None:
                    cfg_scripts = CommentedMap()
                    state.config._data['scripts'] = cfg_scripts
                rel_dir = os.path.relpath(scripts_dir, self._config_dir)
                cfg_scripts['dir'] = rel_dir.replace('\\', '/')
                state.config.cmdscripts_dir = rel_dir.replace('\\', '/')
            default_dir = scripts_dir
        else:
            default_dir = self._config_dir

        default_path = os.path.join(default_dir, default_name)

        path, _ = QFileDialog.getSaveFileName(
            self, "Create %s file" % key.title(),
            default_path,
            "All Files (*)")
        if not path:
            return None

        # Create the file with template content if it doesn't exist
        if not os.path.isfile(path):
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            from qtpyrc import _DEFAULT_TEMPLATES, _resolve_template
            template = _resolve_template(key) if key in _DEFAULT_TEMPLATES else ''
            with open(path, 'w', encoding='utf-8') as f:
                f.write(template)

        # Update the config yaml so the file is loaded next time
        if section == 'scripts':
            scripts_cfg = state.config._data.get('scripts')
            if scripts_cfg is None:
                scripts_cfg = CommentedMap()
                state.config._data['scripts'] = scripts_cfg
            # Store relative to scripts dir
            scripts_dir_cfg = scripts_cfg.get('dir', '')
            if scripts_dir_cfg and not os.path.isabs(scripts_dir_cfg):
                abs_scripts_dir = os.path.join(self._config_dir, scripts_dir_cfg)
            else:
                abs_scripts_dir = scripts_dir_cfg or self._config_dir
            if path.startswith(abs_scripts_dir.rstrip('/\\') + os.sep):
                rel = os.path.relpath(path, abs_scripts_dir)
            else:
                rel = os.path.relpath(path, self._config_dir)
            scripts_cfg[config_key] = rel.replace('\\', '/')
            state.config.startup_file = rel.replace('\\', '/')
        else:
            rel = os.path.relpath(path, self._config_dir)
            state.config._data[config_key] = rel.replace('\\', '/')
            setattr(state.config, config_key, rel.replace('\\', '/'))

        state.config.save()
        self._quick_files[key] = path
        return path

    def _browse(self):
        """Open a file browser dialog."""
        start_dir = self._config_dir or '.'
        path, _ = QFileDialog.getOpenFileName(
            self, "Open File", start_dir,
            "All Files (*.*);;Config Files (*.yaml *.yml *.ini *.rc *.txt);;YAML (*.yaml *.yml);;INI (*.ini);;Scripts (*.rc *.txt);;Python (*.py)")
        if path:
            if not self._check_unsaved():
                return
            if self._try_external(path):
                return
            self._load(path)

    def _init_directory(self):
        """Create all default config files in a user-chosen directory."""
        from qtpyrc import _DEFAULT_ANCILLARY, _DEFAULT_TEMPLATES, init_default_files
        start_dir = self._config_dir or '.'
        directory = QFileDialog.getExistingDirectory(
            self, "Select directory for default config files", start_dir)
        if not directory:
            return

        # Check if config.yaml already exists — offer to rename
        config_name = 'config.yaml'
        config_path = os.path.join(directory, config_name)
        if os.path.isfile(config_path):
            config_name, ok = QInputDialog.getText(
                self, "Config file exists",
                "config.yaml already exists in this directory.\n"
                "Enter an alternative filename, or cancel to skip it:",
                text='config_new.yaml')
            if not ok or not config_name.strip():
                config_name = None  # skip config creation
            else:
                config_name = config_name.strip()
                if not config_name.endswith(('.yaml', '.yml')):
                    config_name += '.yaml'
                if os.path.isfile(os.path.join(directory, config_name)):
                    QMessageBox.warning(self, "Error",
                                        "%s also already exists." % config_name)
                    config_name = None

        # Check each ancillary file for conflicts — prompt per file
        overwrite = set()
        cancelled = False
        for name, is_dir in _DEFAULT_ANCILLARY:
            if is_dir:
                continue
            stem = os.path.splitext(name)[0]
            if stem not in _DEFAULT_TEMPLATES:
                continue
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                reply = QMessageBox.question(
                    self, "File exists",
                    "%s already exists.\n\nOverwrite with defaults?" % name,
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel)
                if reply == QMessageBox.StandardButton.Cancel:
                    cancelled = True
                    break
                if reply == QMessageBox.StandardButton.Yes:
                    overwrite.add(name)

        if cancelled:
            return

        try:
            # If config_name is None, pass 'config.yaml' — it already exists
            # so init_default_files will skip it automatically
            created, skipped, overwritten = init_default_files(
                directory,
                config_name=config_name or 'config.yaml',
                overwrite=overwrite)
        except OSError as e:
            QMessageBox.warning(self, "Error",
                                "Could not create files:\n%s" % e)
            return

        # Build report
        lines = []
        if created:
            lines.append("Created:")
            for name, kind in created:
                lines.append("  %s" % name)
        if overwritten:
            lines.append("Overwritten:")
            for name, kind in overwritten:
                lines.append("  %s" % name)
        if skipped:
            lines.append("Skipped:")
            for name, reason in skipped:
                lines.append("  %s" % name)
        if not lines:
            lines.append("Nothing to do — all files already exist.")
        QMessageBox.information(self, "Init Directory", '\n'.join(lines))

    def _try_external(self, path):
        """If an external editor is configured, open the file in it.
        Returns True if launched, False otherwise."""
        editor = self._get_external_editor()
        if not editor:
            return False
        # Ensure file exists (create empty if needed for new files)
        if not os.path.isfile(path):
            directory = os.path.dirname(path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                pass
        if editor == '__system__':
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return True
        if not os.path.isfile(editor):
            QMessageBox.warning(self, "Error",
                                "External editor not found:\n%s" % editor)
            return False
        try:
            subprocess.Popen([editor, path])
        except Exception as e:
            QMessageBox.warning(self, "Error",
                                "Could not launch external editor:\n%s" % e)
            return False
        return True

    def _restore_defaults(self):
        """Replace the editor contents with the default template."""
        key = self._current_quick_key()
        if not key:
            return
        from qtpyrc import _resolve_template
        template = _resolve_template(key)
        if not template:
            return
        reply = QMessageBox.question(
            self, "Restore Defaults",
            "Replace the current contents with the default %s template?\n\n"
            "This will not save automatically — you can review the changes first."
            % key,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.editor.setPlainText(template)

    def _save_to_disk(self):
        """Write editor contents to disk."""
        if not self._path:
            return False
        directory = os.path.dirname(self._path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        # Temporarily stop watching so our own save doesn't trigger a reload prompt
        was_watched = self._path in self._watcher.files()
        if was_watched:
            self._watcher.removePath(self._path)
        try:
            with open(self._path, 'w', encoding='utf-8') as f:
                f.write(self.editor.toPlainText())
            return True
        except Exception as e:
            QMessageBox.warning(self, "Error", "Could not save file:\n%s" % e)
            return False
        finally:
            if was_watched and os.path.isfile(self._path):
                self._watcher.addPath(self._path)

    def _on_text_changed(self):
        """Update the path label when the user edits the file."""
        if self._path and not self._loading:
            self._dirty = True
            self._path_label.setText(os.path.abspath(self._path) + "  (unsaved)")

    def _save(self):
        """Save button handler."""
        if self._save_to_disk():
            self._dirty = False
            self._path_label.setText(self._path + "  (saved)")

    def _apply_from_text(self):
        """Apply the editor's current text to the running application.
        Returns True if something was applied."""
        key = self._current_quick_key()
        text = self.editor.toPlainText()

        if key == 'config':
            self._apply_config_text(text)
            return True
        elif key == 'popups':
            import popups
            popups._popups = popups._load_sections_text(text)
            return True
        elif key == 'toolbar':
            from toolbar import _parse_toolbar, reload_toolbar_from_entries
            entries = _parse_toolbar(text)
            reload_toolbar_from_entries(entries)
            return True
        elif key == 'variables':
            state.load_variables_text(text)
            return True
        elif key == 'startup':
            self._rerun_startup()
            return True

        # Unknown file type — try by extension
        path_lower = self._path.replace('\\', '/').lower() if self._path else ''
        if path_lower.endswith('.yaml') or path_lower.endswith('.yml'):
            self._apply_config_text(text)
            return True
        return False

    def _apply_only(self):
        """Apply changes without saving to disk."""
        if self._apply_from_text():
            self._path_label.setText(
                os.path.abspath(self._path) + "  (applied, not saved)")

    def _save_and_reload(self):
        """Save the file and reload/re-run it in the application."""
        if not self._save_to_disk():
            return

        path_lower = self._path.replace('\\', '/').lower() if self._path else ''
        reloaded = False

        # Check which quick file this is
        for key, qpath in self._quick_files.items():
            if qpath and qpath.replace('\\', '/').lower() == path_lower:
                if key == 'config':
                    self._reload_config()
                    reloaded = True
                elif key == 'popups':
                    import popups
                    popups.load()
                    reloaded = True
                elif key == 'toolbar':
                    from toolbar import reload_toolbar
                    reload_toolbar()
                    reloaded = True
                elif key == 'variables':
                    state.load_variables()
                    reloaded = True
                elif key == 'startup':
                    self._rerun_startup()
                    reloaded = True
                break

        if not reloaded:
            # Try to detect by extension
            if path_lower.endswith('.yaml') or path_lower.endswith('.yml'):
                self._reload_config()

        self._dirty = False
        self._path_label.setText(self._path + "  (saved & applied)")

    def _reload_config(self):
        """Reload the configuration from disk."""
        from config import loadconfig
        try:
            cfg = loadconfig(state.config.path)
            state.config = cfg
        except Exception as e:
            QMessageBox.warning(self, "Reload Error",
                                "Could not reload config:\n%s" % e)

    def _apply_config_text(self, text):
        """Apply config from editor text without saving to disk."""
        from config import loadconfig_text
        try:
            cfg = loadconfig_text(text, state.config.path)
            state.config = cfg
        except Exception as e:
            QMessageBox.warning(self, "Apply Error",
                                "Could not apply config:\n%s" % e)

    def _rerun_startup(self):
        """Re-run the startup script."""
        from commands import run_script
        win = None
        if state.clients:
            win = next(iter(state.clients)).window
        run_script(self._path, win)
