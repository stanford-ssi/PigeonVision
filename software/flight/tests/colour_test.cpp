#include "pv/colour.hpp"
#include <cassert>

int main() {
  // Absence must stay unknown after JSON serialization, including AWB state.
  auto missing = pv::Json::parse(pv::colour_metadata().dump());
  assert(missing.at("colour_gains").is_null());
  assert(missing.at("colour_temperature_k").is_null());
  assert(missing.at("colour_correction_matrix").is_null());
  assert(missing.at("awb_enabled").is_null());
  assert(missing.at("ae_enabled").is_null() && missing.at("digital_gain").is_null());
  assert(missing.at("exposure_time_mode").is_null() && missing.at("analogue_gain_mode").is_null());

  pv::ColourMetadata measured;
  measured.gains = {{1.25f, 2.5f}};
  measured.temperature_k = 4800;
  measured.awb_enabled = false;
  measured.ae_enabled = false;
  measured.digital_gain = 1.125f;
  measured.exposure_time_mode = 1;
  measured.analogue_gain_mode = 0;
  measured.correction_matrix = {{1.0f, -0.25f, 0.5f, -0.5f, 1.5f, 0.25f, 0.0f, -0.75f, 1.75f}};
  auto record = pv::Json::parse(pv::colour_metadata(measured).dump());
  assert(record.at("colour_gains") == pv::Json::array({1.25, 2.5}));
  assert(record.at("colour_temperature_k") == 4800);
  assert(record.at("awb_enabled") == false);  // Explicit false differs from missing.
  assert(record.at("ae_enabled") == false && record.at("digital_gain") == 1.125);
  assert(record.at("exposure_time_mode") == 1 && record.at("analogue_gain_mode") == 0);
  assert(record.at("colour_correction_matrix") == pv::Json({{1.0, -0.25, 0.5}, {-0.5, 1.5, 0.25}, {0.0, -0.75, 1.75}}));

  measured.awb_enabled.reset();
  assert(pv::colour_metadata(measured).at("awb_enabled").is_null());
  const auto provenance = pv::colour_control_provenance();
  assert(provenance.at("awb_enable_requested").is_null());
  assert(provenance.at("awb_default_enabled").is_null());
  assert(provenance.at("policy") == "vendor_defaults_not_overridden");
  assert(provenance.at("ae_enable_requested").is_null());
  pv::CameraControls requested;
  requested.exposure_us = 9993; requested.analogue_gain = 1.25f;
  requested.colour_gains = {{1.5f,2.25f}};
  requested.colour_correction_matrix = {{2,-.75f,-.25f,-.5f,2,-.5f,0,-1,2}};
  const auto manual = pv::colour_control_provenance(requested);
  assert(manual.at("ae_enable_requested") == false && manual.at("awb_enable_requested") == false);
  assert(manual.at("exposure_us_requested") == 9993 && manual.at("analogue_gain_requested") == 1.25);
  assert(manual.at("colour_gains_requested") == pv::Json({1.5,2.25}));
  assert(manual.at("colour_correction_matrix_requested") == requested.requested().at("colour_correction_matrix"));
  assert(manual.at("awb_default_enabled").is_null());
  assert(manual.at("policy") == "explicit_shared_camera_controls");
  // Requested controls cannot fill absent observed metadata.
  assert(pv::colour_metadata().at("digital_gain").is_null());
  for (double value : {1.0,2.0}) pv::check_manual_control_range("test",value,1,2);
  for (double value : {0.999,2.001}) {
    bool rejected = false;
    try { pv::check_manual_control_range("test",value,1,2); } catch (const std::exception &) { rejected = true; }
    assert(rejected);
  }
}
