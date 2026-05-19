from __future__ import annotations

import json
import sqlite3
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for

from app.config import (
    DEFAULT_STEP_AMOUNT,
    DEFAULT_STEP_UNIT,
    DEFAULT_WINDOW_AMOUNT,
    DEFAULT_WINDOW_UNIT,
    STEP_UNITS,
    WINDOW_UNITS,
)
from app.prometheus import PrometheusError, PrometheusUnreachableError
from app.services.view_backend import (
    _build_payload,
    _build_view_payload,
    _build_view_query_string,
    _metric_type_for_name,
    _metric_types,
    _parse_bool,
    _parse_label_filters,
    _parse_metric_label_filters,
    _parse_metric_label_filters_object,
    _resolve_metric_label_filters,
    _sanitize_choice,
    _sanitize_metric_label_filters,
    _sanitize_optional_positive_int,
    _sanitize_positive_int,
    _saved_view_title,
    _selected_metrics,
)

TYPE_OVERRIDE_CHOICES = ["counter", "gauge", "timing", "histogram", "summary", "untyped"]
AGG_OVERRIDE_CHOICES = ["sum", "avg", "max", "min"]


def _sanitize_choice_or_none(value: str | None, allowed: list[str]) -> str | None:
    if not value:
        return None
    candidate = value.strip().lower()
    return candidate if candidate in allowed else None


def register_routes(app: Flask) -> None:
    @app.errorhandler(PrometheusUnreachableError)
    def _handle_prometheus_unreachable(
        exc: PrometheusUnreachableError,
    ) -> tuple[Any, int]:
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "error": "prometheus_unreachable",
                        "message": str(exc),
                        "base_url": exc.base_url,
                    }
                ),
                503,
            )
        return (
            render_template(
                "prometheus_unavailable.html",
                prometheus_url=exc.base_url,
                error_message=str(exc),
                active_nav=None,
            ),
            503,
        )

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

        type_override = _sanitize_choice_or_none(
            request.args.get("type_override"), TYPE_OVERRIDE_CHOICES
        )
        agg_override = _sanitize_choice_or_none(
            request.args.get("agg_override"), AGG_OVERRIDE_CHOICES
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
                    type_override=type_override,
                    agg_override=agg_override,
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
                type_override = _sanitize_choice_or_none(
                    request.args.get("type_override"), TYPE_OVERRIDE_CHOICES
                )
                agg_override = _sanitize_choice_or_none(
                    request.args.get("agg_override"), AGG_OVERRIDE_CHOICES
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
                    type_override=type_override,
                    agg_override=agg_override,
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
            (card.get("payload") for card in cards if isinstance(card.get("payload"), dict)),
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
            str(payload_dict.get("window_amount", "")).strip() or request.form.get("window_amount"),
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
        title_raw = (
            str(payload_dict.get("title", "")).strip()
            or request.form.get(
                "title",
                "",
            ).strip()
        )

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

        type_override = _sanitize_choice_or_none(
            request.args.get("type_override"), TYPE_OVERRIDE_CHOICES
        )
        agg_override = _sanitize_choice_or_none(
            request.args.get("agg_override"), AGG_OVERRIDE_CHOICES
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
                type_override=type_override,
                agg_override=agg_override,
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
                agg_override=None,
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
                agg_override=None,
            )
        except (PrometheusError, ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify(payload)

    @app.get("/healthz")
    def healthz() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200
