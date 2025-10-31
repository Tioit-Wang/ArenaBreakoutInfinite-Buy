# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = []
datas += collect_data_files('super_buyer.resources.images')
datas += collect_data_files('super_buyer.resources.assets')
datas += collect_data_files('super_buyer.resources.defaults')


a = Analysis(
    ['src\\super_buyer\\__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=['pymsgbox', 'pyscreeze', 'pytweening', 'PyRect', 'mouseinfo'],
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
    name='ArenaBuyer',
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
    name='ArenaBuyer',
)
