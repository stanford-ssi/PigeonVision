#include "pv/config.hpp"
#include <cassert>
#include <fstream>
#include <iostream>
#include <unistd.h>
int main() {
  auto file = std::filesystem::temp_directory_path() / ("pv-config-test-"+std::to_string(getpid())+".json");
  pv::Json valid{{"schema_version",1}, {"session_dir","output/config-test"},
    {"cameras",{{{"id","A"},{"device","physical-a"}}}}, {"encode",false}, {"record",false}};
  auto write = [&](const pv::Json &value) { std::ofstream stream(file); stream << value; };
  write(valid);
  auto config = pv::Config::read(file);
  assert(config.session_dir == std::filesystem::current_path()/"output/config-test");
  assert(!config.encode && config.cameras[0].flip_y && !config.cameras[0].flip_x);
  assert(config.encoder_threads == 2 && config.effective().at("encoder_threads") == 2);
  assert(config.encoder_input == "dmabuf" && config.effective().at("encoder_input") == "dmabuf");
  assert(config.capture_allocator == "libcamera" && config.effective().at("capture_allocator") == "libcamera");
  assert(config.camera_controls.empty() && !config.effective().contains("camera_controls"));
  const pv::Json controls{{"exposure_us", 9993}, {"analogue_gain", 1.25}, {"colour_gains", {1.5, 2.25}},
    {"colour_correction_matrix", {{2.0,-0.75,-0.25}, {-0.5,2.0,-0.5}, {0.0,-1.0,2.0}}}};
  for (const auto &setting : pv::Json::array({controls,
      pv::Json{{"exposure_us",9993},{"analogue_gain",1.25}}, pv::Json{{"colour_gains",{1.5,2.25}}}})) {
    auto j = valid; j["camera_controls"] = setting; write(j);
    const auto manual = pv::Config::read(file);
    assert(!manual.camera_controls.empty());
    assert(manual.effective().at("camera_controls") == setting);
  }
  for (const auto *mode : {"libcamera", "dma_heap_cached"}) {
    auto j = valid; j["capture_allocator"] = mode; write(j);
    const auto configured = pv::Config::read(file);
    assert(configured.capture_allocator == mode && configured.effective().at("capture_allocator") == mode);
  }
  for (const auto *mode : {"dmabuf", "copy"}) {
    auto j = valid; j["encoder_input"] = mode; write(j);
    const auto configured = pv::Config::read(file);
    assert(configured.encoder_input == mode && configured.effective().at("encoder_input") == mode);
  }
  for (unsigned threads : {1u, 3u, 4u, 8u}) {
    auto j = valid; j["encoder_threads"] = threads; write(j);
    const auto configured = pv::Config::read(file);
    assert(configured.encoder_threads == threads);
    assert(configured.effective().at("encoder_threads") == threads);
  }
  auto rejects = [&](const char *key, pv::Json bad) {
    auto j = valid; j[key] = bad; write(j); bool rejected = false;
    try { (void)pv::Config::read(file); } catch (const std::exception &) { rejected = true; }
    assert(rejected);
  };
  rejects("width",1552.5); rejects("height",-1); rejects("fps",true);
  rejects("bitrate",100000001); rejects("min_free_bytes",-1); rejects("mux_bitrate",0);
  rejects("duration_seconds",nullptr); rejects("duration_seconds",false);
  rejects("schema_version",2); rejects("width",1551); rejects("record",true);
  rejects("encoder_threads",0); rejects("encoder_threads",9); rejects("encoder_threads",-1);
  rejects("encoder_threads",2.0); rejects("encoder_threads",true);
  rejects("encoder_threads",nullptr); rejects("encoder_threads","3");
  for (const auto &bad : pv::Json::array({nullptr, true, 1, 2.0, "", "cached", "COPY", pv::Json::array(), pv::Json::object()}))
    rejects("encoder_input", bad);
  for (const auto &bad : pv::Json::array({nullptr, true, 1, 2.0, "", "cached", "system", "LIBCAMERA", pv::Json::array(), pv::Json::object()}))
    rejects("capture_allocator", bad);
  for (const auto &bad : pv::Json::array({nullptr, true, "manual", pv::Json::array(), pv::Json::object(),
      pv::Json{{"exposure_us",9993}}, pv::Json{{"analogue_gain",1.0}},
      pv::Json{{"colour_gains",{1.0,2.0}},{"colour_gain",1.0}},
      pv::Json{{"colour_correction_matrix",{{1,0,0},{0,1,0},{0,0,1}}}}}))
    rejects("camera_controls", bad);
  for (const auto &value : pv::Json::array({0, -1, 9993.0, true, nullptr, 2147483648LL})) {
    auto bad = controls; bad["exposure_us"] = value; rejects("camera_controls", bad);
  }
  for (const auto &value : pv::Json::array({0, -1, true, nullptr, "1.0", 1e300, 1e-300})) {
    auto bad = controls; bad["analogue_gain"] = value; rejects("camera_controls", bad);
    bad = controls; bad["colour_gains"][0] = value; rejects("camera_controls", bad);
  }
  for (const auto &value : pv::Json::array({nullptr, 1.0, {1.0}, {1.0,2.0,3.0}})) {
    auto bad = controls; bad["colour_gains"] = value; rejects("camera_controls", bad);
  }
  for (const auto &value : pv::Json::array({-8.01, 8.0, 7.999999999, true, nullptr, "0"})) {
    auto bad = controls; bad["colour_correction_matrix"][0][0] = value; rejects("camera_controls", bad);
  }
  for (const auto &value : pv::Json::array({{1,0,0}, {{1,0,0},{0,1,0}}, {{1,0},{0,1,0},{0,0,1}}})) {
    auto bad = controls; bad["colour_correction_matrix"] = value; rejects("camera_controls", bad);
  }
  rejects("cameras",{{{"id","A"},{"device","same"}},{{"id","B"},{"device","same"}}});
  std::filesystem::remove(file);
  std::cout << "PASS: strict config types, bounds, unique device identities, capture-only and CWD paths\n";
}
