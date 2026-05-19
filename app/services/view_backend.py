from __future__ import annotations

import json
import math
from typing import Any
from urllib.parse import urlencode

from app.config import (
    STANDARD_PRESETS,
)
from app.prometheus import PrometheusClient, parse_prometheus_duration

_WINDOW_UNIT_TO_DURATION = {
    "hour": lambda n: f"{n}h",
    "day": lambda n: f"{n}d",
    "week": lambda n: f"{n}w",
    "month": lambda n: f"{n * 30}d",
    "year": lambda n: f"{n}y",
}

_STEP_UNIT_TO_DURATION = {
    "minute": lambda n: f"{n}m",
    "hour": lambda n: f"{n}h",
    "day": lambda n: f"{n}d",
    "week": lambda n: f"{n}w",
    "year": lambda n: f"{n}y",
}


def _sanitize_positive_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _sanitize_optional_positive_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _sanitize_choice(value: str | None, allowed: list[str], default: str) -> str:
    if value in allowed:
        return value
    return default


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _parse_label_filters(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    cleaned: dict[str, str] = {}
    for label_name, label_value in parsed.items():
        if not isinstance(label_name, str) or not label_name:
            continue
        if not isinstance(label_value, str) or not label_value:
            continue
        cleaned[label_name] = label_value
    return cleaned


def _parse_metric_label_filters_object(raw: object) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}

    nested: dict[str, dict[str, str]] = {}
    shared: dict[str, str] = {}
    for metric_or_label, value in raw.items():
        if not isinstance(metric_or_label, str) or not metric_or_label:
            continue
        if isinstance(value, str):
            if value:
                shared[metric_or_label] = value
            continue
        if not isinstance(value, dict):
            continue
        cleaned = {
            str(label_name): str(label_value)
            for label_name, label_value in value.items()
            if isinstance(label_name, str)
            and label_name
            and isinstance(label_value, str)
            and label_value
        }
        if cleaned:
            nested[metric_or_label] = cleaned

    if shared:
        if nested:
            nested["*"] = shared
            return nested
        return {"*": shared}
    return nested


def _parse_metric_label_filters(raw: str | None) -> dict[str, dict[str, str]]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return _parse_metric_label_filters_object(parsed)


