"""Plugins live on a search path: the profile's directory, then the app's own.

A profile's `plugins.dir` used to be the *only* place plugins were looked for,
so `qtpyrc --config me/config.yaml` could not see the plugins shipped in the
application's own `plugins/` directory at all.  The way that was papered over
was to copy them into the profile at creation time -- which turns every shipped
plugin into a silent fork that stops receiving updates the moment either copy
changes.  The reported symptoms were both downstream of it:

  * a plugin added to `plugins.auto_load` but only present in the application's
    directory never loaded, so it never registered its `config_fields`, so it
    had no page in the settings tree; and
  * the settings dialog classified that same name as an "external" plugin (in
    auto_load but not in the directory) and drew its row as a composite widget
    rather than a plain list item, which indented it out of line with the rest.

Both are one fact: the loader was looking in one directory.  Now
`plugins.plugin_search_path()` is the profile's directory followed by the
application's, `find_plugin` resolves a name against it in order, and
`available_plugins` reports everything reachable on it -- which is what both the
settings dialog and `/plugins` list, so what you are shown is what would load.

The properties under test, each of which was once false:

  1. a plugin present only in the application's directory is found;
  2. a plugin present only in the profile's directory is found;
  3. a name present in both resolves to the profile's copy, and the shadowed
     one is still reported (so it can be seen and deleted);
  4. `available_plugins` lists the union, one entry per name, each naming the
     file that would actually load;
  5. a bare wildcard in auto_load expands across the whole search path;
  6. a plugin actually loads, and reloading it does not accumulate duplicate
     command/hotkey registrations;
  7. a name found nowhere fails cleanly rather than raising.

Runs headless (offscreen Qt platform), so it needs no display.

Usage:
  python tests/test_plugin_search_path.py    # from the qtpyrc root directory
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from ruamel.yaml import YAML
from PySide6.QtWidgets import QApplication, QWidget

import config as configmod
import state

failures = []


def check(cond, msg):
  if not cond:
    failures.append(msg)


# A plugin body that registers a command and a hotkey, so that a reload can be
# seen to replace its registrations rather than add to them.
PLUGIN_SRC = '''
import plugin

MARK = %r

class Class(plugin.Callbacks):
  def __init__(self, irc):
    self.irc = irc
    irc.add_command(%r, self.cmd)
    irc.bind_key(%r, self.key)
  def cmd(self, irc, window, text):
    pass
  def key(self, irc, window):
    pass
'''


def write_plugin(directory, name, mark, command, hotkey):
  os.makedirs(directory, exist_ok=True)
  with open(os.path.join(directory, name + '.py'), 'w', encoding='utf-8') as f:
    f.write(PLUGIN_SRC % (mark, command, hotkey))


def main(tmp):
  # The application's plugin directory is a real, fixed location, so the test
  # writes its "shipped" plugin into it and removes it again.  Pointing the
  # search path at two temporary directories instead would test a search path
  # that is not the one qtpyrc uses -- the whole bug was in how the second
  # entry is derived, so the second entry has to be the real one.
  import plugins

  app_dir = plugins.app_plugin_dir()
  profile_dir = os.path.join(tmp, 'plugins')
  os.makedirs(profile_dir, exist_ok=True)

  shipped = os.path.join(app_dir, 'zz_test_shipped.py')
  both_app = os.path.join(app_dir, 'zz_test_both.py')
  created = [shipped, both_app]

  write_plugin(app_dir, 'zz_test_shipped', 'app', 'zzshipped', 'Ctrl+Alt+F9')
  write_plugin(app_dir, 'zz_test_both', 'app', 'zzboth', 'Ctrl+Alt+F10')
  write_plugin(profile_dir, 'zz_test_profile', 'profile', 'zzprofile',
               'Ctrl+Alt+F11')
  write_plugin(profile_dir, 'zz_test_both', 'profile', 'zzboth',
               'Ctrl+Alt+F10')

  try:
    # A config in the temp directory, so `plugins.dir` resolves to profile_dir.
    cfg_path = os.path.join(tmp, 'config.yaml')
    with open(cfg_path, 'w', encoding='utf-8') as f:
      f.write('nick: tester\nplugins:\n  auto_load:\n  - zz_test_*\n')
    yaml = YAML()
    with open(cfg_path, encoding='utf-8') as f:
      data = yaml.load(f)
    state.config = configmod.AppConfig(cfg_path, data, yaml)
    state.activescripts = {}

    path = plugins.plugin_search_path()

    # -- 1/2/3: resolution order --
    check(len(path) == 2,
          'the search path has %d entries, not two (profile then application): '
          '%r' % (len(path), path))
    check(path and os.path.normcase(os.path.abspath(path[0]))
          == os.path.normcase(os.path.abspath(profile_dir)),
          'the profile directory is not first on the search path: %r' % (path,))
    check(len(path) > 1 and os.path.normcase(os.path.abspath(path[1]))
          == os.path.normcase(os.path.abspath(app_dir)),
          "the application's own plugin directory is not on the search path: "
          '%r' % (path,))

    f_shipped = plugins.find_plugin('zz_test_shipped')
    check(f_shipped is not None,
          'a plugin present only in the application directory was not found -- '
          'this is the reported bug: the profile directory shadowed the '
          'shipped one with no fallback')
    check(f_shipped is not None and os.path.dirname(f_shipped.path)
          == app_dir,
          'zz_test_shipped resolved somewhere other than the application '
          'directory')

    f_profile = plugins.find_plugin('zz_test_profile')
    check(f_profile is not None and os.path.dirname(f_profile.path)
          == profile_dir,
          'a plugin present only in the profile directory was not found there')

    f_both = plugins.find_plugin('zz_test_both')
    check(f_both is not None and os.path.dirname(f_both.path) == profile_dir,
          'a name present in both directories did not resolve to the '
          "profile's copy -- the profile has to win, or a deliberate override "
          'is impossible')

    # -- 4: the union, one entry per name --
    avail = plugins.available_plugins()
    for name in ('zz_test_shipped', 'zz_test_profile', 'zz_test_both'):
      check(name in avail, '%s is missing from available_plugins()' % name)
    check(avail.get('zz_test_both') is not None
          and avail['zz_test_both'].path == f_both.path,
          'available_plugins() and find_plugin() disagree about which copy of '
          'zz_test_both wins; the settings dialog lists the first and the '
          'loader uses the second, so what you see would not be what loads')
    only_app = plugins.available_plugins([app_dir])
    check('zz_test_both' in only_app
          and only_app['zz_test_both'].path != f_both.path,
          'the shadowed copy is not reachable at all, so nothing can report it')

    # -- 5: wildcards expand across the whole path --
    expanded = plugins._expand_auto_load(['zz_test_*'], path, '.py')
    check(sorted(expanded) == ['zz_test_both', 'zz_test_profile',
                               'zz_test_shipped'],
          'a bare wildcard in auto_load expanded to %r; it must cover every '
          'directory on the search path, and list a name shadowed in one of '
          'them exactly once' % (sorted(expanded),))

    # -- 6/7: loading and reloading --
    import plugin as plugin_api
    plugin_api.irc._init(get_active_window=lambda: None,
                         get_networks=lambda: [])
    loaded = plugins.loadscripts()
    state.activescripts = dict(loaded)
    check(sorted(loaded) == ['zz_test_both', 'zz_test_profile',
                             'zz_test_shipped'],
          'auto_load loaded %r' % (sorted(loaded),))
    check(loaded.get('zz_test_both') is not None
          and getattr(loaded['zz_test_both'].module, 'MARK', None) == 'profile',
          'the loaded zz_test_both is not the profile copy, so the module '
          'executed is not the file find_plugin named')

    before_cmds = sorted(state.plugin_commands)
    before_keys = sorted(state.plugin_keys)
    check(plugins.load_script_by_name('zz_test_shipped'),
          'reloading a plugin found in the application directory failed')
    check(sorted(state.plugin_commands) == before_cmds
          and sorted(state.plugin_keys) == before_keys,
          'reloading changed the registrations: commands %r -> %r, hotkeys '
          '%r -> %r' % (before_cmds, sorted(state.plugin_commands),
                        before_keys, sorted(state.plugin_keys)))

    check(plugins.find_plugin('zz_test_nowhere') is None,
          'a name present in neither directory was somehow found')
    check(plugins.load_script_by_name('zz_test_nowhere') is False,
          'loading a name present in neither directory did not fail cleanly')

  finally:
    for name in list(state.activescripts or {}):
      if name.startswith('zz_test_'):
        plugins.unload_plugin(name)
    for path_ in created:
      if os.path.isfile(path_):
        os.remove(path_)
    shutil.rmtree(os.path.join(app_dir, '__pycache__'), ignore_errors=True)


app = QApplication([])
state.app = app
app.mainwin = QWidget()

tmpdir = tempfile.mkdtemp(prefix='qtpyrc_pluginpath_')
try:
  main(tmpdir)
except Exception:
  import traceback
  traceback.print_exc()
  failures.append('the test itself raised (see traceback above)')
finally:
  shutil.rmtree(tmpdir, ignore_errors=True)

if failures:
  print('\nFAILED (%d):' % len(failures))
  for f in failures:
    print('  - %s' % f)
  sys.exit(1)

print('All plugin search-path checks passed: profile first, application '
      'second, shadowed copies visible.')
sys.exit(0)
