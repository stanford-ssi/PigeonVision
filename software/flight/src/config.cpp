#include "pv/config.hpp"
#include <algorithm>
#include <cmath>
#include <ctime>
#include <fstream>
#include <iostream>
#include <limits>
#include <set>
#include <stdexcept>

namespace pv {
std::int64_t boot_ns() {
  timespec ts{};
  if (clock_gettime(CLOCK_BOOTTIME, &ts)) throw std::runtime_error("CLOCK_BOOTTIME unavailable");
  return std::int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}
std::string utc_now() {
  auto now = std::time(nullptr); std::tm tm{}; gmtime_r(&now, &tm);
  char value[32]; std::strftime(value, sizeof(value), "%Y-%m-%dT%H:%M:%SZ", &tm);
  return value;
}
Config Config::read(const std::filesystem::path &path) {
  std::ifstream file;
  if (path != "-") { file.open(path); if (!file) throw std::runtime_error("cannot open config: " + path.string()); }
  std::istream &input = path == "-" ? std::cin : file;
  Config c; input >> c.original; const auto &j = c.original;
  auto integer = [&](const char *name, std::uint64_t minimum, std::uint64_t maximum) {
    if (!j.contains(name)) return;
    const auto &v = j.at(name);
    if (!v.is_number_integer() || (!v.is_number_unsigned() && v.get<std::int64_t>() < 0))
      throw std::runtime_error(std::string(name) + " must be a nonnegative integer");
    const auto n = v.get<std::uint64_t>();
    if (n < minimum || n > maximum) throw std::runtime_error(std::string(name) + " is out of range");
  };
  integer("schema_version", 1, 1); integer("width",16,2064); integer("height",16,1552);
  integer("fps",1,120); integer("bitrate",10000,100000000); integer("vbv_bits",10000,100000000);
  integer("encoder_threads",1,8);
  integer("segment_seconds",1,86400); integer("mux_bitrate",1,100000000);
  integer("min_free_bytes",0,std::numeric_limits<std::uint64_t>::max());
  if (j.contains("duration_seconds") && !j["duration_seconds"].is_number()) throw std::runtime_error("duration_seconds must be numeric");
  if (j.value("schema_version", 1) != 1) throw std::runtime_error("unsupported configuration schema");
  c.session_dir = j.at("session_dir").get<std::string>();
  if (c.session_dir.empty()) throw std::runtime_error("session_dir is empty");
  // Matches CLI/stdin and remote execution: relative paths use execution CWD.
  c.session_dir = std::filesystem::absolute(c.session_dir);
  std::set<std::string> names, devices;
  for (const auto &camera : j.at("cameras")) {
    CameraConfig v{camera.at("id").get<std::string>(), camera.at("device").get<std::string>(),
                   camera.value("flip_x", false), camera.value("flip_y", true)};
    if ((v.id != "A" && v.id != "B") || !names.insert(v.id).second || v.device.empty() || !devices.insert(v.device).second)
      throw std::runtime_error("camera IDs must be distinct A/B with distinct explicit device IDs");
    c.cameras.push_back(std::move(v));
  }
  if (c.cameras.empty() || c.cameras.size() > 2) throw std::runtime_error("configure one or two cameras");
  if (j.contains("camera_controls")) {
    const auto &v = j.at("camera_controls");
    if (!v.is_object() || v.empty()) throw std::runtime_error("camera_controls must be a nonempty object");
    const std::set<std::string> keys{"exposure_us", "analogue_gain", "colour_gains", "colour_correction_matrix"};
    for (const auto &[key, _] : v.items()) if (!keys.contains(key)) throw std::runtime_error("unknown camera_controls key: " + key);
    if (v.contains("exposure_us") != v.contains("analogue_gain"))
      throw std::runtime_error("manual exposure requires both exposure_us and analogue_gain");
    auto number = [](const Json &value, bool positive) {
      if (!value.is_number()) throw std::runtime_error("camera control values must be finite numbers");
      const double n = value.get<double>();
      if (!std::isfinite(n) || (positive ? n <= 0 || n > std::numeric_limits<float>::max() : n < -8 || n >= 8))
        throw std::runtime_error("camera control value is out of range");
      const auto f = static_cast<float>(n);
      if (!std::isfinite(f) || (positive ? f <= 0 : f < -8 || f >= 8))
        throw std::runtime_error("camera control value cannot be represented in its supported float range");
      return f;
    };
    if (v.contains("exposure_us")) {
      const auto &e = v.at("exposure_us");
      if (!e.is_number_integer() || e.get<double>() < 1 || e.get<double>() > std::numeric_limits<std::int32_t>::max())
        throw std::runtime_error("exposure_us must be a positive int32 number of microseconds");
      c.camera_controls.exposure_us = e.get<std::int32_t>();
      c.camera_controls.analogue_gain = number(v.at("analogue_gain"), true);
    }
    if (v.contains("colour_gains")) {
      const auto &g = v.at("colour_gains");
      if (!g.is_array() || g.size() != 2) throw std::runtime_error("colour_gains must contain red and blue gains");
      c.camera_controls.colour_gains = {{number(g[0], true), number(g[1], true)}};
    }
    if (v.contains("colour_correction_matrix")) {
      if (!c.camera_controls.colour_gains) throw std::runtime_error("manual CCM requires manual colour_gains");
      const auto &m = v.at("colour_correction_matrix");
      if (!m.is_array() || m.size() != 3) throw std::runtime_error("colour_correction_matrix must be 3x3");
      std::array<float, 9> flat;
      for (std::size_t row = 0; row < 3; ++row) {
        if (!m[row].is_array() || m[row].size() != 3) throw std::runtime_error("colour_correction_matrix must be 3x3");
        for (std::size_t col = 0; col < 3; ++col) flat[row * 3 + col] = number(m[row][col], false);
      }
      c.camera_controls.colour_correction_matrix = flat;
    }
  }
  c.width = j.value("width", c.width); c.height = j.value("height", c.height); c.fps = j.value("fps", c.fps);
  c.bitrate = j.value("bitrate", c.bitrate); c.vbv_bits = j.value("vbv_bits", c.vbv_bits);
  c.preset = j.value("preset", c.preset); c.segment_seconds = j.value("segment_seconds", c.segment_seconds);
  c.encoder_threads = j.value("encoder_threads", c.encoder_threads);
  c.encoder_input = j.value("encoder_input", c.encoder_input);
  if (c.encoder_input != "dmabuf" && c.encoder_input != "copy")
    throw std::runtime_error("encoder_input must be dmabuf or copy");
  c.capture_allocator = j.value("capture_allocator", c.capture_allocator);
  if (c.capture_allocator != "libcamera" && c.capture_allocator != "dma_heap_cached")
    throw std::runtime_error("capture_allocator must be libcamera or dma_heap_cached");
  c.min_free_bytes = j.value("min_free_bytes", c.min_free_bytes);
  c.encode = j.value("encode", c.encode); c.record = j.value("record", c.record);
  if (j.contains("udp_destination") && !j["udp_destination"].is_null()) c.udp_destination = j["udp_destination"].get<std::string>();
  c.mux_bitrate = j.value("mux_bitrate", c.mux_bitrate);
  c.duration_seconds = j.value("duration_seconds", c.duration_seconds);
  const std::set<std::string> presets{"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"};
  if (c.width < 16 || c.height < 16 || c.width > 2064 || c.height > 1552 || c.width % 2 || c.height % 2 || c.fps < 1 || c.fps > 120)
    throw std::runtime_error("invalid YUV420 dimensions or frame rate");
  if (c.bitrate < 10000 || c.bitrate > 100000000 || c.vbv_bits < 10000 || c.vbv_bits > 100000000 || !presets.contains(c.preset))
    throw std::runtime_error("invalid encoder settings");
  if (!c.segment_seconds || c.segment_seconds > 86400 || !std::isfinite(c.duration_seconds) || c.duration_seconds < 0)
    throw std::runtime_error("invalid duration");
  if (!c.encode && (c.record || !c.udp_destination.empty())) throw std::runtime_error("capture-only requires record=false and no UDP destination");
  if (!c.udp_destination.empty() && (c.mux_bitrate < c.bitrate * static_cast<std::int64_t>(c.cameras.size()) + 500000 || c.mux_bitrate > 100000000))
    throw std::runtime_error("transport needs at least 500 kbit/s headroom above combined video bitrate");
  return c;
}
Json Config::effective() const {
  Json cams = Json::array();
  for (const auto &c : cameras) cams.push_back({{"id", c.id}, {"device", c.device}, {"flip_x", c.flip_x}, {"flip_y", c.flip_y}});
  Json result{{"schema_version", 1}, {"session_dir", session_dir.string()}, {"cameras", cams},
          {"width", width}, {"height", height}, {"fps", fps}, {"bitrate", bitrate}, {"vbv_bits", vbv_bits},
          {"preset", preset}, {"encoder_threads", encoder_threads}, {"encoder_input", encoder_input},
          {"capture_allocator", capture_allocator},
          {"segment_seconds", segment_seconds}, {"min_free_bytes", min_free_bytes},
          {"encode", encode}, {"record", record}, {"udp_destination", udp_destination.empty() ? Json(nullptr) : Json(udp_destination)},
          {"mux_bitrate", mux_bitrate}, {"duration_seconds", duration_seconds},
          {"sensor_mode", {{"width", 2064}, {"height", 1552}, {"bit_depth", 10}}}};
  if (!camera_controls.empty()) result["camera_controls"] = camera_controls.requested();
  return result;
}
}  // namespace pv
