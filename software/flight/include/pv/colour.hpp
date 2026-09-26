#pragma once
#include "pv/config.hpp"
#include <array>
#include <optional>

namespace pv {
struct ColourMetadata {
  std::optional<std::array<float, 2>> gains;
  std::optional<std::int32_t> temperature_k;
  std::optional<std::array<float, 9>> correction_matrix;
  std::optional<bool> awb_enabled;
};

inline Json colour_metadata(const ColourMetadata &values = {}) {
  Json result{{"colour_gains", nullptr}, {"colour_temperature_k", nullptr},
              {"colour_correction_matrix", nullptr}, {"awb_enabled", nullptr}};
  if (values.gains) result["colour_gains"] = *values.gains;  // Red, blue; green is the reference.
  if (values.temperature_k) result["colour_temperature_k"] = *values.temperature_k;
  if (values.awb_enabled) result["awb_enabled"] = *values.awb_enabled;
  if (values.correction_matrix) {
    const auto &m = *values.correction_matrix;
    result["colour_correction_matrix"] = {{m[0], m[1], m[2]}, {m[3], m[4], m[5]}, {m[6], m[7], m[8]}};
  }
  return result;
}

inline Json colour_control_provenance() {
  return {{"awb_enable_requested", nullptr}, {"awb_mode_requested", nullptr},
          {"colour_gains_requested", nullptr}, {"colour_temperature_k_requested", nullptr},
          {"colour_correction_matrix_requested", nullptr}, {"awb_default_enabled", nullptr},
          {"policy", "vendor_defaults_not_overridden"},
          {"note", "Capture requests no AWB or colour overrides; defaults are unverified. Per-frame reported values may be absent."}};
}
}  // namespace pv
