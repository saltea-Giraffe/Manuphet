"""
model_handler.py  –  Manuphet 予測エンジン（ModelHandler）

依存サブモジュール（model/ パッケージ）:
  model/calendar.py   – 祝日・営業日カレンダー
  model/metrics.py    – smape / calc_metrics
  model/transforms.py – log1p変換・外れ値クリップ・safe_array
  model/trainer.py    – XGBoost 学習・ハイパーパラメータ探索
  model/store.py      – モデル保存/読み込み

需要実績 DataFrame の列は data_store.DataStore.load_demand_frame() と同じ
  record_date / item_code / quantity / customer
を前提とする（英語・日本語の類似列名も自動判定する）。
"""
import os
import warnings
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from sklearn.base import clone
from sklearn.linear_model import Ridge
from xgboost import XGBRegressor

import matplotlib as mpl

from model.metrics import calc_metrics as _calc_metrics_impl
from model.transforms import (
    clip_upper_outliers as _clip_upper_outliers_impl,
    should_use_log_transform as _should_use_log_transform_impl,
    transform_target as _transform_target_impl,
    inverse_target as _inverse_target_impl,
    safe_array as _safe_array_impl,
)
from model.calendar import (
    holiday_count_for_week as _holiday_week_impl,
    holiday_count_for_month as _holiday_month_impl,
)
from model.trainer import (
    default_xgb as _default_xgb_impl,
    fit_estimator as _fit_estimator_impl,
    search_best_xgb as _search_best_xgb_impl,
)
from model.store import save_model as _save_model_impl, load_model as _load_model_impl

def _available_font_family() -> List[str]:
    """日本語を表示できるフォントのうち、この PC に存在するものを 1 つ選ぶ。"""
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP", "IPAexGothic"):
        if name in installed:
            return [name, "DejaVu Sans"]
    return ["DejaVu Sans"]


mpl.rcParams["font.family"] = _available_font_family()
warnings.filterwarnings("ignore")


