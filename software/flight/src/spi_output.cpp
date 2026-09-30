#include "pv/spi_output.hpp"
#include "pv/spi_protocol.hpp"
#include <chrono>
#include <fstream>
#include <thread>
#ifdef __linux__
#include <linux/gpio.h>
#include <linux/spi/spidev.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>
#endif

namespace pv {
#ifdef __linux__
namespace {
struct Fd {
  int value=-1;
  ~Fd() { if(value>=0) ::close(value); }
};
void checked(int status,const char *operation) {
  if(status<0) throw std::runtime_error(std::string(operation)+": "+std::strerror(errno));
}
struct LinuxSpi : SpiPort {
  Fd spi,line;
  explicit LinuxSpi(const SpiConfig &c) {
    std::size_t buffer=0;
    std::ifstream("/sys/module/spidev/parameters/bufsiz")>>buffer;
    if(buffer<spi_message_bytes) throw std::runtime_error("spidev buffer smaller than 1332 bytes");
    spi.value=::open(c.device.c_str(),O_WRONLY|O_CLOEXEC); checked(spi.value,"open SPI");
    checked(flock(spi.value,LOCK_EX|LOCK_NB),"lock SPI");
    std::uint32_t mode=SPI_MODE_0, hz=c.hz; std::uint8_t bits=8;
    checked(ioctl(spi.value,SPI_IOC_WR_MODE32,&mode),"set SPI mode");
    checked(ioctl(spi.value,SPI_IOC_WR_BITS_PER_WORD,&bits),"set SPI bits");
    checked(ioctl(spi.value,SPI_IOC_WR_MAX_SPEED_HZ,&hz),"set SPI rate");
    checked(ioctl(spi.value,SPI_IOC_RD_MODE32,&mode),"read SPI mode");
    checked(ioctl(spi.value,SPI_IOC_RD_BITS_PER_WORD,&bits),"read SPI bits");
    checked(ioctl(spi.value,SPI_IOC_RD_MAX_SPEED_HZ,&hz),"read SPI rate");
    if(mode!=SPI_MODE_0 || bits!=8 || hz!=c.hz) throw std::runtime_error("SPI configuration mismatch");
    std::string chip=c.gpiochip;
    if(chip.empty()) {
      for(const auto &entry:std::filesystem::directory_iterator("/dev")) {
        if(!entry.path().filename().string().starts_with("gpiochip")) continue;
        Fd probe; probe.value=::open(entry.path().c_str(),O_RDONLY|O_CLOEXEC);
        gpiochip_info info{};
        if(probe.value>=0 && ioctl(probe.value,GPIO_GET_CHIPINFO_IOCTL,&info)==0 && std::string(info.label).find("rp1")!=std::string::npos) {
          auto resolved=std::filesystem::canonical(entry.path()).string();
          if(!chip.empty() && chip!=resolved) throw std::runtime_error("multiple RP1 GPIO chips; specify spi.gpiochip");
          chip=resolved;
        }
      }
      if(chip.empty()) throw std::runtime_error("RP1 GPIO chip not found");
    }
    Fd gpio; gpio.value=::open(chip.c_str(),O_RDONLY|O_CLOEXEC); checked(gpio.value,"open GPIO");
    gpiochip_info info{};
    checked(ioctl(gpio.value,GPIO_GET_CHIPINFO_IOCTL,&info),"read GPIO chip");
    if(std::string(info.label).find("rp1")==std::string::npos || c.ready_line>=info.lines)
      throw std::runtime_error("READY must use an RP1 GPIO line");
    gpio_v2_line_info pin{}; pin.offset=c.ready_line;
    checked(ioctl(gpio.value,GPIO_V2_GET_LINEINFO_IOCTL,&pin),"read READY mapping");
    if(std::string(pin.name)!="GPIO"+std::to_string(c.ready_line))
      throw std::runtime_error("READY offset does not match RP1 GPIO number");
    gpio_v2_line_request request{};
    request.offsets[0]=c.ready_line; request.num_lines=1;
    request.config.flags=GPIO_V2_LINE_FLAG_INPUT;
    std::strcpy(request.consumer,"pv-capture-spi");
    checked(ioctl(gpio.value,GPIO_V2_GET_LINE_IOCTL,&request),"request READY input");
    line.value=request.fd;
  }
  std::int64_t now_us() override {
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
  }
  void sleep_until_us(std::int64_t us) override {
    std::this_thread::sleep_until(std::chrono::steady_clock::time_point(std::chrono::microseconds(us)));
  }
  bool ready() override {
    gpio_v2_line_values values{}; values.mask=1;
    checked(ioctl(line.value,GPIO_V2_LINE_GET_VALUES_IOCTL,&values),"read READY");
    return values.bits&1;
  }
  std::ptrdiff_t write(std::span<const std::uint8_t> bytes) override {
    auto n=::write(spi.value,bytes.data(),bytes.size());
    checked(static_cast<int>(n),"write SPI"); return n;
  }
};
}
struct SpiOutput::Impl {
  LinuxSpi port;
  SpiSender sender;
  explicit Impl(const SpiConfig &c):port(c),sender(port) {}
};
SpiOutput::SpiOutput(const SpiConfig &c):impl_(std::make_unique<Impl>(c)) {}
SpiOutput::~SpiOutput()=default;
void SpiOutput::send(std::span<const std::uint8_t> bytes) { impl_->sender.send(bytes); }
Json SpiOutput::stats() const {
  auto s=impl_->sender.stats();
  return {{"messages",s.messages},{"attempted_messages",s.attempted_messages},{"ts_packets",s.ts_packets},
    {"payload_bytes",s.payload_bytes},{"crc_chain",s.crc_chain},{"next_seq",s.next_seq},
    {"ready_waits",s.ready_waits},{"ready_timeouts",s.ready_timeouts},{"ready_waits_over_20ms",s.ready_waits_over_20ms},
    {"max_ready_wait_ms",s.max_ready_wait_us/1000.0},{"pending_payload_bytes",s.pending_payload_bytes},
    {"transfer_uncertain",s.transfer_uncertain}};
}
#else
struct SpiOutput::Impl {};
SpiOutput::SpiOutput(const SpiConfig &) { throw std::runtime_error("SPI requires Linux"); }
SpiOutput::~SpiOutput()=default;
void SpiOutput::send(std::span<const std::uint8_t>) { throw std::runtime_error("SPI requires Linux"); }
Json SpiOutput::stats() const { return nullptr; }
#endif
}
