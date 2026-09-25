# Flight camera simulator

Static GitHub Pages site. Open `docs/index.html` through an HTTP server. **Project** links to the repository README.

Two IMX900 cameras sit on opposite sides of a 156.718 mm body. Each CIL212 lens is modeled at an 8 mm pupil offset. The 1552 × 1552 center crops are encoded separately at 30 fps and 4 Mb/s each, then decoded and stitched in the browser. Panning changes viewing direction, not camera position.

The [CIL212 supplier model](https://commonlands.com/pages/camera-field-of-view-calculator) uses `r = 1.1 sin(0.4 θ) / 0.4` mm, with θ in radians. At 2.25 µm pixel pitch, the square crop retains about 197° across each axis and up to 225.8° diagonally. This is predicted coverage, not installed calibration.

The sequence covers the pad, ascent, apogee, separation and early descent. The camera ring is provisionally at the centre of the upper switchband, 0.9024 m from the nose tip; the iris airbrakes sit 150 mm below it. The desert basin is generated terrain, not a survey of the launch site. The 2.9532 m exterior follows the supplied OpenRocket model, including the three fins and boattail. Translation follows the saved M2400T flight (Simulation 5), with a 3 s pad hold and 3,191.207 m apogee. Camera placement, airbrakes and recovery motion remain provisional. Early recovery uses the file’s 0.61 m nominal drogue and 0.30 m shroud lines, with an assumed 80% projected diameter; its 2.1336 m main is specified at 200 m and is not deployed in this short sequence. Lens blur, sensor noise, vibration, attitude errors and RF packet loss are not calibrated or simulated. Synthetic scenery is not a substitute for testing compression on real footage. This does not establish CM5 throughput or radio performance.

The boattail uses the clipped ellipse from OpenRocket. Fin outlines, 7.5 mm thickness and rail-button stations follow the file. Fin edge rounding, fillets, surface finish and camera housings are simplified. Shock-cord lengths and the separation joint need confirmation.

**Controls:** the opening frame previews ascent. Launch plays from the pad; ↺ restores the pad and starting viewing direction. Drag to pan, scroll to zoom, or use the direction buttons. With the camera canvas focused, Space toggles playback and comma/period step one frame. Camera crops and the source map show which image supplies each direction. Settings and model notes are below the views.

## Run and check

From the repository root:

```sh
python3 software/simulator/serve.py
```

Open `http://127.0.0.1:8767/docs/`. Browser checks require Node, Playwright and Chrome:

```sh
node software/simulator/check.cjs
```

To regenerate the clips, also install FFmpeg with libx264:

```sh
FFMPEG=/path/to/ffmpeg node software/simulator/render_sequence.cjs
```

Outputs go to `build/simulator/`. Copy `manifest-v3.json` and both `cil212-camera-*.mp4` files into `docs/assets/video/`. Keep transport streams and logs out of the site. The manifest records settings, source hashes, frame counts and transport timing; mismatched clips fall back to the ideal model.

For Pages, publish the repository's `/docs` directory after merging. All site assets use relative paths; no build service or external CDN is required.
