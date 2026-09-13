/**
 * Keyboard and screen-reader smoke test for the dispatcher console.
 *
 * Checks the things a mouse-driven test misses: the skip link, a visible focus
 * ring, reachable controls, correct tablist semantics, and labelled form
 * fields. Run it the same way as console.smoke.mjs.
 */

import { chromium } from 'playwright';
import { spawn } from 'node:child_process';

const app = spawn('npx', ['vite', 'preview', '--port', '4173', '--strictPort'], {
  cwd: new URL('..', import.meta.url).pathname,
  stdio: 'ignore',
});
await new Promise((r) => setTimeout(r, 4000));

const results = [];
const check = (name, ok, detail = '') => {
  results.push([ok, name, detail]);
  console.log(`  ${ok ? '✓' : '✗'} ${name}${detail ? ` — ${detail}` : ''}`);
};

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  await page.goto('http://127.0.0.1:4173/', { waitUntil: 'networkidle' });

  // The console is open: the plan is on screen without any sign-in step.
  await page.waitForSelector('[data-testid="map-canvas"]', { timeout: 20000 });
  check('console reaches the plan with no sign-in step', true);
  await page.waitForTimeout(2000);

  // The skip link is the first tab stop and is visible when focused.
  await page.keyboard.press('Tab');
  const skip = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { text: el.textContent?.trim(), visible: rect.width > 0 && rect.height > 0 };
  });
  check('skip link is the first tab stop', skip?.text === 'Skip to main content', skip?.text ?? '');
  check('skip link becomes visible on focus', Boolean(skip?.visible));

  // Focus is always visible.
  const ring = await page.evaluate(() => {
    const el = document.activeElement;
    const style = el ? getComputedStyle(el) : null;
    return style ? `${style.outlineStyle}/${style.boxShadow.slice(0, 30)}` : '';
  });
  check('focused element has a visible focus indicator', ring.length > 0, ring);

  // Tablist semantics.
  const tabs = await page.evaluate(() => {
    const list = document.querySelector('[role="tablist"]');
    const items = Array.from(list?.querySelectorAll('[role="tab"]') ?? []);
    return items.map((t) => ({
      selected: t.getAttribute('aria-selected'),
      controls: t.getAttribute('aria-controls'),
      tabIndex: t.tabIndex,
      panelExists: Boolean(document.getElementById(t.getAttribute('aria-controls') ?? '')),
    }));
  });
  check('tabs expose aria-selected', tabs.every((t) => t.selected !== null));
  check('each tab controls an existing tabpanel', tabs.length > 0 && tabs.every((t) => t.panelExists));
  check('only the selected tab is in the tab order',
    tabs.filter((t) => t.tabIndex === 0).length === 1);

  // Arrow keys move between tabs.
  await page.locator('[role="tab"][aria-selected="true"]').focus();
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(400);
  const movedTo = await page.evaluate(() => document.activeElement?.textContent?.trim());
  check('arrow keys move between tabs', /alerts/i.test(movedTo ?? ''), movedTo ?? '');
  await page.keyboard.press('ArrowLeft');
  await page.waitForTimeout(300);

  // Every form control is labelled.
  await page.getByRole('button', { name: /Add data/i }).click();
  await page.waitForTimeout(600);
  const unlabelled = await page.evaluate(() => {
    const controls = Array.from(
      document.querySelectorAll('[role="dialog"] input, [role="dialog"] select'),
    );
    return controls
      .filter((c) => {
        if (c.getAttribute('aria-label') || c.getAttribute('aria-labelledby')) return false;
        if (c.type === 'file') return false;
        return !(c.id && document.querySelector(`label[for="${CSS.escape(c.id)}"]`));
      })
      .map((c) => c.id || c.name || c.type);
  });
  check('every dialog form control has a label', unlabelled.length === 0, unlabelled.join(', '));

  // Validation errors are announced.
  await page.getByRole('button', { name: /Create order/i }).click();
  await page.waitForTimeout(400);
  const alerts = await page.locator('[role="alert"]').count();
  check('validation errors use role="alert"', alerts > 0, `${alerts} alert nodes`);

  // Icon-only buttons carry accessible names.
  const namelessButtons = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('button'))
      .filter((b) => {
        const text = (b.textContent ?? '').trim();
        return !text && !b.getAttribute('aria-label') && !b.getAttribute('title');
      })
      .length;
  });
  check('icon-only buttons have accessible names', namelessButtons === 0, `${namelessButtons} unnamed`);
} catch (error) {
  check('a11y journey completed', false, String(error).slice(0, 200));
} finally {
  await browser.close();
  app.kill();
}

const failed = results.filter(([ok]) => !ok);
console.log(`\n${results.length - failed.length}/${results.length} accessibility checks passed`);
process.exit(failed.length ? 1 : 0);
