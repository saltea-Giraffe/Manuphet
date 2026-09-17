"""
tests/test_api.py  –  Web API の結合テスト（FastAPI TestClient）

サンプルデータ投入 → 学習 → 予測・需要供給 → データ入力 → CSV 取込 → MySQL 取込
の一連の流れを確認する。MySQL は SQLite エンジンで代用する。
"""
import pytest
from sqlalchemy import create_engine, text

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import manuphet_web  # noqa: E402
from mysql_importer import MySQLImporter  # noqa: E402

client = TestClient(manuphet_web.app)


def _ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def test_index_and_assets():
    r = client.get("/")
    assert r.status_code == 200
    assert "Manuphet" in r.text and "<!-- VERSION -->" not in r.text
    assert client.get("/favicon.ico").headers["content-type"] == "image/x-icon"
    assert client.get("/assets/manuphet_64.png").status_code == 200
    assert client.get("/assets/config.py").status_code == 404


def test_full_flow():
    status = _ok(client.get("/api/status"))
    assert status["version"]

    res = _ok(client.post("/api/data/load_sample"))
    assert res["result"]["demand"]["imported"] > 0
    items = _ok(client.get("/api/items"))["items"]
    assert items == ["SAMPLE-A", "SAMPLE-B", "SAMPLE-C"]
    assert _ok(client.get("/api/items?search=b"))["items"] == ["SAMPLE-B"]

    # 未学習
    assert client.get("/api/forecast_plot?item_code=SAMPLE-A").status_code == 400
    sa = _ok(client.get("/api/supply?item_code=SAMPLE-A"))
    assert sa["has_model"] is False and sa["forecast_1mo"] is None

    # 月次学習 → 予測
    tr = _ok(client.post("/api/train", json={"item_code": "SAMPLE-A"}))
    assert tr["mode"] == "monthly"
    r = client.get("/api/forecast_plot?item_code=SAMPLE-A")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    light = client.get("/api/forecast_plot?item_code=SAMPLE-A&theme=light")
    assert light.status_code == 200 and light.content != r.content
    assert client.get("/api/forecast_plot?item_code=SAMPLE-A&theme=blue").status_code == 422
    sa = _ok(client.get("/api/supply?item_code=SAMPLE-A"))
    assert sa["has_model"] is True
    assert sa["forecast_6mo"] > sa["forecast_1mo"] > 0
    assert sa["product"]["has_inventory"] is True
    comps = {c["component_code"]: c for c in sa["components"]}
    assert set(comps) == {"PART-A01", "PART-A02", "PART-X01"}
    # 部品在庫 60 / 使用数 2 = 30 台分
    assert comps["PART-A02"]["buildable"] == 30
    assert comps["PART-A02"]["days_1mo"] < comps["PART-A01"]["days_1mo"]

    # 週次へ切り替えて学習
    _ok(client.post("/api/weekly", json={"item_code": "SAMPLE-B", "weekly": True}))
    assert "SAMPLE-B" in _ok(client.get("/api/weekly"))["weekly"]
    assert _ok(client.post("/api/train", json={"item_code": "SAMPLE-B"}))["mode"] == "weekly"
    sb = _ok(client.get("/api/supply?item_code=SAMPLE-B"))
    assert sb["mode"] == "weekly" and sb["forecast_1mo"] > 0
    assert client.get("/api/forecast_plot?item_code=SAMPLE-B").status_code == 200

    # 在庫未登録の部品はアラート対象外
    sc_train = client.post("/api/train", json={"item_code": "SAMPLE-C"})
    assert sc_train.status_code == 200
    sc = _ok(client.get("/api/supply?item_code=SAMPLE-C"))
    part_c02 = [c for c in sc["components"] if c["component_code"] == "PART-C02"][0]
    assert part_c02["has_inventory"] is False and part_c02["alert"] is None

    # 除外
    _ok(client.post("/api/excluded", json={"item_code": "SAMPLE-C", "excluded": True}))
    assert "SAMPLE-C" not in _ok(client.get("/api/items"))["items"]
    assert "SAMPLE-C" in _ok(client.get("/api/items?include_excluded=true"))["items"]
    assert client.post("/api/train", json={"item_code": "SAMPLE-C"}).status_code == 400
    _ok(client.post("/api/excluded", json={"item_code": "SAMPLE-C", "excluded": False}))

    status = _ok(client.get("/api/train_status"))
    assert status["running"] is False


