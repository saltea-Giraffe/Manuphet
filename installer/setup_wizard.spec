# -*- mode: python ; coding: utf-8 -*-
#
# Manuphet_Setup_Wizard.exe ビルド用 PyInstaller スペック
#
# ビルド方法（project/ ディレクトリで実行）:
#   pyinstaller installer\setup_wizard.spec --distpath installer\dist --workpath installer\build
#
# 出力: installer\dist\Manuphet_Setup_Wizard.exe

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # project/
ICON = ROOT / 'assets' / 'manuphet.ico'


# conda 環境では libffi・_tkinter・tcl/tk DLL が Library\bin や DLLs にあり、
# PyInstaller が自動収集しないため明示的にバンドルする。
def _collect_conda_dlls():
    env = Path(sys.executable).parent
    bins = []
    for search_dir in [env / 'DLLs', env / 'Library' / 'bin', env]:
        if not search_dir.exists():
            continue
        for pat in ['ffi*.dll', 'libffi*.dll', '_ctypes*.pyd',
                    '_tkinter*.pyd', 'tcl8*.dll', 'tk8*.dll', 'tcl86*.dll', 'tk86*.dll']:
            for f in search_dir.glob(pat):
                bins.append((str(f), '.'))
    return bins


def _collect_tcl_data():
    env = Path(sys.executable).parent
    datas = []
    lib_dir = env / 'Library' / 'lib'
    if not lib_dir.exists():
        lib_dir = env / 'lib'
    for name in ('tcl8.6', 'tk8.6'):
        d = lib_dir / name
        if d.exists():
            datas.append((str(d), name))
    return datas


a = Analysis(
    [str(ROOT / 'setup_wizard.py')],
    pathex=[str(ROOT)],
    binaries=_collect_conda_dlls(),
    datas=_collect_tcl_data() + [(str(ICON), 'assets')],
    hiddenimports=['tkinter', 'tkinter.filedialog', 'tkinter.messagebox', '_tkinter'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'numpy', 'xgboost', 'sklearn', 'fastapi', 'uvicorn', 'sqlalchemy', 'matplotlib'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Manuphet_Setup_Wizard',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON),
    version=None,
)
