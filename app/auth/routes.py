from __future__ import annotations

import logging

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
from app.auth.github import GitHubAuthError, GitHubClient, GitHubUnreachableError
from app.auth.policy import is_allowed

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

        try:
            allowed = is_allowed(login, token, cfg, client)
        except GitHubUnreachableError as exc:
            log.warning(
                "Org membership check unavailable during callback for login=%s: %s",
                login,
                exc,
            )
            allowed = False
        except Exception as exc:
            log.exception("Unexpected error during allowlist check for login=%s", login)
            return render_template(
                "auth_forbidden.html",
                headline="Sign-in error.",
                detail=str(exc)[:MAX_ERROR_DETAIL],
            ), 500

        if not allowed:
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


def register_auth_routes(app: Flask) -> None:
    app.register_blueprint(_build_blueprint())
