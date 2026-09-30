#pragma once
#include "pv/config.hpp"
#include <memory>
#include <span>
namespace pv {
class SpiOutput {
 public:
  explicit SpiOutput(const SpiConfig &config);
  ~SpiOutput();
  void send(std::span<const std::uint8_t> payload);
  Json stats() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}
