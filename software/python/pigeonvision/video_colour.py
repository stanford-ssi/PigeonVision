"""Explicit conversion of verified native Rec.709 video to display-code RGB.

This changes the Y'CbCr matrix and range, not the transfer function: the result
is gamma-encoded BT.709 RGB, not linear light or an sRGB colour-managed image.
"""
from __future__ import annotations

import av


# FFmpeg libswscale/swscale.h; PyAV 16.1 exposes filters but not these flags.
# https://github.com/FFmpeg/FFmpeg/blob/n7.1.1/libswscale/swscale.h
SWS_BILINEAR = 2
SWS_FULL_CHR_H_INT = 0x2000
SWS_ACCURATE_RND = 0x40000


def native_frame_to_pixels(frame, codec_context, *, format: str = "rgb24"):
    """Return ``(pixels, provenance)``; reject missing or contradictory tags.

    Pass the decoded frame and its source stream's ``codec_context``. Explicit
    limited-to-full range arguments also force PyAV 16.1's reformatter to apply
    the BT.709 coefficients, which its equal-defaults path otherwise skips.
    """
    if format not in ("rgb24", "bgr24"):
        raise ValueError("native colour conversion supports rgb24 or bgr24")
    if getattr(getattr(frame, "format", None), "name", None) != "yuv420p":
        raise ValueError("native_colour_format_not_yuv420p")
    source = {"frame": {}, "codec": {}}
    for scope, obj, fields in (
        ("frame", frame, ("colorspace", "color_range")),
        ("codec", codec_context, ("colorspace", "color_range", "color_primaries", "color_trc")),
    ):
        for field in fields:
            value = getattr(obj, field, None)
            # FFmpeg enum value 1 means BT.709 for matrix/primaries/transfer,
            # and MPEG/limited for range. Unknown is not a supported default.
            if isinstance(value, bool) or not isinstance(value, int) or value != 1:
                raise ValueError(f"native_colour_metadata_{scope}_{field}_expected_1_got_{value}")
            source[scope][field] = value
    converted = av.video.reformatter.VideoReformatter().reformat(
        frame, format=format, src_colorspace="ITU709", dst_colorspace="ITU709",
        src_color_range="MPEG", dst_color_range="JPEG",
        interpolation=SWS_BILINEAR | SWS_FULL_CHR_H_INT | SWS_ACCURATE_RND)
    provenance = {
        "schema_version": 1,
        "source_metadata": source,
        "source_pixel_format": "yuv420p",
        "source_matrix": "bt709", "source_range": "limited",
        "source_primaries": "bt709", "source_transfer": "bt709",
        "output_pixel_format": format, "output_range": "full",
        "output_primaries": "bt709", "output_transfer": "bt709",
        "method": "pyav_swscale_explicit_bt709_limited_to_full",
        "swscale_flags": ["BILINEAR", "FULL_CHR_H_INT", "ACCURATE_RND"],
        "pyav_version": av.__version__,
        "libswscale_version": list(av.library_versions["libswscale"]),
        "transfer_conversion": "none",
        "limitation": "Gamma-encoded BT.709 RGB; no sRGB transfer, display colour management, or linear-light conversion.",
    }
    return converted.to_ndarray(), provenance
