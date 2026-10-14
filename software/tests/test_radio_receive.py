"""Real child processes verify supervised shutdown without SDR or TS input."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from pigeonvision.radio.receive import commands, supervise

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def children(tmp_path):
    script = tmp_path / 'child.py'
    script.write_text('''import os,signal,sys,time
from pathlib import Path
name,mode,base=sys.argv[1:4]
if mode=='ignore':
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    signal.signal(signal.SIGINT,signal.SIG_IGN)
Path(base,name+'.pid').write_text(str(os.getpid()))
if name=='receiver':
    assert Path(base,'guard.pid').exists()
if mode=='error':
    time.sleep(.15)
    sys.exit(23)
if mode=='ok':
    sys.exit(0)
while True:
    time.sleep(.05)
''')
    def command(name, mode):
        return [sys.executable, str(script), name, mode, str(tmp_path)]
    return tmp_path, command


def assert_reaped(base):
    for pid_file in base.glob('*.pid'):
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)


def test_forwarder_failure_stops_silent_receiver(children):
    base, command = children
    started = time.monotonic()
    assert supervise(command('guard', 'ok'), command('receiver', 'silent'),
                     command('forwarder', 'error'), grace=.2) == 23
    assert time.monotonic() - started < 2
    assert_reaped(base)


def test_receiver_failure_stops_forwarder(children):
    base, command = children
    assert supervise(command('guard', 'ok'), command('receiver', 'error'),
                     command('forwarder', 'silent'), grace=.2) == 23
    assert_reaped(base)


def test_guard_failure_never_starts_pipeline(children):
    base, command = children
    assert supervise(command('guard', 'error'), command('receiver', 'silent'),
                     command('forwarder', 'silent'), grace=.2) == 23
    assert not (base / 'receiver.pid').exists()
    assert not (base / 'forwarder.pid').exists()
    assert_reaped(base)


def test_failed_receiver_start_reaps_forwarder(children):
    base, command = children
    assert supervise(command('guard', 'ok'), ['/nonexistent/decoder'],
                     command('forwarder', 'silent'), grace=.2) == 127
    assert_reaped(base)


@pytest.mark.parametrize('signum', [signal.SIGINT, signal.SIGTERM])
def test_signal_to_launcher_alone_stops_and_reaps_children(children, signum):
    base, command = children
    # E200_PYTHON may be an executable wrapper; the shell must exec it so the
    # launcher PID remains the supervisor PID. Inject only fake child commands.
    driver = base / 'radio-python'
    driver.write_text('#!' + sys.executable + '\n' +
        'from pigeonvision.radio.receive import supervise\n' +
        f'raise SystemExit(supervise({command("guard", "ok")!r}, '
        f'{command("receiver", "ignore")!r}, {command("forwarder", "ignore")!r}, grace=.2))\n')
    driver.chmod(0o755)
    process = subprocess.Popen(['bash', str(ROOT / 'software/tools/e200_receive.sh'),
                                '2400000000'], env={**os.environ, 'E200_PYTHON': str(driver)},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 3
        while not all((base / (name + '.pid')).exists() for name in ('receiver', 'forwarder')):
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.02)
        process.send_signal(signum)  # Deliberately do not signal the process group.
        process.communicate(timeout=2)
        assert process.returncode == 128 + signum
        assert_reaped(base)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_binary_transport_uses_only_explicit_fd(tmp_path):
    captured = tmp_path / 'transport'
    receiver = [sys.executable, '-c',
                'import os,sys,time; print("diagnostic"); '
                'os.write(int(sys.argv[-1]), b"TS-payload"); time.sleep(.3)']
    forwarder = [sys.executable, '-c',
                 f'import sys; from pathlib import Path; '
                 f'Path({str(captured)!r}).write_bytes(sys.stdin.buffer.read(10)); '
                 'import time; time.sleep(.3)']
    assert supervise([sys.executable, '-c', 'pass'], receiver, forwarder, grace=.2) == 0
    assert captured.read_bytes() == b'TS-payload'


def test_unexpected_clean_forwarder_exit_stops_silent_receiver(children):
    base, command = children
    assert supervise(command('guard', 'ok'), command('receiver', 'silent'),
                     command('forwarder', 'ok'), grace=.2) == 1
    assert_reaped(base)


def test_failed_forwarder_start_never_starts_receiver(children):
    base, command = children
    assert supervise(command('guard', 'ok'), command('receiver', 'silent'),
                     ['/nonexistent/forwarder'], grace=.2) == 127
    assert not (base / 'receiver.pid').exists()
    assert_reaped(base)


def test_failed_receiver_group_is_cleaned_after_leader_exits(children):
    base, command = children
    descendant_pid = base / 'descendant.pid'
    descendant = [sys.executable, '-c',
                  'import os,signal,time; from pathlib import Path; '
                  'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                  'signal.signal(signal.SIGINT, signal.SIG_IGN); '
                  f'Path({str(descendant_pid)!r}).write_text(str(os.getpid())); '
                  'time.sleep(30)']
    receiver = [sys.executable, '-c',
                'import subprocess,sys,time; from pathlib import Path; '
                f'subprocess.Popen({descendant!r}); '
                f'path=Path({str(descendant_pid)!r}); '
                'exec("while not path.exists(): time.sleep(.01)"); '
                'sys.exit(23)']
    try:
        assert supervise(command('guard', 'ok'), receiver,
                         command('forwarder', 'silent'), grace=.2) == 23
        pid = int(descendant_pid.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.02)
        else:
            pytest.fail('Receiver descendant survived process-group cleanup')
        assert_reaped(base)
    finally:
        # Even a failing regression must never leave its descendant running.
        if descendant_pid.exists():
            try:
                os.kill(int(descendant_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
