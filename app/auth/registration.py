from __future__ import annotations

import datetime

from flask import Flask

from app.auth.config import SESSION_LIFETIME_DAYS, AuthConfig
from app.auth.gate import register_auth_gate
from app.auth.github import AuthlibGitHubClient, GitHubClient, build_oauth
from app.auth.routes import register_auth_routes


def register_auth(
    app: Flask,
    auth_config: AuthConfig,
    github_client: GitHubClient | None = None,
) -> None:
    app.permanent_session_lifetime = datetime.timedelta(days=SESSION_LIFETIME_DAYS)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config.setdefault("SESSION_COOKIE_SECURE", False)

    if github_client is None:
        oauth = build_oauth(app, auth_config.client_id, auth_config.client_secret)
        github_client = AuthlibGitHubClient(oauth)
    app.config["github_client"] = github_client

    register_auth_routes(app)
    register_auth_gate(app)
