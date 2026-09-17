"""
tests/test_model_handler.py  –  予測エンジンの動作確認（サンプルデータ使用）
"""
import numpy as np
import pandas as pd
import pytest

import config
from data_store import read_csv_text
from model_handler import ModelHandler


@pytest.fixture(scope="module")
def demand():
    df = read_csv_text("demand", (config.SAMPLE_DATA_DIR / "demand.csv").read_text(encoding="utf-8"))
    df["record_date"] = pd.to_datetime(df["record_date"])
    df["quantity"] = pd.to_numeric(df["quantity"])
    return df


def test_train_and_forecast_monthly(demand, tmp_path):
    mh = ModelHandler(model_dir=str(tmp_path))
    info = mh.train_product_model_monthly(demand, "SAMPLE-A")
    assert info["estimator"] == "xgboost"
    assert mh.has_model("SAMPLE-A", "monthly")

    dates, preds = mh.forecast_periods(demand, "SAMPLE-A", "monthly", 6)
    assert len(preds) == 6 and all(p >= 0 for p in preds)
    assert dates[0] == pd.Timestamp("2026-01-31")

    total = mh.predict_consumption(demand, "SAMPLE-A", 6, "monthly")
    assert total == pytest.approx(sum(preds))
    assert mh.predict_consumption(demand, "SAMPLE-A", 6, "weekly") is None

    res = mh.evaluate_walk_forward(demand, "SAMPLE-A", "monthly", test_periods=12)
    assert res["test_size"] == 12 and np.isfinite(res["smape"])
    fig = mh.backtest_figure(demand, "SAMPLE-A", "monthly", horizon=6)
    assert len(fig.axes[0].lines) == 3


def test_train_weekly(demand, tmp_path):
    mh = ModelHandler(model_dir=str(tmp_path))
    mh.train_product_model_weekly(demand, "SAMPLE-B")
    one_month = mh.predict_consumption(demand, "SAMPLE-B", 1, "weekly")
    _, preds = mh.forecast_periods(demand, "SAMPLE-B", "weekly", 4)
    assert one_month == pytest.approx(sum(preds)) and one_month > 0


def test_short_history_uses_ridge(tmp_path):
    df = pd.DataFrame({
        "record_date": pd.date_range("2025-01-15", periods=6, freq="MS"),
        "item_code": "SHORT",
        "quantity": [3, 4, 5, 4, 6, 5],
        "customer": None,
    })
    mh = ModelHandler(model_dir=str(tmp_path))
    assert mh.train_product_model_monthly(df, "SHORT")["estimator"] == "ridge"
    assert mh.predict_consumption(df, "SHORT", 3, "monthly") >= 0
    with pytest.raises(ValueError, match="バックテスト"):
        mh.backtest_figure(df, "SHORT", "monthly")


def test_unknown_item_and_columns(demand, tmp_path):
    mh = ModelHandler(model_dir=str(tmp_path))
    with pytest.raises(ValueError):
        mh.train_product_model_monthly(demand, "NO-SUCH-ITEM")
    with pytest.raises(ValueError):
        mh.train_product_model_monthly(pd.DataFrame({"x": [1]}), "A")


def test_japanese_column_names(tmp_path):
    df = pd.DataFrame({
        "日付": pd.date_range("2024-01-01", periods=400, freq="D"),
        "品目コード": "JP",
        "数量": np.arange(400) % 7 + 1,
    })
    mh = ModelHandler(model_dir=str(tmp_path))
    mh.train_product_model_monthly(df, "JP")
    assert mh.predict_consumption(df, "JP", 1, "monthly") > 0
