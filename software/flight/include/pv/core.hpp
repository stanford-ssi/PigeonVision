#pragma once
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <utility>

namespace pv {
// A failed push leaves the caller's object intact. Closing wakes consumers and
// allows existing work to drain. No producer waits for a consumer or for space.
template<class T> class BoundedQueue {
 public:
  explicit BoundedQueue(std::size_t capacity) : capacity_(capacity) {
    if (!capacity) throw std::invalid_argument("queue capacity must be positive");
  }
  bool try_push(T &&value) {
    std::lock_guard lock(mutex_);
    if (closed_ || queue_.size() == capacity_) return false;
    queue_.push_back(std::move(value));
    if (queue_.size() > high_water_) high_water_ = queue_.size();
    ready_.notify_one();
    return true;
  }
  std::optional<T> pop() {
    std::unique_lock lock(mutex_);
    ready_.wait(lock, [&] { return closed_ || !queue_.empty(); });
    if (queue_.empty()) return std::nullopt;
    auto value = std::move(queue_.front());
    queue_.pop_front();
    return value;
  }
  void close() {
    std::lock_guard lock(mutex_);
    closed_ = true;
    ready_.notify_all();
  }
  std::size_t size() const { std::lock_guard lock(mutex_); return queue_.size(); }
  std::size_t high_water() const { std::lock_guard lock(mutex_); return high_water_; }
 private:
  const std::size_t capacity_;
  mutable std::mutex mutex_;
  std::condition_variable ready_;
  std::deque<T> queue_;
  std::size_t high_water_ = 0;
  bool closed_ = false;
};

inline std::int64_t relative_pts_us(std::int64_t timestamp_ns, std::int64_t origin_ns) {
  if (timestamp_ns < origin_ns) throw std::runtime_error("sensor timestamp precedes common clock origin");
  return (timestamp_ns - origin_ns) / 1000;
}

struct Crop { int x, y, width, height; };
inline Crop centered_crop(Crop available, unsigned width, unsigned height) {
  if (available.width < 2 || available.height < 2 || width < 2 || height < 2)
    throw std::invalid_argument("invalid crop dimensions");
  int cw = available.width, ch = available.height;
  if (static_cast<std::int64_t>(cw) * height > static_cast<std::int64_t>(ch) * width)
    cw = static_cast<int>(static_cast<std::int64_t>(ch) * width / height);
  else ch = static_cast<int>(static_cast<std::int64_t>(cw) * height / width);
  cw &= ~1; ch &= ~1;
  if (cw < 2 || ch < 2) throw std::invalid_argument("crop too small");
  return {available.x + ((available.width - cw) / 2 & ~1),
          available.y + ((available.height - ch) / 2 & ~1), cw, ch};
}

// Segment boundaries are keyframe-gated; gaps never become artificial frames.
inline bool segment_due(std::int64_t pts, std::int64_t first_pts,
                        std::int64_t duration_us, bool keyframe) {
  return keyframe && pts >= first_pts && pts - first_pts >= duration_us;
}
}  // namespace pv
