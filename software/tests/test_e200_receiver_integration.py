"""Launcher and pinned receiver integration checks; no radio is opened."""
import ast
import os
from pathlib import Path
import shlex
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / 'software/tools/e200_receive.sh'
PATCH = ROOT / 'software/platform/gr-dvbs2rx-iio-prefetch.patch'


def test_launcher_exports_source_path_without_access(tmp_path):
    env = {**os.environ, 'E200_PYTHON': sys.executable,
           'PYTHONPATH': '/existing/modules'}
    env.pop('RX_SAMPLE_RATE', None)
    run = subprocess.run(['bash', str(LAUNCHER), '2400000000', '--dry-run'],
                         env=env, capture_output=True, text=True, check=True)
    args = shlex.split(run.stdout)
    assert args[args.index('--samp-rate') + 1] == '12000000'
    assert args[args.index('--sym-rate') + 1] == '8000000'
    assert args[args.index('--plutosdr-buf-size') + 1] == '262144'
    assert f'{ROOT / "software/python"}:/existing/modules' in run.stdout
    assert run.stdout.index('pigeonvision.radio.rx_guard') < run.stdout.index('&& dvbs2-rx')


def test_patch_configures_native_block_before_returning_prefetch(tmp_path, monkeypatch):
    checkout = ROOT / 'output/e200-native/gr-dvbs2rx'
    if not (checkout / '.git').exists():
        pytest.skip('pinned receiver checkout unavailable')
    original = subprocess.check_output([
        'git', '-C', str(checkout), 'show',
        '130c31576cfeebb1a5842b24ccaf4a561b6a9990:apps/dvbs2-rx'], text=True)
    apps = tmp_path / 'apps'
    apps.mkdir()
    app = apps / 'dvbs2-rx'
    app.write_text(original)
    subprocess.run(['git', 'apply', str(PATCH)], cwd=tmp_path, check=True)
    tree = ast.parse(app.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == 'DVBS2RxTopBlock')
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == 'setup_plutosdr_source')
    calls = []
    class Native:
        def __getattr__(self, name):
            return lambda *args: calls.append(name)
    native, prefetch = Native(), object()
    def open_iio(uri, **kwargs):
        assert calls[-1] == 'set_filter_params'
        assert kwargs == {'sample_rate': 12000000, 'buffer_samples': 262144}
        assert uri == 'ip:test'
        return prefetch
    monkeypatch.setitem(sys.modules, 'gnuradio', types.SimpleNamespace(
        iio=types.SimpleNamespace(fmcomms2_source_fc32=lambda *args: native)))
    monkeypatch.setitem(sys.modules, 'pigeonvision.radio.iio_source',
                        types.SimpleNamespace(open_iio=open_iio))
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(app), 'exec'), namespace)
    tb = types.SimpleNamespace(plutosdr={'address': 'ip:test', 'buffer_size': 262144,
                                        'gain_mode': 'manual', 'manual_gain': 0},
                               freq=2400000000, samp_rate=12000000)
    assert namespace['setup_plutosdr_source'](tb) is prefetch
    assert tb.iio_pluto_source is native
    assert tb.iio_prefetch_source is prefetch
    assert 'start' not in calls
