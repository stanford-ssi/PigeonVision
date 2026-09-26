#pragma once
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <stdexcept>

namespace pv {
struct TimingSnapshot {
  std::uint64_t samples = 0, total_ns = 0, minimum_ns = 0, maximum_ns = 0;
};

// Constant storage; a short snapshot lock keeps count/total/extrema coherent.
// The measured interval ends before entering this accounting lock.
class TimingCounter {
 public:
  void observe(std::chrono::nanoseconds elapsed) {
    if (elapsed.count() < 0) throw std::invalid_argument("negative timing duration");
    const auto value = static_cast<std::uint64_t>(elapsed.count());
    std::lock_guard lock(mutex_);
    if (!value_.samples) value_.minimum_ns = value;
    else value_.minimum_ns = std::min(value_.minimum_ns, value);
    value_.maximum_ns = std::max(value_.maximum_ns, value);
    value_.total_ns += value;
    ++value_.samples;
  }
  TimingSnapshot snapshot() const { std::lock_guard lock(mutex_); return value_; }
 private:
  mutable std::mutex mutex_;
  TimingSnapshot value_;
};

template<class F> int timed_codec_call(TimingCounter &counter, F &&operation) {
  const auto start = std::chrono::steady_clock::now();
  const int result = operation();
  counter.observe(std::chrono::steady_clock::now() - start);
  return result;
}
}  // namespace pv
