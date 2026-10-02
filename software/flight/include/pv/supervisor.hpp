#pragma once
#include <map>
#include <stdexcept>
#include <string>
namespace pv {
enum class Lifecycle { boot, initializing, running, stopping, stopped };
inline const char *name(Lifecycle s) {
  switch (s) {
  case Lifecycle::boot:
    return "boot";
  case Lifecycle::initializing:
    return "initializing";
  case Lifecycle::running:
    return "running";
  case Lifecycle::stopping:
    return "stopping";
  case Lifecycle::stopped:
    return "stopped";
  }
  return "unknown";
}
class Supervisor {
public:
  Lifecycle state() const { return state_; }
  void transition(Lifecycle next) {
    if (static_cast<int>(next) != static_cast<int>(state_) + 1)
      throw std::logic_error("invalid supervisor transition");
    state_ = next;
  }
  void component(std::string id, std::string state) {
    components_[std::move(id)] = std::move(state);
  }
  const auto &components() const { return components_; }

private:
  Lifecycle state_ = Lifecycle::boot;
  std::map<std::string, std::string> components_;
};
} // namespace pv
