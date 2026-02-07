from __future__ import annotations

import httpx
import pytest

from app.prometheus import (
    PrometheusClient,
    PrometheusError,
    aggregate_series_points,
    normalize_metric_type,
    parse_prometheus_duration,
    series_label,
)


def test_parse_prometheus_duration_valid() -> None:
    assert parse_prometheus_duration("5m") == 300
    assert parse_prometheus_duration("1h30m") == 5400
    assert parse_prometheus_duration("7d") == 604800


def test_parse_prometheus_duration_invalid() -> None:
    with pytest.raises(ValueError):
        parse_prometheus_duration("")

    with pytest.raises(ValueError):
        parse_prometheus_duration("2x")


def test_series_label_ignores_name_label() -> None:
    label = series_label("up", {"__name__": "up", "job": "api", "instance": "a"})
    assert label == "up (instance=a, job=api)"


def test_normalize_metric_type() -> None:
    assert normalize_metric_type("counter") == "counter"
    assert normalize_metric_type("GAUGE") == "gauge"
    assert normalize_metric_type("other") == "unknown"
    assert normalize_metric_type(None) == "unknown"


def test_query_range_transforms_points() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query_range"
        assert request.url.params["query"] == "up"
        assert request.url.params["step"] == "30s"
        body = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "up", "job": "demo"},
                        "values": [[1700000000, "1"], [1700000060, "0"]],
                    }
                ]
            },
        }
        return httpx.Response(200, json=body)

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    payload = client.query_range("up", window="5m", step="30s")

    assert payload["metric"] == "up"
    assert payload["window"] == "5m"
    assert len(payload["series"]) == 1
    assert payload["series"][0]["label"] == "up (job=demo)"
    assert payload["series"][0]["points"] == [
        {"t": 1700000000.0, "v": 1.0},
        {"t": 1700000060.0, "v": 0.0},
    ]
    assert payload["aggregate"]["points"] == [
        {"t": 1700000000.0, "v": 1.0},
        {"t": 1700000060.0, "v": 0.0},
    ]


def test_list_metric_catalog_includes_types() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/label/__name__/values":
            return httpx.Response(
                200,
                json={"status": "success", "data": ["up", "node_cpu_seconds_total"]},
            )
        if request.url.path == "/api/v1/metadata":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "up": [{"type": "gauge"}],
                        "node_cpu_seconds_total": [{"type": "counter"}],
                    },
                },
            )
        return httpx.Response(404, json={"status": "error", "error": "not found"})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    catalog = client.list_metric_catalog()
    assert catalog == [
        {"name": "node_cpu_seconds_total", "type": "counter"},
        {"name": "up", "type": "gauge"},
    ]


def test_metric_label_options() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/series"
        assert request.url.params.get("match[]") == "up"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {"__name__": "up", "job": "api", "instance": "a"},
                    {"__name__": "up", "job": "api", "instance": "b"},
                    {"__name__": "up", "job": "worker", "instance": "b"},
                ],
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    options = client.metric_label_options("up")
    assert options == {
        "instance": ["a", "b"],
        "job": ["api", "worker"],
    }


def test_aggregate_series_points_merges_timestamps() -> None:
    merged = aggregate_series_points(
        [
            {"points": [{"t": 1.0, "v": 2.0}, {"t": 2.0, "v": 5.0}]},
            {"points": [{"t": 1.0, "v": 3.0}]},
        ]
    )
    assert merged == [{"t": 1.0, "v": 5.0}, {"t": 2.0, "v": 5.0}]


def test_prometheus_error_raises() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "error": "bad query"})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    with pytest.raises(PrometheusError):
        client.list_metric_names()
