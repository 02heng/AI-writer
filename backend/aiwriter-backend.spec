# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec: 生成 backend/dist/aiwriter-backend/（onedir）
在 backend 目录执行: python -m PyInstaller --noconfirm --clean aiwriter-backend.spec
"""

import pathlib
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

WORK = pathlib.Path(SPECPATH)
if str(WORK) not in sys.path:
    sys.path.insert(0, str(WORK))

try:
    # run_server.py 仅以字符串传给 uvicorn，Analysis 若不显式打入则冻结版无法 import app.main
    _app_hidden = list(collect_submodules("app"))
except Exception:
    _app_hidden = ["app.main"]

def _merge_collect(*names):
    datas, binaries, hidden = [], [], []
    for n in names:
        d, b, h = collect_all(n)
        datas += d
        binaries += b
        hidden += h
    return datas, binaries, hidden


_pkg_datas, _pkg_bins, _pkg_hidden = _merge_collect(
    "chromadb",
    "uvicorn",
    "starlette",
    "fastapi",
    "pydantic",
    "openai",
    "httpx",
    "anyio",
    "jsonschema",
    "dotenv",
)

datas = _pkg_datas + [
    (str(WORK / "app" / "data" / "themes.json"), "app/data"),
    (str(WORK / "app" / "default_prompts"), "app/default_prompts"),
    (str(WORK / "app" / "default_kb"), "app/default_kb"),
]

binaries = _pkg_bins

hiddenimports = _pkg_hidden + _app_hidden + [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "multipart",
    "python_multipart",
]

a = Analysis(
    [str(WORK / "run_server.py")],
    pathex=[str(WORK)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "_pytest",
        "pytest_cov",
        "tkinter",
        "matplotlib",
        "IPython",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="aiwriter-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="aiwriter-backend",
)
