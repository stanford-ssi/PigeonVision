// Offline UI error lifecycle regressions; no camera, server or browser needed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const source = fs.readFileSync(path.resolve(__dirname, "../python/pigeonvision/ground/static/errors.js"));
const modulePromise = import(`data:text/javascript;base64,${source.toString("base64")}`);

test("ready source removes recovered UDP timeout", async () => {
  const { ErrorState } = await modulePromise;
  const errors = new ErrorState();
  errors.set("No UDP transport received for two seconds", "source", true);
  assert.match(errors.message, /No UDP/);
  errors.ready("source");
  assert.equal(errors.message, null);
});

test("source timeout and recovery preserve decoder, calibration and storage failures", async () => {
  const { ErrorState } = await modulePromise;
  const errors = new ErrorState();
  errors.set("Camera A decoder failed", "decoder:A");
  errors.set("Calibration geometry mismatch", "calibration");
  errors.set("Disk full; recording disabled", "transport_recording");
  errors.set("UDP source timed out", "source", true);
  assert.match(errors.message, /decoder failed/); // Source cannot overwrite it.
  errors.ready("source");
  assert.equal(errors.message, "Camera A decoder failed\nCalibration geometry mismatch\nDisk full; recording disabled");
});

test("WebSocket reconnection clears only recoverable connection errors", async () => {
  const { ErrorState } = await modulePromise;
  const errors = new ErrorState();
  errors.set("WebSocket disconnected", "connection", true);
  errors.set("UDP source timed out", "source", true);
  errors.set("Recording failed", "transport_recording");
  errors.ready("connection");
  assert.equal(errors.message, "UDP source timed out\nRecording failed");
  errors.ready("source");
  assert.equal(errors.message, "Recording failed");
});

test("nonrecoverable source error remains explicit", async () => {
  const { ErrorState } = await modulePromise;
  const errors = new ErrorState();
  errors.set("Recorded file is invalid", "source", false);
  errors.ready("source");
  assert.equal(errors.message, "Recorded file is invalid");
});
