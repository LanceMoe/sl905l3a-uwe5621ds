#!/usr/bin/env python3
"""Validate real delivery archives and release publication ordering without GitHub writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, PROJECT / 'scripts' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('driver_release', 'release.py')
package = load('driver_package', 'package.py')


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = tempfile.TemporaryDirectory()
        cls.project = Path(cls.area.name) / 'project'
        cls.project.mkdir()
        for source in package.files(PROJECT, 'integration', None):
            target = cls.project / source.relative_to(PROJECT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        cls.version = (cls.project / 'VERSION').read_text().strip()
        cls.tag = 'v' + cls.version
        cls.kernel = '6.18.56-ophub'
        cls.repository = 'example/cm311'
        cls.commit = 'a' * 40
        source = cls.project / 'build/uwe5622'
        (source / 'unisocwifi').mkdir(parents=True)
        (source / 'unisocwifi/main.c').write_text('/* corresponding source */\n')
        (source / '.cm311-source').write_text('source-stamp\n')
        output = cls.project / 'dist' / cls.kernel
        output.mkdir(parents=True)
        for name, content in (('kernel-release', cls.kernel), ('source-stamp', 'source-stamp'), ('build.log', 'provenance')):
            (output / name).write_text(content + '\n')
        for module in ('uwe5621_bsp_sdio', 'sprdwl_ng'):
            (output / (module + '.ko')).write_bytes(b'test module ' + module.encode())
        (output / 'SHA256SUMS').write_text(''.join(
            f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in output.glob('*.ko')))
        with mock.patch.object(package.subprocess, 'run'):
            for kind in ('source', 'integration', 'modules'):
                package.package(cls.project, kind, cls.kernel if kind == 'modules' else None)

    @classmethod
    def tearDownClass(cls):
        cls.area.cleanup()

    def prepare(self, output, inputs=None, magic=None):
        return release.prepare(self.project, inputs or self.project / 'dist', output,
                               self.tag, self.kernel, self.repository, self.commit,
                               runner=lambda argv: magic or self.kernel + ' SMP aarch64')

    def test_complete_release_plan_and_installation_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'release'
            self.prepare(output)
            metadata = json.loads((output / 'RELEASE.json').read_text())
            self.assertEqual(metadata['binary_kernels'], [self.kernel])
            notes = (output / 'RELEASE_NOTES.md').read_text()
            self.assertIn('6.18.37-ophub', notes)
            self.assertIn('不能互换', notes)
            self.assertIn('INSTALL.md', metadata['files'])
            sums = (output / 'SHA256SUMS').read_text().splitlines()
            self.assertEqual(len(sums), 8)
            for line in sums:
                expected, name = line.split()
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), expected)
            self.assertFalse(any(path.suffix == '.log' for path in output.iterdir()))

    def test_wrong_tag_kernel_or_corrupted_asset_cannot_be_released(self):
        with self.assertRaisesRegex(ValueError, 'exactly match VERSION'):
            release.validate_tag(self.project, 'v99.0.0')
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'release'
            with self.assertRaisesRegex(ValueError, 'vermagic/architecture'):
                self.prepare(output, magic='6.18.37-ophub SMP aarch64')
        with tempfile.TemporaryDirectory() as folder:
            inputs = Path(folder) / 'inputs'
            shutil.copytree(self.project / 'dist', inputs)
            archive = next(inputs.glob('*-source.tar.gz'))
            archive.write_bytes(b'corrupted archive')
            with self.assertRaisesRegex(ValueError, 'asset SHA-256 mismatch'):
                self.prepare(Path(folder) / 'release', inputs)

    def test_new_release_is_draft_until_all_assets_are_uploaded(self):
        for existing in (None, {'draft': True, 'html_url': 'draft-url'}):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / 'release'
                self.prepare(output)
                calls = []

                def gh(argv):
                    calls.append(argv)
                    if argv[1] == 'api':
                        if existing is None:
                            raise subprocess.CalledProcessError(1, argv, stderr='gh: Not Found (HTTP 404)')
                        return json.dumps(existing)
                    return ''

                release.publish(output, self.repository, self.tag, gh)
                self.assertEqual(calls[-1][-1], '--draft=false')
                upload = next(argv for argv in calls if argv[1:3] == ['release', 'upload'])
                self.assertIn('--clobber', upload)
                if existing is None:
                    create = next(argv for argv in calls if argv[1:3] == ['release', 'create'])
                    self.assertIn('--verify-tag', create)
                    self.assertIn('--draft', create)
                self.assertLess(calls.index(upload), len(calls)-1)

    def test_upload_failure_leaves_draft_and_published_release_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'release'
            self.prepare(output)
            calls = []

            def failing_upload(argv):
                calls.append(argv)
                if argv[1] == 'api':
                    return json.dumps({'draft': True})
                if argv[1:3] == ['release', 'upload']:
                    raise subprocess.CalledProcessError(1, argv, stderr='upload interrupted')
                return ''

            with self.assertRaises(subprocess.CalledProcessError):
                release.publish(output, self.repository, self.tag, failing_upload)
            self.assertFalse(any('--draft=false' in argv for argv in calls))
            calls.clear()

            def published(argv):
                calls.append(argv)
                return json.dumps({'draft': False, 'html_url': 'https://example.invalid/release'})

            release.publish(output, self.repository, self.tag, published)
            self.assertEqual(len(calls), 1)

    def test_authentication_errors_and_incomplete_assets_do_not_create_release(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'release'
            self.prepare(output)
            calls = []

            def denied(argv):
                calls.append(argv)
                raise subprocess.CalledProcessError(1, argv, stderr='HTTP 403')

            with self.assertRaises(subprocess.CalledProcessError):
                release.publish(output, self.repository, self.tag, denied)
            self.assertEqual(len(calls), 1)
            (output / 'SHA256SUMS').write_text('')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                release.publish(output, self.repository, self.tag, denied)
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
