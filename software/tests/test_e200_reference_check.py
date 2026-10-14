"""Packet recovery must be exact and aligned; no GNU Radio dependency."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import contextlib
import io
import json
import subprocess
import sys
from unittest import mock

path = Path(__file__).resolve().parents[1] / 'tools/e200_reference_check.py'
spec = importlib.util.spec_from_file_location('e200_reference_check', path)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ReferencePacketsTest(unittest.TestCase):
    def setUp(self):
        self.packets = [bytes([0x47]) + k.to_bytes(4, 'big') + bytes([k % 256]) * 183
                        for k in range(5000)]
        self.original = b''.join(self.packets)

    def test_sample_rate_cli_rejects_unsupported_rate_before_imports(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                check.main(['--reference-root', '/unused', '--sample-rate', '14000000'])
        self.assertEqual(error.exception.code, 2)

    def test_resampling_preserves_duration_and_inband_tone(self):
        import numpy as np
        samples = np.exp(2j * np.pi * 1e6 * np.arange(16000) / 16e6).astype(np.complex64)
        same, metadata = check.resample_reference(samples, 16000000)
        self.assertIs(same, samples)
        self.assertEqual(metadata['method'], 'identity')
        converted, metadata = check.resample_reference(samples, 12000000)
        self.assertEqual(len(converted), 12000)
        self.assertEqual((metadata['up'], metadata['down']), (3, 4))
        self.assertEqual(len(converted) / 12e6, len(samples) / 16e6)
        expected = np.exp(2j * np.pi * 1e6 * np.arange(12000) / 12e6)
        self.assertLess(float(np.max(np.abs(converted[100:-100] - expected[100:-100]))), .003)

    def test_output_preserves_prior_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'new-run'
            self.assertEqual(check.prepare_output(out), out.resolve())
            evidence = out / 'result.json'
            evidence.write_text('prior evidence')
            with self.assertRaisesRegex(ValueError, 'must be empty'):
                check.prepare_output(out)
            self.assertEqual(evidence.read_text(), 'prior evidence')

    def test_acquisition_and_eof_loss_allowed(self):
        result = check.verify_packets(self.original, b''.join(self.packets[100:4900]))
        self.assertEqual(result['decoded_packets'], 4800)
        self.assertEqual(result['first_input_packet'], 100)

    def test_one_packet_low_coverage_and_excess_edge_losses_fail(self):
        for selection in (self.packets[100:101], self.packets[100:1000],
                          self.packets[300:4900], self.packets[100:4400]):
            with self.subTest(packets=len(selection)), self.assertRaises(ValueError):
                check.verify_packets(self.original, b''.join(selection))

    def test_provenance_includes_untracked_status_and_content_hashes(self):
        def git_reply(command, **kwargs):
            if 'status' in command:
                self.assertIn('--untracked-files=all', command)
                return '?? new_source.py\n'
            return 'abc123\n' if 'rev-parse' in command else ''
        with mock.patch.object(check.subprocess, 'check_output', side_effect=git_reply):
            self.assertTrue(check.provenance(Path('/unused'))['dirty'])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'untracked.py'
            source.write_text('first')
            before = check.file_hash(source)
            source.write_text('second')
            self.assertNotEqual(before, check.file_hash(source))

    def _failed_run(self, body, *, timeout=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root/'reference/dvbs2'
            reference.mkdir(parents=True)
            for name in ('dvbs2.py', 'dvbs2_tables.py'):
                (reference/name).write_text('# mock reference input\n')
            receiver = root/'receiver'
            receiver.write_text('#!' + sys.executable + '\n' + body)
            receiver.chmod(0o755)
            output = root/'run'
            def fixture(args, out):
                (out/'input.ts').write_bytes(self.original)
                (out/'reference.fc32').write_bytes(bytes(8))
                return {'samples': 1, 'waveform_seconds': 1/12000000}
            execute = check.execute_receiver
            def bounded(command, out):
                return execute(command, out, timeout=timeout or 5)
            with mock.patch.object(check, 'generate_fixture', side_effect=fixture), \
                 mock.patch.object(check, 'execute_receiver', side_effect=bounded), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = check.main(['--reference-root', str(root), '--receiver', str(receiver),
                                   '--output-dir', str(output)])
            self.assertEqual(code, 1)
            result = json.loads((output/'result.json').read_text())
            self.assertEqual(result['status'], 'failed')
            self.assertIsInstance(result['receiver_run']['command'], list)
            self.assertGreater(result['elapsed_seconds'], 0)
            self.assertTrue(result['reason'])
            self.assertFalse((output/'reference.fc32').exists())
            return result, (output/'receiver.stdout').read_text(), (output/'receiver.stderr').read_text()

    def test_nonzero_receiver_saves_failure_record_and_logs(self):
        result, stdout, stderr = self._failed_run(
            'import sys\nprint("before failure")\nprint("diagnostic", file=sys.stderr)\nraise SystemExit(7)\n')
        self.assertEqual(result['receiver_run']['exit_code'], 7)
        self.assertFalse(result['receiver_run']['timed_out'])
        self.assertIn('before failure', stdout)
        self.assertIn('diagnostic', stderr)

    def test_timeout_saves_partial_output_and_failure_record(self):
        result, stdout, stderr = self._failed_run(
            'import sys,time\nprint("partial", flush=True)\nprint("partial error", file=sys.stderr, flush=True)\ntime.sleep(5)\n',
            timeout=1)
        self.assertTrue(result['receiver_run']['timed_out'])
        self.assertIsNone(result['receiver_run']['exit_code'])
        self.assertIn('partial', stdout)
        self.assertIn('partial error', stderr)

    def test_comparison_failure_still_saves_structured_record(self):
        result, _, _ = self._failed_run(
            'import pathlib,sys\nout=pathlib.Path(sys.argv[sys.argv.index("--out-file")+1])\n'
            'out.write_bytes((out.parent/"input.ts").read_bytes()[:188])\n')
        self.assertEqual(result['receiver_run']['exit_code'], 0)
        self.assertIn('Insufficient recovery', result['reason'])

    def test_empty_truncated_corrupt_and_unaligned_rejected(self):
        for decoded in (b'', self.original[:-1],
                        self.original[:200] + b'bad' + self.original[203:],
                        self.original[1:189]):
            with self.subTest(length=len(decoded)), self.assertRaises(ValueError):
                check.verify_packets(self.original, decoded)


if __name__ == '__main__':
    unittest.main()
