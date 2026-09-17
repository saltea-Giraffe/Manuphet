"""
model/calendar.py  –  祝日・営業日カレンダーユーティリティ

日本の営業日（土日・お盆・年末年始）を近似計算する。
holiday_japan 等の外部依存を避けた最小実装。
"""
from __future__ import annotations

import pandas as pd


def is_holiday(day: pd.Timestamp) -> bool:
    """簡易判定: 土日 / お盆(8/13-15) / 年末年始(12/29-1/3) を休日とみなす。"""
    if day.weekday() >= 5:
        return True
    mm, dd = day.month, day.day
    if mm == 8 and 13 <= dd <= 15:
        return True
    if (mm == 12 and dd >= 29) or (mm == 1 and dd <= 3):
        return True
    return False


def holiday_count_for_week(week_start: pd.Timestamp) -> int:
    """week_start 起算 7 日間の休日数を返す。"""
    return sum(1 for i in range(7) if is_holiday(week_start + pd.Timedelta(days=i)))


def holiday_count_for_month(month_end: pd.Timestamp) -> int:
    """month_end が属する月の休日数を返す。"""
    start = (month_end - pd.offsets.MonthEnd(1)) + pd.Timedelta(days=1)
    count = 0
    d = start
    while d <= month_end:
        if is_holiday(d):
            count += 1
        d += pd.Timedelta(days=1)
    return count
