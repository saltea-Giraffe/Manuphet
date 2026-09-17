"""
data_store.py  –  Manuphet ローカルデータストア（SQLite）

予測に使うデータはすべてこのストアに登録する。登録経路は次の3つ:
  - Web UI の「データ入力」タブ（手入力 / CSV 取込）
  - Web UI の「MySQL連携」タブ（mysql_importer.py 経由）
  - cli.py（CSV 取込・サンプルデータ投入）

テーブル:
  demand_records  需要実績（日付・品目コード・数量・顧客）
  inventory       在庫（製品・部品共通。コード単位で保持）
  bom             部品構成（品目 → 部品、1台あたり使用数）
  import_jobs     MySQL 取込定義（再同期用）
"""
from __future__ import annotations

import io
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

# ---------------------------------------------------------------------------
# 取込対象と項目定義（Web UI / MySQL 取込 / CSV 取込で共通）
# ---------------------------------------------------------------------------
TARGETS: dict[str, dict[str, Any]] = {
    "demand": {
        "label": "需要実績",
        "fields": [
            {"key": "record_date", "label": "日付",       "type": "date",   "required": True},
            {"key": "item_code",   "label": "品目コード", "type": "text",   "required": True},
            {"key": "quantity",    "label": "数量",       "type": "number", "required": True},
            {"key": "customer",    "label": "顧客",       "type": "text",   "required": False},
        ],
    },
    "inventory": {
        "label": "在庫",
        "fields": [
            {"key": "code",     "label": "品目/部品コード", "type": "text",   "required": True},
            {"key": "quantity", "label": "在庫数",          "type": "number", "required": True},
        ],
    },
    "bom": {
        "label": "部品構成",
        "fields": [
            {"key": "item_code",      "label": "品目コード",      "type": "text",   "required": True},
            {"key": "component_code", "label": "部品コード",      "type": "text",   "required": True},
            {"key": "usage_per_unit", "label": "1台あたり使用数", "type": "number", "required": False},
        ],
    },
}

IMPORT_MODES = ("append", "replace_source", "replace_all")

