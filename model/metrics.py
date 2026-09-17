"""
model/metrics.py  –  予測精度メトリクス計算
"""
from __future__ import annotations

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error


def smape(y_true, y_pred) -> float:
    """Symmetric Mean Absolute Percentage Error (%)"""
    y_t = np.asarray(y_true, dtype=float)
    y_p = np.asarray(y_pred, dtype=float)
    denom = np.abs(y_t) + np.abs(y_p)
    mask = denom != 0
    if not np.any(mask):
        return 0.0
    return float(100.0 * np.mean(2.0 * np.abs(y_p[mask] - y_t[mask]) / denom[mask]))


def calc_metrics(y_true, y_pred) -> dict[str, float]:
    y_t = np.asarray(y_true, dtype=float)
    y_p = np.asarray(y_pred, dtype=float)
    return {
        "rmse":  float(np.sqrt(mean_squared_error(y_t, y_p))),
        "mae":   float(mean_absolute_error(y_t, y_p)),
        "smape": smape(y_t, y_p),
    }
