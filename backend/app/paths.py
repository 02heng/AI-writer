from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPTS = PACKAGE_DIR / "default_prompts"
DEFAULT_KB = PACKAGE_DIR / "default_kb"

# 与 electron/paths.cjs 一致：无 AIWRITER_USER_DATA 时在 Windows 上优先 D:/AI-writer-data/UserData
_WIN_DATA_CANDIDATES = (
    Path("D:/AI-writer-data/UserData"),
    Path("E:/AI-writer-data/UserData"),
)


def _windows_preferred_user_data() -> Path | None:
    """若存在 D: 或 E: 盘符，则使用对应 AI-writer-data 下的 UserData，并自动创建目录。"""
    if os.name != "nt" or "pytest" in sys.modules:
        return None
    if os.environ.get("AIWRITER_USE_CWD_USER_DATA", "").strip().lower() in ("1", "true", "yes"):
        return None
    for candidate in _WIN_DATA_CANDIDATES:
        drive = candidate.drive
        if not drive:
            continue
        drive_root = Path(drive + "/")
        try:
            if not drive_root.exists():
                continue
        except OSError:
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate.resolve()
        except OSError:
            continue
    return None


def user_data_root() -> Path:
    raw = os.environ.get("AIWRITER_USER_DATA", "").strip()
    if raw:
        return Path(raw).resolve()
    preferred = _windows_preferred_user_data()
    if preferred is not None:
        return preferred
    return Path.cwd()


def ensure_layout(root: Path) -> None:
    (root / "kb").mkdir(parents=True, exist_ok=True)
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(parents=True, exist_ok=True)
    (root / "memory").mkdir(parents=True, exist_ok=True)
    prompts = root / "prompts"
    if DEFAULT_PROMPTS.is_dir():
        for f in DEFAULT_PROMPTS.glob("*.md"):
            dest = prompts / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
        mf = DEFAULT_PROMPTS / "manifest.json"
        if mf.is_file():
            dest_m = prompts / "manifest.json"
            if not dest_m.exists():
                shutil.copy2(mf, dest_m)
    kb = root / "kb"
    if DEFAULT_KB.is_dir():
        for f in DEFAULT_KB.glob("*.md"):
            dest = kb / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
