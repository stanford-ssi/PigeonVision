"""Bounded, prefetched GNU Radio RX source for the identified E200 Pluto driver.

The caller configures RX separately. This module never writes PHY rate, LO, or
TX controls. It requires the shutdown guard's state before starting. A full host
queue or observed ADC overflow aborts explicitly. Historical E200 kernels clear
DMA status per submission, so no observed overflow does not prove continuity.
GNU Radio, numpy and the libiio 0.26 Python binding are optional dependencies.
"""
from collections import deque
import logging
import queue
import threading
import time
from typing import Callable

import numpy as np
from gnuradio import gr

from .profile import BUFFER_SAMPLES, KERNEL_BUFFERS, QUEUE_BLOCKS, SAMPLE_RATE

_LOG = logging.getLogger(__name__)
_DMA_STATUS = 0x80000088  # ADC FPGA register access, cf_axi_adc_core.c
_DMA_OVERFLOW = 4
# MicroPhase/linux 9c04de11ae53 ad9361.c + ad9361_regs.h: the driver halves
# integer-Hz requests and doubles clock readback. Its
# fractional PLL rounding contributes <=80,008,000/(2*8,388,593*2) Hz at RF;
# input/readback truncation adds <=1 and <2 Hz. Round that <5.385 Hz bound up.
_RX_LO_READBACK_TOLERANCE_HZ = 6


def convert_iq(raw: bytes | bytearray) -> np.ndarray:
    """Convert interleaved le:S12/16>>0 I/Q to independent normalized complex64."""
    if not raw or len(raw) % 4:
        raise RuntimeError('RX block must contain complete 16-bit I/Q pairs')
    words = np.frombuffer(raw, dtype='<i2')
    signed = np.right_shift(np.left_shift(words, 4), 4)
    values = signed.astype(np.float32)
    values *= np.float32(1 / 2048)
    return values.view(np.complex64)


