// Decoder recovery with local assets and mocked transport.
const assert = require("node:assert/strict");
const { openGroundFixture } = require("./browser/ground_fixture.cjs");

(async () => {
  const fixture = await openGroundFixture();
  const { browser, page, errors, emit, snapshot } = fixture;
  try {
    const frame = (name, pts, keyframe = true) => fixture.frame(name, pts, keyframe);
    const presented = pts => page.waitForFunction(pts => window.pigeonGround.snapshot().presentation.frame.pts === pts, pts);
    await emit({ type: "status", state: "ready", source: "udp://synthetic:5000", replay: false, playing: true, generation: 1 });
    await frame("A", 0);
    await presented(0);

    await page.evaluate(() => { window.testDecodeFailure = "async"; });
    await frame("A", 10000, false);
    await page.waitForFunction(() => window.pigeonGround.snapshot().errors?.includes("Camera A decoder: synthetic asynchronous failure"));
    assert.equal((await snapshot()).presentation.frame.pts, null, "Failed decoder must invalidate presentation currency");
    await frame("B", 10000);
    await page.waitForFunction(() => window.pigeonGround.snapshot().presentation.raw.B === 10000);
    assert.match((await snapshot()).errors, /Camera A decoder/, "Camera B recovery cannot clear camera A's error");
    await frame("A", 20000, false);
    await frame("A", 30000);
    await presented(30000);
    assert.equal(await page.evaluate(() => window.testDecodeCalls.includes(20000)), false, "Deltas must be skipped until a fresh keyframe");
    assert.equal((await snapshot()).errors, null, "Successful camera A output must clear its recoverable error");

    // A delayed callback from a retired decoder cannot reset its replacement.
    await page.evaluate(() => window.testDecoders[0].error(Error("retired decoder callback")));
    await frame("A", 40000, false);
    await presented(40000);
    assert.equal((await snapshot()).errors, null);

    await page.evaluate(() => { window.testDecodeFailure = "sync"; });
    await frame("A", 50000, false);
    await page.waitForFunction(() => window.pigeonGround.snapshot().errors?.includes("Camera A decoder: synthetic synchronous failure"));
    assert.equal((await snapshot()).presentation.frame.pts, null);
    await emit({ type: "error", component: "source", message: "independent source failure", recoverable: false });
    await frame("A", 60000);
    await presented(60000);
    assert.equal((await snapshot()).errors, "independent source failure", "Decoder recovery must preserve other failures");
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ checked: ["asynchronous decoder recovery", "synchronous decoder recovery", "wait for IDR", "retired decoder isolation", "independent errors preserved"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
