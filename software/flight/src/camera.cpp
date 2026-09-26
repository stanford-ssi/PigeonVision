#include "pv/camera.hpp"
#include "pv/colour.hpp"
#include "pv/core.hpp"
#include "pv/framos.hpp"
#include <libcamera/camera.h>
#include <libcamera/control_ids.h>
#include <libcamera/formats.h>
#include <libcamera/framebuffer.h>
#include <libcamera/framebuffer_allocator.h>
#include <libcamera/property_ids.h>
#include <linux/dma-buf.h>
#include <linux/videodev2.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <fstream>
#include <iterator>
#include <map>
#include <mutex>
#include <set>
#include <thread>
extern "C" {
#include <libavutil/opt.h>
}

namespace pv {
namespace {
constexpr AVRational us_timebase{1, 1000000};
Json rectangle(const libcamera::Rectangle &r) { return Json::array({r.x, r.y, r.width, r.height}); }
void camera_check(int status, const char *what) {
  if (status < 0) throw std::runtime_error(std::string(what) + ": " + std::strerror(-status));
}
struct ControlDevice {
  int fd;
  explicit ControlDevice(const std::filesystem::path &path) : fd(open(path.c_str(), O_RDWR | O_CLOEXEC)) {
    if (fd < 0) throw std::runtime_error("open sensor control " + path.string() + ": " + std::strerror(errno));
  }
  ~ControlDevice() { close(fd); }
  void call(unsigned long request, void *value, const char *operation) const {
    int status;
    do { status = ioctl(fd, request, value); } while (status < 0 && errno == EINTR);
    if (status < 0) throw std::runtime_error(std::string(operation) + ": " + std::strerror(errno));
  }
};
Json configure_framos_rate(libcamera::Camera &camera, libcamera::CameraConfiguration &configuration, unsigned fps) {
  const auto sensor = resolve_sensor_node(camera.id());
  std::ifstream compatible(sensor.of_node / "compatible", std::ios::binary);
  const std::string properties{std::istreambuf_iterator<char>(compatible), std::istreambuf_iterator<char>()};
  bool framos = false;
  for (std::size_t begin = 0; begin < properties.size();) {
    const auto end = properties.find('\0', begin);
    if (properties.substr(begin, end - begin) == "framos,fr_imx900") framos = true;
    if (end == std::string::npos) break;
    begin = end + 1;
  }
  if (!framos) throw std::runtime_error("camera device-tree compatible is not framos,fr_imx900");
  ControlDevice device(sensor.device);
  v4l2_queryctrl query{}; query.id = framos_frame_rate_id;
  device.call(VIDIOC_QUERYCTRL, &query, "query FRAMOS Frame rate control");
  const std::string name(reinterpret_cast<const char *>(query.name), strnlen(reinterpret_cast<const char *>(query.name), sizeof(query.name)));
  const auto requested = validate_framos_rate(fps, query.id, name, query.minimum, query.maximum, query.step,
      query.type == V4L2_CTRL_TYPE_INTEGER && !(query.flags & (V4L2_CTRL_FLAG_DISABLED | V4L2_CTRL_FLAG_READ_ONLY |
                                                            V4L2_CTRL_FLAG_INACTIVE | V4L2_CTRL_FLAG_GRABBED)));
  v4l2_control rate{}; rate.id = framos_frame_rate_id; rate.value = requested;
  device.call(VIDIOC_S_CTRL, &rate, "set FRAMOS Frame rate control");
  device.call(VIDIOC_G_CTRL, &rate, "read FRAMOS Frame rate control");
  if (rate.value != requested) throw std::runtime_error("FRAMOS Frame rate readback differs from requested micro-fps");

  // The vendor driver exposes VBLANK as a fixed range derived from its private
  // rate. Reconfigure the IDENTICAL mode to refresh libcamera/IPA's cached
  // VBLANK and exposure limits. fr_imx900_set_pad_format preserves the private
  // rate when the mode pointer is unchanged; assert that behavior below.
  camera_check(camera.configure(&configuration), "refresh libcamera timing after FRAMOS frame rate");
  device.call(VIDIOC_G_CTRL, &rate, "verify FRAMOS rate after identical-mode configure");
  if (rate.value != requested) throw std::runtime_error("identical-mode configure reset FRAMOS Frame rate; refusing stale IPA timing");
  const auto limits = camera.controls().find(&libcamera::controls::FrameDurationLimits);
  if (limits == camera.controls().end()) throw std::runtime_error("refreshed FrameDurationLimits unavailable");
  const auto minimum_us = limits->second.min().get<std::int64_t>();
  const auto maximum_us = limits->second.max().get<std::int64_t>();
  const auto requested_us = std::int64_t(1'000'000 / fps);
  // Sensor line quantization and integer nanosecond line-time calculations
  // can put the fixed advertised duration just below the integer request.
  const auto tolerance_us = std::max<std::int64_t>(100, requested_us / 1000);
  if (std::abs(minimum_us - requested_us) > tolerance_us || std::abs(maximum_us - requested_us) > tolerance_us)
    throw std::runtime_error("libcamera retained stale frame-duration limits after FRAMOS control refresh: " +
                             std::to_string(minimum_us) + ".." + std::to_string(maximum_us) + " us");
  v4l2_control vblank{}; vblank.id = V4L2_CID_VBLANK;
  device.call(VIDIOC_G_CTRL, &vblank, "read refreshed sensor VBLANK");
  const auto exposure = camera.controls().find(&libcamera::controls::ExposureTime);
  if (exposure == camera.controls().end()) throw std::runtime_error("refreshed exposure control unavailable");
  return {{"method", "framos_private_rate_then_identical_mode_ipa_refresh"},
          {"control_node", sensor.device.string()}, {"of_node", sensor.of_node.string()},
          {"control_id", query.id}, {"control_name", name}, {"control_units", "micro_fps"},
          {"control_range", {query.minimum, query.maximum, query.step}},
          {"requested_fps", fps}, {"requested_micro_fps", requested}, {"readback_micro_fps", rate.value},
          {"vblank_lines", vblank.value}, {"libcamera_frame_duration_limits_us", {minimum_us, maximum_us}},
          {"libcamera_exposure_max_us", exposure->second.max().get<std::int32_t>()},
          {"measured_cadence_verified", false}};
}
struct Mapping {
  struct Region { void *address; std::size_t length; };
  std::map<int, Region> regions;
  std::vector<std::pair<std::uint8_t *, std::size_t>> planes;
  explicit Mapping(const libcamera::FrameBuffer &buffer) {
    std::map<int, std::size_t> lengths;
    for (const auto &plane : buffer.planes()) {
      if (plane.offset == libcamera::FrameBuffer::Plane::kInvalidOffset) throw std::runtime_error("invalid DMA buffer offset");
      lengths[plane.fd.get()] = std::max(lengths[plane.fd.get()], std::size_t(plane.offset) + plane.length);
    }
    try {
      for (auto [fd, length] : lengths) {
        void *p = mmap(nullptr, length, PROT_READ, MAP_SHARED, fd, 0);
        if (p == MAP_FAILED) throw std::runtime_error(std::string("mmap camera buffer: ") + std::strerror(errno));
        regions[fd] = {p, length};
      }
      for (const auto &plane : buffer.planes())
        planes.emplace_back(static_cast<std::uint8_t *>(regions.at(plane.fd.get()).address) + plane.offset, plane.length);
    } catch (...) { for (auto [_, r] : regions) munmap(r.address, r.length); throw; }
  }
  ~Mapping() { for (auto [_, r] : regions) munmap(r.address, r.length); }
  void begin() {
    std::vector<int> started;
    for (auto [fd, _] : regions) {
      dma_buf_sync sync{DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
      if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0) {
        for (int previous : started) { dma_buf_sync end{DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ}; ioctl(previous, DMA_BUF_IOCTL_SYNC, &end); }
        throw std::runtime_error(std::string("DMA_BUF_SYNC_START: ") + std::strerror(errno));
      }
      started.push_back(fd);
    }
  }
  void end() noexcept {
    for (auto [fd, _] : regions) { dma_buf_sync sync{DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ}; ioctl(fd, DMA_BUF_IOCTL_SYNC, &sync); }
  }
};
struct FrameDelete { void operator()(AVFrame *p) const { av_frame_free(&p); } };
using Frame = std::unique_ptr<AVFrame, FrameDelete>;
struct Lease {
  Mapping &mapping;
  std::function<void()> release;
  ~Lease() { mapping.end(); release(); }
};
void release_buffer(void *opaque, std::uint8_t *) { delete static_cast<std::shared_ptr<Lease> *>(opaque); }
Frame av_frame(Mapping &mapping, unsigned width, unsigned height, unsigned stride,
               const std::shared_ptr<Lease> &lease, std::int64_t pts) {
  Frame frame(av_frame_alloc()); if (!frame) throw std::bad_alloc();
  frame->format = AV_PIX_FMT_YUV420P; frame->width = width; frame->height = height; frame->pts = pts;
  frame->color_range = AVCOL_RANGE_MPEG; frame->colorspace = AVCOL_SPC_BT709;
  frame->color_primaries = AVCOL_PRI_BT709; frame->color_trc = AVCOL_TRC_BT709;
  const std::size_t y = std::size_t(stride) * height, uv = std::size_t(stride / 2) * (height / 2);
  if (mapping.planes.size() == 1) {
    if (mapping.planes[0].second < y + 2 * uv) throw std::runtime_error("YUV420 buffer shorter than negotiated layout");
    frame->data[0] = mapping.planes[0].first; frame->data[1] = frame->data[0] + y; frame->data[2] = frame->data[1] + uv;
  } else if (mapping.planes.size() == 3) {
    if (mapping.planes[0].second < y || mapping.planes[1].second < uv || mapping.planes[2].second < uv)
      throw std::runtime_error("YUV420 plane shorter than negotiated layout");
    for (int i = 0; i < 3; ++i) frame->data[i] = mapping.planes[i].first;
  } else throw std::runtime_error("unsupported YUV420 plane layout");
  frame->linesize[0] = stride; frame->linesize[1] = frame->linesize[2] = stride / 2;
  for (std::size_t i = 0; i < mapping.planes.size(); ++i) {
    auto *reference = new std::shared_ptr<Lease>(lease);
    frame->buf[i] = av_buffer_create(mapping.planes[i].first, mapping.planes[i].second, release_buffer, reference, AV_BUFFER_FLAG_READONLY);
    if (!frame->buf[i]) { delete reference; throw std::bad_alloc(); }
  }
  return frame;
}
}  // namespace

struct CameraSession::Impl {
  const Config &config;
  CameraConfig settings;
  Logs &logs;
  std::int64_t origin;
  std::shared_ptr<libcamera::Camera> camera;
  std::unique_ptr<libcamera::CameraConfiguration> camera_config;
  std::unique_ptr<libcamera::FrameBufferAllocator> allocator;
  std::vector<std::unique_ptr<libcamera::Request>> requests;
  std::map<libcamera::FrameBuffer *, std::unique_ptr<Mapping>> mappings;
  libcamera::Stream *stream = nullptr;
  libcamera::Rectangle crop;
  libcamera::Rectangle full_sensor_crop;
  Json frame_rate_control;
  AVCodecContext *encoder = nullptr;
  Outputs *outputs = nullptr;
  struct Capture { libcamera::Request *request; libcamera::FrameBuffer *buffer; Json metadata; };
  BoundedQueue<Capture> queue{3};
  std::thread worker;
  std::mutex state_mutex;
  std::atomic<bool> active{false}, failure{false};
  bool acquired = false;
  std::atomic<std::uint64_t> captured{0}, encoded{0}, dropped{0};
  std::atomic<std::int64_t> last_frame_ns{0};
  std::int64_t last_pts = -1;
  std::optional<std::uint32_t> last_sequence;
  std::map<std::int64_t, Json> pending_metadata;

