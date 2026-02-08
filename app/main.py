from __future__ import annotations

import atexit
import json
import math
import sqlite3
from typing import Any
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, render_template, request, url_for

from app.config import (
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
from app.saved_views import SavedViewStore

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
            return (
                "histogram_quantile(0.95, "
                f"sum by (le) (rate({bucket_query}[{rate_window}])))"
            )
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
        item.get("name", ""): item.get("type", "unknown")
        for item in catalog
        if item.get("name")
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
        metric_name: filters
        for metric_name, filters in metric_label_filters.items()
        if filters
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
            payload["metric"]: payload.get("filters", {})
            for payload in metric_payloads
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


def create_app(
    settings: Settings | None = None,
    prometheus_client: PrometheusClient | None = None,
) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False

    resolved_settings = settings or Settings()
    client = prometheus_client or PrometheusClient(resolved_settings.prometheus_url)
    saved_store = SavedViewStore(resolved_settings.saved_db_path)

    app.config["settings"] = resolved_settings
    app.config["prometheus_client"] = client
    app.config["saved_store"] = saved_store

    if prometheus_client is None:
        atexit.register(client.close)

    @app.get("/")
    @app.get("/view")
    def index() -> str:
        selected_metrics_raw = request.args.get("metrics", "").strip()
        add_metric = request.args.get("add_metric", "").strip()
        remove_metric = request.args.get("remove_metric", "").strip()
        selected_saved_id = _sanitize_optional_positive_int(request.args.get("saved_id"))
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
        compare_enabled = _parse_bool(request.args.get("compare_enabled"), False)
        raw_metric_label_filters = _parse_metric_label_filters(request.args.get("label_filters"))

        metrics: list[dict[str, str]] = []
        metrics_error: str | None = None
        try:
            metrics = app.config["prometheus_client"].list_metric_catalog()
        except (PrometheusError, OSError) as exc:
            metrics_error = str(exc)

        metric_type_map = _metric_types(metrics)
        metric_names = set(metric_type_map)
        selected_metrics = _selected_metrics(selected_metrics_raw, metric_names)

        if add_metric and add_metric in metric_names:
            if add_metric not in selected_metrics and len(selected_metrics) < 3:
                selected_metrics.append(add_metric)
        if remove_metric:
            selected_metrics = [name for name in selected_metrics if name != remove_metric]
        metric_label_filters = _resolve_metric_label_filters(
            selected_metrics,
            raw_metric_label_filters,
        )

        view_payload: dict[str, Any] | None = None
        view_error: str | None = None
        if selected_metrics:
            try:
                metric_label_filters = _sanitize_metric_label_filters(
                    app.config["prometheus_client"],
                    selected_metrics,
                    metric_label_filters,
                )
                view_payload = _build_view_payload(
                    app.config["prometheus_client"],
                    metrics=selected_metrics,
                    metric_types=metric_type_map,
                    window_amount=window_amount,
                    window_unit=window_unit,
                    step_amount=step_amount,
                    step_unit=step_unit,
                    metric_label_filters=metric_label_filters,
                    compare_enabled=compare_enabled,
                )
            except (PrometheusError, ValueError, OSError) as exc:
                view_error = str(exc)

        selected_metric_cards = [
            {
                "name": metric_name,
                "type": metric_type_map.get(metric_name, "unknown"),
            }
            for metric_name in selected_metrics
        ]
        remove_urls: dict[str, str] = {}
        for metric_name in selected_metrics:
            remaining = [name for name in selected_metrics if name != metric_name]
            remove_params: dict[str, str | int] = {
                "metrics": ",".join(remaining),
                "window_amount": window_amount,
                "window_unit": window_unit,
                "step_amount": step_amount,
                "step_unit": step_unit,
                "compare_enabled": "1" if compare_enabled else "0",
                "label_filters": (
                    json.dumps(metric_label_filters, sort_keys=True, separators=(",", ":"))
                    if metric_label_filters
                    else ""
                ),
            }
            if selected_saved_id is not None:
                remove_params["saved_id"] = selected_saved_id
            remove_urls[metric_name] = url_for("index", **remove_params)

        default_label_filters = (
            {
                metric_payload.get("metric", ""): metric_payload.get("filters", {}).get(
                    "selected",
                    {},
                )
                for metric_payload in view_payload.get("payloads", [])
                if metric_payload.get("metric")
            }
            if view_payload
            else metric_label_filters
        )

        return render_template(
            "index.html",
            metrics_error=metrics_error,
            metric_catalog=metrics,
            selected_metrics=selected_metric_cards,
            selected_metrics_csv=",".join(selected_metrics),
            selected_saved_id=selected_saved_id,
            remove_urls=remove_urls,
            view_payload=view_payload,
            view_error=view_error,
            active_nav="view",
            default_window_amount=window_amount,
            default_window_unit=window_unit,
            default_step_amount=step_amount,
            default_step_unit=step_unit,
            default_compare_enabled=compare_enabled,
            default_label_filters=json.dumps(default_label_filters),
            window_units=WINDOW_UNITS,
            step_units=STEP_UNITS,
            live_refresh_seconds=app.config["settings"].live_refresh_seconds,
        )

    @app.get("/metrics")
    def metrics_page() -> str:
        search = request.args.get("q", "").strip()
        metrics: list[dict[str, str]] = []
        metrics_error: str | None = None
        try:
            metrics = app.config["prometheus_client"].list_metric_catalog()
        except (PrometheusError, OSError) as exc:
            metrics_error = str(exc)

        if search:
            lowered = search.lower()
            metrics = [item for item in metrics if lowered in item["name"].lower()]

        return render_template(
            "metrics.html",
            metrics=metrics,
            metrics_error=metrics_error,
            search=search,
            active_nav="metrics",
        )

    @app.get("/saved")
    def saved_page() -> str:
        search = request.args.get("q", "").strip()
        saved_views = app.config["saved_store"].list(search)

        return render_template(
            "saved.html",
            saved_views=saved_views,
            search=search,
            active_nav="saved",
        )

    @app.get("/dashboards")
    def dashboards_page() -> str:
        dashboards = app.config["saved_store"].list_dashboards()
        return render_template(
            "dashboards.html",
            dashboards=dashboards,
            active_nav="dashboards",
        )

    @app.post("/dashboards")
    def create_dashboard() -> Any:
        dashboard_name = request.form.get("name", "").strip()
        if not dashboard_name:
            dashboards = app.config["saved_store"].list_dashboards()
            return (
                render_template(
                    "dashboards.html",
                    dashboards=dashboards,
                    create_error="Dashboard name is required.",
                    active_nav="dashboards",
                ),
                400,
            )

        try:
            dashboard = app.config["saved_store"].create_dashboard(dashboard_name)
        except ValueError:
            dashboards = app.config["saved_store"].list_dashboards()
            return (
                render_template(
                    "dashboards.html",
                    dashboards=dashboards,
                    create_error="Dashboard name is required.",
                    active_nav="dashboards",
                ),
                400,
            )
        except sqlite3.IntegrityError:
            dashboards = app.config["saved_store"].list_dashboards()
            return (
                render_template(
                    "dashboards.html",
                    dashboards=dashboards,
                    create_error="Dashboard name already exists.",
                    active_nav="dashboards",
                ),
                409,
            )

        return redirect(url_for("dashboard_detail_page", dashboard_id=dashboard["id"]), code=303)

    @app.post("/dashboards/add-item")
    def add_saved_to_dashboard() -> Any:
        dashboard_id = _sanitize_optional_positive_int(request.form.get("dashboard_id"))
        saved_id = _sanitize_optional_positive_int(request.form.get("saved_id"))
        next_url = request.form.get("next", "").strip() or url_for("saved_page")
        if dashboard_id is None or saved_id is None:
            return redirect(next_url, code=303)
        app.config["saved_store"].add_saved_view_to_dashboard(dashboard_id, saved_id)
        return redirect(next_url, code=303)

    @app.get("/dashboards/<int:dashboard_id>")
    def dashboard_detail_page(dashboard_id: int) -> Any:
        dashboard = app.config["saved_store"].get_dashboard(dashboard_id)
        if dashboard is None:
            return redirect(url_for("dashboards_page"), code=303)
        saved_views = app.config["saved_store"].list()

        metrics: list[dict[str, str]] = []
        try:
            metrics = app.config["prometheus_client"].list_metric_catalog()
        except (PrometheusError, OSError):
            metrics = []
        metric_type_map = _metric_types(metrics)
        metric_names = set(metric_type_map)

        cards: list[dict[str, Any]] = []
        for item in app.config["saved_store"].list_dashboard_items(dashboard_id):
            selected_metrics = _selected_metrics(",".join(item["metrics"]), metric_names)
            if not selected_metrics:
                cards.append(
                    {
                        "dashboard_item_id": item["dashboard_item_id"],
                        "saved_view_id": item["saved_view_id"],
                        "title": item["title"],
                        "view_url": (
                            f"{url_for('index')}"
                            f"?saved_id={item['saved_view_id']}&{item['query_string']}"
                        ),
                        "metrics_csv": "",
                        "label_filters_json": "{}",
                        "payload": None,
                        "error": "One or more metrics no longer exist in Prometheus.",
                    }
                )
                continue

            try:
                metric_label_filters = _resolve_metric_label_filters(
                    selected_metrics,
                    dict(item["label_filters"]),
                )
                payload = _build_view_payload(
                    app.config["prometheus_client"],
                    metrics=selected_metrics,
                    metric_types=metric_type_map,
                    window_amount=int(item["window_amount"]),
                    window_unit=str(item["window_unit"]),
                    step_amount=int(item["step_amount"]),
                    step_unit=str(item["step_unit"]),
                    metric_label_filters=metric_label_filters,
                    compare_enabled=bool(item["compare_enabled"]),
                )
                error: str | None = None
            except (PrometheusError, ValueError, OSError) as exc:
                payload = None
                error = str(exc)

            cards.append(
                {
                    "dashboard_item_id": item["dashboard_item_id"],
                    "saved_view_id": item["saved_view_id"],
                    "title": item["title"],
                    "view_url": (
                        f"{url_for('index')}?saved_id={item['saved_view_id']}&{item['query_string']}"
                    ),
                    "metrics_csv": ",".join(selected_metrics),
                    "label_filters_json": json.dumps(
                        dict(item["label_filters"]),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "payload": payload,
                    "error": error,
                }
            )

        default_dashboard_window_amount = DEFAULT_WINDOW_AMOUNT
        default_dashboard_window_unit = DEFAULT_WINDOW_UNIT
        default_dashboard_step_amount = DEFAULT_STEP_AMOUNT
        default_dashboard_step_unit = DEFAULT_STEP_UNIT
        default_dashboard_compare_enabled = False
        first_payload = next(
            (
                card.get("payload")
                for card in cards
                if isinstance(card.get("payload"), dict)
            ),
            None,
        )
        if isinstance(first_payload, dict):
            window = first_payload.get("window")
            step = first_payload.get("step")
            compare = first_payload.get("compare")
            if isinstance(window, dict):
                default_dashboard_window_amount = _sanitize_positive_int(
                    str(window.get("amount", default_dashboard_window_amount)),
                    default_dashboard_window_amount,
                )
                default_dashboard_window_unit = str(
                    window.get("unit", default_dashboard_window_unit)
                )
                default_dashboard_window_unit = _sanitize_choice(
                    default_dashboard_window_unit,
                    WINDOW_UNITS,
                    DEFAULT_WINDOW_UNIT,
                )
            if isinstance(step, dict):
                default_dashboard_step_amount = _sanitize_positive_int(
                    str(step.get("amount", default_dashboard_step_amount)),
                    default_dashboard_step_amount,
                )
                default_dashboard_step_unit = str(step.get("unit", default_dashboard_step_unit))
                default_dashboard_step_unit = _sanitize_choice(
                    default_dashboard_step_unit,
                    STEP_UNITS,
                    DEFAULT_STEP_UNIT,
                )
            if isinstance(compare, dict):
                default_dashboard_compare_enabled = bool(
                    compare.get("enabled", default_dashboard_compare_enabled)
                )

        return render_template(
            "dashboard_detail.html",
            dashboard=dashboard,
            cards=cards,
            saved_views=saved_views,
            current_url=request.path,
            window_units=WINDOW_UNITS,
            step_units=STEP_UNITS,
            default_dashboard_window_amount=default_dashboard_window_amount,
            default_dashboard_window_unit=default_dashboard_window_unit,
            default_dashboard_step_amount=default_dashboard_step_amount,
            default_dashboard_step_unit=default_dashboard_step_unit,
            default_dashboard_compare_enabled=default_dashboard_compare_enabled,
            live_refresh_seconds=app.config["settings"].live_refresh_seconds,
            active_nav="dashboards",
        )

    @app.get("/starred")
    def starred_page_redirect() -> Any:
        return redirect(url_for("saved_page"), code=308)

    @app.post("/api/saved")
    def save_view_api() -> tuple[dict[str, Any], int] | Any:
        payload = request.get_json(silent=True)
        payload_dict: dict[str, Any] = {}
        if isinstance(payload, dict):
            payload_dict = payload

        metrics_raw = str(payload_dict.get("metrics", "")).strip()
        if not metrics_raw:
            metrics_raw = request.form.get("metrics", "").strip()

        metric_catalog: list[dict[str, str]] = []
        try:
            metric_catalog = app.config["prometheus_client"].list_metric_catalog()
        except (PrometheusError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

        metric_names = {item["name"] for item in metric_catalog}
        selected_metrics = _selected_metrics(metrics_raw, metric_names)
        if not selected_metrics:
            return jsonify({"error": "at least one valid metric is required"}), 400

        window_amount = _sanitize_positive_int(
            str(payload_dict.get("window_amount", "")).strip()
            or request.form.get("window_amount"),
            DEFAULT_WINDOW_AMOUNT,
        )
        window_unit = _sanitize_choice(
            str(payload_dict.get("window_unit", "")).strip() or request.form.get("window_unit"),
            WINDOW_UNITS,
            DEFAULT_WINDOW_UNIT,
        )
        step_amount = _sanitize_positive_int(
            str(payload_dict.get("step_amount", "")).strip() or request.form.get("step_amount"),
            DEFAULT_STEP_AMOUNT,
        )
        step_unit = _sanitize_choice(
            str(payload_dict.get("step_unit", "")).strip() or request.form.get("step_unit"),
            STEP_UNITS,
            DEFAULT_STEP_UNIT,
        )

        compare_raw = payload_dict.get("compare_enabled")
        if compare_raw is None:
            compare_raw = request.form.get("compare_enabled")
        compare_enabled = _parse_bool(str(compare_raw) if compare_raw is not None else None, False)
        saved_view_id = _sanitize_optional_positive_int(
            str(payload_dict.get("saved_id", "")).strip() or request.form.get("saved_id")
        )
        save_as_new_raw = payload_dict.get("save_as_new")
        if save_as_new_raw is None:
            save_as_new_raw = request.form.get("save_as_new")
        save_as_new = _parse_bool(
            str(save_as_new_raw) if save_as_new_raw is not None else None,
            False,
        )
        if save_as_new:
            saved_view_id = None
        title_raw = str(payload_dict.get("title", "")).strip() or request.form.get(
            "title",
            "",
        ).strip()

        label_filters_raw = payload_dict.get("label_filters")
        metric_label_filters = (
            _parse_metric_label_filters_object(label_filters_raw)
            if isinstance(label_filters_raw, dict)
            else _parse_metric_label_filters(str(label_filters_raw) if label_filters_raw else None)
        )
        if not metric_label_filters:
            metric_label_filters = _parse_metric_label_filters(request.form.get("label_filters"))
        metric_label_filters = _resolve_metric_label_filters(
            selected_metrics,
            metric_label_filters,
        )

        try:
            metric_label_filters = _sanitize_metric_label_filters(
                app.config["prometheus_client"],
                selected_metrics,
                metric_label_filters,
            )
        except (PrometheusError, OSError):
            metric_label_filters = {metric_name: {} for metric_name in selected_metrics}

        query_string = _build_view_query_string(
            metrics=selected_metrics,
            window_amount=window_amount,
            window_unit=window_unit,
            step_amount=step_amount,
            step_unit=step_unit,
            compare_enabled=compare_enabled,
            metric_label_filters=metric_label_filters,
        )
        if title_raw:
            title = title_raw
        elif saved_view_id is not None:
            existing_saved = app.config["saved_store"].get(saved_view_id)
            if existing_saved:
                title = str(existing_saved.get("title", "")).strip() or _saved_view_title(
                    metrics=selected_metrics,
                    window_amount=window_amount,
                    window_unit=window_unit,
                    step_amount=step_amount,
                    step_unit=step_unit,
                    compare_enabled=compare_enabled,
                )
            else:
                title = _saved_view_title(
                    metrics=selected_metrics,
                    window_amount=window_amount,
                    window_unit=window_unit,
                    step_amount=step_amount,
                    step_unit=step_unit,
                    compare_enabled=compare_enabled,
                )
        else:
            title = _saved_view_title(
                metrics=selected_metrics,
                window_amount=window_amount,
                window_unit=window_unit,
                step_amount=step_amount,
                step_unit=step_unit,
                compare_enabled=compare_enabled,
            )

        saved_entry, created = app.config["saved_store"].save(
            saved_view_id=saved_view_id,
            title=title,
            metrics_csv=",".join(selected_metrics),
            window_amount=window_amount,
            window_unit=window_unit,
            step_amount=step_amount,
            step_unit=step_unit,
            compare_enabled=compare_enabled,
            label_filters=metric_label_filters,
            query_string=query_string,
            force_create=save_as_new,
        )

        return (
            jsonify(
                {
                    "id": saved_entry["id"],
                    "title": saved_entry["title"],
                    "created": created,
                    "url": (
                        f"{url_for('index')}"
                        f"?saved_id={saved_entry['id']}&{saved_entry['query_string']}"
                    ),
                    "updated_at": saved_entry["updated_at"],
                }
            ),
            201 if created else 200,
        )

    @app.delete("/api/saved/<int:saved_id>")
    def delete_saved_view_api(saved_id: int) -> tuple[dict[str, Any], int] | Any:
        deleted = app.config["saved_store"].remove(saved_id)
        if not deleted:
            return jsonify({"error": "saved view not found"}), 404
        return jsonify({"deleted": True, "id": saved_id})

    @app.post("/api/saved/<int:saved_id>/rename")
    def rename_saved_view_api(saved_id: int) -> tuple[dict[str, Any], int] | Any:
        payload = request.get_json(silent=True)
        title = ""
        if isinstance(payload, dict):
            title = str(payload.get("title", "")).strip()
        if not title:
            title = request.form.get("title", "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400

        entry = app.config["saved_store"].rename(saved_id, title)
        if entry is None:
            return jsonify({"error": "saved view not found"}), 404
        return jsonify({"id": entry["id"], "title": entry["title"]})

    @app.post("/api/dashboards/<int:dashboard_id>/reorder")
    def reorder_dashboard_items_api(dashboard_id: int) -> tuple[dict[str, Any], int] | Any:
        payload = request.get_json(silent=True)
        item_ids_raw: list[Any] = []
        if isinstance(payload, dict) and isinstance(payload.get("item_ids"), list):
            item_ids_raw = payload.get("item_ids", [])

        item_ids: list[int] = []
        for raw in item_ids_raw:
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                item_ids.append(parsed)

        if not item_ids:
            return jsonify({"error": "item_ids is required"}), 400

        ok = app.config["saved_store"].reorder_dashboard_items(dashboard_id, item_ids)
        if not ok:
            return jsonify({"error": "invalid dashboard item order"}), 400
        return jsonify({"ok": True, "dashboard_id": dashboard_id, "item_ids": item_ids})

    @app.get("/alerts")
    def alerts_page() -> str:
        alerts: list[dict[str, str]] = []
        alerts_error: str | None = None
        client_ref = app.config["prometheus_client"]
        try:
            if hasattr(client_ref, "list_alerts"):
                alerts = client_ref.list_alerts()
        except (PrometheusError, OSError) as exc:
            alerts_error = str(exc)

        return render_template(
            "alerts.html",
            alerts=alerts,
            alerts_error=alerts_error,
            active_nav="alerts",
        )

    @app.get("/api/view-data")
    def view_data() -> tuple[dict[str, Any], int] | Any:
        metrics_raw = request.args.get("metrics", "").strip()

        metric_catalog: list[dict[str, str]] = []
        try:
            metric_catalog = app.config["prometheus_client"].list_metric_catalog()
        except (PrometheusError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

        metric_names = {item["name"] for item in metric_catalog}
        selected_metrics = _selected_metrics(metrics_raw, metric_names)
        if not selected_metrics:
            return jsonify({"error": "at least one metric is required"}), 400

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
        compare_enabled = _parse_bool(request.args.get("compare_enabled"), False)
        metric_label_filters = _resolve_metric_label_filters(
            selected_metrics,
            _parse_metric_label_filters(request.args.get("label_filters")),
        )

        try:
            metric_type_map = _metric_types(metric_catalog)
            payload = _build_view_payload(
                app.config["prometheus_client"],
                metrics=selected_metrics,
                metric_types=metric_type_map,
                window_amount=window_amount,
                window_unit=window_unit,
                step_amount=step_amount,
                step_unit=step_unit,
                metric_label_filters=metric_label_filters,
                compare_enabled=compare_enabled,
            )
        except (PrometheusError, ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify(payload)

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
        compare_enabled = _parse_bool(request.args.get("compare_enabled"), False)
        label_filters = _parse_label_filters(request.args.get("label_filters"))

        try:
            metric_type = _metric_type_for_name(metric, {})
            payload = _build_payload(
                app.config["prometheus_client"],
                metric=metric,
                metric_type=metric_type,
                window_amount=window_amount,
                window_unit=window_unit,
                step_amount=step_amount,
                step_unit=step_unit,
                label_filters=label_filters,
                compare_enabled=compare_enabled,
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
        compare_enabled = _parse_bool(request.args.get("compare_enabled"), False)
        label_filters = _parse_label_filters(request.args.get("label_filters"))

        try:
            metric_type = _metric_type_for_name(metric, {})
            payload = _build_payload(
                app.config["prometheus_client"],
                metric=metric,
                metric_type=metric_type,
                window_amount=window_amount,
                window_unit=window_unit,
                step_amount=step_amount,
                step_unit=step_unit,
                label_filters=label_filters,
                compare_enabled=compare_enabled,
            )
        except (PrometheusError, ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify(payload)

    @app.get("/healthz")
    def healthz() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    return app


app = create_app()
