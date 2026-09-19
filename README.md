# Route Optimisation Engine (ROE)

A logistics dispatch system that imports delivery orders and fleet vehicles,
assigns orders to vehicles with a capacity- and time-window-aware VRP solver,
and puts a dispatcher in control of the resulting plan through an interactive
map console.

Built to the Kiro specification in `.kiro/specs/route-optimisation-engine/`.

---

## What it does

- **Imports orders from an OMS and vehicles from an FMS**, with validation,
  deduplication, and connectivity warnings when either is unreachable.
- **Manual entry and spreadsheet upload** as first-class fallbacks — CSV/XLSX
  up to 50 MB and 10,000 rows, with row-level error reporting and downloadable
  templates.
- **Optimises routes** with Google OR-Tools CP-SAT: hard weight and volume
  capacity, hard delivery time windows, vehicle operating hours, and
  priority-before-standard stop ordering. 500 orders across 50 vehicles solve
  in well under the 120-second budget.
- **Keeps the dispatcher in charge** — drag-and-drop reassignment, route
  locking, one-click re-optimisation that preserves locked routes, and a diff
  of what changed.
- **Traffic-aware ETAs** that recalculate downstream and raise late-delivery
  alerts, falling back to historical averages when the mapping service is down.
- **Exports approved routes** to the delivery platform with exponential
  back-off retry and manual re-trigger.
- **Alerts, audit trail, and RBAC** — overload, late-delivery, impossible-order
  and export-failure alerts; an immutable 365-day audit log; dispatcher and
  administrator roles.

---

## Quick start

### Docker Compose (everything at once)

```bash
cp backend/.env.example backend/.env
docker compose up --build

# in another shell — load a demo day's work
docker compose exec api python scripts/seed_demo.py --orders 60 --vehicles 8
```

- Dispatcher console: <http://localhost:5173>
- API docs: <http://localhost:8000/docs>

Sign in with `dispatcher@roe.app` / `dispatch12345`, or
`admin@roe.app` / `admin12345` for the administrator view (audit log, user
management, integration webhooks).

### Running locally without Docker

```bash
# --- backend ---
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env                      # adjust DATABASE_URL / REDIS_URL
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_demo.py     # optional demo data
.venv/bin/uvicorn app.main:app --reload --port 8000

# --- frontend ---
cd frontend
npm install
npm run dev                               # proxies /api to localhost:8000
```

PostgreSQL 15+ and Redis 7 are the only external dependencies. Redis is
optional in development — the event bus falls back to an in-process bus, so a
single API instance still delivers live updates.

---

## Architecture

```
                     ┌──────────────────────────────┐
   OMS ──┐           │  Dispatcher console (React)  │
   FMS ──┤ adapters  │  MapLibre · Zustand · Query  │
         │           └──────────────┬───────────────┘
         │                REST + WebSocket
         ▼                          ▼
   ┌──────────────────────────────────────────────┐
   │        API gateway / BFF (FastAPI)           │
   │  JWT auth · RBAC · validation · error shape  │
   └──┬────────────┬──────────────┬───────────┬───┘
      │            │              │           │
   Route      Optimisation     Alert /     Upload /
   Service     Engine          Audit       Manual entry
      │       (OR-Tools         │              │
      │        CP-SAT)          │              │
      └────────────┴────────────┴──────────────┘
                   │                    │
            PostgreSQL 15          Redis 7
         (orders, vehicles,     (pub/sub for live
          routes, stops,         updates, FMS
          alerts, audit log)     last-known cache)
                   │
          Mapping Service ──── Delivery Platform
             (geocode +          (route export)
              travel matrix)
```

Celery workers run the queue-based optimisation path plus scheduled retention,
geocoding-retry, and ETA-refresh jobs. In local development the same jobs run
as in-process background tasks so nothing extra is needed.

### The solver

A two-phase CP-SAT decomposition (see `backend/app/services/optimisation_engine.py`):

1. **Assignment** — a CP-SAT model assigns *delivery points* (co-located orders
   grouped up front) to vehicles under hard weight/volume capacity and a coarse
   shift-time budget, minimising a radial + centroid distance cost with a
   dominating penalty for leaving work unassigned. Two medoid-refinement rounds
   tighten the clusters.
2. **Sequencing** — for each vehicle, an exact CP-SAT `AddCircuit` TSP with time
   windows, minimising `1000 × distance + 1 × time` so distance is the primary
   objective and travel time the tie-breaker. Priority-before-standard ordering
   is a *hard* constraint, relaxed to a penalised soft constraint only when the
   hard model is infeasible (the route is then flagged `priority_relaxed`).

If a cluster cannot be sequenced at all, the hardest order is dropped and
re-solved; dropped orders become Impossible Order Alerts rather than stops
scheduled outside their window. Every produced route is re-verified against the
hard constraints before it is persisted.

Measured on the reference dataset of 500 orders across 50 vehicles, with zero
capacity or time-window violations in the result:

