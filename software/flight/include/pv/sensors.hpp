#pragma once
#include <array>
#include <cstdint>
#include <functional>
#include <memory>
#include <nlohmann/json.hpp>
namespace pv {
// I2C device paths and addresses must be supplied explicitly. No carrier pin map is assumed.
// backend: disabled (default), simulation (explicitly labelled), or i2c.
class SensorWorker {
public:
  using Callback = std::function<void(nlohmann::json)>;
  SensorWorker(const nlohmann::json &config, Callback callback);
  ~SensorWorker();
  void start();
  void stop();
  nlohmann::json stats() const;

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
namespace sensor_conversion {
inline constexpr std::uint16_t ina226_configuration = 0x0537;
inline constexpr std::uint16_t ina226_calibration = 1024;
std::array<double, 3> acceleration(const std::array<std::int16_t, 3> &raw); // ±24 g, m/s²
std::array<double, 3> angular_rate(const std::array<std::int16_t, 3> &raw); // ±2000 °/s, rad/s
nlohmann::json ina226(std::int16_t shunt, std::uint16_t bus, std::int16_t current,
                      std::uint16_t power);
} // namespace sensor_conversion
} // namespace pv
