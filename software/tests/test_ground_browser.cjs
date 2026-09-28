// Run against the generated 12-frame transport from test_ground_transport.py.
// NODE_PATH may point to an existing Playwright installation. No camera needed.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch({
    channel: "chrome",
    headless: true,
    args: ["--enable-gpu"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(process.env.GROUND_URL || "http://127.0.0.1:8768/");
    await page.waitForFunction(
      () => window.pigeonGround?.snapshot().pairs >= 1,
    );
    let snapshot = await page.evaluate(() => window.pigeonGround.snapshot());
    assert.equal(snapshot.errors, null);
    assert.equal(
      snapshot.pair.skew,
      Number(process.env.GROUND_EXPECTED_SKEW ?? -10000),
    );
    const initial = snapshot.pairs;
    await page.getByRole("button", { name: "Step pair" }).click();
    await page.waitForFunction(
      (n) => window.pigeonGround.snapshot().pairs > n,
      initial,
    );
    snapshot = await page.evaluate(() => window.pigeonGround.snapshot());
    assert.equal(snapshot.pairs, initial + 1);
    assert.equal(snapshot.pending.A, 0);
    assert.equal(snapshot.pending.B, 0);
    if (snapshot.calibrated) {
      await page.selectOption("#view", "perspective");
      await page.locator("#image").hover();
      await page.mouse.wheel(0, -100);
    }
    const output = process.env.GROUND_SCREENSHOT;
    if (output) {
      fs.mkdirSync(path.dirname(output), { recursive: true });
      await page.screenshot({ path: output, fullPage: true });
    }
    await page.getByRole("button", { name: "Play", exact: true }).click();
    await page.waitForFunction(
      () => window.pigeonGround.snapshot().pairs >= 12,
    );
    snapshot = await page.evaluate(() => window.pigeonGround.snapshot());
    assert.equal(snapshot.errors, null);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, ...snapshot }));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
