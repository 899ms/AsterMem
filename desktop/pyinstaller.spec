# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AsterMem server sidecar.

Usage (from repo root):
    pyinstaller desktop/pyinstaller.spec

Output goes to: desktop/src-tauri/binaries/
Rename the binary per Tauri sidecar naming convention before bundling:
  macOS arm64:   astermem-server-aarch64-apple-darwin
  macOS x86_64:  astermem-server-x86_64-apple-darwin
  Windows x64:   astermem-server-x86_64-pc-windows-msvc.exe
"""

import platform
import os

block_cipher = None
repo_root = os.path.dirname(os.path.abspath(SPECPATH))

a = Analysis(
    [os.path.join(repo_root, 'server.py')],
    pathex=[os.path.join(repo_root, 'backend')],
    binaries=[],
    datas=[
        (os.path.join(repo_root, 'web-ui', 'dist'), 'web-ui/dist'),
    ],
    hiddenimports=[
        'jieba',
        'jieba.posseg',
        'chromadb',
        'whoosh',
        'whoosh.analysis',
        'whoosh.index',
        'whoosh.qparser',
        'whoosh.fields',
        'sklearn',
        'sklearn.utils._cython_blas',
        'umap',
        'numpy',
        'PIL',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'httpx',
        'yaml',
        'dotenv',
        'frontmatter',
        'markdown',
        'aiosqlite',
        'watchdog',
        'watchdog.observers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy.spatial.transform',
        'IPython',
        'notebook',
        'pytest',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='astermem-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
