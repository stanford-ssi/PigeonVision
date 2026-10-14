"""Paced S12 reader checks and optional native full-decoder regression."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT/'software/tools/e200_prefetch_reference.py'
spec = importlib.util.spec_from_file_location('prefetch_reference', HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class PacedReaderTest(unittest.TestCase):
    def test_s12_chunks_quantization_eof_and_cancel(self):
        import numpy as np
        samples = np.array([-.8-.4j, .2+.8j, -.1+.3j], np.complex64)
        reader = helper.PacedS12Reader(samples, 1_000_000_000, 2, .5)
        raw = reader.read()+reader.read()
        self.assertIsNone(reader.read())
        self.assertEqual(reader.clipped, 0)
        words = np.frombuffer(raw, dtype='<i2')
        self.assertTrue(np.all((words.view(np.uint16) & np.uint16(0xf000)) == 0))
        restored = ((words << 4) >> 4).astype(np.float32).view(np.complex64)/2048
        np.testing.assert_allclose(restored, samples*.5, atol=1/2048)
        cancelled = helper.PacedS12Reader(samples, 1, 2, 1)
        cancelled.cancel()
        self.assertIsNone(cancelled.read())

    @unittest.skipUnless(importlib.util.find_spec('gnuradio'), 'Requires isolated native GNU Radio environment')
    def test_native_paced_prefetch_full_decoder(self):
        reference = Path(os.environ.get('E200_REFERENCE_ROOT',
                         str(ROOT.parents[2]/'RP2350_IQ_Benchmark')))
        receiver = Path(os.environ.get('E200_REFERENCE_RECEIVER',
                        str(Path(sys.executable).with_name('dvbs2-rx'))))
        if not reference.is_dir() or not receiver.is_file():
            self.skipTest('Set E200_REFERENCE_ROOT and E200_REFERENCE_RECEIVER')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'native-reference'
            run = subprocess.run([sys.executable, str(ROOT/'software/tools/e200_reference_check.py'),
                                  '--reference-root', str(reference), '--receiver', str(receiver),
                                  '--source-mode', 'prefetch', '--seconds', '.67',
                                  '--output-dir', str(output)], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            result = json.loads((output/'result.json').read_text())
            self.assertEqual(result['status'], 'passed')
            self.assertGreaterEqual(result['recovered_fraction'], .8)
            self.assertTrue(result['byte_exact_contiguous_match'])
            source = result['prefetch']['source_report']
            self.assertEqual(source['samples_received'], source['samples_delivered'])
            self.assertIsNone(source['error'])
            self.assertFalse((output/'reference.fc32').exists())


if __name__ == '__main__':
    unittest.main()
