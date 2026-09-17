"""
api/service.py  –  ManuphetService

データ登録・学習・予測・需要供給分析・通知の実行ロジックをまとめたサービス層。
FastAPI ルーター（api/routes.py）から利用する。
"""
from __future__ import annotations

import html
import io
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from data_store import TARGETS, DataStore, load_sample_data, read_csv_text  # noqa: E402
from email_notifier import EmailNotifier  # noqa: E402
from model_handler import ModelHandler  # noqa: E402
from mysql_importer import FILTER_OPS, MySQLImporter  # noqa: E402

# アラートしきい値（日）
ALERT_1MO_DAYS = 30
ALERT_6MO_DAYS = 180
PRODUCT_ROW = "(製品在庫)"

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _days_in_months(months: int) -> int:
    now = pd.Timestamp.now()
    return int((now + pd.DateOffset(months=months) - now).days)


def _coverage_days(supply: float, consumption: float | None, period_days: int) -> float | None:
    """在庫 supply が、period_days 日で consumption だけ消費されるペースで何日もつか。"""
    if consumption is None or consumption <= 0:
        return None
    return max(0.0, supply) / consumption * period_days


def _alert_kind(days_1mo: float | None, days_6mo: float | None) -> str | None:
    if days_1mo is not None and days_1mo <= ALERT_1MO_DAYS:
        return "1か月以内"
    if days_6mo is not None and days_6mo <= ALERT_6MO_DAYS:
        return "半年以内"
    return None


# ---------------------------------------------------------------------------
# サービスクラス
# ---------------------------------------------------------------------------