  Impl(libcamera::CameraManager &manager, const Config &c, const CameraConfig &s, Logs &l, std::int64_t epoch)
      : config(c), settings(s), logs(l), origin(epoch) {
    camera = manager.get(settings.device);
    if (!camera) throw std::runtime_error("camera ID unavailable: " + settings.device);
    camera_check(camera->acquire(), "acquire camera"); acquired = true;
    try { configure(); if (config.encode) configure_encoder(); }
    catch (...) { cleanup(); throw; }
  }
  ~Impl() { stop(); cleanup(); }
  void cleanup() {
    if (camera) camera->requestCompleted.disconnect(this);
    avcodec_free_context(&encoder); requests.clear(); mappings.clear(); allocator.reset();
    if (acquired) { camera->release(); acquired = false; }
  }
  void configure() {
    camera_config = camera->generateConfiguration({libcamera::StreamRole::VideoRecording});
    if (!camera_config || camera_config->empty()) throw std::runtime_error("camera has no video configuration");
    libcamera::SensorConfiguration sensor; sensor.bitDepth = 10; sensor.outputSize = {2064, 1552};
    camera_config->sensorConfig = sensor;
    using O = libcamera::Orientation;
    const O orientation = settings.flip_y ? (settings.flip_x ? O::Rotate180 : O::Rotate180Mirror)
                                         : (settings.flip_x ? O::Rotate0Mirror : O::Rotate0);
    camera_config->orientation = orientation;
    auto &output = camera_config->at(0);
    output.pixelFormat = libcamera::formats::YUV420;
    output.size = {config.width, config.height}; output.bufferCount = 8;
    output.colorSpace = libcamera::ColorSpace::Rec709;
    auto status = camera_config->validate();
    if (status == libcamera::CameraConfiguration::Invalid || output.size != libcamera::Size(config.width, config.height) ||
        output.pixelFormat != libcamera::formats::YUV420 || camera_config->orientation != orientation ||
        !camera_config->sensorConfig || camera_config->sensorConfig->outputSize != libcamera::Size(2064, 1552) || camera_config->sensorConfig->bitDepth != 10)
      throw std::runtime_error("camera cannot provide exact full RAW10 mode, dimensions, YUV420 format or requested orientation");
    if (!output.colorSpace || output.colorSpace != libcamera::ColorSpace::Rec709)
      throw std::runtime_error("camera cannot provide requested Rec709 colour space");
    camera_check(camera->configure(camera_config.get()), "configure camera");
    frame_rate_control = configure_framos_rate(*camera, *camera_config, config.fps);
    auto maximum = camera->properties().get(libcamera::properties::ScalerCropMaximum);
    auto array = camera->properties().get(libcamera::properties::PixelArraySize);
    if (!maximum || !array || maximum->width < 2064 || maximum->height < 1552)
      throw std::runtime_error("full sensor crop properties unavailable or unexpectedly narrow");
    auto selected = centered_crop({maximum->x, maximum->y, int(maximum->width), int(maximum->height)}, config.width, config.height);
    full_sensor_crop = *maximum;
    crop = {selected.x, selected.y, unsigned(selected.width), unsigned(selected.height)};
    if (!camera->controls().count(&libcamera::controls::ScalerCrop) || !camera->controls().count(&libcamera::controls::FrameDurationLimits))
      throw std::runtime_error("camera does not expose crop and fixed frame-duration controls");
    stream = output.stream();
    allocator = std::make_unique<libcamera::FrameBufferAllocator>(camera);
    camera_check(allocator->allocate(stream), "allocate camera buffers");
    if (allocator->buffers(stream).size() < 6) throw std::runtime_error("insufficient capture buffers");
    for (const auto &buffer : allocator->buffers(stream)) {
      mappings[buffer.get()] = std::make_unique<Mapping>(*buffer);
      auto request = camera->createRequest();
      if (!request) throw std::runtime_error("cannot allocate request");
      camera_check(request->addBuffer(stream, buffer.get()), "attach camera buffer");
      requests.push_back(std::move(request));
    }
    camera->requestCompleted.connect(this, &Impl::completed);
  }
  void configure_encoder() {
    const auto *codec = avcodec_find_encoder_by_name("libx264");
    if (!codec) throw std::runtime_error("FFmpeg has no libx264 encoder");
    encoder = avcodec_alloc_context3(codec); if (!encoder) throw std::bad_alloc();
    encoder->width = config.width; encoder->height = config.height; encoder->pix_fmt = AV_PIX_FMT_YUV420P;
    encoder->time_base = us_timebase; encoder->framerate = {int(config.fps), 1};
    encoder->bit_rate = config.bitrate; encoder->rc_max_rate = config.bitrate; encoder->rc_min_rate = config.bitrate;
    encoder->rc_buffer_size = config.vbv_bits; encoder->gop_size = config.fps; encoder->max_b_frames = 0;
    encoder->thread_count = 2; encoder->thread_type = FF_THREAD_SLICE;
    encoder->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
    encoder->color_range = AVCOL_RANGE_MPEG; encoder->colorspace = AVCOL_SPC_BT709;
    encoder->color_primaries = AVCOL_PRI_BT709; encoder->color_trc = AVCOL_TRC_BT709;
    AVDictionary *options = nullptr;
    av_dict_set(&options, "preset", config.preset.c_str(), 0); av_dict_set(&options, "tune", "zerolatency", 0);
    const std::string x264 = "nal-hrd=cbr:repeat-headers=1:annexb=1:scenecut=0:keyint=" + std::to_string(config.fps) + ":min-keyint=" + std::to_string(config.fps);
    av_dict_set(&options, "x264-params", x264.c_str(), 0);
    int result = avcodec_open2(encoder, codec, &options); av_dict_free(&options);
    av_check(result, "open x264 encoder");
  }
  void start(Outputs &sinks) {
    outputs = &sinks;
    worker = std::thread([this] { work(); });
    libcamera::ControlList controls(camera->controls());
    const std::array<std::int64_t, 2> duration{1000000 / config.fps, 1000000 / config.fps};
    controls.set(libcamera::controls::FrameDurationLimits, libcamera::Span<const std::int64_t, 2>(duration));
    controls.set(libcamera::controls::ScalerCrop, crop);
    camera_check(camera->start(&controls), "start camera"); active = true;
    for (auto &request : requests) camera_check(camera->queueRequest(request.get()), "queue initial request");
    logs.event("camera", "started", settings.id, settings.device);
  }
  void recycle(libcamera::Request *request) noexcept {
    std::lock_guard lock(state_mutex);
    if (!active) return;
    request->reuse(libcamera::Request::ReuseBuffers);
    int result = camera->queueRequest(request);
    if (result < 0) { failure = true; logs.event("camera", "requeue_failed", settings.id, std::to_string(result)); }
  }
  void drop(Json metadata, const char *reason) {
    ++dropped; metadata["status"] = "dropped"; metadata["drop_reason"] = reason;
    logs.frame(metadata);
    if (outputs) { metadata["type"] = "frame"; outputs->metadata(metadata); }
  }
  void completed(libcamera::Request *request) {
    if (request->status() == libcamera::Request::RequestCancelled) return;
    auto *buffer = request->findBuffer(stream);
    if (!buffer) { failure = true; return; }
    try {
      ++captured; last_frame_ns = boot_ns();
      const auto &m = request->metadata();
      auto timestamp = m.get(libcamera::controls::SensorTimestamp);
      Json metadata{{"schema_version", 1}, {"type", "frame"}, {"camera_id", settings.id},
                    {"sequence", buffer->metadata().sequence}, {"request_sequence", request->sequence()},
                    {"sensor_timestamp_ns", timestamp ? Json(*timestamp) : Json(nullptr)}, {"pts_us", nullptr},
                    {"exposure_us", nullptr}, {"analogue_gain", nullptr}, {"scaler_crop", nullptr}, {"drop_reason", nullptr}};
      if (auto exposure = m.get(libcamera::controls::ExposureTime)) metadata["exposure_us"] = *exposure;
      if (auto gain = m.get(libcamera::controls::AnalogueGain)) metadata["analogue_gain"] = *gain;
      ColourMetadata colour;
      if (auto gains = m.get(libcamera::controls::ColourGains)) colour.gains = {{(*gains)[0], (*gains)[1]}};
      if (auto temperature = m.get(libcamera::controls::ColourTemperature)) colour.temperature_k = *temperature;
      if (auto matrix = m.get(libcamera::controls::ColourCorrectionMatrix)) {
        std::array<float, 9> values;
        std::copy(matrix->begin(), matrix->end(), values.begin());
        colour.correction_matrix = values;
      }
      if (auto enabled = m.get(libcamera::controls::AwbEnable)) colour.awb_enabled = *enabled;
      metadata.update(colour_metadata(colour));
      metadata["sensor_crop"] = nullptr;
      if (auto actual = m.get(libcamera::controls::ScalerCrop)) {
        metadata["scaler_crop"] = rectangle(*actual);
        metadata["sensor_crop"] = sensor_crop(*actual);
      }
      const auto sequence = buffer->metadata().sequence;
      if (last_sequence) {
        const std::uint32_t gap = sequence - *last_sequence;
        if (gap > 1 && gap < 10000) {
          for (std::uint32_t i = 1; i < gap; ++i) {
            Json missing{{"schema_version", 1}, {"type", "frame"}, {"camera_id", settings.id},
                         {"sequence", std::uint32_t(*last_sequence+i)}, {"sensor_timestamp_ns", nullptr},
                         {"pts_us", nullptr}, {"exposure_us", nullptr}, {"analogue_gain", nullptr}};
            missing.update(colour_metadata());
            drop(std::move(missing), "camera_sequence_gap");
          }
        } else if (gap >= 10000) logs.event("camera", "sequence_reset_or_large_gap", settings.id, std::to_string(gap));
      }
      last_sequence = sequence;
      if (!timestamp) { failure = true; drop(std::move(metadata), "missing_sensor_timestamp"); recycle(request); return; }
      if (*timestamp < origin) { failure = true; drop(std::move(metadata), "timestamp_before_origin_clock_mismatch"); recycle(request); return; }
      const auto age = last_frame_ns.load() - *timestamp;
      if (age < -100000000 || age > 5000000000LL) {
        failure = true; drop(std::move(metadata), "implausible_sensor_clock_domain"); recycle(request); return;
      }
      metadata["pts_us"] = relative_pts_us(*timestamp, origin);
      if (buffer->metadata().status != libcamera::FrameMetadata::FrameSuccess) { drop(std::move(metadata), "camera_frame_error"); recycle(request); return; }
      if (failure) { drop(std::move(metadata), "camera_or_encoder_unavailable"); recycle(request); return; }
      Capture item{request, buffer, std::move(metadata)};
      if (!queue.try_push(std::move(item))) { drop(std::move(item.metadata), "capture_queue_full"); recycle(request); }
    } catch (const std::exception &e) { failure = true; logs.event("camera", "callback_failed", settings.id, e.what()); recycle(request); }
  }
  void receive_packets() {
    Packet packet(av_packet_alloc()); if (!packet) throw std::bad_alloc();
    while (true) {
      int result = avcodec_receive_packet(encoder, packet.get());
      if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) break;
      av_check(result, "receive encoded packet");
      auto it = pending_metadata.find(packet->pts);
      if (it == pending_metadata.end()) throw std::runtime_error("encoded PTS has no captured frame metadata");
      auto metadata = std::move(it->second); pending_metadata.erase(it);
      metadata["status"] = "encoded"; metadata["keyframe"] = bool(packet->flags & AV_PKT_FLAG_KEY);
      metadata["encoded_bytes"] = packet->size;
      if (packet->dts != packet->pts) throw std::runtime_error("unexpected reordered H264 packet");
      outputs->packet(settings.id, packet.get(), metadata); logs.frame(std::move(metadata)); ++encoded;
      av_packet_unref(packet.get());
    }
  }
  void work() {
    Json in_flight_metadata;
    try {
      while (auto item = queue.pop()) {
        in_flight_metadata = item->metadata;
        auto &mapping = *mappings.at(item->buffer);
        try { mapping.begin(); }
        catch (...) { recycle(item->request); throw; }
        auto lease = std::shared_ptr<Lease>(new Lease{mapping, [this, request = item->request] { recycle(request); }});
        const auto pts = item->metadata.at("pts_us").get<std::int64_t>();
        if (pts <= last_pts) { drop(std::move(item->metadata), "nonmonotonic_sensor_timestamp"); in_flight_metadata = nullptr; continue; }
        last_pts = pts;
        if (!config.encode) {
          item->metadata["status"] = "captured"; logs.frame(std::move(item->metadata)); in_flight_metadata = nullptr; continue;
        }
        auto frame = av_frame(mapping, config.width, config.height, camera_config->at(0).stride, lease, pts);
        pending_metadata.emplace(pts, std::move(item->metadata));
        in_flight_metadata = nullptr;
        int result = avcodec_send_frame(encoder, frame.get());
        if (result == AVERROR(EAGAIN)) { receive_packets(); result = avcodec_send_frame(encoder, frame.get()); }
        av_check(result, "send frame to encoder"); receive_packets();
      }
      if (encoder) { av_check(avcodec_send_frame(encoder, nullptr), "flush encoder"); receive_packets(); }
    } catch (const std::exception &e) {
      failure = true; logs.event("encoder", "failed", settings.id, e.what()); queue.close();
      if (!in_flight_metadata.is_null()) drop(std::move(in_flight_metadata), "frame_processing_failed");
      while (auto item = queue.pop()) { drop(std::move(item->metadata), "encoder_failed"); recycle(item->request); }
    }
    for (auto &[_, metadata] : pending_metadata) drop(std::move(metadata), "encoder_no_output");
    pending_metadata.clear();
    // Release all remaining x264 frame references while the camera/mappings live.
    avcodec_free_context(&encoder);
  }
  void stop() {
    bool was_active;
    { std::lock_guard lock(state_mutex); was_active = active.exchange(false); }
    if (was_active) {
      int result = camera->stop();
      if (result < 0) { failure = true; logs.event("camera", "stop_failed", settings.id, std::to_string(result)); }
      else logs.event("camera", "stopped", settings.id);
    }
    queue.close(); if (worker.joinable()) worker.join();
  }
  Json description() const {
    const auto &s = camera_config->at(0);
    Json result{{"id", settings.id}, {"device", settings.device}, {"width", s.size.width}, {"height", s.size.height},
                {"stride", s.stride}, {"frame_size", s.frameSize}, {"buffer_count", allocator->buffers(stream).size()},
                {"pixel_format", s.pixelFormat.toString()}, {"colour_space", "Rec709"}, {"sensor_size", {2064,1552}},
                {"sensor_bit_depth", 10}, {"orientation", int(camera_config->orientation)},
                {"flip_x", settings.flip_x}, {"flip_y", settings.flip_y}, {"requested_scaler_crop", rectangle(crop)},
                {"requested_sensor_crop", sensor_crop(crop)},
                {"frame_rate_control", frame_rate_control},
                {"colour_control_provenance", colour_control_provenance()},
                {"sensor_coordinate_transform", {{"source", "configured full-mode ScalerCropMaximum"},
                  {"pixel_array_origin", {full_sensor_crop.x, full_sensor_crop.y}},
                  {"sensor_pixels_per_array_pixel", {2064.0/full_sensor_crop.width, 1552.0/full_sensor_crop.height}},
                  {"canonical_size", {2064,1552}}}},
                {"timestamp_source", "libcamera SensorTimestamp; PiSP forwards CFE buffer timestamp"},
                {"exposure_alignment_verified", false}, {"synchronization", "free_running"}};
    if (auto p = camera->properties().get(libcamera::properties::PixelArraySize)) result["pixel_array_size"] = {p->width,p->height};
    if (auto p = camera->properties().get(libcamera::properties::ScalerCropMaximum)) result["scaler_crop_maximum"] = rectangle(*p);
    if (auto p = camera->properties().get(libcamera::properties::PixelArrayActiveAreas)) {
      result["pixel_array_active_areas"] = Json::array();
      for (const auto &r : *p) result["pixel_array_active_areas"].push_back(rectangle(r));
    }
    return result;
  }
  Json sensor_crop(const libcamera::Rectangle &r) const {
    const double sx = 2064.0/full_sensor_crop.width, sy = 1552.0/full_sensor_crop.height;
    return Json::array({(r.x-full_sensor_crop.x)*sx, (r.y-full_sensor_crop.y)*sy, r.width*sx, r.height*sy});
  }
};
CameraSession::CameraSession(libcamera::CameraManager &m, const Config &c, const CameraConfig &s, Logs &l, std::int64_t origin)
    : impl_(std::make_unique<Impl>(m,c,s,l,origin)) {}