| Measurement | Time |
|---|---|
| Solver alone (matrix + both CP-SAT phases, in-process) | **18.5 s** |
| `POST /api/v1/optimise`, end to end over HTTP | **69.0 s** |

Requirement 18.2 allows 120 s. The end-to-end figure is the one that matters for
that requirement; the gap over the solver figure is the 51×51 travel matrix being
persisted, 50 routes and 500 stops being written, and the audit trail.

---

## Integrations: live vs. local

Every integration ships with a **local mock adapter that requires no
credentials**, and an **HTTP adapter** for the real system. Switch with the
`*_ADAPTER` environment variable.

| Integration | Mock (default) | HTTP adapter | Configure |
|---|---|---|---|
| **OMS** (orders in) | In-memory queue, drained by the poller; also fed by `POST /api/v1/system/integrations/oms/events` | Polls `GET {OMS_BASE_URL}/orders/events` with a cursor | `OMS_ADAPTER=http`, `OMS_BASE_URL`, `OMS_API_KEY` |
| **FMS** (vehicles in) | In-memory queue + the same webhook shape | Polls `GET {FMS_BASE_URL}/vehicles/events` | `FMS_ADAPTER=http`, `FMS_BASE_URL`, `FMS_API_KEY` |
| **Mapping** (geocode + matrix) | Deterministic: a Singapore gazetteer plus a stable hash fallback; travel times from great-circle distance × road factor × time-of-day traffic | Valhalla-compatible `GET /geocode`, `POST /matrix` | `MAPPING_ADAPTER=http`, `MAPPING_BASE_URL`, `MAPPING_API_KEY` |
| **Delivery Platform** (routes out) | Records exports in memory and acknowledges | `POST {DELIVERY_PLATFORM_BASE_URL}/routes` | `DELIVERY_PLATFORM_ADAPTER=http`, `DELIVERY_PLATFORM_BASE_URL`, `DELIVERY_PLATFORM_API_KEY` |
| **Identity** | Local user store with bcrypt passwords, ROE-issued JWTs | External IdP via RS256 | `JWT_ALGORITHM=RS256`, `JWT_PUBLIC_KEY` |
| **Map tiles** | Self-contained offline basemap (routes/stops/depots always render) | Any raster or vector tile source | `VITE_MAP_TILE_URL` or `VITE_MAP_STYLE_URL` |

**Nothing in the default configuration talks to a third party.** The mock
adapters are deterministic, which is also what makes the property tests
reproducible.

---

## Testing

```bash
# backend
cd backend
.venv/bin/ruff check app tests           # lint
.venv/bin/pytest tests/unit -q           # unit
.venv/bin/pytest tests/integration -q    # API + database
.venv/bin/pytest tests/property -q       # Hypothesis, 100 examples each
.venv/bin/pytest -q                      # everything

# frontend
cd frontend
npm run lint
npm run build            # tsc -b + vite build
npm test                 # vitest (includes fast-check property tests)

# live end-to-end checks against a running API
cd backend
.venv/bin/python scripts/journey_check.py     # 57 API journey checks
.venv/bin/python scripts/ws_check.py          # WebSocket event delivery
.venv/bin/python scripts/resilience_check.py  # degraded-integration fallbacks

# built UI in a real browser (backend must be running on :8099)
cd frontend
VITE_API_BASE_URL=http://127.0.0.1:8099 npm run build
npm run test:e2e     # console smoke test in Chromium
npm run test:a11y    # keyboard and screen-reader checks
```

The Python suites share one PostgreSQL database and truncate between tests, so
run them in a single pytest process — two concurrent runs deadlock on the
truncation lock. `DATABASE_URL` should point at a scratch database (`roe_test`
by default), not your development one.

All 36 correctness properties from the design document are covered by
Hypothesis (backend) or fast-check (frontend) property tests — see
[`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) for the property-to-test map.

---

## Repository layout

```
backend/
  app/
    adapters/     OMS, FMS, Mapping, Delivery Platform (mock + HTTP)
    api/v1/       REST routers, WebSocket hub, auth dependencies
    core/         config, security, errors, events, logging
    db/           engine/session, custom SQLAlchemy types
    models/       SQLAlchemy ORM mirroring the design schema
    schemas/      Pydantic request/response models
    services/     route, optimisation, alert, audit, geocoding, upload, export
    workers/      Celery app, background loops, retention jobs
  alembic/        migrations (schema + audit immutability)
  scripts/        seed_demo.py, journey_check.py
  tests/          unit, integration, property
frontend/
  src/
    components/   map, panels, forms, ui primitives, layout
    hooks/        React Query + WebSocket wiring
    lib/          API client, socket, geojson, utils, validation
    pages/        login, dispatcher console
    store/        Zustand store
deploy/k8s/       production manifests
docs/             traceability and design decisions
```

---

## Documentation

- [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) — requirement and task coverage,
  property-test map
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — design decisions, assumptions, and
  deviations from the specification
- [`deploy/k8s/README.md`](deploy/k8s/README.md) — production deployment
