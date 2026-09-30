#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <mutex>
#include <span>
#include <stdexcept>

namespace pv {
inline constexpr std::size_t spi_payload_bytes = 1316, spi_message_bytes = 1332;
inline constexpr auto crc_table = [] {
  std::array<std::uint32_t, 256> t{};
  for (unsigned i=0; i<256; ++i) {
    auto c=i;
    for (unsigned j=0; j<8; ++j) c=(c>>1)^((c&1)?0xedb88320u:0);
    t[i]=c;
  }
  return t;
}();
inline std::uint32_t spi_crc(std::span<const std::uint8_t> bytes, std::uint32_t chain=0) {
  auto c=chain^0xffffffffu;
  for (auto b:bytes) c=crc_table[(c^b)&255]^(c>>8);
  return c^0xffffffffu;
}
inline auto spi_message(std::uint16_t seq, std::span<const std::uint8_t> payload) {
  if (payload.empty() || payload.size()>spi_payload_bytes || payload.size()%188)
    throw std::invalid_argument("SPI payload must contain 1..7 TS packets");
  for (std::size_t i=0; i<payload.size(); i+=188)
    if (payload[i]!=0x47) throw std::invalid_argument("SPI TS sync byte missing");
  std::array<std::uint8_t, spi_message_bytes> frame{};
  frame[0]=0x50; frame[1]=0x56; frame[2]=1; frame[3]=1;
  frame[4]=seq&255; frame[5]=seq>>8;
  frame[6]=payload.size()&255; frame[7]=payload.size()>>8;
  std::copy(payload.begin(),payload.end(),frame.begin()+12);
  auto crc=spi_crc(std::span(frame).first(1328));
  for (unsigned i=0; i<4; ++i) frame[1328+i]=(crc>>(8*i))&255;
  return frame;
}
struct SpiStats {
  std::uint64_t messages=0, attempted_messages=0, ts_packets=0, payload_bytes=0;
  std::uint32_t crc_chain=0;
  std::uint16_t next_seq=0;
  std::uint64_t ready_waits=0, ready_timeouts=0, ready_waits_over_20ms=0;
  std::int64_t max_ready_wait_us=0;
  std::size_t pending_payload_bytes=0;
  bool transfer_uncertain=false;
};
// Port abstraction also permits deterministic timeout/short-write tests.
struct SpiPort {
  virtual ~SpiPort()=default;
  virtual std::int64_t now_us()=0;
  virtual void sleep_until_us(std::int64_t)=0;
  virtual bool ready()=0;
  virtual std::ptrdiff_t write(std::span<const std::uint8_t>)=0;
};
class SpiSender {
 public:
  explicit SpiSender(SpiPort &port, std::int64_t timeout_us=2000000)
      : port_(port), timeout_(timeout_us), released_(port.now_us()+1) {}
  SpiStats stats() const { std::lock_guard lock(mutex_); return stats_; }
  void send(std::span<const std::uint8_t> payload) {
    if (failed_) throw std::runtime_error("SPI sender has stopped after an error");
    auto frame=spi_message(stats().next_seq,payload);
    { std::lock_guard lock(mutex_); stats_.pending_payload_bytes=payload.size(); }
    try {
      port_.sleep_until_us(released_+10); // At least 10 us CS high before READY.
      auto start=port_.now_us(); bool waited=false;
      try {
        while (true) {
          if (waited && port_.now_us()-start>=timeout_) {
            std::lock_guard lock(mutex_); ++stats_.ready_timeouts;
            throw std::runtime_error("SPI READY timeout");
          }
          if (port_.ready()) break;
          waited=true;
          port_.sleep_until_us(std::min(start+timeout_,port_.now_us()+20));
        }
      } catch (...) { record_wait(start,waited); throw; }
      record_wait(start,waited);
      { std::lock_guard lock(mutex_); ++stats_.attempted_messages; }
      try {
        // One write, one hardware CS assertion. Never retry uncertain transfers.
        if (port_.write(frame)!=static_cast<std::ptrdiff_t>(frame.size()))
          throw std::runtime_error("short SPI write");
      } catch (...) {
        std::lock_guard lock(mutex_); stats_.transfer_uncertain=true; throw;
      }
      released_=port_.now_us()+1; // Round up so truncation cannot shorten the CS gap.
      std::lock_guard lock(mutex_);
      ++stats_.messages; stats_.ts_packets+=payload.size()/188;
      stats_.payload_bytes+=payload.size(); ++stats_.next_seq;
      stats_.crc_chain=spi_crc(std::span(frame).last(4),stats_.crc_chain);
      stats_.pending_payload_bytes=0;
    } catch (...) { failed_=true; throw; }
  }
 private:
  void record_wait(std::int64_t start,bool waited) {
    if (!waited) return;
    auto us=port_.now_us()-start;
    std::lock_guard lock(mutex_);
    ++stats_.ready_waits; if(us>=20000) ++stats_.ready_waits_over_20ms;
    stats_.max_ready_wait_us=std::max(stats_.max_ready_wait_us,us);
  }
  SpiPort &port_;
  std::int64_t timeout_,released_;
  mutable std::mutex mutex_;
  SpiStats stats_;
  bool failed_=false;
};
}
