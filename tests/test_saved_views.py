from __future__ import annotations

import pytest

from app.config import Settings
from app.label_filters import LabelFilters
from app.main import create_app
from app.saved_views import (
    get_saved_view,
    list_saved_views,
    remove_saved_view,
    rename_saved_view,
    save_saved_view,
)
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


def test_list_saved_views_returns_empty_initially(app_ctx) -> None:
    assert list_saved_views() == []


def test_list_saved_views_returns_saved(app_ctx) -> None:
    save_saved_view(
        title="up • 1 week @ 1 hour",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(shared={"job": "api"}),
        query_string="metrics=up",
    )
    views = list_saved_views()
    assert len(views) == 1
    assert views[0].title == "up • 1 week @ 1 hour"
    assert views[0].metrics == ["up"]
    assert views[0].label_filters.shared == {"job": "api"}


def test_list_saved_views_search_filters_by_title_or_metrics(app_ctx) -> None:
    save_saved_view(
        title="api errors",
        metrics_csv="http_errors_total",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="q=1",
    )
    save_saved_view(
        title="cpu usage",
        metrics_csv="node_cpu_seconds_total",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="q=2",
    )
    assert [v.title for v in list_saved_views(search="api")] == ["api errors"]
    assert [v.title for v in list_saved_views(search="node_cpu")] == ["cpu usage"]


def test_get_saved_view_returns_none_for_missing(app_ctx) -> None:
    assert get_saved_view(999999) is None


def test_get_saved_view_returns_model(app_ctx) -> None:
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
    fetched = get_saved_view(view.id)
    assert fetched is not None
    assert fetched.id == view.id


def test_remove_saved_view_returns_true_when_deleted(app_ctx) -> None:
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
    assert remove_saved_view(view.id) is True
    assert get_saved_view(view.id) is None


def test_remove_saved_view_returns_false_for_missing(app_ctx) -> None:
    assert remove_saved_view(999999) is False


def test_rename_saved_view_updates_title(app_ctx) -> None:
    view, _ = save_saved_view(
        title="old",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="q",
    )
    renamed = rename_saved_view(view.id, "new")
    assert renamed is not None
    assert renamed.title == "new"


def test_rename_saved_view_returns_none_for_blank_or_missing(app_ctx) -> None:
    view, _ = save_saved_view(
        title="old",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=False,
        label_filters=LabelFilters(),
        query_string="q",
    )
    assert rename_saved_view(view.id, "   ") is None
    assert rename_saved_view(999999, "x") is None
