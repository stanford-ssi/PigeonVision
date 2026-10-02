#pragma once
#include <chrono>
#include <cstdint>
#include <ctime>
#include <system_error>
namespace pv {
// BOOTTIME matches libcamera/main on Linux and includes suspended time.
inline std::int64_t acquisition_us() {
#ifdef __linux__
  timespec time{};
  if (clock_gettime(CLOCK_BOOTTIME, &time) != 0)
    throw std::system_error(errno, std::generic_category(), "clock_gettime(CLOCK_BOOTTIME)");
  return std::int64_t(time.tv_sec) * 1000000 + time.tv_nsec / 1000;
#else
  return std::chrono::duration_cast<std::chrono::microseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
#endif
}
inline const char *acquisition_clock_domain() {
#ifdef __linux__
  return "CLOCK_BOOTTIME";
#else
  return "CLOCK_MONOTONIC";
#endif
}
} // namespace pv
