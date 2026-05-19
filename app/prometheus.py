from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

_DURATION_RE = re.compile(r"(\d+)([smhdwy])")
_DURATION_TO_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
    "y": 60 * 60 * 24 * 365,
}


class PrometheusError(RuntimeError):
    pass


class PrometheusUnreachableError(RuntimeError):
    """Raised when the Prometheus server cannot be reached at all."""

    def __init__(
        self,
        message: str,
        base_url: str,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.base_url = base_url
        self.cause = cause


def normalize_metric_type(raw_type: str | None) -> str:
    if not raw_type:
        return "unknown"

    lowered = raw_type.lower()
    if lowered in {"counter", "gauge", "histogram", "summary", "timing", "untyped"}:
        return lowered
    return "unknown"


def fallback_metric_type(metric_name: str) -> str:
    if metric_name.endswith("_total"):
        return "counter"
    if metric_name.endswith("_bucket"):
        return "histogram"
    if metric_name.endswith("_quantile"):
        return "summary"
    if metric_name.endswith("_sum") or metric_name.endswith("_count"):
        return "counter"
    return "untyped"


_METRIC_TYPE_LABEL_MAP: dict[str, str] = {
    "timing": "timing",
    "gauge": "gauge",
    "set": "gauge",
    "histogram": "gauge",
    "counter": "counter",
}


def metric_type_from_label(label_value: str | None) -> str | None:
    if not label_value:
        return None
    return _METRIC_TYPE_LABEL_MAP.get(label_value)


def parse_prometheus_duration(duration: str) -> int:
    """Parse Prometheus durations like 5m, 1h30m, 7d into seconds."""
    if not duration:
        raise ValueError("duration must not be empty")

    total = 0
    position = 0
    for match in _DURATION_RE.finditer(duration):
        if match.start() != position:
            raise ValueError(f"invalid duration: {duration}")
        value = int(match.group(1))
        unit = match.group(2)
        total += value * _DURATION_TO_SECONDS[unit]
        position = match.end()

    if position != len(duration):
        raise ValueError(f"invalid duration: {duration}")

    return total


def series_label(metric_name: str, labels: dict[str, str]) -> str:
    items = {k: v for k, v in labels.items() if k != "__name__"}
    if not items:
        return metric_name
    rendered = ", ".join(f"{k}={v}" for k, v in sorted(items.items()))
    return f"{metric_name} ({rendered})"


def aggregate_series_points(series: list[dict[str, Any]]) -> list[dict[str, float]]:
    buckets: dict[float, float] = {}
    for item in series:
        points = item.get("points", [])
        for point in points:
            timestamp = point.get("t")
            value = point.get("v")
            if not isinstance(timestamp, float | int):
                continue
            if not isinstance(value, float | int):
                continue
            ts_key = float(timestamp)
            buckets[ts_key] = buckets.get(ts_key, 0.0) + float(value)
    return [{"t": ts, "v": buckets[ts]} for ts in sorted(buckets)]


def _split_credentials(url: str) -> tuple[str, tuple[str, str] | None]:
    """Split userinfo out of a URL.

    Returns (url_without_userinfo, (username, password) | None).
    """
    parts = urlsplit(url)
    if parts.username is None:
        return url, None

    username = unquote(parts.username)
    password = unquote(parts.password) if parts.password is not None else ""

    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"

    cleaned = urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return cleaned, (username, password)


def _resolve_auth(
    *,
    username: str | None,
    password: str,
    embedded: tuple[str, str] | None,
) -> tuple[str, str] | None:
    """Resolve which basic-auth credentials to use.

    Kwarg-supplied username/password take precedence over credentials
    embedded in the URL. If neither is set, returns None.
    """
    if username:
        return (username, password)
    return embedded


class PrometheusClient:
    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str = "",
        http_client: httpx.Client | None = None,
    ) -> None:
        cleaned_url, embedded_auth = _split_credentials(base_url)
        self.base_url = cleaned_url.rstrip("/")

        if http_client is not None:
            # Test-supplied client: caller is responsible for auth setup.
            self._http = http_client
            return

        auth = _resolve_auth(
            username=username,
            password=password,
            embedded=embedded_auth,
        )
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=10.0,
            auth=auth,
        )

    def close(self) -> None:
        self._http.close()

    def _fetch(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.get(path, params=params)
        except httpx.TransportError as exc:
            raise PrometheusUnreachableError(
                f"Cannot reach Prometheus at {self.base_url}: {exc}",
                base_url=self.base_url,
                cause=exc,
            ) from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PrometheusError(f"Prometheus returned HTTP {exc.response.status_code}") from exc
        payload = response.json()
        if payload.get("status") != "success":
            error = payload.get("error") or "unknown Prometheus error"
            raise PrometheusError(error)
        return payload.get("data")

    def list_metric_names(self) -> list[str]:
        data = self._fetch("/api/v1/label/__name__/values")
        values = data if isinstance(data, list) else []
        return sorted(values)

    def metric_types(self) -> dict[str, str]:
        try:
            data = self._fetch("/api/v1/metadata")
        except (httpx.HTTPError, PrometheusError, PrometheusUnreachableError, ValueError):
            return {}

        if not isinstance(data, dict):
            return {}

        resolved: dict[str, str] = {}
        for metric_name, metadata_rows in data.items():
            if not isinstance(metric_name, str) or not metric_name:
                continue
            if not isinstance(metadata_rows, list):
                continue

            metric_type = "unknown"
            for row in metadata_rows:
                if not isinstance(row, dict):
                    continue
                metric_type = normalize_metric_type(row.get("type"))
                if metric_type != "unknown":
                    break
            resolved[metric_name] = metric_type

        return resolved

    def metric_type_labels(self) -> dict[str, str]:
        try:
            data = self._fetch("/api/v1/series", params={"match[]": '{metric_type!=""}'})
        except (httpx.HTTPError, PrometheusError, ValueError):
            return {}

        if not isinstance(data, list):
            return {}

        resolved: dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            metric_name = item.get("__name__")
            label_value = item.get("metric_type")
            if not isinstance(metric_name, str) or not metric_name:
                continue
            if not isinstance(label_value, str) or not label_value:
                continue
            existing = resolved.get(metric_name)
            if existing is None or label_value < existing:
                resolved[metric_name] = label_value
        return resolved

    def list_metric_catalog(self) -> list[dict[str, str]]:
        names = self.list_metric_names()
        types = self.metric_types()
        label_types = self.metric_type_labels()
        catalog: list[dict[str, str]] = []
        for name in names:
            metric_type = types.get(name, "unknown")
            if metric_type == "unknown":
                from_label = metric_type_from_label(label_types.get(name))
                if from_label is not None:
                    metric_type = from_label
            if metric_type == "unknown":
                metric_type = fallback_metric_type(name)
            catalog.append({"name": name, "type": metric_type})
        return catalog

    def metric_label_options(self, metric_name: str) -> dict[str, list[str]]:
        data = self._fetch("/api/v1/series", params={"match[]": metric_name})
        if not isinstance(data, list):
            return {}

        label_values: dict[str, set[str]] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            for label_name, label_value in item.items():
                if label_name == "__name__":
                    continue
                if not isinstance(label_name, str) or not isinstance(label_value, str):
                    continue
                if not label_name or not label_value:
                    continue
                label_values.setdefault(label_name, set()).add(label_value)

        return {
            label_name: sorted(values)
            for label_name, values in sorted(label_values.items())
            if values
        }

    def list_alerts(self) -> list[dict[str, str]]:
        data = self._fetch("/api/v1/alerts")
        if not isinstance(data, dict):
            return []

        alerts_raw = data.get("alerts", [])
        if not isinstance(alerts_raw, list):
            return []

        alerts: list[dict[str, str]] = []
        for row in alerts_raw:
            if not isinstance(row, dict):
                continue
            labels = row.get("labels", {})
            annotations = row.get("annotations", {})
            alert_name = ""
            if isinstance(labels, dict):
                alert_name = str(labels.get("alertname", ""))
            summary = ""
            if isinstance(annotations, dict):
                summary = str(annotations.get("summary") or annotations.get("description") or "")
            alerts.append(
                {
                    "name": alert_name or "unknown",
                    "state": str(row.get("state", "unknown")),
                    "active_at": str(row.get("activeAt", "")),
                    "value": str(row.get("value", "")),
                    "summary": summary,
                }
            )
        return alerts

    def query_range(
        self,
        query: str,
        window: str,
        step: str,
        end_offset: str = "0s",
        series_name: str | None = None,
    ) -> dict[str, Any]:
        now_ts = datetime.now(UTC).timestamp()
        end_ts = now_ts - parse_prometheus_duration(end_offset)
        start_ts = end_ts - parse_prometheus_duration(window)
        display_name = series_name or query

        data = self._fetch(
            "/api/v1/query_range",
            params={
                "query": query,
                "start": start_ts,
                "end": end_ts,
                "step": step,
            },
        )

        result = data.get("result", []) if isinstance(data, dict) else []
        series: list[dict[str, Any]] = []
        for item in result:
            labels = item.get("metric", {})
            values = item.get("values", [])
            points = []
            for ts, value in values:
                try:
                    timestamp = float(ts)
                    point_value = float(value)
                except (TypeError, ValueError):
                    continue
                if not (math.isfinite(timestamp) and math.isfinite(point_value)):
                    continue
                points.append({"t": timestamp, "v": point_value})
            series.append(
                {
                    "label": series_label(display_name, labels),
                    "labels": labels,
                    "points": points,
                }
            )

        return {
            "metric": display_name,
            "query": query,
            "window": window,
            "step": step,
            "end_offset": end_offset,
            "start": start_ts,
            "end": end_ts,
            "series": series,
            "aggregate": {
                "label": display_name,
                "points": aggregate_series_points(series),
            },
        }
