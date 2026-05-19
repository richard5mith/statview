from __future__ import annotations

from app.services.view_backend import _build_aggregate_query, _metric_type_for_name


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


def test_aggregate_query_timing_uses_avg() -> None:
    query = _build_aggregate_query(
        metric="web_response_time",
        metric_type="timing",
        selected_filters={},
        step_duration="1m",
    )
    assert query == "avg(web_response_time)"


def test_aggregate_query_timing_with_label_filters() -> None:
    query = _build_aggregate_query(
        metric="web_response_time",
        metric_type="timing",
        selected_filters={"host": "alpha"},
        step_duration="1m",
    )
    assert query == 'avg(web_response_time{host="alpha"})'


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


def test_metric_type_for_name_uses_override_when_provided() -> None:
    types = {"web_response_time": "untyped"}
    resolved = _metric_type_for_name("web_response_time", types, type_override="timing")
    assert resolved == "timing"


def test_metric_type_for_name_override_wins_over_metadata() -> None:
    types = {"web_response_time": "counter"}
    resolved = _metric_type_for_name("web_response_time", types, type_override="gauge")
    assert resolved == "gauge"


def test_metric_type_for_name_none_override_falls_through() -> None:
    types = {"http_requests_total": "counter"}
    resolved = _metric_type_for_name("http_requests_total", types, type_override=None)
    assert resolved == "counter"


def test_metric_type_for_name_empty_override_falls_through() -> None:
    types = {"http_requests_total": "counter"}
    resolved = _metric_type_for_name("http_requests_total", types, type_override="")
    assert resolved == "counter"


def test_aggregate_query_override_max_on_gauge() -> None:
    query = _build_aggregate_query(
        metric="web_response_time",
        metric_type="gauge",
        selected_filters={},
        step_duration="1m",
        agg_override="max",
    )
    assert query == "max(web_response_time)"


def test_aggregate_query_override_max_on_counter_wraps_rate() -> None:
    query = _build_aggregate_query(
        metric="http_requests_total",
        metric_type="counter",
        selected_filters={},
        step_duration="1m",
        agg_override="max",
    )
    assert query == "max(rate(http_requests_total[1m]))"


def test_aggregate_query_override_sum_on_counter_matches_auto() -> None:
    query = _build_aggregate_query(
        metric="http_requests_total",
        metric_type="counter",
        selected_filters={},
        step_duration="1m",
        agg_override="sum",
    )
    assert query == "sum(rate(http_requests_total[1m]))"


def test_aggregate_query_override_min_on_timing() -> None:
    query = _build_aggregate_query(
        metric="web_response_time",
        metric_type="timing",
        selected_filters={},
        step_duration="1m",
        agg_override="min",
    )
    assert query == "min(web_response_time)"


def test_aggregate_query_override_with_label_filters() -> None:
    query = _build_aggregate_query(
        metric="web_response_time",
        metric_type="gauge",
        selected_filters={"host": "alpha"},
        step_duration="1m",
        agg_override="max",
    )
    assert query == 'max(web_response_time{host="alpha"})'


def test_aggregate_query_none_override_falls_through_to_type_default() -> None:
    query = _build_aggregate_query(
        metric="web_response_time",
        metric_type="gauge",
        selected_filters={},
        step_duration="1m",
        agg_override=None,
    )
    assert query == "avg(web_response_time)"


def test_aggregate_query_empty_override_falls_through() -> None:
    query = _build_aggregate_query(
        metric="http_requests_total",
        metric_type="counter",
        selected_filters={},
        step_duration="1m",
        agg_override="",
    )
    assert query == "sum(rate(http_requests_total[1m]))"
