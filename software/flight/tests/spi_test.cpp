#include "pv/spi_protocol.hpp"
#include <cassert>
#include <iostream>
#include <vector>
struct Port:pv::SpiPort {
  std::int64_t t=0, available=0,last_read=-1;
  unsigned writes=0;
  bool short_write=false,throw_write=false;
  std::vector<std::array<std::uint8_t,1332>> frames;
  std::int64_t now_us() override { return t; }
  void sleep_until_us(std::int64_t deadline) override { t=std::max(t,deadline); }
  bool ready() override { last_read=t; return t>=available; }
  std::ptrdiff_t write(std::span<const std::uint8_t> bytes) override {
    assert(last_read==t && t>=available); ++writes;
    std::array<std::uint8_t,1332> f; std::copy(bytes.begin(),bytes.end(),f.begin()); frames.push_back(f);
    if(throw_write) throw std::runtime_error("injected IO error");
    t+=533; return short_write?1331:1332;
  }
};
template<class F> void rejects(F f) { bool threw=false; try { f(); } catch(const std::exception &) { threw=true; } assert(threw); }
int main() {
  std::array<std::uint8_t,188> payload{};
  payload[0]=0x47; payload[1]=1; payload[3]=0x10;
  for(unsigned i=0;i<184;++i) payload[i+4]=i;
  auto vector=pv::spi_message(1,payload);
  assert(vector[4]==1 && vector[6]==188 && vector[7]==0);
  assert(vector[1328]==0x8d && vector[1329]==0x32 && vector[1330]==0xbe && vector[1331]==0x51);
  for(unsigned i=200;i<1328;++i) assert(vector[i]==0);
  const std::string known="123456789";
  assert(pv::spi_crc({reinterpret_cast<const std::uint8_t *>(known.data()),known.size()})==0xcbf43926);
  rejects([&] { pv::spi_message(0,{}); });
  rejects([&] { pv::spi_message(0,std::span(payload).first(187)); });
  auto bad=payload; bad[0]=0; rejects([&] { pv::spi_message(0,bad); });
  Port p; pv::SpiSender sender(p);
  std::uint32_t chain=0;
  for(unsigned i=0;i<65538;++i) {
    auto before=p.t;
    sender.send(payload);
    assert(p.t-before>=543);
    const auto &f=p.frames.back();
    assert((unsigned(f[4])|(unsigned(f[5])<<8))==(i&65535));
    chain=pv::spi_crc(std::span(f).last(4),chain);
  }
  assert(sender.stats().messages==65538 && sender.stats().next_seq==2);
  assert(sender.stats().crc_chain==chain && sender.stats().payload_bytes==65538*188u);
  Port blocked; blocked.available=3000000; pv::SpiSender timeout(blocked);
  rejects([&] { timeout.send(payload); });
  assert(blocked.writes==0 && timeout.stats().ready_timeouts==1);
  assert(timeout.stats().pending_payload_bytes==188 && !timeout.stats().transfer_uncertain);
  rejects([&] { timeout.send(payload); }); assert(blocked.writes==0);
  for(bool io_error:{false,true}) {
    Port broken; broken.short_write=!io_error; broken.throw_write=io_error; pv::SpiSender tx(broken);
    rejects([&] { tx.send(payload); }); rejects([&] { tx.send(payload); });
    assert(broken.writes==1 && tx.stats().attempted_messages==1 && tx.stats().messages==0);
    assert(tx.stats().transfer_uncertain && tx.stats().pending_payload_bytes==188);
  }
  Port delayed; delayed.available=21000; pv::SpiSender waits(delayed); waits.send(payload);
  assert(waits.stats().ready_waits==1 && waits.stats().ready_waits_over_20ms==1);
  std::cout<<"SPI vector, sequence wrap, CRC chain, CS gap, READY and no-retry checks complete\n";
}
