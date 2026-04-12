from __future__ import annotations

import os
import shutil
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPTS = PACKAGE_DIR / "default_prompts"
DEFAULT_KB = PACKAGE_DIR / "default_kb"


def user_data_root() -> Path:
    raw = os.environ.get("AIWRITER_USER_DATA", "").strip()
    if raw:
        return Path(raw).resolve()
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