def test_data_entry_endpoints():
    new = _ok(client.post("/api/data/demand", json={
        "record_date": "2026-01-10", "item_code": "NEW-ITEM", "quantity": 4, "customer": "<b>X</b>"}))
    rows = _ok(client.get("/api/data/demand?item_code=NEW-ITEM"))["rows"]
    assert rows[0]["customer"] == "<b>X</b>" and rows[0]["source"] == "manual"
    assert "NEW-ITEM" in _ok(client.get("/api/items"))["items"]
    assert client.post("/api/data/demand", json={
        "record_date": "xx", "item_code": "A", "quantity": 1}).status_code == 400
    _ok(client.delete(f"/api/data/demand/{new['id']}"))
    assert "NEW-ITEM" not in _ok(client.get("/api/items"))["items"]

    _ok(client.post("/api/data/inventory", json={"code": "PART/ÜNI 1", "quantity": 12.5}))
    inv = _ok(client.get("/api/data/inventory?search=ÜNI"))
    assert inv["rows"][0]["quantity"] == 12.5
    assert _ok(client.delete("/api/data/inventory", params={"code": "PART/ÜNI 1"}))["ok"] is True

    _ok(client.post("/api/data/bom", json={"item_code": "SAMPLE-A", "component_code": "PART-NEW",
                                           "usage_per_unit": 0.5}))
    bom = _ok(client.get("/api/data/bom?search=PART-NEW"))["rows"]
    assert bom[0]["usage_per_unit"] == 0.5 and bom[0]["component_on_hand"] is None
    _ok(client.delete(f"/api/data/bom/{bom[0]['id']}"))
    assert client.post("/api/data/bom", json={"item_code": "A", "component_code": "B",
                                              "usage_per_unit": -1}).status_code == 400

    csv_text = "日付,品目,数量\n2026-01-01,CSV-ITEM,5\n2026-01-02,CSV-ITEM,x\n"
    r = _ok(client.post("/api/data/import_csv", json={
        "target": "demand", "csv_text": csv_text, "mode": "replace_source", "source_name": "t.csv"}))
    assert (r["imported"], r["skipped"]) == (1, 1)
    r = _ok(client.post("/api/data/import_csv", json={
        "target": "demand", "csv_text": csv_text, "mode": "replace_source", "source_name": "t.csv"}))
    assert r["deleted"] == 1
    assert client.post("/api/data/import_csv", json={
        "target": "demand", "csv_text": "a,b\n1,2\n", "mode": "append"}).status_code == 400

    summary = _ok(client.get("/api/data/summary"))
    assert any(s["source"] == "csv:t.csv" for s in summary["sources"])

    assert client.post("/api/data/clear", json={"target": "bom"}).status_code == 400
    meta = _ok(client.get("/api/data/meta"))
    assert set(meta["targets"]) == {"demand", "inventory", "bom"} and "LIKE" in meta["filter_ops"]


def test_settings_endpoints():
    assert client.post("/api/emails", json={"email": "not-an-address"}).status_code == 400
    _ok(client.post("/api/emails", json={"email": "ops@example.com"}))
    assert _ok(client.get("/api/emails"))["emails"] == ["ops@example.com"]

    _ok(client.post("/api/smtp_config", json={"smtp_server": "smtp.example.com", "smtp_port": 25,
                                              "username": "u@example.com", "password": "secret"}))
    smtp = _ok(client.get("/api/smtp_config"))
    assert smtp["password_set"] is True and "password" not in smtp
    assert smtp["from_addr"] == "u@example.com"

    _ok(client.request("DELETE", "/api/emails", json={"email": "ops@example.com"}))
    res = _ok(client.post("/api/notify_run", json={"force": True}))
    assert res["ok"] is False and "送信先" in res["error"]

    assert _ok(client.post("/api/notify_settings", json={"enabled": False}))["enabled"] is False
    res = _ok(client.post("/api/notify_run", json={"force": False}))
    assert res["skipped"] is True


