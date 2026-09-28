// Negotiated capture geometry is separate from fitted lens parameters.
export function checkGeometry(
  bundle,
  descriptions = {},
  frames = {},
  sizes = {},
) {
  const errors = [],
    unverified = [];
  if (!bundle) return { errors, unverified: ["No calibration loaded"] };
  const equal = (a, b) =>
    Array.isArray(a) &&
    Array.isArray(b) &&
    a.length === b.length &&
    a.every((v, i) => Number.isFinite(v) && Math.abs(v - b[i]) < 1e-5);
  for (const name of ["A", "B"]) {
    const calibration = bundle.cameras[name],
      description = descriptions[name],
      frame = frames[name];
    if (sizes[name] && !equal(sizes[name], calibration.output_size))
      errors.push(`${name}: decoded dimensions differ from calibration`);
    if (!description) {
      unverified.push(
        `${name}: capture identity/orientation metadata unavailable`,
      );
    } else {
      const device = calibration.provenance?.device_id;
      if (device && description.device && device !== description.device)
        errors.push(`${name}: physical camera ID differs from calibration`);
      else if (!device || !description.device)
        unverified.push(`${name}: calibration camera identity unverified`);
      for (const key of ["flip_x", "flip_y"]) {
        if (typeof description[key] !== "boolean")
          unverified.push(`${name}: ${key} unknown`);
        else if (description[key] !== Boolean(calibration[key]))
          errors.push(`${name}: ${key} differs from calibration`);
      }
      if (
        description.sensor_size &&
        !equal(description.sensor_size, calibration.image_size)
      )
        errors.push(`${name}: sensor mode differs from calibration`);
      if (
        Number.isFinite(description.width) &&
        Number.isFinite(description.height) &&
        !equal([description.width, description.height], calibration.output_size)
      )
        errors.push(`${name}: configured output size differs from calibration`);
    }
    if (frame?.sensor_crop) {
      if (!equal(frame.sensor_crop, calibration.crop))
        errors.push(`${name}: actual sensor crop differs from calibration`);
    } else
      unverified.push(
        `${name}: actual crop in full-sensor coordinates unavailable`,
      );
  }
  return { errors, unverified };
}
