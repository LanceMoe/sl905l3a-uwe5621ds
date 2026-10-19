#!/usr/bin/env python3
"""Exercise installation transactions in a temporary root; never load modules."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('system_install', PROJECT / 'scripts/system-install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name) / 'root'
        self.root.mkdir()
        self.output = Path(self.area.name) / 'modules'
        self.output.mkdir()
        self.release = '6.18.37-ophub'
        self.calls = []
        self.fail_depmod = False
        self.magic = self.release + ' SMP preempt mod_unload aarch64'
        for module in install.MODULES:
            (self.output / (module + '.ko')).write_bytes(b'test module: ' + module.encode())
        self.checksums()
        (self.output / 'kernel-release').write_text(self.release + '\n')
        stamp = subprocess.check_output(['python3', str(PROJECT / 'scripts/source-tree.py'), '--stamp'], text=True).strip()
        (self.output / 'source-stamp').write_text(stamp + '\n')
        firmware = self.root / 'lib/firmware/uwe5621ds'
        firmware.mkdir(parents=True)
        for name in ('wcnmodem.bin', 'wifi_56630001_3ant.ini'):
            (firmware / name).write_bytes(b'existing firmware')
        self.context = install.SystemInstall(PROJECT, self.root, self.command)

    def checksums(self):
        (self.output / 'SHA256SUMS').write_text(''.join(
            f'{hashlib.sha256(path.read_bytes()).hexdigest()}  /original/build/{path.name}\n'
            for path in sorted(self.output.glob('*.ko'))))

    def command(self, argv):
        self.calls.append(argv)
        if argv[:3] == ['modinfo', '-F', 'name']:
            return Path(argv[3]).stem
        if argv[:3] == ['modinfo', '-F', 'vermagic']:
            return self.magic
        if argv[0] == 'depmod' and self.fail_depmod:
            self.fail_depmod = False
            raise subprocess.CalledProcessError(1, argv)
        self.assertNotIn(argv[0], ('modprobe', 'insmod', 'rmmod', 'reboot', 'systemctl'))
        return ''

    def install(self):
        self.context.install(self.release, self.output)

    def test_install_repeat_upgrade_uninstall_restores_original(self):
        config = self.root / 'etc/modprobe.d/cm311-wifi.conf'
        config.parent.mkdir(parents=True)
        config.write_text('previous user config\n')
        module = self.root / f'lib/modules/{self.release}/updates/cm311/sprdwl_ng.ko'
        module.parent.mkdir(parents=True)
        module.write_bytes(b'previous module')
        self.install()
        self.assertTrue((self.root / install.ENABLE_LINK).is_symlink())
        self.assertEqual((self.root / install.ENABLE_LINK).readlink().as_posix(), '../cm311-wifi.service')
        self.assertEqual((self.root / 'usr/local/libexec/cm311-wifi-load').stat().st_mode & 0o777, 0o755)
        self.install()  # Reinstall must retain the first baseline backup.
        (self.output / 'sprdwl_ng.ko').write_bytes(b'new module revision')
        self.checksums()
        self.install()
        self.context.uninstall()
        self.assertEqual(config.read_text(), 'previous user config\n')
        self.assertEqual(module.read_bytes(), b'previous module')
        self.assertFalse((self.root / install.ENABLE_LINK).is_symlink())
        self.assertFalse((self.root / install.STATE).exists())
        self.assertEqual((self.root / 'lib/firmware/uwe5621ds/wcnmodem.bin').read_bytes(), b'existing firmware')

    def test_modified_managed_file_is_preserved(self):
        self.install()
        config = self.root / 'etc/modprobe.d/cm311-wifi.conf'
        config.write_text('my edit\n')
        for operation in (self.install, self.context.uninstall):
            with self.assertRaisesRegex(ValueError, 'Managed files changed'):
                operation()
        self.assertEqual(config.read_text(), 'my edit\n')
        self.assertTrue((self.root / install.STATE).exists())

    def test_depmod_failure_rolls_back_files_and_receipt(self):
        self.fail_depmod = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        self.assertFalse((self.root / install.STATE).exists())
        self.assertFalse((self.root / install.ENABLE_LINK).is_symlink())
        self.assertFalse((self.root / 'etc/modprobe.d/cm311-wifi.conf').exists())
        self.install()
        receipt = (self.root / install.STATE).read_bytes()
        self.fail_depmod = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.context.uninstall()
        self.assertEqual((self.root / install.STATE).read_bytes(), receipt)
        self.assertTrue((self.root / install.ENABLE_LINK).is_symlink())

    def test_kernel_source_checksum_and_firmware_preflight(self):
        stamp = self.output / 'source-stamp'
        original_stamp = stamp.read_text()
        stamp.write_text('incorrect-source\n')
        with self.assertRaisesRegex(ValueError, 'do not match this driver source'):
            self.install()
        stamp.write_text(original_stamp)
        self.magic = '6.18.56-ophub SMP aarch64'
        with self.assertRaisesRegex(ValueError, 'vermagic mismatch'):
            self.install()
        self.magic = self.release + ' SMP aarch64'
        (self.output / 'sprdwl_ng.ko').write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.install()
        self.checksums()
        (self.root / 'lib/firmware/uwe5621ds/wcnmodem.bin').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing existing'):
            self.install()
        self.assertFalse((self.root / install.STATE).exists())

    def test_multiple_kernel_installations_share_one_rollback_baseline(self):
        first = self.release
        self.install()
        self.release = '6.18.56-ophub'
        self.magic = self.release + ' SMP aarch64'
        (self.output / 'kernel-release').write_text(self.release + '\n')
        self.install()
        receipt = json.loads((self.root / install.STATE).read_text())
        self.assertEqual(receipt['kernels'], [first, self.release])
        self.context.uninstall()
        for release in (first, self.release):
            self.assertFalse((self.root / f'lib/modules/{release}/updates/cm311/sprdwl_ng.ko').exists())

    def test_escape_and_tampered_receipt_are_rejected(self):
        (self.root / 'etc').symlink_to(Path(self.area.name))
        with self.assertRaisesRegex(ValueError, 'escapes root'):
            self.install()
        (self.root / 'etc').unlink()
        self.install()
        receipt = self.root / install.STATE
        value = json.loads(receipt.read_text())
        value['files']['etc/other-file'] = {'installed_sha256': 'absent', 'original': {'kind': 'absent'}}
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'Unexpected managed path'):
            self.context.uninstall()

    def test_original_symlink_is_restored_without_following_target(self):
        config = self.root / 'etc/modprobe.d/cm311-wifi.conf'
        config.parent.mkdir(parents=True)
        outside = Path(self.area.name) / 'outside'
        outside.write_text('untouched\n')
        config.symlink_to(outside)
        self.install()
        self.context.uninstall()
        self.assertTrue(config.is_symlink())
        self.assertEqual(config.readlink(), outside)
        self.assertEqual(outside.read_text(), 'untouched\n')


if __name__ == '__main__':
    unittest.main()
