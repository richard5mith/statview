from __future__ import annotations

from sqlalchemy import Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.label_filters import LabelFilters


class SavedView(db.Model):
    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_csv: Mapped[str] = mapped_column(Text, nullable=False)
    window_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    window_unit: Mapped[str] = mapped_column(Text, nullable=False)
    step_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    step_unit: Mapped[str] = mapped_column(Text, nullable=False)
    compare_enabled: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    label_filters_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'{}'"),
    )
    query_string: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    @property
    def metrics(self) -> list[str]:
        return [chunk.strip() for chunk in self.metrics_csv.split(",") if chunk.strip()]

    @property
    def label_filters(self) -> LabelFilters:
        return LabelFilters.parse(self.label_filters_json)


class Dashboard(db.Model):
    __tablename__ = "dashboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    items: Mapped[list[DashboardItem]] = relationship(
        back_populates="dashboard",
        cascade="all, delete-orphan",
        order_by="DashboardItem.position, DashboardItem.id",
    )


class DashboardItem(db.Model):
    __tablename__ = "dashboard_items"
    __table_args__ = (db.UniqueConstraint("dashboard_id", "saved_view_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dashboard_id: Mapped[int] = mapped_column(
        db.ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
    )
    saved_view_id: Mapped[int] = mapped_column(
        db.ForeignKey("saved_views.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    dashboard: Mapped[Dashboard] = relationship(back_populates="items")
    saved_view: Mapped[SavedView] = relationship()
