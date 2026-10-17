#!/usr/bin/env python3
"""Collect one CM311 diagnostic bundle, optionally around a single driver load.

No module unload, reprobe, GPIO/register writes, scans or network reconfiguration.
Only load-test.sh changes driver state, when a fresh load is requested/possible.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import tarfile
import tempfile
import time

DRIVERS = ('uwe5621_bsp_sdio', 'uwe5622_bsp_sdio', 'sprdwl_ng', 'sprdbt_tty')
MAX_LOG = 4 * 1024 * 1024
MAX_LIVE = 32 * 1024 * 1024


def stop_reader(proc):
    """Bounded termination for our own diagnostic helpers, never the loader."""
    if proc is None or proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            proc.wait(timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            pass


def limit_live_log():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LIVE, MAX_LIVE))


def choose_action(mode, loaded, taint):
    if mode == 'snapshot':
        return 'snapshot-requested'
    if taint & (1 << 7):  # TAINT_DIE: a prior kernel Oops.
        return 'snapshot-kernel-oops'
    if loaded:
        return 'snapshot-modules-already-loaded'
    return 'load-once'


class Collector:
    def __init__(self, root, output):
        self.root, self.output = Path(root), Path(output)
        self.records = []
        self.started = time.monotonic()

    def note(self, kind, **fields):
        self.records.append({'elapsed_s': round(time.monotonic() - self.started, 3),
                             'kind': kind, **fields})

    def text(self, name, content):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def command(self, name, argv, timeout=5):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        proc = None
        result = None
        try:
            with path.open('wb') as stream:
                proc = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                try:
                    result = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    stop_reader(proc)
                    result = 'timeout'
        except OSError as exc:
            self.text(name, str(exc) + '\n')
            result = str(exc)
        self.note('command', file=name, argv=argv, result=result)

    def copy(self, source, destination=None, limit=MAX_LOG):
        source = Path(source)
        destination = destination or 'files/' + str(source).lstrip('/')
        if not source.is_file():
            self.note('missing', source=str(source))
            return
        target = self.output / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = source.stat().st_size
            with source.open('rb') as src:
                if size > limit:
                    src.seek(size - limit)
                with target.open('wb') as dst:
                    dst.write(src.read(limit))
            self.note('copy', source=str(source), file=destination, size=size,
                      tail_only=size > limit)
        except OSError as exc:
            self.note('copy-error', source=str(source), error=str(exc))

    def static(self, release):
        print('Collecting kernel, headers, device tree and firmware identity...', flush=True)
        for source in ('/etc/os-release', '/etc/armbian-release', '/boot/uEnv.txt',
                       '/boot/extlinux/extlinux.conf', '/boot/config-' + release,
                       '/proc/config.gz'):
            self.copy(source)
        for name in ('version', 'cmdline', 'uptime', 'modules', 'meminfo', 'filesystems',
                     'sys/kernel/tainted', 'sys/kernel/random/boot_id'):
            self.command('system/' + name.replace('/', '_') + '.txt', ['cat', '/proc/' + name])
        self.command('system/uname.txt', ['uname', '-a'])
        self.command('system/mounts.txt', ['findmnt'])
        self.command('system/network-links.txt', ['ip', '-details', 'link', 'show'])
        headers = Path('/lib/modules') / release / 'build'
        for name in ('include/generated/autoconf.h', 'include/generated/utsrelease.h',
                     'include/generated/compile.h', 'include/net/cfg80211.h',
                     'include/linux/pm_wakeup.h', 'Module.symvers'):
            self.copy(headers / name, 'headers/' + name)
        for name in ('PORT_VERSION', 'patches/0001-mainline-meson-uwe5621.patch'):
            self.copy(self.root / name, 'project/' + name)
        modules = self.root / 'dist' / release
        for name in ('source-stamp', 'kernel-release', 'SHA256SUMS'):
            self.copy(modules / name, 'project/' + name)
        for driver in DRIVERS:
            ko = modules / (driver + '.ko')
            self.command('modules/' + driver + '-modinfo.txt', ['modinfo', str(ko)])
        dt_root = Path('/sys/firmware/devicetree/base')
        dt_count = 0
        if dt_root.exists():
            for directory, dirs, files in os.walk(dt_root):
                dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
                for name in files:
                    if name in ('rng-seed', 'kaslr-seed'):
                        continue
                    source = Path(directory) / name
                    rel = source.relative_to(dt_root)
                    self.copy(source, 'device-tree/' + str(rel), limit=1024 * 1024)
                    dt_count += 1
                    if dt_count >= 4096:
                        break
                if dt_count >= 4096:
                    break
        self.note('device-tree', properties=dt_count)
        self.firmware()

    def firmware(self):
        lines = []
        for directory in (Path('/lib/firmware/uwe5621ds'), Path('/lib/firmware/uwe5622')):
            if not directory.exists():
                lines.append(str(directory) + ': missing')
                continue
            for path in sorted(directory.iterdir()):
                if not path.is_file():
                    continue
                digest = hashlib.sha256()
                try:
                    with path.open('rb') as stream:
                        first = stream.read(256)
                        digest.update(first)
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(block)
                    lines.append(f'{digest.hexdigest()}  {path.stat().st_size}  {path}')
                    if path.suffix == '.ini':
                        self.copy(path, 'firmware/' + directory.name + '/' + path.name)
                    elif path.suffix == '.bin':
                        self.text('firmware/' + directory.name + '/' + path.name + '.header.txt',
                                  first.hex(' ') + '\n')
                        self.command('firmware/' + directory.name + '/' + path.name + '.strings.txt',
                                     ['strings', '-n', '10', str(path)])
                except OSError as exc:
                    lines.append(f'{path}: {exc}')
        self.text('firmware/SHA256-and-size.txt', '\n'.join(lines) + '\n')

    def snapshot(self, label, stacks=True):
        prefix = 'snapshots/' + label + '/'
        self.command(prefix + 'state.txt', ['bash', str(self.root / 'scripts/collect-state.sh')])
        self.command(prefix + 'tasks.txt', ['ps', '-eLo', 'pid,tid,ppid,stat,wchan:36,comm,args'])
        for name in ('interrupts', 'softirqs', 'modules'):
            self.command(prefix + name + '.txt', ['cat', '/proc/' + name])
        for device in sorted(Path('/sys/bus/sdio/devices').glob('*')):
            for name in ('vendor', 'device', 'class', 'modalias', 'uevent',
                         'power/runtime_status', 'power/control'):
                if (device / name).exists():
                    self.command(prefix + 'sdio/' + device.name + '/' + name + '.txt',
                                 ['cat', str(device / name)], timeout=2)
        for driver in DRIVERS:
            directory = Path('/sys/module') / driver
            for name in ('initstate', 'refcnt', 'srcversion', 'version', 'taint'):
                if (directory / name).exists():
                    self.command(prefix + 'modules/' + driver + '/' + name + '.txt',
                                 ['cat', str(directory / name)], timeout=2)
            for parameter in sorted((directory / 'parameters').glob('*')):
                self.command(prefix + 'modules/' + driver + '/parameters/' + parameter.name + '.txt',
                             ['cat', str(parameter)], timeout=2)
        if stacks:
            self.stacks(prefix)

    def stacks(self, prefix, proc_root=Path('/proc')):
        deadline = time.monotonic() + 8
        count = 0
        candidates = []
        for directory in proc_root.glob('[0-9]*/task/[0-9]*'):
            try:
                comm = (directory / 'comm').read_text().strip()
                status = (directory / 'status').read_text()
            except OSError:
                continue
            blocked = any(line.startswith('State:') and '\tD' in line for line in status.splitlines())
            if blocked or comm in ('insmod', 'modprobe'):
                priority = 0
            elif comm.startswith(('sdio', 'ksdioirq', 'mdbg', 'wcn')):
                priority = 1
            elif comm.startswith('irq/'):
                priority = 2
            elif comm.startswith('kworker'):
                priority = 3
            else:
                continue
            candidates.append((priority, int(directory.parts[-3]),
                               int(directory.name), directory, comm))
        # Keep transport/IRQ stacks even when many earlier PIDs are kworkers.
        for _, _, _, directory, comm in sorted(candidates):
            if time.monotonic() >= deadline or count >= 80:
                self.note('stack-limit', label=prefix, collected=count)
                break
            pid, tid = directory.parts[-3], directory.name
            name = comm.replace('/', '_').replace(':', '_')
            self.command(prefix + f'stacks/{pid}-{tid}-{name}.txt',
                         ['cat', str(directory / 'comm'), str(directory / 'stack')], timeout=1)
            count += 1

    def debug_state(self, label):
        debug = Path('/sys/kernel/debug')
        for path in [debug / 'gpio', debug / 'clk/clk_summary',
                     debug / 'regulator/regulator_summary',
                     *debug.glob('mmc*/ios'), *debug.glob('mmc*/err_stats'),
                     *debug.glob('pinctrl/*/pinmux-pins')]:
            if path.is_file():
                self.command('debug/' + label + '/' + str(path.relative_to(debug)) + '.txt',
                             ['cat', str(path)], timeout=3)

    def existing_logs(self, release):
        for directory in (self.root / 'dist', self.root / 'dist' / release):
            for path in sorted(directory.glob('*.log')):
                self.copy(path, 'previous-and-current/' + str(path.relative_to(self.root)))
        for directory in ('/etc', '/data/unisoc_dbg', '/data', '/mnt/UDISK'):
            for path in sorted(Path(directory).glob('unisoc_cp2log_*.txt'))[:8]:
                self.copy(path, limit=2 * 1024 * 1024)
        for path in ('/etc/unisoc_cp2log_config.txt', '/data/unisoc_cp2log_config.txt',
                     '/vendor/etc/wifi/unisoc_cp2log_config.txt'):
            self.copy(path)
        self.command('kernel/journal-kernel.txt',
                     ['journalctl', '-k', '-b', '--no-pager', '-n', '5000'], timeout=6)
        self.command('wireless/iw-dev.txt', ['iw', 'dev'], timeout=3)
        self.command('wireless/iw-phy.txt', ['iw', 'phy'], timeout=3)
        self.command('wireless/rfkill.txt', ['rfkill', 'list'], timeout=3)
        self.command('wireless/links-after.txt', ['ip', '-details', 'link', 'show'])

    def package(self, summary):
        self.text('SUMMARY.json', json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
        self.text('collection-manifest.json', json.dumps(self.records, indent=2, ensure_ascii=False) + '\n')
        archive = self.output.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as output:
            output.add(self.output, arcname=self.output.name)
        os.chmod(archive, 0o600)
        hasher = hashlib.sha256()
        with archive.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
        digest = hasher.hexdigest()
        archive.with_suffix('.gz.sha256').write_text(f'{digest}  {archive.name}\n')
        return archive


def observe(collector, worker, seconds):
    start = time.monotonic()
    next_sample = 0
    samples = 0
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= next_sample:
            collector.snapshot(f'during-{int(elapsed):03d}s', stacks=True)
            samples += 1
            next_sample = time.monotonic() - start + (2 if samples < 4 else 5)
        if worker.poll() is not None:
            return worker.returncode
        if time.monotonic() - start >= seconds:
            return 'still-running'
        time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', dest='mode', action='store_const', const='run',
                        help='Request one fresh traced load; collect only if already loaded or after Oops.')
    parser.add_argument('--snapshot', dest='mode', action='store_const', const='snapshot',
                        help='Collect existing state without loading drivers; no reboot needed.')
    parser.set_defaults(mode='auto')
    parser.add_argument('--seconds', type=int, default=45, help='Maximum load observation window (10..180).')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root or with sudo.')
    if not 10 <= args.seconds <= 180:
        parser.error('--seconds must be 10..180.')
    root = Path(__file__).resolve().parent.parent
    dist = root / 'dist'
    dist.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output = Path(tempfile.mkdtemp(prefix=f'cm311-diag-{timestamp}-', dir=dist))
    os.chmod(output, 0o700)
    collector = Collector(root, output)
    loaded = [name for name in DRIVERS if (Path('/sys/module') / name).exists()]
    try:
        taint = int(Path('/proc/sys/kernel/tainted').read_text())
    except (OSError, ValueError):
        taint = 1 << 7  # Unknown state: collect instead of loading.
    action = choose_action(args.mode, loaded, taint)
    summary = {'started_local': timestamp, 'kernel': platform.release(),
               'port_version': (root / 'PORT_VERSION').read_text().strip(),
               'mode': args.mode, 'action': action, 'loaded_before': loaded,
               'kernel_taint_before': taint, 'load_result': 'not-started',
               'output_directory': str(output), 'collector_version': 1}
    print(f'Diagnostic directory: {output}', flush=True)
    collector.command('kernel/dmesg-before.txt', ['dmesg'])
    reader = worker = None
    try:
        with (output / 'kernel/dmesg-live.txt').open('wb') as stream:
            reader = subprocess.Popen(['dmesg', '--follow'], stdout=stream,
                                      stderr=subprocess.STDOUT, start_new_session=True,
                                      preexec_fn=limit_live_log)
        collector.static(platform.release())
        collector.snapshot('before')
        collector.debug_state('before')
        if action == 'load-once' and platform.machine() == 'aarch64':
            env = dict(os.environ, SDIO_TRACE='1', WIFI_LOG_LEVEL='4', CM311_CAPTURE_EXTERNAL='1')
            if 'debugfs' not in Path('/proc/filesystems').read_text().split():
                env['SDIO_TRACE'] = '0'
                summary['sdio_trace_unavailable'] = 'Kernel has no debugfs support.'
            print('Loading once with available SDIO/Wi-Fi trace; collecting snapshots during the load...', flush=True)
            with (output / 'load-console.txt').open('wb') as stream:
                worker = subprocess.Popen(['bash', str(root / 'scripts/load-test.sh')],
                                          env=env, stdout=stream, stderr=subprocess.STDOUT,
                                          start_new_session=True)
            summary['loader_pid'] = worker.pid
            summary['load_result'] = observe(collector, worker, args.seconds)
            if summary['load_result'] == 'still-running':
                print('Loader is still running. Preserving its stack and packaging now; it is left running.', flush=True)
            elif summary['load_result'] != 0:
                print('Wi-Fi startup failed. Saving logs; do not continue to connection commands.', flush=True)
        else:
            if action == 'load-once':
                summary['action'] = 'snapshot-non-arm64-host'
            print('Collecting current state only; no reload or reboot is performed.', flush=True)
            time.sleep(2)
        collector.snapshot('after')
        collector.debug_state('after')
        collector.existing_logs(platform.release())
    except KeyboardInterrupt:
        summary['collection_interrupted'] = True
        print('Collection interrupted; packaging what has been captured.', flush=True)
    except Exception as exc:
        summary['collection_error'] = repr(exc)
        print(f'Collection error: {exc}; packaging available data.', flush=True)
    finally:
        stop_reader(reader)
        collector.command('kernel/dmesg-after.txt', ['dmesg'])
        if worker is not None:
            summary['loader_still_running'] = worker.poll() is None
        summary['elapsed_seconds'] = round(time.monotonic() - collector.started, 1)
        summary['live_log_over_limit'] = (output / 'kernel/dmesg-live.txt').exists() and \
            (output / 'kernel/dmesg-live.txt').stat().st_size >= MAX_LIVE
        archive = collector.package(summary)
        print(f'\nSend this ONE file:\n{archive}', flush=True)
        if summary['action'].startswith('snapshot-'):
            print('This bundle contains existing state; no driver load was attempted.', flush=True)
    if summary.get('collection_interrupted'):
        return 130
    if summary.get('collection_error') or summary.get('load_result') not in (0, 'not-started'):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
