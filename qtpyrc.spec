# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['qasync', 'ruamel.yaml', 'ruamel.yaml.clib']
hiddenimports += collect_submodules('ruamel.yaml')


a = Analysis(
    ['qtpyrc.py'],
    pathex=[],
    binaries=[],
    datas=[('defaults', 'defaults'), ('icons', 'icons'), ('plugins', 'plugins'), ('settings', 'settings'), ('docs', 'docs')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='qtpyrc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='qtpyrc',
)
