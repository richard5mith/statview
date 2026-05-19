from __future__ import annotations

from flask import Flask, render_template


def register_config_error_handler(app: Flask, message: str) -> None:
    """Make every URL (including /healthz) return 500 with `message`.

    Used when load_auth_config() rejects the environment. The container stays
    up so the operator sees the exact problem in the browser instead of
    digging through `docker logs`.
    """

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def _config_error(path: str) -> tuple[str, int]:
        del path
        return render_template("config_error.html", message=message), 500
