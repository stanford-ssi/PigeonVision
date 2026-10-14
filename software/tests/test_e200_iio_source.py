"""Offline prefetch checks. Native GNU environment can run this file directly."""
import sys
from pathlib import Path
import time
import ast
import json
import os
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
if __name__ != '__main__':
    import pytest
    pytest.importorskip('gnuradio', reason='Optional native GNU Radio environment')
try:
    import numpy as np
    from gnuradio import blocks, gr
    from pigeonvision.radio.iio_source import PrefetchSource, convert_iq, open_iio, _require_rx_frequency
except ImportError as exc:
    raise unittest.SkipTest('Optional GNU Radio environment is unavailable') from exc


class IIOPrefetchTest(unittest.TestCase):

    def test_rx_lo_readback_allows_only_bounded_driver_rounding(self):
        lo = SimpleNamespace(attrs={'frequency': SimpleNamespace(value='1280000000')})
        phy = SimpleNamespace(find_channel=lambda name, output: lo)
        for requested in (1280000000, 1280000001, 1280000006, 1279999994):
            with self.subTest(requested=requested):
                _require_rx_frequency(phy, requested)
        for requested in (1280000007, 1279999993, 2400000000):
            with self.subTest(requested=requested), self.assertRaisesRegex(RuntimeError, 'more than 6 Hz'):
                _require_rx_frequency(phy, requested)

    def test_conversion(self):
        raw = np.array([0x0000, 0x07ff, 0x0800, 0x0fff, 0xf800, 0xffff], dtype='<u2').tobytes()
        expected = np.array([0+2047j, -2048-1j, -2048-1j], np.complex64) / 2048
        np.testing.assert_array_equal(convert_iq(raw), expected)


    def test_finite_flowgraph(self):
        raw = np.arange(1024, dtype='<i2').tobytes()
        remaining = [raw] * 4
        source = PrefetchSource(lambda: remaining.pop() if remaining else None, queue_blocks=8)
        sink = blocks.vector_sink_c()
        tb = gr.top_block()
        tb.connect(source, sink)
        tb.run()
        np.testing.assert_array_equal(np.array(sink.data()), np.tile(convert_iq(raw), 4))
        assert source.error is None
        assert source.report()['samples_delivered'] == 2048


    def test_slow_first_frame_does_not_park_gnu_scheduler(self):
        raw = bytes(4096)
        count = [0]
        def read():
            if count[0] == 0:
                time.sleep(.15)  # exceeds the former 50ms queue timeout
            count[0] += 1
            return raw if count[0] <= 4 else None
        source = PrefetchSource(read, queue_blocks=8)
        sink = blocks.vector_sink_c()
        tb = gr.top_block()
        tb.connect(source, sink)
        tb.run()
        assert len(sink.data()) == 4096
        assert source.error is None
        assert source.report()['producer_seconds_including_startup'] >= .15
        assert source.stats['samples_delivered'] == 4096



    def test_queue_full_is_explicit_failure(self):
        source = PrefetchSource(lambda: bytes(4096), queue_blocks=1)
        source.start()
        source.consume_ready.set()
        source.worker.join(2)
        assert not source.worker.is_alive()
        assert source.error == 'Host queue full'
        assert source.stats['blocks_received'] == 1
        assert source.work([], [np.empty(1024, np.complex64)]) == -1
        source.stop()


    def test_startup_delay_does_not_start_capture(self):
        calls = []
        def read():
            calls.append(True)
            return None
        source = PrefetchSource(read, queue_blocks=1)
        source.start()
        time.sleep(.3)  # exceeds the real 8-block startup absorption interval
        assert calls == []
        assert source.error is None
        assert source.work([], [np.empty(32, np.complex64)]) == -1
        source.worker.join(1)
        assert calls == [True]
        source.stop()


    def test_stop_before_first_work_never_starts_capture(self):
        calls = []
        source = PrefetchSource(lambda: calls.append(True), queue_blocks=1)
        source.start()
        source.stop()
        assert calls == []
        assert not source.worker.is_alive()



    def test_iio_format_gate_and_lazy_stream_start(self):
        def attrs(**values):
            return {key: SimpleNamespace(value=str(value)) for key, value in values.items()}
        fmt = SimpleNamespace(is_be=False, is_signed=True, bits=12, length=16, shift=0, repeat=1)
        channels = [SimpleNamespace(id=name, output=False, enabled=False, index=index,
                                    attrs=attrs(sampling_frequency=12000000), data_format=fmt)
                    for index, name in enumerate(('voltage0', 'voltage1'))]
        calls = []
        adc = SimpleNamespace(channels=channels, sample_size=4,
                              find_channel=lambda name, output: next(c for c in channels if c.id == name),
                              set_kernel_buffers_count=lambda count: calls.append(('kernel', count)),
                              reg_write=lambda reg, value: calls.append(('clear', reg, value)),
                              reg_read=lambda reg: 0)
        lo = SimpleNamespace(attrs=attrs(frequency=2400000000))
        phy = SimpleNamespace(find_channel=lambda name, output: lo if name == 'altvoltage0' else SimpleNamespace(attrs=attrs(powerdown=1)),
                              attrs=attrs(calib_mode='manual_tx_quad 26'),
                              debug_attrs={'adi,tx-lo-powerdown-managed-enable': SimpleNamespace(value='0')})
        ctx = SimpleNamespace(set_timeout=lambda count: None,
                              find_device=lambda name: phy if name == 'ad9361-phy' else adc)
        class Buffer:
            def __init__(self, *args):
                calls.append(('buffer',))
                self._buffer = 'fake'
            def read(self):
                return bytes(16)
            def cancel(self):
                calls.append(('cancel',))
        original = sys.modules.get('iio')
        sys.modules['iio'] = SimpleNamespace(Context=lambda uri: ctx, Buffer=Buffer,
                                            _buffer_refill=lambda pointer: 16)
        try:
            source = open_iio('mock:', frequency=2400000000)
            assert calls == [('kernel', 16)]
            for invalid in ('915000000', 'invalid', None):
                with self.subTest(frequency_readback=invalid):
                    lo.attrs = attrs(frequency=invalid) if invalid is not None else {}
                    with self.assertRaisesRegex(RuntimeError, 'RX LO frequency readback'):
                        open_iio('mock:', frequency=2400000000)
                    assert calls == [('kernel', 16)]
            lo.attrs = attrs(frequency=915000000)
            with self.assertRaisesRegex(RuntimeError, 'RX LO frequency readback'):
                source.reader()
            assert calls == [('kernel', 16)]
            lo.attrs = attrs(frequency=2400000000)
            assert source.reader() == bytes(16)
            assert ('buffer',) in calls
            assert source.current_status()['kernel_buffer_blocks_requested'] == 16
            source.close()
            fmt.is_be = True
            try:
                open_iio('mock:')
            except RuntimeError as exc:
                assert 'le:S12/16' in str(exc)
            else:
                raise AssertionError('Big-endian format was not refused')
        finally:
            if original is None:
                del sys.modules['iio']
            else:
                sys.modules['iio'] = original

    def test_cleanup_runs_after_refill_on_producer_thread(self):
        entered, released = threading.Event(), threading.Event()
        closed_by = []
        def read():
            entered.set()
            released.wait(2)
            raise OSError('Cancelled refill')
        source = PrefetchSource(read, cancel=released.set,
                                close=lambda: closed_by.append(threading.get_ident()))
        source.start()
        source.consume_ready.set()
        self.assertTrue(entered.wait(1))
        self.assertTrue(source.stop())
        self.assertEqual(closed_by, [source.worker.ident])
        self.assertFalse(source.worker.is_alive())
        self.assertIsNone(source.error)
        self.assertEqual(source.report()['state'], 'stopped')


    def test_monitor_status_excludes_production_sized_history(self):
        source = PrefetchSource(lambda: None)
        for index in range(120):
            source.windows.append({'elapsed_seconds': index + 1.234567890123,
                                   'duration_seconds': 1.004123456789,
                                   'received_samples_per_second': 12000123.456789,
                                   'delivered_samples_per_second': 12000123.456789})
        self.assertGreater(len(json.dumps(source.report()).encode()), 16 * 1024)
        source.error = 'diagnostic ' * 3000
        monitor = {'lock': True, 'snr': 20.0,
                   'fec': {'frames': 1000, 'errors': 0, 'fer': 0.0},
                   'iio': source.current_status()}
        self.assertNotIn('rate_windows', monitor['iio'])
        self.assertLess(len(json.dumps(monitor).encode()), 4096)
        self.assertGreater(len(json.dumps(source.report()).encode()), 16 * 1024)
        self.assertEqual(len(source.report()['rate_windows']), 120)

    def _patched_app(self, directory):
        root = Path(__file__).resolve().parents[2]
        checkout = root / 'output/e200-native/gr-dvbs2rx'
        if not (checkout / '.git').exists():
            self.skipTest('Pinned receiver checkout unavailable')
        original = subprocess.check_output(['git', '-C', str(checkout), 'show',
            '130c31576cfeebb1a5842b24ccaf4a561b6a9990:apps/dvbs2-rx'], text=True)
        app = Path(directory) / 'apps/dvbs2-rx'
        app.parent.mkdir()
        app.write_text(original)
        subprocess.run(['git', 'apply', str(root / 'software/platform/gr-dvbs2rx-iio-prefetch.patch')],
                       cwd=directory, check=True, capture_output=True)
        return app

    def test_topblock_stop_cancels_stalled_reader_before_scheduler_wait(self):
        entered, cancelled = threading.Event(), threading.Event()
        def read():
            entered.set()
            cancelled.wait(2)  # regression backstop; correct shutdown is immediate
            return None
        source = PrefetchSource(read, cancel=cancelled.set)
        with tempfile.TemporaryDirectory() as directory:
            app = self._patched_app(directory)
            tree = ast.parse(app.read_text())
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == 'DVBS2RxTopBlock')
            stop = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'stop')
            namespace = {'gr': gr}
            exec(compile(ast.Module(body=[stop], type_ignores=[]), str(app), 'exec'), namespace)
            class Top(gr.top_block):
                stop = namespace['stop']
            tb = Top()
            tb.iio_prefetch_source = source
            tb.connect(source, blocks.null_sink(gr.sizeof_gr_complex))
            tb.start()
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            tb.stop()
            tb.wait()
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(cancelled.is_set())
            self.assertFalse(source.worker.is_alive())
            self.assertIsNone(source.error)

    def test_patched_real_file_mode_logs_and_exits_successfully(self):
        root = Path(__file__).resolve().parents[2]
        fixture = root / 'output/review-2026-10-13/reference-12m/reference.fc32'
        if not fixture.exists():
            self.skipTest('Saved file-only reference IQ unavailable')
        with tempfile.TemporaryDirectory() as directory:
            app = self._patched_app(directory)
            decoded = Path(directory) / 'decoded.ts'
            env = {**os.environ, 'PYTHONPATH': str(root / 'software/python')}
            result = subprocess.run([sys.executable, str(app), '--source', 'file',
                '--in-file', str(fixture), '--in-iq-format', 'fc32', '--sink', 'file',
                '--out-file', str(decoded), '--samp-rate', '12e6', '--sym-rate', '8e6',
                '--modcod', 'qpsk2/3', '--frame-size', 'normal', '--pilots', 'on',
                '--rolloff', '0.2', '--log', '--log-period', '1'],
                env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertGreater(decoded.stat().st_size, 188)
            self.assertNotIn('Traceback', result.stdout + result.stderr)
            self.assertIn('Lock=', result.stdout + result.stderr)


    def test_real_signal_handler_fails_when_reader_cleanup_times_out(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            app = self._patched_app(directory)
            driver = Path(directory) / 'signal_cleanup.py'
            driver.write_text("""import os,runpy,signal,sys,threading,time
from gnuradio import blocks,gr
from pigeonvision.radio.iio_source import PrefetchSource
module=runpy.run_path(sys.argv[1])
original=module['DVBS2RxTopBlock']
entered=threading.Event()
def read():
    entered.set()
    threading.Event().wait(20)
    return None
class TestTop(gr.top_block):
    stop=original.stop
    def __init__(self, options):
        gr.top_block.__init__(self)
        self.iio_prefetch_source=PrefetchSource(read)
        self.connect(self.iio_prefetch_source,blocks.null_sink(gr.sizeof_gr_complex))
def notify():
    if entered.wait(2):
        time.sleep(.15)
        os.kill(os.getpid(),signal.SIGTERM)
threading.Thread(target=notify,daemon=True).start()
module['main'].__globals__['DVBS2RxTopBlock']=TestTop
sys.argv=[sys.argv[1],'--source','file','--in-file','/dev/null',
          '--sink','file','--out-file','/dev/null']
module['main']()
""")
            env = {**os.environ, 'PYTHONPATH': str(root / 'software/python')}
            result = subprocess.run([sys.executable, str(driver), str(app)],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('RX worker did not stop within 4 seconds', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
