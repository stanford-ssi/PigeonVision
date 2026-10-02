#pragma once
#include <filesystem>
#include <fstream>
#include <random>
#include <stdexcept>
#ifdef __linux__
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif
namespace pv {
inline std::string session_id() {
  std::random_device random;
  std::string value = "session-";
  constexpr char hex[] = "0123456789abcdef";
  for (int i = 0; i < 32; ++i)
    value += hex[random() & 15];
  return value;
}
class CaptureLock {
public:
  explicit CaptureLock(const std::filesystem::path &path) {
#ifdef __linux__
    fd_ = ::open(path.c_str(), O_CREAT | O_RDONLY | O_CLOEXEC | O_NOFOLLOW, 0644);
    if (fd_ < 0 || flock(fd_, LOCK_EX | LOCK_NB) < 0) {
      if (fd_ >= 0)
        ::close(fd_);
      fd_ = -1;
      throw std::runtime_error("capture lock unavailable: " + path.string());
    }
#else
    (void)path;
#endif
  }
  ~CaptureLock() {
#ifdef __linux__
    if (fd_ >= 0)
      ::close(fd_);
#endif
  }
  CaptureLock(const CaptureLock &) = delete;
  CaptureLock &operator=(const CaptureLock &) = delete;

private:
  [[maybe_unused]] int fd_ = -1;
};
} // namespace pv
