#include "pv/sensors.hpp"

#include <fcntl.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

#include "../vendor/bmi08x/bmi08.h"
#include "../vendor/bmp5/bmp5.h"
#include "pv/acquisition_clock.hpp"
#ifdef __linux__
#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <sys/ioctl.h>
#endif
namespace pv {
namespace sensor_conversion {
std::array<double, 3> acceleration(const std::array<std::int16_t, 3> &raw) {
  std::array<double, 3> v{};
  for (int i = 0; i < 3; ++i)
    v[i] = raw[i] * (24.0 * 9.80665 / 32768.0);
  return v;
}
std::array<double, 3> angular_rate(const std::array<std::int16_t, 3> &raw) {
  std::array<double, 3> v{};
  for (int i = 0; i < 3; ++i)
    v[i] = raw[i] * (2000.0 * 3.141592653589793 / 180.0 / 32768.0);
  return v;
}
nlohmann::json ina226(std::int16_t shunt, std::uint16_t bus, std::int16_t current,
                      std::uint16_t power) {
  return {{"shunt_voltage_v", shunt * 0.0000025},
          {"bus_voltage_v", bus * 0.00125},
          {"current_a", current * 0.001},
          {"power_w", current < 0 ? bus * 0.00125 * current * 0.001 : power * 0.025},
          {"power_basis", current < 0 ? "bus_times_current" : "power_register"},
          {"saturated", shunt == INT16_MIN || shunt == INT16_MAX || current == INT16_MIN ||
                            current == INT16_MAX}};
}
} // namespace sensor_conversion
namespace {
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
std::int64_t now_us() {
  return acquisition_us();
}
struct Bus {
  std::string path;
  int address = -1, fd = -1;
  ~Bus() { close_bus(); }
  void close_bus() {
    if (fd >= 0)
      close(fd);
    fd = -1;
  }
  bool open_bus() {
#ifdef __linux__
    if (path.empty() || address < 0x08 || address > 0x77)
      return false;
    fd = open(path.c_str(), O_RDWR | O_CLOEXEC);
    if (fd < 0)
      return false;
    // Kernel adapters may reject these controls; refusal is a fault rather than an unbounded retry
    // loop.
    if (ioctl(fd, I2C_TIMEOUT, 10) < 0 || ioctl(fd, I2C_RETRIES, 1) < 0) {
      close_bus();
      return false;
    }
    return true;
#else
    return false;
#endif
  }
  bool read(std::uint8_t reg, std::uint8_t *data, std::uint32_t len) {
#ifdef __linux__
    if (fd < 0 || len > 256)
      return false;
    i2c_msg msgs[2]{{static_cast<__u16>(address), 0, 1, &reg},
                    {static_cast<__u16>(address), I2C_M_RD, static_cast<__u16>(len), data}};
    i2c_rdwr_ioctl_data request{msgs, 2};
    return ioctl(fd, I2C_RDWR, &request) == 2;
#else
    (void)reg;
    (void)data;
    (void)len;
    return false;
#endif
  }
  bool write(std::uint8_t reg, const std::uint8_t *data, std::uint32_t len) {
#ifdef __linux__
    if (fd < 0 || len > 255)
      return false;
    std::vector<std::uint8_t> bytes{reg};
    bytes.insert(bytes.end(), data, data + len);
    i2c_msg message{static_cast<__u16>(address), 0, static_cast<__u16>(bytes.size()), bytes.data()};
    i2c_rdwr_ioctl_data request{&message, 1};
    return ioctl(fd, I2C_RDWR, &request) == 1;
#else
    (void)reg;
    (void)data;
    (void)len;
    return false;
#endif
  }
  std::uint8_t byte(std::uint8_t reg) {
    std::uint8_t x;
    if (!read(reg, &x, 1))
      throw std::runtime_error("read failed");
    return x;
  }
  void byte(std::uint8_t reg, std::uint8_t x) {
    if (!write(reg, &x, 1))
      throw std::runtime_error("write failed");
  }
  std::uint16_t word(std::uint8_t reg) {
    std::uint8_t b[2];
    if (!read(reg, b, 2))
      throw std::runtime_error("read failed");
    return std::uint16_t((b[0] << 8) | b[1]);
  }
  void word(std::uint8_t reg, std::uint16_t x) {
    std::uint8_t b[2]{std::uint8_t(x >> 8), std::uint8_t(x)};
    if (!write(reg, b, 2))
      throw std::runtime_error("write failed");
  }
};
std::int8_t read_cb(std::uint8_t reg, std::uint8_t *data, std::uint32_t len, void *ctx) {
  return static_cast<Bus *>(ctx)->read(reg, data, len) ? 0 : -1;
}
std::int8_t write_cb(std::uint8_t reg, const std::uint8_t *data, std::uint32_t len, void *ctx) {
  return static_cast<Bus *>(ctx)->write(reg, data, len) ? 0 : -1;
}
void delay_cb(std::uint32_t us, void *) {
  std::this_thread::sleep_for(std::chrono::microseconds(us));
}
void check(int result) {
  if (result != 0)
    throw std::runtime_error("SensorAPI result " + std::to_string(result));
}
struct Device {
  std::string name, status = "unconfigured";
  Bus bus;
  int interval_ms;
  bool initialized = false;
  Clock::time_point due{}, retry{};
  std::uint64_t sequence = 0, errors = 0, samples = 0, missed_polls = 0;
  std::int64_t last_good = 0;
  bmi08_dev bmi{};
  bmp5_dev bmp{};
  bmp5_osr_odr_press_config bmp_cfg{};
  std::vector<Json> acquired;
  Device(std::string n, int ms) : name(std::move(n)), interval_ms(ms) {}
  void initialize() {
    if (!bus.open_bus())
      throw std::runtime_error("I2C unavailable");
    bmi.intf = BMI08_I2C_INTF;
    bmi.variant = BMI088_VARIANT;
    bmi.intf_ptr_accel = &bus;
    bmi.intf_ptr_gyro = &bus;
    bmi.read = read_cb;
    bmi.write = write_cb;
    bmi.delay_us = delay_cb;
    bmi.read_write_len = 32;
    if (name == "bmi088_accel") {
      check(bmi08a_init(&bmi));
      if (bmi.accel_chip_id != 0x1e)
        throw std::runtime_error("wrong accel chip ID");
      bmi.accel_cfg.power = BMI08_ACCEL_PM_ACTIVE;
      check(bmi08a_set_power_mode(&bmi));
      bus.byte(0x7d, 4);
      delay_cb(50000, nullptr);
      bmi.accel_cfg.odr = BMI08_ACCEL_ODR_100_HZ;
      bmi.accel_cfg.bw = BMI08_ACCEL_BW_NORMAL;
      check(bmi08a_set_meas_conf(&bmi));
      bus.byte(0x41, 3);
      check(bmi08a_get_meas_conf(&bmi));
      if (bmi.accel_cfg.odr != 8 || bmi.accel_cfg.bw != 10 || bmi.accel_cfg.range != 3 ||
          bus.byte(0x7d) != 4)
        throw std::runtime_error("accel config readback");
    } else if (name == "bmi088_gyro") {
      check(bmi08g_init(&bmi));
      if (bmi.gyro_chip_id != 0x0f)
        throw std::runtime_error("wrong gyro chip ID");
      bmi.gyro_cfg.power = BMI08_GYRO_PM_NORMAL;
      check(bmi08g_set_power_mode(&bmi));
      bmi.gyro_cfg.odr = BMI08_GYRO_BW_32_ODR_100_HZ;
      bmi.gyro_cfg.range = BMI08_GYRO_RANGE_2000_DPS;
      check(bmi08g_set_meas_conf(&bmi));
      bus.byte(0x3e, 0);
      bus.byte(0x3e, 0x80);
      check(bmi08g_get_meas_conf(&bmi));
      if (bmi.gyro_cfg.odr != 7 || bmi.gyro_cfg.range != 0 || bus.byte(0x11) != 0 ||
          bus.byte(0x3e) != 0x80)
        throw std::runtime_error("gyro config readback");
    } else if (name == "bmp581") {
      bmp.intf = BMP5_I2C_INTF;
      bmp.intf_ptr = &bus;
      bmp.read = read_cb;
      bmp.write = write_cb;
      bmp.delay_us = delay_cb;
      check(bmp5_init(&bmp));
      if (bmp.chip_id != 0x50)
        throw std::runtime_error("wrong BMP581 chip ID");
      check(bmp5_set_power_mode(BMP5_POWERMODE_STANDBY, &bmp));
      bmp_cfg = {BMP5_OVERSAMPLING_1X, BMP5_OVERSAMPLING_8X, BMP5_ENABLE, BMP5_ODR_25_HZ};
      check(bmp5_set_osr_odr_press_config(&bmp_cfg, &bmp));
      struct bmp5_int_source_select source{};
      source.drdy_en = BMP5_ENABLE;
      check(bmp5_int_source_select(&source, &bmp));
      check(bmp5_set_power_mode(BMP5_POWERMODE_NORMAL, &bmp));
      bmp5_osr_odr_press_config readback{};
      check(bmp5_get_osr_odr_press_config(&readback, &bmp));
      bmp5_osr_odr_eff effective{};
      check(bmp5_get_osr_odr_eff(&effective, &bmp));
      if (readback.odr != BMP5_ODR_25_HZ || readback.osr_t != 0 || readback.osr_p != 3 ||
          !readback.press_en || !effective.odr_is_valid || effective.osr_p_eff != 3 ||
          effective.osr_t_eff != 0)
        throw std::runtime_error("BMP581 config readback");
    } else {
      if (bus.word(0xfe) != 0x5449 || (bus.word(0xff) & 0xfff0) != 0x2260)
        throw std::runtime_error("wrong INA226 ID");
      // AVG=16, VBUSCT=1.1 ms, VSHCT=4.156 ms: 16*(1.1+4.156)=84.096 ms.
      bus.word(0, sensor_conversion::ina226_configuration);
      bus.word(5, sensor_conversion::ina226_calibration);
      if (bus.word(0) != sensor_conversion::ina226_configuration ||
          bus.word(5) != sensor_conversion::ina226_calibration)
        throw std::runtime_error("INA226 config readback");
    }
    initialized = true;
    status = "initializing";
  }
  Json acquire() {
    if (name == "bmi088_accel") {
      std::uint8_t ready = 0;
      check(bmi08a_get_status(&ready, &bmi));
      if (!(ready & 0x80))
        return nullptr;
      bmi08_sensor_data raw{};
      check(bmi08a_get_data(&raw, &bmi));
      return {{"accel_m_s2", sensor_conversion::acceleration({raw.x, raw.y, raw.z})},
              {"saturated", raw.x == INT16_MIN || raw.x == INT16_MAX || raw.y == INT16_MIN ||
                                raw.y == INT16_MAX || raw.z == INT16_MIN || raw.z == INT16_MAX}};
    }
    if (name == "bmi088_gyro") { // The gyro DRDY status lasts only 280–400 us. FIFO occupancy
                                 // persists between Linux polls.
      auto fifo = bus.byte(0x0e);
      int count = fifo & 0x7f;
      acquired.clear();
      for (int frame = 0; frame < count; ++frame) {
        std::uint8_t bytes[6];
        if (!bus.read(0x3f, bytes, 6))
          throw std::runtime_error("gyro FIFO read failed");
        std::array<std::int16_t, 3> raw{};
        for (int axis = 0; axis < 3; ++axis)
          raw[axis] = std::int16_t(bytes[axis * 2] | (bytes[axis * 2 + 1] << 8));
        acquired.push_back(
            {{"gyro_rad_s", sensor_conversion::angular_rate(raw)},
             {"fifo_overrun", bool(fifo & 0x80)},
             {"saturated", raw[0] == INT16_MIN || raw[0] == INT16_MAX || raw[1] == INT16_MIN ||
                               raw[1] == INT16_MAX || raw[2] == INT16_MIN || raw[2] == INT16_MAX}});
      }
      if (acquired.empty())
        return nullptr;
      auto first = acquired.front();
      acquired.erase(acquired.begin());
      return first;
    }
    if (name == "bmp581") {
      std::uint8_t ready = 0;
      check(bmp5_get_interrupt_status(&ready, &bmp));
      if (!(ready & 1))
        return nullptr;
      bmp5_sensor_data data{};
      check(bmp5_get_sensor_data(&data, &bmp_cfg, &bmp));
      return {{"pressure_pa", data.pressure},
              {"temperature_c", data.temperature},
              {"in_operating_pressure_range", data.pressure >= 30000 && data.pressure <= 125000}};
    }
    auto ready = bus.word(6);
    if (ready & 4)
      throw std::runtime_error("INA226 math overflow");
    if (!(ready & 8))
      return nullptr;
    if (bus.word(5) != sensor_conversion::ina226_calibration)
      throw std::runtime_error("INA226 calibration lost");
    auto shunt = std::int16_t(bus.word(1));
    auto voltage = bus.word(2);
    auto current = std::int16_t(bus.word(4));
    auto power = bus.word(3);
    return sensor_conversion::ina226(shunt, voltage, current, power);
  }
};
} // namespace
struct SensorWorker::Impl {
  Json config;
  Callback callback;
  std::string backend;
  std::atomic<bool> running{false};
  std::thread thread;
  mutable std::mutex mutex;
  Json state = Json::object();
  Impl(const Json &c, Callback cb)
      : config(c), callback(std::move(cb)), backend(c.value("backend", std::string("disabled"))) {}
  void run() {
    std::vector<std::unique_ptr<Device>> devices;
    for (auto [name, ms] :
         {std::pair{"bmi088_accel", 10}, {"bmi088_gyro", 10}, {"bmp581", 40}, {"ina226", 100}}) {
      auto d = std::make_unique<Device>(name, ms);
      auto mapping = config.value(name, Json::object());
      d->bus.path = mapping.value("device", std::string{});
      d->bus.address = mapping.value("address", -1);
      devices.push_back(std::move(d));
    }
    auto origin = Clock::now();
    while (running) {
      auto now = Clock::now();
      for (auto &ptr : devices) {
        auto &d = *ptr;
        if (now < d.due)
          continue;
        if (d.due == Clock::time_point{})
          d.due = now;
        d.due += std::chrono::milliseconds(d.interval_ms);
        while (d.due <= now) {
          d.due += std::chrono::milliseconds(d.interval_ms);
          ++d.missed_polls;
        }
        Json values = nullptr;
        std::string error;
        auto started = now_us();
        if (backend == "disabled")
          d.status = "disabled";
        else if (backend == "simulation") {
          // Deterministic synthetic values, explicitly labelled and never a hardware fallback.
          auto elapsed =
              std::chrono::duration_cast<std::chrono::milliseconds>(now - origin).count();
          auto fail_ms = config.value("simulation_fail_after_ms", -1);
          if (fail_ms >= 0 && elapsed >= fail_ms) {
            d.status = "io_error";
            ++d.errors;
          } else {
            if (d.name == "bmi088_accel")
              values = {{"accel_m_s2", {0., 0., 9.80665}}};
            else if (d.name == "bmi088_gyro")
              values = {{"gyro_rad_s", {0., 0., 0.}}};
            else if (d.name == "bmp581")
              values = {{"pressure_pa", 101325.0}, {"temperature_c", 20.0}};
            else
              values = {{"bus_voltage_v", 12.0},
                        {"shunt_voltage_v", 0.01},
                        {"current_a", 2.0},
                        {"power_w", 24.0}};
            d.status = "fresh";
          }
        } else if (d.bus.path.empty() || d.bus.address < 8 || d.bus.address > 0x77) {
          d.status = "unconfigured";
        } else if (now < d.retry) {
          d.status = "retry_wait";
        } else {
          try {
            if (!d.initialized)
              d.initialize();
            values = d.acquire();
            d.status = values.is_null() ? "not_ready" : "fresh";
          } catch (const std::exception &e) {
            error = e.what();
            d.acquired.clear();
            ++d.errors;
            d.status = "io_error";
            d.initialized = false;
            d.bus.close_bus();
            d.retry = Clock::now() + std::chrono::seconds(1);
          }
        }
        auto ended = now_us();
        bool valid = !values.is_null();
        if (valid) {
          d.last_good = ended;
          ++d.samples;
        }
        Json record = {{"type", "sensor_sample"},
                       {"source", d.name},
                       {"backend", backend},
                       {"sequence", d.sequence++},
                       {"monotonic_us", ended},
                       {"read_start_monotonic_us", started},
                       {"timestamp_basis", "host_read"},
                       {"host_clock_domain", acquisition_clock_domain()},
                       {"valid", valid},
                       {"status", d.status},
                       {"values", values},
                       {"last_good_monotonic_us", d.last_good ? Json(d.last_good) : Json(nullptr)},
                       {"errors", d.errors},
                       {"missed_polls", d.missed_polls}};
        if (!error.empty())
          record["error"] = error;
        {
          std::lock_guard l(mutex);
          state[d.name] = record;
          state[d.name]["samples"] = d.samples;
        }
        // Disabled/unconfigured/backoff states are emitted at 1 Hz, not at the sensor sample rate.
        if (valid || d.status == "not_ready" || d.status == "io_error" || d.sequence == 1 ||
            d.sequence % std::uint64_t(1000 / d.interval_ms) == 0)
          callback(record);
        for (const auto &frame : d.acquired) {
          record["sequence"] = d.sequence++;
          record["values"] = frame;
          ++d.samples;
          {
            std::lock_guard l(mutex);
            state[d.name] = record;
            state[d.name]["samples"] = d.samples;
          }
          callback(record);
        }
        d.acquired.clear();
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  }
};
SensorWorker::SensorWorker(const Json &c, Callback cb)
    : impl_(std::make_unique<Impl>(c, std::move(cb))) {
  if (impl_->backend != "disabled" && impl_->backend != "simulation" && impl_->backend != "i2c")
    throw std::invalid_argument("sensors backend must be disabled, simulation or i2c");
  (void)c.value("simulation_fail_after_ms", -1);
  for (auto name : {"bmi088_accel", "bmi088_gyro", "bmp581", "ina226"}) {
    auto entry = c.value(name, Json::object());
    if (!entry.is_object())
      throw std::invalid_argument("sensor device config must be an object");
    (void)entry.value("device", std::string{});
    (void)entry.value("address", -1);
  }
}
SensorWorker::~SensorWorker() {
  stop();
}
void SensorWorker::start() {
  if (impl_->running.exchange(true))
    return;
  impl_->thread = std::thread([this] { impl_->run(); });
}
void SensorWorker::stop() {
  impl_->running = false;
  if (impl_->thread.joinable())
    impl_->thread.join();
}
Json SensorWorker::stats() const {
  std::lock_guard l(impl_->mutex);
  return {{"backend", impl_->backend}, {"devices", impl_->state}};
}
} // namespace pv
