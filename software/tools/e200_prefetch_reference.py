#!/usr/bin/env python3
"""Child process for the file-only paced prefetch/full-decoder regression.

Called by e200_reference_check.py. It overrides source construction before the
receiver is instantiated; it never constructs an IIO context or hardware block.
"""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'software/python'))


class PacedS12Reader:
    """Owned little-endian S12 chunks with an absolute sample-clock deadline."""
    def __init__(self, samples, sample_rate, chunk, gain):
        self.samples, self.sample_rate, self.chunk, self.gain = samples, sample_rate, chunk, gain
        self.index, self.started, self.clipped = 0, None, 0
        self.halt = threading.Event()
        self.digest = hashlib.sha256()

    def read(self):
        import numpy as np
        if self.index == len(self.samples) or self.halt.is_set():
            return None
        if self.started is None:
            self.started = time.monotonic()
        end = min(self.index+self.chunk, len(self.samples))
        scaled = np.rint(self.samples[self.index:end].view(np.float32)*self.gain*2048)
        self.clipped += int(np.count_nonzero((scaled < -2048) | (scaled > 2047)))
        # Clear storage's upper four bits to exercise explicit S12 extension.
        raw = np.bitwise_and(np.clip(scaled, -2048, 2047).astype(np.int16), 4095).astype('<i2').tobytes()
        if self.halt.wait(max(0, end/self.sample_rate-(time.monotonic()-self.started))):
            return None
        self.index = end
        self.digest.update(raw)
        return raw

    def cancel(self):
        self.halt.set()

    def close(self):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receiver', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--sample-rate', required=True, type=int, choices=(12000000, 16000000))
    args = parser.parse_args(argv)
    import numpy as np
    from pigeonvision.radio import profile
    from pigeonvision.radio.iio_source import PrefetchSource
    out = args.output_dir
    samples = np.memmap(out/'reference.fc32', dtype=np.complex64, mode='r')
    chunk = profile.BUFFER_SAMPLES
    peak = max(float(np.max(np.abs(samples[k:k+chunk].view(np.float32))))
               for k in range(0, len(samples), chunk))
    if not peak > 0 or not np.isfinite(peak):
        raise ValueError('Require finite nonzero reference waveform')
    gain = .8/peak

    reader = PacedS12Reader(samples, args.sample_rate, chunk, gain)
    source = PrefetchSource(reader.read, reader.cancel, reader.close,
                            queue_blocks=profile.QUEUE_BLOCKS, sample_rate=args.sample_rate)
    module = runpy.run_path(str(args.receiver))
    top = module['DVBS2RxTopBlock']

    def injected(self):
        self.iio_prefetch_source = source
        return source

    top.setup_plutosdr_source = injected
    sys.argv = ['dvbs2-rx', '--source', 'plutosdr', '--plutosdr-addr', 'FILE_ONLY',
                '--sym-rate', str(profile.SYMBOL_RATE), '--samp-rate', str(args.sample_rate),
                '--modcod', profile.MODCOD, '--frame-size', profile.FRAME_SIZE,
                '--pilots', profile.PILOTS, '--rolloff', str(profile.ROLLOFF),
                '--gold-code', str(profile.GOLD_CODE), '--multistream', profile.MULTISTREAM,
                '--sink', 'file', '--out-file', str(out/'decoded.ts')]
    tb = top(module['argument_parser']())
    started = time.monotonic()
    try:
        tb.start()
        tb.wait()
        stats = tb.get_stats()
    finally:
        # Explicit early cancellation also covers exceptional scheduler exit.
        source.stop()
        tb.stop()
        tb.wait()
    report = {'samples': len(samples), 'sample_rate_hz': args.sample_rate,
              'gain_before_s12_quantization': gain, 'component_peak_before_gain': peak,
              'component_limit_after_gain': .8, 'clipped_components': reader.clipped,
              'raw_s12_sha256': reader.digest.hexdigest(),
              'receiver_wall_seconds': time.monotonic()-started,
              'source_report': source.report(), 'receiver_stats': stats}
    (out/'prefetch.json').write_text(json.dumps(report, indent=2)+'\n')
    if source.error or reader.clipped:
        raise RuntimeError(f'Prefetch failed: {source.error}; clipped={reader.clipped}')
    if source.stats['samples_received'] != len(samples) or source.stats['samples_delivered'] != len(samples):
        raise RuntimeError('Prefetch did not deliver every fixture sample')
    if not stats['lock'] or not stats['fec']['frames'] or stats['fec']['errors']:
        raise RuntimeError('Expected locked, error-free FEC on the noiseless fixture')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
