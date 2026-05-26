from __future__ import annotations

import logging

from flask import (
    Flask,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.auth.config import AuthConfig
from app.auth.github import GitHubClient, GitHubUnreachableError
from app.auth.policy import is_allowed

EXEMPT_PREFIXES = ("/static/",)
EXEMPT_PATHS = frozenset(
    {
        "/healthz",
        "/login",
        "/auth/start",
        "/auth/callback",
        "/logout",
        "/favicon.ico",
    }
)
log = logging.getLogger(__name__)


def _is_exempt(path: str) -> bool:
    if path in EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def register_auth_gate(app: Flask) -> None:
    @app.before_request
    def _gate():
        path = request.path
        if _is_exempt(path):
            return None

        login = session.get("github_login")
        token = session.get("github_token")
        if not login or not token:
            # setdefault, not assignment: a browser loading "/" also fires
            # parallel subresource requests (favicon, etc.) which all get gated.
            # Whichever lands last would otherwise overwrite the user's intended
            # destination, so the first gated path wins.
            session.setdefault(
                "next",
                request.full_path.rstrip("?") if request.query_string else path,
            )
            return redirect(url_for("auth.login_page"), code=302)

        cfg: AuthConfig = current_app.config["auth_config"]
        client: GitHubClient = current_app.config["github_client"]

        try:
            allowed = is_allowed(login, token, cfg, client)
        except GitHubUnreachableError as exc:
            log.warning("Org membership check unavailable for login=%s: %s", login, exc)
            return render_template(
                "auth_unavailable.html",
                detail=str(exc)[:200],
            ), 503

        if allowed:
            return None

        log.warning("Mid-session denial: login=%s no longer authorised", login)
        return _revoke()


def _revoke():
    session.pop("github_login", None)
    session.pop("github_token", None)
    return redirect(url_for("auth.login_page", reason="revoked"), code=302)
