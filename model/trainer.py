"""
model/trainer.py  –  XGBoost モデルの学習ロジック

ハイパーパラメータ探索 (RandomizedSearchCV + TimeSeriesSplit) を含む。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from xgboost import XGBRegressor

from model.transforms import safe_array


# ---------------------------------------------------------------------------
# XGBoost デフォルトパラメータ
# ---------------------------------------------------------------------------

def default_xgb(mode: str, **override) -> XGBRegressor:
    """モード別デフォルト XGBRegressor を返す。"""
    import sys
    # PyInstaller frozen exe ではサブプロセス起動が禁止されるため n_jobs=1 を強制
    _n_jobs = 1 if getattr(sys, "frozen", False) else -1
    base: Dict[str, Any] = {
        "objective": "reg:squarederror",
        "random_state": 42,
        "n_jobs": _n_jobs,
    }
    if mode == "weekly":
        base.update({
            "n_estimators": 300, "max_depth": 4, "learning_rate": 0.05,
            "subsample": 0.9, "colsample_bytree": 0.8,
            "min_child_weight": 3, "reg_alpha": 0.0, "reg_lambda": 1.0,
        })
    else:
        base.update({
            "n_estimators": 250, "max_depth": 4, "learning_rate": 0.05,
            "subsample": 0.9, "colsample_bytree": 0.8,
            "min_child_weight": 2, "reg_alpha": 0.0, "reg_lambda": 1.0,
        })
    base.update(override)
    return XGBRegressor(**base)


def _param_dist(mode: str) -> Dict[str, List[Any]]:
    if mode == "weekly":
        return {
            "n_estimators":     [200, 300, 500, 800, 1200],
            "max_depth":        [2, 3, 4, 5, 6, 7, 8],
            "learning_rate":    [0.01, 0.02, 0.03, 0.05, 0.07, 0.1],
            "subsample":        [0.6, 0.7, 0.8, 0.9, 1.0],
            "colsample_bytree": [0.5, 0.6, 0.7, 0.8, 1.0],
            "min_child_weight": [1, 2, 3, 5, 7, 10],
            "reg_alpha":        [0.0, 0.01, 0.1, 0.5, 1.0, 2.0],
            "reg_lambda":       [0.3, 0.5, 1.0, 2.0, 5.0],
            "gamma":            [0.0, 0.1, 0.3, 0.5, 1.0],
        }
    return {
        "n_estimators":     [200, 300, 500, 800, 1200],
        "max_depth":        [2, 3, 4, 5, 6, 7, 8],
        "learning_rate":    [0.01, 0.02, 0.03, 0.05, 0.07, 0.1],
        "subsample":        [0.6, 0.7, 0.8, 0.9, 1.0],
        "colsample_bytree": [0.5, 0.6, 0.7, 0.8, 1.0],
        "min_child_weight": [1, 2, 3, 5, 7, 10],
        "reg_alpha":        [0.0, 0.01, 0.1, 0.5, 1.0, 2.0],
        "reg_lambda":       [0.3, 0.5, 1.0, 2.0, 5.0],
        "gamma":            [0.0, 0.1, 0.3, 0.5, 1.0],
    }


# ---------------------------------------------------------------------------
# フィッティング
# ---------------------------------------------------------------------------

def fit_estimator(estimator: Any, X: np.ndarray, y: np.ndarray) -> Any:
    """Early stopping 付きで XGBRegressor をフィット（失敗時は通常 fit にフォールバック）。"""
    X_safe = safe_array(X)
    y_arr  = np.asarray(y, dtype=float)
    if isinstance(estimator, XGBRegressor):
        val_size = max(4, int(len(X_safe) * 0.15))
        if len(X_safe) - val_size >= 8:
            X_tr, X_val = X_safe[:-val_size], X_safe[-val_size:]
            y_tr, y_val = y_arr[:-val_size], y_arr[-val_size:]
            try:
                estimator.fit(X_tr, y_tr, eval_set=[(X_val, y_val)],
                              verbose=False, early_stopping_rounds=20)
                return estimator
            except Exception:
                pass
    estimator.fit(X_safe, y_arr)
    return estimator


def search_best_xgb(mode: str, X: np.ndarray, y: np.ndarray,
                    n_iter: int = 100) -> Tuple[XGBRegressor, Dict[str, Any]]:
    """TimeSeriesSplit + RandomizedSearchCV で最良の XGBRegressor を探索して返す。"""
    n_splits = min(4, max(2, len(X) // (20 if mode == "weekly" else 12)))

    import sys
    _n_jobs = 1 if getattr(sys, "frozen", False) else -1
    search = RandomizedSearchCV(
        estimator=default_xgb(mode),
        param_distributions=_param_dist(mode),
        n_iter=max(1, int(n_iter)),
        scoring="neg_root_mean_squared_error",
        cv=TimeSeriesSplit(n_splits=n_splits),
        n_jobs=_n_jobs,
        random_state=42,
        verbose=0,
    )
    search.fit(safe_array(X), np.asarray(y, dtype=float))
    best_params = dict(search.best_params_)
    best = fit_estimator(default_xgb(mode, **best_params), X, y)
    return best, best_params


def fit_ridge(X: np.ndarray, y: np.ndarray) -> Ridge:
    """データ数が少ない場合用の Ridge 回帰。"""
    model = Ridge()
    fit_estimator(model, X, y)
    return model
