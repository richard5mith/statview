# StatView

StatView is a Flask + HTMX frontend for Prometheus metrics.

## What?

If you have a bunch of time-series statistics in Prometheus and want an easy way to browse and visualise them, without the hassle of pre-creating dashboards, this is the app for you.

It supports:

- Metric browsing.
- Click-to-graph behavior.
- Window/step selection.
- Live updates (periodic refresh).
- Comparison between two selected timeframes.
- A six-panel standard timeframe view.
- Dashboard creation

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

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/tJw2Dm?referralCode=g99H_9&utm_medium=integration&utm_source=template&utm_campaign=generic)

The button above deploys StatView from this repo using the included [`railway.toml`](railway.toml) (Dockerfile build, `/healthz` healthcheck, restart-on-failure). You will need to point it at your own Prometheus.

## How does Prometheus support this?

Prometheus stores time-series samples keyed by metric name and labels. This app uses:

- `/api/v1/label/__name__/values` to list all metric names.
- `/api/v1/query_range` to fetch graph data for chosen time windows.

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

Local dev runs in Docker — `./app`, `./alembic`, and `./data` are bind-mounted into the container, so editing files on the host triggers Flask's auto-reload inside the container without a rebuild.

First time:

```bash
cp .env.example .env  # set PROMETHEUS_URL
tools/run-dev.sh      # docker compose up --build -d, picks up the dev override
```

After the first build, plain `docker compose up` (no `--build`) is enough. Re-run `tools/run-dev.sh` whenever you change `pyproject.toml`/`uv.lock` or anything else baked into the image.

Open <http://localhost:8000> once it's up. Tail logs with `docker compose logs -f statview`.

> **Linux users:** the container runs as UID 1000 by default. If your host user has a different UID, set `STATVIEW_UID` and `STATVIEW_GID` in `.env` (typically `STATVIEW_UID=$(id -u)`, `STATVIEW_GID=$(id -g)`) so the bind-mounted `./data` directory is writable from inside the container.

### Test, lint, and inspect

Use the wrappers in `tools/`:

```bash
tools/check.sh   # ruff check + ruff format --check
tools/test.sh    # pytest with the 85% coverage gate
tools/db.sh      # open the sqlite store in sqlite3 (auto-migrates if missing)
```

These run via `uv` on the host — you'll need [uv](https://docs.astral.sh/uv/) installed (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`). They do not require the dev container to be running.

### Build the production image locally

If you want to test what the published image actually does (no bind-mounts, gunicorn instead of `flask --debug`):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

### Running directly on the host (not recommended)

If you specifically want to skip Docker — for instance to attach a debugger that does not like containers — you can run the app the way the entrypoint does:

```bash
uv sync --group dev
export PROMETHEUS_URL=http://your-prometheus:9090
uv run alembic upgrade head
uv run flask --app app.main:create_app run --host 0.0.0.0 --port 8000 --debug
```

Note that the deployment target is always Docker, so this path is purely for ergonomic exceptions.

## License

[MIT](LICENSE).
