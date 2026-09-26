#include "pv/encoder_input.hpp"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>

namespace {
void released(void *opaque, std::uint8_t *data) { *static_cast<bool *>(opaque) = true; std::free(data); }
void pixels(const AVFrame &frame, int bias) {
  for (int p = 0; p < 3; ++p)
    for (int y = 0; y < (p ? frame.height / 2 : frame.height); ++y)
      for (int x = 0; x < (p ? frame.width / 2 : frame.width); ++x)
        assert(frame.data[p][y * frame.linesize[p] + x] == (bias + p * 53 + y * 7 + x) % 256);
}
void fill(AVFrame &frame, int bias) {
  for (int p = 0; p < 3; ++p)
    for (int y = 0; y < (p ? frame.height / 2 : frame.height); ++y)
      for (int x = 0; x < (p ? frame.width / 2 : frame.width); ++x)
        frame.data[p][y * frame.linesize[p] + x] = (bias + p * 53 + y * 7 + x) % 256;
}
}
int main() {
  // Deliberately different source/destination strides, all three planes and
  // poison padding. No libcamera or DMA device is needed for pixel ownership.
  bool source_released = false;
  AVFrame *source = av_frame_alloc(); assert(source);
  source->width = 18; source->height = 10; source->format = AV_PIX_FMT_YUV420P;
  source->linesize[0] = 32; source->linesize[1] = source->linesize[2] = 16;
  auto *data = static_cast<std::uint8_t *>(std::malloc(480)); assert(data);
  std::memset(data, 0xee, 480);
  source->data[0] = data; source->data[1] = data + 320; source->data[2] = data + 400;
  source->buf[0] = av_buffer_create(data, 480, released, &source_released, AV_BUFFER_FLAG_READONLY);
  assert(source->buf[0]);
  source->pts = 1234567; source->duration = 33333;
  source->color_range = AVCOL_RANGE_MPEG; source->colorspace = AVCOL_SPC_BT709;
  source->color_primaries = AVCOL_PRI_BT709; source->color_trc = AVCOL_TRC_BT709;
  source->sample_aspect_ratio = {1, 1}; source->chroma_location = AVCHROMA_LOC_LEFT;
  fill(*source, 5);
  pv::CachedEncoderInput cached(18, 10);
  AVFrame *copy = cached.copy_from(*source);
  assert(copy->data[0] != source->data[0] && copy->linesize[0] != source->linesize[0]);
  pixels(*copy, 5);
  assert(copy->pts == source->pts && copy->duration == source->duration);
  assert(copy->color_range == source->color_range && copy->colorspace == source->colorspace);
  assert(copy->color_primaries == source->color_primaries && copy->color_trc == source->color_trc);
  assert(copy->chroma_location == source->chroma_location && copy->sample_aspect_ratio.num == 1);

  auto *first = copy->data[0];
  source->pts += 33333; fill(*source, 6);
  copy = cached.copy_from(*source);
  assert(copy->data[0] == first && copy->pts == source->pts);  // Reuses unreferenced cached storage.
  pixels(*copy, 6);
  AVFrame *held = av_frame_clone(copy); assert(held);  // Simulate FFmpeg retaining the previous input.
  source->pts += 33333; fill(*source, 7);
  copy = cached.copy_from(*source);
  assert(copy->data[0] != held->data[0]);
  pixels(*held, 6); pixels(*copy, 7);
  assert(held->pts != copy->pts);
  av_frame_free(&held);
  auto *reused = copy->data[0];
  for (int i = 0; i < 100; ++i) {
    fill(*source, i);
    copy = cached.copy_from(*source);
    assert(copy->data[0] == reused); pixels(*copy, i);
  }
  bool rejected = false;
  source->linesize[1] = 8;
  try { cached.copy_from(*source); } catch (const std::runtime_error &) { rejected = true; }
  assert(rejected); source->linesize[1] = 16;
  source->width = 20; rejected = false;
  try { cached.copy_from(*source); } catch (const std::runtime_error &) { rejected = true; }
  assert(rejected); source->width = 18;
  av_frame_free(&source);
  assert(source_released); pixels(*copy, 99);  // Copied pixels outlive the source/lease.
  std::cout << "PASS: padded YUV copy, properties, cached reuse, retained-frame isolation, early source release\n";
}
