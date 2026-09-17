"""
mysql_importer.py  –  MySQL からの選択取込

MySQL のテーブル（ビュー含む）を参照し、必要な列・行だけを選んで
ローカルデータストア（data_store.DataStore）へ登録する。

安全性:
  - テーブル名・列名は実際のスキーマ（リフレクション結果）に存在するものだけを受け付ける
  - SQL は SQLAlchemy Core で組み立て、値はすべてバインドパラメータで渡す
  - 参照のみ（SELECT）。MySQL 側のデータは変更しない

SQLAlchemy の Engine を差し替えられるため、テストでは SQLite で動作確認できる。
"""
from __future__ import annotations

import datetime as _dt
import decimal
from typing import Any

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select
from sqlalchemy.engine import URL, Engine

from data_store import TARGETS, DataStore, all_fields, required_fields

PREVIEW_LIMIT_MAX = 500

# 絞り込み条件で使える演算子
FILTER_OPS = ("=", "!=", ">", ">=", "<", "<=", "LIKE", "NOT LIKE", "IN", "NOT IN", "IS NULL", "IS NOT NULL")

_DRIVERS = {
    "mysqlconnector": ("mysql+mysqlconnector", "connection_timeout"),
    "pymysql":        ("mysql+pymysql",        "connect_timeout"),
}


def build_engine(conf: dict[str, Any], timeout: int = 10) -> Engine:
    """接続設定から SQLAlchemy Engine を生成する。"""
    if not conf.get("host") or not conf.get("database"):
        raise ValueError("MySQL のホストとデータベース名を設定してください")
    driver = str(conf.get("driver") or "mysqlconnector")
    if driver not in _DRIVERS:
        raise ValueError(f"未対応のドライバです: {driver}（mysqlconnector / pymysql）")
    drivername, timeout_arg = _DRIVERS[driver]
    url = URL.create(
        drivername=drivername,
        username=conf.get("user") or None,
        password=conf.get("password") or None,   # URL.create で特殊文字（@ など）も安全に扱える
        host=conf["host"],
        port=int(conf.get("port") or 3306),
        database=conf["database"],
        query={"charset": "utf8mb4"},
    )
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600,
                         connect_args={timeout_arg: int(timeout)})


