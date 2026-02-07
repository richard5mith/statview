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
- Docker (`Dockerfile.dev` and `Dockerfile`)

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

## Test and lint

```bash
uv run ruff check .
uv run pytest
```

## Docker Compose

Create your env file:

```bash
cp .env.example .env
```

Development:

```bash
docker compose -f docker-compose.dev.yml up --build
```

After the first build, code is bind-mounted in dev so most changes do not need rebuilds:

```bash
docker compose -f docker-compose.dev.yml up
```

Production:

```bash
docker compose -f docker-compose.prod.yml up --build -d
```
