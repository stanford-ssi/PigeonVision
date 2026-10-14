#!/usr/bin/env python3
"""Bounded file-only DVB-S2 recovery check, including paced S12 prefetch input.

Never opens an SDR. Every reserved run directory receives result.json and logs,
including receiver failures. Recovered TS must be exact, cover at least 80% of
input and contain at least 512 packets; acquisition/end losses are bounded.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / 'software/python'))
from pigeonvision.radio import profile

MIN_RECOVERED_PACKETS = 512
MIN_COVERAGE = .80
MAX_START_LOSS_PACKETS = 256
MAX_END_LOSS_PACKETS = 512


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def provenance(checkout):
    """Report tracked AND untracked changes; content hashes identify execution."""
    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    try:
        status = git('status', '--porcelain', '--untracked-files=all')
        return {'commit': git('rev-parse', 'HEAD'), 'dirty': bool(status),
                'status_porcelain': status,
                'tracked_diff_sha256': hashlib.sha256(git('diff', 'HEAD', '--binary').encode()).hexdigest()}
    except subprocess.CalledProcessError:
        return {'commit': None, 'dirty': None, 'git_unavailable': True}


def verify_packets(original, decoded):
    """Bound both edge losses and require meaningful exact contiguous recovery.

    The fixed 256/512-packet edge budgets allow roughly 9/18 normal QPSK 2/3
    frames for software acquisition/EOF. They are test allowances, not RF specs.
    """
    if not original or len(original) % 188 or not decoded or len(decoded) % 188:
        raise ValueError('Require nonempty complete input and decoded TS packets')
    offset = original.find(decoded)
    if offset < 0 or offset % 188:
        raise ValueError('Decoded bytes do not match a contiguous input packet slice')
    if any(decoded[k] != 0x47 for k in range(0, len(decoded), 188)):
        raise ValueError('Decoded packet sync byte differs from 0x47')
    total, recovered, first = len(original)//188, len(decoded)//188, offset//188
    end_loss = total - first - recovered
    coverage = recovered / total
    if recovered < MIN_RECOVERED_PACKETS or coverage < MIN_COVERAGE:
        raise ValueError(f'Insufficient recovery: {recovered} packets, {coverage:.3%} coverage')
    if first > MAX_START_LOSS_PACKETS or end_loss > MAX_END_LOSS_PACKETS:
        raise ValueError(f'Edge losses exceed budget: start={first}, end={end_loss} packets')
    return {'input_packets': total, 'decoded_packets': recovered,
            'first_input_packet': first, 'end_loss_packets': end_loss,
            'recovered_fraction': coverage, 'byte_exact_contiguous_match': True,
            'decoded_sha256': hashlib.sha256(decoded).hexdigest()}


def prepare_output(path):
    out = path.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError(f'Output directory must be empty: {out}')
    return out


def resample_reference(samples, sample_rate):
    if sample_rate not in (12_000_000, 16_000_000):
        raise ValueError('Reference sample rate must be 12000000 or 16000000')
    before = len(samples)
    up, down = (3, 4) if sample_rate == 12_000_000 else (1, 1)
    if up != down:
        from scipy.signal import resample_poly
        samples = resample_poly(samples, up, down).astype(samples.dtype)
    return samples, {'input_sample_rate_hz': 16_000_000,
                     'output_sample_rate_hz': sample_rate, 'up': up, 'down': down,
                     'method': 'scipy.signal.resample_poly' if up != down else 'identity',
                     'input_samples': before, 'output_samples': len(samples)}


def receiver_artifacts(module):
    root = Path(module.__file__).parent
    files = [p for p in root.rglob('*') if p.is_file() and p.suffix in ('.py', '.so')]
    library = next((p for p in root.parents if p.name in ('lib', 'lib64')), None)
    if library:
        files.extend(library.glob('libgnuradio-dvbs2rx*.dylib'))
        files.extend(library.glob('libgnuradio-dvbs2rx*.so*'))
    return {str(p.resolve()): file_hash(p) for p in files if p.is_file()}


def generate_fixture(args, out):
    import numpy as np
    from gnuradio.filter import firdes
    from gnuradio import gr, dvbs2rx
    sys.path.insert(0, str(args.reference_root / 'reference/dvbs2'))
    import dvbs2
    frames_wanted = math.ceil(args.seconds * profile.SYMBOL_RATE / 33282)
    npackets = math.ceil(frames_wanted * 42960 / 1504)
    packets = np.zeros((npackets, 188), np.uint8)
    for k in range(npackets):
        packets[k, :4] = [0x47, 1, 0, 0x10 | (k & 15)]
        packets[k, 4:] = (np.arange(184, dtype=np.uint32) + k) % 256
        packets[k, 4:8] = np.frombuffer(k.to_bytes(4, 'big'), np.uint8)
    packets.tofile(out / 'input.ts')
    frames = dvbs2.ts_bbframes(packets, '2/3', ro=profile.ROLLOFF)
    symbols = []
    for frame in frames:
        bi, bq = dvbs2.plframe(dvbs2.fecframe(frame, '2/3'), '2/3', pilots=True, n=profile.GOLD_CODE)
        symbols.append(((1-2*bi.astype(np.float32)) + 1j*(1-2*bq.astype(np.float32))) / np.sqrt(2))
    symbols = np.concatenate(symbols)
    x = np.zeros(2*len(symbols), np.complex64)
    x[::2] = symbols
    rrc = np.asarray(firdes.root_raised_cosine(2, 2, 1, profile.ROLLOFF, 41))
    y, conversion = resample_reference(np.convolve(x, rrc).astype(np.complex64), args.sample_rate)
    y.tofile(out / 'reference.fc32')
    return {'frames': len(frames), 'samples': len(y), 'sample_rate_hz': args.sample_rate,
            'waveform_seconds': len(y)/args.sample_rate, 'rational_conversion': conversion,
            'iq_sha256': file_hash(out/'reference.fc32'), 'input_ts_sha256': file_hash(out/'input.ts'),
            'runtime': {'python': sys.version, 'numpy': np.__version__, 'gnuradio': gr.version()},
            'receiver_module_sha256': receiver_artifacts(dvbs2rx)}


def execute_receiver(command, out, timeout=30):
    """Always persist captured output, including TimeoutExpired partial output."""
    started = time.monotonic()
    record = {'command': command, 'exit_code': None, 'timed_out': False, 'timeout_seconds': timeout}
    stdout = stderr = b''
    try:
        run = subprocess.run(command, capture_output=True, timeout=timeout)
        stdout, stderr = run.stdout, run.stderr
        record['exit_code'] = run.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b'', exc.stderr or b''
        record['timed_out'] = True
    except OSError as exc:
        record['launch_error'] = str(exc)
    finally:
        record['elapsed_seconds'] = time.monotonic()-started
        for name, data in [('stdout', stdout), ('stderr', stderr)]:
            if isinstance(data, str):
                data = data.encode()
            (out/f'receiver.{name}').write_bytes(data)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-root', type=Path, required=True)
    parser.add_argument('--receiver', type=Path, help='dvbs2-rx executable; defaults to PATH')
    parser.add_argument('--receiver-checkout', type=Path)
    parser.add_argument('--output-dir', type=Path, default=PROJECT_ROOT/'build/e200-reference')
    parser.add_argument('--seconds', type=float, default=.67, help='0.5 to 3 s; allows meaningful recovery beyond acquisition/EOF')
    parser.add_argument('--sample-rate', type=int, choices=(12_000_000, 16_000_000), default=profile.SAMPLE_RATE)
    parser.add_argument('--source-mode', choices=('file', 'prefetch'), default='file')
    parser.add_argument('--real-time', action='store_true', help='throttle file mode; prefetch is always paced')
    parser.add_argument('--keep-iq', action='store_true')
    args = parser.parse_args(argv)
    if not .5 <= args.seconds <= 3:
        parser.error('--seconds must be between 0.5 and 3')
    args.reference_root = args.reference_root.resolve()
    ref = args.reference_root/'reference/dvbs2'
    if not (ref/'dvbs2.py').is_file():
        parser.error('--reference-root must contain reference/dvbs2/dvbs2.py')
    receiver = args.receiver.resolve() if args.receiver else shutil.which('dvbs2-rx')
    if not receiver or not Path(receiver).is_file():
        parser.error('dvbs2-rx not found')
    receiver = str(Path(receiver).resolve())
    try:
        out = prepare_output(args.output_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    started = time.monotonic()
    result = {'status': 'failed', 'reason': None, 'source_mode': args.source_mode,
              'receiver_run': {'command': None, 'exit_code': None, 'timed_out': False},
              'scope': 'Synthetic sibling Python reference; no firmware, SPI or RF validation',
              'recovery_requirements': {'min_packets': MIN_RECOVERED_PACKETS, 'min_fraction': MIN_COVERAGE,
                                        'max_start_loss_packets': MAX_START_LOSS_PACKETS,
                                        'max_end_loss_packets': MAX_END_LOSS_PACKETS}}
    (out/'receiver.stdout').write_bytes(b'')
    (out/'receiver.stderr').write_bytes(b'')
    try:
        inputs = [Path(__file__), Path(__file__).with_name('e200_prefetch_reference.py'),
                  PROJECT_ROOT/'software/python/pigeonvision/radio/profile.py',
                  PROJECT_ROOT/'software/python/pigeonvision/radio/iio_source.py',
                  ref/'dvbs2.py', ref/'dvbs2_tables.py', Path(receiver), Path(sys.executable)]
        result['executed_input_sha256'] = {str(p.resolve()): file_hash(p) for p in inputs}
        result['reference'] = provenance(args.reference_root)
        result['project'] = provenance(PROJECT_ROOT)
        if args.receiver_checkout:
            result['receiver_checkout'] = provenance(args.receiver_checkout.resolve())
        result.update(generate_fixture(args, out))
        if args.source_mode == 'prefetch':
            command = [sys.executable, str(Path(__file__).with_name('e200_prefetch_reference.py')),
                       '--receiver', receiver, '--output-dir', str(out), '--sample-rate', str(args.sample_rate)]
        else:
            command = [receiver, '--source', 'file', '--in-file', str(out/'reference.fc32'),
                       '--in-iq-format', 'fc32', '--sink', 'file', '--out-file', str(out/'decoded.ts'),
                       '--samp-rate', str(args.sample_rate), '--sym-rate', str(profile.SYMBOL_RATE),
                       '--modcod', profile.MODCOD, '--frame-size', profile.FRAME_SIZE,
                       '--pilots', profile.PILOTS, '--rolloff', str(profile.ROLLOFF),
                       '--gold-code', str(profile.GOLD_CODE), '--multistream', profile.MULTISTREAM,
                       '--log', '--log-period', '1']
            if args.real_time:
                command.append('--in-real-time')
        result['receiver_run'] = execute_receiver(command, out)
        changed = [path for path, digest in result['executed_input_sha256'].items()
                   if file_hash(path) != digest]
        if changed:
            raise RuntimeError(f'Executed inputs changed during the run: {changed}')
        run = result['receiver_run']
        result['receiver_wall_seconds'] = run['elapsed_seconds']
        if run.get('launch_error'):
            raise RuntimeError(f'Receiver launch failed: {run["launch_error"]}')
        if run['timed_out'] or run['exit_code'] != 0:
            raise RuntimeError('Receiver timed out' if run['timed_out'] else f'Receiver exit {run["exit_code"]}: {run.get("launch_error", "")}')
        if args.source_mode == 'prefetch':
            result['prefetch'] = json.loads((out/'prefetch.json').read_text())
        result.update(verify_packets((out/'input.ts').read_bytes(), (out/'decoded.ts').read_bytes()))
        result['file_playback_throttled'] = args.real_time or args.source_mode == 'prefetch'
        logs = (out/'receiver.stdout').read_bytes() + (out/'receiver.stderr').read_bytes()
        result['lock_true_logged'] = b'Lock=True' in logs or bool(result.get('prefetch', {}).get('receiver_stats', {}).get('lock'))
        result['nominal_duration_over_wall_time'] = result['waveform_seconds']/run['elapsed_seconds']
        result['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['elapsed_seconds'] = time.monotonic()-started
        if not args.keep_iq:
            try:
                (out/'reference.fc32').unlink(missing_ok=True)
            except OSError as exc:
                result['status'] = 'failed'
                result['cleanup_error'] = str(exc)
                result['reason'] = result['reason'] or f'IQ cleanup failed: {exc}'
        result['iq_retained'] = (out/'reference.fc32').exists()
        with (out/'result.json').open('x') as handle:
            json.dump(result, handle, indent=2)
            handle.write('\n')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
