// Exercise the actual internal queue and decoder-chain gate without mux timing,
// cameras or hardware. The fake clock makes expiry deterministic.
#include "../src/outputs.cpp"
#include <cassert>
#include <iostream>

namespace {
std::int64_t now_us = 0;
std::int64_t clock_us() { return now_us; }

pv::Encoded video(const std::string &camera, int sequence, bool idr = false) {
  pv::Packet packet(av_packet_alloc());
  assert(packet);
  packet->pts = sequence;
  packet->flags = idr ? AV_PKT_FLAG_KEY : 0;
  return {camera, std::move(packet), {{"type", "frame"}, {"sequence", sequence}}};
}

struct DecoderChains {
  std::map<std::string, pv::VideoRecovery> cameras{{"A", {}}, {"B", {}}};
  bool accept(const pv::Encoded &item) { return cameras.at(item.camera).accept(item); }
  void start() {
    assert(!accept(video("A", -1)) && !accept(video("B", -1)));
    assert(accept(video("A", 0, true)) && accept(video("B", 0, true)));
  }
};

void repeated_expiry() {
  now_us = 0;
  pv::TransportQueue queue(clock_us);
  DecoderChains chains;
  chains.start();
  for (int i = 1; i <= 12; ++i) {
    assert(queue.push(video("A", i)));
    now_us += 1'000'001;
    assert(queue.push(video("B", i)));
    auto b = queue.pop();
    assert(b && b->camera == "B" && b->packet->pts == i);
    assert(chains.accept(*b)); // A's recurring loss never interrupts B's deltas.
    assert(queue.push(video("A", 100 + i)));
    auto a = queue.pop();
    assert(a && a->camera == "A" && !chains.accept(*a));
  }
  assert(queue.stats()["expired_records"] == 12);
  assert(queue.push(video("A", 200, true)));
  assert(chains.accept(*queue.pop()));
  assert(queue.push(video("A", 201)) && queue.push(video("B", 13)));
  assert(chains.accept(*queue.pop()) && chains.accept(*queue.pop()));
  queue.close();
  assert(!queue.pop());
}

void capacity_drop(const std::string &lost, const std::string &other) {
  now_us = 0;
  pv::TransportQueue queue(clock_us);
  DecoderChains chains;
  chains.start();
  for (int i = 1; i <= 240; ++i)
    assert(queue.push(video(lost, i)));
  assert(!queue.push(video(lost, 241)));
  auto first = queue.pop();
  assert(first && first->packet->pts == 1 && chains.accept(*first));
  assert(queue.push(video(other, 1)));
  assert(chains.accept(*queue.pop()));
  assert(queue.push(video(lost, 242)));
  int uninterrupted = 2;
  bool saw_other = false, rejected_gap = false;
  for (int remaining = 0; remaining < 240; ++remaining) {
    auto item = queue.pop();
    assert(item);
    if (item->camera == other) {
      assert(chains.accept(*item));
      saw_other = true;
    } else if (item->packet->pts == 242) {
      assert(!chains.accept(*item));
      rejected_gap = true;
    } else {
      assert(chains.accept(*item)); // Packets queued before the gap stay valid.
      ++uninterrupted;
    }
  }
  assert(uninterrupted == 240 && saw_other && rejected_gap);
  // The producer stamps the next accepted IDR with the post-gap generation.
  assert(queue.push(video(lost, 243, true)));
  assert(chains.accept(*queue.pop()));
  assert(queue.push(video(lost, 244)) && queue.push(video(other, 2)));
  assert(chains.accept(*queue.pop()) && chains.accept(*queue.pop()));
  queue.close();
}

void expiry_preserves_capacity_gap() {
  now_us = 0;
  pv::TransportQueue queue(clock_us);
  DecoderChains chains;
  chains.start();
  assert(queue.push(video("B", 1)) && queue.push(video("A", 1)));
  now_us = 500'000;
  assert(queue.push(video("A", 2, true)));
  for (int i = 2; i <= 238; ++i)
    assert(queue.push(video("B", i)));
  assert(!queue.push(video("A", 3))); // A gap after the still-queued IDR.
  assert(chains.accept(*queue.pop())); // Free a slot without consuming A.
  assert(queue.push(video("A", 4)));
  now_us = 1'000'001;
  auto idr = queue.pop(); // A1 expires; A2 and A4 retain distinct generations.
  assert(idr && idr->camera == "A" && idr->packet->pts == 2 && chains.accept(*idr));
  for (int i = 2; i <= 238; ++i) {
    auto b = queue.pop();
    assert(b && b->camera == "B" && b->packet->pts == i && chains.accept(*b));
  }
  auto gap = queue.pop();
  assert(gap && gap->camera == "A" && gap->packet->pts == 4 && !chains.accept(*gap));
  assert(queue.push(video("A", 5, true)) && queue.push(video("A", 6)));
  assert(chains.accept(*queue.pop()) && chains.accept(*queue.pop()));
  assert(queue.stats()["expired_records"] == 1 && queue.stats()["video_queue_drops"] == 2);
  queue.close();
}
} // namespace

int main() {
  repeated_expiry();
  capacity_drop("A", "B");
  capacity_drop("B", "A");
  expiry_preserves_capacity_gap();
  std::cout << "Transport recovery: independent camera chains, repeated expiry, capacity gaps "
               "and IDR recovery verified\n";
}
