#pragma once
#include "pv/config.hpp"
#include "pv/log.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
}
#include <memory>

namespace pv {
inline void av_check(int code, const char *operation) {
  if (code >= 0) return;
  char text[AV_ERROR_MAX_STRING_SIZE]; av_strerror(code, text, sizeof(text));
  throw std::runtime_error(std::string(operation) + ": " + text);
}
struct PacketDelete { void operator()(AVPacket *p) const { av_packet_free(&p); } };
using Packet = std::unique_ptr<AVPacket, PacketDelete>;
struct Encoded {
  std::string camera;
  Packet packet;
  Json metadata;
  std::uint64_t generation = 0;
  std::int64_t transport_admission_us = 0;
};
struct StreamInfo {
  std::string camera;
  const AVCodecContext *codec;
};
class Outputs {
 public:
  Outputs(const Config &config, Logs &logs, const std::vector<StreamInfo> &streams, std::int64_t origin_ns);
  ~Outputs();
  void packet(const std::string &camera, const AVPacket *packet, const Json &metadata);
  void metadata(const Json &record);
  void stop();
  Json stats() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}  // namespace pv
