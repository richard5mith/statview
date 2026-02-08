from __future__ import annotations

from app.saved_views import SavedViewStore


def test_saved_view_store_save_list_and_remove(tmp_path) -> None:
    db_path = tmp_path / "saved.sqlite3"
    store = SavedViewStore(str(db_path))

    assert store.list() == []

    first, created = store.save(
        title="up • 1 week @ 1 hour • no compare",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters={"job": "api"},
        query_string="metrics=up&window_amount=1&window_unit=week&step_amount=1&step_unit=hour&compare_enabled=0&label_filters=%7B%22job%22%3A%22api%22%7D",
    )
    assert created is True
    assert first["metrics"] == ["up"]
    assert first["label_filters"] == {"job": "api"}

    second, created_again = store.save(
        title="updated title",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters={"job": "api"},
        query_string="metrics=up&window_amount=1&window_unit=week&step_amount=1&step_unit=hour&compare_enabled=0&label_filters=%7B%22job%22%3A%22api%22%7D",
    )
    assert created_again is False
    assert second["id"] == first["id"]
    assert second["title"] == "updated title"
    assert len(store.list()) == 1

    assert store.remove(first["id"]) is True
    assert store.list() == []


def test_saved_view_store_dashboards_and_items(tmp_path) -> None:
    db_path = tmp_path / "saved.sqlite3"
    store = SavedViewStore(str(db_path))

    saved, _ = store.save(
        title="up • 1 week @ 1 hour • no compare",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters={"job": "api"},
        query_string="metrics=up&window_amount=1&window_unit=week&step_amount=1&step_unit=hour&compare_enabled=0&label_filters=%7B%22job%22%3A%22api%22%7D",
    )

    dashboard = store.create_dashboard("Operations")
    assert dashboard["name"] == "Operations"
    assert store.get_dashboard(dashboard["id"]) is not None

    assert store.add_saved_view_to_dashboard(dashboard["id"], saved["id"]) is True
    items = store.list_dashboard_items(dashboard["id"])
    assert len(items) == 1
    assert items[0]["saved_view_id"] == saved["id"]
    assert items[0]["metrics"] == ["up"]

    assert store.reorder_dashboard_items(dashboard["id"], [items[0]["dashboard_item_id"]]) is True

    fetched = store.get(saved["id"])
    assert fetched is not None
    assert fetched["title"] == "up • 1 week @ 1 hour • no compare"

    renamed = store.rename(saved["id"], "Renamed")
    assert renamed is not None
    assert renamed["title"] == "Renamed"


def test_saved_view_store_edge_cases(tmp_path) -> None:
    db_path = tmp_path / "saved.sqlite3"
    store = SavedViewStore(str(db_path))

    assert store.get(999999) is None
    assert store.rename(999999, "x") is None

    dashboard = store.create_dashboard("Edge")
    assert store.add_saved_view_to_dashboard(dashboard["id"], 999999) is False
    assert store.add_saved_view_to_dashboard(999999, 1) is False

    assert store.reorder_dashboard_items(dashboard["id"], []) is False
