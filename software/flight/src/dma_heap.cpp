#include "pv/dma_heap.hpp"
#include <fcntl.h>
#include <linux/dma-heap.h>
#include <sys/ioctl.h>
#include <unistd.h>

namespace pv {
const DmaHeapOps &system_dma_heap_ops() {
  static const DmaHeapOps operations{
    [](const char *path) { return ::open(path, O_RDWR | O_CLOEXEC); },
    [](int heap, std::uint64_t bytes) {
      dma_heap_allocation_data allocation{};
      allocation.len = bytes;
      allocation.fd_flags = O_RDWR | O_CLOEXEC;
      // No optional flags and no heap fallback: the selected experiment must
      // either allocate the requested cached heap or fail configuration.
      if (::ioctl(heap, DMA_HEAP_IOCTL_ALLOC, &allocation) < 0) return -1;
      return static_cast<int>(allocation.fd);
    },
    [](int fd) { ::close(fd); }
  };
  return operations;
}
}  // namespace pv
