// Cross-language check: shader texture coordinates versus Python reference.
const { chromium } = require("playwright");
const cp = require("node:child_process");
const path = require("node:path");
const assert = require("node:assert/strict");
const python =
  process.env.GROUND_PYTHON || path.resolve(__dirname, "../.venv/bin/python");
const code = `
import json,math
from pigeonvision.ground.projection import project_ray
base={"image_size":[64,64],"K":[[20,0,31.5],[0,20,31.5],[0,0,1]],"D":[.001,-.0001,.0002,-.0001],"xi":1,"R_camera_from_rig":[[1,0,0],[0,1,0],[0,0,1]],"crop":[0,0,64,64],"output_size":[64,64],"flip_x":False,"flip_y":False,"valid_radius_px":None,"max_theta_deg":110}
cases=[]
for c,angles in [(base,[0,30,80,90,100,120]),({**base,"crop":[8,8,48,48],"output_size":[32,32],"flip_x":True,"flip_y":True},[0,30,80]),({**base,"xi":2,"max_theta_deg":170},[90,110,140])]:
 for angle in angles:
  ray=(math.sin(math.radians(angle)),0,math.cos(math.radians(angle)))
  cases.append({"camera":c,"angle":angle,"expected":project_ray(c,ray)})
print(json.dumps(cases))
`;
const cases = JSON.parse(
  cp.execFileSync(python, ["-c", code], { encoding: "utf8" }),
);
(async () => {
  const browser = await chromium.launch({
    channel: "chrome",
    headless: true,
    args: ["--enable-gpu"],
  });
  try {
    const page = await browser.newPage({ deviceScaleFactor: 1 });
    await page.goto(process.env.GROUND_URL || "http://127.0.0.1:8768/");
    const samples = await page.evaluate(async (cases) => {
      const { Renderer } = await import("/static/projection.js");
      const canvas = document.createElement("canvas");
      canvas.style.cssText =
        "position:fixed;left:-1000px;top:0;width:129px;height:129px";
      document.body.append(canvas);
      const renderer = new Renderer(canvas);
      renderer.mode = "perspective";
      renderer.seam = 2;
      const results = [];
      for (const item of cases) {
        const c = item.camera,
          [w, h] = c.output_size,
          source = document.createElement("canvas");
        source.width = w;
        source.height = h;
        const ctx = source.getContext("2d"),
          image = ctx.createImageData(w, h);
        for (let y = 0; y < h; y++)
          for (let x = 0; x < w; x++) {
            const p = (y * w + x) * 4;
            image.data[p] = Math.round((x * 255) / (w - 1));
            image.data[p + 1] = Math.round((y * 255) / (h - 1));
            image.data[p + 3] = 255;
          }
        ctx.putImageData(image, 0, 0);
        renderer.upload("A", source, true);
        renderer.upload("B", source, true);
        renderer.calibration = { cameras: { A: c, B: c } };
        renderer.yaw = (item.angle * Math.PI) / 180;
        renderer.pitch = 0;
        renderer.draw();
        const pixel = new Uint8Array(4);
        renderer.gl.readPixels(
          64,
          64,
          1,
          1,
          renderer.gl.RGBA,
          renderer.gl.UNSIGNED_BYTE,
          pixel,
        );
        results.push({
          pixel: Array.from(pixel),
          error: renderer.gl.getError(),
        });
      }
      canvas.remove();
      return results;
    }, cases);
    cases.forEach((item, index) => {
      const { pixel, error } = samples[index];
      assert.equal(error, 0);
      if (item.expected) {
        const [u, v] = item.expected,
          [w, h] = item.camera.output_size;
        assert.ok(
          Math.abs(pixel[0] - (u * 255) / (w - 1)) <= 2,
          JSON.stringify({ item, pixel }),
        );
        assert.ok(
          Math.abs(pixel[1] - (v * 255) / (h - 1)) <= 2,
          JSON.stringify({ item, pixel }),
        );
      } else
        assert.ok(
          pixel[2] > 20,
          "Rejected ray must show the no-coverage hatch",
        );
    });
    const geometry = await page.evaluate(async (camera) => {
      const { checkGeometry } = await import("/static/geometry.js");
      const bundle = {
        cameras: {
          A: { ...camera, provenance: { device_id: "physical-A" } },
          B: { ...camera, provenance: { device_id: "physical-B" } },
        },
      };
      const descriptions = Object.fromEntries(
        ["A", "B"].map((n) => [
          n,
          {
            device: "physical-" + n,
            width: 64,
            height: 64,
            sensor_size: [64, 64],
            flip_x: false,
            flip_y: false,
          },
        ]),
      );
      const frames = {
        A: { sensor_crop: [0, 0, 64, 64] },
        B: { sensor_crop: [0, 0, 64, 64] },
      };
      const valid = checkGeometry(bundle, descriptions, frames, {
        A: [64, 64],
        B: [64, 64],
      });
      const mismatch = checkGeometry(
        bundle,
        {
          ...descriptions,
          A: { ...descriptions.A, device: "other", flip_y: true },
        },
        { ...frames, B: { sensor_crop: [2, 0, 62, 64] } },
        { A: [32, 32], B: [64, 64] },
      );
      const unknown = checkGeometry(bundle, {}, {});
      return { valid, mismatch, unknown };
    }, cases[0].camera);
    assert.deepEqual(geometry.valid, { errors: [], unverified: [] });
    assert.equal(geometry.mismatch.errors.length, 4);
    assert.ok(geometry.unknown.unverified.length >= 4);
    console.log(
      JSON.stringify({
        passed: true,
        shader_vectors: cases.length,
        runtime_geometry_checks: 3,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
