#pragma once
#include "pv/config.hpp"
#include "pv/core.hpp"
#include <atomic>
#include <fstream>
#include <thread>

namespace pv {
class Logs {
 public:
  explicit Logs(const std::filesystem::path &path);
  ~Logs();
  void finish();
  void frame(Json value);
  void health(Json value);
  void segment(Json value);
  void event(const std::string &component, const std::string &event,
             const std::string &camera = "", const std::string &detail = "");
  std::uint64_t lost() const { return lost_.load(); }
  bool failed() const { return failed_.load(); }
 private:
  struct Item { int file; Json value; };
  void append(int file, Json value);
  BoundedQueue<Item> queue_{8192};
  std::ofstream files_[3];
  std::thread worker_;
  std::atomic<std::uint64_t> lost_{0};
  std::atomic<bool> failed_{false};
};
Json system_health();
}  // namespace pv
