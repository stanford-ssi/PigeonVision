#include "pv/sensors.hpp"

#include <array>
#include <cassert>
#include <cmath>
#include <mutex>
#include <thread>
#include <vector>

#include "../vendor/bmi08x/bmi08.h"
#include "../vendor/bmp5/bmp5.h"
static std::array<std::uint8_t, 256> registers{};
static std::int8_t read_reg(std::uint8_t reg, std::uint8_t *data, std::uint32_t len, void *) {
  for (std::uint32_t i = 0; i < len; ++i)
    data[i] = registers[reg + i];
  return 0;
}
static std::int8_t write_reg(std::uint8_t reg, const std::uint8_t *data, std::uint32_t len,
                             void *) {
  for (std::uint32_t i = 0; i < len; ++i)
    registers[reg + i] = data[i];
  return 0;
}
static void delay(std::uint32_t, void *) {
}
static bool near(double a, double b) {
  return std::abs(a - b) < 0.0001;
}
int main() {
  constexpr auto conf = pv::sensor_conversion::ina226_configuration;
  // TI table7-3: AVG binary010=16; CT tables: bus100=1.1ms,shunt110=4.156ms.
  static_assert(((conf >> 9) & 7) == 2 && ((conf >> 6) & 7) == 4 && ((conf >> 3) & 7) == 6 &&
                (conf & 7) == 7);
  static_assert(pv::sensor_conversion::ina226_calibration == 1024);
  assert(near(0.00512 / (0.001 * 0.005), pv::sensor_conversion::ina226_calibration));
  auto a = pv::sensor_conversion::acceleration({16384, -16384, 0});
  assert(near(a[0], 12 * 9.80665) && near(a[1], -12 * 9.80665));
  auto g = pv::sensor_conversion::angular_rate({16384, 0, -16384});
  assert(near(g[0], 1000 * 3.141592653589793 / 180) && near(g[2], -g[0]));
  auto ina = pv::sensor_conversion::ina226(4000, 9600, 2000, 960);
  assert(near(ina["shunt_voltage_v"], .01) && near(ina["bus_voltage_v"], 12) &&
         near(ina["current_a"], 2) && near(ina["power_w"], 24));
  auto negative = pv::sensor_conversion::ina226(-4000, 9600, -2000, 0);
  assert(near(negative["power_w"], -24));
  // Known register vectors exercise manufacturer byte decoding, not a duplicated decoder.
  bmi08_dev bmi{};
  bmi.intf = BMI08_I2C_INTF;
  bmi.read = read_reg;
  bmi.write = write_reg;
  bmi.delay_us = delay;
  bmi.intf_ptr_accel = &registers;
  bmi.intf_ptr_gyro = &registers;
  registers[0x12] = 0;
  registers[0x13] = 0x40;
  registers[0x14] = 0;
  registers[0x15] = 0xc0;
  registers[0x16] = 0xff;
  registers[0x17] = 0x7f;
  bmi08_sensor_data raw{};
  assert(bmi08a_get_data(&raw, &bmi) == 0);
  assert(raw.x == 16384 && raw.y == -16384 && raw.z == 32767);
  registers[2] = 0;
  registers[3] = 0x80;
  registers[4] = 0xff;
  registers[5] = 0xff;
  registers[6] = 0;
  registers[7] = 0;
  assert(bmi08g_get_data(&raw, &bmi) == 0);
  assert(raw.x == -32768 && raw.y == -1 && raw.z == 0);
  bmp5_dev bmp{};
  bmp.intf = BMP5_I2C_INTF;
  bmp.read = read_reg;
  bmp.write = write_reg;
  bmp.delay_us = delay;
  bmp.intf_ptr = &registers;
  // -10°C = signed 24-bit -655360; 100000Pa = unsigned 24-bit 6400000.
  registers[0x1d] = 0;
  registers[0x1e] = 0;
  registers[0x1f] = 0xf6;
  registers[0x20] = 0;
  registers[0x21] = 0xa8;
  registers[0x22] = 0x61;
  bmp5_osr_odr_press_config cfg{};
  cfg.press_en = 1;
  bmp5_sensor_data data{};
  assert(bmp5_get_sensor_data(&data, &cfg, &bmp) == 0);
  assert(near(data.temperature, -10) && near(data.pressure, 100000));
  std::mutex mutex;
  std::vector<nlohmann::json> records;
  pv::SensorWorker missing({{"backend", "i2c"}}, [&](auto r) {
    std::lock_guard l(mutex);
    records.push_back(r);
  });
  missing.start();
  std::this_thread::sleep_for(std::chrono::milliseconds(30));
  missing.stop();
  auto stats = missing.stats();
  assert(stats["devices"].size() == 4);
  for (auto &v : stats["devices"]) {
    assert(v["valid"] == false && v["values"].is_null() && v["status"] == "unconfigured");
  }
  pv::SensorWorker unavailable(
      {{"backend", "i2c"}, {"ina226", {{"device", "/nonexistent-pv-i2c"}, {"address", 64}}}},
      [](auto) {});
  unavailable.start();
  std::this_thread::sleep_for(std::chrono::milliseconds(140));
  unavailable.stop();
  auto device = unavailable.stats()["devices"]["ina226"];
  assert(device["valid"] == false && device["values"].is_null() && device["errors"] == 1 &&
         device["status"] == "retry_wait");
  // A failing backend retries without turning errors into valid zero samples.
  pv::SensorWorker retry(
      {{"backend", "i2c"}, {"ina226", {{"device", "/nonexistent-pv-i2c"}, {"address", 64}}}},
      [](auto) {});
  retry.start();
  std::this_thread::sleep_for(std::chrono::milliseconds(1130));
  retry.stop();
  assert(retry.stats()["devices"]["ina226"]["errors"].get<unsigned>() >= 2);
  records.clear();
  pv::SensorWorker sim({{"backend", "simulation"}, {"simulation_fail_after_ms", 30}}, [&](auto r) {
    std::lock_guard l(mutex);
    records.push_back(r);
  });
  sim.start();
  std::this_thread::sleep_for(std::chrono::milliseconds(140));
  sim.stop();
  bool good = false, failed = false;
  for (auto &r : records) {
    assert(r["backend"] == "simulation");
    if (r["source"] == "bmi088_accel") {
      if (r["valid"] == true)
        good = true;
      else if (r["status"] == "io_error") {
        failed = true;
        assert(r["values"].is_null() && !r["last_good_monotonic_us"].is_null());
      }
    }
  }
  assert(good && failed);
}
