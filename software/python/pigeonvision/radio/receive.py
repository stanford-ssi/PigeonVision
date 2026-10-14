"""Supervise the RX guard, DVB-S2 decoder and binary-TS forwarder."""
import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

from . import profile

SHUTDOWN_GRACE_S = 6.0


def supervise(guard, receiver, forwarder, *, grace=SHUTDOWN_GRACE_S):
    """Stop and reap every child on peer exit, startup failure or launcher signal."""
    children = []
    requested_signal = None
    previous = {}

    def request_stop(signum, frame):
        nonlocal requested_signal
        requested_signal = signum

    def start(command, **kwargs):
        child = subprocess.Popen(command, start_new_session=True, **kwargs)
        children.append(child)
        return child

    def wait_for_exit(processes):
        while requested_signal is None:
            statuses = [process.poll() for process in processes]
            # Prefer an actual failure when both children exit together.
            for status in statuses:
                if status not in (None, 0):
                    return status if status > 0 else 128 - status
            if any(status is not None for status in statuses):
                return 0
            time.sleep(.05)
        return 128 + requested_signal

    def stop_children():
        # A group can outlive its leader. Keep every group started by us until
        # cleanup completes, including a decoder that exited after spawning work.
        groups = [child.pid for child in children]

        def send(group, signum):
            try:
                os.killpg(group, signum)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                # macOS can report EPERM while a signalled group is exiting.
                # Keep tracking it until the next probe or the kill deadline.
                return True

        for group in groups:
            send(group, requested_signal or signal.SIGTERM)
        deadline = time.monotonic() + grace
        while groups:
            for child in children:
                child.poll()  # Reap direct children so zombies don't hold groups.
            groups = [group for group in groups if send(group, 0)]
            remaining = deadline - time.monotonic()
            if not groups or remaining <= 0:
                break
            time.sleep(min(.05, remaining))
        for group in groups:
            send(group, signal.SIGKILL)
        for child in children:
            child.wait()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, request_stop)
        guard_child = start(guard, stdout=sys.stderr)
        result = wait_for_exit([guard_child])
        if result or requested_signal is not None:
            return result
        read_fd, write_fd = os.pipe()
        try:
            # Only the explicit fd carries TS. Ordinary decoder stdout is diagnostic.
            receiver_command = [*receiver, '--out-fd', str(write_fd)]
            sink = start(forwarder, stdin=read_fd, stdout=sys.stderr)
            if requested_signal is not None:
                return 128 + requested_signal
            source = start(receiver_command, pass_fds=(write_fd,), stdout=sys.stderr)
        finally:
            os.close(read_fd)
            os.close(write_fd)
        result = wait_for_exit([source, sink])
        if result == 0 and source.poll() is None:
            print('TS forwarder exited while the receiver was still running.', file=sys.stderr)
            return 1
        return result
    except OSError as exc:
        print(f'Cannot start RX pipeline: {exc}', file=sys.stderr)
        return 127
    finally:
        stop_children()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def commands(frequency, environ):
    rate = environ.get('RX_SAMPLE_RATE', str(profile.SAMPLE_RATE))
    buffer_samples = environ.get('RX_BUFFER_SAMPLES', str(profile.BUFFER_SAMPLES))
    if rate not in {'12000000', '16000000'}:
        raise ValueError('RX_SAMPLE_RATE must be 12000000 or 16000000.')
    if buffer_samples not in {'65536', '262144', '1048576'}:
        raise ValueError('RX_BUFFER_SAMPLES must be 65536, 262144 or 1048576.')
    uri = environ.get('E200_URI', profile.URI)
    guard = [sys.executable, '-m', 'pigeonvision.radio.rx_guard', '--uri', uri]
    receiver = ['dvbs2-rx', '--source', 'plutosdr', '--plutosdr-addr', uri,
                '--plutosdr-buf-size', buffer_samples, '--freq', str(frequency),
                '--sym-rate', str(profile.SYMBOL_RATE), '--samp-rate', rate,
                '--modcod', profile.MODCOD, '--frame-size', profile.FRAME_SIZE,
                '--pilots', profile.PILOTS, '--rolloff', str(profile.ROLLOFF),
                '--gold-code', str(profile.GOLD_CODE), '--multistream', profile.MULTISTREAM,
                '--plutosdr-gain-mode', 'manual', '--plutosdr-gain', environ.get('RX_GAIN_DB', '0'),
                '--sink', 'fd', '--log']
    forwarder = ['tsp', '--realtime', '--initial-input-packets', '7',
                 '--max-input-packets', '7', '--max-flushed-packets', '7',
                 '-I', 'file', '-', '-O', 'ip', environ.get('TS_DEST', profile.TS_DEST),
                 '--packet-burst', '7', '--enforce-burst']
    return guard, receiver, forwarder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Environment: E200_URI, RX_GAIN_DB, RX_SAMPLE_RATE (12000000 default or 16000000), '
        'RX_BUFFER_SAMPLES (65536, 262144 default or 1048576), TS_DEST. '
        'The shell wrapper selects its Python using E200_PYTHON.'))
    parser.add_argument('frequency_hz', type=int)
    parser.add_argument('--dry-run', action='store_true', help='Print commands without opening a device')
    args = parser.parse_args(argv)
    if args.frequency_hz <= 0:
        parser.error('Frequency must be a positive integer in Hz.')
    try:
        guard, receiver, forwarder = commands(args.frequency_hz, os.environ)
    except ValueError as exc:
        parser.error(str(exc))
    if args.dry_run:
        print('export PYTHONPATH=' + shlex.quote(os.environ.get('PYTHONPATH', '')))
        print(shlex.join(guard) + ' && ' + shlex.join(receiver) +
              ' --out-fd 3 3>&1 1>&2 | ' + shlex.join(forwarder))
        return 0
    for command in (guard, receiver, forwarder):
        if shutil.which(command[0]) is None:
            print(f'Missing {command[0]}. See the ground receiver README, Radio input.', file=sys.stderr)
            return 127
    return supervise(guard, receiver, forwarder)


if __name__ == '__main__':
    raise SystemExit(main())