CameraSession::~CameraSession() = default;
void CameraSession::start(Outputs &s) { impl_->start(s); }
void CameraSession::stop() { impl_->stop(); }
StreamInfo CameraSession::stream_info() const { return {impl_->settings.id, impl_->encoder}; }
Json CameraSession::description() const { return impl_->description(); }
bool CameraSession::failed() const { return impl_->failure; }
Json CameraSession::stats() const {
  return {{"camera_id", impl_->settings.id}, {"active", impl_->active.load()}, {"failed", impl_->failure.load()},
          {"captured_frames", impl_->captured.load()}, {"encoded_frames", impl_->encoded.load()}, {"dropped_frames", impl_->dropped.load()},
          {"queue", impl_->queue.size()}, {"queue_high_water", impl_->queue.high_water()}, {"last_frame_ns", impl_->last_frame_ns.load()}};
}
Json enumerate_cameras(libcamera::CameraManager &manager) {
  Json cameras = Json::array();
  for (const auto &camera : manager.cameras()) {
    Json entry{{"id", camera->id()}, {"model", nullptr}, {"pixel_array_size", nullptr}};
    if (auto p = camera->properties().get(libcamera::properties::Model)) entry["model"] = *p;
    if (auto p = camera->properties().get(libcamera::properties::PixelArraySize)) entry["pixel_array_size"] = {p->width,p->height};
    cameras.push_back(std::move(entry));
  }
  return {{"schema_version", 1}, {"cameras", cameras}};
}
}  // namespace pv
