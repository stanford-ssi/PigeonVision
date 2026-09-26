#pragma once
#include <nlohmann/json.hpp>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace pv {
using Json = nlohmann::json;
struct CameraConfig {
  std::string id, device;
  bool flip_x = false, flip_y = true;
};
struct Config {
  std::filesystem::path session_dir;
  std::vector<CameraConfig> cameras;
  unsigned width = 1552, height = 1552, fps = 30;
  std::int64_t bitrate = 4000000, vbv_bits = 2000000;
  std::string preset = "ultrafast";
  unsigned encoder_threads = 2;
  std::string encoder_input = "dmabuf";
  std::string capture_allocator = "libcamera";
  unsigned segment_seconds = 60;
  std::uintmax_t min_free_bytes = 2147483648ULL;
  bool encode = true, record = true;
  std::string udp_destination;
  std::int64_t mux_bitrate = 9000000;
  double duration_seconds = 0;
  Json original;
  static Config read(const std::filesystem::path &path);
  Json effective() const;
};
std::int64_t boot_ns();
std::string utc_now();
}  // namespace pv
