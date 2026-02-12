# StatView

StatView is a Flask + HTMX frontend for Prometheus metrics.

## Can Prometheus support this?

Yes. Prometheus stores time-series samples keyed by metric name and labels. This app uses:
- `/api/v1/label/__name__/values` to list all metric names.
- `/api/v1/query_range` to fetch graph data for chosen time windows.

That directly supports:
- Metric browsing.
- Click-to-graph behavior.
- Window/step selection.
- Live updates (periodic refresh).
- Comparison between two selected timeframes.
- A six-panel standard timeframe view.

## Stack

- Python 3.12
- Flask
- HTMX
- Chart.js
- `uv` for dependency and run workflows
- `ruff` for linting
- `pytest` + `pytest-cov` with `--cov-fail-under=85`
- Docker (single `Dockerfile`, compose overrides for dev/prod runtime mode)

## Local development

```bash
uv sync --group dev
uv run flask --app app.main:create_app run --host 0.0.0.0 --port 8000 --debug
```

Set Prometheus URL:

```bash
export PROMETHEUS_URL=http://your-prometheus:9090
```

Optional live refresh interval:

```bash
export LIVE_REFRESH_SECONDS=15
```

Run database migrations:

```bash
uv run alembic upgrade head
```

## Local tools

Use the scripts in `tools/` for common local workflows:

```bash
# Open the app sqlite database in sqlite3 (auto-runs migrations if missing)
tools/db.sh

# Run tests
tools/test.sh

# Run ruff linting/format checks
tools/check.sh

# Build and start the local dev container
tools/run-dev.sh
```

## Test and lint

```bash
tools/check.sh
tools/test.sh
```

## Docker Compose

Create your env file:

```bash
cp .env.example .env
```

Default local `docker compose` uses `docker-compose.yml` + `docker-compose.override.yml`:
- Starts in dev mode (`flask --debug`)
- Runs migrations on startup
- Uses `/app/data` for persistent sqlite data inside the container
- Bind mounts `./app`, `./alembic`, and `./data`

Development:

```bash
docker compose up --build
```

After the first build, code is bind-mounted in dev so most changes do not need rebuilds:

```bash
docker compose up
```

Production:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```