@pytest.fixture()
def fake_mysql(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'erp.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE orders (order_date TEXT, product_code TEXT, qty INTEGER, "
                          "customer_code TEXT, deleted INTEGER)"))
        conn.execute(text("INSERT INTO orders VALUES ('2025-03-01','ERP-1',10,'K1',0),"
                          "('2025-03-02','ERP-1',99,'K1',1),('2025-04-01','ERP-2',5,'K2',0)"))
    monkeypatch.setattr(MySQLImporter, "from_conf", classmethod(lambda cls, conf: cls(engine)))
    return engine


def test_mysql_endpoints(fake_mysql):
    _ok(client.post("/api/mysql/config", json={"host": "db.local", "port": 3306, "user": "ro",
                                               "database": "erp", "password": "pw"}))
    conf = _ok(client.get("/api/mysql/config"))
    assert conf["password_set"] is True and "password" not in conf
    _ok(client.post("/api/mysql/config", json={"host": "db.local", "port": 3306, "user": "ro",
                                               "database": "erp", "password": None}))
    assert _ok(client.get("/api/mysql/config"))["password_set"] is True

    assert _ok(client.post("/api/mysql/test"))["ok"] is True
    assert _ok(client.get("/api/mysql/tables"))["tables"] == [{"name": "orders", "kind": "table"}]
    cols = [c["name"] for c in _ok(client.get("/api/mysql/columns?table=orders"))["columns"]]
    assert "product_code" in cols
    assert client.get("/api/mysql/columns?table=nope").status_code == 400

    mapping = {"record_date": "order_date", "item_code": "product_code", "quantity": "qty",
               "customer": "customer_code"}
    filters = [{"column": "deleted", "op": "=", "value": 0}]
    prev = _ok(client.post("/api/mysql/preview", json={"table": "orders", "target": "demand",
                                                       "mapping": mapping, "filters": filters}))
    assert prev["total"] == 2 and prev["columns"] == list(mapping)
    raw = _ok(client.post("/api/mysql/preview", json={"table": "orders"}))
    assert raw["total"] == 3

    imp = _ok(client.post("/api/mysql/import", json={"table": "orders", "target": "demand", "mapping": mapping,
                                                     "filters": filters, "mode": "replace_source",
                                                     "save_as": "ERP受注"}))
    assert imp["imported"] == 2 and imp["job_id"]
    assert "ERP-1" in _ok(client.get("/api/items"))["items"]

    jobs = _ok(client.get("/api/mysql/jobs"))["jobs"]
    assert jobs[0]["name"] == "ERP受注" and jobs[0]["last_count"] == 2
    run = _ok(client.post(f"/api/mysql/jobs/{jobs[0]['id']}/run"))
    assert run["imported"] == 2 and run["deleted"] == 2
    sync = _ok(client.post("/api/mysql/sync_all"))
    assert sync["ok"] is True and sync["results"][0]["job"] == "ERP受注"
    rows = _ok(client.get("/api/data/demand?item_code=ERP-1"))["rows"]
    assert len(rows) == 1 and rows[0]["source"] == "mysql:ERP受注"

    bad = client.post("/api/mysql/import", json={"table": "orders", "target": "demand",
                                                 "mapping": {"item_code": "product_code"}})
    assert bad.status_code == 400
    _ok(client.delete(f"/api/mysql/jobs/{jobs[0]['id']}"))
    assert _ok(client.get("/api/mysql/jobs"))["jobs"] == []