def _json_value(v: Any) -> Any:
    """プレビュー表示用に JSON 化できる値へ変換する。"""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, (pd.Timestamp, _dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.hex()
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


class MySQLImporter:
    """MySQL のテーブルを参照して、選択した列・行をデータストアへ取り込む。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_conf(cls, conf: dict[str, Any]) -> "MySQLImporter":
        return cls(build_engine(conf))

    def dispose(self) -> None:
        self.engine.dispose()

    # ------------------------------------------------------------------
    # スキーマ参照
    # ------------------------------------------------------------------
    def test_connection(self) -> dict[str, Any]:
        with self.engine.connect() as conn:
            version = None
            if self.engine.dialect.name == "mysql":
                version = conn.exec_driver_sql("SELECT VERSION()").scalar()
        return {"ok": True, "server_version": version, "tables": len(self.list_tables())}

    def list_tables(self) -> list[dict[str, str]]:
        insp = inspect(self.engine)
        tables = [{"name": t, "kind": "table"} for t in insp.get_table_names()]
        try:
            views = [{"name": v, "kind": "view"} for v in insp.get_view_names()]
        except NotImplementedError:
            views = []
        return sorted(tables + views, key=lambda x: x["name"].lower())

    def list_columns(self, table: str) -> list[dict[str, Any]]:
        t = self._table(table)
        return [{"name": c.name, "type": str(c.type), "nullable": bool(c.nullable)} for c in t.columns]

    def _table(self, table: str) -> Table:
        names = {x["name"] for x in self.list_tables()}
        if table not in names:
            raise ValueError(f"テーブルが見つかりません: {table}")
        return Table(table, MetaData(), autoload_with=self.engine)

    # ------------------------------------------------------------------
    # クエリ組み立て
    # ------------------------------------------------------------------
    @staticmethod
    def _column(t: Table, name: str):
        if name not in t.c:
            raise ValueError(f"列が見つかりません: {name}")
        return t.c[name]

    def _where(self, t: Table, filters: list[dict[str, Any]] | None) -> list[Any]:
        clauses = []
        for f in filters or []:
            col_name = str(f.get("column") or "").strip()
            op = str(f.get("op") or "=").strip().upper()
            if not col_name:
                continue
            if op not in FILTER_OPS:
                raise ValueError(f"未対応の演算子です: {op}")
            col = self._column(t, col_name)
            value = f.get("value")

            if op == "IS NULL":
                clauses.append(col.is_(None))
                continue
            if op == "IS NOT NULL":
                clauses.append(col.is_not(None))
                continue
            if value is None or (isinstance(value, str) and value == "" and op not in ("=", "!=")):
                raise ValueError(f"絞り込み条件の値が空です: {col_name} {op}")
            if op in ("IN", "NOT IN"):
                values = value if isinstance(value, list) else [v.strip() for v in str(value).split(",")]
                values = [v for v in values if str(v) != ""]
                if not values:
                    raise ValueError(f"{op} の値をカンマ区切りで入力してください: {col_name}")
                clauses.append(col.in_(values) if op == "IN" else col.not_in(values))
            elif op == "LIKE":
                clauses.append(col.like(str(value)))
            elif op == "NOT LIKE":
                clauses.append(col.not_like(str(value)))
            elif op == "=":
                clauses.append(col == value)
            elif op == "!=":
                clauses.append(col != value)
            elif op == ">":
                clauses.append(col > value)
            elif op == ">=":
                clauses.append(col >= value)
            elif op == "<":
                clauses.append(col < value)
            else:
                clauses.append(col <= value)
        return clauses

    def _validate_mapping(self, target: str, mapping: dict[str, str]) -> dict[str, str]:
        if target not in TARGETS:
            raise ValueError(f"不明な取込対象です: {target}")
        allowed = set(all_fields(target))
        cleaned = {k: v for k, v in (mapping or {}).items() if k in allowed and v}
        missing = [k for k in required_fields(target) if k not in cleaned]
        if missing:
            labels = {f["key"]: f["label"] for f in TARGETS[target]["fields"]}
            raise ValueError("必須項目の列を選択してください: " + ", ".join(labels[k] for k in missing))
        return cleaned

    def _mapped_select(self, table: str, target: str, mapping: dict[str, str],
                       filters: list[dict[str, Any]] | None):
        t = self._table(table)
        mapping = self._validate_mapping(target, mapping)
        cols = [self._column(t, col).label(field) for field, col in mapping.items()]
        stmt = select(*cols)
        where = self._where(t, filters)
        if where:
            stmt = stmt.where(*where)
        return t, stmt, where

    # ------------------------------------------------------------------
    # プレビュー / 取込
    # ------------------------------------------------------------------
    def preview_table(self, table: str, filters: list[dict[str, Any]] | None = None,
                      limit: int = 20) -> dict[str, Any]:
        """テーブルの生データを先頭から数行表示する。"""
        t = self._table(table)
        limit = max(1, min(int(limit), PREVIEW_LIMIT_MAX))
        stmt = select(t)
        where = self._where(t, filters)
        if where:
            stmt = stmt.where(*where)
        with self.engine.connect() as conn:
            result = conn.execute(stmt.limit(limit))
            columns = list(result.keys())
            rows = [[_json_value(v) for v in r] for r in result.fetchall()]
        return {"columns": columns, "rows": rows, "total": self.count(table, filters)}

    def count(self, table: str, filters: list[dict[str, Any]] | None = None) -> int:
        t = self._table(table)
        stmt = select(func.count()).select_from(t)
        where = self._where(t, filters)
        if where:
            stmt = stmt.where(*where)
        with self.engine.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)

    def fetch_mapped(self, table: str, target: str, mapping: dict[str, str],
                     filters: list[dict[str, Any]] | None = None,
                     limit: int | None = None) -> pd.DataFrame:
        """対応付けた列だけを取得し、取込対象の項目名を列名にした DataFrame を返す。"""
        _, stmt, _ = self._mapped_select(table, target, mapping, filters)
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        with self.engine.connect() as conn:
            result = conn.execute(stmt)
            df = pd.DataFrame(result.fetchall(), columns=list(result.keys()))
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].map(lambda v: float(v) if isinstance(v, decimal.Decimal) else v)
        return df

    def preview_mapped(self, table: str, target: str, mapping: dict[str, str],
                       filters: list[dict[str, Any]] | None = None, limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(int(limit), PREVIEW_LIMIT_MAX))
        df = self.fetch_mapped(table, target, mapping, filters, limit=limit)
        return {
            "columns": list(df.columns),
            "rows": [[_json_value(v) for v in row] for row in df.itertuples(index=False)],
            "total": self.count(table, filters),
        }

    def import_into(self, store: DataStore, table: str, target: str, mapping: dict[str, str],
                    filters: list[dict[str, Any]] | None, mode: str, source: str) -> dict[str, int]:
        df = self.fetch_mapped(table, target, mapping, filters)
        result = store.import_frame(target, df, source=source, mode=mode)
        result["fetched"] = int(len(df))
        return result
