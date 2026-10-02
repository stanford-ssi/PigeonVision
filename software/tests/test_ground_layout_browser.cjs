// Isolated CSS regression: ordinary extension children must not take app rows.
// Assets are routed from disk; app.js is disabled and no receiver is contacted.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const assets = path.resolve(__dirname, "../python/pigeonvision/ground/static");

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const results = [];
    for (const [width, height] of [[1600, 1000], [1280, 800], [1100, 650], [390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      await page.route("http://127.0.0.1:9876/**", (route) => {
        const uri = new URL(route.request().url()).pathname;
        const file = uri === "/" ? "index.html" : uri.replace(/^\/static\//, "");
        if (file.endsWith(".js")) return route.fulfill({ body: "", contentType: "application/javascript" });
        const contentType = file.endsWith(".css") ? "text/css" : file.endsWith(".png") ? "image/png" : "text/html";
        return route.fulfill({ body: fs.readFileSync(path.join(assets, file)), contentType });
      });
      await page.goto("http://127.0.0.1:9876/");
      await page.locator(".brand-mark").evaluate((logo) => logo.decode());
      const measure = () => page.evaluate(() => {
        const bounds = (selector) => {
          const r = document.querySelector(selector).getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height };
        };
        return { shell: bounds(".bench-app"), header: bounds(".masthead"), image: bounds("#image"),
          reset: bounds("#home"), focus: bounds("#focus-zoom"), footer: bounds(".bench-footer"),
          pageWidth: document.documentElement.scrollWidth, pageHeight: document.documentElement.scrollHeight,
          logoLoaded: document.querySelector(".brand-mark").naturalWidth > 0 };
      });
      const before = await measure();
      await page.evaluate(() => {
        const before = document.createElement("div");
        before.id = "extension-before";
        before.textContent = "Ordinary extension child before the app";
        before.style.cssText = "height:1200px;width:200px;background:red";
        document.body.prepend(before);
        const after = document.createElement("div");
        after.id = "extension-after";
        after.textContent = "Ordinary extension child after the footer";
        after.style.cssText = "height:900px;width:200px;background:blue";
        document.body.append(after);
        const custom = document.createElement("grammarly-desktop-integration");
        document.body.prepend(custom);
      });
      const after = await measure();
      for (const element of ["shell", "header", "image", "reset", "focus", "footer"])
        assert.deepEqual(after[element], before[element], `${width}px: injected body child moved ${element}`);
      assert.ok(after.logoLoaded);
      assert.equal(after.header.y, 0);
      assert.ok(after.header.height <= 78);
      assert.equal(after.shell.height, height);
      assert.equal(after.pageWidth, width);
      assert.equal(after.pageHeight, height);
      assert.ok(after.image.height >= 250, `${width}px: image unexpectedly collapsed`);
      if (width > 800) {
        assert.ok(after.focus.y + after.focus.height <= height);
        assert.ok(after.reset.y + after.reset.height <= height);
        assert.equal(after.footer.y + after.footer.height, height);
      } else {
        await page.locator("#focus-zoom").scrollIntoViewIfNeeded();
        const scrolled = await measure();
        assert.ok(scrolled.focus.y >= 0 && scrolled.focus.y < height);
      }
      results.push({ viewport: [width, height], header_height: after.header.height,
        image: [after.image.width, after.image.height], injected_body_children: 3 });
      await page.close();
    }
    console.log(JSON.stringify({ passed: true, layouts: results }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
