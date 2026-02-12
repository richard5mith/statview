from __future__ import annotations

import json
import sqlite3
from typing import Any


class SavedViewStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_entry(self, row: sqlite3.Row) -> dict[str, Any]:
        label_filters = _decode_label_filters_json(str(row["label_filters_json"] or "{}"))

        metrics = [chunk.strip() for chunk in row["metrics_csv"].split(",") if chunk.strip()]
        return {
            "id": int(row["id"]),
            "title": row["title"],
            "metrics": metrics,
            "metrics_csv": row["metrics_csv"],
            "window_amount": int(row["window_amount"]),
            "window_unit": row["window_unit"],
            "step_amount": int(row["step_amount"]),
            "step_unit": row["step_unit"],
            "compare_enabled": bool(row["compare_enabled"]),
            "label_filters": label_filters,
            "query_string": row["query_string"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self, search: str = "") -> list[dict[str, Any]]:
        params: tuple[str, ...] = ()
        query = """
            SELECT
                id,
                title,
                metrics_csv,
                window_amount,
                window_unit,
                step_amount,
                step_unit,
                compare_enabled,
                label_filters_json,
                query_string,
                created_at,
                updated_at
            FROM saved_views
        """
        if search:
            wildcard = f"%{search.lower()}%"
            query += " WHERE lower(title) LIKE ? OR lower(metrics_csv) LIKE ?"
            params = (wildcard, wildcard)
        query += " ORDER BY datetime(updated_at) DESC, id DESC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get(self, saved_view_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    title,
                    metrics_csv,
                    window_amount,
                    window_unit,
                    step_amount,
                    step_unit,
                    compare_enabled,
                    label_filters_json,
                    query_string,
                    created_at,
                    updated_at
                FROM saved_views
                WHERE id = ?
                LIMIT 1
                """,
                (saved_view_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def save(
        self,
        *,
        saved_view_id: int | None = None,
        title: str,
        metrics_csv: str,
        window_amount: int,
        window_unit: str,
        step_amount: int,
        step_unit: str,
        compare_enabled: bool,
        label_filters: dict[str, Any],
        query_string: str,
        force_create: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        label_filters_json = json.dumps(label_filters, sort_keys=True, separators=(",", ":"))

        with self._connect() as conn:
            existing_by_id: sqlite3.Row | None = None
            existing_by_query: sqlite3.Row | None = None
            inserted_id: int | None = None

            if saved_view_id is not None:
                existing_by_id = conn.execute(
                    "SELECT id FROM saved_views WHERE id = ? LIMIT 1",
                    (saved_view_id,),
                ).fetchone()
                if existing_by_id is not None:
                    conn.execute(
                        """
                        UPDATE saved_views
                        SET
                            title = ?,
                            metrics_csv = ?,
                            window_amount = ?,
                            window_unit = ?,
                            step_amount = ?,
                            step_unit = ?,
                            compare_enabled = ?,
                            label_filters_json = ?,
                            query_string = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            title,
                            metrics_csv,
                            window_amount,
                            window_unit,
                            step_amount,
                            step_unit,
                            int(compare_enabled),
                            label_filters_json,
                            query_string,
                            saved_view_id,
                        ),
                    )

            if existing_by_id is None:
                if not force_create:
                    existing_by_query = conn.execute(
                        "SELECT id FROM saved_views WHERE query_string = ? ORDER BY id ASC LIMIT 1",
                        (query_string,),
                    ).fetchone()

                if existing_by_query is not None:
                    conn.execute(
                        """
                        UPDATE saved_views
                        SET
                            title = ?,
                            metrics_csv = ?,
                            window_amount = ?,
                            window_unit = ?,
                            step_amount = ?,
                            step_unit = ?,
                            compare_enabled = ?,
                            label_filters_json = ?,
                            query_string = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            title,
                            metrics_csv,
                            window_amount,
                            window_unit,
                            step_amount,
                            step_unit,
                            int(compare_enabled),
                            label_filters_json,
                            query_string,
                            existing_by_query["id"],
                        ),
                    )
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO saved_views(
                            title,
                            metrics_csv,
                            window_amount,
                            window_unit,
                            step_amount,
                            step_unit,
                            compare_enabled,
                            label_filters_json,
                            query_string
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            title,
                            metrics_csv,
                            window_amount,
                            window_unit,
                            step_amount,
                            step_unit,
                            int(compare_enabled),
                            label_filters_json,
                            query_string,
                        ),
                    )
                    inserted_id = int(cursor.lastrowid)
            conn.commit()

            if existing_by_id is not None:
                row = conn.execute(
                    """
                    SELECT
                        id,
                        title,
                        metrics_csv,
                        window_amount,
                        window_unit,
                        step_amount,
                        step_unit,
                        compare_enabled,
                        label_filters_json,
                        query_string,
                        created_at,
                        updated_at
                    FROM saved_views
                    WHERE id = ?
                    LIMIT 1
                    """,
                    (saved_view_id,),
                ).fetchone()
            elif existing_by_query is not None:
                row = conn.execute(
                    """
                    SELECT
                        id,
                        title,
                        metrics_csv,
                        window_amount,
                        window_unit,
                        step_amount,
                        step_unit,
                        compare_enabled,
                        label_filters_json,
                        query_string,
                        created_at,
                        updated_at
                    FROM saved_views
                    WHERE id = ?
                    LIMIT 1
                    """,
                    (existing_by_query["id"],),
                ).fetchone()
            elif inserted_id is not None:
                row = conn.execute(
                    """
                    SELECT
                        id,
                        title,
                        metrics_csv,
                        window_amount,
                        window_unit,
                        step_amount,
                        step_unit,
                        compare_enabled,
                        label_filters_json,
                        query_string,
                        created_at,
                        updated_at
                    FROM saved_views
                    WHERE id = ?
                    LIMIT 1
                    """,
                    (inserted_id,),
                ).fetchone()
            else:
                row = None

        if row is None:
            raise RuntimeError("failed to read saved view after write")
        created = existing_by_id is None and existing_by_query is None
        return self._row_to_entry(row), created

    def remove(self, saved_view_id: int) -> bool:
        with self._connect() as conn:
            result = conn.execute("DELETE FROM saved_views WHERE id = ?", (saved_view_id,))
            conn.commit()
        return result.rowcount > 0

    def rename(self, saved_view_id: int, title: str) -> dict[str, Any] | None:
        cleaned = title.strip()
        if not cleaned:
            return None

        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE saved_views
                SET
                    title = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (cleaned, saved_view_id),
            )
            conn.commit()
            if result.rowcount == 0:
                return None

            row = conn.execute(
                """
                SELECT
                    id,
                    title,
                    metrics_csv,
                    window_amount,
                    window_unit,
                    step_amount,
                    step_unit,
                    compare_enabled,
                    label_filters_json,
                    query_string,
                    created_at,
                    updated_at
                FROM saved_views
                WHERE id = ?
                LIMIT 1
                """,
                (saved_view_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def list_dashboards(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    d.id,
                    d.name,
                    d.created_at,
                    d.updated_at,
                    COUNT(di.id) AS item_count
                FROM dashboards d
                LEFT JOIN dashboard_items di ON di.dashboard_id = d.id
                GROUP BY d.id
                ORDER BY lower(d.name) ASC
                """
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": row["name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "item_count": int(row["item_count"] or 0),
            }
            for row in rows
        ]

    def create_dashboard(self, name: str) -> dict[str, Any]:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("dashboard name is required")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dashboards(name)
                VALUES (?)
                """,
                (cleaned,),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT id, name, created_at, updated_at
                FROM dashboards
                WHERE id = last_insert_rowid()
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            raise RuntimeError("failed to read dashboard after create")
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "item_count": 0,
        }

    def get_dashboard(self, dashboard_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    d.id,
                    d.name,
                    d.created_at,
                    d.updated_at,
                    COUNT(di.id) AS item_count
                FROM dashboards d
                LEFT JOIN dashboard_items di ON di.dashboard_id = d.id
                WHERE d.id = ?
                GROUP BY d.id
                LIMIT 1
                """,
                (dashboard_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "item_count": int(row["item_count"] or 0),
        }

    def add_saved_view_to_dashboard(self, dashboard_id: int, saved_view_id: int) -> bool:
        with self._connect() as conn:
            dashboard = conn.execute(
                "SELECT id FROM dashboards WHERE id = ? LIMIT 1",
                (dashboard_id,),
            ).fetchone()
            saved = conn.execute(
                "SELECT id FROM saved_views WHERE id = ? LIMIT 1",
                (saved_view_id,),
            ).fetchone()
            if dashboard is None or saved is None:
                return False

            position_row = conn.execute(
                """
                SELECT COALESCE(MAX(position), 0) + 1 AS next_position
                FROM dashboard_items
                WHERE dashboard_id = ?
                """,
                (dashboard_id,),
            ).fetchone()
            next_position = int(position_row["next_position"] if position_row else 1)

            conn.execute(
                """
                INSERT INTO dashboard_items(dashboard_id, saved_view_id, position)
                VALUES (?, ?, ?)
                ON CONFLICT(dashboard_id, saved_view_id) DO UPDATE SET
                    position = excluded.position
                """,
                (dashboard_id, saved_view_id, next_position),
            )
            conn.execute(
                "UPDATE dashboards SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (dashboard_id,),
            )
            conn.commit()
        return True

    def list_dashboard_items(self, dashboard_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    di.id AS dashboard_item_id,
                    di.position AS dashboard_position,
                    sv.id AS saved_view_id,
                    sv.title,
                    sv.metrics_csv,
                    sv.window_amount,
                    sv.window_unit,
                    sv.step_amount,
                    sv.step_unit,
                    sv.compare_enabled,
                    sv.label_filters_json,
                    sv.query_string,
                    sv.updated_at
                FROM dashboard_items di
                JOIN saved_views sv ON sv.id = di.saved_view_id
                WHERE di.dashboard_id = ?
                ORDER BY di.position ASC, di.id ASC
                """,
                (dashboard_id,),
            ).fetchall()
        entries: list[dict[str, Any]] = []
        for row in rows:
            metrics = [
                chunk.strip() for chunk in str(row["metrics_csv"]).split(",") if chunk.strip()
            ]
            label_filters = _decode_label_filters_json(str(row["label_filters_json"]))
            entries.append(
                {
                    "dashboard_item_id": int(row["dashboard_item_id"]),
                    "dashboard_position": int(row["dashboard_position"]),
                    "saved_view_id": int(row["saved_view_id"]),
                    "title": row["title"],
                    "metrics": metrics,
                    "metrics_csv": row["metrics_csv"],
                    "window_amount": int(row["window_amount"]),
                    "window_unit": row["window_unit"],
                    "step_amount": int(row["step_amount"]),
                    "step_unit": row["step_unit"],
                    "compare_enabled": bool(row["compare_enabled"]),
                    "label_filters": label_filters,
                    "query_string": row["query_string"],
                    "updated_at": row["updated_at"],
                }
            )
        return entries

    def reorder_dashboard_items(self, dashboard_id: int, ordered_item_ids: list[int]) -> bool:
        if not ordered_item_ids:
            return False

        with self._connect() as conn:
            existing_rows = conn.execute(
                "SELECT id FROM dashboard_items WHERE dashboard_id = ?",
                (dashboard_id,),
            ).fetchall()
            existing_ids = {int(row["id"]) for row in existing_rows}
            provided_ids = set(ordered_item_ids)
            if existing_ids != provided_ids:
                return False

            for index, item_id in enumerate(ordered_item_ids, start=1):
                conn.execute(
                    """
                    UPDATE dashboard_items
                    SET position = ?
                    WHERE dashboard_id = ? AND id = ?
                    """,
                    (index, dashboard_id, item_id),
                )
            conn.execute(
                "UPDATE dashboards SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (dashboard_id,),
            )
            conn.commit()
        return True


def _decode_label_filters_json(raw_filters: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_filters)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    cleaned: dict[str, Any] = {}
    shared: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, str):
            if value:
                shared[key] = value
            continue
        if not isinstance(value, dict):
            continue
        metric_filters = {
            str(label_name): str(label_value)
            for label_name, label_value in value.items()
            if isinstance(label_name, str)
            and label_name
            and isinstance(label_value, str)
            and label_value
        }
        if metric_filters:
            cleaned[key] = metric_filters
    if cleaned:
        if shared:
            cleaned["*"] = shared
        return cleaned
    return shared
