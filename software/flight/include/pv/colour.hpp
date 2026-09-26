#pragma once
#include "pv/config.hpp"
#include <array>
#include <cmath>
#include <optional>
#include <stdexcept>

namespace pv {
struct ColourMetadata {
  std::optional<std::array<float, 2>> gains;
  std::optional<std::int32_t> temperature_k;
  std::optional<std::array<float, 9>> correction_matrix;
  std::optional<bool> awb_enabled;
  std::optional<bool> ae_enabled;
  std::optional<float> digital_gain;
  std::optional<std::int32_t> exposure_time_mode, analogue_gain_mode;
};

inline Json colour_metadata(const ColourMetadata &values = {}) {
  Json result{{"colour_gains", nullptr}, {"colour_temperature_k", nullptr},
              {"colour_correction_matrix", nullptr}, {"awb_enabled", nullptr},
              {"ae_enabled", nullptr}, {"digital_gain", nullptr},
              {"exposure_time_mode", nullptr}, {"analogue_gain_mode", nullptr}};
  if (values.gains) result["colour_gains"] = *values.gains;  // Red, blue; green is the reference.
  if (values.temperature_k) result["colour_temperature_k"] = *values.temperature_k;
  if (values.awb_enabled) result["awb_enabled"] = *values.awb_enabled;
  if (values.ae_enabled) result["ae_enabled"] = *values.ae_enabled;
  if (values.digital_gain) result["digital_gain"] = *values.digital_gain;
  if (values.exposure_time_mode) result["exposure_time_mode"] = *values.exposure_time_mode;
  if (values.analogue_gain_mode) result["analogue_gain_mode"] = *values.analogue_gain_mode;
  if (values.correction_matrix) {
    const auto &m = *values.correction_matrix;
    result["colour_correction_matrix"] = {{m[0], m[1], m[2]}, {m[3], m[4], m[5]}, {m[6], m[7], m[8]}};
  }
  return result;
}

inline void check_manual_control_range(const std::string &name, double value, double minimum, double maximum) {
  if (!std::isfinite(value) || !std::isfinite(minimum) || !std::isfinite(maximum) || minimum > maximum || value < minimum || value > maximum)
    throw std::runtime_error(name + " is outside the camera's reported range");
}

inline Json colour_control_provenance(const CameraControls &controls = {}) {
  Json result{{"awb_enable_requested", nullptr}, {"awb_mode_requested", nullptr},
          {"colour_gains_requested", nullptr}, {"colour_temperature_k_requested", nullptr},
          {"colour_correction_matrix_requested", nullptr}, {"awb_default_enabled", nullptr},
          {"ae_enable_requested", nullptr}, {"exposure_us_requested", nullptr}, {"analogue_gain_requested", nullptr},
          {"policy", "vendor_defaults_not_overridden"},
          {"note", "Capture requests no AWB or colour overrides; defaults are unverified. Per-frame reported values may be absent."}};
  if (!controls.empty()) {
    result["policy"] = "explicit_shared_camera_controls";
    result["note"] = "Requested controls are not measured results. Compare per-frame exposure/gain/WB/CCM; sensor quantization, digital gain and ISP processing may differ. Unreported values remain null.";
  }
  if (controls.exposure_us) {
    result["ae_enable_requested"] = false;
    result["exposure_us_requested"] = *controls.exposure_us;
    result["analogue_gain_requested"] = *controls.analogue_gain;
  }
  if (controls.colour_gains) {
    result["awb_enable_requested"] = false;
    result["colour_gains_requested"] = *controls.colour_gains;
  }
  if (controls.colour_correction_matrix) {
    result["colour_correction_matrix_requested"] = controls.requested().at("colour_correction_matrix");
    result["ccm_note"] = "Explicit base matrix; vendor saturation/lux processing can still alter the reported matrix.";
  }
  return result;
}
}  // namespace pv
