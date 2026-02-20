# Template API

FastAPI template with browser automation, task workers, and monitoring stack.

## Setup

```bash
uv sync
```

## Run

### Local app

```bash
uv run app
```

### Docker Compose

```bash
docker compose up --build -d
```

## Browser Automation

`BrowserPool` supports autoscaling and context pooling:

- default browser limit: `APP__PLAYWRIGHT__MAX_BROWSERS=2`
- default contexts per browser: `APP__PLAYWRIGHT__CONTEXTS_PER_BROWSER=5`
- max parallel browser contexts by default: `2 * 5 = 10`

You can tune this via `.env`:

- `APP__PLAYWRIGHT__HEADLESS`
- `APP__PLAYWRIGHT__MAX_BROWSERS`
- `APP__PLAYWRIGHT__CONTEXTS_PER_BROWSER`

## Browser Emulation Service

Compose service `emulation` runs Playwright worker with Xvfb + VNC + noVNC.

- VNC: `localhost:5901`
- noVNC: `http://localhost:6081/vnc.html`

## CLI

### Create migration

```bash
uv run cli migration
```

### Upgrade database with Alembic

```bash
uv run cli upgrade
```

## Pre-commit

```bash
uv sync --group dev
pre-commit install
pre-commit run --all-files
```

## Monitoring

- Grafana: `http://localhost:3000` (`admin/admin`)
- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
