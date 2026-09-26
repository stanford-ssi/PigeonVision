#pragma once
#include "pv/config.hpp"
#include "pv/log.hpp"
#include "pv/outputs.hpp"
#include <libcamera/camera_manager.h>
#include <memory>

namespace pv {
class CameraSession {
 public:
  CameraSession(libcamera::CameraManager &manager, const Config &config,
                const CameraConfig &camera, Logs &logs, std::int64_t origin);
  ~CameraSession();
  void start(Outputs &outputs);
  void stop();
  StreamInfo stream_info() const;
  Json description() const;
  Json stats() const;
  bool failed() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
Json enumerate_cameras(libcamera::CameraManager &manager);
}  // namespace pv
