// Integration test of the real recorder/transport code using prerecorded H264.
// No camera or synthetic acquisition is substituted into pv-capture.
#include "pv/outputs.hpp"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cassert>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <map>
#include <limits>
extern "C" {
#include <libavcodec/bsf.h>
}

void check_transport_clock(const std::filesystem::path &path) {
  std::ifstream file(path, std::ios::binary);
  std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(file)), {});
  assert(bytes.size() % 188 == 0);
  double anchor_time = 0; std::size_t anchor_byte = 0; bool anchored = false;
  std::map<int,double> deadlines;
  int late_pcr_on_a = 0;
  auto timestamp = [](const unsigned char *p) {
    auto value = std::uint64_t((p[0]>>1)&7)*(1ULL<<30) + std::uint64_t(p[1])*(1ULL<<22) +
      std::uint64_t(p[2]>>1)*(1ULL<<15) + std::uint64_t(p[3])*128 + (p[4]>>1);
    return value / 90000.0;
  };
  for (std::size_t offset = 0; offset < bytes.size(); offset += 188) {
    auto *p = bytes.data()+offset; assert(p[0] == 0x47);
    int pid = ((p[1]&31)<<8)|p[2], afc = (p[3]>>4)&3, payload = 4;
    if (afc&2) {
      int length = p[4];
      if (length >= 7 && (p[5]&16)) {
        auto base = std::uint64_t(p[6])*(1ULL<<25) + std::uint64_t(p[7])*(1ULL<<17) +
          std::uint64_t(p[8])*512 + std::uint64_t(p[9])*2 + (p[10]>>7);
        auto extension = ((p[10]&1)<<8)|p[11];
        double clock = base/90000.0 + extension/27000000.0;
        if (!anchored) { anchor_time = clock; anchor_byte = offset+12; anchored = true; }
        else assert(std::abs(clock-(anchor_time+double(offset+12-anchor_byte)*8/9000000)) < 0.001);
        if (pid == 256 && clock > 3.0) ++late_pcr_on_a;
      }
      payload += length+1;
    }
    if (!(afc&1) || payload >= 188 || (pid != 256 && pid != 257)) continue;
    if ((p[1]&64) && payload+19 <= 188 && p[payload] == 0 && p[payload+1] == 0 && p[payload+2] == 1) {
      int flags = p[payload+7]>>6; assert(flags&2);
      deadlines[pid] = timestamp(p+payload+(flags == 3 ? 14 : 9));
    }
    if (anchored && deadlines.contains(pid)) {
      double packet_end = anchor_time + double(offset+188-anchor_byte)*8/9000000;
      assert(packet_end <= deadlines[pid]);
    }
  }
  assert(anchored && deadlines.size() == 2 && late_pcr_on_a > 10);
}

