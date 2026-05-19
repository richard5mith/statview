# StatView

StatView is a Flask + HTMX frontend for Prometheus metrics.

## Why?

I wanted to browse through my metrics, see the latest info, see a comparison of other time periods, and easily send the URL of a specific metric to colleagues.

I don't want to create dashboards in advance based on guessing what I might want to see. And Grafana makes it really hard to even quickly change a graph from one stat to another.

I also loved StatHat, RIP.

## Security model

**StatView has no built-in authentication or authorization.** Anyone who can reach the listening port can read every metric, create/rename/delete saved views, and modify dashboards. Deploy it only behind a trusted boundary — a reverse proxy with auth (oauth2-proxy, Cloudflare Access, Tailscale, basic-auth via nginx, etc.), a VPN, or a private network segment.

## Run from the published image (recommended)

The image is published to GitHub Container Registry:

```bash
docker pull ghcr.io/richard5mith/statview:latest
```

Quick start with `docker run`:

```bash
docker run --rm \
  -p 8000:8000 \
  -e PROMETHEUS_URL=http://your-prometheus:9090 \
  -v statview-data:/app/data \
  ghcr.io/richard5mith/statview:latest
```

Or with `docker compose` — see [docker-compose.ghcr.yml](docker-compose.ghcr.yml) for a published-image example:

```bash
cp .env.example .env  # set PROMETHEUS_URL
docker compose -f docker-compose.ghcr.yml up -d
```

Available tags: `latest` (most recent push to `main`), `v<semver>` for tagged releases, `sha-<short>` for any specific commit.

## Deploy on Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/template/REPLACE_ME)

The button above deploys StatView from this repo using the included [`railway.toml`](railway.toml) (Dockerfile build, `/healthz` healthcheck, restart-on-failure). You will need to point it at your own Prometheus.

## How does Prometheus support this?

Prometheus stores time-series samples keyed by metric name and labels. This app uses:

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

## Docker Compose (local build)

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

> **Linux users:** the container runs as UID 1000 by default. If your host user has a different UID, set `STATVIEW_UID` and `STATVIEW_GID` in `.env` (typically `STATVIEW_UID=$(id -u)`, `STATVIEW_GID=$(id -g)`) so the bind-mounted `./data` directory is writable from inside the container.

Production (local build, not using the published image):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

## License

[MIT](LICENSE).
