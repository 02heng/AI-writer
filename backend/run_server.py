"""Frozen entry point: uvicorn(app.main:app).

开发环境继续使用 `python -m uvicorn app.main:app`；打包后为 PyInstaller --onedir。
"""
from __future__ import annotations

import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()

    import os
    import sys
    from pathlib import Path

    # Frozen（PyInstaller onedir）：_MEIPASS 指向 _internal，内含 app 包
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            root = Path(meipass)
        else:
            root = Path(sys.executable).resolve().parent
        os.chdir(root)
        pr = str(root)
        if pr not in sys.path:
            sys.path.insert(0, pr)

    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

    os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")

    import uvicorn

    host = os.environ.get("AIWRITER_UVICORN_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_s = (
        os.environ.get("AIWRITER_BACKEND_PORT", "").strip()
        or os.environ.get("BACKEND_PORT", "").strip()
        or "18765"
    )
    port = int(port_s)
    log_level = os.environ.get("AIWRITER_LOG_LEVEL", "info").strip().lower() or "info"

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
