#include "pv/log.hpp"
#include <cstdio>
#include <iostream>
#include <sstream>

namespace pv {
Logs::Logs(const std::filesystem::path &path) {
  const char *names[]{"frames.jsonl", "health.jsonl", "segments.jsonl"};
  for (int i = 0; i < 3; ++i) {
    files_[i].open(path / names[i]);
    if (!files_[i]) throw std::runtime_error("cannot open session log");
  }
  worker_ = std::thread([this] {
    while (auto item = queue_.pop()) {
      files_[item->file] << item->value.dump() << '\n';
      // One flush per record makes timing/drop evidence available while running.
      files_[item->file].flush();
      if (!files_[item->file] && !failed_.exchange(true)) std::cerr << "session log write failed\n";
    }
  });
}
Logs::~Logs() { finish(); }
void Logs::finish() { queue_.close(); if (worker_.joinable()) worker_.join(); }
void Logs::append(int file, Json value) {
  if (!queue_.try_push(Item{file, std::move(value)})) ++lost_;
}
void Logs::frame(Json value) { append(0, std::move(value)); }
void Logs::health(Json value) {
  value["schema_version"] = 1; value["timestamp_ns"] = boot_ns(); value["clock_domain"] = "CLOCK_BOOTTIME";
  append(1, std::move(value));
}
void Logs::segment(Json value) { value["schema_version"] = 1; append(2, std::move(value)); }
void Logs::event(const std::string &component, const std::string &event,
                 const std::string &camera, const std::string &detail) {
  health({{"type", "event"}, {"component", component}, {"event", event},
          {"camera_id", camera.empty() ? Json(nullptr) : Json(camera)}, {"detail", detail}});
}
Json system_health() {
  Json value{{"type", "sample"}, {"temperature_c", nullptr}, {"cpu_clock_khz", nullptr},
             {"cpu_busy_percent", nullptr}, {"memory_available_bytes", nullptr},
             {"throttled_bits", nullptr}, {"undervoltage", nullptr}};
  long number;
  std::ifstream thermal("/sys/class/thermal/thermal_zone0/temp");
  if (thermal >> number) value["temperature_c"] = number / 1000.0;
  std::ifstream freq("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq");
  if (freq >> number) value["cpu_clock_khz"] = number;
  std::ifstream memory("/proc/meminfo"); std::string line;
  while (std::getline(memory, line)) if (line.starts_with("MemAvailable:")) {
    std::istringstream s(line.substr(13)); if (s >> number) value["memory_available_bytes"] = number * 1024LL;
  }
  std::ifstream stat("/proc/stat");
  if (std::getline(stat, line)) {
    std::istringstream s(line); std::string name; s >> name;
    std::uint64_t fields[10]{}; for (auto &field : fields) s >> field;
    // guest and guest_nice are already included in user/nice.
    std::uint64_t total = 0; for (int i = 0; i < 8; ++i) total += fields[i];
    const auto idle = fields[3] + fields[4];
    static std::uint64_t previous_total = 0, previous_idle = 0;
    if (previous_total && total > previous_total)
      value["cpu_busy_percent"] = 100.0 * (1.0 - double(idle - previous_idle) / double(total - previous_total));
    previous_total = total; previous_idle = idle;
  }
  if (std::filesystem::exists("/usr/bin/vcgencmd")) {
    if (FILE *pipe = popen("/usr/bin/vcgencmd get_throttled 2>/dev/null", "r")) {
      char result[128]{}; unsigned flags;
      if (fgets(result, sizeof(result), pipe) && std::sscanf(result, "throttled=0x%x", &flags) == 1) {
        value["throttled_bits"] = flags; value["undervoltage"] = bool(flags & 1);
      }
      pclose(pipe);
    }
  }
  return value;
}
}  // namespace pv
