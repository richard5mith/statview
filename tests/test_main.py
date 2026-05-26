from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.prometheus import PrometheusClient, PrometheusUnreachableError
from tests.test_support import TEST_DB_PATH

pytestmark = pytest.mark.usefixtures("test_db")


class FakePrometheusClient:
    def list_metric_catalog(self) -> list[dict[str, str]]:
        return [
            {"name": "node_cpu_seconds_total", "type": "counter"},
            {"name": "up", "type": "gauge"},
            {"name": "http_request_duration_seconds_bucket", "type": "histogram"},
        ]

    def metric_label_options(self, metric_name: str) -> dict[str, list[str]]:
        if metric_name != "up":
            return {}
        return {
            "instance": ["a", "b"],
            "job": ["api", "worker"],
        }

    def list_alerts(self) -> list[dict[str, str]]:
        return [
            {
                "name": "HighErrorRate",
                "state": "firing",
                "active_at": "2026-02-08T12:00:00Z",
                "value": "1",
                "summary": "Errors are high",
            }
        ]

    def query_range(
        self,
        query: str,
        window: str,
        step: str,
        end_offset: str = "0s",
        series_name: str | None = None,
    ) -> dict[str, Any]:
        _ = query
        display_name = series_name or query
        delta = 0.0 if end_offset == "0s" else 10.0
        points = [{"t": 1.0, "v": 3.0 + delta}, {"t": 2.0, "v": 4.0 + delta}]
        return {
            "metric": display_name,
            "query": query,
            "window": window,
            "step": step,
            "end_offset": end_offset,
            "start": 1.0,
            "end": 2.0,
            "series": [
                {
                    "label": f"{display_name} (job=test)",
                    "labels": {"job": "test"},
                    "points": points,
                }
            ],
            "aggregate": {
                "label": display_name,
                "points": points,
            },
        }


