"""
config.py  –  Manuphet 集中設定

優先順位:
  1. 環境変数 (MANUPHET_*)
  2. スクリプト同階層の settings.json                         (開発用ローカル上書き)
  3. %ProgramData%\\Manuphet\\data\\config\\settings.json    (セットアップウィザードが書込む)
  4. ここに定義したデフォルト値

パス・接続情報・資格情報はこのファイルまたは settings.json 経由で解決し、
他のモジュールへのハードコードは禁止とする。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "Manuphet"

# ---------------------------------------------------------------------------
# ディレクトリ解決ヘルパー
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_PROGRAM_DATA = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
_APP_ROOT = _PROGRAM_DATA / APP_NAME


def resource_dir() -> Path:
    """同梱リソース（web/, assets/, examples/ など）の基準ディレクトリ。

    PyInstaller onefile 実行時は展開先 (sys._MEIPASS)、通常実行時は project/。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else _HERE


def _load_settings() -> dict[str, Any]:
    """settings.json を優先順位に従って読み込む。"""
    candidates = [
        _HERE / "settings.json",                      # 開発者ローカル上書き (git 追跡外)
        _APP_ROOT / "data" / "config" / "settings.json",  # セットアップウィザードが書込む本番設定
        _APP_ROOT / "settings.json",                  # 手動配置用
    ]
    for p in candidates:
        if p.exists():
            try:
                with p.open(encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception:
                pass
    return {}


_SETTINGS: dict[str, Any] = _load_settings()


def _get(key: str, env_var: str, default: Any = None) -> Any:
    """環境変数 → settings.json → デフォルト の順で解決する。"""
    env_val = os.environ.get(env_var)
    if env_val is not None:
        return env_val
    return _SETTINGS.get(key, default)


def _get_bool(key: str, env_var: str, default: bool) -> bool:
    val = _get(key, env_var, default)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Web サーバー
# ---------------------------------------------------------------------------
HOST: str = str(_get("host", "MANUPHET_HOST", "0.0.0.0"))
PORT: int = int(_get("port", "MANUPHET_PORT", 8000))

# ---------------------------------------------------------------------------
# データ / 設定ファイルの保存先
# ---------------------------------------------------------------------------
DATA_DIR   = Path(_get("data_dir",   "MANUPHET_DATA_DIR",   str(_APP_ROOT / "data")))
MODELS_DIR = Path(_get("models_dir", "MANUPHET_MODELS_DIR", str(DATA_DIR / "models")))
LOG_DIR    = Path(_get("log_dir",    "MANUPHET_LOG_DIR",    str(DATA_DIR / "logs")))
CONFIG_DIR = Path(_get("config_dir", "MANUPHET_CONFIG_DIR", str(DATA_DIR / "config")))

# ローカルデータストア（需要実績・在庫・部品構成・MySQL 取込定義）
DB_PATH = Path(_get("db_path", "MANUPHET_DB_PATH", str(DATA_DIR / "manuphet.db")))

# ---------------------------------------------------------------------------
# ランタイム JSON（実体は DATA_DIR / CONFIG_DIR 以下）
# ---------------------------------------------------------------------------
EXCLUDED_JSON        = CONFIG_DIR / "excluded_items.json"
WEEKLY_JSON          = CONFIG_DIR / "weekly_items.json"
EMAIL_JSON           = CONFIG_DIR / "email_list.json"
NOTIFY_SETTINGS_JSON = CONFIG_DIR / "notify_settings.json"
SMTP_CONFIG_JSON     = CONFIG_DIR / "smtp_config.json"
MYSQL_CONFIG_JSON    = CONFIG_DIR / "mysql_config.json"
NOTIFY_STATE_JSON    = DATA_DIR / "notify_state.json"
RETRAIN_STATE_JSON   = DATA_DIR / "retrain_state.json"

# ---------------------------------------------------------------------------
# MySQL 接続（任意。Web UI の「MySQL連携」タブからも設定できる）
# ---------------------------------------------------------------------------
_MYSQL_DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "",
    "password": "",
    "database": "",
    "driver": "mysqlconnector",   # mysqlconnector / pymysql
}


