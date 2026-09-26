"""Reference for the browser's measured Mei infinity projection.

Rig axes: +Z forward through A, +X right, +Y down. Pixel coordinates have a
top-left origin. Translation is intentionally excluded: depth is not known.
"""
import math


def validate_calibration(bundle: dict) -> dict:
    if bundle.get("schema_version") != 1 or bundle.get("model") != "mei":
        raise ValueError("Expected version 1 Mei calibration")
    if set(bundle.get("cameras", {})) != {"A", "B"}:
        raise ValueError("Calibration requires camera A and B")
    for name, camera in bundle["cameras"].items():
        for key in ("K", "R_camera_from_rig"):
            matrix = camera[key]
            if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
                raise ValueError(f"{name}.{key} must be 3x3")
            if not all(math.isfinite(float(v)) for row in matrix for v in row):
                raise ValueError(f"{name}.{key} must be finite")
        rotation = camera["R_camera_from_rig"]
        for i in range(3):
            for j in range(3):
                if abs(sum(rotation[i][k] * rotation[j][k] for k in range(3)) - (1 if i == j else 0)) > 1e-5:
                    raise ValueError(f"{name} rotation must be orthonormal")
        determinant = sum(rotation[0][i] * (rotation[1][(i + 1) % 3] * rotation[2][(i + 2) % 3] - rotation[1][(i + 2) % 3] * rotation[2][(i + 1) % 3]) for i in range(3))
        if abs(determinant - 1) > 1e-5:
            raise ValueError(f"{name} rotation must be proper (determinant +1)")
        if len(camera["D"]) != 4 or not all(math.isfinite(float(x)) for x in camera["D"]):
            raise ValueError(f"{name}.D must contain four finite coefficients")
        if not math.isfinite(float(camera["xi"])):
            raise ValueError(f"{name}.xi must be finite")
        if camera["K"][0][0] <= 0 or camera["K"][1][1] <= 0:
            raise ValueError(f"{name} focal scales must be positive")
        width, height = camera["image_size"]
        x, y, cw, ch = camera["crop"]
        ow, oh = camera["output_size"]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v == int(v)
                   for v in (width, height, x, y, cw, ch, ow, oh)):
            raise ValueError(f"{name} dimensions must be finite integers")
        if min(width, height, cw, ch, ow, oh) <= 0 or x < 0 or y < 0 or x + cw > width or y + ch > height:
            raise ValueError(f"{name} crop/output dimensions are invalid")
        radius = camera.get("valid_radius_px")
        if radius is not None and (not math.isfinite(radius) or radius <= 0):
            raise ValueError(f"{name} valid_radius_px must be positive")
        limit = camera.get("max_theta_deg")
        if limit is not None and not 0 < limit < 180:
            raise ValueError(f"{name} max_theta_deg must lie between 0 and 180")
    colour = bundle.get("display_colour")
    if colour is not None:
        if (not isinstance(colour, dict) or colour.get("schema_version") != 1
                or colour.get("method") != "display_rgb_gain"):
            raise ValueError("Expected a version 1 display RGB colour match")
        reference = colour.get("reference_camera")
        neutral_target = reference is None and colour.get("reference_target") == "colorchecker_neutrals"
        if reference not in ("A", "B") and not neutral_target:
            raise ValueError("Colour match needs a camera or ColorChecker neutral reference")
        if reference in ("A", "B") and colour.get("reference_target") is not None:
            raise ValueError("Declare one colour reference, not both camera and target")
        scale = colour.get("common_headroom_scale", 1)
        if type(scale) not in (int, float) or not math.isfinite(scale) or not .5 <= scale <= 1:
            raise ValueError("Colour headroom scale must be finite and between 0.5 and 1")
        if not neutral_target and scale != 1:
            raise ValueError("A colour reference camera requires identity headroom scale")
        if "camera_strengths" in colour:
            strengths = colour["camera_strengths"]
            if (not isinstance(strengths, dict) or set(strengths) != {"A", "B"}
                    or not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1
                               for v in strengths.values())):
                raise ValueError("Colour strengths must contain finite A and B values between 0 and 1")
        if not isinstance(colour.get("gains"), dict) or set(colour["gains"]) != {"A", "B"}:
            raise ValueError("Colour match needs gains for A and B")
        if not isinstance(colour.get("devices"), dict):
            raise ValueError("Colour match must identify both physical cameras")
        for name in ("A", "B"):
            gains = colour["gains"][name]
            if (not isinstance(gains, list) or len(gains) != 3
                    or not all(type(v) in (int, float) and math.isfinite(v) and .5 <= v <= 2 for v in gains)):
                raise ValueError("Colour gains must be three finite values between 0.5 and 2")
            device = bundle["cameras"][name].get("provenance", {}).get("device_id")
            if not device or colour.get("devices", {}).get(name) != device:
                raise ValueError("Colour match must identify the same physical cameras as the lens models")
        if not neutral_target and any(abs(v - 1) > 1e-9 for v in colour["gains"][reference]):
            raise ValueError("The colour reference camera must retain identity gains")
    return bundle


def project_ray(camera: dict, ray: tuple[float, float, float]) -> tuple[float, float] | None:
    """Map a rig direction to the actual encoded image, or None outside its mask."""
    r = camera["R_camera_from_rig"]
    d = [sum(row[i] * ray[i] for i in range(3)) for row in r]
    norm = math.sqrt(sum(x * x for x in d))
    if norm < 1e-12:
        return None
    x, y, z = [v / norm for v in d]
    limit = camera.get("max_theta_deg")
    if limit is not None and math.acos(max(-1, min(1, z))) > math.radians(limit):
        return None
    xi = camera["xi"]
    denominator = z + xi
    # For xi > 1 the unified sphere has a second projection branch. Exclude
    # it rather than mapping a rear ray onto the same calibrated image point.
    if denominator <= 1e-8 or (xi > 1 and z <= -1 / xi):
        return None
    x, y = x / denominator, y / denominator
    k1, k2, p1, p2 = camera["D"]
    r2 = x * x + y * y
    radial = 1 + k1 * r2 + k2 * r2 * r2
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    k = camera["K"]
    u, v = k[0][0] * xd + k[0][1] * yd + k[0][2], k[1][1] * yd + k[1][2]
    radius = camera.get("valid_radius_px")
    if radius is not None and math.hypot(u - k[0][2], v - k[1][2]) > radius:
        return None
    cx, cy, cw, ch = camera["crop"]
    if not (cx <= u < cx + cw and cy <= v < cy + ch):
        return None
    ow, oh = camera["output_size"]
    # Pixel-centre convention survives resize and then the output image flips.
    u, v = (u - cx + .5) * ow / cw - .5, (v - cy + .5) * oh / ch - .5
    if camera.get("flip_x", False):
        u = ow - 1 - u
    if camera.get("flip_y", False):
        v = oh - 1 - v
    return u, v
