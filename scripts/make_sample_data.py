"""
scripts/make_sample_data.py  –  サンプルデータ（架空の品目）の生成

examples/sample_data/ に次の CSV を作る:
  demand.csv     需要実績（2023-01〜2025-12、営業日ごとの注文）
  inventory.csv  在庫（製品・部品）
  bom.csv        部品構成

乱数シードを固定しているため、何度実行しても同じ内容になる。

使い方（project/ で実行）:
  python scripts/make_sample_data.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from model.calendar import is_holiday  # noqa: E402

OUT = ROOT / "examples" / "sample_data"

# 品目ごとの 1 営業日あたり平均注文数・1 注文あたり数量・月別係数・年成長率
ITEMS = {
    "SAMPLE-A": {"orders": 1.0, "qty": (3, 12), "growth": 0.08,
                 "season": [0.8, 0.9, 1.4, 1.0, 0.9, 1.0, 1.0, 0.8, 1.3, 1.0, 1.1, 1.3]},
    "SAMPLE-B": {"orders": 2.5, "qty": (5, 30), "growth": 0.15,
                 "season": [0.9, 1.0, 1.2, 1.1, 1.0, 1.0, 0.9, 0.8, 1.1, 1.0, 1.1, 1.2]},
    "SAMPLE-C": {"orders": 0.3, "qty": (1, 4), "growth": -0.05,
                 "season": [1.0] * 12},
}
CUSTOMERS = [f"CUST-{i:02d}" for i in range(1, 9)]

INVENTORY = [
    ("SAMPLE-A", 120), ("SAMPLE-B", 3000), ("SAMPLE-C", 15),
    ("PART-A01", 300), ("PART-A02", 60), ("PART-B01", 4200),
    ("PART-B02", 700), ("PART-C01", 12), ("PART-X01", 1500),
]
BOM = [
    ("SAMPLE-A", "PART-A01", 1), ("SAMPLE-A", "PART-A02", 2), ("SAMPLE-A", "PART-X01", 4),
    ("SAMPLE-B", "PART-B01", 3), ("SAMPLE-B", "PART-B02", 1), ("SAMPLE-B", "PART-X01", 2),
    ("SAMPLE-C", "PART-C01", 1), ("SAMPLE-C", "PART-C02", 1),
]


def main() -> int:
    rng = np.random.default_rng(20240101)
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    days = pd.date_range("2023-01-01", "2025-12-31", freq="D")
    for day in days:
        if is_holiday(day):
            continue
        years = (day - days[0]).days / 365.0
        for item, spec in ITEMS.items():
            lam = spec["orders"] * spec["season"][day.month - 1] * (1 + spec["growth"]) ** years
            for _ in range(rng.poisson(lam)):
                lo, hi = spec["qty"]
                customers = CUSTOMERS[:4] if item == "SAMPLE-C" else CUSTOMERS
                rows.append((day.strftime("%Y-%m-%d"), item, int(rng.integers(lo, hi + 1)),
                             customers[int(rng.integers(0, len(customers)))]))

    with (OUT / "demand.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_date", "item_code", "quantity", "customer"])
        w.writerows(rows)

    with (OUT / "inventory.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "quantity"])
        w.writerows(INVENTORY)

    with (OUT / "bom.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_code", "component_code", "usage_per_unit"])
        w.writerows(BOM)

    print(f"demand.csv: {len(rows)} rows / inventory.csv: {len(INVENTORY)} rows / bom.csv: {len(BOM)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
