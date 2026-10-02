// Offline browser regression. Local assets and a mocked WebSocket only.
// Extension children must not move the full-bleed stage or its fixed HUD.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const assets = path.resolve(__dirname, "../python/pigeonvision/ground/static");
const screenshots = path.resolve(__dirname, "../../build/ground-station");
fs.mkdirSync(screenshots, { recursive: true });

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--enable-gpu"] });
  try {
    const results = [], failures = [];
    for (const [width, height] of [[1600, 1000], [1280, 800], [1100, 650], [844, 390], [390, 844], [320, 568]]) {
      const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });
      page.on("pageerror", error => failures.push(error.message));
      await page.route("http://127.0.0.1:8773/**", route => {
        const uri = new URL(route.request().url()).pathname;
        const file = uri === "/" ? "index.html" : uri.replace(/^\/static\//, "");
        if (file === "favicon.ico") return route.fulfill({ status: 204 });
        if (file.includes("..") || !fs.existsSync(path.join(assets, file))) return route.abort();
        const contentType = file.endsWith(".js") ? "application/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".png") ? "image/png" : file.endsWith(".ttf") ? "font/ttf" : "text/html";
        return route.fulfill({ body: fs.readFileSync(path.join(assets, file)), contentType });
      });
      await page.addInitScript(() => {
        window.sentControls = [];
        window.WebSocket = class {
          static OPEN = 1;
          constructor() {
            this.readyState = 1;
            window.testSocket = this;
            queueMicrotask(() => {
              this.onopen?.();
              this.onmessage?.({ data: JSON.stringify({ type: "control_owner", can_control: true }) });
            });
          }
          send(message) {
            const value = JSON.parse(message);
            window.sentControls.push(value);
            if (value.type === "claim_control") {
              queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ type: "control_owner", can_control: true }) }));
            } else if (value.type !== "view") throw Error("Only offline ground presentation controls are expected");
          }
          close() { this.readyState = 3; }
        };
      });
      await page.goto("http://127.0.0.1:8773/");
      await page.waitForFunction(() => window.pigeonGround?.snapshot().station.controlling);
      await page.locator(".brand-mark").evaluate(logo => logo.decode());
      await page.waitForFunction(() => document.getElementById("connection").textContent !== "Connecting…");
      await page.evaluate(() => document.fonts.ready);
      assert.equal(await page.evaluate(() => [...document.fonts].some(face =>
        face.family.includes("Plex Condensed") && face.weight === "300" && face.status === "loaded")), true,
        "The local light display font must load before layout measurement");
      const measure = () => page.evaluate(() => {
        const bounds = selector => {
          const r = document.querySelector(selector).getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height };
        };
        return { shell: bounds(".ground-app"), header: bounds(".masthead"), image: bounds("#image"),
          hud: bounds(".mission-hud"), altitude: bounds(".altitude-readout"), clock: bounds(".clock-readout"),
          speed: bounds(".speed-readout"), events: bounds(".event-rail"), drawer: bounds("#operator-controls"),
          footer: bounds(".station-toolbar"), pageWidth: document.documentElement.scrollWidth,
          pageHeight: document.documentElement.scrollHeight, logoLoaded: document.querySelector(".brand-mark").naturalWidth > 0 };
      });
      const before = await measure();
      assert.equal(await page.locator("#operator-controls").isVisible(), false, "Drawer starts closed");
      assert.equal(await page.locator("#controls-toggle").getAttribute("aria-expanded"), "false");
      assert.equal(await page.locator("#take-control").isVisible(), false, "An owner does not need to claim control");
      assert.equal(before.image.x, 0);
      assert.equal(before.image.y, 0);
      assert.equal(before.image.width, width);
      assert.equal(before.image.height, height);
      assert.ok(before.hud.y > before.header.y + before.header.height, "HUD must not overlap the header");
      assert.ok(before.events.y + before.events.height <= before.footer.y, "Event rail must stay above toolbar");
      for (const element of ["altitude", "clock", "speed", "events", "footer"]) {
        const r = before[element];
        assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.width <= width + .1 && r.y + r.height <= height + .1,
          `${width}x${height}: ${element} must fit the viewport`);
      }
      if (width > 680) {
        assert.ok(before.clock.x + before.clock.width <= before.altitude.x + .1);
        assert.ok(before.altitude.x + before.altitude.width <= before.speed.x + .1);
      } else {
        assert.ok(before.altitude.y + before.altitude.height <= before.clock.y + .1);
        assert.ok(before.clock.x + before.clock.width <= before.speed.x + .1);
      }
      await page.evaluate(() => {
        const before = document.createElement("div");
        before.id = "extension-before";
        before.textContent = "Ordinary extension child before the app";
        before.style.cssText = "height:1200px;width:200px;background:red";
        document.body.prepend(before);
        const after = document.createElement("div");
        after.id = "extension-after";
        after.textContent = "Ordinary extension child after the app";
        after.style.cssText = "height:900px;width:200px;background:blue";
        document.body.append(after);
        document.body.prepend(document.createElement("grammarly-desktop-integration"));
      });
      const after = await measure();
      for (const element of ["shell", "header", "image", "hud", "altitude", "clock", "speed", "events", "drawer", "footer"])
        assert.deepEqual(after[element], before[element], `${width}px: injected body child moved ${element}`);
      assert.ok(after.logoLoaded);
      assert.equal(after.shell.height, height);
      assert.equal(after.pageWidth, width);
      assert.equal(after.pageHeight, height);
      await page.screenshot({ path: path.join(screenshots, `operator-${width}x${height}.png`) });

      await page.evaluate(() => window.testSocket.onmessage({
        data: JSON.stringify({ type: "control_owner", can_control: false }),
      }));
      await page.waitForFunction(() => !window.pigeonGround.snapshot().station.controlling);
      assert.equal(await page.locator("#operator-controls").isVisible(), false);
      assert.equal(await page.locator("#pilot-status").textContent(), "Following operator");
      assert.equal(await page.locator("#take-control").isVisible(), true, "A follower can find Take control without opening the drawer");
      const claim = await page.locator("#take-control").boundingBox();
      const tools = await page.locator(".toolbar-actions").boundingBox();
      assert.ok(claim.x >= 0 && claim.y >= 0 && claim.x + claim.width <= width && claim.y + claim.height <= height);
      assert.ok(claim.x + claim.width < tools.x, "Take control must not overlap the view toolbar");
      assert.equal(await page.evaluate(() => window.sentControls.filter(value => value.type === "claim_control").length), 0,
        "Following another operator must never claim control automatically");
      await page.screenshot({ path: path.join(screenshots, `following-${width}x${height}.png`) });
      await page.locator("#take-control").click();
      await page.waitForFunction(() => window.pigeonGround.snapshot().station.controlling);
      assert.equal(await page.evaluate(() => window.sentControls.filter(value => value.type === "claim_control").length), 1,
        "The visible action sends one explicit claim request");
      assert.equal(await page.locator("#take-control").isVisible(), false);

      await page.locator("#controls-toggle").click();
      await page.locator("#operator-controls").waitFor({ state: "visible" });
      assert.equal(await page.locator("#controls-toggle").getAttribute("aria-expanded"), "true");
      assert.equal(await page.locator("#advanced-controls").getAttribute("open"), null, "Advanced starts closed");
      assert.equal(await page.locator("#focus-zoom").isVisible(), false, "Bench controls stay out of the normal menu");
      assert.equal(await page.locator("#view-presets button").count(), 6, "Normal menu has six direction presets");
      assert.equal(await page.locator("#extra-presets button").count(), 3, "Extra saved views belong in Advanced");
      assert.equal(await page.locator("#horizon-lock").isChecked(), false, "Horizon leveling is optional and starts off");
      assert.equal(await page.locator("#horizon-lock").isDisabled(), true, "Horizon leveling requires timed attitude");
      assert.equal(await page.locator("#horizon-status").textContent(), "Needs timed attitude");
      assert.equal(await page.inputValue("#seam"), "0", "Narrow feather is the initial seam");
      await page.screenshot({ path: path.join(screenshots, `drawer-${width}x${height}.png`) });
      await page.locator("#advanced-controls > summary").click();
      await page.locator("#focus-zoom").scrollIntoViewIfNeeded();
      assert.equal(await page.locator("#focus-zoom").isVisible(), true);
      const opened = await measure();
      assert.deepEqual(opened.image, before.image, "Drawer must overlay the stage without resizing it");
      assert.deepEqual(opened.hud, before.hud, "Drawer must not move telemetry");
      assert.ok(opened.drawer.x >= 0 && opened.drawer.y >= 0 && opened.drawer.x + opened.drawer.width <= width + .1 && opened.drawer.y + opened.drawer.height <= height + .1);
      const focus = await page.locator("#focus-zoom").boundingBox();
      assert.ok(focus.y >= opened.drawer.y && focus.y + focus.height <= opened.drawer.y + opened.drawer.height + .1,
        "Drawer can scroll its raw camera controls into view");
      await page.screenshot({ path: path.join(screenshots, `advanced-${width}x${height}.png`) });
      await page.locator("#controls-toggle").click();
      await page.locator("#operator-controls").waitFor({ state: "hidden" });
      assert.equal(await page.locator("#operator-controls").isVisible(), false);

      if (width === 1600) {
        // Synthetic measurements verify the rendering boundary, not hardware.
        const result = await page.evaluate(async () => {
          const { MissionHUD } = await import("/static/hud.js");
          const hud = new MissionHUD();
          const plot = Array.from({ length: 2200 }, (_, i) => ({ pts: i * 5000, altitude: i / 5 }))
            .filter(p => p.pts < 1200000 || p.pts > 1500000);
          plot[80].altitude = null;
          const mission = { pts: 3000000, altitude: 120, speed: -12.3, elapsed: 3,
            pressureFresh: true, phase: "DESCENT", simulated: true, reference: { pressure: 100000 },
            events: [{ pts: 0, event: "LAUNCH" }, { pts: 4000000, event: "APOGEE" }], plot };
          hud.update({ mission, connected: true, replay: true, mode: "perspective", yaw: .4, pitch: -.7, fov: 1.2, frameAgeMs: 2500 });
          const d = document.getElementById("hud-altitude-plot").getAttribute("d");
          const initial = { altitude: document.getElementById("hud-altitude").textContent,
            speed: document.getElementById("hud-speed").textContent,
            clock: document.getElementById("hud-clock").textContent,
            mode: document.getElementById("hud-feed-mode").textContent,
            simulated: !document.getElementById("hud-simulated").hidden,
            pitch: document.getElementById("hud-pitch").textContent,
            futureEvent: document.querySelector('[data-event="APOGEE"]').classList.contains("is-recorded"),
            vertices: (d.match(/[ML]/g) || []).length, breaks: (d.match(/M/g) || []).length };
          const sourceLabels = {};
          for (const sourceState of ["ready", "waiting", "disconnected", "error", "ended"]) {
            hud.update({ mission, replay: true, connected: true, frameAgeMs: 0, sourceState });
            sourceLabels[sourceState] = document.getElementById("hud-link-state").textContent;
          }
          hud.update({ mission, mode: "perspective", yaw: .4, pitch: 0, fov: 1.2,
            horizon: { requested: true, active: true } });
          const horizonLabels = { direction: document.getElementById("hud-direction").textContent,
            centre: document.getElementById("hud-bearing-center").textContent,
            description: document.getElementById("hud-view-direction").getAttribute("aria-label") };
          hud.update({ mission, mode: "perspective", yaw: .4, pitch: 0, fov: 1.2,
            horizon: { requested: true, active: false } });
          horizonLabels.unavailable = document.getElementById("hud-direction").textContent;
          hud.update({ mission, connected: true, sourceState: "disconnected", frameAgeMs: null });
          sourceLabels.noHeldFrame = document.getElementById("hud-link-state").textContent;
          hud.update({ mission: { ...mission, pressureFresh: false, plot: [] }, replay: true, connected: true });
          return { ...initial, sourceLabels, horizonLabels, staleAltitude: document.getElementById("hud-altitude").textContent,
            staleSpeed: document.getElementById("hud-speed").textContent,
            staleNote: document.getElementById("hud-pressure-status").textContent,
            clearedPlot: document.getElementById("hud-altitude-plot").getAttribute("d") };
        });
        assert.equal(result.altitude, "120");
        assert.equal(result.speed, "-12.3");
        assert.equal(result.clock, "00:03.0");
        assert.equal(result.mode, "REPLAY");
        assert.equal(result.simulated, true);
        assert.match(result.pitch, /^↓ -40°$/);
        assert.equal(result.futureEvent, false, "Future events cannot be marked received");
        assert.deepEqual(result.horizonLabels, { direction: "LEVEL / 23°", centre: "0°",
          description: "View direction in the simulated world frame", unavailable: "LEVEL UNAVAILABLE / 23°" });
        assert.ok(result.vertices > 0 && result.vertices <= 200, "Sparkline vertices must stay bounded");
        assert.ok(result.breaks >= 3, "Invalid samples and acquisition gaps must break the plot");
        assert.deepEqual(result.sourceLabels, { ready: "RECEIVING", waiting: "VIDEO WAITING",
          disconnected: "IMAGE HELD", error: "SOURCE ERROR", ended: "END OF RECORDING", noHeldFrame: "SOURCE OFFLINE" },
          "Known source state must take priority over recently decoded cached frames");
        assert.equal(result.staleAltitude, "—");
        assert.equal(result.staleSpeed, "—");
        assert.equal(result.staleNote, "PRESSURE STALE");
        assert.equal(result.clearedPlot, "");
      }

      await page.goto("http://127.0.0.1:8773/?mode=audience");
      await page.waitForFunction(() => window.pigeonGround?.snapshot().station.audience);
      assert.equal(await page.locator("#controls-toggle").isVisible(), false);
      assert.equal(await page.locator("#take-control").isVisible(), false, "Audience mode never offers control");
      assert.equal(await page.locator(".ownership-control").isVisible(), false);
      assert.equal(await page.locator("#operator-controls").isVisible(), false);
      assert.equal(await page.locator(".audience-return").isVisible(), true);
      assert.equal(await page.locator(".audience-return").getAttribute("href"), "/");
      assert.equal((await page.evaluate(() => window.pigeonGround.snapshot())).station.controlling, false);
      assert.equal(await page.evaluate(() => window.sentControls.some(value => value.type === "claim_control")), false);
      await page.evaluate(() => {
        document.body.classList.add("controls-open");
        window.testSocket.onmessage({ data: JSON.stringify({ type: "error", component: "viewer", message: "Synthetic decoder failure" }) });
      });
      assert.equal(await page.locator("#operator-controls").isVisible(), false, "Audience never exposes the operator drawer");
      await page.waitForFunction(() => document.getElementById("error").textContent.includes("Synthetic decoder"));
      assert.equal(await page.locator("#error").isVisible(), true, "Audience must see decoder failures");
      assert.equal(await page.locator("#overlay").isVisible(), true, "Audience must see unavailable video");
      await page.screenshot({ path: path.join(screenshots, `audience-${width}x${height}.png`) });
      results.push({ viewport: [width, height], stage: [after.image.width, after.image.height],
        overlay_drawer: true, audience_error_visible: true, injected_body_children: 3 });
      await page.close();
    }
    assert.deepEqual(failures, []);
    console.log(JSON.stringify({ passed: true, layouts: results,
      checked: ["full-bleed stage", "fixed telemetry", "scrollable drawer", "local logo", "visible follower control action", "explicit ownership claim only", "audience controls hidden", "audience errors visible", "stale telemetry blank", "future events excluded", "plot gaps and vertex bound"] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
