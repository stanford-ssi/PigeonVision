#include "pv/camera.hpp"
#include "pv/config.hpp"
#include "pv/log.hpp"
#include "pv/outputs.hpp"
#include <sys/utsname.h>
#include <csignal>
#include <fstream>
#include <iostream>
#include <thread>
extern "C" {
#include <libavutil/avutil.h>
}

namespace {
volatile std::sig_atomic_t interrupted = 0;
void signal_handler(int signal) { interrupted = signal; }
std::int64_t monotonic_ns() {
  timespec ts{}; if (clock_gettime(CLOCK_MONOTONIC, &ts)) throw std::runtime_error("CLOCK_MONOTONIC unavailable");
  return std::int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}
std::string device_model() {
  std::ifstream f("/proc/device-tree/model"); std::string model; std::getline(f, model, '\0'); return model;
}
}
int main(int argc, char **argv) {
  try {
    if (argc == 2 && std::string(argv[1]) == "--help") {
      std::cout << "pv-capture --list-cameras\npv-capture --config path.json\n"; return 0;
    }
    const bool list = argc == 2 && std::string(argv[1]) == "--list-cameras";
    if (!list && !(argc == 3 && std::string(argv[1]) == "--config")) {
      std::cerr << "usage: pv-capture --list-cameras | --config path.json\n"; return 2;
    }
    libcamera::CameraManager manager;
    if (manager.start() < 0) throw std::runtime_error("cannot start libcamera CameraManager");
    if (list) { std::cout << pv::enumerate_cameras(manager).dump(2) << '\n'; return 0; }
    auto config = pv::Config::read(argv[2]);
    if (std::filesystem::exists(config.session_dir / "session.json"))
      throw std::runtime_error("session.json already exists; choose a new session directory");
    std::filesystem::create_directories(config.session_dir);
    pv::Logs logs(config.session_dir);
    const auto mono_before = monotonic_ns(), origin = pv::boot_ns(), mono_after = monotonic_ns();
    std::unique_ptr<pv::Outputs> outputs;
    std::vector<std::unique_ptr<pv::CameraSession>> cameras;
    std::vector<pv::StreamInfo> streams;
    pv::Json descriptions = pv::Json::array();
    for (const auto &camera : config.cameras) {
      auto session = std::make_unique<pv::CameraSession>(manager, config, camera, logs, origin);
      descriptions.push_back(session->description());
      if (config.encode) streams.push_back(session->stream_info());
      cameras.push_back(std::move(session));
    }
    utsname system{}; uname(&system);
    pv::Json manifest{{"schema_version", 1}, {"session_id", config.session_dir.filename().string()},
      {"created_utc", pv::utc_now()}, {"clock_origin_ns", origin}, {"clock_domain", "CLOCK_BOOTTIME"},
      {"clock_sample", {{"monotonic_before_ns", mono_before}, {"boottime_ns", origin}, {"monotonic_after_ns", mono_after}}},
      {"configuration", config.effective()}, {"software", {{"capture_version", "0.1.0"}, {"libcamera", libcamera::CameraManager::version()}, {"ffmpeg", av_version_info()}}},
      {"hardware", {{"model", device_model()}, {"kernel", system.release}, {"architecture", system.machine}, {"cameras", descriptions}}},
      {"calibration", nullptr}, {"sensor_timestamp_optically_verified", false}};
    manifest["transport"] = {{"pts_offset_us", 1000000}, {"mux_delay_us", 500000},
      {"metadata_pes_clock", "mux_admission"}, {"video_pids", {{"A",256},{"B",257}}}, {"metadata_pid",258}};
    {
      std::ofstream file(config.session_dir / "session.json.tmp");
      file << manifest.dump(2) << '\n'; file.flush();
      if (!file) throw std::runtime_error("cannot write session manifest");
    }
    std::filesystem::rename(config.session_dir / "session.json.tmp", config.session_dir / "session.json");
    outputs = std::make_unique<pv::Outputs>(config, logs, streams, origin);
    std::signal(SIGINT, signal_handler); std::signal(SIGTERM, signal_handler);
    std::signal(SIGPIPE, SIG_IGN);
    for (auto &camera : cameras) camera->start(*outputs);
    const auto run_start = pv::boot_ns();
    logs.event("session", "started");
    std::cout << pv::Json{{"type", "session"}, {"session_dir", config.session_dir.string()}, {"camera_count", cameras.size()}}.dump() << std::endl;
    pv::Json wire_session{{"schema_version", 1}, {"type", "session"}, {"session_id", manifest["session_id"]},
                          {"clock_origin_ns", origin}, {"clock_domain", "CLOCK_BOOTTIME"}, {"cameras", descriptions},
                          {"transport_pts_offset_us",1000000}, {"metadata_pes_clock","mux_admission"}, {"pts_us", (run_start-origin)/1000}};
    outputs->metadata(wire_session);
    auto next_health = run_start;
    bool runtime_failed = false;
    while (!interrupted) {
      const auto now = pv::boot_ns();
      if (config.duration_seconds && (now-run_start) / 1e9 >= config.duration_seconds) break;
      if (now >= next_health) {
        auto health = pv::system_health(); health["cameras"] = pv::Json::array();
        std::size_t healthy = 0;
        for (auto &camera : cameras) {
          auto s = camera->stats();
          auto last = s["last_frame_ns"].get<std::int64_t>();
          bool stalled = now - (last ? last : run_start) > 2000000000LL;
          if ((stalled || camera->failed()) && s["active"].get<bool>()) {
            logs.event("camera", stalled ? "stalled" : "disabled_after_failure", s["camera_id"].get<std::string>());
            camera->stop(); runtime_failed = true; s = camera->stats();
          }
          if (s["active"].get<bool>() && !camera->failed()) ++healthy;
          health["cameras"].push_back(std::move(s));
        }
        health["outputs"] = outputs->stats(); health["log_records_lost"] = logs.lost(); health["log_failed"] = logs.failed();
        health["type"] = "health"; health["pts_us"] = (now-origin)/1000;
        health["timestamp_ns"] = now; health["schema_version"] = 1;
        wire_session["pts_us"] = (now-origin)/1000;
        outputs->metadata(wire_session);  // Late UDP joins recover geometry/timeline at the next GOP.
        outputs->metadata(health); logs.health(health);
        std::cout << health.dump() << std::endl;
        next_health = now + 1000000000;
        if (!healthy) { runtime_failed = true; break; }
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    for (auto &camera : cameras) { camera->stop(); if (camera->failed()) runtime_failed = true; }
    outputs->stop();
    auto final_stats = outputs->stats();
    for (const auto &[_, recorder] : final_stats["recorders"].items()) if (recorder["failed"].get<bool>()) runtime_failed = true;
    if (!final_stats["transport"].is_null() && final_stats["transport"]["failed"].get<bool>()) runtime_failed = true;
    logs.health({{"type", "session_end"}, {"signal", int(interrupted)}, {"outputs", final_stats}, {"failed", runtime_failed}});
    logs.finish();
    return runtime_failed || logs.failed() || logs.lost() ? 1 : 0;
  } catch (const std::exception &e) {
    std::cerr << "pv-capture: " << e.what() << '\n'; return 1;
  }
}
