"""
evaluate_models.py  –  予測精度の一括評価（ウォークフォワード）

Usage:
    python evaluate_models.py
    python evaluate_models.py --top-n 30 --selection random --train-missing
"""
import argparse
from datetime import datetime

import numpy as np
import pandas as pd


def _select_items(demand: pd.DataFrame, top_n: int, selection: str, seed: int) -> list:
    totals = (
        demand.groupby("item_code", as_index=False)["quantity"]
        .sum()
        .sort_values("quantity", ascending=False)
    )
    all_items = [c for c in totals["item_code"].tolist() if str(c).strip()]
    if not all_items:
        return []
    n = max(1, min(int(top_n), len(all_items)))
    if selection == "random":
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(all_items), size=n, replace=False)
        return [all_items[i] for i in idx]
    return all_items[:n]


def _format_float(x):
    try:
        return f"{float(x):.4f}"
    except Exception:
        return ""


def parse_args():
    p = argparse.ArgumentParser(description="Evaluate Manuphet weekly/monthly walk-forward metrics.")
    p.add_argument("--top-n", type=int, default=20, help="評価する品目数（需要量の多い順）")
    p.add_argument("--selection", choices=["top", "random"], default="top", help="品目の選び方")
    p.add_argument("--seed", type=int, default=42, help="random 選択時の乱数シード")
    p.add_argument("--weekly-test-weeks", type=int, default=12, help="週次の評価期間")
    p.add_argument("--monthly-test-months", type=int, default=12, help="月次の評価期間")
    p.add_argument("--train-missing", action="store_true", help="モデルが無い品目は先に学習する")
    p.add_argument("--output", default="", help="出力 CSV（既定: LOG_DIR/evaluation_results_YYYYmmdd_HHMMSS.csv）")
    return p.parse_args()


def main():
    args = parse_args()

    import config
    from api.service import ManuphetService

    svc = ManuphetService(start_background=False)
    mh = svc.model_handler
    demand = svc._demand()
    excluded = svc.get_excluded_set()
    demand = demand[~demand["item_code"].isin(excluded)]

    items = _select_items(demand, args.top_n, args.selection, args.seed)
    if not items:
        print("[ERROR] 需要実績が登録されていません。")
        return 1
    print(f"[INFO] evaluate target count = {len(items)} (selection={args.selection})")

    rows = []
    for i, code in enumerate(items, start=1):
        mode = svc.item_mode(code)
        model_exists = mh.has_model(code, mode)
        print(f"[{i:03d}/{len(items):03d}] {code} mode={mode} model_exists={model_exists}")
        try:
            if args.train_missing and not model_exists:
                svc.train_one(code)
            test_periods = args.weekly_test_weeks if mode == "weekly" else args.monthly_test_months
            res = mh.evaluate_walk_forward(demand, code, mode, test_periods=test_periods)
            rows.append({
                "item_code": code, "mode": mode,
                "train_size": int(res.get("train_size", 0)), "test_size": int(res.get("test_size", 0)),
                "rmse": float(res.get("rmse", np.nan)), "mae": float(res.get("mae", np.nan)),
                "smape": float(res.get("smape", np.nan)), "use_log1p": bool(res.get("use_log1p", False)),
                "model_exists_before_eval": bool(model_exists), "error": "",
            })
        except Exception as e:
            rows.append({
                "item_code": code, "mode": mode, "train_size": 0, "test_size": 0,
                "rmse": np.nan, "mae": np.nan, "smape": np.nan, "use_log1p": False,
                "model_exists_before_eval": bool(model_exists), "error": str(e),
            })

    result_df = pd.DataFrame(rows)
    ok_df = result_df[result_df["error"] == ""]
    ng_df = result_df[result_df["error"] != ""]

    print("\n===== Evaluation Summary =====")
    print(f"total={len(result_df)} ok={len(ok_df)} ng={len(ng_df)}")
    if not ok_df.empty:
        summary = ok_df.groupby("mode")[["rmse", "mae", "smape"]].mean().reset_index().sort_values("mode")
        print("\n[Mean metrics by mode]")
        for _, r in summary.iterrows():
            print(f"mode={r['mode']:<7} RMSE={_format_float(r['rmse'])} "
                  f"MAE={_format_float(r['mae'])} sMAPE={_format_float(r['smape'])}")
        print("\n[Top 10 best by sMAPE]")
        show_cols = ["item_code", "mode", "rmse", "mae", "smape", "test_size", "use_log1p"]
        print(ok_df.sort_values("smape").head(10)[show_cols].to_string(index=False))

    if not ng_df.empty:
        print("\n[Errors]")
        print(ng_df[["item_code", "mode", "error"]].to_string(index=False))

    if args.output:
        out_csv = args.output
    else:
        config.ensure_dirs()
        out_csv = str(config.LOG_DIR / f"evaluation_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    result_df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n[INFO] saved: {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
