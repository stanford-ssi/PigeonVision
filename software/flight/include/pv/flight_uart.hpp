#pragma once
#include <cstdint>
#include <functional>
#include <memory>
#include <nlohmann/json.hpp>
#include <string>
#include <string_view>
#include <vector>
namespace pv {
std::uint16_t flight_crc16(std::string_view bytes);
// Pure streaming parser: receive time is host monotonic µs; FC uptime is independent.
class FlightUartParser {
public:
  void feed(std::string_view bytes, std::int64_t receive_us);
  nlohmann::json snapshot(std::int64_t now_us) const;
  std::vector<nlohmann::json> take_events();

private:
  void line(std::int64_t receive_us);
  std::vector<nlohmann::json> events_;
  std::uint64_t dropped_events_ = 0;
  std::string buffer_;
  bool dropping_ = false, have_ = false;
  std::uint32_t sequence_ = 0, uptime_ = 0, event_sequence_ = 0;
  std::int64_t receive_us_ = 0, candidate_receive_us_ = 0;
  bool candidate_ = false;
  std::uint32_t candidate_seq_ = 0, candidate_uptime_ = 0, candidate_event_seq_ = 0;
  std::string candidate_event_;
  std::uint64_t inferred_epoch_ = 0;
  std::string phase_ = "UNKNOWN", event_ = "NONE";
  std::uint64_t bad_crc_ = 0, malformed_ = 0, duplicate_ = 0, out_of_order_ = 0, accepted_ = 0;
};
class FlightUartWorker {
public:
  using Callback = std::function<void(nlohmann::json)>;
  FlightUartWorker(const nlohmann::json &config, Callback callback);
  ~FlightUartWorker();
  void start();
  void stop();
  nlohmann::json stats() const;

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
} // namespace pv
