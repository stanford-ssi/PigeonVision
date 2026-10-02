// Selected prototype settings. Optical and RF performance still need bench measurements.
export const SCENARIO = {
  id: "imx900-cil212-ground-stitch-v3",
  fps: 30,
  camera: {
    sensor_width: 2064, sensor_height: 1552, crop_px: 1552,
    pixel_pitch_mm: 0.00225, lens: "CIL212", image_circle_mm: 3.9,
    field_deg: 225.8, efl_mm: 1.1, distortion_b: 0.4,
  },
  geometry: { diameter_mm: 156.718, pupil_standoff_mm: 8, airbrake_offset_mm: 150 },
  capture: { exposure_ms: 0.5, skew_ms: 0, readout_ms: 0 },
  video: { per_camera_mbps: 4, transport_mbps: 9 },
  radio: {
    frequency_ghz: 1.28, tx_average_w: 0.5, flight_gain_dbi: 0,
    ground_gain_dbi: 15, aggregate_losses_db: 7, noise_figure_db: 3,
    required_cn_db: 8, bandwidth_mhz: 9.6, reserve_db: 10,
    symbols_per_second: 8000000, symbols_per_frame: 33282,
    user_bits_per_frame: 42960, capacity_fraction: 0.95,
  },
};
export function linkMargin(rangeM) {
  const r = SCENARIO.radio;
  const fspl = 92.45 + 20 * Math.log10(r.frequency_ghz) + 20 * Math.log10(Math.max(rangeM, 1) / 1000);
  const received = 10 * Math.log10(r.tx_average_w * 1000) + r.flight_gain_dbi + r.ground_gain_dbi - r.aggregate_losses_db - fspl;
  const noise = -174 + 10 * Math.log10(r.bandwidth_mhz * 1e6) + r.noise_figure_db;
  return received - noise - r.required_cn_db - r.reserve_db;
}
export function radialPixels(angle, preset) {
  if (preset.efl && preset.b) return preset.efl * Math.sin(preset.b * angle) / preset.b / preset.pitch;
  return angle / (preset.field * Math.PI / 360) * preset.circle / preset.pitch / 2;
}
