"""
cli.py  –  Manuphet コマンドラインツール

他システム（バッチ・PHP・スクリプト等）から予測結果を取得したり、
データを登録したりするためのコマンド。結果は JSON で標準出力に出す。

使い方:
  python cli.py summary
  python cli.py list
  python cli.py predict --item ITEM-001 --months 6
  python cli.py batch --items ITEM-001,ITEM-002 --months 3
  python cli.py train --item ITEM-001
  python cli.py train-all
  python cli.py import-csv --target demand --file demand.csv [--mode append|replace_source|replace_all]
  python cli.py load-sample
  python cli.py sync-mysql

データ保存先などの設定は config.py（MANUPHET_* 環境変数 / settings.json）に従う。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _service():
    from api.service import ManuphetService
    return ManuphetService(start_background=False)


def _predict(svc, item_code: str, months: int) -> dict[str, Any]:
    if item_code not in svc.list_items(include_excluded=True):
        return {"success": False, "item_code": item_code, "error": "需要実績がありません"}
    mode = svc.item_mode(item_code)
    consumption = svc.model_handler.predict_consumption(svc._demand(), item_code, months, mode)
    if consumption is None:
        return {"success": False, "item_code": item_code, "mode": mode,
                "error": "学習済みモデルがありません（train を実行してください）"}
    inventory = svc.store.get_inventory(item_code)
    monthly = consumption / months if months > 0 else 0.0
    return {
        "success": True,
        "item_code": item_code,
        "mode": mode,
        "months": months,
        "predicted_consumption": consumption,
        "current_inventory": inventory,
        "months_left": (inventory / monthly) if (inventory is not None and monthly > 0) else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manuphet CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="登録データの件数を表示")
    sub.add_parser("list", help="品目コード一覧")

    p = sub.add_parser("predict", help="1 品目の予測需要")
    p.add_argument("--item", required=True)
    p.add_argument("--months", type=int, default=6)

    p = sub.add_parser("batch", help="複数品目の予測需要")
    p.add_argument("--items", required=True, help="カンマ区切り")
    p.add_argument("--months", type=int, default=6)

    p = sub.add_parser("train", help="1 品目を学習")
    p.add_argument("--item", required=True)

    sub.add_parser("train-all", help="全品目を学習（除外品目を除く）")

    p = sub.add_parser("import-csv", help="CSV を登録")
    p.add_argument("--target", required=True, choices=["demand", "inventory", "bom"])
    p.add_argument("--file", required=True)
    p.add_argument("--mode", default="append", choices=["append", "replace_source", "replace_all"])

    sub.add_parser("load-sample", help="サンプルデータを登録")
    sub.add_parser("sync-mysql", help="保存済みの MySQL 取込定義をすべて実行")

    args = parser.parse_args(argv)

    try:
        svc = _service()
        if args.command == "summary":
            result: Any = {"success": True, **svc.store.summary()}
        elif args.command == "list":
            items = svc.list_items()
            result = {"success": True, "items": items, "count": len(items)}
        elif args.command == "predict":
            result = _predict(svc, args.item.strip(), args.months)
        elif args.command == "batch":
            codes = [c.strip() for c in args.items.split(",") if c.strip()]
            rows = [_predict(svc, c, args.months) for c in codes]
            result = {
                "success": True,
                "results": {r["item_code"]: r for r in rows if r["success"]},
                "errors": [r for r in rows if not r["success"]],
                "total": len(rows),
            }
        elif args.command == "train":
            result = {"success": True, **svc.train_one(args.item.strip())}
        elif args.command == "train-all":
            done, failed = [], []
            for code in svc.list_items():
                try:
                    done.append(svc.train_one(code))
                except Exception as e:
                    failed.append({"item_code": code, "error": str(e)})
            result = {"success": not failed, "trained": len(done), "failed": failed}
        elif args.command == "import-csv":
            path = Path(args.file)
            text = path.read_text(encoding="utf-8-sig")
            result = {"success": True, **svc.import_csv(args.target, text, args.mode, path.name)}
        elif args.command == "load-sample":
            result = {"success": True, "result": svc.load_sample()}
        else:  # sync-mysql
            result = svc.mysql_sync_all()
            result["success"] = result.pop("ok")
    except Exception as e:
        result = {"success": False, "error": str(e), "error_type": type(e).__name__}

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
