/**
 * Browser smoke test for the dispatcher console.
 *
 * Builds are served by `vite preview`; API calls are rerouted to a running ROE
 * backend. Verifies the journeys that unit tests cannot: real map mount, route
 * selection, stop detail, alerts, manual entry, and upload.
 *
 *   cd backend && uvicorn app.main:app --port 8099   # in another shell
 *   cd frontend && npm run build && node e2e/console.smoke.mjs
 *
 * Override the API with API_BASE=http://host:port.
 */
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';

// The build under test is served by `vite preview`; VITE_API_BASE_URL points
// it straight at the running backend.
const app = spawn('npx', ['vite', 'preview', '--port', '4173', '--strictPort'], {
  cwd: new URL('..', import.meta.url).pathname,
  env: { ...process.env },
  stdio: 'ignore',
});
await new Promise((r) => setTimeout(r, 4000));

const API_BASE = process.env.API_BASE ?? 'http://127.0.0.1:8099';
const results = [];
const check = (name, ok, detail = '') => {
  results.push([ok, name, detail]);
  console.log(`  ${ok ? '✓' : '✗'} ${name}${detail ? ` — ${detail}` : ''}`);
};

// The container ships a pre-installed Chromium; point at it explicitly so the
// script does not try to download a matching build.
const executablePath = process.env.CHROMIUM_PATH ?? undefined;
const browser = await chromium.launch(executablePath ? { executablePath } : {});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));
// 4xx from the API is often the behaviour under test (a rejected
// reassignment, a validation failure); only 5xx indicates a defect.
page.on('response', (r) => {
  if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`);
});

try {
  await page.goto('http://127.0.0.1:4173/', { waitUntil: 'networkidle' });
  check('console opens straight to the plan (no sign-in step)',
    await page.getByRole('heading', { name: /Route Optimisation Engine/i }).isVisible());

  try {
    await page.waitForSelector('[data-testid="map-canvas"]', { timeout: 20000 });
  } catch (e) {
    console.log('--- page text ---\n' + (await page.locator('body').innerText()).slice(0, 800));
    console.log('--- console errors ---\n' + errors.slice(0, 8).join('\n'));
    throw e;
  }
  check('dispatcher console loads with the map', true);

  await page.waitForTimeout(2500);
  const routeButtons = page.locator('ul li button[aria-pressed]');
  const routeCount = await routeButtons.count();
  check('route summary list is populated', routeCount > 0, `${routeCount} routes`);

  const firstRouteText = await routeButtons.first().innerText();
  check('route entry shows stops, distance and duration',
    /stop/.test(firstRouteText) && /km/.test(firstRouteText) && /min/.test(firstRouteText),
    firstRouteText.split('\n').slice(0, 2).join(' | '));
  check('load utilisation is shown to 1 d.p.', /\d+\.\d%/.test(firstRouteText));

  await routeButtons.first().click();
  await page.waitForTimeout(800);
  check('route detail opens', await page.getByRole('button', { name: /Refresh ETAs/i }).isVisible());
  check('detail shows distance metric', await page.getByTestId('detail-distance').isVisible());
  check('lock control present', await page.getByRole('button', { name: /^Lock$/i }).isVisible());
  check('approve control present', await page.getByRole('button', { name: /Approve/i }).isVisible());

  const stops = page.locator('ol li div[role="button"]');
  const stopCount = await stops.count();
  check('stop list rendered', stopCount > 0, `${stopCount} stops`);
  await stops.first().click();
  await page.waitForTimeout(500);
  check('stop detail panel opens', await page.getByText('Orders at this stop').isVisible());

  // Drag-and-drop reassignment: the drop targets must be reachable while a
  // route's detail is open, otherwise the feature cannot be used at all.
  const targets = page.locator('[data-testid^="reassign-target-"]');
  const targetCount = await targets.count();
  check('other routes are offered as drop targets', targetCount > 0, `${targetCount} targets`);
  if (targetCount > 0) {
    const before = await page.locator('ol li div[role="button"]').count();
    const source = page.locator('ol li div[role="button"]').first();
    await source.dragTo(targets.first());
    await page.waitForTimeout(3000);
    const toastText = await page.locator('[role="status"]').innerText();
    check('dropping a stop reassigns or explains why not',
      /reassigned|capacity|locked|late|window/i.test(toastText),
      toastText.split('\n').slice(0, 2).join(' | '));
    check('stop list reflects the change', (await page.locator('ol li div[role="button"]').count()) !== before
      || /capacity|locked|late|window/i.test(toastText));
  }

  // Alerts tab
  await page.getByRole('tab', { name: /Alerts/i }).click();
  await page.waitForTimeout(600);
  const alertPanel = await page.locator('main').innerText();
  check('alerts panel renders', /alert|Acknowledge|No open alerts/i.test(alertPanel));

  // Data entry drawer
  await page.getByRole('tab', { name: /Routes/i }).click();
  await page.getByRole('button', { name: /Add data/i }).click();
  await page.waitForTimeout(600);
  check('data entry dialog opens', await page.getByRole('dialog').isVisible());
  check('order form present', await page.getByLabel(/Delivery address/i).isVisible());

  // Client-side validation blocks an empty submit.
  await page.getByRole('button', { name: /Create order/i }).click();
  await page.waitForTimeout(400);
  check('empty order submit shows field errors',
    await page.getByText('Delivery address is required').isVisible());

  // Fill and submit a real order.
  await page.getByLabel(/Delivery address/i).fill('23 Serangoon Central, Singapore 556083');
  await page.getByLabel(/Cargo weight/i).fill('33.5');
  await page.getByRole('button', { name: /Create order/i }).click();
  await page.waitForTimeout(2000);
  check('order created toast appears', await page.getByText(/Order created/i).isVisible());

  // Upload tab with template download links.
  await page.getByRole('tab', { name: /Upload orders/i }).click();
  await page.waitForTimeout(400);
  check('upload dropzone present', await page.getByText(/Drop a orders spreadsheet here/i).isVisible());
  check('template buttons present', await page.getByRole('button', { name: /^CSV$/ }).isVisible());

  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);

  await page.screenshot({ path: process.env.SCREENSHOT ?? 'e2e/console.png', fullPage: false });
  check('console screenshot captured', true);

  const realErrors = errors.filter(
    (e) => !/favicon|tile|WebGL|maplibre|status of 4\d\d/i.test(e),
  );
  if (realErrors.length) console.log('  errors:', JSON.stringify(realErrors.slice(0, 5), null, 2));
  check('no unexpected console errors', realErrors.length === 0, realErrors.slice(0, 3).join(' | '));
} catch (error) {
  check('journey completed without exception', false, String(error).slice(0, 200));
} finally {
  await browser.close();
  app.kill();
}

const failed = results.filter(([ok]) => !ok);
console.log(`\n${results.length - failed.length}/${results.length} UI checks passed`);
process.exit(failed.length ? 1 : 0);
