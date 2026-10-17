#!/usr/bin/env python3
"""Compare ICMP sizes over the existing wlan0 connection; package one report.

Tests the WLAN gateway and 1.1.1.1, then temporarily tests MTU 1400 and restores
the original MTU. Does not reconnect, reload modules or change saved profiles.
With --soak-seconds, retains the current MTU and observes sustained ICMP instead.
With --addba-compare-seconds, compares three live TX ADDBA retry settings on v8.
"""
import argparse
from contextlib import ExitStack
import datetime
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

from diagnose import Collector, limit_live_log, stop_reader

SIZES = (56, 1200, 1372, 1440, 1452, 1472)


def gateway_targets(routes, interface):
    targets = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        # `ip ... route show ... dev wlan0` can omit the already-filtered dev.
        if route.get('dev', interface) != interface or not route.get('gateway'):
            continue
        try:
            gateway = ipaddress.IPv4Address(route['gateway'])
        except ipaddress.AddressValueError:
            continue
        if gateway.is_multicast or gateway.is_unspecified or gateway.is_loopback:
            continue
        targets.append(('gateway', str(gateway)))
        break
    targets.append(('internet', '1.1.1.1'))
    return targets


def command(collector, name, argv, timeout=4):
    collector.command(name, argv, timeout=timeout)
    return collector.records[-1]['result']


def preflight(collector, summary):
    """Save each query before interpreting it; query failures still produce a bundle."""
    results = {}
    queries = (
        ('iw-link', ['iw', 'dev', 'wlan0', 'link']),
        ('ip-link', ['ip', '-j', 'link', 'show', 'dev', 'wlan0']),
        ('ip-address', ['ip', '-j', '-4', 'address', 'show', 'dev', 'wlan0']),
        ('nm-state', ['nmcli', '-g', 'GENERAL.STATE', 'device', 'show', 'wlan0']),
        ('ip-route', ['ip', '-j', '-4', 'route', 'show', 'default', 'dev', 'wlan0']),
    )
    for name, argv in queries:
        results[name] = command(collector, f'preflight/{name}.txt', argv, timeout=3)
    summary['preflight_results'] = results

    def text(name):
        return (collector.output / f'preflight/{name}.txt').read_text(errors='replace')

    def rows(name):
        if results[name] != 0:
            return []
        try:
            value = json.loads(text(name))
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                raise ValueError('Expected a JSON array of objects')
            return value
        except (ValueError, TypeError) as exc:
            collector.note('invalid-query-json', query=name, error=repr(exc))
            return []

    links = [row for row in rows('ip-link') if row.get('ifname') == 'wlan0']
    link = links[0] if links else {}
    mtu = link.get('mtu')
    valid_mtu = type(mtu) is int and 68 <= mtu <= 65535
    flags = link.get('flags', [])
    up = isinstance(flags, list) and 'UP' in flags
    carrier = isinstance(flags, list) and ('LOWER_UP' in flags or link.get('operstate') == 'UP')
    has_ipv4 = False
    for row in rows('ip-address'):
        if row.get('ifname') != 'wlan0':
            continue
        for address in row.get('addr_info', []):
            if not isinstance(address, dict) or address.get('scope') != 'global':
                continue
            try:
                value = ipaddress.IPv4Address(address.get('local', ''))
                has_ipv4 |= not (value.is_unspecified or value.is_multicast or value.is_loopback)
            except ipaddress.AddressValueError:
                continue
    iw_connected = results['iw-link'] == 0 and text('iw-link').startswith('Connected to ')
    nm_connected = results['nm-state'] == 0 and re.match(r'^100(?:\s|$)', text('nm-state')) is not None
    evidence = ('iw-link' if iw_connected else
                'networkmanager-and-carrier' if nm_connected and carrier else 'unconfirmed')
    summary.update(original_mtu=mtu if valid_mtu else None,
                   targets=gateway_targets(rows('ip-route'), 'wlan0'),
                   connection_evidence=evidence, has_global_ipv4=has_ipv4)
    reasons = []
    kernel_log = collector.output / 'kernel/dmesg-before.txt'
    if kernel_log.is_file():
        kernel = kernel_log.read_text(errors='replace')
        failed = kernel.rfind('sprdwl:cp2 reset begin')
        started = max(kernel.rfind('then marlin download finished and run ok'),
                      kernel.rfind('sprdwl_init_fw,open success'))
        if failed >= 0 and failed > started:
            reasons.append('CP2 reset was observed without a later successful startup')
            summary['cp2_failure_observed'] = True
    if not summary['driver_bound']:
        reasons.append('Wi-Fi platform driver is not bound')
    if not valid_mtu or not up:
        reasons.append('Cannot confirm wlan0 is UP with a readable original MTU')
    if not has_ipv4:
        reasons.append('Cannot confirm a global IPv4 address on wlan0')
    if evidence == 'unconfirmed':
        reasons.append('Neither iw nor NetworkManager/carrier confirms a connection')
    if reasons:
        summary['skip_reason'] = '; '.join(reasons)
        return False
    return True


