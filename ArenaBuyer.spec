# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


def collect_dir_tree(source_dir: Path, target_root: str):
    rows = []
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        rel_parent = path.relative_to(source_dir).parent
        dest = Path(target_root) / rel_parent
        rows.append((str(path), str(dest).replace("\\", "/")))
    return rows


datas = []
datas += collect_data_files('super_buyer.resources.images')
datas += collect_data_files('super_buyer.resources.assets')
datas += collect_data_files('super_buyer.resources.defaults')

umi_candidates = []
env_umi_dir = os.environ.get('UMI_OCR_SOURCE_DIR', '').strip()
if env_umi_dir:
    umi_candidates.append(Path(env_umi_dir))
umi_candidates.append(Path('Umi-OCR_Paddle_v2.1.5'))
umi_candidates.append(Path('Umi-OCR'))

for umi_dir in umi_candidates:
    try:
        if umi_dir.exists() and (umi_dir / 'Umi-OCR.exe').exists():
            datas += collect_dir_tree(umi_dir, 'Umi-OCR')
            break
    except Exception:
        pass

icon_path = Path('tools') / 'bin' / 'app_icon.ico'
exe_kwargs = {}
if icon_path.exists():
    exe_kwargs['icon'] = [str(icon_path)]


a = Analysis(
    ['src\\super_buyer\\__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=['pymsgbox', 'pyscreeze', 'pytweening', 'pyrect', 'mouseinfo'],
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
    **exe_kwargs,
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