class PrefetchSource(gr.sync_block):
    """Single-use source. Reader returns owned IQ bytes, or None at finite EOF."""

    def __init__(self, reader: Callable, cancel: Callable = lambda: None,
                 close: Callable = lambda: None, queue_blocks: int = QUEUE_BLOCKS,
                 sample_rate: int = SAMPLE_RATE):
        if not 1 <= queue_blocks <= 64 or sample_rate <= 0:
            raise ValueError('Require 1–64 queue blocks and positive sample rate')
        super().__init__('e200_rx_prefetch', in_sig=[], out_sig=[np.complex64])
        self.reader, self.cancel, self.close = reader, cancel, close
        self.blocks = queue.Queue(maxsize=queue_blocks)
        self.halt, self.consume_ready = threading.Event(), threading.Event()
        self.worker = None
        self.current, self.offset = None, 0
        self.error = None
        self.state = 'created'
        self.started_at = self.ended_at = None
        self.windows = deque(maxlen=120)
        self.stats = {'blocks_received': 0, 'samples_received': 0,
                      'samples_delivered': 0, 'queue_peak_blocks': 0,
                      'sample_rate_hz': sample_rate, 'queue_capacity_blocks': queue_blocks}

    def start(self) -> bool:
        if self.worker is not None or self.halt.is_set():
            raise RuntimeError('Source is single-use; construct a new source to restart')
        self.state = 'waiting_for_consumer'
        self.worker = threading.Thread(target=self._produce, daemon=True)
        self.worker.start()
        return True

    def _fail(self, message: str) -> None:
        self.error = message
        self.state = 'failed'
        _LOG.error('RX_PREFETCH_FAILED: %s', message)

    def _produce(self) -> None:
        try:
            while not self.consume_ready.wait(.05):
                if self.halt.is_set():
                    return
            if self.halt.is_set():
                return
            self.started_at = window_at = time.monotonic()
            window_received = window_delivered = 0
            self.state = 'running'
            while not self.halt.is_set():
                raw = self.reader()
                if raw is None:
                    self.state = 'eof'
                    return
                values = convert_iq(raw)
                self.blocks.put_nowait(values)
                self.stats['blocks_received'] += 1
                self.stats['samples_received'] += len(values)
                self.stats['queue_peak_blocks'] = max(self.stats['queue_peak_blocks'], self.blocks.qsize())
                now = time.monotonic()
                if now - window_at >= 1:
                    received, delivered = self.stats['samples_received'], self.stats['samples_delivered']
                    self.windows.append({'elapsed_seconds': now - self.started_at,
                                         'duration_seconds': now - window_at,
                                         'received_samples_per_second': (received-window_received)/(now-window_at),
                                         'delivered_samples_per_second': (delivered-window_delivered)/(now-window_at)})
                    window_at, window_received, window_delivered = now, received, delivered
        except Exception as exc:
            if not self.halt.is_set():
                self._fail('Host queue full' if isinstance(exc, queue.Full) else str(exc))
        finally:
            # Only the producer destroys its native buffer, after refill ends.
            try:
                self.close()
            except Exception as exc:
                self._fail(f'RX cleanup failed: {exc}')
            self.ended_at = time.monotonic()
            self.halt.set()

    def work(self, input_items, output_items) -> int:
        self.consume_ready.set()
        if self.error:
            return -1
        while self.current is None:
            if self.error:
                return -1
            try:
                self.current = self.blocks.get(timeout=.05)
                self.offset = 0
            except queue.Empty:
                if self.halt.is_set():
                    return -1
                # Zero would park this source: queue.put does not wake GNU Radio.
        count = min(len(output_items[0]), len(self.current) - self.offset)
        output_items[0][:count] = self.current[self.offset:self.offset + count]
        self.offset += count
        self.stats['samples_delivered'] += count
        if self.offset == len(self.current):
            self.current = None
        return count

    def stop(self) -> bool:
        self.halt.set()
        self.consume_ready.set()
        self.cancel()
        if self.worker is not None:
            self.worker.join(timeout=4)
            if self.worker.is_alive():
                self._fail('RX worker did not stop within 4 seconds; buffer retained')
                return False
        if self.state not in ('eof', 'failed'):
            self.state = 'stopped'
        return True

    def current_status(self) -> dict:
        """Bounded current summary for monitoring; history stays in report()."""
        end = self.ended_at or time.monotonic()
        duration = end - self.started_at if self.started_at is not None else 0
        extra = self.reader.report() if hasattr(self.reader, 'report') else {}
        extra = {key: extra[key] for key in ('kernel_buffer_blocks_requested',
                                            'overflow_status_polls', 'observed_overflows',
                                            'overflow_poll_interval_seconds') if key in extra}
        return {**self.stats, **extra, 'error': self.error[:512] if self.error else None, 'state': self.state,
                'producer_seconds_including_startup': duration,
                'received_samples_per_second': self.stats['samples_received']/duration if duration else None,
                'latest_rate_window': dict(self.windows[-1]) if self.windows else None,
                'queue_residual_blocks': self.blocks.qsize(),
                'contiguous_samples_proven': False}


    def report(self) -> dict:
        """Full benchmark evidence, including bounded recent rate history."""
        return {**self.current_status(), 'error': self.error,
                'rate_windows': list(self.windows)}


def _require_tx_shutdown(phy) -> None:
    try:
        tx_lo = phy.find_channel('altvoltage1', True)
        valid = (tx_lo is not None and tx_lo.attrs['powerdown'].value.strip() == '1'
                 and phy.debug_attrs['adi,tx-lo-powerdown-managed-enable'].value.strip() == '0'
                 and phy.attrs['calib_mode'].value.split()[:1] == ['manual_tx_quad'])
    except (KeyError, AttributeError) as exc:
        raise RuntimeError('Missing critical TX shutdown readback') from exc
    if not valid:
        raise RuntimeError('Run RX shutdown guard and configure RX before this source')


def _require_rx_frequency(phy, frequency: int | None) -> None:
    if frequency is None:
        return
    try:
        lo = phy.find_channel('altvoltage0', True)
        actual = int(lo.attrs['frequency'].value)
    except (KeyError, AttributeError, ValueError) as exc:
        raise RuntimeError('Missing/invalid RX LO frequency readback') from exc
    if abs(actual - frequency) > _RX_LO_READBACK_TOLERANCE_HZ:
        raise RuntimeError(f'RX LO frequency readback {actual} Hz differs from requested '
                           f'{frequency} Hz by more than {_RX_LO_READBACK_TOLERANCE_HZ} Hz')


