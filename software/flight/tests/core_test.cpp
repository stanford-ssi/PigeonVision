#include "pv/core.hpp"
#include <cassert>
#include <future>
#include <memory>
#include <thread>
#include <vector>

int main() {
  // A shared origin preserves the measured inter-camera offset and real gaps.
  assert(pv::relative_pts_us(1'000'001'000, 1'000'000'000) == 1);
  assert(pv::relative_pts_us(1'008'334'000, 1'000'000'000) == 8334);
  bool rejected = false;
  try { (void)pv::relative_pts_us(99, 100); } catch (const std::runtime_error &) { rejected = true; }
  assert(rejected);
  auto crop = pv::centered_crop({0, 0, 2064, 1552}, 1552, 1552);
  assert(crop.x == 256 && crop.y == 0 && crop.width == 1552 && crop.height == 1552);
  crop = pv::centered_crop({8, 12, 2064, 1552}, 1552, 1552);
  assert(crop.x == 264 && crop.y == 12);
  assert(!pv::segment_due(70'000'000, 1'000'000, 60'000'000, false));
  assert(pv::segment_due(70'000'000, 1'000'000, 60'000'000, true));
  assert(!pv::segment_due(500'000, 1'000'000, 60'000'000, true));
  pv::BoundedQueue<std::unique_ptr<int>> q(1);
  auto one = std::make_unique<int>(1), two = std::make_unique<int>(2);
  assert(q.try_push(std::move(one)));
  assert(!q.try_push(std::move(two)) && two && *two == 2);
  assert(**q.pop() == 1);
  q.close();
  assert(!q.try_push(std::move(two)) && two);
  assert(!q.pop());
  pv::BoundedQueue<int> drain(2);
  assert(drain.try_push(1) && drain.try_push(2));
  drain.close();
  assert(*drain.pop() == 1 && *drain.pop() == 2 && !drain.pop());
  pv::BoundedQueue<int> wake(1);
  auto waiter = std::async(std::launch::async, [&] { return wake.pop(); });
  wake.close();
  assert(waiter.wait_for(std::chrono::seconds(1)) == std::future_status::ready);
  assert(!waiter.get());
  pv::BoundedQueue<int> threaded(8);
  std::vector<int> received;
  std::thread consumer([&] { while (auto n = threaded.pop()) received.push_back(*n); });
  for (int i = 0; i < 10000; ++i) {
    int value = i;
    while (!threaded.try_push(std::move(value))) std::this_thread::yield();
  }
  threaded.close(); consumer.join();
  assert(received.size() == 10000 && threaded.high_water() <= 8);
  for (int i = 0; i < 10000; ++i) assert(received[i] == i);
}
