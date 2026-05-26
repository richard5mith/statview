from __future__ import annotations

import pytest

from app.config import Settings
from app.extensions import db
from app.label_filters import LabelFilters
from app.main import create_app
from app.models import Dashboard, DashboardItem, SavedView
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


def test_saved_view_metrics_property_splits_csv(app_ctx) -> None:
    view = SavedView(
        title="t",
        metrics_csv="up,node_cpu_seconds_total",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=0,
        label_filters_json="{}",
        query_string="metrics=up",
    )
    assert view.metrics == ["up", "node_cpu_seconds_total"]


def test_saved_view_metrics_strips_whitespace_and_drops_empty(app_ctx) -> None:
    view = SavedView(
        title="t",
        metrics_csv="up,  ,node_cpu_seconds_total ,",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=0,
        label_filters_json="{}",
        query_string="metrics=up",
    )
    assert view.metrics == ["up", "node_cpu_seconds_total"]


def test_saved_view_label_filters_property_returns_parsed_instance(app_ctx) -> None:
    view = SavedView(
        title="t",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=0,
        label_filters_json='{"*":{"env":"prod"},"up":{"job":"api"}}',
        query_string="metrics=up",
    )
    filters = view.label_filters
    assert isinstance(filters, LabelFilters)
    assert filters.shared == {"env": "prod"}
    assert filters.per_metric == {"up": {"job": "api"}}


def test_dashboard_items_relationship(app_ctx) -> None:
    view = SavedView(
        title="t",
        metrics_csv="up",
        window_amount=1,
        window_unit="week",
        step_amount=1,
        step_unit="hour",
        compare_enabled=0,
        label_filters_json="{}",
        query_string="metrics=up",
    )
    db.session.add(view)
    db.session.commit()

    dashboard = Dashboard(name="Ops")
    db.session.add(dashboard)
    db.session.commit()

    item = DashboardItem(
        dashboard_id=dashboard.id,
        saved_view_id=view.id,
        position=1,
    )
    db.session.add(item)
    db.session.commit()

    fetched = db.session.get(Dashboard, dashboard.id)
    assert fetched is not None
    assert len(fetched.items) == 1
    assert fetched.items[0].saved_view.id == view.id
    assert fetched.items[0].dashboard.id == dashboard.id
