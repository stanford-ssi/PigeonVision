#pragma once
#include <nlohmann/json.hpp>
#include <array>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace pv {
using Json = nlohmann::json;
struct CameraConfig {
  std::string id, device;
  bool flip_x = false, flip_y = true;
};
struct CameraControls {
  std::optional<std::int32_t> exposure_us;
  std::optional<float> analogue_gain;
  std::optional<std::array<float, 2>> colour_gains;
  std::optional<std::array<float, 9>> colour_correction_matrix;
  bool empty() const { return !exposure_us && !colour_gains; }
  Json requested() const {
    Json result = Json::object();
    if (exposure_us) { result["exposure_us"] = *exposure_us; result["analogue_gain"] = *analogue_gain; }
    if (colour_gains) result["colour_gains"] = *colour_gains;
    if (colour_correction_matrix) {
      const auto &m = *colour_correction_matrix;
      result["colour_correction_matrix"] = {{m[0],m[1],m[2]}, {m[3],m[4],m[5]}, {m[6],m[7],m[8]}};
    }
    return result;
  }
};
struct Config {
  std::filesystem::path session_dir;
  std::vector<CameraConfig> cameras;
  CameraControls camera_controls;
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
