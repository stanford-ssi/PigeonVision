#include "pv/framos.hpp"
#include <cassert>
#include <chrono>

template<class F> void rejects(F &&function) {
  bool rejected = false;
  try { function(); } catch (const std::exception &) { rejected = true; }
  assert(rejected);
}

int main() {
  const auto valid = [](unsigned fps) {
    return pv::validate_framos_rate(fps, 0x009819b1, "Frame rate", 1'000'000, 87'176'053, 1, true);
  };
  assert(valid(30) == 30'000'000 && valid(87) == 87'000'000);
  rejects([&] { valid(88); });
  rejects([&] { valid(0); });
  rejects([] { pv::validate_framos_rate(30, 0x009819b2, "Frame rate", 1'000'000, 90'000'000, 1, true); });
  rejects([] { pv::validate_framos_rate(30, 0x009819b1, "Exposure", 1'000'000, 90'000'000, 1, true); });
  rejects([] { pv::validate_framos_rate(30, 0x009819b1, "Frame rate", 1, 90'000'000, 1, true); });
  rejects([] { pv::validate_framos_rate(30, 0x009819b1, "Frame rate", 1'000'000, 90'000'000, 1, false); });

  namespace fs = std::filesystem;
  const auto root = fs::temp_directory_path() / ("pv-framos-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  struct Cleanup { fs::path root; ~Cleanup() { fs::remove_all(root); } } cleanup{root};
  const auto tree = root / "devicetree", entries = root / "video4linux", dev = root / "dev";
  const std::string a = "/base/axi/i2c@88000/imx900@1a", b = "/base/axi/i2c@70000/imx900@1a";
  fs::create_directories(tree / fs::path(a).relative_path());
  fs::create_directories(tree / fs::path(b).relative_path());
  auto add = [&](const std::string &name, const std::string &id) {
    fs::create_directories(entries / name / "device");
    fs::create_directory_symlink(tree / fs::path(id).relative_path(), entries / name / "device/of_node");
  };
  add("v4l-subdev0", b);
  rejects([&] { pv::resolve_sensor_node(a, entries, tree, dev); });
  add("v4l-subdev19", a);
  add("video19", a);  // A video node must never count as the sensor subdevice.
  const auto result = pv::resolve_sensor_node(a, entries, tree, dev);
  assert(result.device == dev / "v4l-subdev19");
  assert(result.of_node == fs::canonical(tree / fs::path(a).relative_path()));
  assert(pv::resolve_sensor_node(b, entries, tree, dev).device == dev / "v4l-subdev0");
  rejects([&] { pv::resolve_sensor_node("imx900@1a", entries, tree, dev); });
  rejects([&] { pv::resolve_sensor_node("/base/../base/axi/i2c@88000/imx900@1a", entries, tree, dev); });
  add("v4l-subdev23", a);
  rejects([&] { pv::resolve_sensor_node(a, entries, tree, dev); });
}