def state(collector, phase):
    for name, argv in (
        ('link', ['iw', 'dev', 'wlan0', 'link']),
        ('station', ['iw', 'dev', 'wlan0', 'station', 'dump']),
        ('power-save', ['iw', 'dev', 'wlan0', 'get', 'power_save']),
        ('net-stats', ['ip', '-s', 'link', 'show', 'dev', 'wlan0']),
        ('ipv4', ['ip', '-4', 'address', 'show', 'dev', 'wlan0']),
        ('route', ['ip', '-4', 'route', 'show', 'table', 'all']),
        ('route-to-target', ['ip', '-4', 'route', 'get', '1.1.1.1', 'oif', 'wlan0']),
        ('networkmanager', ['nmcli', '-f', 'GENERAL,IP4,IP6', 'device', 'show', 'wlan0']),
    ):
        collector.command(f'link/{phase}/{name}.txt', argv, timeout=3)
    collector.snapshot(phase, stacks=False)


def sweep(collector, phase, targets, sizes):
    results = []
    for label, target in targets:
        for size in sizes:
            print(f'{phase}: {label} {target}, ICMP payload {size}, IPv4 size {size + 28}', flush=True)
            name = f'ping/{phase}/{label}-{size}.txt'
            result = command(collector, name,
                             ['ping', '-n', '-4', '-I', 'wlan0', '-c', '2', '-i', '0.2',
                              '-W', '1', '-w', '3', '-M', 'do', '-s', str(size), target])
            results.append({'target': target, 'label': label, 'icmp_payload': size,
                            'ipv4_size': size + 28, 'exit_status': result, 'output': name})
    return results


def probe(collector, targets, original_mtu, summary):
    attempted_change = False
    try:
        state(collector, 'baseline')
        summary['baseline_pings'] = sweep(collector, 'baseline', targets, SIZES)
        if original_mtu > 1400:
            # Mark before invoking ip so Ctrl-C during the command still restores.
            attempted_change = True
            result = command(collector, 'link/set-mtu-1400.txt',
                             ['ip', 'link', 'set', 'dev', 'wlan0', 'mtu', '1400'])
            summary['set_mtu_result'] = result
            if result == 0:
                state(collector, 'mtu-1400')
                summary['mtu_1400_pings'] = sweep(collector, 'mtu-1400', targets, (56, 1372))
    finally:
        if attempted_change:
            summary['restore_mtu_result'] = command(
                collector, 'link/restore-mtu.txt',
                ['ip', 'link', 'set', 'dev', 'wlan0', 'mtu', str(original_mtu)])
            print(f'Restore MTU {original_mtu}: {summary["restore_mtu_result"]}', flush=True)


def soak(collector, targets, mtu, seconds, summary, phase='soak'):
    """Observe existing Wi-Fi for a bounded interval; terminate only our pings."""
    payload = min(1372, mtu - 28)
    summary['soak_pings'] = []
    processes = []
    started = time.monotonic()
    deadline = started + seconds + 5
    next_snapshot = started
    with ExitStack() as streams:
        try:
            state(collector, phase + '-start')
            for label, target in targets:
                name = f'ping/{phase}/{label}.txt'
                path = collector.output / name
                path.parent.mkdir(parents=True, exist_ok=True)
                stream = streams.enter_context(path.open('wb'))
                argv = ['ping', '-n', '-4', '-I', 'wlan0', '-i', '1', '-W', '2',
                        '-w', str(seconds), '-M', 'do', '-s', str(payload), target]
                proc = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT,
                                        start_new_session=True, preexec_fn=limit_live_log)
                record = {'target': target, 'label': label, 'icmp_payload': payload,
                          'ipv4_size': payload + 28, 'output': name}
                processes.append((proc, record, argv))
                summary['soak_pings'].append(record)
            deadline = time.monotonic() + seconds + 5
            while any(proc.poll() is None for proc, _, _ in processes):
                now = time.monotonic()
                if now >= deadline:
                    summary['soak_deadline_reached'] = True
                    break
                if now >= next_snapshot:
                    elapsed = int(now - started)
                    print(f'{phase}: {elapsed}/{seconds} seconds; wlan0 only.', flush=True)
                    prefix = f'{phase}/{elapsed:04d}/'
                    collector.command(prefix + 'state.txt',
                                      ['bash', str(collector.root / 'scripts/collect-state.sh')], timeout=3)
                    collector.command(prefix + 'link.txt', ['iw', 'dev', 'wlan0', 'link'], timeout=3)
                    collector.command(prefix + 'net-stats.txt',
                                      ['ip', '-s', 'link', 'show', 'dev', 'wlan0'], timeout=3)
                    next_snapshot = time.monotonic() + 30
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        finally:
            for proc, record, argv in processes:
                record['terminated_by_collector'] = proc.poll() is None
                stop_reader(proc)
                record['exit_status'] = proc.poll()
                collector.note('soak-ping', argv=argv, **record)
            summary['soak_elapsed_seconds'] = round(time.monotonic() - started, 1)