class ModelHandler:
    def __init__(self, model_dir: Optional[str] = None):
        import config

        self.model_dir = str(model_dir or config.MODELS_DIR)
        os.makedirs(self.model_dir, exist_ok=True)
        self.search_iter = int(config.SEARCH_ITER)
        self.enable_customer_features = bool(config.ENABLE_CUSTOMER_FEATURES)

        self.WEEKLY_FREQ = "W-SUN"
        self.MONTHLY_FREQ = "ME"

        self.WEEKLY_LAGS = [1, 2, 3, 4, 6, 8, 12, 26, 52]
        self.WEEKLY_ROLLING = [(4, "mean"), (4, "std"), (8, "mean"), (8, "std")]
        self.WEEKLY_BASE_COLS = [
            "week", "year", "week_sin", "week_cos", "holiday_days", "work_days",
            "is_may", "is_june", "is_nov", "is_dec",
        ]

        self.MONTHLY_LAGS = [1, 2, 3, 6, 12]
        self.MONTHLY_ROLLING = [(3, "mean"), (3, "std")]
        self.MONTHLY_BASE_COLS = [
            "year", "month", "month_sin", "month_cos", "holiday_days", "work_days",
            "is_may", "is_june", "is_nov", "is_dec",
        ]

        self.CUSTOMER_FEATURE_COLS = [
            "cust_unique", "cust_top_share", "cust_unique_roll", "cust_top_share_roll",
        ]

        self.OUTLIER_IQR_MULTIPLIER = 1.5

    # ======================================================
    # Utility
    # ======================================================
    @staticmethod
    def _pick_column(columns: List[Any], preferred: str, keywords: List[str]) -> Optional[Any]:
        col_list = list(columns)
        if preferred in col_list:
            return preferred
        lower_map = [str(c).lower() for c in col_list]
        for kw in keywords:
            kw_lower = kw.lower()
            for idx, c in enumerate(lower_map):
                if kw_lower in c:
                    return col_list[idx]
        return None

    def _resolve_demand_columns(self, demand_data: pd.DataFrame) -> Tuple[Any, Any, Any, Optional[Any]]:
        cols = list(demand_data.columns)
        if not cols:
            raise ValueError("需要実績データに列がありません")

        date_col = self._pick_column(cols, "record_date", ["date", "ds", "日付"])
        item_col = self._pick_column(cols, "item_code", ["item", "product", "code", "品目", "製品"])
        qty_col = self._pick_column(cols, "quantity", ["quantity", "qty", "数量"])
        customer_col = self._pick_column(cols, "customer", ["customer", "client", "顧客", "得意先"])
        if None in (date_col, item_col, qty_col):
            raise ValueError(f"需要実績データの列を判定できません: {cols}")
        if customer_col in (date_col, item_col, qty_col):
            customer_col = None
        return date_col, item_col, qty_col, customer_col

    def _to_float(self, value: Any, default: float = 0.0) -> float:
        try:
            x = float(value)
        except Exception:
            return default
        if not np.isfinite(x):
            return default
        return x

    def _weekly_feature_cols(self, use_customer: bool = False) -> List[str]:
        cols = (
            list(self.WEEKLY_BASE_COLS)
            + [f"lag_{l}" for l in self.WEEKLY_LAGS]
            + [f"rolling_{stat}_{w}" for (w, stat) in self.WEEKLY_ROLLING]
        )
        if use_customer:
            cols += list(self.CUSTOMER_FEATURE_COLS)
        return cols

    def _monthly_feature_cols(self, use_customer: bool = False) -> List[str]:
        cols = (
            list(self.MONTHLY_BASE_COLS)
            + [f"lag_{l}" for l in self.MONTHLY_LAGS]
            + [f"rolling_{stat}_{w}" for (w, stat) in self.MONTHLY_ROLLING]
        )
        if use_customer:
            cols += list(self.CUSTOMER_FEATURE_COLS)
        return cols

    def _choose_default_feature_cols(self, mode: str, model: Optional[Any]) -> List[str]:
        # meta に feature_cols が無いモデル向け: 特徴量数から候補を選ぶ
        if mode == "weekly":
            candidates = [
                self._weekly_feature_cols(use_customer=False),
                self._weekly_feature_cols(use_customer=True),
            ]
        else:
            candidates = [
                self._monthly_feature_cols(use_customer=False),
                self._monthly_feature_cols(use_customer=True),
            ]

        n_features = getattr(model, "n_features_in_", None)
        if n_features is not None:
            for cand in candidates:
                if len(cand) == int(n_features):
                    return list(cand)
        return list(candidates[0])

    def _safe_array(self, X: Any) -> np.ndarray:
        return _safe_array_impl(X)

    def _clip_upper_outliers(self, y: pd.Series) -> pd.Series:
        return _clip_upper_outliers_impl(y, iqr_multiplier=self.OUTLIER_IQR_MULTIPLIER)

    def _should_use_log_transform(self, y_raw: np.ndarray) -> bool:
        return _should_use_log_transform_impl(y_raw)

    def _transform_target(self, y_raw: np.ndarray, use_log1p: bool) -> np.ndarray:
        return _transform_target_impl(y_raw, use_log1p)

    def _inverse_target(self, y_model: np.ndarray, use_log1p: bool) -> np.ndarray:
        return _inverse_target_impl(y_model, use_log1p)

    def _safe_predict(self, model: Any, X: Any, use_log1p: bool = False) -> np.ndarray:
        X_safe = self._safe_array(X)
        y_model = np.asarray(model.predict(X_safe), dtype=float).reshape(-1)
        return self._inverse_target(y_model, use_log1p=use_log1p)

    def _calc_metrics(self, y_true: Any, y_pred: Any) -> Dict[str, float]:
        return _calc_metrics_impl(y_true, y_pred)

    def _unwrap_model_payload(self, loaded: Any, mode: str) -> Tuple[Optional[Any], Dict[str, Any]]:
        if loaded is None:
            return None, {}
        if isinstance(loaded, dict) and "model" in loaded:
            model = loaded.get("model")
            meta = loaded.get("meta", {})
            if not isinstance(meta, dict):
                meta = {}
        else:
            model = loaded
            meta = {}

        if model is None:
            return None, {}

        if "feature_cols" not in meta:
            meta["feature_cols"] = self._choose_default_feature_cols(mode=mode, model=model)
        if "use_log1p" not in meta:
            meta["use_log1p"] = False
        return model, meta

    def _default_xgb_for_mode(self, mode: str, **params) -> XGBRegressor:
        return _default_xgb_impl(mode, **params)

    def _fit_estimator(self, estimator: Any, X: np.ndarray, y: np.ndarray) -> Any:
        return _fit_estimator_impl(estimator, X, y)

    def _search_best_xgb(self, mode: str, X: np.ndarray, y: np.ndarray) -> Tuple[XGBRegressor, Dict[str, Any]]:
        return _search_best_xgb_impl(mode, X, y, n_iter=self.search_iter)

    def has_model(self, item_code: str, mode: str) -> bool:
        from model.store import model_path
        return os.path.exists(model_path(self.model_dir, item_code, mode))

    # ======================================================
    # Data Prep
    # ======================================================
    def _filter_item_rows(self, demand_data: pd.DataFrame, item_code: str) -> pd.DataFrame:
        date_col, item_col, qty_col, customer_col = self._resolve_demand_columns(demand_data)
        df = demand_data[demand_data[item_col].astype(str) == str(item_code)].copy()
        if df.empty:
            raise ValueError(f"需要実績がありません: {item_code}")

        df["ds"] = pd.to_datetime(df[date_col], errors="coerce")
        df["y"] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0.0)
        df = df.dropna(subset=["ds"]).copy()
        df["y"] = df["y"].clip(lower=0.0)
        if df.empty:
            raise ValueError(f"日付が有効な需要実績がありません: {item_code}")

        if customer_col is not None and customer_col in df.columns:
            df["customer_id"] = df[customer_col].astype(str)
            df.loc[df["customer_id"].isin(["", "nan", "None", "NaN"]), "customer_id"] = np.nan
        else:
            df["customer_id"] = np.nan
        return df[["ds", "y", "customer_id"]].copy()

    def _build_customer_period_features(
        self,
        item_df: pd.DataFrame,
        freq: str,
        rolling_window: int,
    ) -> pd.DataFrame:
        tmp = item_df.dropna(subset=["customer_id"]).copy()
        if tmp.empty:
            return pd.DataFrame(columns=["ds"] + self.CUSTOMER_FEATURE_COLS)

        period_grouper = pd.Grouper(key="ds", freq=freq)
        unique_count = tmp.groupby(period_grouper)["customer_id"].nunique()
        vol_total = tmp.groupby(period_grouper)["y"].sum()
        by_customer = tmp.groupby([period_grouper, "customer_id"])["y"].sum()
        top_customer = by_customer.groupby(level=0).max()
        top_share = (top_customer / vol_total.replace(0, np.nan)).fillna(0.0)

        cust_df = pd.DataFrame(
            {
                "ds": unique_count.index,
                "cust_unique": unique_count.astype(float).values,
                "cust_top_share": top_share.reindex(unique_count.index, fill_value=0.0).astype(float).values,
            }
        )
        cust_df = cust_df.set_index("ds").asfreq(freq, fill_value=0.0).reset_index()
        cust_df["cust_unique_roll"] = cust_df["cust_unique"].rolling(rolling_window, min_periods=1).mean()
        cust_df["cust_top_share_roll"] = cust_df["cust_top_share"].rolling(rolling_window, min_periods=1).mean()
        return cust_df

    def _prepare_periodic_series(
        self,
        demand_data: pd.DataFrame,
        item_code: str,
        freq: str,
        include_customer: bool = False,
    ) -> pd.DataFrame:
        item_df = self._filter_item_rows(demand_data, item_code)
        periodic = (
            item_df.set_index("ds")["y"]
            .resample(freq)
            .sum()
            .asfreq(freq, fill_value=0.0)
            .reset_index()
        )
        periodic.columns = ["ds", "y"]

        if include_customer:
            roll = 4 if freq == self.WEEKLY_FREQ else 3
            cust = self._build_customer_period_features(item_df, freq=freq, rolling_window=roll)
            if not cust.empty:
                periodic = periodic.merge(cust, on="ds", how="left")
            for c in self.CUSTOMER_FEATURE_COLS:
                if c not in periodic.columns:
                    periodic[c] = 0.0
                periodic[c] = pd.to_numeric(periodic[c], errors="coerce").fillna(0.0)

        periodic["y"] = pd.to_numeric(periodic["y"], errors="coerce").fillna(0.0).clip(lower=0.0)
        periodic = periodic.sort_values("ds").reset_index(drop=True)
        return periodic

    def _add_calendar_features_weekly(self, df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        d["week"] = d["ds"].dt.isocalendar().week.astype(int)
        d["year"] = d["ds"].dt.year.astype(int)
        d["week_sin"] = np.sin(2.0 * np.pi * d["week"] / 52.0)
        d["week_cos"] = np.cos(2.0 * np.pi * d["week"] / 52.0)

        holiday_days = []
        for end_day in d["ds"]:
            week_start = end_day - pd.Timedelta(days=6)
            holiday_days.append(_holiday_week_impl(week_start))
        d["holiday_days"] = np.asarray(holiday_days, dtype=int)
        d["work_days"] = 7 - d["holiday_days"]

        m = d["ds"].dt.month
        d["is_may"] = (m == 5).astype(int)
        d["is_june"] = (m == 6).astype(int)
        d["is_nov"] = (m == 11).astype(int)
        d["is_dec"] = (m == 12).astype(int)
        return d

    def _add_calendar_features_monthly(self, df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        d["year"] = d["ds"].dt.year.astype(int)
        d["month"] = d["ds"].dt.month.astype(int)
        d["month_sin"] = np.sin(2.0 * np.pi * d["month"] / 12.0)
        d["month_cos"] = np.cos(2.0 * np.pi * d["month"] / 12.0)

        d["is_may"] = (d["month"] == 5).astype(int)
        d["is_june"] = (d["month"] == 6).astype(int)
        d["is_nov"] = (d["month"] == 11).astype(int)
        d["is_dec"] = (d["month"] == 12).astype(int)

        # 月次にも営業日寄与を追加（休日数/稼働日数）
        holiday_days = [_holiday_month_impl(pd.Timestamp(x)) for x in d["ds"]]
        d["holiday_days"] = np.asarray(holiday_days, dtype=int)
        d["work_days"] = np.maximum(d["ds"].dt.days_in_month - d["holiday_days"], 0).astype(int)
        return d

    def _add_lag_rolling(self, d: pd.DataFrame, lags: List[int], rolling: List[Tuple[int, str]]) -> pd.DataFrame:
        for lag in lags:
            d[f"lag_{lag}"] = d["y"].shift(lag, fill_value=0.0)
        for (window, stat) in rolling:
            if stat == "mean":
                d[f"rolling_mean_{window}"] = d["y"].rolling(window, min_periods=1).mean().fillna(0.0)
            else:
                d[f"rolling_std_{window}"] = d["y"].rolling(window, min_periods=1).std(ddof=0).fillna(0.0)
        for c in self.CUSTOMER_FEATURE_COLS:
            if c not in d.columns:
                d[c] = 0.0
            d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
        return d

    def _build_weekly_features(self, df_in: pd.DataFrame) -> pd.DataFrame:
        d = self._add_calendar_features_weekly(df_in.copy())
        d = self._add_lag_rolling(d, self.WEEKLY_LAGS, self.WEEKLY_ROLLING)
        full_cols = self._weekly_feature_cols(use_customer=True)
        return d[["ds", "y"] + full_cols].copy()

    def _build_monthly_features(self, df_in: pd.DataFrame) -> pd.DataFrame:
        d = self._add_calendar_features_monthly(df_in.copy())
        d = self._add_lag_rolling(d, self.MONTHLY_LAGS, self.MONTHLY_ROLLING)
        full_cols = self._monthly_feature_cols(use_customer=True)
        return d[["ds", "y"] + full_cols].copy()

    def _build_features_by_mode(self, mode: str, df: pd.DataFrame) -> pd.DataFrame:
        if mode == "weekly":
            return self._build_weekly_features(df)
        return self._build_monthly_features(df)

    # ======================================================
    # Train (Weekly / Monthly)
    # ======================================================
    def _train(self, demand_data: pd.DataFrame, item_code: str, mode: str) -> Dict[str, Any]:
        freq = self.WEEKLY_FREQ if mode == "weekly" else self.MONTHLY_FREQ
        periodic = self._prepare_periodic_series(
            demand_data=demand_data,
            item_code=item_code,
            freq=freq,
            include_customer=self.enable_customer_features,
        )
        periodic["y"] = self._clip_upper_outliers(periodic["y"])
        feat = self._build_features_by_mode(mode, periodic)

        if mode == "weekly":
            feature_cols = self._weekly_feature_cols(use_customer=self.enable_customer_features)
            min_rows, min_hold = 20, 8
        else:
            feature_cols = self._monthly_feature_cols(use_customer=self.enable_customer_features)
            min_rows, min_hold = 12, 6

        X = self._safe_array(feat[feature_cols].values)
        y_raw = pd.to_numeric(feat["y"], errors="coerce").fillna(0.0).clip(lower=0.0).values

        use_log1p = self._should_use_log_transform(y_raw)
        y_train = self._transform_target(y_raw, use_log1p=use_log1p)

        meta = {
            "mode": mode,
            "feature_cols": feature_cols,
            "use_log1p": use_log1p,
            "freq": freq,
            "with_customer_features": self.enable_customer_features,
        }

        if len(X) < min_rows:
            # データが少ない品目は Ridge 回帰で代替する
            model = Ridge()
            self._fit_estimator(model, X, y_train)
            self._save_model(item_code, model, mode, meta=meta)
            return {"item_code": item_code, "mode": mode, "estimator": "ridge", "periods": int(len(X))}

        model, best_params = self._search_best_xgb(mode, X, y_train)
        split = min(max(min_hold, int(len(X) * 0.2)), len(X))
        hold_pred = self._safe_predict(model, X[-split:], use_log1p=use_log1p)
        hold_true = y_raw[-split:]
        m = self._calc_metrics(hold_true, hold_pred)

        print(f"[{mode} XGB] {item_code} best_params= {best_params}")
        print(f"{item_code} RMSE={m['rmse']:.3f} MAE={m['mae']:.3f} sMAPE={m['smape']:.3f}")

        self._save_model(item_code, model, mode, meta=meta)
        return {"item_code": item_code, "mode": mode, "estimator": "xgboost",
                "periods": int(len(X)), "holdout": m}

    def train_product_model_weekly(self, demand_data: pd.DataFrame, item_code: str) -> Dict[str, Any]:
        return self._train(demand_data, item_code, "weekly")

    def train_product_model_monthly(self, demand_data: pd.DataFrame, item_code: str) -> Dict[str, Any]:
        # 欠損月は asfreq(ME) で0埋めし、月次でも一貫した時系列整形を実施
        return self._train(demand_data, item_code, "monthly")

    # ======================================================
    # Walk-Forward Evaluation
    # ======================================================
    def _fit_model_from_dataframe(
        self,
        mode: str,
        train_df: pd.DataFrame,
        model_payload: Optional[Any] = None,
    ) -> Tuple[Any, List[str], bool]:
        use_customer_default = bool(
            self.enable_customer_features and all(c in train_df.columns for c in self.CUSTOMER_FEATURE_COLS)
        )
        feat_df = self._build_features_by_mode(mode, train_df)
        if mode == "weekly":
            default_cols = self._weekly_feature_cols(use_customer=use_customer_default)
        else:
            default_cols = self._monthly_feature_cols(use_customer=use_customer_default)

        model_template = None
        meta: Dict[str, Any] = {}
        if model_payload is not None:
            model_template, meta = self._unwrap_model_payload(model_payload, mode=mode)

        if "feature_cols" in meta:
            feature_cols = list(meta["feature_cols"])
        elif model_template is not None:
            feature_cols = self._choose_default_feature_cols(mode=mode, model=model_template)
        else:
            feature_cols = list(default_cols)

        for c in feature_cols:
            if c not in feat_df.columns:
                feat_df[c] = 0.0

        X = self._safe_array(feat_df[feature_cols].values)
        y_raw = pd.to_numeric(feat_df["y"], errors="coerce").fillna(0.0).clip(lower=0.0).values
        use_log1p = bool(meta.get("use_log1p", self._should_use_log_transform(y_raw)))
        y_train = self._transform_target(y_raw, use_log1p=use_log1p)

        if model_template is not None:
            try:
                estimator = clone(model_template)
            except Exception:
                estimator = Ridge() if len(X) < 10 else self._default_xgb_for_mode(mode)
        else:
            estimator = Ridge() if len(X) < 10 else self._default_xgb_for_mode(mode)

        estimator = self._fit_estimator(estimator, X, y_train)
        return estimator, feature_cols, use_log1p

    def _walk_forward_evaluate(
        self,
        periodic_df: pd.DataFrame,
        mode: str,
        test_periods: int,
        model_payload: Optional[Any] = None,
    ) -> Dict[str, Any]:
        freq = self.WEEKLY_FREQ if mode == "weekly" else self.MONTHLY_FREQ
        min_train = 16 if mode == "weekly" else 12
        min_eval = 8 if mode == "weekly" else 6

        d = periodic_df.copy()
        d["ds"] = pd.to_datetime(d["ds"])
        d["y"] = pd.to_numeric(d["y"], errors="coerce").fillna(0.0).clip(lower=0.0)
        d = d.set_index("ds").asfreq(freq, fill_value=0.0).reset_index()

        for c in self.CUSTOMER_FEATURE_COLS:
            if c in d.columns:
                d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)

        label = "週" if mode == "weekly" else "か月"
        if len(d) < (min_train + 2):
            raise ValueError(f"バックテストには最低 {min_train + 2} {label}分の需要実績が必要です（現在 {len(d)} {label}分）")

        max_test = len(d) - min_train
        test_periods = int(test_periods)
        if max_test >= min_eval and test_periods < min_eval:
            test_periods = min_eval
        test_periods = min(test_periods, max_test)
        if test_periods <= 0:
            raise ValueError(f"バックテストの評価期間を確保できません（{mode}）")

        train_df = d.iloc[:-test_periods].copy()
        test_df = d.iloc[-test_periods:].copy()

        model, feature_cols, use_log1p = self._fit_model_from_dataframe(
            mode=mode,
            train_df=train_df,
            model_payload=model_payload,
        )

        actual = []
        pred = []
        dates = []
        history = train_df.copy()

        for _, row in test_df.iterrows():
            ds = pd.to_datetime(row["ds"])
            y_true = self._to_float(row["y"], default=0.0)

            future_row = {"ds": ds, "y": self._to_float(history["y"].iloc[-1], default=0.0)}
            for c in self.CUSTOMER_FEATURE_COLS:
                if c in history.columns:
                    future_row[c] = self._to_float(history[c].iloc[-1], default=0.0)

            tmp = pd.concat([history, pd.DataFrame([future_row])], ignore_index=True)
            feat_tmp = self._build_features_by_mode(mode, tmp)
            for c in feature_cols:
                if c not in feat_tmp.columns:
                    feat_tmp[c] = 0.0

            X_test = self._safe_array(feat_tmp.iloc[[-1]][feature_cols].values)
            y_pred = float(self._safe_predict(model, X_test, use_log1p=use_log1p)[0])

            actual.append(max(0.0, y_true))
            pred.append(max(0.0, y_pred))
            dates.append(ds)

            next_hist = {"ds": ds, "y": y_true}
            for c in self.CUSTOMER_FEATURE_COLS:
                if c in history.columns:
                    if c in test_df.columns:
                        next_hist[c] = self._to_float(row[c], default=self._to_float(history[c].iloc[-1], 0.0))
                    else:
                        next_hist[c] = self._to_float(history[c].iloc[-1], default=0.0)
            history = pd.concat([history, pd.DataFrame([next_hist])], ignore_index=True)

        metrics = self._calc_metrics(actual, pred)
        return {
            "mode": mode,
            "dates": dates,
            "actual": actual,
            "pred": pred,
            "rmse": metrics["rmse"],
            "mae": metrics["mae"],
            "smape": metrics["smape"],
            "train_size": len(train_df),
            "test_size": len(test_df),
            "use_log1p": bool(use_log1p),
            "feature_cols": list(feature_cols),
        }

    def evaluate_walk_forward(
        self,
        demand_data: pd.DataFrame,
        item_code: str,
        mode: str,
        test_periods: int = 12,
        model_payload: Optional[Any] = None,
    ) -> Dict[str, Any]:
        freq = self.WEEKLY_FREQ if mode == "weekly" else self.MONTHLY_FREQ
        periodic = self._prepare_periodic_series(
            demand_data=demand_data,
            item_code=item_code,
            freq=freq,
            include_customer=self.enable_customer_features,
        )
        payload = model_payload if model_payload is not None else self._load_model(item_code, mode)
        return self._walk_forward_evaluate(
            periodic_df=periodic,
            mode=mode,
            test_periods=test_periods,
            model_payload=payload,
        )

    # 需要予測グラフの配色
    _CP_BG      = "#06060E"
    _CP_CARD    = "#0C0C1A"
    _CP_YELLOW  = "#FCE300"
    _CP_CYAN    = "#00E5FF"
    _CP_GRID    = "#1A1A2E"
    _CP_TEXT    = "#8090A0"

    def _apply_dark_axes(self, ax):
        ax.set_facecolor(self._CP_CARD)
        ax.tick_params(colors=self._CP_TEXT, labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor(self._CP_GRID)
        ax.xaxis.label.set_color(self._CP_TEXT)
        ax.yaxis.label.set_color(self._CP_TEXT)
        ax.grid(True, color=self._CP_GRID, linewidth=0.5, alpha=0.8, zorder=0)

    def _plot_backtest_result(self, result: Dict[str, Any], title_prefix: str,
                              forecast: Optional[Tuple[List[Any], List[float]]] = None) -> Figure:
        fig = Figure(figsize=(6, 4), dpi=100)
        fig.patch.set_facecolor(self._CP_BG)
        ax = fig.add_subplot(111)
        self._apply_dark_axes(ax)
        ax.plot(result["dates"], result["actual"],
                label="ACTUAL", color=self._CP_YELLOW, linewidth=2, zorder=3)
        ax.plot(result["dates"], result["pred"],
                label="BACKTEST", color=self._CP_CYAN, linewidth=2, linestyle="--", zorder=3)
        ax.fill_between(result["dates"], result["actual"],
                        alpha=0.07, color=self._CP_YELLOW, zorder=2)
        colors = [self._CP_YELLOW, self._CP_CYAN]
        if forecast and forecast[0]:
            f_dates = [result["dates"][-1]] + list(forecast[0])
            f_vals = [result["actual"][-1]] + list(forecast[1])
            ax.plot(f_dates, f_vals, label="FORECAST", color="#FF6BD6",
                    linewidth=2, linestyle=":", marker="o", markersize=3, zorder=3)
            colors.append("#FF6BD6")
        ax.set_title(
            f"{title_prefix}  RMSE={result['rmse']:.2f}  "
            f"MAE={result['mae']:.2f}  sMAPE={result['smape']:.1f}%",
            color=self._CP_YELLOW, fontsize=10, fontweight="bold", pad=10,
        )
        leg = ax.legend(facecolor=self._CP_BG, edgecolor=self._CP_YELLOW,
                        fontsize=9, labelcolor=colors)
        leg.get_frame().set_alpha(0.9)
        fig.autofmt_xdate()
        return fig

    def backtest_figure(self, demand_data: pd.DataFrame, item_code: str, mode: str,
                        horizon: int = 0) -> Figure:
        """ウォークフォワード評価（直近12期間）と、指定があれば将来予測を描画する。"""
        model_payload = self._load_model(item_code, model_type=mode)
        if model_payload is None:
            raise ValueError(f"{item_code} の{'週次' if mode == 'weekly' else '月次'}モデルがありません。先に学習してください")
        result = self.evaluate_walk_forward(
            demand_data=demand_data,
            item_code=item_code,
            mode=mode,
            test_periods=12,
            model_payload=model_payload,
        )
        forecast = None
        if horizon > 0:
            forecast = self.forecast_periods(demand_data, item_code, mode, horizon)
        title = "Weekly Backtest (WF)" if mode == "weekly" else "Monthly Backtest (WF)"
        return self._plot_backtest_result(result, title, forecast=forecast)

    # ======================================================
    # Iterative Forecast
    # ======================================================
    def _next_period(self, last_ds: pd.Timestamp, mode: str) -> pd.Timestamp:
        if mode == "weekly":
            return pd.to_datetime(last_ds) + pd.Timedelta(days=7)
        return pd.to_datetime(last_ds) + pd.offsets.MonthEnd(1)

    def _predict_future_periods(
        self,
        history_df: pd.DataFrame,
        model: Any,
        mode: str,
        n_periods: int,
        feature_cols: List[str],
        use_log1p: bool,
    ) -> Tuple[List[Any], List[float]]:
        if history_df.empty or n_periods <= 0:
            return [], []

        freq = self.WEEKLY_FREQ if mode == "weekly" else self.MONTHLY_FREQ
        current = history_df.copy()
        current["ds"] = pd.to_datetime(current["ds"])
        current["y"] = pd.to_numeric(current["y"], errors="coerce").fillna(0.0).clip(lower=0.0)
        current = current.set_index("ds").asfreq(freq, fill_value=0.0).reset_index()

        for c in self.CUSTOMER_FEATURE_COLS:
            if c in current.columns:
                current[c] = pd.to_numeric(current[c], errors="coerce").fillna(0.0)

        dates: List[Any] = []
        preds: List[float] = []

        for _ in range(int(n_periods)):
            next_ds = self._next_period(current["ds"].iloc[-1], mode=mode)
            next_row = {"ds": next_ds, "y": self._to_float(current["y"].iloc[-1], default=0.0)}
            for c in self.CUSTOMER_FEATURE_COLS:
                if c in current.columns:
                    next_row[c] = self._to_float(current[c].tail(4).mean(), default=0.0)

            tmp = pd.concat([current, pd.DataFrame([next_row])], ignore_index=True)
            feat_tmp = self._build_features_by_mode(mode, tmp)
            for c in feature_cols:
                if c not in feat_tmp.columns:
                    feat_tmp[c] = 0.0

            X_test = self._safe_array(feat_tmp.iloc[[-1]][feature_cols].values)
            y_hat = float(self._safe_predict(model, X_test, use_log1p=use_log1p)[0])
            y_hat = max(0.0, y_hat)

            dates.append(next_ds)
            preds.append(y_hat)

            next_row["y"] = y_hat
            current = pd.concat([current, pd.DataFrame([next_row])], ignore_index=True)
        return dates, preds

    def forecast_periods(self, demand_data: pd.DataFrame, item_code: str, mode: str,
                         n_periods: int) -> Tuple[List[Any], List[float]]:
        """学習済みモデルで n 期間先まで反復予測する。モデルが無ければ空を返す。"""
        loaded = self._load_model(item_code, model_type=mode)
        model, meta = self._unwrap_model_payload(loaded, mode=mode)
        if model is None:
            return [], []

        feature_cols = list(meta.get("feature_cols", self._choose_default_feature_cols(mode, model)))
        use_customer = any(c in feature_cols for c in self.CUSTOMER_FEATURE_COLS)
        use_log1p = bool(meta.get("use_log1p", False))
        freq = self.WEEKLY_FREQ if mode == "weekly" else self.MONTHLY_FREQ

        periodic = self._prepare_periodic_series(
            demand_data=demand_data,
            item_code=item_code,
            freq=freq,
            include_customer=use_customer,
        )
        max_lag = max(self.WEEKLY_LAGS) if mode == "weekly" else max(self.MONTHLY_LAGS)
        hist = periodic.iloc[-max(16 if mode == "weekly" else 12, max_lag + 1):].copy().reset_index(drop=True)
        return self._predict_future_periods(
            history_df=hist,
            model=model,
            mode=mode,
            n_periods=int(n_periods),
            feature_cols=feature_cols,
            use_log1p=use_log1p,
        )

    def predict_consumption(self, demand_data: pd.DataFrame, item_code: str,
                            months: int, mode: str) -> Optional[float]:
        """今後 months か月分の予測需要合計。モデルが無ければ None。

        週次モデルは 1か月 = 4週として合算する。
        """
        if not self.has_model(item_code, mode):
            return None
        n_periods = int(months) * 4 if mode == "weekly" else int(months)
        _, preds = self.forecast_periods(demand_data, item_code, mode, n_periods)
        return float(sum(preds))

    # ======================================================
    # Save / Load
    # ======================================================
    def _save_model(self, item_code: str, model, model_type: str = "weekly", meta: Optional[Dict[str, Any]] = None):
        _save_model_impl(self.model_dir, item_code, model, model_type=model_type, meta=meta)

    def _load_model(self, item_code: str, model_type: str = "weekly"):
        return _load_model_impl(self.model_dir, item_code, model_type=model_type)
