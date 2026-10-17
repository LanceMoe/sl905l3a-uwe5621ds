#!/usr/bin/env python3
"""Install the validated modules and boot service without loading/unloading them."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile
import uuid

MODULES = ('uwe5621_bsp_sdio', 'sprdwl_ng')
FIXED = {
    'etc/modprobe.d/cm311-wifi.conf': ('packaging/cm311-wifi.conf', 0o644),
    'etc/systemd/system/cm311-wifi.service': ('packaging/cm311-wifi.service', 0o644),
    'usr/local/libexec/cm311-wifi-load': ('packaging/cm311-wifi-load', 0o755),
}
ENABLE_LINK = 'etc/systemd/system/multi-user.target.wants/cm311-wifi.service'
STATE = 'var/lib/cm311-wifi/install.json'
RELEASE = re.compile(r'6\.18\.[A-Za-z0-9._+-]+\Z')


def run(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SystemInstall:
    def __init__(self, project, root=Path('/'), runner=run):
        self.project = Path(project).resolve()
        self.root = Path(root).resolve()
        self.run = runner

    def path(self, relative):
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Invalid relative installation path: ' + str(relative))
        target = self.root / rel
        # Follow parent symlinks (including usrmerge) only within this root.
        if not target.parent.resolve().is_relative_to(self.root):
            raise ValueError('Installation path escapes root: ' + str(relative))
        return target

    def snapshot(self, relative):
        path = self.path(relative)
        if path.is_symlink():
            return {'kind': 'symlink', 'target': os.readlink(path)}
        if not path.exists():
            return {'kind': 'absent'}
        if not path.is_file():
            raise ValueError('Expected a regular file: ' + str(path))
        info = path.stat()
        return {'kind': 'file', 'data': path.read_bytes(), 'mode': stat.S_IMODE(info.st_mode),
                'uid': info.st_uid, 'gid': info.st_gid}

    def write(self, relative, value):
        path = self.path(relative)
        if value['kind'] == 'absent':
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.cm311-', dir=path.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, 'wb') as stream:
                if value['kind'] == 'file':
                    stream.write(value['data'])
                    stream.flush()
                    os.fchmod(stream.fileno(), value['mode'])
                    if os.geteuid() == 0:
                        os.fchown(stream.fileno(), value.get('uid', 0), value.get('gid', 0))
                    os.fsync(stream.fileno())
            if value['kind'] == 'symlink':
                temporary.unlink()
                temporary.symlink_to(value['target'])
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def fingerprint(self, relative):
        value = self.snapshot(relative)
        if value['kind'] == 'file':
            return sha(value['data'])
        if value['kind'] == 'symlink':
            return 'symlink:' + value['target']
        return 'absent'

    @staticmethod
    def managed(relative):
        return relative in FIXED or relative == ENABLE_LINK or re.fullmatch(
            r'lib/modules/6\.18\.[A-Za-z0-9._+-]+/updates/cm311/(uwe5621_bsp_sdio|sprdwl_ng)\.ko',
            relative) is not None

    def receipt(self):
        path = self.path(STATE)
        if not path.exists():
            return {'format': 1, 'files': {}, 'kernels': []}
        if path.is_symlink():
            raise ValueError('Installation receipt must not be a symlink')
        value = json.loads(path.read_text())
        if value.get('format') != 1 or not isinstance(value.get('files'), dict):
            raise ValueError('Unsupported installation receipt')
        for name in value['files']:
            if not self.managed(name):
                raise ValueError('Unexpected managed path in receipt: ' + name)
        for release in value['kernels']:
            if not RELEASE.fullmatch(release):
                raise ValueError('Invalid kernel release in receipt')
        return value

    def unchanged(self, receipt):
        changed = [name for name, value in receipt['files'].items()
                   if self.fingerprint(name) != value['installed_sha256']]
        if changed:
            raise ValueError('Managed files changed; preserve/reconcile your edits before continuing: ' + ', '.join(changed))

    def depmod(self, releases):
        for release in sorted(set(releases)):
            self.run(['depmod', '-b', str(self.root), '-a', release])

    def reload(self):
        if self.root == Path('/'):
            self.run(['systemctl', 'daemon-reload'])

    def validate_modules(self, release, directory):
        if not RELEASE.fullmatch(release):
            raise ValueError('Only explicit Linux 6.18 releases are supported')
        directory = Path(directory)
        if (directory / 'kernel-release').read_text().strip() != release:
            raise ValueError('Build output kernel-release does not match the requested kernel')
        patch = sha((self.project / 'patches/0001-mainline-meson-uwe5621.patch').read_bytes())
        expected = 'fa9d1a6703a93a85bcf68e865ebc2726474076ad ' + patch
        if (directory / 'source-stamp').read_text().strip() != expected:
            raise ValueError('Modules do not match this driver patch; rebuild before installing')
        recorded = {}
        for line in (directory / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            name = Path(name.lstrip('*')).name
            if name in recorded or not re.fullmatch('[0-9a-fA-F]{64}', digest):
                raise ValueError('Invalid or duplicate module checksum')
            recorded[name] = digest.lower()
        files = {}
        for module in MODULES:
            path = directory / (module + '.ko')
            data = path.read_bytes()
            if sha(data) != recorded.get(path.name):
                raise ValueError('Module checksum mismatch: ' + path.name)
            if self.run(['modinfo', '-F', 'name', str(path)]) != module:
                raise ValueError('Unexpected module name: ' + path.name)
            magic = self.run(['modinfo', '-F', 'vermagic', str(path)]).split()
            if not magic or magic[0] != release or 'aarch64' not in magic:
                raise ValueError('Module architecture or vermagic mismatch: ' + path.name)
            files[f'lib/modules/{release}/updates/cm311/{path.name}'] = {
                'kind': 'file', 'data': data, 'mode': 0o644}
        return files

    def install(self, release, directory):
        files = self.validate_modules(release, directory)
        firmware = self.path('lib/firmware/uwe5621ds')
        for name in ('wcnmodem.bin', 'wifi_56630001_3ant.ini'):
            if not (firmware / name).is_file() or not (firmware / name).stat().st_size:
                raise ValueError('Missing existing CM311 firmware/config: ' + str(firmware / name))
        for target, (source, mode) in FIXED.items():
            files[target] = {'kind': 'file', 'data': (self.project / source).read_bytes(), 'mode': mode}
        files[ENABLE_LINK] = {'kind': 'symlink', 'target': '../cm311-wifi.service'}
        previous = self.receipt()
        self.unchanged(previous)
        receipt = copy.deepcopy(previous)
        before = {name: self.snapshot(name) for name in files}
        backup_id = uuid.uuid4().hex
        for index, (name, value) in enumerate(before.items()):
            if name in receipt['files']:
                continue
            original = {key: item for key, item in value.items() if key != 'data'}
            if value['kind'] == 'file':
                backup = f'var/lib/cm311-wifi/backups/{backup_id}/{index}'
                self.write(backup, {'kind': 'file', 'data': value['data'], 'mode': 0o600})
                original['backup'] = backup
            receipt['files'][name] = {'original': original}
        try:
            for name, value in files.items():
                self.write(name, value)
                receipt['files'][name]['installed_sha256'] = self.fingerprint(name)
            self.depmod([release])
            self.reload()
            receipt.update(version=(self.project / 'VERSION').read_text().strip(),
                           port_version=(self.project / 'PORT_VERSION').read_text().strip(),
                           kernels=sorted(set(previous['kernels'] + [release])))
            self.write(STATE, {'kind': 'file', 'data': (json.dumps(receipt, indent=2) + '\n').encode(), 'mode': 0o600})
        except BaseException:
            for name, value in reversed(list(before.items())):
                self.write(name, value)
            self.depmod([release])
            self.reload()
            raise
        print(f'Installed for {release}; boot service enabled. No module was loaded or unloaded.')
        if self.root != Path('/'):
            print('Offline root staged; no host systemctl command was executed.')
        print('At the next normal boot, inspect: systemctl status cm311-wifi.service')

    def original(self, metadata):
        value = dict(metadata)
        if value['kind'] == 'file':
            backup = value.pop('backup')
            if not re.fullmatch(r'var/lib/cm311-wifi/backups/[0-9a-f]{32}/[0-9]+', backup):
                raise ValueError('Invalid backup location in receipt')
            value['data'] = self.path(backup).read_bytes()
        return value

    def uninstall(self):
        receipt = self.receipt()
        if not receipt['files']:
            print('No CM311 installation receipt; nothing changed.')
            return
        self.unchanged(receipt)
        originals = {name: self.original(meta['original']) for name, meta in receipt['files'].items()}
        before = {name: self.snapshot(name) for name in originals}
        try:
            for name, value in originals.items():
                self.write(name, value)
            self.depmod(receipt['kernels'])
            self.reload()
        except BaseException:
            for name, value in before.items():
                self.write(name, value)
            self.depmod(receipt['kernels'])
            self.reload()
            raise
        self.path(STATE).unlink()
        print('Managed files restored; loaded modules stay in memory until a normal reboot.')
        print('Firmware and NetworkManager profiles were preserved. Backup files remain in /var/lib/cm311-wifi/backups.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'uninstall'))
    parser.add_argument('--root', type=Path, default=Path('/'), help='Offline root filesystem to stage (default: live system).')
    parser.add_argument('--kernel-release', default=platform.release())
    parser.add_argument('--modules', type=Path, help='Existing build output, default dist/KERNEL_RELEASE.')
    args = parser.parse_args(argv)
    project = Path(__file__).resolve().parent.parent
    root = args.root.resolve()
    if root == Path('/'):
        if os.geteuid() != 0 or platform.machine() != 'aarch64':
            parser.error('Live installation requires root on the ARM64 box')
        if not Path('/run/systemd/system').is_dir():
            parser.error('Live integration requires a running systemd system')
    try:
        installer = SystemInstall(project, root)
        lock = installer.path('var/lib/cm311-wifi/install.lock')
        lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if lock.is_symlink():
            raise ValueError('Installation lock must not be a symlink')
        with lock.open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == 'install':
                installer.install(args.kernel_release, args.modules or project / 'dist' / args.kernel_release)
            else:
                installer.uninstall()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Installation error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
