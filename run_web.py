"""run_web.py

Manuphet Web の起動スクリプト。

Usage:
  python   run_web.py          # 通常起動（コンソール表示あり）
  pythonw  run_web.py          # 隠し起動（タスクスケジューラから呼び出す）
"""

from __future__ import annotations

import logging
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path


def _setup_file_logging(log_path: Path) -> None:
    """stdout/stderr が使えない環境（pythonw / SYSTEM タスク）でログをファイルへ書く。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    # uvicorn / print() の出力もファイルへリダイレクト
    sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stderr = sys.stdout


def _write_startup_error(text: str) -> None:
    """起動時の例外を LOG_DIR（使えなければ一時フォルダ）の startup_error.log に残す。"""
    candidates = []
    try:
        import config
        candidates.append(config.LOG_DIR / "startup_error.log")
    except Exception:
        pass
    candidates.append(Path(tempfile.gettempdir()) / "manuphet_startup_error.log")
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
            return
        except OSError:
            continue


def main() -> None:
    import uvicorn

    import config

    # pythonw.exe / PyInstaller (console=False) 実行時は stdout/stderr が None
    no_console = (getattr(sys, "stdout", None) is None) or (getattr(sys, "stderr", None) is None)
    if no_console:
        _setup_file_logging(config.LOG_DIR / "service.log")

    # PyInstaller バンドル時は文字列参照が動作しないためオブジェクトを直接渡す
    from manuphet_web import app

    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level="info",
        log_config=None if no_console else uvicorn.config.LOGGING_CONFIG,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # ウィンドウなしで動く場合は例外が見えず、ダイアログで止まることもあるため
        # ログに残して終了コード 1 で終わる
        _write_startup_error(traceback.format_exc())
        if getattr(sys, "stderr", None) is not None:
            traceback.print_exc()
        sys.exit(1)
