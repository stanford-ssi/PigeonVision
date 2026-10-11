#include "pv/log.hpp"
#include "pv/session.hpp"
#include <cassert>
#include <csignal>
#include <fstream>
#include <sys/resource.h>
int main() {
  const auto root = std::filesystem::temp_directory_path() / pv::session_id();
  {
    pv::Logs unavailable(root / "missing");
    assert(unavailable.failed());
    unavailable.sensor({{"timestamp_ns", 123}, {"valid", false}});
    unavailable.finish();
  }
  std::filesystem::create_directory(root);
  {
    pv::Logs logs(root);
    assert(!logs.failed());
    logs.sensor({{"timestamp_ns", 123}, {"valid", true}});
    logs.finish();
  }
  std::ifstream file(root / "sensors.jsonl");
  pv::Json record;
  file >> record;
  assert(record["timestamp_ns"] == 123 && record["valid"] == true);
  file.close();
  {
    // One short sensor record stays buffered until finish(), below the 32-record batch.
    // Force the final write to fail after the files have opened successfully.
    pv::Logs logs(root);
    assert(!logs.failed());
    rlimit original{};
    assert(getrlimit(RLIMIT_FSIZE, &original) == 0);
    auto limited = original;
    limited.rlim_cur = 0;
    const auto handler = std::signal(SIGXFSZ, SIG_IGN);
    assert(handler != SIG_ERR);
    assert(setrlimit(RLIMIT_FSIZE, &limited) == 0);
    logs.sensor({{"timestamp_ns", 456}, {"valid", true}});
    logs.finish();
    assert(setrlimit(RLIMIT_FSIZE, &original) == 0);
    assert(std::signal(SIGXFSZ, handler) != SIG_ERR);
    assert(std::filesystem::file_size(root / "sensors.jsonl") == 0);
    assert(logs.failed() && logs.lost() == 0);
    logs.finish(); // The completed writer remains safe to finish again.
    assert(logs.failed());
  }
  std::filesystem::remove_all(root);
}
