#include "pv/outputs.hpp"
#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <iomanip>
#include <map>
#include <sstream>
#include <vector>
extern "C" {
#include <libavutil/opt.h>
}

namespace pv {
namespace {
constexpr AVRational us_timebase{1, 1000000};
struct Format {
  AVFormatContext *context = nullptr;
  AVIOContext *custom_io = nullptr;
  bool header = false;
  ~Format() { close(false); }
  void close(bool checked = false) {
    if (!context) return;
    int error = header ? av_write_trailer(context) : 0;
    if (custom_io) {
      avio_flush(custom_io);
      if (error >= 0 && custom_io->error < 0) error = custom_io->error;
      av_freep(&custom_io->buffer);
      avio_context_free(&custom_io);
      context->pb = nullptr;
    } else if (context->pb) {
      avio_flush(context->pb);
      if (error >= 0 && context->pb->error < 0) error = context->pb->error;
      int result = avio_closep(&context->pb); if (error >= 0) error = result;
    }
    avformat_free_context(context); context = nullptr; header = false;
    if (checked) av_check(error, "finalize output");
  }
};
struct CodecParameters {
  AVCodecParameters *value = avcodec_parameters_alloc();
  explicit CodecParameters(const AVCodecContext *codec) {
    if (!value) throw std::bad_alloc();
    av_check(avcodec_parameters_from_context(value, codec), "copy encoder parameters");
  }
  ~CodecParameters() { avcodec_parameters_free(&value); }
};
struct Recorder {
  const Config &config;
  Logs &logs;
  std::string camera;
  CodecParameters parameters;
  BoundedQueue<Encoded> queue{120};
  std::thread worker;
  std::atomic<bool> failed{false};
  std::atomic<std::uint64_t> drops{0}, written{0};
  std::uint64_t generation = 0;
  Recorder(const Config &c, Logs &l, const StreamInfo &s) : config(c), logs(l), camera(s.camera), parameters(s.codec) {
    worker = std::thread([this] { run(); });
  }
  ~Recorder() { stop(); }
  void stop() { queue.close(); if (worker.joinable()) worker.join(); }
  bool submit(Encoded item) {
    item.generation = generation;
    if (failed || !queue.try_push(std::move(item))) { ++drops; ++generation; return false; }
    return true;
  }
  void run() {
    Format output;
    std::uint64_t segment = 0;
    std::int64_t first_pts = 0, last_pts = 0, last_space_check = -1000000;
    std::string filename;
    bool await_keyframe = true;
    bool in_flight = false;
    std::uint64_t observed_generation = 0;
    auto finish = [&](bool complete) {
      if (!output.context) return;
      try { output.close(complete); }
      catch (...) {
        logs.segment({{"camera_id", camera}, {"path", filename}, {"index", segment},
                      {"first_pts_us", first_pts}, {"last_pts_us", last_pts}, {"complete", false}});
        throw;
      }
      logs.segment({{"camera_id", camera}, {"path", filename}, {"index", segment},
                    {"first_pts_us", first_pts}, {"last_pts_us", last_pts}, {"complete", complete}});
    };
    try {
      while (auto item = queue.pop()) {
        in_flight = true;
        const auto pts = item->packet->pts;
        const bool keyframe = item->packet->flags & AV_PKT_FLAG_KEY;
        if (item->generation != observed_generation) {
          observed_generation = item->generation; await_keyframe = true;
          finish(false);
          logs.event("recording", "queue_overflow_waiting_for_keyframe", camera);
        }
        if (await_keyframe && !keyframe) { ++drops; in_flight = false; continue; }
        if (await_keyframe || pts - last_space_check >= 1000000) {
          if (std::filesystem::space(config.session_dir).available < config.min_free_bytes)
            throw std::runtime_error("free-space floor reached; recording disabled");
          last_space_check = pts;
        }
        if (!output.context || segment_due(pts, first_pts, std::int64_t(config.segment_seconds) * 1000000, keyframe)) {
          finish(true);
          std::ostringstream name; name << camera << '-' << std::setw(6) << std::setfill('0') << ++segment << ".mkv";
          filename = name.str();
          auto path = (config.session_dir / filename).string();
          av_check(avformat_alloc_output_context2(&output.context, nullptr, "matroska", path.c_str()), "create Matroska");
          auto *stream = avformat_new_stream(output.context, nullptr);
          if (!stream) throw std::bad_alloc();
          av_check(avcodec_parameters_copy(stream->codecpar, parameters.value), "copy recording stream");
          stream->codecpar->codec_tag = 0; stream->time_base = us_timebase;
          av_dict_set(&stream->metadata, "title", camera.c_str(), 0);
          output.context->avoid_negative_ts = AVFMT_AVOID_NEG_TS_DISABLED;
          av_check(avio_open(&output.context->pb, path.c_str(), AVIO_FLAG_WRITE), "open recording");
          AVDictionary *options = nullptr;
          av_dict_set(&options, "cluster_time_limit", "1000", 0);
          int status = avformat_write_header(output.context, &options); av_dict_free(&options);
          av_check(status, "write recording header"); output.header = true;
          first_pts = pts; await_keyframe = false;
          logs.event("recording", "segment_open", camera, filename);
        }
        last_pts = pts;
        av_packet_rescale_ts(item->packet.get(), us_timebase, output.context->streams[0]->time_base);
        item->packet->stream_index = 0;
        av_check(av_interleaved_write_frame(output.context, item->packet.get()), "write recording packet");
        if (output.context->pb->error < 0) av_check(output.context->pb->error, "recording IO");
        ++written;
        in_flight = false;
      }
      finish(true);
    } catch (const std::exception &e) {
      failed = true; logs.event("recording", "failed", camera, e.what()); finish(false);
      if (in_flight) ++drops;
      // Release remaining packet references promptly, without slowing producers.
      queue.close(); while (queue.pop()) ++drops;
    }
  }
};

// Custom AVIO groups exactly seven TS packets and paces bytes to the configured
// transport clock. UDP writes are nonblocking: a bad link cannot hold camera buffers.
struct UdpWriter {
  int socket = -1;
  sockaddr_storage destination{};
  socklen_t destination_size = 0;
  std::vector<std::uint8_t> pending;
  std::int64_t bitrate;
  std::uint64_t sent_bytes = 0, errors = 0;
  std::chrono::steady_clock::time_point started{};
  explicit UdpWriter(const Config &config) : bitrate(config.mux_bitrate) {
    auto address = config.udp_destination;
    std::string host, port;
    if (address.starts_with('[')) {
      auto end = address.find(']');
      if (end == std::string::npos || end + 1 >= address.size() || address[end + 1] != ':') throw std::runtime_error("invalid UDP [IPv6]:port");
      host = address.substr(1, end - 1); port = address.substr(end + 2);
    } else {
      auto colon = address.rfind(':');
      if (colon == std::string::npos) throw std::runtime_error("UDP destination must be host:port");
      host = address.substr(0, colon); port = address.substr(colon + 1);
    }
    if (host.empty() || port.empty() || port.find_first_not_of("0123456789") != std::string::npos || std::stoul(port) < 1 || std::stoul(port) > 65535)
      throw std::runtime_error("invalid UDP host/port");
    addrinfo hints{}; hints.ai_socktype = SOCK_DGRAM; hints.ai_family = AF_UNSPEC;
    addrinfo *result = nullptr;
    int status = getaddrinfo(host.c_str(), port.c_str(), &hints, &result);
    if (status) throw std::runtime_error(std::string("UDP address: ") + gai_strerror(status));
    for (auto *r = result; r; r = r->ai_next) {
      socket = ::socket(r->ai_family, SOCK_DGRAM, r->ai_protocol);
      if (socket >= 0) {
        if (fcntl(socket, F_SETFL, O_NONBLOCK) < 0 || fcntl(socket, F_SETFD, FD_CLOEXEC) < 0) { ::close(socket); socket = -1; continue; }
        std::memcpy(&destination, r->ai_addr, r->ai_addrlen); destination_size = r->ai_addrlen; break;
      }
    }
    freeaddrinfo(result);
    if (socket < 0) throw std::runtime_error("cannot create UDP socket");
    pending.reserve(32768 + 1316);
  }
  ~UdpWriter() { if (socket >= 0) ::close(socket); }
  void send(const std::uint8_t *data, std::size_t length) {
    if (started == std::chrono::steady_clock::time_point{}) started = std::chrono::steady_clock::now();
    auto deadline = started + std::chrono::nanoseconds(static_cast<std::int64_t>(static_cast<long double>(sent_bytes) * 8000000000.0L / bitrate));
    std::this_thread::sleep_until(deadline);
    auto n = sendto(socket, data, length, MSG_DONTWAIT, reinterpret_cast<sockaddr *>(&destination), destination_size);
    if (n != static_cast<ssize_t>(length)) ++errors;
    sent_bytes += length;
  }
  static int write(void *opaque, const std::uint8_t *data, int size) noexcept {
    try {
      auto &self = *static_cast<UdpWriter *>(opaque);
      self.pending.insert(self.pending.end(), data, data + size);
      std::size_t consumed = 0;
      while (self.pending.size() - consumed >= 1316) { self.send(self.pending.data() + consumed, 1316); consumed += 1316; }
      self.pending.erase(self.pending.begin(), self.pending.begin() + consumed);
      return size;
    } catch (...) { return AVERROR(EIO); }
  }
  void flush() {
    if (!pending.empty() && pending.size() % 188 == 0) { send(pending.data(), pending.size()); pending.clear(); }
  }
};
struct Transport {
  const Config &config;
  Logs &logs;
  std::int64_t origin;
  std::map<std::string, std::unique_ptr<CodecParameters>> parameters;
  BoundedQueue<Encoded> queue{240};
  std::thread worker;
  std::atomic<bool> failed{false};
  std::atomic<std::uint64_t> drops{0}, packets{0}, datagram_errors{0};
  std::mutex submit_mutex;
  std::uint64_t generation = 0;
  Transport(const Config &c, Logs &l, const std::vector<StreamInfo> &streams, std::int64_t epoch) : config(c), logs(l), origin(epoch) {
    for (const auto &s : streams) parameters[s.camera] = std::make_unique<CodecParameters>(s.codec);
    worker = std::thread([this] { run(); });
  }
  ~Transport() { stop(); }
  void stop() { queue.close(); if (worker.joinable()) worker.join(); }
  bool submit(Encoded item) {
    std::lock_guard lock(submit_mutex);
    item.generation = generation;
    if (failed || !queue.try_push(std::move(item))) { ++drops; ++generation; return false; } return true;
  }
  void run() {
    bool in_flight = false;
    try {
      UdpWriter udp(config);
      Format output;
      av_check(avformat_alloc_output_context2(&output.context, nullptr, "mpegts", nullptr), "create transport");
      std::map<std::string, AVStream *> streams;
      for (const auto &[id, p] : parameters) {
        auto *s = avformat_new_stream(output.context, nullptr); if (!s) throw std::bad_alloc();
        av_check(avcodec_parameters_copy(s->codecpar, p->value), "copy transport stream");
        s->codecpar->codec_tag = 0; s->id = id == "A" ? 256 : 257; s->time_base = us_timebase;
        av_dict_set(&s->metadata, "title", id.c_str(), 0); streams[id] = s;
      }
      auto *data = avformat_new_stream(output.context, nullptr); if (!data) throw std::bad_alloc();
      data->id = 258; data->time_base = us_timebase;
      data->codecpar->codec_type = AVMEDIA_TYPE_DATA; data->codecpar->codec_id = AV_CODEC_ID_BIN_DATA;
      auto *program = av_new_program(output.context, 1); if (!program) throw std::bad_alloc();
      for (unsigned i = 0; i < output.context->nb_streams; ++i) av_program_add_stream_index(output.context, 1, i);
      auto *buffer = static_cast<unsigned char *>(av_malloc(32768)); if (!buffer) throw std::bad_alloc();
      output.custom_io = avio_alloc_context(buffer, 32768, 1, &udp, nullptr, UdpWriter::write, nullptr);
      if (!output.custom_io) { av_free(buffer); throw std::bad_alloc(); }
      output.context->pb = output.custom_io; output.context->flags |= AVFMT_FLAG_CUSTOM_IO;
      output.context->max_delay = 500000; output.context->max_interleave_delta = 100000;
      output.context->avoid_negative_ts = AVFMT_AVOID_NEG_TS_DISABLED;
      AVDictionary *options = nullptr;
      av_dict_set_int(&options, "muxrate", config.mux_bitrate, 0);
      av_dict_set(&options, "pcr_period", "20", 0); av_dict_set(&options, "pat_period", "0.1", 0);
      // libav's CBR TS mux adds 2*max_delay to ALL PTS/DTS and max_delay
      // to the initial PCR. Declare the common 1s media offset to the receiver;
      // copyts=1 removes the required startup transmission lead and is invalid.
      av_dict_set(&options, "mpegts_copyts", "0", 0);
      int result = avformat_write_header(output.context, &options); av_dict_free(&options);
      av_check(result, "write transport header"); output.header = true;
      logs.event("transport", "started", "", config.udp_destination);
      std::uint64_t observed_generation = 0;
      std::map<std::string, bool> waiting_for_keyframe;
      for (const auto &[id, _] : parameters) waiting_for_keyframe[id] = true;
      while (auto item = queue.pop()) {
        in_flight = true;
        if (item->generation != observed_generation) {
          observed_generation = item->generation;
          for (auto &[_, wait] : waiting_for_keyframe) wait = true;
          logs.event("transport", "queue_overflow_waiting_for_keyframes");
        }
        if (item->packet) {
          auto &wait = waiting_for_keyframe.at(item->camera);
          if (wait && !(item->packet->flags & AV_PKT_FLAG_KEY)) { ++drops; in_flight = false; continue; }
          wait = false;
          auto *stream = streams.at(item->camera);
          av_packet_rescale_ts(item->packet.get(), us_timebase, stream->time_base);
          item->packet->stream_index = stream->index;
          av_check(av_interleaved_write_frame(output.context, item->packet.get()), "mux video");
          ++packets;
        }
        const auto payload = item->metadata.dump();
        // A single JSON object is a single private-data PES. Stay far below its
        // size limit and constrain accidental huge session/calibration records.
        if (payload.size() > 8192) throw std::runtime_error("transport metadata record exceeds 8192 bytes");
        Packet metadata(av_packet_alloc()); if (!metadata) throw std::bad_alloc();
        av_check(av_new_packet(metadata.get(), payload.size()), "allocate metadata");
        std::memcpy(metadata->data, payload.data(), payload.size());
        // A and B complete asynchronously. A single data stream therefore uses
        // mux-admission timestamps; its JSON preserves the exact capture PTS.
        // Video packets keep the sensor-derived PTS without this adjustment.
        metadata->pts = metadata->dts = (boot_ns() - origin) / 1000;
        metadata->stream_index = data->index;
        av_packet_rescale_ts(metadata.get(), us_timebase, data->time_base);
        av_check(av_interleaved_write_frame(output.context, metadata.get()), "mux metadata");
        avio_flush(output.context->pb);
        datagram_errors = udp.errors;
        in_flight = false;
      }
      av_check(av_interleaved_write_frame(output.context, nullptr), "flush transport");
      output.close(true); udp.flush(); datagram_errors = udp.errors;
    } catch (const std::exception &e) {
      failed = true; logs.event("transport", "failed", "", e.what());
      if (in_flight) ++drops;
      queue.close(); while (queue.pop()) ++drops;
    }
  }
};
}  // namespace
struct Outputs::Impl {
  Logs &logs;
  std::map<std::string, std::unique_ptr<Recorder>> recorders;
  std::unique_ptr<Transport> transport;
  Impl(const Config &c, Logs &l, const std::vector<StreamInfo> &s, std::int64_t origin) : logs(l) {
    if (c.record) for (const auto &stream : s) recorders[stream.camera] = std::make_unique<Recorder>(c, l, stream);
    if (!c.udp_destination.empty()) transport = std::make_unique<Transport>(c, l, s, origin);
  }
};
Outputs::Outputs(const Config &c, Logs &l, const std::vector<StreamInfo> &s, std::int64_t origin) : impl_(std::make_unique<Impl>(c,l,s,origin)) {}
Outputs::~Outputs() { stop(); }
void Outputs::stop() {
  if (!impl_) return;
  for (auto &[_, recorder] : impl_->recorders) recorder->stop();
  if (impl_->transport) impl_->transport->stop();
}
void Outputs::packet(const std::string &camera, const AVPacket *packet, const Json &metadata) {
  auto clone = [&] { Packet p(av_packet_clone(packet)); if (!p) throw std::bad_alloc(); return Encoded{camera, std::move(p), metadata}; };
  if (auto it = impl_->recorders.find(camera); it != impl_->recorders.end()) it->second->submit(clone());
  if (impl_->transport) impl_->transport->submit(clone());
}
void Outputs::metadata(const Json &record) {
  // Missing-frame records have no measured time; retain them in JSONL rather
  // than inventing a PES presentation timestamp. Ground also detects sequence gaps.
  if (impl_->transport && record.contains("pts_us") && !record["pts_us"].is_null())
    impl_->transport->submit({"", nullptr, record});
}
Json Outputs::stats() const {
  Json out{{"recorders", Json::object()}, {"transport", nullptr}};
  for (const auto &[id, r] : impl_->recorders)
    out["recorders"][id] = {{"failed", r->failed.load()}, {"queue", r->queue.size()}, {"queue_high_water", r->queue.high_water()}, {"dropped_packets", r->drops.load()}, {"written_packets", r->written.load()}};
  if (auto &t = impl_->transport; t)
    out["transport"] = {{"failed", t->failed.load()}, {"queue", t->queue.size()}, {"queue_high_water", t->queue.high_water()}, {"dropped_packets", t->drops.load()}, {"video_packets", t->packets.load()}, {"datagram_errors", t->datagram_errors.load()}};
  return out;
}
}  // namespace pv
