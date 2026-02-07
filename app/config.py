from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    prometheus_url: str = field(
        default_factory=lambda: os.getenv("PROMETHEUS_URL", "http://localhost:9090")
    )
    live_refresh_seconds: int = field(
        default_factory=lambda: int(os.getenv("LIVE_REFRESH_SECONDS", "15"))
    )


WINDOW_UNITS = ["hour", "day", "week", "month", "year"]
STEP_UNITS = ["minute", "hour", "day", "week", "year"]

COMPARE_OFFSETS = [
    {"value": "1h", "label": "1 hour ago"},
    {"value": "1d", "label": "1 day ago"},
    {"value": "1w", "label": "1 week ago"},
    {"value": "30d", "label": "1 month ago"},
]

STANDARD_PRESETS = [
    {"id": "1h_1m", "label": "1 hour @ 1 minute", "window": "1h", "step": "1m"},
    {"id": "12h_10m", "label": "12 hours @ 10 minutes", "window": "12h", "step": "10m"},
    {"id": "1d_15m", "label": "1 day @ 15 minutes", "window": "1d", "step": "15m"},
    {"id": "1w_3h", "label": "1 week @ 3 hours", "window": "1w", "step": "3h"},
    {"id": "1m_1d", "label": "1 month @ 1 day", "window": "30d", "step": "1d"},
    {"id": "1y_1w", "label": "1 year @ 1 week", "window": "1y", "step": "1w"},
]

DEFAULT_WINDOW_AMOUNT = 1
DEFAULT_WINDOW_UNIT = "week"
DEFAULT_STEP_AMOUNT = 1
DEFAULT_STEP_UNIT = "hour"
DEFAULT_COMPARE_ENABLED = False
DEFAULT_COMPARE_OFFSET = "1w"
