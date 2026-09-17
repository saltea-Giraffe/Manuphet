# -*- mode: python ; coding: utf-8 -*-
#
# Manuphet_Web.exe ビルド用 PyInstaller スペック
#
# ビルド方法（project/ ディレクトリで実行）:
#   pyinstaller installer\run_web.spec --distpath installer\dist --workpath installer\build
#
# 出力: installer\dist\Manuphet_Web.exe

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent  # project/
ICON = ROOT / 'assets' / 'manuphet.ico'


def _collect_extra_dlls():
    """conda 環境で PyInstaller が取りこぼす DLL を明示的に同梱する。"""
    env = Path(sys.executable).parent
    candidates = []
    patterns = {
        env / 'DLLs': ['ffi*.dll', 'libffi*.dll', '_ctypes*.pyd', 'pyexpat*.pyd', '_sqlite3*.pyd'],
        env / 'Library' / 'bin': ['ffi*.dll', 'libffi*.dll', 'libexpat*.dll', 'expat*.dll',
                                  'sqlite3.dll', 'liblzma.dll', 'libbz2.dll', 'LIBBZ2.dll'],
        env: ['ffi*.dll'],
    }
    for search_dir, pats in patterns.items():
        if search_dir.exists():
            for pat in pats:
                for f in search_dir.glob(pat):
                    candidates.append((str(f), '.'))
    return candidates


a = Analysis(
    [str(ROOT / 'run_web.py')],
    pathex=[str(ROOT)],
    binaries=_collect_extra_dlls(),
    datas=[
        (str(ROOT / 'web'),         'web'),
        (str(ROOT / 'assets' / 'manuphet.ico'),    'assets'),
        (str(ROOT / 'assets' / 'manuphet.png'),    'assets'),
        (str(ROOT / 'assets' / 'manuphet_64.png'), 'assets'),
        (str(ROOT / 'examples'),    'examples'),
        (str(ROOT / 'version.txt'), '.'),
    ] + collect_data_files('xgboost'),
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'fastapi.routing',
        'starlette',
        'starlette.routing',
        'starlette.responses',
        'anyio',
        'anyio._backends._asyncio',
        'pandas',
        'numpy',
        'xgboost',
        'sklearn',
        'sklearn.utils._cython_blas',
        'sklearn.neighbors._quad_tree',
        'sklearn.tree._utils',
        'sqlite3',
        'sqlalchemy',
        'sqlalchemy.dialects.mysql',
        'sqlalchemy.dialects.mysql.mysqlconnector',
        'sqlalchemy.dialects.mysql.pymysql',
        'mysql',
        'mysql.connector',
        'mysql.connector.plugins',
        'mysql.connector.plugins.mysql_native_password',
        'mysql.connector.plugins.caching_sha2_password',
        'pymysql',
        'config',
        'manuphet_web',
        'api',
        'api.service',
        'api.routes',
        'data_store',
        'mysql_importer',
        'email_notifier',
        'model',
        'model.calendar',
        'model.metrics',
        'model.transforms',
        'model.features',
        'model.trainer',
        'model.evaluator',
        'model.store',
        'model_handler',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'IPython', 'jupyter', 'notebook', 'pytest'],
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
    name='Manuphet_Web',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON),
    version=None,
)
