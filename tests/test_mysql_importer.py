"""
tests/test_mysql_importer.py  –  mysql_importer.py のテスト

MySQL サーバーが無くても動くよう、SQLAlchemy の SQLite エンジンで代用する
（クエリは SQLAlchemy Core で組み立てているため、方言に依存しない部分を検証できる）。
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from data_store import DataStore
from mysql_importer import MySQLImporter, build_engine


@pytest.fixture()
def importer(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'src.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE sales (id INTEGER PRIMARY KEY, "order date" DATE, sku TEXT, qty NUMERIC, '
            "client TEXT, status TEXT)"
        ))
        conn.execute(text(
            'INSERT INTO sales ("order date", sku, qty, client, status) VALUES '
            "('2025-01-05','A-1',3,'C1','done'),('2025-01-06','A-1',2,'C2','cancel'),"
            "('2025-02-01','B-2',7,NULL,'done'),('2024-12-31','A-1',1,'C1','done'),"
            "('2025-02-03','B-2',NULL,'C3','done')"
        ))
        conn.execute(text("CREATE TABLE parts_on_hand (part TEXT, warehouse TEXT, on_hand REAL)"))
        conn.execute(text(
            "INSERT INTO parts_on_hand VALUES ('P1','W1',10),('P1','W2',5),('P2','W1',1)"
        ))
        conn.execute(text("CREATE VIEW done_sales AS SELECT * FROM sales WHERE status = 'done'"))
    imp = MySQLImporter(engine)
    yield imp
    imp.dispose()


MAPPING = {"record_date": "order date", "item_code": "sku", "quantity": "qty", "customer": "client"}


def test_list_tables_and_columns(importer):
    tables = importer.list_tables()
    assert {"name": "sales", "kind": "table"} in tables
    assert {"name": "done_sales", "kind": "view"} in tables
    cols = [c["name"] for c in importer.list_columns("sales")]
    assert cols == ["id", "order date", "sku", "qty", "client", "status"]


def test_unknown_table_and_column_are_rejected(importer):
    with pytest.raises(ValueError):
        importer.list_columns("sales; DROP TABLE sales")
    with pytest.raises(ValueError):
        importer.preview_mapped("sales", "demand", {**MAPPING, "quantity": "qty) --"})
    with pytest.raises(ValueError):
        importer.preview_table("sales", [{"column": "status", "op": "OR 1=1", "value": "x"}])


def test_missing_required_mapping(importer):
    with pytest.raises(ValueError, match="数量"):
        importer.preview_mapped("sales", "demand", {"record_date": "order date", "item_code": "sku"})


def test_preview_table_with_filters(importer):
    res = importer.preview_table("sales", [{"column": "status", "op": "=", "value": "done"}], limit=2)
    assert res["total"] == 4
    assert len(res["rows"]) == 2
    assert res["columns"][1] == "order date"


def test_filter_operators(importer):
    def count(filters):
        return importer.count("sales", filters)

    assert count([{"column": "sku", "op": "LIKE", "value": "A%"}]) == 3
    assert count([{"column": "sku", "op": "NOT LIKE", "value": "A%"}]) == 2
    assert count([{"column": "client", "op": "IN", "value": "C1, C3"}]) == 3
    assert count([{"column": "client", "op": "NOT IN", "value": ["C1"]}]) == 2
    assert count([{"column": "client", "op": "IS NULL"}]) == 1
    assert count([{"column": "client", "op": "IS NOT NULL"}]) == 4
    assert count([{"column": "order date", "op": ">=", "value": "2025-01-01"},
                  {"column": "order date", "op": "<", "value": "2025-02-01"}]) == 2
    assert count([{"column": "status", "op": "!=", "value": "cancel"}]) == 4
    # 値のバインドで SQL が注入されないこと
    assert count([{"column": "sku", "op": "=", "value": "A-1' OR '1'='1"}]) == 0
    with pytest.raises(ValueError):
        count([{"column": "sku", "op": "IN", "value": " , "}])
    with pytest.raises(ValueError):
        count([{"column": "sku", "op": ">", "value": ""}])


def test_import_demand_with_filters_and_resync(importer, tmp_path):
    store = DataStore(tmp_path / "store.db")
    filters = [{"column": "status", "op": "=", "value": "done"},
               {"column": "order date", "op": ">=", "value": "2025-01-01"}]
    r = importer.import_into(store, "sales", "demand", MAPPING, filters, "replace_source", "mysql:sales")
    # 条件一致 3 行のうち数量 NULL の 1 行は除外
    assert (r["fetched"], r["imported"], r["skipped"]) == (3, 2, 1)
    rows = store.list_demand()["rows"]
    assert {(x["record_date"], x["item_code"], x["quantity"]) for x in rows} == {
        ("2025-01-05", "A-1", 3.0), ("2025-02-01", "B-2", 7.0)}
    assert all(x["source"] == "mysql:sales" for x in rows)

    # 再同期しても重複しない
    importer.import_into(store, "sales", "demand", MAPPING, filters, "replace_source", "mysql:sales")
    assert store.list_demand()["total"] == 2


def test_import_from_view_and_inventory_aggregation(importer, tmp_path):
    store = DataStore(tmp_path / "store.db")
    importer.import_into(store, "done_sales", "demand", MAPPING, [], "append", "mysql:view")
    assert store.list_demand()["total"] == 3

    r = importer.import_into(store, "parts_on_hand", "inventory",
                             {"code": "part", "quantity": "on_hand"}, [], "append", "mysql:inv")
    assert r["imported"] == 2
    assert store.get_inventory("P1") == 15


def test_fetch_mapped_converts_decimal():
    from mysql_importer import _json_value
    assert _json_value(Decimal("1.5")) == 1.5
    assert _json_value(dt.date(2025, 1, 2)) == "2025-01-02"
    assert _json_value(b"\xff") == "ff"
    assert _json_value(None) is None


def test_build_engine_quotes_password():
    engine = build_engine({"host": "db.example", "port": 3306, "user": "u",
                           "password": "p@ss:w/rd", "database": "d", "driver": "pymysql"})
    assert engine.url.password == "p@ss:w/rd"
    assert engine.url.host == "db.example"
    assert "p@ss" not in repr(engine.url)
    with pytest.raises(ValueError):
        build_engine({"host": "", "database": "d"})
    with pytest.raises(ValueError):
        build_engine({"host": "h", "database": "d", "driver": "odbc"})
