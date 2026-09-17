"""
tests/test_config.py  –  config.py の単体テスト
"""
import importlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _reload_config(env: dict | None = None, settings: dict | None = None):
    """環境変数と一時 settings.json を適用して config を読み込み直す。"""
    backup = {}
    settings_path = PROJECT_ROOT / "settings.json"
    created = False
    try:
        for k, v in (env or {}).items():
            backup[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        if settings is not None:
            assert not settings_path.exists(), "project/settings.json が既に存在するためテストできません"
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            created = True
        sys.modules.pop("config", None)
        return importlib.import_module("config")
    finally:
        if created:
            settings_path.unlink(missing_ok=True)
        for k, v in backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("config", None)
        importlib.import_module("config")


class TestDefaults:
    def test_default_port(self):
        c = _reload_config(env={"MANUPHET_PORT": None})
        if not (PROJECT_ROOT / "settings.json").exists():
            assert c.PORT == 8000

    def test_env_port_override(self):
        assert _reload_config(env={"MANUPHET_PORT": "9999"}).PORT == 9999

    def test_data_dir_from_env(self, tmp_path):
        c = _reload_config(env={"MANUPHET_DATA_DIR": str(tmp_path)})
        assert c.DATA_DIR == tmp_path
        assert c.DB_PATH == tmp_path / "manuphet.db"
        assert c.CONFIG_DIR == tmp_path / "config"
        assert c.MODELS_DIR == tmp_path / "models"

    def test_bool_env(self):
        assert _reload_config(env={"MANUPHET_ALLOW_WEB_TRAIN": "0"}).ALLOW_WEB_TRAIN is False
        assert _reload_config(env={"MANUPHET_ALLOW_WEB_TRAIN": "true"}).ALLOW_WEB_TRAIN is True


class TestSettingsJson:
    def test_settings_json_values(self):
        c = _reload_config(env={"MANUPHET_PORT": None},
                           settings={"port": 7777, "allow_web_train": False, "notify_auto": True})
        assert c.PORT == 7777
        # JSON の真偽値もそのまま解釈できる
        assert c.ALLOW_WEB_TRAIN is False

    def test_nav_links_filtering(self):
        c = _reload_config(settings={"nav_links": [{"label": "A", "url": "http://a"}, "bad"]})
        assert c.NAV_LINKS == [{"label": "A", "url": "http://a"}]


class TestMysqlConf:
    def test_defaults(self):
        import config
        conf = config.load_mysql_conf()
        assert conf["port"] == 3306
        assert conf["driver"] == "mysqlconnector"

    def test_save_and_env_override(self):
        import config
        config.save_mysql_conf({"host": "db.example", "port": "3307", "user": "u",
                                "password": "p@ss", "database": "d", "unknown": "x"})
        saved = json.loads(config.MYSQL_CONFIG_JSON.read_text(encoding="utf-8"))
        assert "unknown" not in saved
        assert saved["port"] == 3307

        os.environ["MANUPHET_MYSQL_HOST"] = "10.0.0.5"
        try:
            conf = config.load_mysql_conf()
        finally:
            del os.environ["MANUPHET_MYSQL_HOST"]
        assert conf["host"] == "10.0.0.5"
        assert conf["password"] == "p@ss"
        config.MYSQL_CONFIG_JSON.unlink()


class TestSummary:
    def test_summary_has_no_password(self):
        import config
        s = config.summary()
        for key in ["port", "data_dir", "models_dir", "db_path", "mysql_host"]:
            assert key in s
        assert not any("password" in k for k in s)
