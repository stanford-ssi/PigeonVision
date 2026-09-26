"""Synthetic code values check conversion against the Rec.709 equations."""
from types import SimpleNamespace

import av
import numpy as np
import pytest

from pigeonvision.video_colour import native_frame_to_pixels


def tagged_frame(y=100, cb=140, cr=160):
    frame = av.VideoFrame(64, 16, "yuv420p")
    for plane, value in zip(frame.planes, (y, cb, cr)):
        plane.update(np.full((plane.height, plane.line_size), value, np.uint8).tobytes())
    frame.colorspace = frame.color_range = 1
    codec = SimpleNamespace(colorspace=1, color_range=1, color_primaries=1, color_trc=1)
    return frame, codec


def reference_rgb(y, cb, cr, kr=.2126, kb=.0722):
    # ITU-R BT.709 limited-range 8-bit Y'CbCr, independently evaluated.
    luma, blue, red = (y - 16) / 219, (cb - 128) / 224, (cr - 128) / 224
    green = 1 - kr - kb
    return np.clip(255 * np.array([
        luma + 2 * (1 - kr) * red,
        luma - 2 * kb * (1 - kb) / green * blue - 2 * kr * (1 - kr) / green * red,
        luma + 2 * (1 - kb) * blue,
    ]), 0, 255)


@pytest.mark.parametrize("codes", [(16, 128, 128), (235, 128, 128), (100, 140, 160), (150, 95, 110)])
def test_native_conversion_matches_independent_709_matrix_and_full_range(codes):
    frame, codec = tagged_frame(*codes)
    pixels, provenance = native_frame_to_pixels(frame, codec)
    np.testing.assert_allclose(pixels[8, 32], reference_rgb(*codes), atol=2)
    assert provenance["source_range"] == "limited"
    assert provenance["output_range"] == "full"
    assert provenance["source_metadata"]["codec"]["color_trc"] == 1
    assert provenance["output_transfer"] == "bt709"
    assert provenance["transfer_conversion"] == "none"
    assert provenance["libswscale_version"] == list(av.library_versions["libswscale"])


def test_chromatic_reference_distinguishes_709_from_601_and_bgr_order():
    codes = (100, 140, 160)
    frame, codec = tagged_frame(*codes)
    rgb, _ = native_frame_to_pixels(frame, codec)
    bgr, provenance = native_frame_to_pixels(frame, codec, format="bgr24")
    np.testing.assert_array_equal(bgr, rgb[:, :, ::-1])
    assert np.max(np.abs(rgb[8, 32] - reference_rgb(*codes, kr=.299, kb=.114))) > 7
    assert provenance["output_pixel_format"] == "bgr24"


@pytest.mark.parametrize("scope,field,value", [
    ("frame", "colorspace", 2), ("frame", "colorspace", 5),
    ("frame", "color_range", 0), ("frame", "color_range", 2),
    ("codec", "colorspace", 5), ("codec", "color_range", 0),
    ("codec", "color_primaries", 2), ("codec", "color_trc", 2),
    ("codec", "color_trc", None), ("codec", "color_range", True),
])
def test_unknown_conflicting_or_invalid_metadata_is_rejected(scope, field, value):
    frame, codec = tagged_frame()
    setattr(frame if scope == "frame" else codec, field, value)
    with pytest.raises(ValueError, match=f"native_colour_metadata_{scope}_{field}"):
        native_frame_to_pixels(frame, codec)


def test_untagged_rgb_is_not_silently_labelled_native_709():
    frame = av.VideoFrame(64, 16, "bgr0")
    with pytest.raises(ValueError, match="not_yuv420p"):
        native_frame_to_pixels(frame, SimpleNamespace())