int main(int argc, char **argv) {
  if (argc < 3 || argc > 4) { std::cerr << "usage: pv-media-test input-h264.mp4 empty-output-directory [storage-floor|transport-failure]\n"; return 2; }
  try {
    const std::string scenario = argc == 4 ? argv[3] : "normal";
    if (scenario != "normal" && scenario != "storage-floor" && scenario != "transport-failure") throw std::runtime_error("unknown test scenario");
    pv::Config config; config.session_dir = argv[2]; config.segment_seconds = 1; config.min_free_bytes = 0;
    if (scenario == "storage-floor") config.min_free_bytes = std::numeric_limits<std::uintmax_t>::max();
    if (std::filesystem::exists(config.session_dir)) throw std::runtime_error("test directory must not exist");
    std::filesystem::create_directories(config.session_dir);
    AVFormatContext *input = nullptr;
    pv::av_check(avformat_open_input(&input, argv[1], nullptr, nullptr), "open fixture");
    pv::av_check(avformat_find_stream_info(input, nullptr), "inspect fixture");
    int video = av_find_best_stream(input, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    pv::av_check(video, "find fixture video");
    AVBSFContext *filter = nullptr;
    pv::av_check(av_bsf_alloc(av_bsf_get_by_name("h264_mp4toannexb"), &filter), "create Annex-B filter");
    pv::av_check(avcodec_parameters_copy(filter->par_in, input->streams[video]->codecpar), "copy fixture parameters");
    filter->time_base_in = input->streams[video]->time_base;
    pv::av_check(av_bsf_init(filter), "initialize Annex-B filter");
    AVCodecContext *codec = avcodec_alloc_context3(nullptr); assert(codec);
    pv::av_check(avcodec_parameters_to_context(codec, filter->par_out), "create stream description");
    codec->time_base = {1,1000000};
    std::vector<pv::Packet> frames;
    pv::Packet packet(av_packet_alloc());
    while (frames.size() < 120 && av_read_frame(input, packet.get()) >= 0) {
      if (packet->stream_index != video) { av_packet_unref(packet.get()); continue; }
      pv::av_check(av_bsf_send_packet(filter, packet.get()), "filter fixture packet");
      while (av_bsf_receive_packet(filter, packet.get()) >= 0) {
        packet->pts = packet->dts = static_cast<std::int64_t>(frames.size()) * 1000000 / 30;
        packet->duration = 1000000 / 30;
        frames.emplace_back(av_packet_clone(packet.get())); av_packet_unref(packet.get());
      }
    }
    avformat_close_input(&input); av_bsf_free(&filter);
    assert(frames.size() == 120 && (frames[0]->flags & AV_PKT_FLAG_KEY));
    int socket = ::socket(AF_INET, SOCK_DGRAM, 0); assert(socket >= 0);
    sockaddr_in address{}; address.sin_family = AF_INET; address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    assert(bind(socket, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0);
    socklen_t length = sizeof(address); assert(getsockname(socket, reinterpret_cast<sockaddr *>(&address), &length) == 0);
    timeval timeout{0,100000}; setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    int socket_buffer = 4*1024*1024; setsockopt(socket, SOL_SOCKET, SO_RCVBUF, &socket_buffer, sizeof(socket_buffer));
    config.udp_destination = "127.0.0.1:" + std::to_string(ntohs(address.sin_port));
    if (scenario == "transport-failure") config.udp_destination = "127.0.0.1:0";
    std::atomic<bool> finished{false}; std::atomic<int> datagrams{0};
    std::thread receiver([&] {
      std::ofstream file(config.session_dir/"received.ts", std::ios::binary);
      unsigned char data[2048]; int idle = 0;
      while (!finished || idle < 2) {
        auto n = recv(socket, data, sizeof(data), 0);
        if (n < 0) { ++idle; continue; }
        idle = 0; assert(n <= 1316 && n % 188 == 0);
        for (int i = 0; i < n; i += 188) assert(data[i] == 0x47);
        file.write(reinterpret_cast<char *>(data), n); ++datagrams;
      }
    });
    pv::Logs logs(config.session_dir);
    auto origin = pv::boot_ns();
    pv::Outputs output(config, logs, {{"A",codec}, {"B",codec}}, origin);
    auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < 120; ++i) {
      std::this_thread::sleep_until(start + std::chrono::microseconds(i*1000000/30));
      for (const auto &id : {"A", "B"}) {
        if (id[0] == 'A' && i >= 60) continue;  // PCR owner stops halfway.
        pv::Json metadata{{"schema_version",1}, {"type","frame"}, {"camera_id",id}, {"sequence",i}, {"pts_us",frames[i]->pts}, {"drop_reason",nullptr}};
        output.packet(id, frames[i].get(), metadata);
      }
      if (i % 15 == 0) output.metadata({{"schema_version",1}, {"type","health"}, {"pts_us",(pv::boot_ns()-origin)/1000}});
    }
    output.stop(); logs.finish(); finished = true; receiver.join(); close(socket);
    auto stats = output.stats(); std::cout << stats.dump(2) << '\n';
    if (scenario == "storage-floor") {
      assert(stats["recorders"]["A"]["failed"].get<bool>() && stats["recorders"]["B"]["failed"].get<bool>());
      assert(stats["recorders"]["A"]["written_packets"] == 0 && stats["recorders"]["B"]["written_packets"] == 0);
    } else {
      assert(stats["recorders"]["A"]["written_packets"] == 60 && stats["recorders"]["B"]["written_packets"] == 120);
    }
    if (scenario == "transport-failure") {
      assert(stats["transport"]["failed"].get<bool>() && datagrams == 0);
    } else {
    assert(!stats["transport"]["failed"].get<bool>() && stats["transport"]["datagram_errors"] == 0);
    assert(stats["transport"]["dropped_packets"] == 0 && datagrams > 100);
    check_transport_clock(config.session_dir/"received.ts");
    // Demux the actual received TS: both PID identity and packet counts survive A stopping.
    AVFormatContext *received = nullptr;
    pv::av_check(avformat_open_input(&received, (config.session_dir/"received.ts").c_str(), nullptr, nullptr), "read transport");
    pv::av_check(avformat_find_stream_info(received, nullptr), "inspect transport");
    std::map<int,int> counts; int metadata_count = 0;
    while (av_read_frame(received, packet.get()) >= 0) {
      auto *stream = received->streams[packet->stream_index];
      ++counts[stream->id];
      if (stream->id == 258) {
        auto record = pv::Json::parse(packet->data, packet->data+packet->size);
        assert(record.contains("type")); ++metadata_count;
      }
      av_packet_unref(packet.get());
    }
    assert(counts[256] == 60 && counts[257] == 120 && metadata_count >= 180);
    avformat_close_input(&received);
    }
    // Finalized one-second segments must all begin at an independently decodable IDR.
    int segments = 0;
    for (const auto &entry : std::filesystem::directory_iterator(config.session_dir)) {
      if (entry.path().extension() != ".mkv") continue;
      AVFormatContext *segment = nullptr;
      pv::av_check(avformat_open_input(&segment, entry.path().c_str(), nullptr, nullptr), "read segment");
      pv::av_check(av_read_frame(segment, packet.get()), "first segment packet");
      assert(packet->flags & AV_PKT_FLAG_KEY); av_packet_unref(packet.get());
      avformat_close_input(&segment); ++segments;
    }
    assert(scenario == "storage-floor" ? segments == 0 : segments >= 6);
    avcodec_free_context(&codec);
    std::cout << "PASS: " << scenario << ": independent sinks, bounded queues, keyframe segments; valid PCR/decode deadlines and private JSON when streaming\n";
    return 0;
  } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