def _test_app():
    return create_app(
        settings=Settings(
            prometheus_url="http://example",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        prometheus_client=FakePrometheusClient(),
        run_migrations=False,
    )


def test_index_renders_metric_list() -> None:
    app = _test_app()
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"node_cpu_seconds_total" in response.data
    assert b"counter" in response.data
    assert b"StatView" in response.data


def test_index_does_not_auto_select_first_metric() -> None:
    app = _test_app()
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"Add a metric to begin charting." in response.data


def test_removing_last_metric_results_in_empty_selection() -> None:
    app = _test_app()
    response = app.test_client().get("/", query_string={"metrics": "up", "remove_metric": "up"})

    assert response.status_code == 200
    assert b"Add a metric to begin charting." in response.data


def test_metrics_page_renders_metric_list() -> None:
    app = _test_app()
    response = app.test_client().get("/metrics")

    assert response.status_code == 200
    assert b"Metrics" in response.data
    assert b"node_cpu_seconds_total" in response.data


def test_save_view_api_and_saved_page() -> None:
    app = _test_app()
    client = app.test_client()

    payload = {
        "metrics": "up",
        "window_amount": "1",
        "window_unit": "week",
        "step_amount": "1",
        "step_unit": "hour",
        "compare_enabled": "1",
        "label_filters": json.dumps({"job": "api"}),
    }

    first = client.post("/api/saved", json=payload)
    assert first.status_code == 201
    first_body = first.get_json()
    assert first_body["created"] is True
    assert first_body["id"] > 0
    assert f"saved_id={first_body['id']}" in first_body["url"]
    assert "metrics=up" in first_body["url"]

    saved_page = client.get("/saved")
    assert saved_page.status_code == 200
    assert b"No saved views yet." not in saved_page.data
    assert b"up" in saved_page.data
    assert b"data-saved-delete" in saved_page.data
    assert b"data-rename-trigger" in saved_page.data

    second = client.post("/api/saved", json=payload)
    assert second.status_code == 200
    assert second.get_json()["created"] is False


def test_save_view_api_updates_existing_row_when_saved_id_provided() -> None:
    app = _test_app()
    client = app.test_client()

    create_payload = {
        "metrics": "up",
        "window_amount": "1",
        "window_unit": "week",
        "step_amount": "1",
        "step_unit": "hour",
        "compare_enabled": "0",
        "label_filters": json.dumps({"job": "api"}),
    }
    created = client.post("/api/saved", json=create_payload)
    assert created.status_code == 201
    created_body = created.get_json()
    saved_id = created_body["id"]

    update_payload = {
        **create_payload,
        "saved_id": str(saved_id),
        "step_amount": "2",
    }
    updated = client.post("/api/saved", json=update_payload)
    assert updated.status_code == 200
    updated_body = updated.get_json()
    assert updated_body["created"] is False
    assert updated_body["id"] == saved_id
    assert "step_amount=2" in updated_body["url"]


def test_save_view_api_can_force_create_new_entry() -> None:
    app = _test_app()
    client = app.test_client()

    payload = {
        "metrics": "up",
        "window_amount": "1",
        "window_unit": "week",
        "step_amount": "1",
        "step_unit": "hour",
        "compare_enabled": "0",
        "title": "Original Save",
    }
    first = client.post("/api/saved", json=payload)
    assert first.status_code == 201
    first_body = first.get_json()

    second = client.post(
        "/api/saved",
        json={
            **payload,
            "title": "Copy Save",
            "save_as_new": "1",
        },
    )
    assert second.status_code == 201
    second_body = second.get_json()
    assert second_body["created"] is True
    assert second_body["id"] != first_body["id"]
    assert "saved_id=" in second_body["url"]

    from app.saved_views import list_saved_views

    with app.app_context():
        saved_entries = list_saved_views()
    assert len(saved_entries) == 2


def test_save_view_api_accepts_custom_title() -> None:
    app = _test_app()
    client = app.test_client()

    created = client.post(
        "/api/saved",
        json={
            "metrics": "up",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "compare_enabled": "0",
            "title": "Temperature Dashboard Card",
        },
    )
    assert created.status_code == 201
    body = created.get_json()
    assert body["title"] == "Temperature Dashboard Card"


def test_delete_saved_view_api_removes_entry() -> None:
    app = _test_app()
    client = app.test_client()

    created = client.post(
        "/api/saved",
        json={
            "metrics": "up",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "compare_enabled": "0",
        },
    )
    saved_id = created.get_json()["id"]

    deleted = client.delete(f"/api/saved/{saved_id}")
    assert deleted.status_code == 200
    assert deleted.get_json() == {"deleted": True, "id": saved_id}

    missing = client.delete(f"/api/saved/{saved_id}")
    assert missing.status_code == 404


def test_rename_saved_view_api_updates_title() -> None:
    app = _test_app()
    client = app.test_client()

    created = client.post(
        "/api/saved",
        json={
            "metrics": "up",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "compare_enabled": "0",
        },
    )
    saved_id = created.get_json()["id"]

    renamed = client.post(
        f"/api/saved/{saved_id}/rename",
        json={"title": "Renamed Saved Stat"},
    )
    assert renamed.status_code == 200
    assert renamed.get_json() == {"id": saved_id, "title": "Renamed Saved Stat"}


def test_rename_saved_view_api_validates_inputs() -> None:
    app = _test_app()
    client = app.test_client()

    missing_title = client.post("/api/saved/1/rename", json={"title": ""})
    assert missing_title.status_code == 400

    missing_saved = client.post("/api/saved/999999/rename", json={"title": "x"})
    assert missing_saved.status_code == 404


def test_dashboards_page_create_and_list() -> None:
    app = _test_app()
    client = app.test_client()

    created = client.post("/dashboards", data={"name": "Ops"}, follow_redirects=False)
    assert created.status_code == 303
    location = created.headers["Location"]
    assert "/dashboards/" in location

    listing = client.get("/dashboards")
    assert listing.status_code == 200
    assert b"Ops" in listing.data


def test_dashboards_page_create_validation_and_duplicate() -> None:
    app = _test_app()
    client = app.test_client()

    missing_name = client.post("/dashboards", data={"name": ""}, follow_redirects=False)
    assert missing_name.status_code == 400

    first = client.post("/dashboards", data={"name": "Duplicate"}, follow_redirects=False)
    assert first.status_code == 303
    second = client.post("/dashboards", data={"name": "Duplicate"}, follow_redirects=False)
    assert second.status_code == 409


def test_add_saved_to_dashboard_and_render_detail() -> None:
    app = _test_app()
    client = app.test_client()

    saved = client.post(
        "/api/saved",
        json={
            "metrics": "up",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "compare_enabled": "1",
        },
    ).get_json()
    saved_id = saved["id"]

    dashboard_redirect = client.post(
        "/dashboards",
        data={"name": "Primary"},
        follow_redirects=False,
    )
    dashboard_id = int(dashboard_redirect.headers["Location"].rstrip("/").split("/")[-1])

    added = client.post(
        "/dashboards/add-item",
        data={"dashboard_id": str(dashboard_id), "saved_id": str(saved_id)},
        follow_redirects=False,
    )
    assert added.status_code == 303

    detail = client.get(f"/dashboards/{dashboard_id}")
    assert detail.status_code == 200
    assert b"dashboard-chart-" in detail.data
    assert b"Drag to reorder" in detail.data
    assert b"Add saved stat" in detail.data
    assert b"data-add-saved-dialog" in detail.data
    assert b"data-rename-trigger" in detail.data


def test_dashboard_reorder_api_updates_positions() -> None:
    app = _test_app()
    client = app.test_client()

    first_saved = client.post(
        "/api/saved",
        json={
            "metrics": "up",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "compare_enabled": "0",
        },
    ).get_json()["id"]
    second_saved = client.post(
        "/api/saved",
        json={
            "metrics": "node_cpu_seconds_total",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "compare_enabled": "0",
        },
    ).get_json()["id"]

    dashboard_redirect = client.post(
        "/dashboards",
        data={"name": "Order Test"},
        follow_redirects=False,
    )
    dashboard_id = int(dashboard_redirect.headers["Location"].rstrip("/").split("/")[-1])
    client.post(
        "/dashboards/add-item",
        data={"dashboard_id": str(dashboard_id), "saved_id": str(first_saved)},
    )
    client.post(
        "/dashboards/add-item",
        data={"dashboard_id": str(dashboard_id), "saved_id": str(second_saved)},
    )

    from app.dashboards import list_dashboard_items

    with app.app_context():
        items = list_dashboard_items(dashboard_id)
        original_ids = [item.id for item in items]
    reversed_ids = list(reversed(original_ids))

    reordered = client.post(
        f"/api/dashboards/{dashboard_id}/reorder",
        json={"item_ids": reversed_ids},
    )
    assert reordered.status_code == 200

    with app.app_context():
        updated = list_dashboard_items(dashboard_id)
        updated_ids = [item.id for item in updated]
    assert updated_ids == reversed_ids


def test_dashboard_reorder_api_validation_and_not_found_dashboard() -> None:
    app = _test_app()
    client = app.test_client()

    bad_payload = client.post("/api/dashboards/1/reorder", json={"item_ids": []})
    assert bad_payload.status_code == 400

    missing_dashboard = client.get("/dashboards/999999", follow_redirects=False)
    assert missing_dashboard.status_code == 303
    assert missing_dashboard.headers["Location"].endswith("/dashboards")


def test_add_saved_to_dashboard_invalid_payload_redirects() -> None:
    app = _test_app()
    client = app.test_client()

    response = client.post(
        "/dashboards/add-item",
        data={"dashboard_id": "", "saved_id": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/saved")


def test_starred_redirects_to_saved_page() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/starred",
        follow_redirects=False,
    )
    assert response.status_code == 308
    assert response.headers["Location"].endswith("/saved")


def test_alerts_page_renders_alerts() -> None:
    app = _test_app()
    response = app.test_client().get("/alerts")

    assert response.status_code == 200
    assert b"HighErrorRate" in response.data


def test_metric_panel_renders_with_payload() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/metric-panel?metric=up&window_amount=1&window_unit=week&step_amount=1&step_unit=hour"
    )

    assert response.status_code == 200
    assert b"Main chart" in response.data
    assert b"Tag filters" in response.data
    assert b'data-role="payload"' in response.data


def test_metric_data_api_returns_json() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/api/metric-data",
        query_string={
            "metric": "up",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "compare_enabled": "1",
            "label_filters": json.dumps({"job": "api"}),
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["metric"] == "up"
    assert body["window"]["duration"] == "1w"
    assert body["step"]["duration"] == "1h"
    assert body["compare"]["enabled"] is True
    assert body["compare"]["chart"] is not None
    assert body["filters"]["selected"] == {"job": "api"}
    assert len(body["presets"]) == 6
    assert body["presets"][0]["previous_offset"] == body["presets"][0]["window"]
    assert (
        body["presets"][0]["previous_chart"]["end_offset"] == body["presets"][0]["previous_offset"]
    )
    assert len(body["summary_rows"]) == 2


def test_view_data_api_returns_json() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/api/view-data",
        query_string={
            "metrics": "up,node_cpu_seconds_total",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "compare_enabled": "1",
            "label_filters": json.dumps({"job": "api"}),
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["metrics"] == ["up", "node_cpu_seconds_total"]
    assert len(body["payloads"]) == 2
    assert len(body["metric_summaries"]) == 2
    assert body["metric_summaries"][0]["metric"] == "up"
    assert body["metric_summaries"][1]["metric"] == "node_cpu_seconds_total"
    assert body["summary_metric"] == "up"
    assert body["filters"]["selected"] == {"job": "api"}
    payloads = {item["metric"]: item for item in body["payloads"]}
    assert "rate(" not in payloads["up"]["primary"]["query"]
    assert "sum(rate(" in payloads["node_cpu_seconds_total"]["primary"]["query"]


def test_index_view_renders_tag_filters_for_selected_metric() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/",
        query_string={"metrics": "up", "label_filters": json.dumps({"job": "api"})},
    )

    assert response.status_code == 200
    assert b"Tag filters" in response.data
    assert b'data-tag-filter-label="job"' in response.data
    assert b'data-tag-filter-metric="up"' in response.data


def test_view_data_api_supports_per_metric_label_filters() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/api/view-data",
        query_string={
            "metrics": "up,node_cpu_seconds_total",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
            "label_filters": json.dumps(
                {
                    "up": {"job": "api"},
                    "node_cpu_seconds_total": {"instance": "a"},
                }
            ),
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    payloads = {item["metric"]: item for item in body["payloads"]}
    assert payloads["up"]["filters"]["selected"] == {"job": "api"}
    assert payloads["node_cpu_seconds_total"]["filters"]["selected"] == {}


def test_view_data_histogram_metric_uses_histogram_quantile() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/api/view-data",
        query_string={
            "metrics": "http_request_duration_seconds_bucket",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "hour",
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    primary_query = body["payloads"][0]["primary"]["query"]
    assert "histogram_quantile(0.95" in primary_query
    assert "sum by (le) (rate(" in primary_query


def test_view_data_counter_uses_selected_minute_rate_window() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/api/view-data",
        query_string={
            "metrics": "node_cpu_seconds_total",
            "window_amount": "1",
            "window_unit": "week",
            "step_amount": "1",
            "step_unit": "minute",
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    primary_query = body["payloads"][0]["primary"]["query"]
    assert "sum(rate(" in primary_query
    assert "[1m]" in primary_query


def test_metric_data_api_requires_metric() -> None:
    app = _test_app()
    response = app.test_client().get("/api/metric-data")

    assert response.status_code == 400
    assert response.get_json()["error"] == "metric is required"


class UnreachablePrometheusClient:
    """Fake client where every Prometheus call fails with unreachable."""

    base_url = "http://example"

    def _raise(self) -> None:
        raise PrometheusUnreachableError(
            "Cannot reach Prometheus at http://example: connection refused",
            base_url=self.base_url,
        )

    def list_metric_catalog(self) -> list[dict[str, str]]:
        self._raise()
        return []  # unreachable, but typing

    def list_alerts(self) -> list[dict[str, str]]:
        self._raise()
        return []

    def metric_label_options(self, metric_name: str) -> dict[str, list[str]]:
        self._raise()
        return {}

    def query_range(
        self,
        query: str,
        window: str,
        step: str,
        end_offset: str = "0s",
        series_name: str | None = None,
    ) -> dict[str, Any]:
        self._raise()
        return {}


def _unreachable_app():
    return create_app(
        settings=Settings(
            prometheus_url="http://example",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        prometheus_client=UnreachablePrometheusClient(),
        run_migrations=False,
    )


def test_index_renders_unreachable_page_on_connection_failure() -> None:
    app = _unreachable_app()
    response = app.test_client().get("/")

    assert response.status_code == 503
    body = response.data.decode("utf-8")
    assert "Cannot reach Prometheus" in body
    assert "http://example" in body
    assert "connection refused" in body
    # Top nav still rendered (template extends layout.html)
    assert "StatView" in body


def test_metrics_page_renders_unreachable_page_on_connection_failure() -> None:
    app = _unreachable_app()
    response = app.test_client().get("/metrics")
    assert response.status_code == 503
    assert b"Cannot reach Prometheus" in response.data


def test_api_view_data_returns_json_503_on_connection_failure() -> None:
    app = _unreachable_app()
    response = app.test_client().get("/api/view-data?metrics=up")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error"] == "prometheus_unreachable"
    assert payload["base_url"] == "http://example"
    assert "connection refused" in payload["message"]


def test_settings_reads_username_and_password_env_vars() -> None:
    with patch.dict(
        os.environ,
        {"PROMETHEUS_USERNAME": "alice", "PROMETHEUS_PASSWORD": "s3cret"},
        clear=False,
    ):
        s = Settings()
    assert s.prometheus_username == "alice"
    assert s.prometheus_password == "s3cret"


def test_settings_username_none_when_unset() -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PROMETHEUS_USERNAME", "PROMETHEUS_PASSWORD")
    }
    with patch.dict(os.environ, env, clear=True):
        s = Settings()
    assert s.prometheus_username is None
    assert s.prometheus_password == ""


def test_create_app_passes_auth_settings_to_client(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class _CapturingClient:
        def __init__(self, base_url: str, **kwargs: Any) -> None:
            captured["base_url"] = base_url
            captured["kwargs"] = kwargs

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.main.PrometheusClient", _CapturingClient)

    create_app(
        settings=Settings(
            prometheus_url="http://example",
            prometheus_username="alice",
            prometheus_password="s3cret",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        run_migrations=False,
    )
    assert captured["base_url"] == "http://example"
    assert captured["kwargs"] == {"username": "alice", "password": "s3cret"}


def test_create_app_loads_dotenv_when_dev_mode_and_no_settings(monkeypatch) -> None:
    called: dict[str, Any] = {}

    def fake_load_dotenv(*args: Any, **kwargs: Any) -> bool:
        called["args"] = args
        called["kwargs"] = kwargs
        return True

    monkeypatch.setattr("dotenv.load_dotenv", fake_load_dotenv)
    monkeypatch.setenv("STATVIEW_MODE", "dev")

    create_app(run_migrations=False)

    assert called["kwargs"].get("override") is True


def test_create_app_does_not_load_dotenv_outside_dev_mode(monkeypatch) -> None:
    called = False

    def fake_load_dotenv(*args: Any, **kwargs: Any) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr("dotenv.load_dotenv", fake_load_dotenv)
    monkeypatch.delenv("STATVIEW_MODE", raising=False)

    create_app(
        settings=Settings(
            prometheus_url="http://example",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        prometheus_client=FakePrometheusClient(),
        run_migrations=False,
    )

    assert called is False


def test_create_app_does_not_load_dotenv_when_settings_passed_explicitly(monkeypatch) -> None:
    """Tests pass explicit Settings; load_dotenv should NOT run, even in dev mode."""
    called = False

    def fake_load_dotenv(*args: Any, **kwargs: Any) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr("dotenv.load_dotenv", fake_load_dotenv)
    monkeypatch.setenv("STATVIEW_MODE", "dev")

    create_app(
        settings=Settings(
            prometheus_url="http://example",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        prometheus_client=FakePrometheusClient(),
        run_migrations=False,
    )

    assert called is False


def test_view_data_honours_type_and_agg_overrides() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/api/view-data",
        query_string={
            "metrics": "up",
            "window_amount": "1",
            "window_unit": "hour",
            "step_amount": "1",
            "step_unit": "minute",
            "type_override": "timing",
            "agg_override": "max",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["type_override"] == "timing"
    assert payload["agg_override"] == "max"
    primary = payload["payloads"][0]
    assert primary["metric_type"] == "timing"
    aggregate_query = primary["primary"]["query"]
    # The override should force max(...) regardless of detected type.
    assert aggregate_query.startswith("max(")


def test_view_data_rejects_unknown_overrides_silently() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/api/view-data",
        query_string={
            "metrics": "up",
            "window_amount": "1",
            "window_unit": "hour",
            "step_amount": "1",
            "step_unit": "minute",
            "type_override": "weird",
            "agg_override": "stddev",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["type_override"] == ""
    assert payload["agg_override"] == ""


def test_error_page_does_not_leak_url_embedded_credentials() -> None:
    # Build a real PrometheusClient (not the fake) so the URL is parsed by
    # _split_credentials. Mock the transport so the connection fails.
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://host:9090",
    )
    client = PrometheusClient(
        "http://leakuser:leakpass@host:9090",
        http_client=http_client,
    )

    app = create_app(
        settings=Settings(
            prometheus_url="http://leakuser:leakpass@host:9090",
            live_refresh_seconds=5,
            saved_db_path=str(TEST_DB_PATH),
        ),
        prometheus_client=client,
        run_migrations=False,
    )
    response = app.test_client().get("/")
    body = response.data.decode("utf-8")

    assert response.status_code == 503
    assert "leakuser" not in body
    assert "leakpass" not in body
    # The sanitized URL must still appear.
    assert "http://host:9090" in body
