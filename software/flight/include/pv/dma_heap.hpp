#pragma once
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

namespace pv {
inline constexpr char cached_video_heap[] = "/dev/dma_heap/vidbuf_cached";
inline constexpr unsigned dma_retry_attempts = 16;

// Retry transient sync errors, but never spin forever in an AVFrame release
// callback. At most 15 ms of intentional backoff; an individual kernel ioctl
// itself cannot be given a userspace execution-time guarantee here.
template<class Call> int retry_dma_operation(Call &&call) {
  for (unsigned attempt = 0; ; ++attempt) {
    const int result = call();
    if (result >= 0 || (errno != EINTR && errno != EAGAIN) || attempt + 1 == dma_retry_attempts)
      return result;
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}

// Kept independent of Linux/libcamera so failure ownership can be tested
// without a camera or heap device. Operations must outlive the returned FDs.
struct DmaHeapOps {
  std::function<int(const char *)> open;
  std::function<int(int, std::uint64_t)> allocate;
  std::function<void(int)> close;
};
const DmaHeapOps &system_dma_heap_ops();

class OwnedDmaFd {
 public:
  OwnedDmaFd(const DmaHeapOps &ops, int fd) : ops_(&ops), fd_(fd) {}
  ~OwnedDmaFd() { if (fd_ >= 0) ops_->close(fd_); }
  OwnedDmaFd(const OwnedDmaFd &) = delete;
  OwnedDmaFd &operator=(const OwnedDmaFd &) = delete;
  OwnedDmaFd(OwnedDmaFd &&other) noexcept : ops_(other.ops_), fd_(other.release()) {}
  OwnedDmaFd &operator=(OwnedDmaFd &&) = delete;
  int get() const { return fd_; }
  int release() noexcept { return std::exchange(fd_, -1); }
 private:
  const DmaHeapOps *ops_;
  int fd_;
};

class DmaHeap {
 public:
  explicit DmaHeap(const DmaHeapOps &ops = system_dma_heap_ops())
      : ops_(ops), heap_(ops, retry_dma_operation([&] { return ops.open(cached_video_heap); })) {
    if (heap_.get() < 0)
      throw std::system_error(errno, std::generic_category(), std::string("open ") + cached_video_heap);
  }
  OwnedDmaFd allocate(std::size_t bytes) const {
    if (!bytes) throw std::invalid_argument("DMA heap allocation size must be positive");
    int fd = retry_dma_operation([&] { return ops_.allocate(heap_.get(), bytes); });
    if (fd < 0) throw std::system_error(errno, std::generic_category(), "allocate cached DMA buffer");
    return OwnedDmaFd(ops_, fd);
  }
 private:
  const DmaHeapOps &ops_;
  OwnedDmaFd heap_;
};

// CPU cache synchronization is separate from device completion and ownership.
// A fault permanently quarantines this buffer. Even when START fails partway,
// every successful START is paired with an END before returning the error.
class DmaReadSync {
 public:
  using Operation = std::function<int(int, bool)>;
  DmaReadSync(std::vector<int> fds, Operation operation)
      : fds_(std::move(fds)), operation_(std::move(operation)) { started_.reserve(fds_.size()); }
  DmaReadSync(const DmaReadSync &) = delete;
  DmaReadSync &operator=(const DmaReadSync &) = delete;
  int begin() noexcept {
    if (failed_ || !started_.empty()) { failed_ = true; return EBUSY; }
    for (int fd : fds_) {
      if (const int error = call(fd, true)) {
        failed_ = true;
        (void)end();
        return error;
      }
      started_.push_back(fd); // capacity reserved before any CPU access begins
    }
    return 0;
  }
  int end() noexcept {
    int first_error = 0;
    for (int fd : started_)
      if (const int error = call(fd, false); error && !first_error) first_error = error;
    started_.clear();
    if (first_error) failed_ = true;
    return first_error;
  }
  bool failed() const noexcept { return failed_; }
 private:
  int call(int fd, bool start) noexcept {
    try {
      return retry_dma_operation([&] { return operation_(fd, start); }) < 0 ? errno : 0;
    } catch (...) { return EIO; }
  }
  std::vector<int> fds_, started_;
  Operation operation_;
  bool failed_ = false;
};

struct Yuv420Layout { std::size_t y_bytes, uv_bytes, total_bytes; };
inline Yuv420Layout checked_yuv420_layout(unsigned width, unsigned height,
                                         unsigned stride, std::size_t frame_size) {
  if (!width || !height || width % 2 || height % 2 || stride < width || stride % 2)
    throw std::runtime_error("invalid negotiated YUV420 dimensions or stride");
  const std::uint64_t y = std::uint64_t(stride) * height;
  const std::uint64_t uv = std::uint64_t(stride / 2) * (height / 2);
  if (y > std::numeric_limits<std::size_t>::max() ||
      uv > (std::numeric_limits<std::size_t>::max() - y) / 2 || y + 2 * uv > frame_size)
    throw std::runtime_error("negotiated frame size is shorter than padded YUV420 layout");
  return {std::size_t(y), std::size_t(uv), std::size_t(y + 2 * uv)};
}
}  // namespace pv
