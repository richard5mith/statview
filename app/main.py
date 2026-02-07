from __future__ import annotations

import atexit
import json
import math
from typing import Any

from flask import Flask, jsonify, render_template, request

from app.config import (
    COMPARE_OFFSETS,
    DEFAULT_COMPARE_ENABLED,
    DEFAULT_COMPARE_OFFSET,
    DEFAULT_STEP_AMOUNT,
    DEFAULT_STEP_UNIT,
    DEFAULT_WINDOW_AMOUNT,
    DEFAULT_WINDOW_UNIT,
    STANDARD_PRESETS,
    STEP_UNITS,
    WINDOW_UNITS,
    Settings,
)
from app.prometheus import PrometheusClient, PrometheusError, parse_prometheus_duration

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


def _normalize_compare_offset(value: str | None) -> str:
    fallback = DEFAULT_COMPARE_OFFSET
    if not value:
        return fallback
    try:
        parse_prometheus_duration(value)
    except ValueError:
        return fallback
    return value


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


def _build_payload(
    client: PrometheusClient,
    metric: str,
    window_amount: int,
    window_unit: str,
    step_amount: int,
    step_unit: str,
    label_filters: dict[str, str],
    compare_enabled: bool,
    compare_offset: str,
) -> dict[str, Any]:
    window_duration = _WINDOW_UNIT_TO_DURATION[window_unit](window_amount)
    step_duration = _STEP_UNIT_TO_DURATION[step_unit](step_amount)
    available_filters = client.metric_label_options(metric)
    selected_filters = _sanitize_label_filters(label_filters, available_filters)
    metric_query = _metric_selector(metric, selected_filters)
    aggregate_query = f"sum({metric_query})"

    primary = client.query_range(
        aggregate_query,
        window=window_duration,
        step=step_duration,
        series_name=metric,
    )

    compare_payload = None
    compare_offset_seconds = parse_prometheus_duration(compare_offset)
    compare_label = next(
        (item["label"] for item in COMPARE_OFFSETS if item["value"] == compare_offset),
        f"{compare_offset} ago",
    )
    if compare_enabled:
        compare_payload = client.query_range(
            aggregate_query,
            window=window_duration,
            step=step_duration,
            end_offset=compare_offset,
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


def create_app(
    settings: Settings | None = None,
    prometheus_client: PrometheusClient | None = None,
) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False

    resolved_settings = settings or Settings()
    client = prometheus_client or PrometheusClient(resolved_settings.prometheus_url)

    app.config["settings"] = resolved_settings
    app.config["prometheus_client"] = client

    if prometheus_client is None:
        atexit.register(client.close)

    @app.get("/")
    def index() -> str:
        search = request.args.get("q", "").strip()
        selected_metric = request.args.get("metric", "").strip()
        window_amount = _sanitize_positive_int(
            request.args.get("window_amount"),
            DEFAULT_WINDOW_AMOUNT,
        )
        window_unit = _sanitize_choice(
            request.args.get("window_unit"),
            WINDOW_UNITS,
            DEFAULT_WINDOW_UNIT,
        )
        step_amount = _sanitize_positive_int(
            request.args.get("step_amount"),
            DEFAULT_STEP_AMOUNT,
        )
        step_unit = _sanitize_choice(
            request.args.get("step_unit"),
            STEP_UNITS,
            DEFAULT_STEP_UNIT,
        )
        compare_enabled = _parse_bool(
            request.args.get("compare_enabled"),
            DEFAULT_COMPARE_ENABLED,
        )
        compare_offset = _normalize_compare_offset(request.args.get("compare_offset"))
        label_filters = _parse_label_filters(request.args.get("label_filters"))

        metrics: list[dict[str, str]] = []
        metrics_error: str | None = None
        try:
            metrics = app.config["prometheus_client"].list_metric_catalog()
        except (PrometheusError, OSError) as exc:
            metrics_error = str(exc)

        if search:
            lowered = search.lower()
            metrics = [item for item in metrics if lowered in item["name"].lower()]

        if not selected_metric and metrics:
            selected_metric = metrics[0]["name"]

        initial_payload: dict[str, Any] | None = None
        initial_error: str | None = None
        if selected_metric:
            try:
                initial_payload = _build_payload(
                    app.config["prometheus_client"],
                    metric=selected_metric,
                    window_amount=window_amount,
                    window_unit=window_unit,
                    step_amount=step_amount,
                    step_unit=step_unit,
                    label_filters=label_filters,
                    compare_enabled=compare_enabled,
                    compare_offset=compare_offset,
                )
            except (PrometheusError, ValueError, OSError) as exc:
                initial_error = str(exc)

        return render_template(
            "index.html",
            metrics=metrics,
            metrics_error=metrics_error,
            selected_metric=selected_metric,
            initial_payload=initial_payload,
            initial_error=initial_error,
            search=search,
            default_window_amount=window_amount,
            default_window_unit=window_unit,
            default_step_amount=step_amount,
            default_step_unit=step_unit,
            default_compare_enabled=compare_enabled,
            default_compare_offset=compare_offset,
            window_units=WINDOW_UNITS,
            step_units=STEP_UNITS,
            compare_offsets=COMPARE_OFFSETS,
            live_refresh_seconds=app.config["settings"].live_refresh_seconds,
        )

    @app.get("/metric-panel")
    def metric_panel() -> str:
        metric = request.args.get("metric", "").strip()
        if not metric:
            return render_template(
                "metric_panel.html",
                metric="",
                error="Select a metric to inspect.",
            )

        window_amount = _sanitize_positive_int(
            request.args.get("window_amount"),
            DEFAULT_WINDOW_AMOUNT,
        )
        window_unit = _sanitize_choice(
            request.args.get("window_unit"),
            WINDOW_UNITS,
            DEFAULT_WINDOW_UNIT,
        )
        step_amount = _sanitize_positive_int(
            request.args.get("step_amount"),
            DEFAULT_STEP_AMOUNT,
        )
        step_unit = _sanitize_choice(
            request.args.get("step_unit"),
            STEP_UNITS,
            DEFAULT_STEP_UNIT,
        )
        compare_enabled = _parse_bool(
            request.args.get("compare_enabled"),
            DEFAULT_COMPARE_ENABLED,
        )
        compare_offset = _normalize_compare_offset(request.args.get("compare_offset"))
        label_filters = _parse_label_filters(request.args.get("label_filters"))

        try:
            payload = _build_payload(
                app.config["prometheus_client"],
                metric=metric,
                window_amount=window_amount,
                window_unit=window_unit,
                step_amount=step_amount,
                step_unit=step_unit,
                label_filters=label_filters,
                compare_enabled=compare_enabled,
                compare_offset=compare_offset,
            )
            error: str | None = None
        except (PrometheusError, ValueError, OSError) as exc:
            payload = None
            error = str(exc)

        return render_template(
            "metric_panel.html",
            metric=metric,
            payload=payload,
            error=error,
            window_units=WINDOW_UNITS,
            step_units=STEP_UNITS,
            compare_offsets=COMPARE_OFFSETS,
            live_refresh_seconds=app.config["settings"].live_refresh_seconds,
        )

    @app.get("/api/metric-data")
    def metric_data() -> tuple[dict[str, Any], int] | Any:
        metric = request.args.get("metric", "").strip()
        if not metric:
            return jsonify({"error": "metric is required"}), 400

        window_amount = _sanitize_positive_int(
            request.args.get("window_amount"),
            DEFAULT_WINDOW_AMOUNT,
        )
        window_unit = _sanitize_choice(
            request.args.get("window_unit"),
            WINDOW_UNITS,
            DEFAULT_WINDOW_UNIT,
        )
        step_amount = _sanitize_positive_int(
            request.args.get("step_amount"),
            DEFAULT_STEP_AMOUNT,
        )
        step_unit = _sanitize_choice(
            request.args.get("step_unit"),
            STEP_UNITS,
            DEFAULT_STEP_UNIT,
        )
        compare_enabled = _parse_bool(
            request.args.get("compare_enabled"),
            DEFAULT_COMPARE_ENABLED,
        )
        compare_offset = _normalize_compare_offset(request.args.get("compare_offset"))
        label_filters = _parse_label_filters(request.args.get("label_filters"))

        try:
            payload = _build_payload(
                app.config["prometheus_client"],
                metric=metric,
                window_amount=window_amount,
                window_unit=window_unit,
                step_amount=step_amount,
                step_unit=step_unit,
                label_filters=label_filters,
                compare_enabled=compare_enabled,
                compare_offset=compare_offset,
            )
        except (PrometheusError, ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify(payload)

    @app.get("/healthz")
    def healthz() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    return app


app = create_app()
