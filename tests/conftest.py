"""
tests/conftest.py  –  テスト共通設定

config はインポート時に設定を確定するため、どのモジュールよりも先に
データ保存先を一時ディレクトリへ向け、バックグラウンド処理を止めておく。
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="manuphet_test_")
os.environ["MANUPHET_DATA_DIR"] = _TMP
os.environ["MANUPHET_NOTIFY_AUTO"] = "0"
os.environ["MANUPHET_AUTO_RETRAIN_MONTHLY"] = "0"
os.environ["MANUPHET_SEARCH_ITER"] = "3"
for _k in list(os.environ):
    if _k.startswith("MANUPHET_MYSQL_") or _k.startswith("MANUPHET_SMTP_"):
        del os.environ[_k]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
