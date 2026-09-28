#include "pv/dma_heap.hpp"
#include <cassert>
#include <iostream>
#include <map>
#include <vector>

namespace {
template<class Call> void rejects(Call call) {
  bool rejected = false;
  try { call(); } catch (const std::exception &) { rejected = true; }
  assert(rejected);
}
void allocation_ownership() {
  int opens = 0, allocations = 0;
  std::map<int, int> closes;
  pv::DmaHeapOps ops{
    [&](const char *path) { assert(std::string(path) == pv::cached_video_heap); ++opens; return 41; },
    [&](int heap, std::uint64_t bytes) {
      assert(heap == 41 && bytes == 5065728);
      if (++allocations == 3) { errno = ENOMEM; return -1; }
      return 41 + allocations;
    },
    [&](int fd) { ++closes[fd]; }
  };
  rejects([&] {
    pv::DmaHeap heap(ops);
    std::vector<pv::OwnedDmaFd> buffers;
    for (int i = 0; i < 8; ++i) buffers.push_back(heap.allocate(5065728));
  });
  assert(opens == 1 && allocations == 3);
  assert((closes == std::map<int, int>{{41, 1}, {42, 1}, {43, 1}}));

  // No fallback, including on a permission failure rather than missing heap.
  opens = 0;
  ops.open = [&](const char *) { ++opens; errno = EACCES; return -1; };
  rejects([&] { pv::DmaHeap heap(ops); });
  assert(opens == 1 && closes.size() == 3);

  // Transient ioctl failures are retried; transfer into libcamera ownership
  // must not also close the transferred descriptor from this wrapper.
  ops.open = [&](const char *) { return 50; };
  allocations = 0;
  ops.allocate = [&](int heap, std::uint64_t bytes) {
    assert(heap == 50 && bytes == 5065728);
    ++allocations;
    if (allocations < 3) { errno = allocations == 1 ? EINTR : EAGAIN; return -1; }
    return 51;
  };
  {
    pv::DmaHeap heap(ops);
    rejects([&] { (void)heap.allocate(0); });
    auto buffer = heap.allocate(5065728);
    assert(buffer.get() == 51 && buffer.release() == 51 && buffer.get() == -1);
  }
  assert(allocations == 3 && closes[50] == 1 && !closes.contains(51));
  allocations = 0;
  ops.allocate = [&](int, std::uint64_t) { ++allocations; errno = EAGAIN; return -1; };
  rejects([&] { pv::DmaHeap heap(ops); (void)heap.allocate(5065728); });
  assert(allocations == int(pv::dma_retry_attempts) && closes[50] == 2);
}
void synchronization_failures() {
  using Event = std::pair<int, bool>;
  std::vector<Event> calls;
  int interrupts = 0;
  pv::DmaReadSync normal({11, 12}, [&](int fd, bool start) {
    calls.emplace_back(fd, start);
    if (fd == 11 && start && interrupts++ < 2) { errno = interrupts == 1 ? EINTR : EAGAIN; return -1; }
    return 0;
  });
  assert(normal.begin() == 0 && !normal.failed());
  assert(normal.end() == 0 && !normal.failed());
  assert((calls == std::vector<Event>{{11, true}, {11, true}, {11, true}, {12, true}, {11, false}, {12, false}}));
  calls.clear();
  assert(normal.end() == 0 && calls.empty());

  pv::DmaReadSync partial({11, 12, 13}, [&](int fd, bool start) {
    calls.emplace_back(fd, start);
    if (fd == 13 && start) { errno = EIO; return -1; }
    if (fd == 11 && !start) { errno = EFAULT; return -1; }
    return 0;
  });
  assert(partial.begin() == EIO && partial.failed());
  assert((calls == std::vector<Event>{{11, true}, {12, true}, {13, true}, {11, false}, {12, false}}));
  calls.clear();
  assert(partial.begin() == EBUSY && partial.end() == 0 && partial.failed() && calls.empty());

  pv::DmaReadSync ending({11, 12}, [&](int fd, bool start) {
    calls.emplace_back(fd, start);
    if (fd == 11 && !start) { errno = EIO; return -1; }
    return 0;
  });
  assert(ending.begin() == 0);
  assert(ending.end() == EIO && ending.failed());
  assert((calls == std::vector<Event>{{11, true}, {12, true}, {11, false}, {12, false}}));

  pv::DmaReadSync throwing({11}, [](int, bool) -> int { throw std::runtime_error("injected failure"); });
  assert(throwing.begin() == EIO && throwing.failed());
  assert(throwing.end() == 0);

  unsigned end_attempts = 0;
  pv::DmaReadSync unavailable({11}, [&](int, bool start) {
    if (start) return 0;
    ++end_attempts; errno = EAGAIN; return -1;
  });
  assert(unavailable.begin() == 0);
  assert(unavailable.end() == EAGAIN && unavailable.failed());
  assert(end_attempts == pv::dma_retry_attempts);
}
void padded_layout() {
  const auto layout = pv::checked_yuv420_layout(2064, 1552, 2176, 5065728);
  assert(layout.y_bytes == 3377152 && layout.uv_bytes == 844288 && layout.total_bytes == 5065728);
  rejects([] { (void)pv::checked_yuv420_layout(2064, 1552, 2063, 5065728); });
  rejects([] { (void)pv::checked_yuv420_layout(2064, 1552, 2175, 5065728); });
  rejects([] { (void)pv::checked_yuv420_layout(2064, 1551, 2176, 5065728); });
  rejects([] { (void)pv::checked_yuv420_layout(2064, 1552, 2176, 5065727); });
  rejects([] { (void)pv::checked_yuv420_layout(0, 1552, 2176, 5065728); });
}
}  // namespace
int main() {
  allocation_ownership(); synchronization_failures(); padded_layout();
  std::cout << "PASS: DMA allocation ownership, explicit failure, sync retry/quarantine and padded layout\n";
}
