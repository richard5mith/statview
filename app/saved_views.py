from __future__ import annotations

from sqlalchemy import or_, select

from app.extensions import db
from app.label_filters import LabelFilters
from app.models import SavedView


def list_saved_views(search: str = "") -> list[SavedView]:
    stmt = select(SavedView)
    if search:
        wildcard = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                db.func.lower(SavedView.title).like(wildcard),
                db.func.lower(SavedView.metrics_csv).like(wildcard),
            )
        )
    stmt = stmt.order_by(SavedView.updated_at.desc(), SavedView.id.desc())
    return list(db.session.scalars(stmt))


def save_saved_view(
    *,
    saved_view_id: int | None = None,
    title: str,
    metrics_csv: str,
    window_amount: int,
    window_unit: str,
    step_amount: int,
    step_unit: str,
    compare_enabled: bool,
    label_filters: LabelFilters,
    query_string: str,
    force_create: bool = False,
) -> tuple[SavedView, bool]:
    label_filters_json = label_filters.to_json()

    existing: SavedView | None = None
    if saved_view_id is not None:
        existing = db.session.get(SavedView, saved_view_id)
    if existing is None and not force_create:
        existing = db.session.scalar(
            select(SavedView)
            .where(SavedView.query_string == query_string)
            .order_by(SavedView.id.asc())
            .limit(1)
        )

    created = existing is None
    if existing is None:
        existing = SavedView(
            title=title,
            metrics_csv=metrics_csv,
            window_amount=window_amount,
            window_unit=window_unit,
            step_amount=step_amount,
            step_unit=step_unit,
            compare_enabled=int(compare_enabled),
            label_filters_json=label_filters_json,
            query_string=query_string,
        )
        db.session.add(existing)
    else:
        existing.title = title
        existing.metrics_csv = metrics_csv
        existing.window_amount = window_amount
        existing.window_unit = window_unit
        existing.step_amount = step_amount
        existing.step_unit = step_unit
        existing.compare_enabled = int(compare_enabled)
        existing.label_filters_json = label_filters_json
        existing.query_string = query_string
        existing.updated_at = db.func.current_timestamp()

    db.session.commit()
    db.session.refresh(existing)
    return existing, created


def get_saved_view(saved_view_id: int) -> SavedView | None:
    return db.session.get(SavedView, saved_view_id)


def remove_saved_view(saved_view_id: int) -> bool:
    view = db.session.get(SavedView, saved_view_id)
    if view is None:
        return False
    db.session.delete(view)
    db.session.commit()
    return True


def rename_saved_view(saved_view_id: int, title: str) -> SavedView | None:
    cleaned = title.strip()
    if not cleaned:
        return None
    view = db.session.get(SavedView, saved_view_id)
    if view is None:
        return None
    view.title = cleaned
    view.updated_at = db.func.current_timestamp()
    db.session.commit()
    db.session.refresh(view)
    return view
