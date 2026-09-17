"""
model パッケージ

model_handler.py（プロジェクトルート）が予測エンジンのファサードで、
部品ごとの実装をこのパッケージに分けている。

サブモジュール:
  calendar.py   – 祝日・営業日カレンダー
  metrics.py    – smape / calc_metrics
  transforms.py – log1p変換・外れ値クリップ・safe_array
  features.py   – 特徴量エンジニアリング
  trainer.py    – XGBoost 学習・ハイパーパラメータ探索
  evaluator.py  – WalkForwardResult
  store.py      – モデル保存/読み込み
"""
from model.metrics import smape, calc_metrics   # noqa: F401
from model.store import save_model, load_model, list_saved_items  # noqa: F401

__all__ = [
    "smape", "calc_metrics",
    "save_model", "load_model", "list_saved_items",
]
