from __future__ import annotations

import logging
from typing import Any

from flask import (
    Blueprint,
    Flask,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.auth.config import AuthConfig
from app.auth.github import GitHubAuthError, GitHubClient

MAX_ERROR_DETAIL = 200
log = logging.getLogger(__name__)


def _build_blueprint() -> Blueprint:
    bp = Blueprint("auth", __name__)

    @bp.get("/login")
    def login_page() -> tuple[str, int] | str:
        reason = request.args.get("reason", "")
        return render_template("login.html", reason=reason)

    @bp.get("/auth/start")
    def start():
        cfg: AuthConfig = current_app.config["auth_config"]
        client: GitHubClient = current_app.config["github_client"]
        redirect_uri = cfg.oauth_redirect_url or url_for("auth.callback", _external=True)
        return client.start_oauth(redirect_uri)

    @bp.get("/auth/callback")
    def callback():
        cfg: AuthConfig = current_app.config["auth_config"]
        client: GitHubClient = current_app.config["github_client"]

        try:
            login, token = client.complete_oauth()
        except GitHubAuthError as exc:
            log.warning("OAuth callback failed: %s", exc)
            detail = str(exc)[:MAX_ERROR_DETAIL]
            return render_template(
                "auth_forbidden.html",
                headline="Sign-in failed.",
                detail=detail,
            ), 403

        if not _is_allowed(login, token, cfg, client):
            log.warning("OAuth allowlist denial for login=%s", login)
            return render_template(
                "auth_forbidden.html",
                headline=f"{login} isn't authorized for this StatView instance.",
                detail=None,
            ), 403

        session["github_login"] = login
        session["github_token"] = token
        next_url = session.pop("next", None) or "/"
        return redirect(next_url, code=302)

    @bp.post("/logout")
    def logout():
        session.pop("github_login", None)
        session.pop("github_token", None)
        session.pop("next", None)
        return redirect(url_for("auth.login_page"), code=302)

    return bp


def _is_allowed(
    login: str,
    token: dict[str, Any],
    cfg: AuthConfig,
    client: GitHubClient,
) -> bool:
    if login.lower() in cfg.allowed_users:
        return True
    if cfg.allowed_org is None:
        return False
    try:
        return client.is_org_member(login, cfg.allowed_org, token)
    except Exception as exc:
        log.warning("Org membership check failed during callback: %s", exc)
        return False


def register_auth_routes(app: Flask) -> None:
    app.register_blueprint(_build_blueprint())
