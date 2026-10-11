// Requires an isolated replay of tools/full_flight.py + tools/ground_demo.py.
// Runs the complete encoded flight at its recorded speed.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const url = new URL(process.env.GROUND_URL || 'http://127.0.0.1:8773/');
  assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname));
  const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-gpu'] });
  const errors = [];
  const shotDir = process.env.GROUND_SCREENSHOTS;
  const snapshot = page => page.evaluate(() => window.pigeonGround.snapshot());
  const pageFor = async route => {
    const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(new URL(route, url).href);
    await page.waitForFunction(() => window.pigeonGround?.snapshot().pair);
    return page;
  };
  const shot = async (page, name) => {
    if (!shotDir) return;
    fs.mkdirSync(shotDir, { recursive: true });
    await page.screenshot({ path: path.join(shotDir, name + '.png') });
  };
  try {
    const pilot = await pageFor('/');
    if (await pilot.locator('#take-control').isVisible()) await pilot.click('#take-control');
    await pilot.click('#controls-toggle');
    const generation = await pilot.locator('#receiver').evaluate(node => JSON.parse(node.textContent).generation);
    await pilot.getByRole('button', { name: 'Restart', exact: true }).click();
    await pilot.waitForFunction(previous => {
      const status = JSON.parse(document.getElementById('receiver').textContent);
      return status.generation > previous && window.pigeonGround.snapshot().pair?.a === 0;
    }, generation);
    await pilot.selectOption('#view', 'perspective');
    await pilot.waitForFunction(() => window.pigeonGround.snapshot().station.horizon.available);
    assert.equal((await snapshot(pilot)).station.horizon.requested, false, 'Leveling starts off');
    await pilot.click('#pad-reference');
    await pilot.waitForFunction(() => window.pigeonGround.snapshot().station.mission.reference !== null);
    await pilot.check('#horizon-lock');
    await pilot.waitForFunction(() => window.pigeonGround.snapshot().station.horizon.active);
    const audience = await pageFor('/?mode=audience');
    await audience.waitForFunction(() => window.pigeonGround.snapshot().station.horizon.active);
    await audience.waitForFunction(() => window.pigeonGround.snapshot().station.mission.reference !== null);
    await pilot.getByRole('button', { name: 'Play', exact: true }).click();
    await pilot.click('#controls-toggle');

    for (const seconds of [35, 70, 113, 146.5]) {
      await pilot.waitForFunction(time => window.pigeonGround.snapshot().pair.a >= time * 1e6,
        seconds, { timeout: 65000 });
      const state = await snapshot(pilot);
      assert.equal(state.station.horizon.active, true, `Timed attitude available at ${seconds}s`);
      assert.equal(state.station.horizon.pts, state.pair.a);
      await shot(audience, `level-${seconds}s`);
      if (seconds === 70) {
        await pilot.click('#controls-toggle');
        await pilot.getByRole('button', { name: 'Pause', exact: true }).click();
        await pilot.selectOption('#view', 'a');
        await audience.waitForFunction(() => window.pigeonGround.snapshot().navigation.mode === 'a');
        assert.equal((await snapshot(pilot)).station.horizon.active, false, 'Raw camera stays raw');
        await shot(audience, 'raw-descent');
        await pilot.selectOption('#view', 'perspective');
        await pilot.waitForFunction(() => window.pigeonGround.snapshot().station.horizon.active);
        await pilot.getByRole('button', { name: 'Play', exact: true }).click();
        await pilot.click('#controls-toggle');
      }
    }
    await pilot.waitForFunction(() => window.pigeonGround.snapshot().station.mission.phase === 'LANDED');
    await pilot.click('#controls-toggle');
    await pilot.getByRole('button', { name: 'Pause', exact: true }).click();
    const landed = await snapshot(pilot);
    assert.ok(Number.isFinite(landed.station.mission.altitude));
    assert.ok(Number.isFinite(landed.station.mission.speed));
    assert.ok(Math.abs(landed.station.mission.altitude) < 1);
    assert.ok(Math.abs(landed.station.mission.speed) < .1);
    assert.ok(landed.station.mission.events.some(event => event.event === 'LANDED'));
    await pilot.getByRole('button', { name: 'Restart', exact: true }).click();
    await pilot.waitForFunction(() => {
      const s = window.pigeonGround.snapshot();
      return s.pair?.a === 0 && s.station.mission.phase === 'PAD' && s.station.horizon.active;
    });
    assert.equal((await snapshot(pilot)).navigation.mode, 'perspective');
    assert.deepEqual((await snapshot(pilot)).station.mission.events, []);
    await pilot.uncheck('#horizon-lock');
    await audience.waitForFunction(() => !window.pigeonGround.snapshot().station.horizon.requested);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, landedAt: landed.pair.a / 1e6,
      checked: ['full encoded descent', 'paired attitude at displayed PTS', 'audience leveling',
        'raw view unaffected', 'landed telemetry', 'restart preserves view and clears mission'] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
