#!/usr/bin/env python3
"""Run the actual boot shell with a temporary sysfs and stub module tools."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parent.parent


class BootLoaderTests(unittest.TestCase):
    def run_loader(self, mode):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sysfs, binaries, log = root / 'sys', root / 'bin', root / 'calls'
            binaries.mkdir()
            sysfs.mkdir()
            loader = root / 'load.sh'
            loader.write_text((PROJECT / 'packaging/cm311-wifi-load').read_text().replace('/sys/', str(sysfs) + '/'))
            if mode != 'no-card':
                card = sysfs / 'bus/sdio/devices/mmc0:8800:1'
                card.mkdir(parents=True)
                (card / 'uevent').write_text('OF_COMPATIBLE_0=sprd,unisoc-wifi\nSDIO_ID=0000:0000\n')
            if mode in ('already-bound', 'failed-wifi'):
                (sysfs / 'module/sprdwl_ng').mkdir(parents=True)
            if mode == 'transport-only':
                (sysfs / 'module/uwe5621_bsp_sdio').mkdir(parents=True)
            if mode == 'already-bound':
                binding = sysfs / 'bus/platform/drivers/unisoc_wifi/unisoc_wifi'
                binding.parent.mkdir(parents=True)
                binding.symlink_to('../../fake-platform')
            scripts = {
                'uname': '#!/bin/sh\nprintf "%s\\n" "$TEST_KERNEL"\n',
                'sleep': '#!/bin/sh\nexit 0\n',
                'modprobe': '''#!/bin/sh
printf '%s\n' "$1" >> "$TEST_CALLS"
if [ "$1" = sprdwl_ng ] && [ "$TEST_MODE" != no-binding ]; then
    mkdir -p "$TEST_SYS/bus/platform/drivers/unisoc_wifi"
    ln -s ../../fake-platform "$TEST_SYS/bus/platform/drivers/unisoc_wifi/unisoc_wifi"
fi
''',
            }
            for name, script in scripts.items():
                (binaries / name).write_text(script)
                (binaries / name).chmod(0o755)
            env = dict(os.environ, PATH=str(binaries) + ':' + os.environ['PATH'],
                       TEST_KERNEL='6.12.1' if mode == 'wrong-kernel' else '6.18.37-ophub',
                       TEST_CALLS=str(log), TEST_SYS=str(sysfs), TEST_MODE=mode)
            result = subprocess.run(['bash', str(loader)], env=env, capture_output=True, text=True, timeout=3)
            return result, log.read_text().splitlines() if log.exists() else []

    def test_normal_load_order_and_real_binding_check(self):
        result, calls = self.run_loader('normal')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['cfg80211', 'uwe5621_bsp_sdio', 'sprdwl_ng'])
        result, calls = self.run_loader('no-binding')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertIn('did not bind', result.stderr)

    def test_existing_or_failed_probe_is_not_repeated(self):
        result, calls = self.run_loader('already-bound')
        self.assertEqual((result.returncode, calls), (0, []))
        for mode in ('failed-wifi', 'transport-only', 'no-card', 'wrong-kernel'):
            with self.subTest(mode=mode):
                result, calls = self.run_loader(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
