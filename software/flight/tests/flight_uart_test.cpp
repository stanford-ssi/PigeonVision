#include "pv/flight_uart.hpp"

#include <cassert>
#include <cstdio>
#include <mutex>
#include <thread>
#include <vector>
static std::string frame(std::string body) {
  char crc[6];
  snprintf(crc, sizeof(crc), "%04X", pv::flight_crc16(body));
  return body + "*" + crc + "\n";
}
int main() {
  assert(pv::flight_crc16("123456789") == 0x29b1);
  pv::FlightUartParser p;
  auto f = frame("PV1,4294967295,4294967200,ASCENT,4294967295,LAUNCH");
  p.feed(f.substr(0, 9), 100);
  assert(!p.snapshot(100)["valid"].get<bool>());
  p.feed(f.substr(9), 200);
  assert(p.snapshot(200)["valid"] == true);
  assert(p.snapshot(200)["phase"] == "ASCENT");
  p.feed(frame("PV1,0,20,DESCENT,0,APOGEE"), 300);
  assert(p.snapshot(300)["seq"] == 0);
  assert(p.snapshot(300)["event"] == "APOGEE");
  p.feed(frame("PV1,0,20,DESCENT,0,APOGEE"), 400);
  assert(p.snapshot(400)["counters"]["duplicate"] == 1);
  assert(p.snapshot(400)["receive_monotonic_us"] == 300);
  p.feed(frame("PV1,4294967295,19,PAD,0,APOGEE"), 500);
  assert(p.snapshot(500)["counters"]["out_of_order"] == 1);
  p.feed("PV1,1,30,PAD,0,APOGEE*0000\n", 600);
  assert(p.snapshot(600)["counters"]["bad_crc"] == 1);
  p.feed(frame("PV1,1,30,BOGUS,0,APOGEE"), 700);
  p.feed(std::string(96, 'x') + "\n", 800);
  assert(p.snapshot(800)["counters"]["malformed"] == 2);
  assert(p.snapshot(3000299)["valid"] == true);
  auto stale = p.snapshot(3000300);
  assert(stale["valid"] == false && stale["phase"] == "UNKNOWN" && stale["event"] == "APOGEE");
  p.feed(frame("PV1,1,30,DESCENT,0,APOGEE"), 4000000);
  assert(p.snapshot(4000000)["valid"] == true);
  p.feed(frame("PV1,2,29,DESCENT,0,APOGEE"), 4000100);
  assert(p.snapshot(4000100)["counters"]["out_of_order"] == 2);
  p.feed(frame("PV1,2,40,DESCENT,0,DEPLOYMENT"), 4000100);
  assert(p.snapshot(4000100)["counters"]["malformed"] == 3);
  pv::FlightUartParser reboot;
  reboot.feed(frame("PV1,100000,500000,ASCENT,4,LAUNCH"), 100);
  reboot.feed(frame("PV1,0,100,PAD,0,NONE"), 1000);
  assert(reboot.snapshot(1000)["seq"] == 100000 && reboot.snapshot(1000)["inferred_epoch"] == 0);
  reboot.feed("PV1,0,100,PAD,0,NONE*0000\n", 3100000);
  assert(reboot.snapshot(3100000)["receive_monotonic_us"] == 100);
  reboot.feed(frame("PV1,0,100,PAD,0,NONE"), 3200000);
  assert(reboot.snapshot(3200000)["valid"] == false &&
         reboot.snapshot(3200000)["epoch_reacquisition_pending"] == true);
  reboot.feed(frame("PV1,1,600,PAD,0,NONE"), 3700000);
  assert(reboot.snapshot(3700000)["valid"] == true &&
         reboot.snapshot(3700000)["inferred_epoch"] == 1 && reboot.snapshot(3700000)["seq"] == 1);
  // Every accepted event survives coalesced serial reads.
  pv::FlightUartParser events;
  events.feed(frame("PV1,1,1,ASCENT,1,LAUNCH") + frame("PV1,2,2,DESCENT,2,APOGEE") +
                  frame("PV1,3,3,DESCENT,3,DEPLOYMENT"),
              10);
  auto batch = events.take_events();
  assert(batch.size() == 3 && batch[0]["event"] == "LAUNCH" && batch[2]["event"] == "DEPLOYMENT");
  assert(events.take_events().empty());
  std::mutex mutex;
  std::vector<nlohmann::json> records;
  pv::FlightUartWorker worker({{"backend", "serial"}, {"device", "/nonexistent-pv-uart"}},
                              [&](auto r) {
                                std::lock_guard l(mutex);
                                records.push_back(r);
                              });
  worker.start();
  std::this_thread::sleep_for(std::chrono::milliseconds(30));
  worker.stop();
  assert(worker.stats()["status"] == "unavailable");
  assert(worker.stats()["valid"] == false);
}