def compare_addba(collector, summary, control=None):
    """Change only the retry gate; restore it even after interruption or errors."""
    control = control if control is not None else Path('/sys/module/sprdwl_ng/parameters/tx_addba_retry_ms')
    original = control.read_text().strip()
    if not original.isdecimal():
        raise ValueError('Cannot read original tx_addba_retry_ms; refusing to change it')
    summary['addba_original_retry_ms'] = int(original)
    summary['addba_phases'] = []
    try:
        for name, interval in (('addba-legacy', 3000), ('addba-paused', 0), ('addba-backoff', 30000)):
            phase = {'phase': name, 'tx_addba_retry_ms': interval}
            summary['addba_phases'].append(phase)
            control.write_text(str(interval) + '\n')
            print(f'{name}: tx_addba_retry_ms={interval}', flush=True)
            # Do not abort an in-flight command; allow its normal completion.
            time.sleep(1)
            soak(collector, summary['targets'], summary['original_mtu'],
                 summary['addba_compare_seconds'], phase, phase=name)
    finally:
        try:
            control.write_text(original + '\n')
            summary['addba_restore_result'] = 'restored'
        except OSError as exc:
            summary['addba_restore_result'] = repr(exc)
            collector.note('addba-restore-error', error=repr(exc))
            print(f'ADDBA restore failed. Run: echo {original} > {control}', flush=True)


def parse_options(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--soak-seconds', type=int, default=0,
                        help='Instead of the size sweep, observe existing wlan0 for 30–3600 seconds.')
    mode.add_argument('--addba-compare-seconds', type=int, default=0,
                      help='v8: compare TX ADDBA retries 3000/0/30000 ms, 30–1200 seconds per phase.')
    options = parser.parse_args(argv)
    if options.soak_seconds != 0 and not 30 <= options.soak_seconds <= 3600:
        parser.error('--soak-seconds must be 0 or between 30 and 3600')
    if options.addba_compare_seconds != 0 and not 30 <= options.addba_compare_seconds <= 1200:
        parser.error('--addba-compare-seconds must be 0 or between 30 and 1200')
    return options


