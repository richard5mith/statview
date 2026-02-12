from __future__ import annotations

import atexit

from flask import Flask

import app.models  # noqa: F401
from app.config import Settings
from app.db import migrate_database
from app.extensions import db, migrate
from app.prometheus import PrometheusClient
from app.routes import register_routes
from app.saved_views import SavedViewStore


def create_app(
    settings: Settings | None = None,
    prometheus_client: PrometheusClient | None = None,
    run_migrations: bool = True,
) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False

    resolved_settings = settings or Settings()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{resolved_settings.saved_db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    migrate.init_app(app, db, directory="alembic")

    if run_migrations:
        migrate_database(resolved_settings.saved_db_path)

    client = prometheus_client or PrometheusClient(resolved_settings.prometheus_url)
    saved_store = SavedViewStore(resolved_settings.saved_db_path)

    app.config["settings"] = resolved_settings
    app.config["prometheus_client"] = client
    app.config["saved_store"] = saved_store

    if prometheus_client is None:
        atexit.register(client.close)

    register_routes(app)
    return app


app = create_app()
