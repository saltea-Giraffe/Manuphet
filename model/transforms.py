"""
model/transforms.py  –  ターゲット変換（log1p / 逆変換）・外れ値除去

すべて numpy/pandas のみで完結する純粋関数。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 外れ値クリップの IQR 倍率（ModelHandler と一致させる）
OUTLIER_IQR_MULTIPLIER = 1.5


def clip_upper_outliers(y: pd.Series, iqr_multiplier: float = OUTLIER_IQR_MULTIPLIER) -> pd.Series:
    """上側外れ値を IQR 基準でクリップ（下限は 0 のみ）。"""
    s = pd.to_numeric(y, errors="coerce").fillna(0.0).clip(lower=0.0)
    if len(s) == 0:
        return s
    q1, q3 = s.quantile([0.25, 0.75])
    iqr = max(float(q3 - q1), 1e-6)
    upper = float(q3 + iqr_multiplier * iqr)
    return s.clip(upper=max(0.0, upper))


def should_use_log_transform(y_raw: np.ndarray) -> bool:
    """歪みが強い系列のみ log1p 変換を有効化する。"""
    y = np.asarray(y_raw, dtype=float)
    if len(y) < 12 or np.any(y < 0) or np.nanmax(y) < 10:
        return False
    skew = pd.Series(y).skew()
    return bool(np.isfinite(skew) and skew > 1.0)


def transform_target(y_raw: np.ndarray, use_log1p: bool) -> np.ndarray:
    y = np.asarray(y_raw, dtype=float)
    return np.log1p(np.clip(y, 0.0, None)) if use_log1p else y


def inverse_target(y_model: np.ndarray, use_log1p: bool) -> np.ndarray:
    y = np.asarray(y_model, dtype=float)
    return np.clip(np.expm1(y) if use_log1p else y, 0.0, None)


def safe_array(X) -> np.ndarray:
    """NaN/inf を 0 に置換して ndarray に変換する。"""
    return np.nan_to_num(np.asarray(X, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
