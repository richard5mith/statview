from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.main import create_app


class FakePrometheusClient:
    def list_metric_catalog(self) -> list[dict[str, str]]:
        return [
            {"name": "node_cpu_seconds_total", "type": "counter"},
            {"name": "up", "type": "gauge"},
        ]

    def metric_label_options(self, metric_name: str) -> dict[str, list[str]]:
        if metric_name != "up":
            return {}
        return {
            "instance": ["a", "b"],
            "job": ["api", "worker"],
        }

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
        settings=Settings(prometheus_url="http://example", live_refresh_seconds=5),
        prometheus_client=FakePrometheusClient(),
    )


def test_index_renders_metric_list() -> None:
    app = _test_app()
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"node_cpu_seconds_total" in response.data
    assert b"counter" in response.data
    assert b"StatView" in response.data


def test_metric_panel_renders_with_payload() -> None:
    app = _test_app()
    response = app.test_client().get(
        "/metric-panel?metric=up&window_amount=1&window_unit=week&step_amount=1&step_unit=hour"
    )

    assert response.status_code == 200
    assert b"Main chart" in response.data
    assert b"Tag filters" in response.data
    assert b"data-role=\"payload\"" in response.data


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
            "compare_offset": "1w",
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
        body["presets"][0]["previous_chart"]["end_offset"]
        == body["presets"][0]["previous_offset"]
    )
    assert len(body["summary_rows"]) == 2


def test_metric_data_api_requires_metric() -> None:
    app = _test_app()
    response = app.test_client().get("/api/metric-data")

    assert response.status_code == 400
    assert response.get_json()["error"] == "metric is required"
