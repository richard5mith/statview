from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEST_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "test"
TEST_DB_PATH = TEST_DATA_DIR / "save.sqlite3"


@dataclass(frozen=True)
class DatabaseFixture:
    db_path: Path = TEST_DB_PATH

    def reset(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
