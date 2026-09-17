"""
model/features.py  –  特徴量エンジニアリング（カレンダー・ラグ・ローリング統計）

ModelHandler と同一のロジックをモジュールレベル関数として公開。
ModelHandler は内部でこれらを呼び出す（段階的移行済み）。
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd

from model.calendar import holiday_count_for_week, holiday_count_for_month

# ---------------------------------------------------------------------------
# カレンダー特徴量
# ---------------------------------------------------------------------------

def add_calendar_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """週次データにカレンダー特徴量を付与する。"""
    d = df.copy()
    d["week"]     = d["ds"].dt.isocalendar().week.astype(int)
    d["year"]     = d["ds"].dt.year.astype(int)
    d["week_sin"] = np.sin(2.0 * np.pi * d["week"] / 52.0)
    d["week_cos"] = np.cos(2.0 * np.pi * d["week"] / 52.0)

    hols = [holiday_count_for_week(ts - pd.Timedelta(days=6)) for ts in d["ds"]]
    d["holiday_days"] = np.asarray(hols, dtype=int)
    d["work_days"]    = 7 - d["holiday_days"]

    m = d["ds"].dt.month
    d["is_may"]  = (m == 5).astype(int)
    d["is_june"] = (m == 6).astype(int)
    d["is_nov"]  = (m == 11).astype(int)
    d["is_dec"]  = (m == 12).astype(int)
    return d


def add_calendar_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """月次データにカレンダー特徴量を付与する。"""
    d = df.copy()
    d["year"]      = d["ds"].dt.year.astype(int)
    d["month"]     = d["ds"].dt.month.astype(int)
    d["month_sin"] = np.sin(2.0 * np.pi * d["month"] / 12.0)
    d["month_cos"] = np.cos(2.0 * np.pi * d["month"] / 12.0)
    d["is_may"]    = (d["month"] == 5).astype(int)
    d["is_june"]   = (d["month"] == 6).astype(int)
    d["is_nov"]    = (d["month"] == 11).astype(int)
    d["is_dec"]    = (d["month"] == 12).astype(int)

    hols = [holiday_count_for_month(pd.Timestamp(x)) for x in d["ds"]]
    d["holiday_days"] = np.asarray(hols, dtype=int)
    d["work_days"] = np.maximum(d["ds"].dt.days_in_month - d["holiday_days"], 0).astype(int)
    return d


# ---------------------------------------------------------------------------
# ラグ・ローリング特徴量
# ---------------------------------------------------------------------------

def add_lags(df: pd.DataFrame, lags: List[int]) -> pd.DataFrame:
    """指定したラグを列として追加する（in-place 非破壊）。"""
    d = df.copy()
    for lag in lags:
        d[f"lag_{lag}"] = d["y"].shift(lag, fill_value=0.0)
    return d


def add_rolling(df: pd.DataFrame, windows: List[Tuple[int, str]]) -> pd.DataFrame:
    """(window, stat) のリストに従ってローリング統計を追加する。"""
    d = df.copy()
    for (window, stat) in windows:
        if stat == "mean":
            d[f"rolling_mean_{window}"] = d["y"].rolling(window, min_periods=1).mean().fillna(0.0)
        elif stat == "std":
            d[f"rolling_std_{window}"] = d["y"].rolling(window, min_periods=1).std(ddof=0).fillna(0.0)
    return d


# ---------------------------------------------------------------------------
# 顧客特徴量
# ---------------------------------------------------------------------------

CUSTOMER_FEATURE_COLS = ["cust_unique", "cust_top_share", "cust_unique_roll", "cust_top_share_roll"]


def build_customer_features(
    product_df: pd.DataFrame,
    freq: str,
    rolling_window: int,
) -> pd.DataFrame:
    """顧客多様性・集中度の特徴量を期間単位で集計する。"""
    tmp = product_df.dropna(subset=["customer_id"]).copy()
    if tmp.empty:
        return pd.DataFrame(columns=["ds"] + CUSTOMER_FEATURE_COLS)

    grouper      = pd.Grouper(key="ds", freq=freq)
    unique_count = tmp.groupby(grouper)["customer_id"].nunique()
    vol_total    = tmp.groupby(grouper)["y"].sum()
    by_customer  = tmp.groupby([grouper, "customer_id"])["y"].sum()
    top_customer = by_customer.groupby(level=0).max()
    top_share    = (top_customer / vol_total.replace(0, np.nan)).fillna(0.0)

    cust_df = pd.DataFrame({
        "ds":             unique_count.index,
        "cust_unique":    unique_count.astype(float).values,
        "cust_top_share": top_share.reindex(unique_count.index, fill_value=0.0).astype(float).values,
    })
    cust_df = cust_df.set_index("ds").asfreq(freq, fill_value=0.0).reset_index()
    cust_df["cust_unique_roll"]    = cust_df["cust_unique"].rolling(rolling_window, min_periods=1).mean()
    cust_df["cust_top_share_roll"] = cust_df["cust_top_share"].rolling(rolling_window, min_periods=1).mean()
    return cust_df


# ---------------------------------------------------------------------------
# 欠損補完ヘルパー
# ---------------------------------------------------------------------------

def ensure_customer_cols(df: pd.DataFrame) -> pd.DataFrame:
    """顧客特徴量列が存在しない場合 0 で埋める。"""
    d = df.copy()
    for c in CUSTOMER_FEATURE_COLS:
        if c not in d.columns:
            d[c] = 0.0
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    return d
