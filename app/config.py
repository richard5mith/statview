from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    prometheus_url: str = field(
        default_factory=lambda: os.getenv("PROMETHEUS_URL", "http://localhost:9090")
    )
    prometheus_username: str | None = field(
        default_factory=lambda: os.getenv("PROMETHEUS_USERNAME") or None
    )
    prometheus_password: str = field(default_factory=lambda: os.getenv("PROMETHEUS_PASSWORD", ""))
    live_refresh_seconds: int = field(
        default_factory=lambda: int(os.getenv("LIVE_REFRESH_SECONDS", "15"))
    )
    app_data_dir: str = field(
        default_factory=lambda: os.getenv(
            "STATVIEW_DATA_DIR",
            os.getenv("APP_DATA_DIR", "./data"),
        )
    )
    saved_db_filename: str = field(
        default_factory=lambda: os.getenv("STATVIEW_DB_FILENAME", "statview.sqlite3")
    )
    saved_db_path: str = field(
        default_factory=lambda: os.getenv(
            "SAVED_DB_PATH",
            os.getenv("STAR_DB_PATH", ""),
        )
    )

    def __post_init__(self) -> None:
        data_dir = Path(self.app_data_dir).expanduser().resolve(strict=False)
        db_path_raw = self.saved_db_path.strip()
        db_path = (
            Path(db_path_raw).expanduser().resolve(strict=False)
            if db_path_raw
            else (data_dir / self.saved_db_filename).resolve(strict=False)
        )

        object.__setattr__(self, "app_data_dir", str(data_dir))
        object.__setattr__(self, "saved_db_path", str(db_path))


WINDOW_UNITS = ["hour", "day", "week", "month", "year"]
STEP_UNITS = ["minute", "hour", "day", "week", "year"]

COMPARE_OFFSETS = [
    {"value": "none", "label": "None"},
    {"value": "1h", "label": "1 hour ago"},
    {"value": "1d", "label": "1 day ago"},
    {"value": "1w", "label": "1 week ago"},
    {"value": "30d", "label": "1 month ago"},
]

STANDARD_PRESETS = [
    {
        "id": "1h_1m",
        "label": "1 hour @ 1 minute",
        "window": "1h",
        "step": "1m",
        "window_amount": 1,
        "window_unit": "hour",
        "step_amount": 1,
        "step_unit": "minute",
    },
    {
        "id": "12h_10m",
        "label": "12 hours @ 10 minutes",
        "window": "12h",
        "step": "10m",
        "window_amount": 12,
        "window_unit": "hour",
        "step_amount": 10,
        "step_unit": "minute",
    },
    {
        "id": "1d_15m",
        "label": "1 day @ 15 minutes",
        "window": "1d",
        "step": "15m",
        "window_amount": 1,
        "window_unit": "day",
        "step_amount": 15,
        "step_unit": "minute",
    },
    {
        "id": "1w_3h",
        "label": "1 week @ 3 hours",
        "window": "1w",
        "step": "3h",
        "window_amount": 1,
        "window_unit": "week",
        "step_amount": 3,
        "step_unit": "hour",
    },
    {
        "id": "1m_1d",
        "label": "1 month @ 1 day",
        "window": "30d",
        "step": "1d",
        "window_amount": 1,
        "window_unit": "month",
        "step_amount": 1,
        "step_unit": "day",
    },
    {
        "id": "1y_1w",
        "label": "1 year @ 1 week",
        "window": "1y",
        "step": "1w",
        "window_amount": 1,
        "window_unit": "year",
        "step_amount": 1,
        "step_unit": "week",
    },
]

DEFAULT_WINDOW_AMOUNT = 1
DEFAULT_WINDOW_UNIT = "week"
DEFAULT_STEP_AMOUNT = 1
DEFAULT_STEP_UNIT = "hour"
DEFAULT_COMPARE_ENABLED = False
DEFAULT_COMPARE_OFFSET = "none"