class ManuphetService:
    """データ登録・学習・予測・通知の統合サービス。"""

    def __init__(self, start_background: bool = True) -> None:
        config.ensure_dirs()

        self.store = DataStore(config.DB_PATH)
        self.model_handler = ModelHandler(model_dir=str(config.MODELS_DIR))
        self.email_notifier = EmailNotifier()

        self._lock        = threading.RLock()
        self._db_lock     = threading.RLock()
        self._notify_lock = threading.Lock()
        self._train_lock  = threading.Lock()
        self._status_lock = threading.Lock()
        self._mysql_lock  = threading.Lock()

        self._demand_df: pd.DataFrame | None = None

        self._train_status: dict[str, Any] = {
            "running": False, "total": 0, "done": 0,
            "failed": [], "started_at": None, "finished_at": None, "current": None,
        }

        with self._lock:
            self._ensure_notify_settings_file()

        if start_background and config.NOTIFY_AUTO:
            threading.Thread(target=self._notify_loop, daemon=True).start()
        if start_background and config.AUTO_RETRAIN_MONTHLY:
            threading.Thread(target=self._monthly_retrain_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # 状態
    # ------------------------------------------------------------------
    def get_status(self) -> dict[str, Any]:
        mysql = config.load_mysql_conf()
        return {
            "ok": True,
            "data": self.store.summary(),
            "allow_web_train": config.ALLOW_WEB_TRAIN,
            "mysql_configured": bool(mysql.get("host") and mysql.get("database")),
        }

    # ------------------------------------------------------------------
    # 永続リスト（除外/週次/メール）
    # ------------------------------------------------------------------
    def get_excluded_set(self) -> set[str]:
        with self._lock:
            return set(_load_json(config.EXCLUDED_JSON, []))

    def set_excluded(self, item_code: str, excluded: bool) -> None:
        self._toggle_list(config.EXCLUDED_JSON, item_code, excluded)

    def get_weekly_set(self) -> set[str]:
        with self._lock:
            return set(_load_json(config.WEEKLY_JSON, []))

    def set_weekly(self, item_code: str, weekly: bool) -> None:
        self._toggle_list(config.WEEKLY_JSON, item_code, weekly)

    def _toggle_list(self, path: Path, value: str, on: bool) -> None:
        if not value:
            raise ValueError("品目コードが空です")
        with self._lock:
            current = set(_load_json(path, []))
            if on:
                current.add(value)
            else:
                current.discard(value)
            _save_json(path, sorted(current))

    def item_mode(self, item_code: str) -> str:
        return "weekly" if item_code in self.get_weekly_set() else "monthly"

    def get_email_list(self) -> list[str]:
        with self._lock:
            return list(_load_json(config.EMAIL_JSON, []))

    def add_email(self, email: str) -> None:
        email = email.strip()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("メールアドレスの形式が正しくありません")
        with self._lock:
            current = self.get_email_list()
            if email not in current:
                current.append(email)
                _save_json(config.EMAIL_JSON, current)

    def remove_email(self, email: str) -> None:
        with self._lock:
            current = [e for e in self.get_email_list() if e != email]
            _save_json(config.EMAIL_JSON, current)

    # ------------------------------------------------------------------
    # SMTP 設定
    # ------------------------------------------------------------------
    def get_smtp_config(self) -> dict[str, Any]:
        """SMTP設定を返す。パスワードは設定済みかどうかのフラグのみ返す。"""
        with self._lock:
            d = _load_json(config.SMTP_CONFIG_JSON, {})
        return {
            "smtp_server": d.get("smtp_server", "smtp.gmail.com"),
            "smtp_port":   int(d.get("smtp_port", 587)),
            "username":    d.get("username", ""),
            "from_addr":   d.get("from_addr", ""),
            "password_set": bool(d.get("password", "")),
        }

    def save_smtp_config(self, smtp_server: str, smtp_port: int,
                         username: str, from_addr: str,
                         password: str | None) -> None:
        """SMTP設定を保存する。password が None または空文字なら既存パスワードを保持。"""
        with self._lock:
            d = _load_json(config.SMTP_CONFIG_JSON, {})
            d["smtp_server"] = smtp_server
            d["smtp_port"]   = int(smtp_port)
            d["username"]    = username
            d["from_addr"]   = from_addr or username
            if password:
                d["password"] = password
            _save_json(config.SMTP_CONFIG_JSON, d)
        self.email_notifier.reload()

    def send_test_email(self) -> dict[str, Any]:
        to_addrs = self.get_email_list()
        self.email_notifier.reload()
        self.email_notifier.set_to_addrs(to_addrs)
        self.email_notifier.send_notification(
            f"【{config.APP_NAME}】テスト送信",
            f"{config.APP_NAME} からのテストメールです。\n送信日時: {_now_iso()}",
        )
        return {"ok": True, "to": to_addrs}

    # ------------------------------------------------------------------
    # 通知設定
    # ------------------------------------------------------------------
    def _ensure_notify_settings_file(self) -> None:
        """notify_settings.json を初期化（呼び出し側でロック取得済みを前提）。"""
        d = _load_json(config.NOTIFY_SETTINGS_JSON, {})
        changed = not d
        if "enabled" not in d:
            d["enabled"] = True
            changed = True
        if changed:
            d["updated_at"] = _now_iso()
            _save_json(config.NOTIFY_SETTINGS_JSON, d)

    def get_notify_settings(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_notify_settings_file()
            d = _load_json(config.NOTIFY_SETTINGS_JSON, {})
        return {"enabled": bool(d.get("enabled", True)), "updated_at": d.get("updated_at", "")}

    def update_notify_settings(self, enabled: bool | None) -> dict[str, Any]:
        with self._lock:
            self._ensure_notify_settings_file()
            d = _load_json(config.NOTIFY_SETTINGS_JSON, {})
            if enabled is not None:
                d["enabled"] = bool(enabled)
            d["updated_at"] = _now_iso()
            _save_json(config.NOTIFY_SETTINGS_JSON, d)
        return self.get_notify_settings()

    # ------------------------------------------------------------------
    # データロード（キャッシュ付き）
    # ------------------------------------------------------------------
    def refresh_db(self) -> None:
        with self._db_lock:
            self._demand_df = self.store.load_demand_frame()

    def invalidate(self) -> None:
        with self._db_lock:
            self._demand_df = None

    def _demand(self) -> pd.DataFrame:
        with self._db_lock:
            if self._demand_df is None:
                self.refresh_db()
            assert self._demand_df is not None
            return self._demand_df

    # ------------------------------------------------------------------
    # データ入力
    # ------------------------------------------------------------------
    def add_demand(self, record_date: str, item_code: str, quantity: float, customer: str | None) -> int:
        new_id = self.store.add_demand(record_date, item_code, quantity, customer or None)
        self.invalidate()
        return new_id

    def delete_demand(self, record_id: int) -> bool:
        ok = self.store.delete_demand(record_id)
        self.invalidate()
        return ok

    def upsert_inventory(self, code: str, quantity: float) -> None:
        self.store.upsert_inventory(code, quantity)

    def upsert_bom(self, item_code: str, component_code: str, usage_per_unit: float | None) -> None:
        self.store.upsert_bom(item_code, component_code, usage_per_unit)

    def import_csv(self, target: str, csv_text: str, mode: str, source_name: str) -> dict[str, int]:
        df = read_csv_text(target, csv_text)
        source = "csv:" + (source_name.strip() or "upload")
        result = self.store.import_frame(target, df, source=source, mode=mode)
        self.invalidate()
        return result

    def load_sample(self) -> dict[str, Any]:
        result = load_sample_data(self.store, config.SAMPLE_DATA_DIR)
        self.invalidate()
        return result

    def clear_data(self, target: str) -> int:
        n = self.store.clear(target)
        self.invalidate()
        return n

    # ------------------------------------------------------------------
    # MySQL 連携
    # ------------------------------------------------------------------
    def get_mysql_config(self) -> dict[str, Any]:
        conf = config.load_mysql_conf()
        return {
            "host": conf.get("host", ""),
            "port": int(conf.get("port") or 3306),
            "user": conf.get("user", ""),
            "database": conf.get("database", ""),
            "driver": conf.get("driver", "mysqlconnector"),
            "password_set": bool(conf.get("password")),
        }

    def save_mysql_config(self, host: str, port: int, user: str, database: str,
                          driver: str, password: str | None) -> None:
        with self._lock:
            conf = config.load_mysql_conf()
            conf.update({"host": host, "port": int(port), "user": user,
                         "database": database, "driver": driver})
            if password:
                conf["password"] = password
            config.save_mysql_conf(conf)

    @contextmanager
    def _importer(self) -> Iterator[MySQLImporter]:
        importer = MySQLImporter.from_conf(config.load_mysql_conf())
        try:
            yield importer
        finally:
            importer.dispose()

    def mysql_test(self) -> dict[str, Any]:
        with self._importer() as imp:
            return imp.test_connection()

    def mysql_tables(self) -> list[dict[str, str]]:
        with self._importer() as imp:
            return imp.list_tables()

    def mysql_columns(self, table: str) -> list[dict[str, Any]]:
        with self._importer() as imp:
            return imp.list_columns(table)

    def mysql_preview(self, table: str, target: str | None, mapping: dict[str, str] | None,
                      filters: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        with self._importer() as imp:
            if target and mapping:
                return imp.preview_mapped(table, target, mapping, filters, limit)
            return imp.preview_table(table, filters, limit)

    def mysql_import(self, table: str, target: str, mapping: dict[str, str],
                     filters: list[dict[str, Any]], mode: str, save_as: str | None) -> dict[str, Any]:
        job_id = None
        source = f"mysql:{table}"
        if save_as:
            job_id = self.store.save_job(save_as, target, table, mapping, filters, mode)
            source = f"mysql:{save_as.strip()}"
        with self._mysql_lock, self._importer() as imp:
            result = imp.import_into(self.store, table, target, mapping, filters, mode, source)
        if job_id is not None:
            self.store.mark_job_run(job_id, result["imported"])
        self.invalidate()
        return {"ok": True, "job_id": job_id, **result}

    def mysql_jobs(self) -> list[dict[str, Any]]:
        return self.store.list_jobs()

    def mysql_delete_job(self, job_id: int) -> bool:
        return self.store.delete_job(job_id)

    def mysql_run_job(self, job_id: int) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise ValueError(f"取込定義が見つかりません: {job_id}")
        with self._mysql_lock, self._importer() as imp:
            result = imp.import_into(self.store, job["table_name"], job["target"], job["mapping"],
                                     job["filters"], job["mode"], source=f"mysql:{job['name']}")
        self.store.mark_job_run(job_id, result["imported"])
        self.invalidate()
        return {"ok": True, "job": job["name"], **result}

    def mysql_sync_all(self) -> dict[str, Any]:
        results, errors = [], []
        for job in self.store.list_jobs():
            try:
                results.append(self.mysql_run_job(int(job["id"])))
            except Exception as e:
                errors.append({"job": job["name"], "error": str(e)})
        return {"ok": not errors, "results": results, "errors": errors}

    @staticmethod
    def import_meta() -> dict[str, Any]:
        return {"targets": TARGETS, "filter_ops": list(FILTER_OPS)}

    # ------------------------------------------------------------------
    # 品目・学習・予測
    # ------------------------------------------------------------------
    def list_items(self, search: str = "", include_excluded: bool = False) -> list[str]:
        demand = self._demand()
        items = sorted({str(x) for x in demand["item_code"].dropna().unique() if str(x).strip()})
        if not include_excluded:
            excluded = self.get_excluded_set()
            items = [i for i in items if i not in excluded]
        if search:
            s = search.strip().lower()
            items = [i for i in items if s in i.lower()]
        return items

    def train_one(self, item_code: str) -> dict[str, Any]:
        demand = self._demand()
        if item_code in self.get_excluded_set():
            raise ValueError(f"{item_code} は除外対象です")
        mode = self.item_mode(item_code)
        if mode == "weekly":
            info = self.model_handler.train_product_model_weekly(demand, item_code)
        else:
            info = self.model_handler.train_product_model_monthly(demand, item_code)
        return {"item_code": item_code, "mode": mode, "estimator": info.get("estimator")}

    def backtest_png(self, item_code: str) -> io.BytesIO:
        """バックテスト＋将来予測のグラフを PNG で返す。"""
        mode = self.item_mode(item_code)
        horizon = 12 if mode == "weekly" else 6
        fig = self.model_handler.backtest_figure(self._demand(), item_code, mode, horizon=horizon)
        buf = io.BytesIO()
        try:
            fig.set_size_inches(12, 6, forward=True)
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                        pad_inches=0.05, facecolor=fig.get_facecolor())
        finally:
            plt.close(fig)
        buf.seek(0)
        return buf

    def supply_analysis(self, item_code: str) -> dict[str, Any]:
        """品目の予測需要と、製品在庫・部品在庫の残日数を計算する。"""
        demand = self._demand()
        mode = self.item_mode(item_code)
        mh = self.model_handler
        has_model = mh.has_model(item_code, mode)

        c_1mo = mh.predict_consumption(demand, item_code, 1, mode) if has_model else None
        c_6mo = mh.predict_consumption(demand, item_code, 6, mode) if has_model else None
        d1, d6 = _days_in_months(1), _days_in_months(6)

        on_hand_raw = self.store.get_inventory(item_code)
        on_hand = on_hand_raw or 0.0
        product = {
            "on_hand": on_hand,
            "has_inventory": on_hand_raw is not None,
            "days_1mo": _coverage_days(on_hand, c_1mo, d1),
            "days_6mo": _coverage_days(on_hand, c_6mo, d6),
        }
        # 在庫未登録のものは「不足」と誤判定しないようアラート対象外にする
        product["alert"] = _alert_kind(product["days_1mo"], product["days_6mo"]) if on_hand_raw is not None else None

        components = []
        for r in self.store.get_components(item_code).itertuples(index=False):
            usage = float(r.usage_per_unit) or 1.0
            comp_on_hand = float(r.on_hand)
            # 部品在庫で作れる台数 + 製品在庫 を供給量とみなす
            supply = on_hand + comp_on_hand / usage
            row = {
                "component_code": r.component_code,
                "usage_per_unit": usage,
                "on_hand": comp_on_hand,
                "has_inventory": bool(r.has_inventory),
                "buildable": comp_on_hand / usage,
                "days_1mo": _coverage_days(supply, c_1mo, d1),
                "days_6mo": _coverage_days(supply, c_6mo, d6),
            }
            row["alert"] = _alert_kind(row["days_1mo"], row["days_6mo"]) if row["has_inventory"] else None
            components.append(row)

        def _alert_days(r: dict[str, Any]) -> float:
            return r["days_1mo"] if r["alert"] == "1か月以内" else r["days_6mo"]

        alerts = []
        if not has_model:
            alerts.append("未学習のため予測できません。「学習」を実行してください。")
        else:
            if product["alert"]:
                alerts.append(f"製品在庫が{product['alert']}に不足する見込みです"
                              f"（残り約 {_alert_days(product):.0f} 日）")
            for c in components:
                if c["alert"]:
                    alerts.append(f"部品 {c['component_code']} が{c['alert']}に不足する見込みです"
                                  f"（残り約 {_alert_days(c):.0f} 日）")

        return {
            "item_code": item_code,
            "mode": mode,
            "has_model": has_model,
            "forecast_1mo": c_1mo,
            "forecast_6mo": c_6mo,
            "product": product,
            "components": components,
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # 在庫不足通知
    # ------------------------------------------------------------------
    def _notify_loop(self) -> None:
        time.sleep(5)
        while True:
            try:
                self.run_notification(force=False)
            except Exception as e:
                print(f"[WARN] notify loop failed: {e}")
            time.sleep(max(1, config.NOTIFY_INTERVAL_MIN) * 60)

    def run_notification(self, force: bool = False) -> dict[str, Any]:
        if not self._notify_lock.acquire(blocking=False):
            return {"ok": True, "skipped": True, "reason": "通知チェックを実行中です"}
        try:
            return self._run_notification_impl(force=force)
        finally:
            self._notify_lock.release()

    def _run_notification_impl(self, force: bool = False) -> dict[str, Any]:
        if (not force) and (not self.get_notify_settings().get("enabled", True)):
            return {"ok": True, "skipped": True, "reason": "通知は無効です"}

        to_addrs = self.get_email_list()
        if not to_addrs:
            return {"ok": False, "error": "送信先メールアドレスが登録されていません"}

        now = datetime.now()
        self.refresh_db()

        with self._lock:
            state = _load_json(config.NOTIFY_STATE_JSON, {})
            items: dict[str, Any] = state.get("items") if isinstance(state.get("items"), dict) else {}

        pending: list[dict[str, Any]] = []
        for item_code in self.list_items():
            try:
                sa = self.supply_analysis(item_code)
            except Exception:
                continue
            if not sa["has_model"]:
                continue

            rows = [{"component": PRODUCT_ROW, "on_hand": sa["product"]["on_hand"],
                     "supply": sa["product"]["on_hand"], **sa["product"]}]
            for c in sa["components"]:
                rows.append({"component": c["component_code"], "on_hand": c["on_hand"],
                             "supply": sa["product"]["on_hand"] + c["buildable"], **c})

            for r in rows:
                key = f"{item_code}||{r['component']}"
                if r["alert"] is None:
                    items.pop(key, None)   # アラート条件を外れた → 状態クリア
                    continue

                total_supply = float(r["supply"])
                rec = items.get(key)
                if rec is None:
                    rec = {"item_code": item_code, "component": r["component"],
                           "last_seen_supply": total_supply, "first_notified_at": None,
                           "month1_notified": False, "month6_notified": False}
                    items[key] = rec

                if total_supply > float(rec.get("last_seen_supply", total_supply)) + 1e-9:
                    # 在庫が増加 → 通知サイクルをリセット
                    rec.update({"last_seen_supply": total_supply, "first_notified_at": None,
                                "month1_notified": False, "month6_notified": False})
                    continue
                rec["last_seen_supply"] = total_supply

                # 送信判定: 初回 → 1ヶ月後 → 6ヶ月後
                first = _parse_iso(rec.get("first_notified_at"))
                reason = None
                if first is None:
                    reason = "initial"
                elif not rec.get("month1_notified") and now >= first + timedelta(days=30):
                    reason = "1month"
                elif not rec.get("month6_notified") and now >= first + timedelta(days=180):
                    reason = "6month"
                if reason is None:
                    continue

                pending.append({
                    "item_code": item_code, "component": r["component"],
                    "on_hand": r["on_hand"], "supply": total_supply,
                    "days_1mo": r["days_1mo"], "days_6mo": r["days_6mo"],
                    "alert": r["alert"], "mode": sa["mode"],
                    "key": key, "reason": reason,
                })

        with self._lock:
            state["items"] = items
            state["updated_at"] = _now_iso()
            _save_json(config.NOTIFY_STATE_JSON, state)

        if not pending:
            return {"ok": True, "sent": 0, "pending": 0}

        def _days(v: float | None) -> str:
            return "-" if v is None else f"{v:.1f}日"

        rows_html = "".join(
            "<tr>"
            f"<td>{html.escape(x['item_code'])}</td><td>{html.escape(x['component'])}</td>"
            f"<td style='text-align:right;'>{x['on_hand']:.1f}</td>"
            f"<td style='text-align:right;'>{x['supply']:.1f}</td>"
            f"<td>{_days(x['days_1mo'])}</td><td>{_days(x['days_6mo'])}</td>"
            f"<td>{x['alert']}</td><td>{x['mode']}</td>"
            "</tr>"
            for x in pending
        )
        html_body = f"""<html><head><meta charset='utf-8'></head><body>
<h2>【{config.APP_NAME}】在庫不足見込みのお知らせ</h2>
<table border='1' cellspacing='0' cellpadding='4' style='border-collapse:collapse;'>
<tr><th>品目</th><th>部品</th><th>在庫</th><th>供給可能数</th>
    <th>1か月ペース残日数</th><th>半年ペース残日数</th><th>不足時期</th><th>モデル</th></tr>
{rows_html}
</table>
<p style="color:#666;">generated at {_now_iso()}</p>
</body></html>"""

        try:
            self.email_notifier.reload()
            self.email_notifier.set_to_addrs(to_addrs)
            self.email_notifier.send_notification(
                f"【{config.APP_NAME}】在庫不足見込みのお知らせ", html_body, html_mode=True)
        except Exception as e:
            return {"ok": False, "error": f"メール送信に失敗しました: {e}", "pending": len(pending)}

        with self._lock:
            state2 = _load_json(config.NOTIFY_STATE_JSON, {})
            items2 = state2.get("items", {})
            now_iso = _now_iso()
            for x in pending:
                rec2 = items2.get(x["key"])
                if not rec2:
                    continue
                if x["reason"] == "initial":
                    rec2["first_notified_at"] = now_iso
                elif x["reason"] == "1month":
                    rec2["month1_notified"] = True
                elif x["reason"] == "6month":
                    rec2["month6_notified"] = True
            state2["items"] = items2
            state2["updated_at"] = now_iso
            _save_json(config.NOTIFY_STATE_JSON, state2)

        return {"ok": True, "sent": 1, "pending": len(pending), "items": pending[:50]}

    # ------------------------------------------------------------------
    # 一括学習
    # ------------------------------------------------------------------
    def train_all(self) -> dict[str, Any]:
        """全品目を順番に学習する（バックグラウンド実行）。"""
        if not self._train_lock.acquire(blocking=False):
            return {"ok": False, "reason": "already_running"}

        def _run() -> None:
            try:
                items = self.list_items()
                failed: list[dict[str, str]] = []
                with self._status_lock:
                    self._train_status.update({
                        "running": True, "total": len(items), "done": 0,
                        "failed": [], "started_at": _now_iso(),
                        "finished_at": None, "current": None,
                    })
                for item_code in items:
                    with self._status_lock:
                        self._train_status["current"] = item_code
                    try:
                        self.train_one(item_code)
                    except Exception as e:
                        failed.append({"item_code": item_code, "error": str(e)})
                    with self._status_lock:
                        self._train_status["done"] += 1
                        self._train_status["failed"] = list(failed)

                now_iso = _now_iso()
                with self._status_lock:
                    self._train_status.update({"running": False, "finished_at": now_iso, "current": None})
                self._save_retrain_state(now_iso)
            except Exception as e:
                with self._status_lock:
                    self._train_status.update({"running": False, "finished_at": _now_iso(), "current": None})
                    self._train_status["failed"] = list(self._train_status["failed"]) + [
                        {"item_code": "(全体)", "error": str(e)}]
            finally:
                self._train_lock.release()

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "started": True}

    def get_train_status(self) -> dict[str, Any]:
        with self._status_lock:
            status = dict(self._train_status)
            status["failed"] = list(status.get("failed", []))
        retrain = _load_json(config.RETRAIN_STATE_JSON, {})
        status["last_retrain_at"] = retrain.get("last_retrain_at")
        status["next_retrain_at"] = retrain.get("next_retrain_at")
        status["auto_retrain_monthly"] = config.AUTO_RETRAIN_MONTHLY
        return status

    # ------------------------------------------------------------------
    # 月次自動再学習
    # ------------------------------------------------------------------
    def _save_retrain_state(self, last_retrain_at: str) -> None:
        next_dt = datetime.fromisoformat(last_retrain_at) + timedelta(days=30)
        _save_json(config.RETRAIN_STATE_JSON, {
            "last_retrain_at": last_retrain_at,
            "next_retrain_at": next_dt.isoformat(timespec="seconds"),
        })

    def _monthly_retrain_loop(self) -> None:
        time.sleep(60)  # 起動直後は待機
        while True:
            try:
                next_dt = _parse_iso(_load_json(config.RETRAIN_STATE_JSON, {}).get("next_retrain_at"))
                if next_dt and datetime.now() >= next_dt:
                    # 学習前に保存済みの MySQL 取込定義で最新データへ同期する
                    if self.store.list_jobs():
                        self.mysql_sync_all()
                    self.train_all()
            except Exception as e:
                print(f"[WARN] monthly retrain check failed: {e}")
            time.sleep(3600)  # 1時間ごとにチェック
