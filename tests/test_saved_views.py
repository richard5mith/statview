from __future__ import annotations

import pytest

from app.saved_views import SavedViewStore
from tests.test_support import TEST_DB_PATH

pytestmark = pytest.mark.usefixtures("test_db")


def test_saved_view_store_save_list_and_remove() -> None:
    store = SavedViewStore(str(TEST_DB_PATH))

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


def test_saved_view_store_dashboards_and_items() -> None:
    store = SavedViewStore(str(TEST_DB_PATH))

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


def test_saved_view_store_edge_cases() -> None:
    store = SavedViewStore(str(TEST_DB_PATH))

    assert store.get(999999) is None
    assert store.rename(999999, "x") is None

    dashboard = store.create_dashboard("Edge")
    assert store.add_saved_view_to_dashboard(dashboard["id"], 999999) is False
    assert store.add_saved_view_to_dashboard(999999, 1) is False

    assert store.reorder_dashboard_items(dashboard["id"], []) is False


def test_saved_view_store_force_create_allows_duplicate_query_strings() -> None:
    store = SavedViewStore(str(TEST_DB_PATH))

    first, created_first = store.save(
        title="first",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters={},
        query_string="metrics=up&window_amount=1&window_unit=week&step_amount=1&step_unit=hour",
    )
    assert created_first is True

    second, created_second = store.save(
        title="second",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters={},
        query_string="metrics=up&window_amount=1&window_unit=week&step_amount=1&step_unit=hour",
        force_create=True,
    )
    assert created_second is True
    assert second["id"] != first["id"]
    assert len(store.list()) == 2


def test_saved_view_store_persists_per_metric_label_filters() -> None:
    store = SavedViewStore(str(TEST_DB_PATH))

    saved, created = store.save(
        title="multi",
        metrics_csv="up,node_cpu_seconds_total",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters={
            "up": {"job": "api"},
            "node_cpu_seconds_total": {"instance": "a"},
        },
        query_string=(
            "metrics=up%2Cnode_cpu_seconds_total&window_amount=1&window_unit=week"
            "&step_amount=1&step_unit=hour&compare_enabled=0"
        ),
    )
    assert created is True
    assert saved["label_filters"] == {
        "up": {"job": "api"},
        "node_cpu_seconds_total": {"instance": "a"},
    }

    fetched = store.get(saved["id"])
    assert fetched is not None
    assert fetched["label_filters"] == saved["label_filters"]
