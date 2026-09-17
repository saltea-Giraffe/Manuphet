"""
tests/test_data_store.py  –  data_store.py の単体テスト
"""
import pandas as pd
import pytest

import config
from data_store import DataStore, load_sample_data, normalize_frame, read_csv_text


@pytest.fixture()
def store(tmp_path):
    return DataStore(tmp_path / "test.db")


class TestDemand:
    def test_add_list_delete(self, store):
        rid = store.add_demand("2025-01-15", " ITEM-1 ", 5, "C1")
        store.add_demand("2025/02/01", "ITEM-1", 3)
        res = store.list_demand(item_code="ITEM-1")
        assert res["total"] == 2
        assert res["rows"][0]["record_date"] == "2025-02-01"   # 新しい順
        assert res["rows"][1]["item_code"] == "ITEM-1"         # 前後の空白は除去
        assert res["rows"][1]["customer"] == "C1"
        assert store.delete_demand(rid) is True
        assert store.list_demand()["total"] == 1

    def test_invalid_input(self, store):
        with pytest.raises(ValueError):
            store.add_demand("not-a-date", "ITEM-1", 1)
        with pytest.raises(ValueError):
            store.add_demand("2025-01-01", "", 1)
        with pytest.raises(ValueError):
            store.add_demand("2025-01-01", "ITEM-1", "abc")

    def test_load_frame_excludes_non_positive(self, store):
        store.add_demand("2025-01-01", "A", 2)
        store.add_demand("2025-01-02", "A", 0)
        store.add_demand("2025-01-03", "B", -1)
        df = store.load_demand_frame()
        assert list(df.columns) == ["record_date", "item_code", "quantity", "customer"]
        assert len(df) == 1
        assert pd.api.types.is_datetime64_any_dtype(df["record_date"])
        assert store.list_item_codes() == ["A"]

    def test_search_matches_customer(self, store):
        store.add_demand("2025-01-01", "A", 1, "ACME")
        store.add_demand("2025-01-01", "B", 1, "OTHER")
        assert store.list_demand(search="acm")["total"] == 1


class TestInventoryAndBom:
    def test_inventory_upsert(self, store):
        store.upsert_inventory("P1", 10)
        store.upsert_inventory("P1", 7)
        assert store.get_inventory("P1") == 7
        assert store.get_inventory("NONE") is None
        assert store.delete_inventory("P1") is True
        assert store.get_inventory("P1") is None

    def test_bom_and_components(self, store):
        store.upsert_bom("ITEM", "P1", 2)
        store.upsert_bom("ITEM", "P2", None)
        store.upsert_inventory("P1", 100)
        comps = store.get_components("ITEM")
        assert comps["component_code"].tolist() == ["P1", "P2"]
        assert comps["usage_per_unit"].tolist() == [2.0, 1.0]
        assert comps["on_hand"].tolist() == [100.0, 0.0]
        assert comps["has_inventory"].tolist() == [1, 0]
        with pytest.raises(ValueError):
            store.upsert_bom("ITEM", "P3", 0)
        listed = store.list_bom(item_code="ITEM")
        assert listed["rows"][0]["component_on_hand"] == 100
        assert store.delete_bom(listed["rows"][0]["id"]) is True


class TestImport:
    def test_import_modes(self, store):
        df = pd.DataFrame({"record_date": ["2025-01-01", "bad", "2025-01-03"],
                           "item_code": ["A", "A", None], "quantity": [1, 2, 3]})
        r = store.import_frame("demand", df, source="csv:a.csv", mode="append")
        assert r == {"imported": 1, "skipped": 2, "deleted": 0}

        store.add_demand("2025-01-05", "A", 9)          # manual
        df2 = pd.DataFrame({"record_date": ["2025-02-01", "2025-02-02"],
                            "item_code": ["A", "A"], "quantity": [4, 5]})
        r = store.import_frame("demand", df2, source="csv:a.csv", mode="replace_source")
        assert r["deleted"] == 1 and r["imported"] == 2
        assert store.list_demand()["total"] == 3      # manual 1 + csv 2

        r = store.import_frame("demand", df2, source="x", mode="replace_all")
        assert r["deleted"] == 3
        assert store.list_demand()["total"] == 2

    def test_inventory_duplicates_are_summed(self, store):
        df = pd.DataFrame({"code": ["P1", "P1", "P2", ""], "quantity": [1, 2, "x", 5]})
        r = store.import_frame("inventory", df, source="mysql:inv", mode="append")
        assert r["imported"] == 1
        assert r["skipped"] == 2
        assert store.get_inventory("P1") == 3

    def test_unknown_mode_or_target(self, store):
        with pytest.raises(ValueError):
            store.import_frame("demand", pd.DataFrame(), source="s", mode="bogus")
        with pytest.raises(ValueError):
            normalize_frame("nope", pd.DataFrame())

    def test_read_csv_aliases(self):
        df = read_csv_text("demand", "﻿日付,品目コード,数量,得意先\n2025-01-01,A,3,X\n")
        assert list(df.columns) == ["record_date", "item_code", "quantity", "customer"]
        with pytest.raises(ValueError, match="数量"):
            read_csv_text("demand", "date,item\n2025-01-01,A\n")

    def test_summary_and_clear(self, store):
        store.add_demand("2025-01-01", "A", 1)
        store.add_demand("2025-03-01", "B", 1)
        store.upsert_inventory("A", 1)
        s = store.summary()
        assert (s["demand_count"], s["item_count"], s["date_from"], s["date_to"]) == (2, 2, "2025-01-01", "2025-03-01")
        assert store.clear("demand") == 2
        with pytest.raises(ValueError):
            store.clear("import_jobs")


class TestJobs:
    def test_save_update_delete(self, store):
        jid = store.save_job("job1", "demand", "sales", {"record_date": "d"}, [], "append")
        jid2 = store.save_job("job1", "inventory", "inv", {"code": "c"}, [{"column": "c", "op": "=", "value": 1}],
                              "replace_source")
        assert jid == jid2
        job = store.get_job(jid)
        assert job["target"] == "inventory"
        assert job["filters"][0]["value"] == 1
        store.mark_job_run(jid, 42)
        assert store.list_jobs()[0]["last_count"] == 42
        assert store.delete_job(jid) is True
        with pytest.raises(ValueError):
            store.save_job(" ", "demand", "t", {}, [], "append")


def test_load_sample_data_is_idempotent(store):
    first = load_sample_data(store, config.SAMPLE_DATA_DIR)
    load_sample_data(store, config.SAMPLE_DATA_DIR)
    s = store.summary()
    assert s["demand_count"] == first["demand"]["imported"] > 0
    assert s["item_count"] == 3
    assert s["bom_count"] == first["bom"]["imported"]