def load_mysql_conf() -> dict[str, Any]:
    """mysql_config.json → 環境変数 の順で解決（呼び出しのたびに読み直す）。"""
    conf: dict[str, Any] = dict(_MYSQL_DEFAULTS)
    if MYSQL_CONFIG_JSON.exists():
        try:
            with MYSQL_CONFIG_JSON.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                conf.update({k: v for k, v in data.items() if k in _MYSQL_DEFAULTS})
        except Exception:
            pass

    env_map = {
        "host": "MANUPHET_MYSQL_HOST",
        "port": "MANUPHET_MYSQL_PORT",
        "user": "MANUPHET_MYSQL_USER",
        "password": "MANUPHET_MYSQL_PASSWORD",
        "database": "MANUPHET_MYSQL_DATABASE",
        "driver": "MANUPHET_MYSQL_DRIVER",
    }
    for key, env_var in env_map.items():
        if os.environ.get(env_var) is not None:
            conf[key] = os.environ[env_var]
    conf["port"] = int(conf.get("port") or 3306)
    return conf


def save_mysql_conf(conf: dict[str, Any]) -> None:
    """mysql_config.json を保存する（未知のキーは保存しない）。"""
    data = {k: conf.get(k, v) for k, v in _MYSQL_DEFAULTS.items()}
    data["port"] = int(data["port"] or 3306)
    MYSQL_CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)
    with MYSQL_CONFIG_JSON.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Web 学習許可 / 通知 / 再学習
# ---------------------------------------------------------------------------
ALLOW_WEB_TRAIN      : bool = _get_bool("allow_web_train",      "MANUPHET_ALLOW_WEB_TRAIN",      True)
NOTIFY_AUTO          : bool = _get_bool("notify_auto",          "MANUPHET_NOTIFY_AUTO",          True)
NOTIFY_INTERVAL_MIN  : int  = int(_get("notify_interval_min",   "MANUPHET_NOTIFY_INTERVAL_MIN",  360))
AUTO_RETRAIN_MONTHLY : bool = _get_bool("auto_retrain_monthly", "MANUPHET_AUTO_RETRAIN_MONTHLY", True)

# ハイパーパラメータ探索の試行回数（テストや低スペック環境では小さくする）
SEARCH_ITER: int = int(_get("search_iter", "MANUPHET_SEARCH_ITER", 100))

# 顧客特徴量（顧客数・上位顧客シェア）を学習に使うか
ENABLE_CUSTOMER_FEATURES: bool = _get_bool(
    "enable_customer_features", "MANUPHET_ENABLE_CUSTOMER_FEATURES", False
)

# ---------------------------------------------------------------------------
# サンプルデータ（「サンプルデータ投入」ボタン / cli.py load-sample 用）
# ---------------------------------------------------------------------------
SAMPLE_DATA_DIR = Path(_get("sample_data_dir", "MANUPHET_SAMPLE_DATA_DIR",
                            str(resource_dir() / "examples" / "sample_data")))

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    """必要なディレクトリを作成する（起動時に呼ぶ）。"""
    for d in [DATA_DIR, MODELS_DIR, LOG_DIR, CONFIG_DIR, DB_PATH.parent]:
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Web UI ヘッダーのナビリンク（任意の外部ページへのリンクを設定から差し込む）
# 例: "nav_links": [{"label": "販売管理", "url": "http://intranet.example/sales/"}]
# ---------------------------------------------------------------------------
def _load_nav_links() -> list[dict[str, str]]:
    val = _SETTINGS.get("nav_links")
    if isinstance(val, list):
        return [v for v in val if isinstance(v, dict)]
    return []


NAV_LINKS: list[dict[str, str]] = _load_nav_links()


def summary() -> dict[str, Any]:
    """設定内容の概要（デバッグ・ログ用。機密値は除外）。"""
    mysql = load_mysql_conf()
    return {
        "app":                  APP_NAME,
        "host":                 HOST,
        "port":                 PORT,
        "data_dir":             str(DATA_DIR),
        "models_dir":           str(MODELS_DIR),
        "log_dir":              str(LOG_DIR),
        "config_dir":           str(CONFIG_DIR),
        "db_path":              str(DB_PATH),
        "mysql_host":           mysql.get("host"),
        "mysql_db":             mysql.get("database"),
        "allow_web_train":      ALLOW_WEB_TRAIN,
        "notify_auto":          NOTIFY_AUTO,
        "auto_retrain_monthly": AUTO_RETRAIN_MONTHLY,
    }
