#include "pv/camera.hpp"
#include "pv/config.hpp"
#include "pv/log.hpp"
#include "pv/outputs.hpp"
#include "pv/session.hpp"
#include "pv/supervisor.hpp"
#include "pv/sensors.hpp"
#include "pv/flight_uart.hpp"
#include "pv/telemetry_batcher.hpp"
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
void signal_handler(int signal) {
  interrupted = signal;
}
std::int64_t monotonic_ns() {
  timespec ts{};
  if (clock_gettime(CLOCK_MONOTONIC, &ts))
    throw std::runtime_error("CLOCK_MONOTONIC unavailable");
  return std::int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}
void write_status(const std::filesystem::path &path, const pv::Json &record) {
  std::ofstream file(path.string() + ".tmp");
  file << record.dump() << '\n';
  file.close();
  if (!file)
    return;
  std::error_code error;
  std::filesystem::rename(path.string() + ".tmp", path, error);
}
std::string device_model() {
  std::ifstream f("/proc/device-tree/model");
  std::string model;
  std::getline(f, model, '\0');
  return model;
}
} // namespace
int main(int argc, char **argv) {
  try {
    if (argc == 2 && std::string(argv[1]) == "--help") {
      std::cout << "pv-capture --list-cameras\npv-capture --config path.json\npv-capture --status "
                   "[path.json]\n";
      return 0;
    }
    if ((argc == 2 || argc == 3) && std::string(argv[1]) == "--status") {
      std::ifstream status(argc == 3 ? argv[2] : "/run/pv-capture-status.json");
      if (!status)
        return 1;
      std::cout << status.rdbuf();
      return 0;
    }
    const bool list = argc == 2 && std::string(argv[1]) == "--list-cameras";
    if (!list && !(argc == 3 && std::string(argv[1]) == "--config")) {
      std::cerr << "usage: pv-capture --list-cameras | --config path.json\n";
      return 2;
    }
    libcamera::CameraManager manager;
    if (list) {
      if (manager.start() < 0)
        throw std::runtime_error("cannot start CameraManager");
      std::cout << pv::enumerate_cameras(manager).dump(2) << '\n';
      return 0;
    }
    auto config = pv::Config::read(argv[2]);
    pv::CaptureLock capture_lock(config.lock_path);
    pv::Supervisor supervisor;
    supervisor.transition(pv::Lifecycle::initializing);
    const bool camera_manager_ready = manager.start() >= 0;
    supervisor.component("camera_manager", camera_manager_ready ? "ready" : "failed");
    std::error_code existence_error;
    if (std::filesystem::exists(config.session_dir / "session.json", existence_error))
      throw std::runtime_error("session.json already exists; choose a new session directory");
    std::error_code storage_error;
    std::filesystem::create_directories(config.session_dir, storage_error);
    pv::Logs logs(config.session_dir);
    const auto mono_before = monotonic_ns(), origin = pv::boot_ns(), mono_after = monotonic_ns();
    std::unique_ptr<pv::Outputs> outputs;
    std::vector<std::unique_ptr<pv::CameraSession>> cameras;
    std::vector<pv::StreamInfo> streams;
    pv::Json descriptions = pv::Json::array();
    for (const auto &camera : config.cameras) {
      try {
        if (!camera_manager_ready)
          throw std::runtime_error("CameraManager unavailable");
        auto session = std::make_unique<pv::CameraSession>(manager, config, camera, logs, origin);
        descriptions.push_back(session->description());
        if (config.encode)
          streams.push_back(session->stream_info());
        cameras.push_back(std::move(session));
      } catch (const std::exception &e) {
        supervisor.component(camera.id, "failed");
        logs.event("camera", "initialization_failed", camera.id, e.what());
      }
    }
    utsname system{};
    uname(&system);
    pv::Json manifest{{"schema_version", 1},
                      {"session_id", config.session_dir.filename().string()},
                      {"created_utc", pv::utc_now()},
                      {"clock_origin_ns", origin},
                      {"clock_domain", "CLOCK_BOOTTIME"},
                      {"clock_sample",
                       {{"monotonic_before_ns", mono_before},
                        {"boottime_ns", origin},
                        {"monotonic_after_ns", mono_after}}},
                      {"configuration", config.effective()},
                      {"software",
                       {{"capture_version", "0.1.0"},
                        {"libcamera", libcamera::CameraManager::version()},
                        {"ffmpeg", av_version_info()}}},
                      {"hardware",
                       {{"model", device_model()},
                        {"kernel", system.release},
                        {"architecture", system.machine},
                        {"cameras", descriptions}}},
                      {"calibration", nullptr},
                      {"sensor_timestamp_optically_verified", false}};
    manifest["transport"] = {{"pts_offset_us", 1000000},
                             {"mux_delay_us", 500000},
                             {"metadata_pes_clock", "producer_queue_admission"},
                             {"video_pids", {{"A", 256}, {"B", 257}}},
                             {"metadata_pid", 258}};
    {
      std::ofstream file(config.session_dir / "session.json.tmp");
      file << manifest.dump(2) << '\n';
      file.flush();
      if (!file)
        logs.event("storage", "manifest_failed");
    }
    std::filesystem::rename(config.session_dir / "session.json.tmp",
                            config.session_dir / "session.json", storage_error);
    outputs = std::make_unique<pv::Outputs>(config, logs, streams, origin);
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);
    std::signal(SIGPIPE, SIG_IGN);
    for (auto &camera : cameras) {
      try {
        camera->start(*outputs);
      } catch (const std::exception &e) {
        logs.event("camera", "start_failed", "", e.what());
        camera->stop();
      }
    }
    pv::TelemetryBatcher telemetry;
    auto publish_sample = [&](pv::Json record) {
      const auto now = pv::boot_ns();
      record["pts_us"] = (now - origin) / 1000;
      record["publication_timestamp_ns"] = now;
      record["clock_domain"] = "CLOCK_BOOTTIME";
      record["acquisition_clock_domain"] =
          record.value("host_clock_domain", std::string("CLOCK_BOOTTIME"));
      logs.sensor(record);
      if (record.value("type", std::string()) == "sensor_sample")
        telemetry.add(record);
      else
        outputs->metadata(record);
    };
    pv::SensorWorker sensors(config.original.value("sensors", pv::Json{{"backend", "disabled"}}),
                             publish_sample);
    pv::FlightUartWorker uart(
        config.original.value("flight_uart", pv::Json{{"backend", "disabled"}}), publish_sample);
    sensors.start();
    uart.start();
    supervisor.transition(pv::Lifecycle::running);
    const auto run_start = pv::boot_ns();
    logs.event("session", "started");
    std::cout << pv::Json{{"type", "session"},
                          {"session_dir", config.session_dir.string()},
                          {"camera_count", cameras.size()}}
                     .dump()
              << std::endl;
    pv::Json wire_session{{"schema_version", 1},
                          {"type", "session"},
                          {"session_id", manifest["session_id"]},
                          {"clock_origin_ns", origin},
                          {"clock_domain", "CLOCK_BOOTTIME"},
                          {"cameras", descriptions},
                          {"transport_pts_offset_us", 1000000},
                          {"metadata_pes_clock", "producer_queue_admission"},
                          {"pts_us", (run_start - origin) / 1000}};
    outputs->metadata(wire_session);
    auto next_health = run_start;
    auto next_telemetry = run_start;
    bool runtime_failed = cameras.size() != config.cameras.size();
    while (!interrupted) {
      const auto now = pv::boot_ns();
      if (now >= next_telemetry) {
        for (auto &batch :
             telemetry.take(manifest["session_id"].get<std::string>(), (now - origin) / 1000))
          outputs->metadata(batch);
        next_telemetry = now + 100000000;
      }
      if (config.duration_seconds && (now - run_start) / 1e9 >= config.duration_seconds)
        break;
      for (auto &camera : cameras) {
        const auto state = camera->stats();
        const auto last = state["last_frame_ns"].get<std::int64_t>();
        if (state["active"].get<bool>() &&
            (camera->failed() || now - (last ? last : run_start) > 2000000000LL)) {
          logs.event("camera", camera->failed() ? "disabled_after_failure" : "stalled",
                     state["camera_id"].get<std::string>());
          camera->stop();
          runtime_failed = true;
        }
        supervisor.component(state["camera_id"].get<std::string>(),
                             camera->failed()                        ? "failed"
                             : camera->stats()["active"].get<bool>() ? "capturing"
                                                                     : "disabled");
      }
      supervisor.component("storage", logs.failed() ? "failed" : "recording");
      if (now >= next_health) {
        auto health = pv::system_health();
        health["cameras"] = pv::Json::array();
        std::size_t healthy = 0;
        for (auto &camera : cameras) {
          auto s = camera->stats();
          if (s["active"].get<bool>() && !camera->failed())
            ++healthy;
          health["cameras"].push_back(std::move(s));
        }
        health["sensors"] = sensors.stats();
        health["flight_uart"] = uart.stats();
        health["phase"] = health["flight_uart"].value("phase", std::string("UNKNOWN"));
        health["phase_stale"] = health["flight_uart"].value("stale", true);
        health["degraded"] = cameras.size() != config.cameras.size() ||
                             healthy != config.cameras.size() || logs.failed();
        health["lifecycle"] = pv::name(supervisor.state());
        health["components"] = supervisor.components();
        health["outputs"] = outputs->stats();
        const auto &transport = health["outputs"]["transport"];
        if (!transport.is_null()) {
          const bool failed = transport.value("failed", false);
          supervisor.component("transport", failed ? "failed" : "transmitting");
          health["degraded"] = health["degraded"].get<bool>() || failed;
        }
        for (const auto &[id, recorder] : health["outputs"]["recorders"].items()) {
          const bool failed = recorder.value("failed", false);
          supervisor.component("recording_" + id, failed ? "failed" : "recording");
          health["degraded"] = health["degraded"].get<bool>() || failed;
        }
        if (health["sensors"]["backend"] != "disabled") {
          for (const auto &[id, sensor] : health["sensors"]["devices"].items()) {
            const auto last_good = sensor.value("last_good_monotonic_us", pv::Json(nullptr));
            const auto status = sensor.value("status", std::string("unavailable"));
            const bool fresh = (status == "fresh" || status == "not_ready") &&
                               last_good.is_number_integer() &&
                               now / 1000 - last_good.get<std::int64_t>() < 1000000;
            supervisor.component(id, fresh ? "receiving" : sensor.value("status", "unavailable"));
            health["degraded"] = health["degraded"].get<bool>() || !fresh;
          }
        }
        if (config.original.value("flight_uart", pv::Json::object()).value("backend", "disabled") !=
            "disabled") {
          const bool stale = health["flight_uart"].value("stale", true);
          supervisor.component("flight_uart", stale ? "stale" : "receiving");
          health["degraded"] = health["degraded"].get<bool>() || stale;
        }
        health["components"] = supervisor.components();
        health["log_records_lost"] = logs.lost();
        health["log_failed"] = logs.failed();
        health["type"] = "health";
        health["pts_us"] = (now - origin) / 1000;
        health["clock_domain"] = "CLOCK_BOOTTIME";
        health["timestamp_ns"] = now;
        health["schema_version"] = 1;
        wire_session["pts_us"] = (now - origin) / 1000;
        outputs->metadata(
            wire_session); // Late UDP joins recover geometry/timeline at the next GOP.
        outputs->metadata(health);
        logs.health(health);
        std::cout << health.dump() << std::endl;
        write_status(config.status_path, health);
        next_health = now + 1000000000;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    supervisor.transition(pv::Lifecycle::stopping);
    sensors.stop();
    uart.stop();
    for (auto &batch :
         telemetry.take(manifest["session_id"].get<std::string>(), (pv::boot_ns() - origin) / 1000))
      outputs->metadata(batch);
    for (auto &camera : cameras) {
      camera->stop();
      if (camera->failed())
        runtime_failed = true;
    }
    outputs->stop();
    auto final_stats = outputs->stats();
    for (const auto &[_, recorder] : final_stats["recorders"].items())
      if (recorder["failed"].get<bool>())
        runtime_failed = true;
    if (!final_stats["transport"].is_null() && final_stats["transport"]["failed"].get<bool>())
      runtime_failed = true;
    logs.health({{"type", "session_end"},
                 {"signal", int(interrupted)},
                 {"outputs", final_stats},
                 {"failed", runtime_failed}});
    logs.finish();
    supervisor.transition(pv::Lifecycle::stopped);
    {
      pv::Json stopped{{"type", "health"},
                       {"lifecycle", "stopped"},
                       {"timestamp_ns", pv::boot_ns()},
                       {"outputs", final_stats},
                       {"failed", runtime_failed},
                       {"session_id", manifest["session_id"]}};
      write_status(config.status_path, stopped);
    }
    if (config.spi && final_stats.contains("transport") && !final_stats["transport"].is_null() &&
        final_stats["transport"].contains("spi") &&
        final_stats["transport"]["spi"].value("transfer_uncertain", false))
      return 78;
    return runtime_failed || logs.failed() || logs.lost() ? 1 : 0;
  } catch (const std::exception &e) {
    std::cerr << "pv-capture: " << e.what() << '\n';
    return 1;
  }
}