def main(root=None, argv=()):
    options = parse_options(argv)
    if os.geteuid() != 0 or platform.machine() != 'aarch64':
        raise SystemExit('Run as root on the ARM64 box with wlan0 already connected.')
    os.umask(0o077)
    os.environ['LC_ALL'] = 'C'
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    dist = root / 'dist'
    dist.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output = Path(tempfile.mkdtemp(prefix=f'cm311-linktest-{timestamp}-', dir=dist))
    os.chmod(output, 0o700)
    (output / 'kernel').mkdir()
    collector = Collector(root, output)
    summary = {'action': ('existing-wlan-addba-comparison' if options.addba_compare_seconds else
                          'existing-wlan-link-soak-test' if options.soak_seconds
                          else 'existing-wlan-link-size-test'), 'kernel': platform.release(),
               'link_test_version': 4, 'soak_seconds': options.soak_seconds,
               'addba_compare_seconds': options.addba_compare_seconds,
               'driver_bound': Path('/sys/bus/platform/drivers/unisoc_wifi/unisoc_wifi').is_symlink()}
    print(f'Link test scripts v4; diagnostic directory: {output}', flush=True)
    if options.addba_compare_seconds:
        print(f'Three ADDBA phases, {options.addba_compare_seconds} seconds each; existing wlan0 connection.', flush=True)
    elif options.soak_seconds:
        print(f'Using wlan0 only for {options.soak_seconds} seconds; MTU and connection are retained.', flush=True)
    else:
        print('Using wlan0 only. Testing sizes, then temporary MTU 1400; original MTU will be restored.', flush=True)
    reader = None
    control = Path('/sys/kernel/debug/sdiohal_debug/at_cmd')
    try:
        summary['port_version'] = (root / 'PORT_VERSION').read_text().strip()
        collector.command('kernel/dmesg-before.txt', ['dmesg'])
        with (output / 'kernel/dmesg-live.txt').open('wb') as stream:
            reader = subprocess.Popen(['dmesg', '--follow'], stdout=stream,
                                      stderr=subprocess.STDOUT, start_new_session=True,
                                      preexec_fn=limit_live_log)
        if not preflight(collector, summary):
            summary['action'] = 'snapshot-link-unconfirmed'
            print('Collecting existing state only: ' + summary['skip_reason'], flush=True)
        else:
            if summary['preflight_results']['iw-link'] != 0:
                print('iw link failed; using NetworkManager, carrier and IPv4 evidence. Raw error is saved.', flush=True)
            run_probe(collector, output, control, summary)
    except KeyboardInterrupt:
        summary['interrupted'] = True
    except Exception as exc:
        summary['error'] = repr(exc)
        print(f'Test error: {exc}; packaging collected data.', flush=True)
    finally:
        for label, collect in (
            ('after-state', lambda: state(collector, 'after')),
            ('driver-stacks', lambda: collector.stacks('snapshots/after/')),
            ('static', lambda: collector.static(platform.release())),
            ('debug', lambda: collector.debug_state('after')),
            ('existing-logs', lambda: collector.existing_logs(platform.release())),
            ('memory', lambda: collector.command('memory-and-netlink.txt',
                ['cat', '/proc/meminfo', '/proc/net/netlink'])),
            ('networkmanager-journal', lambda: collector.command('networkmanager-journal.txt',
                ['journalctl', '-b', '-u', 'NetworkManager', '-u', 'wpa_supplicant',
                 '--no-pager', '-n', '500'], timeout=5)),
        ):
            try:
                collect()
            except (Exception, KeyboardInterrupt) as exc:
                collector.note('collection-error', step=label, error=repr(exc))
        stop_reader(reader)
        collector.command('kernel/dmesg-after.txt', ['dmesg'])
        summary['elapsed_seconds'] = round(time.monotonic() - collector.started, 1)
        archive = collector.package(summary)
        print(f'\nSend this ONE file:\n{archive}', flush=True)
        if summary.get('restore_mtu_result', 0) != 0:
            print(f'MTU restore failed. Run: ip link set dev wlan0 mtu {summary["original_mtu"]}', flush=True)
    return 0


def run_probe(collector, output, control, summary):
    capture = None
    trace_attempted = False
    wifi_control = Path('/sys/module/sprdwl_ng/parameters/log_level')
    wifi_original = None
    long_test = bool(summary.get('soak_seconds') or summary.get('addba_compare_seconds'))
    try:
        if shutil.which('tcpdump'):
            with (output / 'capture-status.txt').open('wb') as stream:
                capture = subprocess.Popen(
                    ['tcpdump', '-i', 'wlan0', '-n', '-s', '128', '-U',
                     '-w', str(output / 'icmp-headers.pcap'), 'icmp'],
                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                    preexec_fn=limit_live_log)
        else:
            collector.note('tcpdump-unavailable', detail='Other tests and kernel trace still collected.')
        if control.exists():
            trace_attempted = True
            control.write_text('log_level=1\n' if long_test else 'log_level=11\n')
        if long_test:
            # Avoid per-packet hex dumps overwhelming the log during a long run.
            if wifi_control.exists():
                wifi_original = wifi_control.read_text()
                wifi_control.write_text('3\n')
            if summary.get('addba_compare_seconds'):
                compare_addba(collector, summary)
            else:
                soak(collector, summary['targets'], summary['original_mtu'],
                     summary['soak_seconds'], summary)
        else:
            probe(collector, summary['targets'], summary['original_mtu'], summary)
    finally:
        if wifi_original is not None:
            try:
                wifi_control.write_text(wifi_original)
            except OSError as exc:
                collector.note('wifi-log-restore-error', error=repr(exc))
        if trace_attempted:
            try:
                control.write_text('log_level=1\n')
            except OSError as exc:
                collector.note('trace-reset-error', error=repr(exc))
        stop_reader(capture)
        if capture is not None:
            summary['tcpdump_exit_status'] = capture.poll()


if __name__ == '__main__':
    raise SystemExit(main(argv=sys.argv[1:]))
