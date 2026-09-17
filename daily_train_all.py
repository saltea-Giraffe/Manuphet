"""daily_train_all.py

全品目（除外品目を除く）を一括学習するバッチ。タスクスケジューラ等から定期実行する。

 - 保存済みの MySQL 取込定義があれば、学習前に最新データへ同期する（--no-sync で無効）
 - 週次/月次の振り分けは「品目設定」タブ（weekly_items.json）に従う
 - ログは LOG_DIR/daily_train_YYYYMMDD.log に追記する

実行例:
  python daily_train_all.py
  python daily_train_all.py --no-sync
"""

from __future__ import annotations

import argparse
from datetime import datetime


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log_write(fp, msg: str) -> None:
    fp.write(f"[{_now_str()}] {msg}\n")
    fp.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manuphet 一括学習バッチ")
    parser.add_argument("--no-sync", action="store_true", help="MySQL 取込定義の同期を行わない")
    args = parser.parse_args()

    import config
    from api.service import ManuphetService

    config.ensure_dirs()
    log_path = config.LOG_DIR / f"daily_train_{datetime.now().strftime('%Y%m%d')}.log"

    with log_path.open("a", encoding="utf-8") as fp:
        _log_write(fp, "==== daily_train_all START ====")
        _log_write(fp, f"DB={config.DB_PATH}")
        _log_write(fp, f"MODELS_DIR={config.MODELS_DIR}")

        svc = ManuphetService(start_background=False)

        if not args.no_sync and svc.store.list_jobs():
            sync = svc.mysql_sync_all()
            for r in sync["results"]:
                _log_write(fp, f"mysql sync: {r['job']} imported={r['imported']} skipped={r['skipped']}")
            for e in sync["errors"]:
                _log_write(fp, f"[WARN] mysql sync failed: {e['job']} -> {e['error']}")

        items = svc.list_items()
        _log_write(fp, f"targets={len(items)} excluded={len(svc.get_excluded_set())} "
                       f"weekly={len(svc.get_weekly_set())}")

        ok = ng = 0
        failed: list[str] = []
        for i, code in enumerate(items, start=1):
            try:
                svc.train_one(code)
                ok += 1
            except Exception as e:
                ng += 1
                failed.append(code)
                _log_write(fp, f"[WARN] train failed: {code} ({svc.item_mode(code)}) -> {e}")
            if i % 10 == 0:
                _log_write(fp, f"progress {i}/{len(items)} ok={ok} ng={ng}")

        _log_write(fp, f"DONE ok={ok} ng={ng}")
        if failed:
            _log_write(fp, "FAILED LIST: " + ", ".join(failed[:100]) + (" ..." if len(failed) > 100 else ""))
        _log_write(fp, "==== daily_train_all END ====")

    # バッチ運用上は部分失敗でも 0 を返し、スケジューラ側の停止連鎖を避ける
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
