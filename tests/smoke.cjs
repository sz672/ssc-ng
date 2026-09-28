/* Optional, read-only browser smoke test against a running SSC-NG local service.
 * Start the backend first: python3 server.py --port 8765
 * Run: SSCNG_BASE_URL=http://127.0.0.1:8765 node tests/smoke.cjs
 * Works with either a fresh or an already populated registry. Never submits,
 * approves, restores, captures, or transfers a package.
 */
'use strict';
const assert = require('node:assert/strict');

async function main() {
  let chromium;
  try { ({chromium} = require('playwright')); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    throw new Error('Optional dependency missing. Run npm install --no-save --package-lock=false playwright, then npx playwright install chromium.');
  }
  const baseURL = new URL(process.env.SSCNG_BASE_URL || 'http://127.0.0.1:8765');
  assert.match(baseURL.protocol, /^https?:$/, 'SSCNG_BASE_URL must be an HTTP service URL, not index.html.');
  const options = {headless: true};
  if (process.env.CHROMIUM_EXECUTABLE) options.executablePath = process.env.CHROMIUM_EXECUTABLE;
  const browser = await chromium.launch(options);
  try {
    const page = await browser.newPage({viewport: {width: 1060, height: 1250}, colorScheme: 'light'});
    const errors = [];
    const writes = [];
    page.on('pageerror', error => errors.push(error.message));
    // A smoke test must never cause a write, even if a future UI regression does.
    await page.route('**/api/**', route => {
      if (route.request().method() !== 'GET') {
        writes.push(`${route.request().method()} ${route.request().url()}`);
        return route.abort();
      }
      return route.continue();
    });
    const loadedState = page.waitForResponse(response => new URL(response.url()).pathname === '/api/state' && response.status() === 200);
    await page.goto(baseURL.href);
    await loadedState;
    await page.locator('#sg-service-status').filter({hasText: 'Local service online'}).waitFor();
    assert.equal(await page.locator('#sg-connection-error').isHidden(), true);
    assert.match(await page.locator('#sg-stata-status').textContent(), /Stata executable found|Stata unavailable/);
    assert.equal(await page.locator('#sg-trusted').isChecked(), false, 'Code execution needs explicit trust.');
    assert.equal(await page.locator('#sg-file').getAttribute('type'), 'file');
    assert.equal(await page.locator('#sg-license').inputValue(), 'Unspecified');
    assert.ok(await page.locator('#sg-example option').count() > 0, 'Examples must be listed.');
    assert.equal(await page.locator('#sg-example-run').isEnabled(), true);

    const tabs = [
      ['submit', '1 · Submit'],
      ['review', '2 · Checks & review'],
      ['history', '3 · Package history'],
      ['records', '4 · Registry records'],
    ];
    for (const viewport of [{width:1060,height:1250},{width:375,height:1200}]) {
      await page.setViewportSize(viewport);
      for (const [id, name] of tabs) {
        const tab = page.getByRole('tab', {name, exact:true});
        await tab.click();
        assert.equal(await tab.getAttribute('aria-selected'), 'true', `${id} tab selected`);
        assert.equal(await page.locator(`#sg-${id}`).isVisible(), true, `${id} panel visible`);
        assert.equal(await page.locator('[role="tabpanel"]:visible').count(), 1, 'Exactly one workspace is visible.');
        assert.ok((await page.locator(`#sg-${id}`).innerText()).trim().length > 0, `${id} workspace is not blank`);
        const widths = await page.evaluate(() => ({viewport: innerWidth, document: document.documentElement.scrollWidth}));
        assert.ok(widths.document <= widths.viewport + 1, `${id} overflows at ${viewport.width}px: ${widths.document}px`);
      }
    }

    // Keyboard tab navigation should expose the corresponding workspace.
    await page.getByRole('tab', {name:'1 · Submit', exact:true}).focus();
    await page.keyboard.press('Home');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#sg-tab-review').getAttribute('aria-selected'), 'true');
    await page.keyboard.press('End');
    assert.equal(await page.locator('#sg-tab-records').getAttribute('aria-selected'), 'true');
    await page.emulateMedia({colorScheme:'dark'});
    await page.getByRole('tab', {name:'1 · Submit', exact:true}).click();
    assert.equal(await page.locator('#sg-submit').isVisible(), true);
    assert.deepEqual(writes, [], 'The read-only smoke test must not send mutations.');
    assert.deepEqual(errors, [], 'No browser errors.');
    console.log('Read-only UI checks passed on desktop and mobile. Registry records were not changed.');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
