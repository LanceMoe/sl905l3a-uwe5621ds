#!/usr/bin/env python3
"""Check delivery boundaries, manifests, LF scripts and reproducible archives."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('package_driver', PROJECT / 'scripts/package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_module_package_carries_source_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for source in package.files(PROJECT, 'integration', None):
                target = root / source.relative_to(PROJECT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            source = root / 'build/uwe5622'
            (source / 'unisocwifi').mkdir(parents=True)
            (source / 'unisocwifi/main.c').write_text('/* corresponding source */\n')
            (source / '.cm311-source').write_text('source-stamp\n')
            release = '6.18.37-ophub'
            output = root / 'dist' / release
            output.mkdir(parents=True)
            (output / 'kernel-release').write_text(release + '\n')
            (output / 'source-stamp').write_text('source-stamp\n')
            (output / 'build.log').write_text('build provenance\n')
            for module in ('sprdwl_ng', 'uwe5621_bsp_sdio'):
                (output / (module + '.ko')).write_bytes(b'test module ' + module.encode())
            (output / 'SHA256SUMS').write_text(''.join(
                f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in output.glob('*.ko')))
            with mock.patch.object(package.subprocess, 'run'):
                archive_path = package.package(root, 'modules', release)
                with tarfile.open(archive_path) as archive:
                    prefix = 'cm311-uwe5621ds/'
                    self.assertIn(prefix + 'build/uwe5622/unisocwifi/main.c', archive.getnames())
                    manifest = json.load(archive.extractfile(prefix + 'PACKAGE_MANIFEST.json'))
                    self.assertEqual(manifest['kernel_release'], release)
                (output / 'sprdwl_ng.ko').write_bytes(b'corrupted')
                with self.assertRaisesRegex(ValueError, 'Module checksum mismatch'):
                    package.package(root, 'modules', release)

    def test_integration_is_reproducible_and_preserves_existing_build(self):
        # Use only distributable project files; never copy local firmware or diagnostics.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sentinel = hashlib.sha256(b'test-only excluded payload').digest() * 8
            for source in package.files(PROJECT, 'integration', None):
                target = root / source.relative_to(PROJECT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            for name in ('.cache/private-token', 'dist/private.log', 'build/uwe5622/fw/wcnmodem.bin'):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(sentinel)
            output = package.package(root, 'integration')
            first = output.read_bytes()
            self.assertEqual(package.package(root, 'integration').read_bytes(), first)
            with tarfile.open(output) as archive:
                names = archive.getnames()
                self.assertFalse(any('/build/' in name or '/.cache/' in name or '/dist/' in name for name in names))
                prefix = 'cm311-uwe5621ds/'
                manifest = json.load(archive.extractfile(prefix + 'PACKAGE_MANIFEST.json'))
                self.assertEqual(manifest['kind'], 'integration')
                for name, metadata in manifest['files'].items():
                    member = archive.getmember(prefix + name)
                    self.assertTrue(member.isfile())
                    data = archive.extractfile(member).read()
                    self.assertNotIn(sentinel, data)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), metadata['sha256'])
                    if name.endswith(('.sh', '.py')) or name == 'packaging/cm311-wifi-load':
                        self.assertNotIn(b'\r', data, name)
                    self.assertEqual(member.uid, 0)
                self.assertEqual(archive.getmember(prefix + 'packaging/cm311-wifi-load').mode, 0o755)
                self.assertIn(prefix + '.github/workflows/ci.yml', names)
                self.assertIn(prefix + 'scripts/system-install.py', names)
            self.assertEqual(output.with_name(output.name + '.sha256').read_text().split()[0],
                             hashlib.sha256(first).hexdigest())


if __name__ == '__main__':
    unittest.main()