class _IIOReader:
    """Own native context/buffer. Producer alone creates and destroys the buffer."""

    def __init__(self, iio, ctx, phy, adc, buffer_samples: int, frequency: int | None = None):
        self.iio, self.ctx, self.phy, self.adc = iio, ctx, phy, adc
        self.buffer_samples = buffer_samples
        self.frequency = frequency
        self.buffer = None
        self.cancelled = threading.Event()
        self.poll_at = 0
        self.status_polls = self.overflows = 0

    def __call__(self):
        if self.cancelled.is_set():
            return None
        if self.buffer is None:
            _require_tx_shutdown(self.phy)
            _require_rx_frequency(self.phy, self.frequency)
            self.buffer = self.iio.Buffer(self.adc, self.buffer_samples, False)
            if self.cancelled.is_set():
                self.buffer.cancel()
                return None
            self.adc.reg_write(_DMA_STATUS, _DMA_OVERFLOW)
            self.poll_at = time.monotonic() + .25
        count = self.iio._buffer_refill(self.buffer._buffer)
        raw = self.buffer.read()
        if count <= 0 or count % 4 or count != len(raw):
            raise RuntimeError('Partial/invalid IIO block; refusing assumed layout')
        if time.monotonic() >= self.poll_at:
            self.status_polls += 1
            if self.adc.reg_read(_DMA_STATUS) & _DMA_OVERFLOW:
                self.overflows += 1
                raise RuntimeError('Observed ADC DMA overflow: sample continuity lost')
            self.poll_at = time.monotonic() + .25
        return raw

    def cancel(self) -> None:
        self.cancelled.set()
        buffer = self.buffer  # retain a strong reference across native cancel
        if buffer is not None:
            buffer.cancel()

    def close(self) -> None:
        self.buffer = None

    def report(self) -> dict:
        return {'kernel_buffer_blocks_requested': KERNEL_BUFFERS,
                'overflow_status_polls': self.status_polls,
                'observed_overflows': self.overflows,
                'overflow_poll_interval_seconds': .25}


def open_iio(uri: str, sample_rate: int = SAMPLE_RATE,
             buffer_samples: int = BUFFER_SAMPLES, queue_blocks: int = QUEUE_BLOCKS,
             frequency: int | None = None) -> PrefetchSource:
    """Open ADC control only; actual stream starts on first GNU work invocation."""
    if (not 1 <= queue_blocks <= 64 or buffer_samples <= 0 or sample_rate <= 0
            or (frequency is not None and frequency <= 0)):
        raise ValueError('Invalid buffer, queue or sample-rate setting')
    import iio
    if not hasattr(iio, '_buffer_refill'):
        raise RuntimeError('Requires pinned libiio 0.26 Python _buffer_refill interface')
    ctx = iio.Context(uri)
    ctx.set_timeout(3000)
    phy = ctx.find_device('ad9361-phy')
    if phy is None:
        raise RuntimeError('Missing AD936x PHY')
    _require_tx_shutdown(phy)
    # gr-iio's PHY setters log failed writes without raising. Refuse an old LO
    # setting before any capture buffer can be created.
    _require_rx_frequency(phy, frequency)
    adc = ctx.find_device('cf-ad9361-lpc')
    if adc is None:
        raise RuntimeError('Missing ADC')
    for channel in adc.channels:
        if not channel.output:
            channel.enabled = channel.id in ('voltage0', 'voltage1')
    for index, name in enumerate(('voltage0', 'voltage1')):
        channel = adc.find_channel(name, False)
        if channel is None:
            raise RuntimeError(f'Missing ADC {name}')
        fmt = channel.data_format
        if (channel.index != index or fmt.is_be or not fmt.is_signed
                or fmt.bits != 12 or fmt.length != 16 or fmt.shift != 0 or fmt.repeat != 1):
            raise RuntimeError('Requires index0 I/index1 Q, le:S12/16>>0, repeat1')
        if int(channel.attrs['sampling_frequency'].value) != sample_rate:
            raise RuntimeError('ADC sample rate differs from requested source rate')
    if adc.sample_size != 4:
        raise RuntimeError('Expected one 4-byte I/Q pair')
    adc.set_kernel_buffers_count(KERNEL_BUFFERS)
    reader = _IIOReader(iio, ctx, phy, adc, buffer_samples, frequency)
    return PrefetchSource(reader, reader.cancel, reader.close, queue_blocks, sample_rate)
