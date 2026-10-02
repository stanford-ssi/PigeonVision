#include "pv/flight_uart.hpp"

#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cstdio>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

#include "pv/acquisition_clock.hpp"
namespace pv {
namespace {
std::int64_t now_us() {
  return acquisition_us();
}
bool newer(std::uint32_t a, std::uint32_t b) {
  auto delta = a - b;
  return delta && delta < 0x80000000U;
}
bool number(std::string_view s, std::uint32_t &n) {
  if (s.empty())
    return false;
  auto [p, e] = std::from_chars(s.data(), s.data() + s.size(), n);
  return e == std::errc{} && p == s.data() + s.size();
}
bool allowed(std::string_view x, std::initializer_list<std::string_view> list) {
  return std::find(list.begin(), list.end(), x) != list.end();
}
} // namespace
std::uint16_t flight_crc16(std::string_view bytes) {
  std::uint16_t crc = 0xffff;
  for (unsigned char c : bytes) {
    crc ^= std::uint16_t(c) << 8;
    for (int i = 0; i < 8; ++i)
      crc = std::uint16_t((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0));
  }
  return crc;
}
void FlightUartParser::feed(std::string_view bytes, std::int64_t time) {
  for (char c : bytes) {
    if (c == '\n') {
      if (!dropping_)
        line(time);
      buffer_.clear();
      dropping_ = false;
    } else if (!dropping_) {
      if (buffer_.size() >= 95) {
        ++malformed_;
        buffer_.clear();
        dropping_ = true;
      } else
        buffer_ += c;
    }
  }
}
void FlightUartParser::line(std::int64_t time) {
  auto star = buffer_.find('*');
  if (star == std::string::npos || star + 5 != buffer_.size()) {
    ++malformed_;
    return;
  }
  unsigned crc = 0;
  auto [p, e] =
      std::from_chars(buffer_.data() + star + 1, buffer_.data() + buffer_.size(), crc, 16);
  if (e != std::errc{} || p != buffer_.data() + buffer_.size()) {
    ++malformed_;
    return;
  }
  std::string_view body(buffer_.data(), star);
  if (crc != flight_crc16(body)) {
    ++bad_crc_;
    return;
  }
  std::vector<std::string_view> fields;
  std::size_t start = 0;
  for (std::size_t i = 0; i <= body.size(); ++i)
    if (i == body.size() || body[i] == ',') {
      fields.push_back(body.substr(start, i - start));
      start = i + 1;
    }
  std::uint32_t seq, uptime, event_seq;
  if (fields.size() != 6 || fields[0] != "PV1" || !number(fields[1], seq) ||
      !number(fields[2], uptime) || !number(fields[4], event_seq) ||
      !allowed(fields[3], {"UNKNOWN", "PAD", "ASCENT", "DESCENT", "LANDED"}) ||
      !allowed(fields[5], {"NONE", "LAUNCH", "APOGEE", "DEPLOYMENT", "LANDED"})) {
    ++malformed_;
    return;
  }
  bool forward =
      !have_ || (newer(seq, sequence_) && (uptime == uptime_ || newer(uptime, uptime_)) &&
                 (event_seq == event_sequence_ || newer(event_seq, event_sequence_)));
  bool reacquired = false;
  if (have_ && !forward && time - receive_us_ >= 3000000) {
    // No boot identifier exists on the wire. Two progressing valid frames after
    // staleness infer an epoch; they do not prove that the FC physically rebooted.
    if (candidate_ && time - candidate_receive_us_ < 3000000 && newer(seq, candidate_seq_) &&
        newer(uptime, candidate_uptime_) &&
        (event_seq == candidate_event_seq_ || newer(event_seq, candidate_event_seq_)) &&
        (event_seq != candidate_event_seq_ || fields[5] == candidate_event_)) {
      ++inferred_epoch_;
      reacquired = true;
      candidate_ = false;
    } else {
      candidate_ = true;
      candidate_seq_ = seq;
      candidate_uptime_ = uptime;
      candidate_event_seq_ = event_seq;
      candidate_event_ = fields[5];
      candidate_receive_us_ = time;
      ++out_of_order_;
      return;
    }
  }
  if (have_ && !reacquired) {
    if (seq == sequence_) {
      ++duplicate_;
      return;
    }
    if (!forward) {
      ++out_of_order_;
      return;
    }
    if (event_seq == event_sequence_ && fields[5] != event_) {
      ++malformed_;
      return;
    }
  }
  candidate_ = false;
  bool changed_event =
      (!have_ || reacquired || event_seq != event_sequence_) && fields[5] != "NONE";
  have_ = true;
  sequence_ = seq;
  uptime_ = uptime;
  event_sequence_ = event_seq;
  phase_ = fields[3];
  event_ = fields[5];
  receive_us_ = time;
  ++accepted_;
  if (changed_event) {
    auto record = snapshot(time);
    record["type"] = "flight_event";
    if (events_.size() == 16) {
      events_.erase(events_.begin());
      ++dropped_events_;
    }
    events_.push_back(std::move(record));
  }
}
nlohmann::json FlightUartParser::snapshot(std::int64_t time) const {
  bool stale = !have_ || time - receive_us_ >= 3000000;
  return {{"type", "flight_status"},
          {"valid", have_ && !stale},
          {"stale", stale},
          {"phase", stale ? "UNKNOWN" : phase_},
          {"event", event_},
          {"inferred_epoch", inferred_epoch_},
          {"epoch_reacquisition_pending", candidate_},
          {"seq", have_ ? nlohmann::json(sequence_) : nlohmann::json(nullptr)},
          {"event_seq", have_ ? nlohmann::json(event_sequence_) : nlohmann::json(nullptr)},
          {"fc_uptime_ms", have_ ? nlohmann::json(uptime_) : nlohmann::json(nullptr)},
          {"receive_monotonic_us", have_ ? nlohmann::json(receive_us_) : nlohmann::json(nullptr)},
          {"monotonic_us", time},
          {"host_clock_domain", acquisition_clock_domain()},
          {"counters",
           {{"accepted", accepted_},
            {"bad_crc", bad_crc_},
            {"malformed", malformed_},
            {"duplicate", duplicate_},
            {"out_of_order", out_of_order_},
            {"dropped_events", dropped_events_}}}};
}
std::vector<nlohmann::json> FlightUartParser::take_events() {
  auto result = std::move(events_);
  events_.clear();
  return result;
}
struct FlightUartWorker::Impl {
  nlohmann::json config;
  Callback callback;
  std::atomic<bool> running{false};
  std::thread thread;
  mutable std::mutex mutex;
  FlightUartParser parser;
  std::string status = "disabled";
  std::uint64_t io_errors = 0;
  Impl(const nlohmann::json &c, Callback cb) : config(c), callback(std::move(cb)) {}
  void run() {
    auto backend = config.value("backend", std::string("disabled"));
    if (backend == "disabled")
      return;
    int fd = -1;
    auto retry = std::chrono::steady_clock::time_point{};
    auto next = std::chrono::steady_clock::now();
    std::uint32_t seq = 0;
    while (running) {
      auto now = std::chrono::steady_clock::now();
      if (backend == "serial" && fd < 0 && now >= retry) {
        auto device = config.value("device", std::string{});
        fd = device.empty() ? -1
                            : open(device.c_str(), O_RDONLY | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
        bool ok = fd >= 0;
        if (ok) {
          termios t{};
          ok = tcgetattr(fd, &t) == 0;
          if (ok) {
            cfmakeraw(&t);
            cfsetispeed(&t, B115200);
            cfsetospeed(&t, B115200);
            t.c_cflag = (t.c_cflag & ~(CSIZE | PARENB | CSTOPB | CRTSCTS)) | CS8 | CLOCAL | CREAD;
            t.c_cc[VMIN] = 0;
            t.c_cc[VTIME] = 0;
            ok = tcsetattr(fd, TCSANOW, &t) == 0;
          }
        }
        if (!ok) {
          if (fd >= 0)
            close(fd);
          fd = -1;
          retry = now + std::chrono::seconds(1);
          std::lock_guard l(mutex);
          status = "unavailable";
          ++io_errors;
        } else {
          std::lock_guard l(mutex);
          status = "receiving";
        }
      }
      if (fd >= 0) {
        pollfd item{fd, POLLIN, 0};
        int result = poll(&item, 1, 10);
        if (result > 0 && (item.revents & (POLLERR | POLLHUP | POLLNVAL))) {
          close(fd);
          fd = -1;
          retry = now + std::chrono::seconds(1);
          std::lock_guard l(mutex);
          status = "unavailable";
          ++io_errors;
        } else if (result > 0 && (item.revents & POLLIN)) {
          char bytes[256];
          auto n = read(fd, bytes, sizeof(bytes));
          if (n > 0) {
            std::lock_guard l(mutex);
            parser.feed(std::string_view(bytes, std::size_t(n)), now_us());
          }
        }
      }
      if (now >= next) {
        if (backend == "simulation") {
          auto body =
              "PV1," + std::to_string(seq) + "," + std::to_string(seq * 500) + ",PAD,0,NONE";
          char crc[6];
          snprintf(crc, sizeof(crc), "%04X", flight_crc16(body));
          std::lock_guard l(mutex);
          parser.feed(body + "*" + crc + "\n", now_us());
          ++seq;
          status = "simulation";
        }
        nlohmann::json record;
        {
          std::lock_guard l(mutex);
          record = parser.snapshot(now_us());
        }
        record["backend"] = backend;
        {
          std::lock_guard l(mutex);
          record["status"] = status;
        }
        callback(record);
        next = now + std::chrono::milliseconds(500);
      }
      std::vector<nlohmann::json> events;
      {
        std::lock_guard l(mutex);
        events = parser.take_events();
      }
      for (auto &event : events) {
        event["backend"] = backend;
        callback(std::move(event));
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    if (fd >= 0)
      close(fd);
  }
};
FlightUartWorker::FlightUartWorker(const nlohmann::json &c, Callback cb)
    : impl_(std::make_unique<Impl>(c, std::move(cb))) {
  auto b = c.value("backend", std::string("disabled"));
  (void)c.value("device", std::string{});
  if (b != "disabled" && b != "simulation" && b != "serial")
    throw std::invalid_argument("flight_uart backend must be disabled, simulation or serial");
}
FlightUartWorker::~FlightUartWorker() {
  stop();
}
void FlightUartWorker::start() {
  if (impl_->running.exchange(true))
    return;
  impl_->thread = std::thread([this] { impl_->run(); });
}
void FlightUartWorker::stop() {
  impl_->running = false;
  if (impl_->thread.joinable())
    impl_->thread.join();
}
nlohmann::json FlightUartWorker::stats() const {
  std::lock_guard l(impl_->mutex);
  auto s = impl_->parser.snapshot(now_us());
  s["status"] = impl_->status;
  s["io_errors"] = impl_->io_errors;
  return s;
}
} // namespace pv
