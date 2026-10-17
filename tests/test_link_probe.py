#!/usr/bin/env python3
"""Verify interface isolation and MTU restoration without network changes."""
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

scripts = Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location('link_probe', scripts / 'test-link.py')
link_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(link_probe)


class FakeCollector:
    def __init__(self, failure=None):
        self.commands, self.records = [], []
        self.mtu = 1500
        self.failure = failure

    def command(self, name, argv, timeout):
        self.commands.append(argv)
        assert timeout <= 4
        if argv[:3] == ['ip', 'link', 'set']:
            self.mtu = int(argv[-1])
        if argv[0] == 'ping' and self.mtu == 1400:
            if self.failure == 'interrupt':
                raise KeyboardInterrupt()
            if self.failure == 'error':
                raise OSError('test failure during ping')
        self.records.append({'result': 0})

    def snapshot(self, *args, **kwargs):
        pass


class LinkProbeTests(unittest.TestCase):
    def test_preflight_errors_always_make_an_archive(self):
        # Exercise the real main(), preflight(), archive and manifest code.
        # Only external commands and host-wide information reads are replaced.
        for iw_result, nm_state, malformed_link, expected_probe in (
            (244, '30 (disconnected)\n', False, False),
            (244, '100 (connected)\n', False, True),
            ('timeout', '100 (connected)\n', False, True),
            (244, '100 (connected)\n', True, False),
        ):
            with self.subTest(iw_result=iw_result, nm_state=nm_state, malformed=malformed_link):
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    (root / 'PORT_VERSION').write_text('5\n')
                    calls = []
                    responses = {
                        'preflight/iw-link.txt': (iw_result, 'Synthetic iw query failure (-12)\n'),
                        'preflight/ip-link.txt': (0, 'not-json' if malformed_link else json.dumps([
                            {'ifname': 'wlan0', 'mtu': 1500, 'flags': ['UP', 'LOWER_UP'], 'operstate': 'UP'}])),
                        'preflight/ip-address.txt': (0, json.dumps([
                            {'ifname': 'wlan0', 'addr_info': [
                                {'scope': 'global', 'local': '172.16.80.154', 'family': 'inet'}]}])),
                        'preflight/nm-state.txt': (0, nm_state),
                        'preflight/ip-route.txt': (0, json.dumps([
                            {'dev': 'wlan0', 'gateway': '172.16.64.1'}])),
                    }

                    def fake_command(collector, name, argv, timeout=5):
                        calls.append(argv)
                        result, text = responses.get(name, (0, ''))
                        collector.text(name, text)
                        collector.note('command', file=name, argv=argv, result=result)

                    collector_class = link_probe.Collector
                    with mock.patch.object(link_probe.os, 'geteuid', return_value=0), \
                         mock.patch.object(link_probe.platform, 'machine', return_value='aarch64'), \
                         mock.patch.object(Path, 'is_symlink', return_value=True), \
                         mock.patch.object(link_probe.subprocess, 'Popen'), \
                         mock.patch.object(link_probe, 'stop_reader'), \
                         mock.patch.object(link_probe, 'run_probe') as run_probe, \
                         mock.patch.object(collector_class, 'command', fake_command), \
                         mock.patch.object(collector_class, 'snapshot'), \
                         mock.patch.object(collector_class, 'stacks'), \
                         mock.patch.object(collector_class, 'static', side_effect=OSError('synthetic collection failure')), \
                         mock.patch.object(collector_class, 'debug_state'), \
                         mock.patch.object(collector_class, 'existing_logs'):
                        self.assertEqual(link_probe.main(root), 0)
                    self.assertEqual(run_probe.called, expected_probe)
                    archives = list((root / 'dist').glob('cm311-linktest-*.tar.gz'))
                    self.assertEqual(len(archives), 1)
                    with tarfile.open(archives[0]) as archive:
                        prefix = archive.getnames()[0]
                        summary = json.load(archive.extractfile(prefix + '/SUMMARY.json'))
                        self.assertEqual(summary['preflight_results']['iw-link'], iw_result)
                        error = archive.extractfile(prefix + '/preflight/iw-link.txt').read()
                        self.assertIn(b'Synthetic iw query failure', error)
                        manifest = json.load(archive.extractfile(prefix + '/collection-manifest.json'))
                        self.assertTrue(any(row['kind'] == 'collection-error' for row in manifest))
                        if expected_probe:
                            self.assertEqual(summary['connection_evidence'], 'networkmanager-and-carrier')
                        else:
                            self.assertEqual(summary['action'], 'snapshot-link-unconfirmed')
                            self.assertIn('skip_reason', summary)
                    self.assertFalse(any(argv[0] in ('ping', 'insmod', 'modprobe', 'rmmod', 'reboot')
                                         or argv[:3] == ['ip', 'link', 'set'] for argv in calls))

    def test_uses_wlan_gateway_and_validates_addresses(self):
        routes = [{'dev': 'eth0', 'gateway': '192.0.2.1'},
                  {'dev': 'wlan0', 'gateway': 'bad-value'},
                  {'dev': 'wlan0', 'gateway': '127.0.0.1'},
                  {'dev': 'wlan0', 'gateway': '172.16.64.1'}]
        self.assertEqual(link_probe.gateway_targets(routes, 'wlan0'),
                         [('gateway', '172.16.64.1'), ('internet', '1.1.1.1')])
        self.assertEqual(link_probe.gateway_targets([], 'wlan0'), [('internet', '1.1.1.1')])
        self.assertEqual(link_probe.gateway_targets([
            {'dst': 'default', 'gateway': '172.16.64.1'}], 'wlan0'),
            [('gateway', '172.16.64.1'), ('internet', '1.1.1.1')])

    def test_cp2_failure_overrides_stale_networkmanager_connected(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = link_probe.Collector(Path(folder), Path(folder) / 'capture')
            responses = {
                'preflight/iw-link.txt': (244, 'command failed: Cannot allocate memory (-12)\n'),
                'preflight/ip-link.txt': (0, json.dumps([
                    {'ifname': 'wlan0', 'mtu': 1500, 'flags': ['UP', 'LOWER_UP']} ])),
                'preflight/ip-address.txt': (0, json.dumps([
                    {'ifname': 'wlan0', 'addr_info': [{'scope': 'global', 'local': '172.16.80.154'}]}])),
                'preflight/nm-state.txt': (0, '100 (connected)\n'),
                'preflight/ip-route.txt': (0, '[{"gateway":"172.16.64.1"}]'),
            }

            def query(name, argv, timeout):
                result, output = responses[name]
                collector.text(name, output)
                collector.note('command', result=result)

            collector.command = query
            log = 'WCN: then marlin download finished and run ok\nsprdwl:cp2 reset begin..........\n'
            collector.text('kernel/dmesg-before.txt', log)
            summary = {'driver_bound': True}
            self.assertFalse(link_probe.preflight(collector, summary))
            self.assertTrue(summary['cp2_failure_observed'])
            self.assertIn('CP2 reset', summary['skip_reason'])
            collector.text('kernel/dmesg-before.txt', log + 'sprdwl_init_fw,open success\n')
            self.assertTrue(link_probe.preflight(collector, {'driver_bound': True}))

    def test_all_pings_use_wlan_and_no_fragmentation(self):
        collector, summary = FakeCollector(), {}
        targets = [('gateway', '172.16.64.1'), ('internet', '1.1.1.1')]
        link_probe.probe(collector, targets, 1500, summary)
        pings = [argv for argv in collector.commands if argv[0] == 'ping']
        self.assertEqual(len(pings), len(link_probe.SIZES) * 2 + 4)
        for argv in pings:
            self.assertEqual(argv[argv.index('-I') + 1], 'wlan0')
            self.assertEqual(argv[argv.index('-M') + 1], 'do')
            self.assertEqual(argv[argv.index('-w') + 1], '3')
        self.assertEqual(collector.mtu, 1500)
        self.assertEqual(summary['restore_mtu_result'], 0)
        self.assertEqual(collector.commands[-1], ['ip', 'link', 'set', 'dev', 'wlan0', 'mtu', '1500'])
        self.assertFalse(any(argv[0] in ('insmod', 'modprobe', 'rmmod', 'reboot')
                             for argv in collector.commands))

    def test_restores_mtu_on_error_and_ctrl_c(self):
        for failure, exception in [('error', OSError), ('interrupt', KeyboardInterrupt)]:
            collector, summary = FakeCollector(failure), {}
            with self.assertRaises(exception):
                link_probe.probe(collector, [('internet', '1.1.1.1')], 1500, summary)
            self.assertEqual(collector.mtu, 1500)
            self.assertEqual(summary['restore_mtu_result'], 0)

    def test_soak_bounds_traffic_and_cleans_up_own_processes(self):
        for outcome in ('completed', 'deadline', 'interrupt', 'second-spawn-error'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                collector = link_probe.Collector(root, root / 'report')
                clock = [0.0]
                children = []
                commands = []
                stopped = []

                class Child:
                    status = None

                    def poll(self):
                        if outcome == 'completed' and clock[0] >= 30:
                            self.status = 0
                        return self.status

                def spawn(argv, **kwargs):
                    commands.append(argv)
                    if outcome == 'second-spawn-error' and children:
                        raise OSError('synthetic second child launch failure')
                    kwargs['stdout'].write(b'raw ping output\n')
                    child = Child()
                    children.append(child)
                    return child

                def tick(seconds):
                    if outcome == 'interrupt':
                        raise KeyboardInterrupt()
                    clock[0] += seconds

                def stop(child):
                    stopped.append(child)
                    if child.poll() is None:
                        child.status = -15

                summary = {}
                with mock.patch.object(link_probe.subprocess, 'Popen', side_effect=spawn), \
                     mock.patch.object(link_probe.time, 'monotonic', side_effect=lambda: clock[0]), \
                     mock.patch.object(link_probe.time, 'sleep', side_effect=tick), \
                     mock.patch.object(link_probe, 'stop_reader', side_effect=stop), \
                     mock.patch.object(link_probe, 'state'), \
                     mock.patch.object(collector, 'command'):
                    args = (collector, [('gateway', '172.16.64.1'), ('internet', '1.1.1.1')],
                            1400, 30, summary)
                    if outcome in ('interrupt', 'second-spawn-error'):
                        expected = KeyboardInterrupt if outcome == 'interrupt' else OSError
                        with self.assertRaises(expected):
                            link_probe.soak(*args)
                    else:
                        link_probe.soak(*args)
                self.assertEqual(stopped, children)
                self.assertLessEqual(clock[0], 35)
                for argv in commands:
                    self.assertEqual(argv[0], 'ping')
                    for option, value in (('-I', 'wlan0'), ('-M', 'do'), ('-i', '1'),
                                          ('-w', '30'), ('-s', '1372')):
                        self.assertEqual(argv[argv.index(option) + 1], value)
                for record in summary['soak_pings']:
                    self.assertIsNotNone(record['exit_status'])
                    self.assertEqual((collector.output / record['output']).read_bytes(), b'raw ping output\n')
                self.assertEqual(summary.get('soak_deadline_reached', False), outcome == 'deadline')

    def test_soak_restores_wifi_logging_on_interrupt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wifi = root / 'wifi-log'
            control = root / 'sdio-log'
            wifi.write_text('4\n')
            control.write_text('log_level=1\n')
            collector = link_probe.Collector(root, root / 'report')

            def interrupted(*args):
                self.assertEqual(wifi.read_text(), '3\n')
                self.assertEqual(control.read_text(), 'log_level=1\n')
                raise KeyboardInterrupt()

            with mock.patch.object(link_probe, 'Path', return_value=wifi), \
                 mock.patch.object(link_probe.shutil, 'which', return_value=None), \
                 mock.patch.object(link_probe, 'soak', side_effect=interrupted), \
                 mock.patch.object(link_probe, 'probe') as short_test:
                with self.assertRaises(KeyboardInterrupt):
                    link_probe.run_probe(collector, collector.output, control,
                                         {'soak_seconds': 600, 'targets': [], 'original_mtu': 1400})
            self.assertFalse(short_test.called)
            self.assertEqual(wifi.read_text(), '4\n')
            self.assertEqual(control.read_text(), 'log_level=1\n')

    def test_soak_option_limits(self):
        self.assertEqual(link_probe.parse_options([]).soak_seconds, 0)
        for seconds in (30, 600, 3600):
            self.assertEqual(link_probe.parse_options(['--soak-seconds', str(seconds)]).soak_seconds, seconds)
        for seconds in (-1, 1, 3601):
            with self.assertRaises(SystemExit):
                link_probe.parse_options(['--soak-seconds', str(seconds)])

    def test_addba_comparison_restores_gate_and_keeps_phases_separate(self):
        for failure in (None, KeyboardInterrupt, OSError):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                control = root / 'tx_addba_retry_ms'
                control.write_text('3000\n')  # Restore the actual original, not a hard-coded default.
                collector = link_probe.Collector(root, root / 'report')
                summary = {'targets': [('gateway', '172.16.64.1')],
                           'original_mtu': 1400, 'addba_compare_seconds': 180}
                visited = []

                def fake_soak(actual_collector, targets, mtu, seconds, result, phase):
                    self.assertIs(actual_collector, collector)
                    self.assertEqual((mtu, seconds), (1400, 180))
                    self.assertEqual(int(control.read_text()), result['tx_addba_retry_ms'])
                    visited.append(phase)
                    collector.text(f'ping/{phase}/gateway.txt', 'phase output\n')
                    if failure and phase == 'addba-paused':
                        raise failure()

                with mock.patch.object(link_probe, 'soak', side_effect=fake_soak), \
                     mock.patch.object(link_probe.time, 'sleep'):
                    if failure:
                        with self.assertRaises(failure):
                            link_probe.compare_addba(collector, summary, control)
                    else:
                        link_probe.compare_addba(collector, summary, control)
                self.assertEqual(control.read_text(), '3000\n')
                self.assertEqual(summary['addba_restore_result'], 'restored')
                expected = ['addba-legacy', 'addba-paused'] + ([] if failure else ['addba-backoff'])
                self.assertEqual(visited, expected)
                self.assertEqual(len(list(collector.output.glob('ping/*/gateway.txt'))), len(expected))

    def test_addba_mode_requires_readable_original_and_bounded_duration(self):
        for seconds in (30, 180, 1200):
            self.assertEqual(link_probe.parse_options(
                ['--addba-compare-seconds', str(seconds)]).addba_compare_seconds, seconds)
        for argv in (['--addba-compare-seconds', '1201'],
                     ['--addba-compare-seconds', '180', '--soak-seconds', '600']):
            with self.assertRaises(SystemExit):
                link_probe.parse_options(argv)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            control = root / 'gate'
            control.write_text('invalid\n')
            collector = link_probe.Collector(root, root / 'report')
            with self.assertRaises(ValueError):
                link_probe.compare_addba(collector, {}, control)
            self.assertEqual(control.read_text(), 'invalid\n')


if __name__ == '__main__':
    unittest.main()
