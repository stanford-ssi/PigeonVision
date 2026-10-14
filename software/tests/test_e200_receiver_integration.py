"""Launcher and pinned receiver integration checks; no radio is opened."""
import ast
import importlib.util
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


@pytest.fixture
def patch_updater(tmp_path):
    checkout = ROOT / 'output/e200-native/gr-dvbs2rx'
    if not (checkout / '.git').exists():
        pytest.skip('pinned receiver checkout unavailable')
    for name in ('apps/dvbs2-rx', 'lib/ldpc_decoder/CMakeLists.txt'):
        original = subprocess.check_output(['git', '-C', str(checkout), 'show',
            f'130c31576cfeebb1a5842b24ccaf4a561b6a9990:{name}'], text=True)
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original)
    path = ROOT / 'software/tools/e200_apply_receiver_patches.py'
    spec = importlib.util.spec_from_file_location('apply_receiver_patches', path)
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    return updater, tmp_path


@pytest.mark.parametrize('state', ['pristine', 'previous', 'current'])
def test_patch_update_preserves_unrelated_changes_and_is_repeatable(patch_updater, state):
    updater, checkout = patch_updater
    if state != 'pristine':
        patch = PATCH.read_text()
        if state == 'previous':
            patch = patch.replace(updater.NEW_CALL, updater.OLD_CALL)
        assert updater.git_apply(checkout, patch, '--recount').returncode == 0
    app = checkout / 'apps/dvbs2-rx'
    app.write_text(app.read_text() + '\n# unrelated local change\n')
    updater.apply_patches(checkout)
    installed = app.read_text()
    assert 'frequency=int(self.freq))' in installed
    assert installed.endswith('# unrelated local change\n')
    updater.apply_patches(checkout)
    assert app.read_text() == installed


def test_unknown_patch_conflict_preserves_both_files(patch_updater):
    updater, checkout = patch_updater
    previous = PATCH.read_text().replace(updater.NEW_CALL, updater.OLD_CALL)
    assert updater.git_apply(checkout, previous, '--recount').returncode == 0
    app = checkout / 'apps/dvbs2-rx'
    app.write_text(app.read_text().replace('self.iio_prefetch_source = open_iio(',
                                          'self.iio_prefetch_source = custom_source('))
    before = {path: path.read_bytes() for path in checkout.rglob('*') if path.is_file()}
    with pytest.raises(RuntimeError, match='conflicts with local changes'):
        updater.apply_patches(checkout)
    assert {path: path.read_bytes() for path in before} == before


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
        assert kwargs == {'sample_rate': 12000000, 'buffer_samples': 262144,
                          'frequency': 2400000000}
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
