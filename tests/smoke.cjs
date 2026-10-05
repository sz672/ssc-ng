/* Optional, read-only browser smoke test against a running SSC-NG local service.
 * Start the backend first: python3 server.py --port 8765
 * Run: SSCNG_BASE_URL=http://127.0.0.1:8765 node tests/smoke.cjs
 * Works with either a fresh or an already populated submission demo.
 * Never submits or approves a package.
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
    const serviceState = await (await loadedState).json();
    assert.equal(serviceState.api_revision, 1, 'The running service must support the page’s API revision.');
    await page.locator('#sg-service-status').filter({hasText: 'Local service online'}).waitFor();
    assert.equal(await page.locator('#sg-connection-error').isHidden(), true);
    assert.match(await page.locator('#sg-stata-status').textContent(), /Stata executable found|Stata unavailable/);
    assert.equal(await page.locator('#sg-trusted').isChecked(), false, 'Code execution needs explicit trust.');
    assert.equal(await page.locator('#sg-file').getAttribute('type'), 'file');
    assert.equal(await page.getByRole('button', {name:'Read package details',exact:true}).count(), 1);
    assert.equal(await page.locator('#sg-test_file').inputValue(), 'smoke.do');
    assert.equal(await page.locator('#sg-source_url').getAttribute('type'), 'url');
    assert.equal(await page.locator('#sg-license').inputValue(), 'Unspecified');
    assert.ok(await page.locator('#sg-example option').count() > 0, 'Examples must be listed.');
    assert.equal(await page.locator('#sg-example-run').isEnabled(), true);

    const tabs = [
      ['submit', '1 · Submit'],
      ['review', '2 · Checks & review'],
      ['archive', '3 · Deliver & archive'],
    ];
    assert.equal(await page.getByRole('tab').count(), 3, 'Submission, review, and current archive are the only workspaces.');
    assert.equal(await page.locator('#sg-history, #sg-records').count(), 0, 'Archive history and registry management are outside this demo.');
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

    assert.match(await page.locator('#sg-archive').innerText(), /Archive history and versioning are handled by the existing/);
    assert.match(await page.locator('#sg-archive').innerText(), /not connected to the production archive/);
    assert.match(await page.locator('#sg-archive').innerText(), /Delivery queue/);
    assert.match(await page.locator('#sg-archive').innerText(), /Current packages/);
    assert.equal(await page.locator('#sg-archive a[href="https://github.com/ssc-ng/archive/"]').count(), 1);
    assert.equal(await page.locator('[data-action="restore"], [data-action="load-diff"], [data-action="capture"], #sg-transfer-form').count(), 0);
    for (const link of await page.locator('#sg-archive .sg-link-button').all()) {
      assert.match(await link.getAttribute('href'), /^\/api\/(archive\/[a-z][a-z0-9_]*\/download|submissions\/[A-Za-z0-9_-]+\/(handoff|receipt))$/, 'Downloads refer to package handoffs, receipts, or current archive packages.');
    }

    // Keyboard tab navigation should expose the corresponding workspace.
    await page.getByRole('tab', {name:'1 · Submit', exact:true}).focus();
    await page.keyboard.press('Home');
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#sg-tab-review').getAttribute('aria-selected'), 'true');
    await page.keyboard.press('End');
    assert.equal(await page.locator('#sg-tab-archive').getAttribute('aria-selected'), 'true');
    await page.emulateMedia({colorScheme:'dark'});
    await page.getByRole('tab', {name:'1 · Submit', exact:true}).click();
    assert.equal(await page.locator('#sg-submit').isVisible(), true);
    assert.deepEqual(writes, [], 'The read-only smoke test must not send mutations.');
    assert.deepEqual(errors, [], 'No browser errors.');
    console.log('Read-only submission UI checks passed on desktop and mobile. No packages were changed.');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
