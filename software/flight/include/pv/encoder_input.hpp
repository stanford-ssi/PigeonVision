#pragma once
#include <new>
#include <stdexcept>
#include <string>
extern "C" {
#include <libavutil/error.h>
#include <libavutil/frame.h>
}

namespace pv {
// One reusable cached AVFrame per camera. If FFmpeg retains a reference,
// make_writable detaches its buffer before the next copy; retained pixels are
// never overwritten. No additional frame queue or history is kept here.
class CachedEncoderInput {
 public:
  CachedEncoderInput(int width, int height) : frame_(av_frame_alloc()) {
    if (!frame_) throw std::bad_alloc();
    frame_->format = AV_PIX_FMT_YUV420P; frame_->width = width; frame_->height = height;
    const int result = av_frame_get_buffer(frame_, 64);
    if (result < 0) { av_frame_free(&frame_); check(result, "allocate cached encoder input"); }
  }
  ~CachedEncoderInput() { av_frame_free(&frame_); }
  CachedEncoderInput(const CachedEncoderInput &) = delete;
  CachedEncoderInput &operator=(const CachedEncoderInput &) = delete;

  AVFrame *copy_from(const AVFrame &source) {
    if (source.format != frame_->format || source.width != frame_->width || source.height != frame_->height)
      throw std::runtime_error("cached encoder input layout differs from configured YUV420");
    for (int plane = 0; plane < 3; ++plane)
      if (!source.data[plane] || source.linesize[plane] < (plane ? source.width / 2 : source.width))
        throw std::runtime_error("cached encoder input has an invalid source plane");
    check(av_frame_make_writable(frame_), "make cached encoder input writable");
    // libavutil copies active image pixels using each frame's independent plane
    // strides. It never copies source padding or aliases the DMA allocation.
    check(av_frame_copy(frame_, &source), "copy encoder input pixels");
    check(av_frame_copy_props(frame_, &source), "copy encoder input properties");
    return frame_;
  }

 private:
  static void check(int result, const char *operation) {
    if (result >= 0) return;
    char error[AV_ERROR_MAX_STRING_SIZE]; av_strerror(result, error, sizeof(error));
    throw std::runtime_error(std::string(operation) + ": " + error);
  }
  AVFrame *frame_;
};
}  // namespace pv
