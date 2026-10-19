#!/usr/bin/env python3
"""Check local preparation, edit detection and protection of existing builds."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('source_tree', Path(__file__).resolve().parent.parent / 'scripts/source-tree.py')
source_tree = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source_tree)


class SourceTreeTests(unittest.TestCase):
    def test_local_copy_and_changed_source_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            driver = root / 'driver/uwe5622'
            driver.mkdir(parents=True)
            (driver / 'Makefile').write_text('original\n')
            source_tree.prepare(root)
            source_tree.prepare(root)
            self.assertEqual((root / 'build/uwe5622/Makefile').read_text(), 'original\n')
            (driver / 'Makefile').write_text('edited\n')
            with self.assertRaisesRegex(ValueError, 'Source changed'):
                source_tree.prepare(root)
            self.assertEqual((root / 'build/uwe5622/Makefile').read_text(), 'original\n')

    def test_unmarked_directory_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'driver/uwe5622').mkdir(parents=True)
            (root / 'driver/uwe5622/Makefile').write_text('source\n')
            (root / 'build/uwe5622').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'unmarked'):
                source_tree.prepare(root)


if __name__ == '__main__':
    unittest.main()
