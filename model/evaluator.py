"""
model/evaluator.py  –  バックテスト・ウォークフォワード評価

ModelHandler の walk-forward ロジックをスタンドアロン関数として公開。
現状は ModelHandler のメソッドへの薄いラッパーを提供し、
完全移行は model_handler.py のリファクタリング後に行う。
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from model.metrics import calc_metrics, smape

__all__ = ["smape", "calc_metrics", "WalkForwardResult"]


class WalkForwardResult:
    """ウォークフォワード評価の結果を保持する dataclass 代替。"""

    def __init__(
        self,
        barcode: str,
        mode: str,
        periods: int,
        actuals: List[float],
        predictions: List[float],
        dates: List[Any],
    ) -> None:
        self.barcode     = barcode
        self.mode        = mode
        self.periods     = periods
        self.actuals     = actuals
        self.predictions = predictions
        self.dates       = dates

    @property
    def metrics(self) -> Dict[str, float]:
        if not self.actuals:
            return {"rmse": float("nan"), "mae": float("nan"), "smape": float("nan")}
        return calc_metrics(self.actuals, self.predictions)

    def __repr__(self) -> str:
        m = self.metrics
        return (
            f"WalkForwardResult({self.barcode}, {self.mode}, periods={self.periods}, "
            f"RMSE={m['rmse']:.3f}, MAE={m['mae']:.3f}, sMAPE={m['smape']:.3f})"
        )
