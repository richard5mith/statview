from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command

_STATVIEW_TABLES = {
    "saved_views",
    "dashboards",
    "dashboard_items",
}


def resolve_db_path(db_path: str) -> Path:
    return Path(db_path).expanduser().resolve(strict=False)


def sqlite_url(db_path: str) -> str:
    return f"sqlite:///{resolve_db_path(db_path)}"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _alembic_config(db_path: str) -> Config:
    root = _project_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", sqlite_url(db_path))
    return config


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _has_existing_statview_schema(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    ).fetchall()
    table_names = {str(row[0]) for row in rows}
    return bool(table_names & _STATVIEW_TABLES)


def migrate_database(db_path: str) -> None:
    resolved = resolve_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    has_existing_schema = False
    has_version_table = False
    with sqlite3.connect(resolved) as conn:
        has_existing_schema = _has_existing_statview_schema(conn)
        has_version_table = _table_exists(conn, "alembic_version")

    config = _alembic_config(str(resolved))

    if has_existing_schema and not has_version_table:
        command.stamp(config, "head")

    command.upgrade(config, "head")
