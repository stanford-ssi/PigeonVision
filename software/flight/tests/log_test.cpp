#include "pv/log.hpp"
#include "pv/session.hpp"
#include <cassert>
#include <fstream>
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
  std::filesystem::remove_all(root);
}
