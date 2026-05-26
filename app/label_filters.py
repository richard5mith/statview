from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LabelFilters:
    shared: dict[str, str] = field(default_factory=dict)
    per_metric: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str | object | None) -> LabelFilters:
        if raw is None or raw == "":
            return cls()
        if isinstance(raw, str):
            try:
                decoded: object = json.loads(raw)
            except json.JSONDecodeError:
                return cls()
        else:
            decoded = raw
        if not isinstance(decoded, dict):
            return cls()

        shared: dict[str, str] = {}
        per_metric: dict[str, dict[str, str]] = {}
        for key, value in decoded.items():
            if not isinstance(key, str) or not key:
                continue
            if isinstance(value, str):
                if value:
                    shared[key] = value
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
            if not cleaned:
                continue
            if key == "*":
                shared.update(cleaned)
            else:
                per_metric[key] = cleaned

        return cls(shared=shared, per_metric=per_metric)

    def is_empty(self) -> bool:
        return not self.shared and not self.per_metric

    def to_json(self) -> str:
        if not self.shared and not self.per_metric:
            return "{}"
        if not self.per_metric:
            return json.dumps(self.shared, sort_keys=True, separators=(",", ":"))
        if not self.shared:
            return json.dumps(self.per_metric, sort_keys=True, separators=(",", ":"))
        merged: dict[str, dict[str, str]] = {"*": dict(self.shared), **self.per_metric}
        return json.dumps(merged, sort_keys=True, separators=(",", ":"))

    def for_metric(self, metric: str) -> dict[str, str]:
        merged = dict(self.shared)
        merged.update(self.per_metric.get(metric, {}))
        return merged

    def resolve(self, metrics: list[str]) -> dict[str, dict[str, str]]:
        return {metric: self.for_metric(metric) for metric in metrics}
