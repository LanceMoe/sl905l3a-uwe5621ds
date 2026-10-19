#!/usr/bin/env python3
"""Fingerprint the checked-in driver and prepare a disposable build copy."""
import hashlib
from pathlib import Path
import shutil
import sys

REVISION = 'fa9d1a6703a93a85bcf68e865ebc2726474076ad'


def source_stamp(root):
    source = Path(root) / 'driver/uwe5622'
    paths = sorted(path for path in source.rglob('*') if path.is_file())
    if not paths or not (source / 'Makefile').is_file():
        raise ValueError('Missing checked-in driver/uwe5622 source')
    digest = hashlib.sha256()
    for path in paths:
        name = path.relative_to(source).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, 'big') + name)
        digest.update(len(data).to_bytes(8, 'big') + data)
    return REVISION + ' ' + digest.hexdigest()


def prepare(root):
    root = Path(root)
    expected = source_stamp(root) + '\n'
    target = root / 'build/uwe5622'
    marker = target / '.cm311-source'
    if target.exists():
        if not marker.is_file() or marker.read_text() != expected:
            raise ValueError('Source changed or build directory is unmarked; move build/uwe5622 aside and rebuild')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root / 'driver/uwe5622', target)
        marker.write_text(expected)
    print('Prepared source: ' + str(target))


if __name__ == '__main__':
    project = Path(__file__).resolve().parent.parent
    if sys.argv[1:] == ['--stamp']:
        print(source_stamp(project))
    else:
        prepare(project)
