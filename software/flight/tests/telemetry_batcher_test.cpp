#include "pv/telemetry_batcher.hpp"

#include <cassert>
void budget();
int main() {
  budget();
  pv::TelemetryBatcher b;
  for (int i = 0; i < 24; ++i)
    b.add({{"source", i % 2 ? "bmi088_accel" : "bmi088_gyro"},
           {"backend", "i2c"},
           {"timestamp_basis", "host_read"},
           {"missed_polls", 0},
           {"sequence", i},
           {"monotonic_us", 123456 + i},
           {"read_start_monotonic_us", 123450 + i},
           {"valid", true},
           {"status", "fresh"},
           {"values", {{"accel_m_s2", {1.23, 2.34, 3.45}}}},
           {"errors", 0},
           {"last_good_monotonic_us", 123456 + i}});
  auto batches = b.take("test", 100);
  int samples = 0;
  for (const auto &batch : batches) {
    assert(batch.dump().size() <= 2500);
    assert(batch["clock_domain"] == "CLOCK_BOOTTIME");
    assert(batch["acquisition_clock_domain"] == "CLOCK_BOOTTIME");
    assert(batch["backend"] == "i2c" && batch["timestamp_basis"] == "host_read");
    for (const auto &source : batch["sources"])
      for (const auto &row : source["rows"]) {
        assert(row[1].get<int>() + source["base_monotonic_us"].get<int>() ==
               123456 + row[0].get<int>());
        ++samples;
      }
  }
  assert(samples == 24 && b.take("test", 200).empty());
  b.add({{"source", "bmi088_accel"},
         {"backend", "simulation"},
         {"timestamp_basis", "host_read"},
         {"missed_polls", 3},
         {"monotonic_us", 100},
         {"sequence", 1},
         {"valid", true},
         {"status", "fresh"},
         {"values", {{"accel_m_s2", {0, 0, 9.80665}}, {"saturated", false}}}});
  b.add({{"source", "bmi088_accel"},
         {"backend", "i2c"},
         {"timestamp_basis", "host_read"},
         {"missed_polls", 5},
         {"monotonic_us", 200},
         {"sequence", 2},
         {"valid", true},
         {"status", "fresh"},
         {"values", {{"accel_m_s2", {0, 0, 9.80665}}, {"saturated", true}}}});
  auto metadata = b.take("mixed", 0);
  assert(metadata.size() == 1);
  assert(metadata[0]["backend"] == "simulation" && metadata[0]["timestamp_basis"] == "host_read");
  auto groups = metadata[0]["sources"];
  assert(groups.size() == 2 && groups[0]["missed_polls"] == 3 && groups[1]["missed_polls"] == 5);
  assert(groups[1]["backend"] == "i2c" && groups[0]["shared_values"]["saturated"] == false &&
         groups[1]["shared_values"]["saturated"] == true);
}

#include <iostream>
void budget() {
  pv::TelemetryBatcher batcher;
  std::size_t bytes = 0, records = 0;
  for (int tick = 0; tick < 10; ++tick) {
    for (int source = 0; source < 4; ++source) {
      const int count = source < 2 ? 10 : source == 2 ? (tick % 2 ? 3 : 2) : 1;
      for (int i = 0; i < count; ++i) {
        pv::Json values;
        if (source < 2)
          values = {{source ? "gyro_rad_s" : "accel_m_s2",
                     {1.2345678901234567, -2.3456789012345678, 3.4567890123456789}},
                    {"saturated", false}};
        if (source == 1)
          values["fifo_overrun"] = false;
        if (source == 2)
          values = {{"pressure_pa", 98765.432123456},
                    {"temperature_c", 23.456789012345},
                    {"in_operating_pressure_range", true}};
        else if (source == 3)
          values = {{"bus_voltage_v", 7.234567890123}, {"shunt_voltage_v", .00123456789},
                    {"current_a", 1.23456789},         {"power_w", 8.9123456789},
                    {"power_basis", "power_register"}, {"saturated", false}};
        const auto timestamp = 123456789012LL + tick * 100000 + i * 10000;
        batcher.add({{"source", source == 0   ? "bmi088_accel"
                                : source == 1 ? "bmi088_gyro"
                                : source == 2 ? "bmp581"
                                              : "ina226"},
                     {"backend", "i2c"},
                     {"timestamp_basis", "host_read"},
                     {"missed_polls", 0},
                     {"sequence", tick * 10 + i},
                     {"monotonic_us", timestamp},
                     {"read_start_monotonic_us", timestamp - 100},
                     {"valid", true},
                     {"status", "fresh"},
                     {"values", values},
                     {"errors", 0},
                     {"last_good_monotonic_us", timestamp}});
      }
    }
    for (const auto &batch :
         batcher.take("session-01234567890123456789012345678901", tick * 100000)) {
      const auto length = batch.dump().size();
      assert(length <= 2500);
      bytes += ((length + 32 + 183) / 184) * 188;
      ++records;
    }
  }
  // Reserve three TS packets per 10 Hz FC status record, independently of sensors.
  assert((bytes + 10 * 564) * 8 <= 250000);
  std::cout << bytes << " TS sensor bytes/s; " << records << " records; " << (bytes + 10 * 564) * 8
            << " bit/s including FC allowance\n";
}
