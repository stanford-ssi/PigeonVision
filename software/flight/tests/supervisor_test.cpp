#include "pv/supervisor.hpp"
#include "pv/session.hpp"
#include <cassert>
#include <set>
int main() {
  pv::Supervisor s;
  s.transition(pv::Lifecycle::initializing);
  s.component("A", "failed");
  s.component("storage", "disabled");
  s.component("telemetry", "running");
  s.transition(pv::Lifecycle::running);
  assert(s.state() == pv::Lifecycle::running && s.components().at("telemetry") == "running");
  bool rejected = false;
  try {
    s.transition(pv::Lifecycle::stopped);
  } catch (const std::logic_error &) {
    rejected = true;
  }
  assert(rejected);
  s.transition(pv::Lifecycle::stopping);
  s.transition(pv::Lifecycle::stopped);
  std::set<std::string> ids;
  for (int i = 0; i < 100; ++i)
    assert(ids.insert(pv::session_id()).second);
#ifdef __linux__
  const auto path = std::filesystem::temp_directory_path() / pv::session_id();
  {
    pv::CaptureLock a(path);
    rejected = false;
    try {
      pv::CaptureLock b(path);
    } catch (const std::runtime_error &) {
      rejected = true;
    }
    assert(rejected);
  }
  {
    pv::CaptureLock c(path);
  }
  std::filesystem::remove(path);
#endif
}
