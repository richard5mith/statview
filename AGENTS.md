This is a stat viewer with a Prometheus back-end.

Follow best-practice for app structure. Follow modern Python constraints. Use Typing.
Use documented local command wrappers in `tools/` (see README Local tools section).

There's an sqlite database for storing settings.

* Use uv for package management.
* Don't install the app as a package.
* Flask, sqlalchemy, Alembic, HTMX, Flask-Migrate.
* Use ruff to check code quality, typing constraints and check for code complexity.
* Split code into proper function modules
* Use Alembic for migrations. Let it auto-generate migrations, do not write your own.
* Run migrations at startup in prod via an entrypoint script
* A configurable single location for app data, including the sqlite table
* Separate front-end view code from back-end function code
* Prefer `tools/check.sh`, `tools/test.sh`, `tools/db.sh`, and `tools/run-dev.sh` for local workflows.
* No in-line Javascript. Put it in files and include.
* No in-line CSS. Put it in static files and include.
* Tests that can run with their own test database file.
* Github workflows to create a Docker image and upload to Github package source.
* The app will always be run inside Docker, never on a host. Docker only deployment model.
* Vendor dependencies
* Use red/green TDD
* Tests should test behavior, not implementation