def _resolve_metric_label_filters(
    selected_metrics: list[str],
    parsed_metric_filters: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    shared = parsed_metric_filters.get("*", {})
    resolved: dict[str, dict[str, str]] = {}
    for metric in selected_metrics:
        metric_filters = dict(shared)
        metric_filters.update(parsed_metric_filters.get(metric, {}))
        resolved[metric] = metric_filters
    return resolved


def _sanitize_metric_label_filters(
    client: PrometheusClient,
    selected_metrics: list[str],
    metric_label_filters: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    sanitized: dict[str, dict[str, str]] = {}
    for metric in selected_metrics:
        available_filters = client.metric_label_options(metric)
        sanitized[metric] = _sanitize_label_filters(
            metric_label_filters.get(metric, {}),
            available_filters,
        )
    return sanitized


def _pluralize(unit: str, amount: int) -> str:
    if amount == 1:
        return unit
    return f"{unit}s"


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None

    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]

    span = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * span


def _series_stats(chart_payload: dict[str, Any]) -> dict[str, float | None]:
    aggregate = chart_payload.get("aggregate", {})
    points = aggregate.get("points", [])
    values = [point.get("v") for point in points if isinstance(point.get("v"), float | int)]
    float_values = [float(v) for v in values]

    if not float_values:
        return {
            "latest": None,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "total": None,
            "p95": None,
            "p99": None,
        }

    return {
        "latest": float_values[-1],
        "min": min(float_values),
        "max": max(float_values),
        "mean": sum(float_values) / len(float_values),
        "median": _percentile(float_values, 0.5),
        "total": sum(float_values),
        "p95": _percentile(float_values, 0.95),
        "p99": _percentile(float_values, 0.99),
    }


def _sanitize_label_filters(
    selected_filters: dict[str, str],
    available_filters: dict[str, list[str]],
) -> dict[str, str]:
    valid: dict[str, str] = {}
    for label_name, label_value in selected_filters.items():
        if label_name not in available_filters:
            continue
        if label_value not in available_filters[label_name]:
            continue
        valid[label_name] = label_value
    return valid


def _escape_promql_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _metric_selector(metric_name: str, label_filters: dict[str, str]) -> str:
    if not label_filters:
        return metric_name

    matcher_parts = []
    for label_name in sorted(label_filters):
        label_value = _escape_promql_label_value(label_filters[label_name])
        matcher_parts.append(f'{label_name}="{label_value}"')
    return f"{metric_name}{{{','.join(matcher_parts)}}}"


def _counter_rate_window(step_duration: str) -> str:
    try:
        parse_prometheus_duration(step_duration)
        return step_duration
    except ValueError:
        return "1m"


def _metric_type_for_name(metric_name: str, metric_types: dict[str, str]) -> str:
    metric_type = metric_types.get(metric_name, "unknown")
    if metric_type != "unknown":
        return metric_type
    if metric_name.endswith("_total"):
        return "counter"
    if metric_name.endswith("_bucket"):
        return "histogram"
    if metric_name.endswith("_quantile"):
        return "summary"
    if metric_name.endswith("_sum") or metric_name.endswith("_count"):
        return "counter"
    return "untyped"


def _build_aggregate_query(
    metric: str,
    metric_type: str,
    selected_filters: dict[str, str],
    step_duration: str,
) -> str:
    metric_query = _metric_selector(metric, selected_filters)
    rate_window = _counter_rate_window(step_duration)

    if metric_type == "counter":
        return f"sum(rate({metric_query}[{rate_window}]))"

    if metric_type == "histogram":
        if metric.endswith("_bucket"):
            filters_without_le = {
                label_name: label_value
                for label_name, label_value in selected_filters.items()
                if label_name != "le"
            }
            bucket_query = _metric_selector(metric, filters_without_le)
            return f"histogram_quantile(0.95, sum by (le) (rate({bucket_query}[{rate_window}])))"
        if metric.endswith("_sum"):
            count_metric = f"{metric[:-4]}_count"
            count_query = _metric_selector(count_metric, selected_filters)
            return (
                f"sum(rate({metric_query}[{rate_window}])) / "
                f"clamp_min(sum(rate({count_query}[{rate_window}])), 1e-9)"
            )
        if metric.endswith("_count"):
            return f"sum(rate({metric_query}[{rate_window}]))"
        return f"sum({metric_query})"

    if metric_type == "summary":
        if metric.endswith("_sum"):
            count_metric = f"{metric[:-4]}_count"
            count_query = _metric_selector(count_metric, selected_filters)
            return (
                f"sum(rate({metric_query}[{rate_window}])) / "
                f"clamp_min(sum(rate({count_query}[{rate_window}])), 1e-9)"
            )
        if metric.endswith("_count"):
            return f"sum(rate({metric_query}[{rate_window}]))"
        return f"avg({metric_query})"

    return f"sum({metric_query})"


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100.0


def _preset_comparison_rows(
    current: dict[str, float | None],
    previous: dict[str, float | None],
) -> list[dict[str, float | None | str]]:
    fields = ["min", "max", "mean", "total"]
    rows: list[dict[str, float | None | str]] = []
    for field in fields:
        current_value = current.get(field)
        previous_value = previous.get(field)
        rows.append(
            {
                "label": field,
                "current": current_value,
                "previous": previous_value,
                "delta_pct": _pct_change(current_value, previous_value),
            }
        )
    return rows


def _metric_types(catalog: list[dict[str, str]]) -> dict[str, str]:
    return {
        item.get("name", ""): item.get("type", "unknown") for item in catalog if item.get("name")
    }


def _selected_metrics(raw: str | None, valid_names: set[str], limit: int = 3) -> list[str]:
    if not raw:
        return []

    selected: list[str] = []
    for chunk in raw.split(","):
        name = chunk.strip()
        if not name or name not in valid_names or name in selected:
            continue
        selected.append(name)
        if len(selected) >= limit:
            break
    return selected


def _build_view_query_string(
    metrics: list[str],
    window_amount: int,
    window_unit: str,
    step_amount: int,
    step_unit: str,
    compare_enabled: bool,
    metric_label_filters: dict[str, dict[str, str]],
) -> str:
    params: list[tuple[str, str]] = [
        ("metrics", ",".join(metrics)),
        ("window_amount", str(window_amount)),
        ("window_unit", window_unit),
        ("step_amount", str(step_amount)),
        ("step_unit", step_unit),
        ("compare_enabled", "1" if compare_enabled else "0"),
    ]
    non_empty_filters = {
        metric_name: filters for metric_name, filters in metric_label_filters.items() if filters
    }
    if non_empty_filters:
        params.append(
            (
                "label_filters",
                json.dumps(non_empty_filters, sort_keys=True, separators=(",", ":")),
            )
        )
    return urlencode(params)


def _saved_view_title(
    metrics: list[str],
    window_amount: int,
    window_unit: str,
    step_amount: int,
    step_unit: str,
    compare_enabled: bool,
) -> str:
    metrics_label = " • ".join(metrics)
    window_label = f"{window_amount} {_pluralize(window_unit, window_amount)}"
    step_label = f"{step_amount} {_pluralize(step_unit, step_amount)}"
    compare_label = "compare previous" if compare_enabled else "no compare"
    return f"{metrics_label} • {window_label} @ {step_label} • {compare_label}"


def _build_view_payload(
    client: PrometheusClient,
    metrics: list[str],
    metric_types: dict[str, str],
    window_amount: int,
    window_unit: str,
    step_amount: int,
    step_unit: str,
    metric_label_filters: dict[str, dict[str, str]],
    compare_enabled: bool,
) -> dict[str, Any]:
    metric_payloads = [
        _build_payload(
            client,
            metric=metric_name,
            metric_type=_metric_type_for_name(metric_name, metric_types),
            window_amount=window_amount,
            window_unit=window_unit,
            step_amount=step_amount,
            step_unit=step_unit,
            label_filters=metric_label_filters.get(metric_name, {}),
            compare_enabled=compare_enabled,
        )
        for metric_name in metrics
    ]

    primary_payload = metric_payloads[0]
    metric_summaries = [
        {
            "metric": payload["metric"],
            "rows": payload.get("summary_rows", []),
        }
        for payload in metric_payloads
    ]
    return {
        "metrics": metrics,
        "window": primary_payload["window"],
        "step": primary_payload["step"],
        "filters": primary_payload["filters"],
        "metric_filters": {
            payload["metric"]: payload.get("filters", {}) for payload in metric_payloads
        },
        "compare": primary_payload["compare"],
        "payloads": metric_payloads,
        "presets": primary_payload["presets"],
        "metric_summaries": metric_summaries,
        "summary_rows": primary_payload["summary_rows"],
        "summary_metric": primary_payload["metric"],
    }


def _build_payload(
    client: PrometheusClient,
    metric: str,
    metric_type: str,
    window_amount: int,
    window_unit: str,
    step_amount: int,
    step_unit: str,
    label_filters: dict[str, str],
    compare_enabled: bool,
) -> dict[str, Any]:
    window_duration = _WINDOW_UNIT_TO_DURATION[window_unit](window_amount)
    step_duration = _STEP_UNIT_TO_DURATION[step_unit](step_amount)
    available_filters = client.metric_label_options(metric)
    selected_filters = _sanitize_label_filters(label_filters, available_filters)
    aggregate_query = _build_aggregate_query(
        metric=metric,
        metric_type=metric_type,
        selected_filters=selected_filters,
        step_duration=step_duration,
    )

    primary = client.query_range(
        aggregate_query,
        window=window_duration,
        step=step_duration,
        series_name=metric,
    )

    compare_payload = None
    compare_offset = "none"
    compare_offset_seconds = 0
    compare_label = "None"
    if compare_enabled:
        compare_offset = window_duration
        compare_offset_seconds = parse_prometheus_duration(window_duration)
        compare_label = f"previous {window_amount} {_pluralize(window_unit, window_amount)}"
        compare_payload = client.query_range(
            aggregate_query,
            window=window_duration,
            step=step_duration,
            end_offset=window_duration,
            series_name=metric,
        )

    presets = []
    for preset in STANDARD_PRESETS:
        chart_payload = client.query_range(
            aggregate_query,
            window=preset["window"],
            step=preset["step"],
            series_name=metric,
        )
        preset_previous_offset = preset["window"]
        previous_payload = client.query_range(
            aggregate_query,
            window=preset["window"],
            step=preset["step"],
            end_offset=preset_previous_offset,
            series_name=metric,
        )

        if preset["id"] == "1y_1w":
            for bucket in (chart_payload, previous_payload):
                aggregate = bucket.get("aggregate", {})
                points = aggregate.get("points", [])
                if isinstance(points, list) and len(points) > 52:
                    aggregate["points"] = points[-52:]

        current_stats = _series_stats(chart_payload)
        previous_stats = _series_stats(previous_payload)
        presets.append(
            {
                "id": preset["id"],
                "label": preset["label"],
                "window": preset["window"],
                "step": preset["step"],
                "window_amount": int(preset["window_amount"]),
                "window_unit": str(preset["window_unit"]),
                "step_amount": int(preset["step_amount"]),
                "step_unit": str(preset["step_unit"]),
                "previous_offset": preset_previous_offset,
                "previous_offset_seconds": parse_prometheus_duration(preset_previous_offset),
                "previous_label": f"previous {preset['label']}",
                "chart": chart_payload,
                "previous_chart": previous_payload,
                "stats_rows": _preset_comparison_rows(current_stats, previous_stats),
            }
        )

    summary_current = client.query_range(
        aggregate_query,
        window="1w",
        step="1h",
        series_name=metric,
    )
    summary_previous = client.query_range(
        aggregate_query,
        window="1w",
        step="1h",
        end_offset="1w",
        series_name=metric,
    )
    summary_rows = [
        {"label": "1 week @ 1 hour", "stats": _series_stats(summary_current)},
        {"label": "1 week ago", "stats": _series_stats(summary_previous)},
    ]

    return {
        "metric": metric,
        "metric_type": metric_type,
        "window": {
            "amount": window_amount,
            "unit": window_unit,
            "duration": window_duration,
            "label": f"{window_amount} {_pluralize(window_unit, window_amount)}",
        },
        "step": {
            "amount": step_amount,
            "unit": step_unit,
            "duration": step_duration,
            "label": f"{step_amount} {_pluralize(step_unit, step_amount)}",
        },
        "filters": {
            "available": available_filters,
            "selected": selected_filters,
        },
        "compare": {
            "enabled": compare_enabled,
            "offset": compare_offset,
            "offset_seconds": compare_offset_seconds,
            "label": compare_label,
            "chart": compare_payload,
        },
        "primary": primary,
        "presets": presets,
        "summary_rows": summary_rows,
    }
