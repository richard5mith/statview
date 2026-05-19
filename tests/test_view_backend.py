from __future__ import annotations

from app.services.view_backend import _build_aggregate_query


def test_aggregate_query_counter_uses_sum_rate() -> None:
    query = _build_aggregate_query(
        metric="http_requests_total",
        metric_type="counter",
        selected_filters={},
        step_duration="1m",
    )
    assert query == "sum(rate(http_requests_total[1m]))"


def test_aggregate_query_gauge_uses_avg() -> None:
    query = _build_aggregate_query(
        metric="web_response_time",
        metric_type="gauge",
        selected_filters={},
        step_duration="1m",
    )
    assert query == "avg(web_response_time)"


def test_aggregate_query_untyped_uses_avg() -> None:
    query = _build_aggregate_query(
        metric="mystery_metric",
        metric_type="untyped",
        selected_filters={},
        step_duration="1m",
    )
    assert query == "avg(mystery_metric)"


def test_aggregate_query_unknown_falls_through_to_avg() -> None:
    query = _build_aggregate_query(
        metric="weird",
        metric_type="something-new",
        selected_filters={},
        step_duration="1m",
    )
    assert query == "avg(weird)"


def test_aggregate_query_gauge_with_label_filters() -> None:
    query = _build_aggregate_query(
        metric="web_response_time",
        metric_type="gauge",
        selected_filters={"host": "alpha"},
        step_duration="1m",
    )
    assert query == 'avg(web_response_time{host="alpha"})'


def test_aggregate_query_histogram_bucket_unchanged() -> None:
    query = _build_aggregate_query(
        metric="latency_bucket",
        metric_type="histogram",
        selected_filters={},
        step_duration="1m",
    )
    assert query == "histogram_quantile(0.95, sum by (le) (rate(latency_bucket[1m])))"


def test_aggregate_query_summary_count_unchanged() -> None:
    query = _build_aggregate_query(
        metric="latency_count",
        metric_type="summary",
        selected_filters={},
        step_duration="1m",
    )
    assert query == "sum(rate(latency_count[1m]))"
