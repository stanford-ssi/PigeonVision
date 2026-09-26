#include "pv/config.hpp"
#include <cassert>
#include <fstream>
#include <iostream>
#include <unistd.h>
int main() {
  auto file = std::filesystem::temp_directory_path() / ("pv-config-test-"+std::to_string(getpid())+".json");
  pv::Json valid{{"schema_version",1}, {"session_dir","output/config-test"},
    {"cameras",{{{"id","A"},{"device","physical-a"}}}}, {"encode",false}, {"record",false}};
  auto write = [&](const pv::Json &value) { std::ofstream stream(file); stream << value; };
  write(valid);
  auto config = pv::Config::read(file);
  assert(config.session_dir == std::filesystem::current_path()/"output/config-test");
  assert(!config.encode && config.cameras[0].flip_y && !config.cameras[0].flip_x);
  auto rejects = [&](const char *key, pv::Json bad) {
    auto j = valid; j[key] = bad; write(j); bool rejected = false;
    try { (void)pv::Config::read(file); } catch (const std::exception &) { rejected = true; }
    assert(rejected);
  };
  rejects("width",1552.5); rejects("height",-1); rejects("fps",true);
  rejects("bitrate",100000001); rejects("min_free_bytes",-1); rejects("mux_bitrate",0);
  rejects("duration_seconds",nullptr); rejects("duration_seconds",false);
  rejects("schema_version",2); rejects("width",1551); rejects("record",true);
  rejects("cameras",{{{"id","A"},{"device","same"}},{{"id","B"},{"device","same"}}});
  std::filesystem::remove(file);
  std::cout << "PASS: strict config types, bounds, unique device identities, capture-only and CWD paths\n";
}
