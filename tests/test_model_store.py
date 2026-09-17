"""
tests/test_model_store.py  –  model/store.py の単体テスト
"""
from pathlib import Path

from model.store import list_saved_items, load_model, model_path, save_model


class TestModelStore:
    def test_save_and_load_roundtrip(self, tmp_path):
        dummy_model = {"weights": [1, 2, 3]}
        meta = {"feature_cols": ["a", "b"], "use_log1p": False}

        path = save_model(tmp_path, "ITEM-X", dummy_model, model_type="monthly", meta=meta)
        assert Path(path).exists()

        loaded = load_model(tmp_path, "ITEM-X", model_type="monthly")
        assert loaded["model"] == dummy_model
        assert loaded["meta"]["use_log1p"] is False

    def test_load_nonexistent_returns_none(self, tmp_path):
        assert load_model(tmp_path, "NO_SUCH_ITEM") is None

    def test_unsafe_characters_in_item_code(self, tmp_path):
        path = model_path(tmp_path, 'A/B:C*"D', "weekly")
        assert Path(path).name == "weekly_A_B_C__D.pkl"
        save_model(tmp_path, 'A/B:C*"D', {}, model_type="weekly")
        assert load_model(tmp_path, 'A/B:C*"D', model_type="weekly") is not None

    def test_list_saved_items(self, tmp_path):
        save_model(tmp_path, "A-001", {}, model_type="monthly")
        save_model(tmp_path, "A-002", {}, model_type="weekly")
        save_model(tmp_path, "A-001", {}, model_type="weekly")

        assert {"A-001", "A-002"} <= set(list_saved_items(tmp_path))
        assert list_saved_items(tmp_path, model_type="weekly") == ["A-001", "A-002"]
        assert list_saved_items(tmp_path, model_type="monthly") == ["A-001"]
