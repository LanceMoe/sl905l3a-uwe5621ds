#!/usr/bin/env python3
"""Safety and failure regressions for the collector, without loading drivers."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    'diagnose', Path(__file__).resolve().parent.parent / 'scripts/diagnose.py')
diagnose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnose)


class DiagnosticsTests(unittest.TestCase):
    def test_failed_load_returns_failure_after_packaging(self):
        for result, expected in ((1, 1), (0, 0), ('still-running', 1)):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'scripts').mkdir()
                (root / 'PORT_VERSION').write_text('7\n')

                def fake_command(collector, name, argv, timeout=5):
                    collector.text(name, 'synthetic diagnostic output\n')
                    collector.note('command', file=name, result=0)

                cls = diagnose.Collector
                worker = mock.MagicMock()
                worker.pid = 123456
                worker.poll.return_value = None if result == 'still-running' else result
                with mock.patch.object(diagnose, '__file__', str(root / 'scripts/diagnose.py')), \
                     mock.patch.object(sys, 'argv', ['diagnose.py', '--run']), \
                     mock.patch.object(diagnose.os, 'geteuid', return_value=0), \
                     mock.patch.object(diagnose.platform, 'machine', return_value='aarch64'), \
                     mock.patch.object(diagnose, 'choose_action', return_value='load-once'), \
                     mock.patch.object(diagnose.subprocess, 'Popen', return_value=worker), \
                     mock.patch.object(diagnose, 'observe', return_value=result), \
                     mock.patch.object(diagnose, 'stop_reader'), \
                     mock.patch.object(cls, 'command', fake_command), \
                     mock.patch.object(cls, 'static'), mock.patch.object(cls, 'snapshot'), \
                     mock.patch.object(cls, 'debug_state'), mock.patch.object(cls, 'existing_logs'), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(diagnose.main(), expected)
                archives = list((root / 'dist').glob('*.tar.gz'))
                self.assertEqual(len(archives), 1)
                with tarfile.open(archives[0]) as archive:
                    prefix = archive.getnames()[0]
                    summary = json.load(archive.extractfile(prefix + '/SUMMARY.json'))
                    self.assertEqual(summary['load_result'], result)
                    self.assertEqual(summary['loader_still_running'], result == 'still-running')

    def test_transport_and_blocked_stacks_survive_worker_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            proc = root / 'proc'
            entries = [(pid, 'kworker/0:1', 'S') for pid in range(1, 91)]
            entries += [(1001, 'ksdioirqd/mmc0', 'S'),
                        (1002, 'sdiohal_rx_threa', 'S'),
                        (1003, 'insmod', 'D'), (1004, 'irq/47-mmc0', 'S')]
            for pid, comm, state in entries:
                task = proc / str(pid) / 'task' / str(pid)
                task.mkdir(parents=True)
                (task / 'comm').write_text(comm + '\n')
                (task / 'status').write_text(f'State:\t{state} (test)\n')
            collector = diagnose.Collector(root, root / 'output')
            captured = []
            collector.command = lambda name, argv, timeout: captured.append(name)
            collector.stacks('sample/', proc_root=proc)
            self.assertEqual(len(captured), 80)
            for pid in (1001, 1002, 1003, 1004):
                self.assertTrue(any(f'/{pid}-{pid}-' in path for path in captured))
            self.assertIn('/1003-1003-insmod.txt', captured[0])
            self.assertEqual(collector.records[-1]['kind'], 'stack-limit')

    def test_load_policy(self):
        for mode in ('auto', 'run', 'snapshot'):
            self.assertTrue(diagnose.choose_action(mode, ['sprdwl_ng'], 0).startswith('snapshot-'))
            self.assertTrue(diagnose.choose_action(mode, [], 1 << 7).startswith('snapshot-'))
        self.assertEqual(diagnose.choose_action('snapshot', [], 0), 'snapshot-requested')
        # The expected out-of-tree-module taint alone does not mean an Oops.
        self.assertEqual(diagnose.choose_action('run', [], 1 << 12), 'load-once')

    def test_timeout_missing_file_and_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'capture'
            output.mkdir()
            collector = diagnose.Collector(root, output)
            start = time.monotonic()
            collector.command('blocked.txt', [sys.executable, '-c',
                              'import time; print("started", flush=True); time.sleep(30)'], timeout=0.1)
            self.assertLess(time.monotonic() - start, 2)
            self.assertEqual(collector.records[-1]['result'], 'timeout')
            collector.copy(root / 'absent.log')
            original = root / 'large.log'
            original.write_bytes(b'prefixTAIL')
            collector.copy(original, 'tail.log', limit=4)
            self.assertEqual((output / 'tail.log').read_bytes(), b'TAIL')
            self.assertTrue(collector.records[-1]['tail_only'])
            archive = collector.package({'result': 'incomplete-command-captured'})
            with tarfile.open(archive) as bundle:
                self.assertEqual(bundle.extractfile('capture/tail.log').read(), b'TAIL')
                summary = json.load(bundle.extractfile('capture/SUMMARY.json'))
                self.assertEqual(summary['result'], 'incomplete-command-captured')
                records = json.load(bundle.extractfile('capture/collection-manifest.json'))
                self.assertTrue(any(r['kind'] == 'missing' for r in records))
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)

    def test_observation_does_not_kill_loader(self):
        class FakeCollector:
            def __init__(self):
                self.samples = []

            def snapshot(self, label, stacks):
                self.samples.append((label, stacks))

        worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                  start_new_session=True)
        try:
            collector = FakeCollector()
            result = diagnose.observe(collector, worker, 0.1)
            self.assertEqual(result, 'still-running')
            self.assertIsNone(worker.poll())
            self.assertTrue(collector.samples)
        finally:
            # This is a test-owned sleep process, never a real module loader.
            diagnose.stop_reader(worker)


if __name__ == '__main__':
    unittest.main()
