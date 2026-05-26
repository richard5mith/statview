from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Dashboard, DashboardItem, SavedView


def list_dashboards() -> list[tuple[Dashboard, int]]:
    stmt = (
        select(Dashboard, func.count(DashboardItem.id))
        .outerjoin(DashboardItem, DashboardItem.dashboard_id == Dashboard.id)
        .group_by(Dashboard.id)
        .order_by(func.lower(Dashboard.name).asc())
    )
    rows = db.session.execute(stmt).all()
    return [(dashboard, int(item_count or 0)) for dashboard, item_count in rows]


def get_dashboard(dashboard_id: int) -> Dashboard | None:
    return db.session.get(Dashboard, dashboard_id)


def create_dashboard(name: str) -> Dashboard:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("dashboard name is required")
    dashboard = Dashboard(name=cleaned)
    db.session.add(dashboard)
    db.session.commit()
    db.session.refresh(dashboard)
    return dashboard


def add_saved_view_to_dashboard(dashboard_id: int, saved_view_id: int) -> bool:
    dashboard = db.session.get(Dashboard, dashboard_id)
    saved = db.session.get(SavedView, saved_view_id)
    if dashboard is None or saved is None:
        return False

    next_position = db.session.scalar(
        select(func.coalesce(func.max(DashboardItem.position), 0) + 1).where(
            DashboardItem.dashboard_id == dashboard_id
        )
    )

    existing = db.session.scalar(
        select(DashboardItem).where(
            DashboardItem.dashboard_id == dashboard_id,
            DashboardItem.saved_view_id == saved_view_id,
        )
    )
    if existing is None:
        db.session.add(
            DashboardItem(
                dashboard_id=dashboard_id,
                saved_view_id=saved_view_id,
                position=int(next_position),
            )
        )
    else:
        existing.position = int(next_position)

    db.session.execute(
        update(Dashboard)
        .where(Dashboard.id == dashboard_id)
        .values(updated_at=func.current_timestamp())
    )
    db.session.commit()
    return True


def list_dashboard_items(dashboard_id: int) -> list[DashboardItem]:
    stmt = (
        select(DashboardItem)
        .where(DashboardItem.dashboard_id == dashboard_id)
        .order_by(DashboardItem.position.asc(), DashboardItem.id.asc())
        .options(selectinload(DashboardItem.saved_view))
    )
    return list(db.session.scalars(stmt))


def reorder_dashboard_items(dashboard_id: int, ordered_item_ids: list[int]) -> bool:
    if not ordered_item_ids:
        return False

    existing_ids = set(
        db.session.scalars(
            select(DashboardItem.id).where(DashboardItem.dashboard_id == dashboard_id)
        )
    )
    if existing_ids != set(ordered_item_ids):
        return False

    by_id = {
        item.id: item
        for item in db.session.scalars(
            select(DashboardItem).where(DashboardItem.dashboard_id == dashboard_id)
        )
    }
    for position, item_id in enumerate(ordered_item_ids, start=1):
        by_id[item_id].position = position

    db.session.execute(
        update(Dashboard)
        .where(Dashboard.id == dashboard_id)
        .values(updated_at=func.current_timestamp())
    )
    db.session.commit()
    return True
