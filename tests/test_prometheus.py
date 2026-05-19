from __future__ import annotations

import base64

import httpx
import pytest

from app.prometheus import (
    PrometheusClient,
    PrometheusError,
    PrometheusUnreachableError,
    _resolve_auth,
    _split_credentials,
    aggregate_series_points,
    fallback_metric_type,
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


def test_fallback_metric_type() -> None:
    assert fallback_metric_type("http_requests_total") == "counter"
    assert fallback_metric_type("latency_bucket") == "histogram"
    assert fallback_metric_type("latency_quantile") == "summary"
    assert fallback_metric_type("latency_sum") == "counter"
    assert fallback_metric_type("up") == "untyped"


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


def test_list_metric_catalog_uses_fallback_when_metadata_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/label/__name__/values":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": ["http_requests_total", "orphan_metric"],
                },
            )
        if request.url.path == "/api/v1/metadata":
            return httpx.Response(200, json={"status": "success", "data": {}})
        return httpx.Response(404, json={"status": "error", "error": "not found"})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    catalog = client.list_metric_catalog()
    assert catalog == [
        {"name": "http_requests_total", "type": "counter"},
        {"name": "orphan_metric", "type": "untyped"},
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


def test_list_alerts_parses_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/alerts"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "alerts": [
                        {
                            "labels": {"alertname": "HighErrorRate"},
                            "annotations": {"summary": "Too many errors"},
                            "state": "firing",
                            "activeAt": "2026-02-08T12:00:00Z",
                            "value": "1",
                        }
                    ]
                },
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    alerts = client.list_alerts()
    assert alerts == [
        {
            "name": "HighErrorRate",
            "state": "firing",
            "active_at": "2026-02-08T12:00:00Z",
            "value": "1",
            "summary": "Too many errors",
        }
    ]


def test_list_alerts_handles_invalid_shapes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/alerts"
        return httpx.Response(200, json={"status": "success", "data": {"alerts": "bad"}})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)
    assert client.list_alerts() == []


def test_query_range_skips_invalid_points() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"__name__": "up"},
                            "values": [
                                ["bad-ts", "1"],
                                [1700000060, "nan"],
                                [1700000120, "2"],
                            ],
                        }
                    ]
                },
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)
    payload = client.query_range("up", window="5m", step="30s")
    assert payload["aggregate"]["points"] == [{"t": 1700000120.0, "v": 2.0}]


def test_prometheus_unreachable_error_holds_base_url_and_cause() -> None:
    cause = RuntimeError("boom")
    exc = PrometheusUnreachableError(
        "Cannot reach Prometheus at http://test",
        base_url="http://test",
        cause=cause,
    )
    assert str(exc) == "Cannot reach Prometheus at http://test"
    assert exc.base_url == "http://test"
    assert exc.cause is cause


def test_prometheus_unreachable_error_is_not_prometheus_error() -> None:
    # Sibling exception, NOT a subclass — existing `except PrometheusError`
    # handlers must not catch it.
    exc = PrometheusUnreachableError("x", base_url="http://test")
    assert not isinstance(exc, PrometheusError)


def test_fetch_wraps_connect_error_as_unreachable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    with pytest.raises(PrometheusUnreachableError) as exc_info:
        client.list_metric_names()

    assert exc_info.value.base_url == "http://test"
    assert "http://test" in str(exc_info.value)
    assert isinstance(exc_info.value.cause, httpx.ConnectError)


def test_fetch_wraps_read_timeout_as_unreachable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    with pytest.raises(PrometheusUnreachableError):
        client.list_metric_names()


def test_fetch_wraps_http_status_error_as_prometheus_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="kaboom")

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    client = PrometheusClient("http://test", http_client=http_client)

    with pytest.raises(PrometheusError) as exc_info:
        client.list_metric_names()
    # Must NOT be the unreachable variant — server is reachable, just unhappy.
    assert not isinstance(exc_info.value, PrometheusUnreachableError)
    assert "500" in str(exc_info.value)


def test_split_credentials_returns_none_when_url_has_no_userinfo() -> None:
    url, auth = _split_credentials("http://host:9090/")
    assert url == "http://host:9090/"
    assert auth is None


def test_split_credentials_extracts_user_and_password() -> None:
    url, auth = _split_credentials("http://alice:s3cret@host:9090/")
    assert url == "http://host:9090/"
    assert auth == ("alice", "s3cret")


def test_split_credentials_handles_empty_password() -> None:
    url, auth = _split_credentials("http://alice@host:9090/")
    assert url == "http://host:9090/"
    assert auth == ("alice", "")


def test_split_credentials_url_decodes_userinfo() -> None:
    # URL-encoded special characters in userinfo
    url, auth = _split_credentials("http://alice:p%40ss@host/")
    assert url == "http://host/"
    assert auth == ("alice", "p@ss")


# --- Unit tests for the auth-resolution helper (precedence logic) ---


def test_resolve_auth_returns_none_when_no_inputs() -> None:
    assert _resolve_auth(username=None, password="", embedded=None) is None


def test_resolve_auth_uses_embedded_when_no_username_kwarg() -> None:
    assert _resolve_auth(username=None, password="", embedded=("u", "p")) == ("u", "p")


def test_resolve_auth_uses_kwargs_when_no_embedded() -> None:
    assert _resolve_auth(username="alice", password="s3cret", embedded=None) == ("alice", "s3cret")


def test_resolve_auth_kwargs_win_over_embedded() -> None:
    assert _resolve_auth(
        username="envuser",
        password="envpass",
        embedded=("urluser", "urlpass"),
    ) == ("envuser", "envpass")


def test_resolve_auth_kwarg_username_with_empty_password_is_valid() -> None:
    assert _resolve_auth(username="alice", password="", embedded=None) == ("alice", "")


# --- Integration tests for the client constructor ---


def test_client_sends_basic_auth_when_supplied_via_kwargs() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "success", "data": ["up"]})

    # http_client is passed with auth=(...) to verify httpx sends the header;
    # PrometheusClient kwargs ride alongside so the constructor path runs.
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
        auth=("alice", "s3cret"),
    )
    client = PrometheusClient(
        "http://test",
        username="alice",
        password="s3cret",
        http_client=http_client,
    )
    client.list_metric_names()

    expected = "Basic " + base64.b64encode(b"alice:s3cret").decode("ascii")
    assert captured["auth"] == expected


def test_client_sanitizes_url_with_embedded_credentials() -> None:
    client = PrometheusClient("http://alice:s3cret@host:9090/")
    assert client.base_url == "http://host:9090"
    client.close()


def test_client_sanitizes_url_when_env_var_auth_supplied() -> None:
    client = PrometheusClient(
        "http://urluser:urlpass@host:9090/",
        username="envuser",
        password="envpass",
    )
    assert client.base_url == "http://host:9090"
    client.close()
