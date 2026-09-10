# Browser smoke tests

Two scripts drive the built console in a real Chromium against a running ROE
backend.

`console.smoke.mjs` drives the built dispatcher console in a real Chromium
against a running ROE backend. It covers what jsdom cannot: the MapLibre canvas
mounting, route selection and highlighting, the stop detail panel, the alert
panel, manual order entry (including client-side validation), and the upload
drawer.

`a11y.smoke.mjs` checks the keyboard and screen-reader path: the skip link, a
visible focus ring, tablist semantics and arrow-key navigation, labelled form
controls, announced validation errors, and accessible names on icon-only
buttons.

```bash
# 1. backend
cd backend && .venv/bin/uvicorn app.main:app --port 8099

# 2. seed and optimise, so there are routes for the console to show
cd backend
.venv/bin/python scripts/seed_demo.py --orders 60 --vehicles 8
.venv/bin/python scripts/journey_check.py     # or POST /api/v1/optimise

# 3. build the frontend against that API and run the smoke test
cd frontend
VITE_API_BASE_URL=http://127.0.0.1:8099 npm run build
npm run test:e2e     # console smoke test
npm run test:a11y    # keyboard and screen-reader checks
```

Both scripts need a Chromium. If Playwright's own build is not downloaded, point
them at a pre-installed one:

```bash
CHROMIUM_PATH=/path/to/chromium npm run test:e2e
```

Environment:

| Variable | Default | Purpose |
|---|---|---|
| `API_BASE` | `http://127.0.0.1:8099` | Backend under test |
| `CHROMIUM_PATH` | Playwright's bundled build | Use a pre-installed Chromium |
| `SCREENSHOT` | `e2e/console.png` | Where the console screenshot is written |

The backend's `CORS_ORIGINS` must include `http://127.0.0.1:4173`, the port
`vite preview` serves the build on.
