from __future__ import annotations

import pytest

from app.config import Settings
from app.dashboards import (
    add_saved_view_to_dashboard,
    create_dashboard,
    get_dashboard,
    list_dashboard_items,
    list_dashboards,
    reorder_dashboard_items,
)
from app.label_filters import LabelFilters
from app.main import create_app
from app.saved_views import save_saved_view
from tests.test_support import TEST_DB_PATH


@pytest.fixture
def app_ctx(test_db):
    app = create_app(
        settings=Settings(
            prometheus_url="http://example",
            saved_db_path=str(TEST_DB_PATH),
        ),
        run_migrations=False,
    )
    with app.app_context():
        yield app


def test_list_dashboards_returns_empty_initially(app_ctx) -> None:
    assert list_dashboards() == []


def test_create_dashboard_returns_new_dashboard(app_ctx) -> None:
    dashboard = create_dashboard("Ops")
    assert dashboard.id is not None
    assert dashboard.name == "Ops"


def test_create_dashboard_strips_whitespace(app_ctx) -> None:
    dashboard = create_dashboard("  Ops  ")
    assert dashboard.name == "Ops"


def test_create_dashboard_rejects_blank_name(app_ctx) -> None:
    with pytest.raises(ValueError):
        create_dashboard("   ")


def test_get_dashboard_returns_none_for_missing(app_ctx) -> None:
    assert get_dashboard(999999) is None


def test_get_dashboard_returns_model(app_ctx) -> None:
    created = create_dashboard("Ops")
    fetched = get_dashboard(created.id)
    assert fetched is not None
    assert fetched.id == created.id


def test_list_dashboards_returns_dashboards_with_item_count(app_ctx) -> None:
    create_dashboard("Beta")
    create_dashboard("Alpha")
    results = list_dashboards()
    assert [d.name for d, _count in results] == ["Alpha", "Beta"]
    assert all(count == 0 for _d, count in results)


def test_add_saved_view_attaches_and_assigns_position(app_ctx) -> None:
    view, _ = save_saved_view(
        title="t",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="q",
    )
    dashboard = create_dashboard("Ops")
    assert add_saved_view_to_dashboard(dashboard.id, view.id) is True
    items = list_dashboard_items(dashboard.id)
    assert len(items) == 1
    assert items[0].saved_view.id == view.id
    assert items[0].position == 1


def test_add_saved_view_returns_false_for_missing_either_side(app_ctx) -> None:
    dashboard = create_dashboard("Ops")
    assert add_saved_view_to_dashboard(dashboard.id, 999999) is False
    assert add_saved_view_to_dashboard(999999, 1) is False


def test_add_saved_view_twice_bumps_position(app_ctx) -> None:
    view_a, _ = save_saved_view(
        title="a",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="qa",
    )
    view_b, _ = save_saved_view(
        title="b",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="qb",
    )
    dashboard = create_dashboard("Ops")
    add_saved_view_to_dashboard(dashboard.id, view_a.id)
    add_saved_view_to_dashboard(dashboard.id, view_b.id)
    # Re-adding view_a bumps its position to the next free slot, moving it to the tail.
    add_saved_view_to_dashboard(dashboard.id, view_a.id)
    items = list_dashboard_items(dashboard.id)
    assert len(items) == 2
    assert items[-1].saved_view.id == view_a.id


def test_list_dashboard_items_returns_eager_loaded_saved_views(app_ctx) -> None:
    view, _ = save_saved_view(
        title="t",
        metrics_csv="up,node_cpu_seconds_total",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(per_metric={"up": {"job": "api"}}),
        query_string="q",
    )
    dashboard = create_dashboard("Ops")
    add_saved_view_to_dashboard(dashboard.id, view.id)

    items = list_dashboard_items(dashboard.id)
    assert items[0].saved_view.title == "t"
    assert items[0].saved_view.metrics == ["up", "node_cpu_seconds_total"]
    assert items[0].saved_view.label_filters.per_metric == {"up": {"job": "api"}}


def test_reorder_dashboard_items_rejects_empty_list(app_ctx) -> None:
    dashboard = create_dashboard("Ops")
    assert reorder_dashboard_items(dashboard.id, []) is False


def test_reorder_dashboard_items_rejects_mismatched_ids(app_ctx) -> None:
    view, _ = save_saved_view(
        title="t",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="q",
    )
    dashboard = create_dashboard("Ops")
    add_saved_view_to_dashboard(dashboard.id, view.id)
    items = list_dashboard_items(dashboard.id)
    assert reorder_dashboard_items(dashboard.id, [items[0].id, 999999]) is False


def test_reorder_dashboard_items_updates_positions(app_ctx) -> None:
    view_a, _ = save_saved_view(
        title="a",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="qa",
    )
    view_b, _ = save_saved_view(
        title="b",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="qb",
    )
    dashboard = create_dashboard("Ops")
    add_saved_view_to_dashboard(dashboard.id, view_a.id)
    add_saved_view_to_dashboard(dashboard.id, view_b.id)
    items = list_dashboard_items(dashboard.id)
    a_item, b_item = items[0], items[1]

    assert reorder_dashboard_items(dashboard.id, [b_item.id, a_item.id]) is True
    reordered = list_dashboard_items(dashboard.id)
    assert [it.id for it in reordered] == [b_item.id, a_item.id]
    assert reordered[0].position == 1
    assert reordered[1].position == 2
