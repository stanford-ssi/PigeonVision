#pragma once
#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>

namespace pv {
// Pinned FRAMOS fr_imx900.c: V4L2_CID_USER_IMX_BASE + 1, microframes/s.
inline constexpr std::uint32_t framos_frame_rate_id = 0x009819b1;

struct SensorNode {
  std::filesystem::path device;
  std::filesystem::path of_node;
};

// A Raspberry Pi libcamera ID is the exact OF path, including /base. Never
// infer a /dev/v4l-subdevN index from camera ordering or a partial I2C name.
inline SensorNode resolve_sensor_node(
    const std::string &camera_id,
    const std::filesystem::path &video_class = "/sys/class/video4linux",
    const std::filesystem::path &device_tree = "/sys/firmware/devicetree",
    const std::filesystem::path &devices = "/dev") {
  if (!camera_id.starts_with("/base/"))
    throw std::runtime_error("FRAMOS camera ID is not an absolute /base device-tree path: " + camera_id);
  const std::filesystem::path identity(camera_id);
  for (const auto &part : identity)
    if (part == "." || part == "..") throw std::runtime_error("invalid camera device-tree path");
  const auto expected = std::filesystem::canonical(device_tree / identity.relative_path());
  SensorNode result;
  unsigned matches = 0;
  for (const auto &entry : std::filesystem::directory_iterator(video_class)) {
    const auto name = entry.path().filename().string();
    constexpr std::string_view prefix = "v4l-subdev";
    if (!name.starts_with(prefix) || name.size() == prefix.size() ||
        name.find_first_not_of("0123456789", prefix.size()) != std::string::npos) continue;
    std::error_code error;
    auto node = std::filesystem::canonical(entry.path() / "device/of_node", error);
    if (!error && node == expected) { result = {devices / name, node}; ++matches; }
  }
  if (matches != 1)
    throw std::runtime_error("expected exactly one sensor subdevice matching " + camera_id +
                             ", found " + std::to_string(matches));
  return result;
}

inline std::int32_t validate_framos_rate(unsigned fps, std::uint32_t id,
    std::string_view name, std::int32_t minimum, std::int32_t maximum,
    std::int32_t step, bool writable_integer) {
  if (id != framos_frame_rate_id || name != "Frame rate" || !writable_integer ||
      minimum != 1'000'000 || maximum < minimum || step <= 0)
    throw std::runtime_error("sensor does not expose the pinned FRAMOS micro-fps Frame rate control");
  const auto rate = std::uint64_t(fps) * 1'000'000;
  if (rate < std::uint64_t(minimum) || rate > std::uint64_t(maximum) ||
      (rate - minimum) % step)
    throw std::runtime_error("requested fps is outside the selected FRAMOS sensor mode's control range");
  return static_cast<std::int32_t>(rate);
}
}  // namespace pv
