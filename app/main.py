from __future__ import annotations

import atexit
import os

from flask import Flask

import app.models  # noqa: F401
from app.auth.config import ConfigError, load_auth_config
from app.auth.errors import register_config_error_handler
from app.auth.github import GitHubClient
from app.auth.registration import register_auth
from app.config import Settings
from app.db import migrate_database
from app.extensions import db, migrate
from app.prometheus import PrometheusClient
from app.routes import register_routes


def create_app(
    settings: Settings | None = None,
    prometheus_client: PrometheusClient | None = None,
    github_client: GitHubClient | None = None,
    run_migrations: bool = True,
) -> Flask:
    if settings is None and os.getenv("STATVIEW_MODE") == "dev":
        from dotenv import load_dotenv

        load_dotenv(override=True)

    app = Flask(__name__)
    app.json.sort_keys = False

    try:
        auth_config = load_auth_config()
    except ConfigError as exc:
        register_config_error_handler(app, str(exc))
        return app

    app.config["SECRET_KEY"] = auth_config.secret_key

    resolved_settings = settings or Settings()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{resolved_settings.saved_db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    migrate.init_app(app, db, directory="alembic")

    if run_migrations:
        migrate_database(resolved_settings.saved_db_path)

    client = prometheus_client or PrometheusClient(
        resolved_settings.prometheus_url,
        username=resolved_settings.prometheus_username,
        password=resolved_settings.prometheus_password,
    )

    app.config["settings"] = resolved_settings
    app.config["prometheus_client"] = client
    app.config["auth_config"] = auth_config

    if prometheus_client is None:
        atexit.register(client.close)

    if auth_config.enabled:
        register_auth(app, auth_config, github_client)

    register_routes(app)
    return app