# CSV ヘッダーの別名（小文字で比較）
_CSV_ALIASES: dict[str, list[str]] = {
    "record_date":    ["record_date", "date", "ds", "日付", "年月日", "計上日"],
    "item_code":      ["item_code", "item", "product", "product_code", "品目コード", "品目", "製品コード", "製品"],
    "quantity":       ["quantity", "qty", "数量", "在庫数", "数"],
    "customer":       ["customer", "customer_id", "client", "顧客", "顧客コード", "得意先"],
    "code":           ["code", "item_code", "component_code", "コード", "品目コード", "部品コード"],
    "component_code": ["component_code", "component", "part", "part_code", "部品コード", "部品"],
    "usage_per_unit": ["usage_per_unit", "usage", "qty_per_unit", "使用数", "員数", "1台あたり使用数"],
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS demand_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_date TEXT    NOT NULL,
    item_code   TEXT    NOT NULL,
    quantity    REAL    NOT NULL,
    customer    TEXT,
    source      TEXT    NOT NULL DEFAULT 'manual',
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_demand_item_date ON demand_records(item_code, record_date);
CREATE INDEX IF NOT EXISTS idx_demand_source    ON demand_records(source);

CREATE TABLE IF NOT EXISTS inventory (
    code       TEXT PRIMARY KEY,
    quantity   REAL NOT NULL,
    source     TEXT NOT NULL DEFAULT 'manual',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bom (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code      TEXT NOT NULL,
    component_code TEXT NOT NULL,
    usage_per_unit REAL NOT NULL DEFAULT 1,
    source         TEXT NOT NULL DEFAULT 'manual',
    updated_at     TEXT NOT NULL,
    UNIQUE(item_code, component_code)
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    target      TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    mapping     TEXT NOT NULL,
    filters     TEXT NOT NULL DEFAULT '[]',
    mode        TEXT NOT NULL DEFAULT 'replace_source',
    last_run_at TEXT,
    last_count  INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def required_fields(target: str) -> list[str]:
    return [f["key"] for f in _target_spec(target)["fields"] if f["required"]]


def all_fields(target: str) -> list[str]:
    return [f["key"] for f in _target_spec(target)["fields"]]


def _target_spec(target: str) -> dict[str, Any]:
    spec = TARGETS.get(target)
    if spec is None:
        raise ValueError(f"不明な取込対象です: {target}")
    return spec


# ---------------------------------------------------------------------------
# 値の正規化
# ---------------------------------------------------------------------------

def _clean_text(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    return s.mask(s.isin(["", "nan", "NaN", "None", "<NA>"]))


def normalize_frame(target: str, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """取込対象の列を型変換し、不正な行を除外する。(正規化済み DataFrame, 除外行数) を返す。"""
    _target_spec(target)
    missing = [k for k in required_fields(target) if k not in df.columns]
    if missing:
        raise ValueError(f"必須項目が不足しています: {', '.join(missing)}")

    total = len(df)
    d = pd.DataFrame(index=df.index)

    if target == "demand":
        dates = pd.to_datetime(df["record_date"], errors="coerce")
        d["record_date"] = dates.dt.strftime("%Y-%m-%d")
        d["item_code"] = _clean_text(df["item_code"])
        d["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
        d["customer"] = _clean_text(df["customer"]) if "customer" in df.columns else pd.NA
        d = d.dropna(subset=["record_date", "item_code", "quantity"])
        d["customer"] = d["customer"].astype(object).where(d["customer"].notna(), None)

    elif target == "inventory":
        d["code"] = _clean_text(df["code"])
        d["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
        d = d.dropna(subset=["code", "quantity"])
        # 同じコードが複数行（倉庫・ロケーション別など）ある場合は合計する
        d = d.groupby("code", as_index=False, sort=False)["quantity"].sum()

    else:  # bom
        d["item_code"] = _clean_text(df["item_code"])
        d["component_code"] = _clean_text(df["component_code"])
        if "usage_per_unit" in df.columns:
            usage = pd.to_numeric(df["usage_per_unit"], errors="coerce").fillna(1.0)
        else:
            usage = pd.Series(1.0, index=df.index)
        d["usage_per_unit"] = usage
        d = d.dropna(subset=["item_code", "component_code"])
        d = d[d["usage_per_unit"] > 0]
        # 同じ組み合わせが複数行ある場合は使用数を合計する
        d = d.groupby(["item_code", "component_code"], as_index=False, sort=False)["usage_per_unit"].sum()

    d = d.reset_index(drop=True)
    skipped = total - len(d) if target == "demand" else max(0, total - int(_source_rows(target, df, d)))
    return d, skipped


def _source_rows(target: str, raw: pd.DataFrame, cleaned: pd.DataFrame) -> int:
    """集約前の有効行数（除外行数の算出用）。"""
    if target == "inventory":
        code = _clean_text(raw["code"])
        qty = pd.to_numeric(raw["quantity"], errors="coerce")
        return int((code.notna() & qty.notna()).sum())
    item = _clean_text(raw["item_code"])
    comp = _clean_text(raw["component_code"])
    if "usage_per_unit" in raw.columns:
        usage = pd.to_numeric(raw["usage_per_unit"], errors="coerce").fillna(1.0)
    else:
        usage = pd.Series(1.0, index=raw.index)
    return int((item.notna() & comp.notna() & (usage > 0)).sum())


def read_csv_text(target: str, text: str) -> pd.DataFrame:
    """CSV テキストを読み込み、ヘッダーを取込対象の項目名へ対応付ける。"""
    _target_spec(target)
    text = text.lstrip("﻿")
    if not text.strip():
        raise ValueError("CSV が空です")
    raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    lower_cols = {str(c).strip().lower(): c for c in raw.columns}

    rename: dict[str, str] = {}
    for key in all_fields(target):
        for alias in _CSV_ALIASES.get(key, [key]):
            col = lower_cols.get(alias.lower())
            if col is not None and col not in rename:
                rename[col] = key
                break
    df = raw.rename(columns=rename)
    missing = [k for k in required_fields(target) if k not in df.columns]
    if missing:
        labels = {f["key"]: f["label"] for f in _target_spec(target)["fields"]}
        need = ", ".join(f"{k}（{labels[k]}）" for k in missing)
        raise ValueError(f"CSV に必要な列が見つかりません: {need}")
    return df[[k for k in all_fields(target) if k in df.columns]]


# ---------------------------------------------------------------------------
# ストア本体
# ---------------------------------------------------------------------------

class DataStore:
    """SQLite ベースのローカルデータストア。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # 需要実績
    # ------------------------------------------------------------------
    def add_demand(self, record_date: str, item_code: str, quantity: float,
                   customer: str | None = None, source: str = "manual") -> int:
        df = pd.DataFrame([{"record_date": record_date, "item_code": item_code,
                            "quantity": quantity, "customer": customer}])
        clean, _ = normalize_frame("demand", df)
        if clean.empty:
            raise ValueError("日付・品目コード・数量を正しく入力してください")
        r = clean.iloc[0]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO demand_records (record_date, item_code, quantity, customer, source, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (r["record_date"], r["item_code"], float(r["quantity"]), r["customer"], source, _now_iso()),
            )
            return int(cur.lastrowid)

    def list_demand(self, item_code: str = "", search: str = "",
                    limit: int = 100, offset: int = 0) -> dict[str, Any]:
        where, params = [], []
        if item_code:
            where.append("item_code = ?")
            params.append(item_code)
        if search:
            where.append("(item_code LIKE ? OR IFNULL(customer, '') LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM demand_records {clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT id, record_date, item_code, quantity, customer, source FROM demand_records {clause}"
                " ORDER BY record_date DESC, id DESC LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)],
            ).fetchall()
        return {"total": int(total), "rows": [dict(r) for r in rows]}

    def delete_demand(self, record_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM demand_records WHERE id = ?", (int(record_id),))
            return cur.rowcount > 0

    def load_demand_frame(self) -> pd.DataFrame:
        """予測用の需要実績（数量 > 0 の行のみ）。"""
        with self._connect() as conn:
            df = pd.read_sql_query(
                "SELECT record_date, item_code, quantity, customer FROM demand_records WHERE quantity > 0",
                conn,
            )
        df["record_date"] = pd.to_datetime(df["record_date"], errors="coerce")
        return df.dropna(subset=["record_date"]).reset_index(drop=True)

    def list_item_codes(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT item_code FROM demand_records WHERE quantity > 0 ORDER BY item_code"
            ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # 在庫
    # ------------------------------------------------------------------
    def upsert_inventory(self, code: str, quantity: float, source: str = "manual") -> None:
        clean, _ = normalize_frame("inventory", pd.DataFrame([{"code": code, "quantity": quantity}]))
        if clean.empty:
            raise ValueError("コードと在庫数を正しく入力してください")
        self._upsert_inventory_rows(clean, source)

    def _upsert_inventory_rows(self, clean: pd.DataFrame, source: str,
                               conn: sqlite3.Connection | None = None) -> int:
        now = _now_iso()
        rows = [(r.code, float(r.quantity), source, now) for r in clean.itertuples(index=False)]
        sql = ("INSERT INTO inventory (code, quantity, source, updated_at) VALUES (?, ?, ?, ?)"
               " ON CONFLICT(code) DO UPDATE SET quantity = excluded.quantity,"
               " source = excluded.source, updated_at = excluded.updated_at")
        if conn is not None:
            conn.executemany(sql, rows)
        else:
            with self._connect() as c:
                c.executemany(sql, rows)
        return len(rows)

    def list_inventory(self, search: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        clause, params = "", []
        if search:
            clause, params = "WHERE code LIKE ?", [f"%{search}%"]
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM inventory {clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT code, quantity, source, updated_at FROM inventory {clause}"
                " ORDER BY code LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)],
            ).fetchall()
        return {"total": int(total), "rows": [dict(r) for r in rows]}

    def delete_inventory(self, code: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM inventory WHERE code = ?", (code,))
            return cur.rowcount > 0

    def get_inventory(self, code: str) -> float | None:
        with self._connect() as conn:
            row = conn.execute("SELECT quantity FROM inventory WHERE code = ?", (code,)).fetchone()
        return None if row is None else float(row[0])

    # ------------------------------------------------------------------
    # 部品構成
    # ------------------------------------------------------------------
    def upsert_bom(self, item_code: str, component_code: str,
                   usage_per_unit: float | None = 1.0, source: str = "manual") -> None:
        df = pd.DataFrame([{"item_code": item_code, "component_code": component_code,
                            "usage_per_unit": 1.0 if usage_per_unit in (None, "") else usage_per_unit}])
        clean, _ = normalize_frame("bom", df)
        if clean.empty:
            raise ValueError("品目コード・部品コードと、0 より大きい使用数を入力してください")
        self._upsert_bom_rows(clean, source)

    def _upsert_bom_rows(self, clean: pd.DataFrame, source: str,
                         conn: sqlite3.Connection | None = None) -> int:
        now = _now_iso()
        rows = [(r.item_code, r.component_code, float(r.usage_per_unit), source, now)
                for r in clean.itertuples(index=False)]
        sql = ("INSERT INTO bom (item_code, component_code, usage_per_unit, source, updated_at)"
               " VALUES (?, ?, ?, ?, ?)"
               " ON CONFLICT(item_code, component_code) DO UPDATE SET"
               " usage_per_unit = excluded.usage_per_unit, source = excluded.source,"
               " updated_at = excluded.updated_at")
        if conn is not None:
            conn.executemany(sql, rows)
        else:
            with self._connect() as c:
                c.executemany(sql, rows)
        return len(rows)

    def list_bom(self, item_code: str = "", search: str = "",
                 limit: int = 100, offset: int = 0) -> dict[str, Any]:
        where, params = [], []
        if item_code:
            where.append("b.item_code = ?")
            params.append(item_code)
        if search:
            where.append("(b.item_code LIKE ? OR b.component_code LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM bom b {clause}", params).fetchone()[0]
            rows = conn.execute(
                "SELECT b.id, b.item_code, b.component_code, b.usage_per_unit, b.source,"
                " i.quantity AS component_on_hand"
                f" FROM bom b LEFT JOIN inventory i ON i.code = b.component_code {clause}"
                " ORDER BY b.item_code, b.component_code LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)],
            ).fetchall()
        return {"total": int(total), "rows": [dict(r) for r in rows]}

    def delete_bom(self, bom_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM bom WHERE id = ?", (int(bom_id),))
            return cur.rowcount > 0

    def get_components(self, item_code: str) -> pd.DataFrame:
        """品目の部品一覧と部品在庫（在庫未登録の部品は 0 とみなす）。"""
        with self._connect() as conn:
            df = pd.read_sql_query(
                "SELECT b.component_code, b.usage_per_unit, IFNULL(i.quantity, 0) AS on_hand,"
                " CASE WHEN i.code IS NULL THEN 0 ELSE 1 END AS has_inventory"
                " FROM bom b LEFT JOIN inventory i ON i.code = b.component_code"
                " WHERE b.item_code = ? ORDER BY b.component_code",
                conn, params=[item_code],
            )
        return df

    # ------------------------------------------------------------------
    # 一括取込
    # ------------------------------------------------------------------
    def import_frame(self, target: str, df: pd.DataFrame, source: str,
                     mode: str = "append") -> dict[str, int]:
        """正規化してから一括登録する。

        mode:
          append          既存データを残して追加（在庫・部品構成は同じキーを上書き）
          replace_source  同じ取込元（source）の既存データを削除してから登録
          replace_all     対象テーブルの全データを削除してから登録
        """
        if mode not in IMPORT_MODES:
            raise ValueError(f"不明な取込モードです: {mode}")
        clean, skipped = normalize_frame(target, df)
        table = {"demand": "demand_records", "inventory": "inventory", "bom": "bom"}[target]

        with self._connect() as conn:
            deleted = 0
            if mode == "replace_all":
                deleted = conn.execute(f"DELETE FROM {table}").rowcount
            elif mode == "replace_source":
                deleted = conn.execute(f"DELETE FROM {table} WHERE source = ?", (source,)).rowcount

            if target == "demand":
                now = _now_iso()
                rows = [(r.record_date, r.item_code, float(r.quantity), r.customer, source, now)
                        for r in clean.itertuples(index=False)]
                conn.executemany(
                    "INSERT INTO demand_records (record_date, item_code, quantity, customer, source, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
                imported = len(rows)
            elif target == "inventory":
                imported = self._upsert_inventory_rows(clean, source, conn=conn)
            else:
                imported = self._upsert_bom_rows(clean, source, conn=conn)

        return {"imported": int(imported), "skipped": int(skipped), "deleted": int(max(deleted, 0))}

    def clear(self, target: str) -> int:
        table = {"demand": "demand_records", "inventory": "inventory", "bom": "bom"}.get(target)
        if table is None:
            raise ValueError(f"不明な対象です: {target}")
        with self._connect() as conn:
            return int(conn.execute(f"DELETE FROM {table}").rowcount)

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            d = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT item_code), MIN(record_date), MAX(record_date)"
                " FROM demand_records"
            ).fetchone()
            inv = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
            bom = conn.execute("SELECT COUNT(*) FROM bom").fetchone()[0]
            sources = conn.execute(
                "SELECT 'demand' AS target, source, COUNT(*) AS n FROM demand_records GROUP BY source"
                " UNION ALL SELECT 'inventory', source, COUNT(*) FROM inventory GROUP BY source"
                " UNION ALL SELECT 'bom', source, COUNT(*) FROM bom GROUP BY source"
            ).fetchall()
        return {
            "demand_count": int(d[0]),
            "item_count": int(d[1]),
            "date_from": d[2],
            "date_to": d[3],
            "inventory_count": int(inv),
            "bom_count": int(bom),
            "sources": [dict(r) for r in sources],
        }

    # ------------------------------------------------------------------
    # MySQL 取込定義
    # ------------------------------------------------------------------
    def save_job(self, name: str, target: str, table_name: str,
                 mapping: dict[str, str], filters: list[dict[str, Any]], mode: str) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("取込定義の名前を入力してください")
        _target_spec(target)
        if mode not in IMPORT_MODES:
            raise ValueError(f"不明な取込モードです: {mode}")
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO import_jobs (name, target, table_name, mapping, filters, mode, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET target = excluded.target, table_name = excluded.table_name,"
                " mapping = excluded.mapping, filters = excluded.filters, mode = excluded.mode,"
                " updated_at = excluded.updated_at",
                (name, target, table_name, json.dumps(mapping, ensure_ascii=False),
                 json.dumps(filters, ensure_ascii=False), mode, now, now),
            )
            row = conn.execute("SELECT id FROM import_jobs WHERE name = ?", (name,)).fetchone()
        return int(row[0])

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["mapping"] = json.loads(d.get("mapping") or "{}")
        d["filters"] = json.loads(d.get("filters") or "[]")
        return d

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM import_jobs ORDER BY name").fetchall()
        return [self._job_from_row(r) for r in rows]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (int(job_id),)).fetchone()
        return None if row is None else self._job_from_row(row)

    def delete_job(self, job_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM import_jobs WHERE id = ?", (int(job_id),))
            return cur.rowcount > 0

    def mark_job_run(self, job_id: int, count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE import_jobs SET last_run_at = ?, last_count = ? WHERE id = ?",
                (_now_iso(), int(count), int(job_id)),
            )


# ---------------------------------------------------------------------------
# サンプルデータ
# ---------------------------------------------------------------------------
SAMPLE_FILES = {"demand": "demand.csv", "inventory": "inventory.csv", "bom": "bom.csv"}


def load_sample_data(store: DataStore, sample_dir: str | Path) -> dict[str, dict[str, int]]:
    """examples/sample_data の CSV を source='sample' として登録する（再実行しても重複しない）。"""
    sample_dir = Path(sample_dir)
    result: dict[str, dict[str, int]] = {}
    for target, filename in SAMPLE_FILES.items():
        path = sample_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"サンプルデータが見つかりません: {path}")
        df = read_csv_text(target, path.read_text(encoding="utf-8"))
        result[target] = store.import_frame(target, df, source="sample", mode="replace_source")
    return result
