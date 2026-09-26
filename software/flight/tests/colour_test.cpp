#include "pv/colour.hpp"
#include <cassert>

int main() {
  // Absence must stay unknown after JSON serialization, including AWB state.
  auto missing = pv::Json::parse(pv::colour_metadata().dump());
  assert(missing.at("colour_gains").is_null());
  assert(missing.at("colour_temperature_k").is_null());
  assert(missing.at("colour_correction_matrix").is_null());
  assert(missing.at("awb_enabled").is_null());

  pv::ColourMetadata measured;
  measured.gains = {{1.25f, 2.5f}};
  measured.temperature_k = 4800;
  measured.awb_enabled = false;
  measured.correction_matrix = {{1.0f, -0.25f, 0.5f, -0.5f, 1.5f, 0.25f, 0.0f, -0.75f, 1.75f}};
  auto record = pv::Json::parse(pv::colour_metadata(measured).dump());
  assert(record.at("colour_gains") == pv::Json::array({1.25, 2.5}));
  assert(record.at("colour_temperature_k") == 4800);
  assert(record.at("awb_enabled") == false);  // Explicit false differs from missing.
  assert(record.at("colour_correction_matrix") == pv::Json({{1.0, -0.25, 0.5}, {-0.5, 1.5, 0.25}, {0.0, -0.75, 1.75}}));

  measured.awb_enabled.reset();
  assert(pv::colour_metadata(measured).at("awb_enabled").is_null());
  const auto provenance = pv::colour_control_provenance();
  assert(provenance.at("awb_enable_requested").is_null());
  assert(provenance.at("awb_default_enabled").is_null());
  assert(provenance.at("policy") == "vendor_defaults_not_overridden");
}
