#!/usr/bin/env python3
"""Verify an ophub header archive and rebuild only its host tools for this runner."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request


def main():
    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / 'ci/kernel.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Use a local, checksum-verified archive instead of downloading.')
    parser.add_argument('--output', type=Path, help='New output directory (default .cache/ci-headers-RELEASE).')
    args = parser.parse_args()
    override = {key: os.environ.get('CI_KERNEL_' + key.upper(), '').strip() for key in config}
    if any(override.values()) and not all(override.values()):
        parser.error('Custom CI_KERNEL_RELEASE, CI_KERNEL_URL and CI_KERNEL_SHA256 must be supplied together')
    if all(override.values()):
        config = override
    release, url, digest = (config[key] for key in ('release', 'url', 'sha256'))
    if not re.fullmatch(r'6\.18\.[A-Za-z0-9._+-]+', release):
        parser.error('Expected an explicit Linux 6.18 kernel release')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        parser.error('The header URL must use HTTPS without embedded credentials')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
        parser.error('An explicit SHA-256 is required')
    area = (args.output or root / '.cache' / ('ci-headers-' + release)).resolve()
    area.parent.mkdir(exist_ok=True)
    if area.exists():
        parser.error(f'Refusing to overwrite existing headers: {area}')
    with tempfile.TemporaryDirectory(prefix='.headers-', dir=area.parent) as folder:
        temp = Path(folder)
        archive = args.archive or temp / 'kernel.tar.gz'
        if args.archive is None:
            with urllib.request.urlopen(url, timeout=90) as response, archive.open('wb') as stream:
                shutil.copyfileobj(response, stream)
        with archive.open('rb') as stream:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(chunk)
            actual = hasher.hexdigest()
        if actual != digest.lower():
            raise ValueError(f'Header archive checksum mismatch: {actual}')
        with tarfile.open(archive) as outer:
            members = outer.getmembers()
            nested = [member for member in members if member.isfile()
                      and Path(member.name).name == f'header-{release}.tar.gz']
            if nested:
                if len(nested) != 1 or nested[0].size > 512 * 1024 * 1024:
                    raise ValueError('Ambiguous or oversized nested header archive')
                nested_path = temp / 'headers.tar.gz'
                with outer.extractfile(nested[0]) as src, nested_path.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
            else:
                nested_path = archive
        prepared = temp / 'prepared'
        prepared.mkdir()
        with tarfile.open(nested_path) as headers:
            if sum(member.size for member in headers.getmembers()) > 1024 * 1024 * 1024:
                raise ValueError('Unpacked headers exceed 1 GiB')
            # DTC/include-prefix aliases are not needed by external modules.
            # Do not follow archived links (some upstream bundles contain
            # aliases outside their packaged subset of the source tree).
            links = [member.name for member in headers.getmembers() if member.issym() or member.islnk()]
            members = [member for member in headers.getmembers() if not (member.issym() or member.islnk())]
            headers.extractall(prepared, members=members, filter='data')
        required = ('Makefile', 'Module.symvers', 'include/generated/autoconf.h',
                    'include/generated/utsrelease.h')
        if not all((prepared / name).is_file() for name in required):
            raise ValueError('Expected a prepared header tree at the archive root')
        if f'"{release}"' not in (prepared / required[-1]).read_text():
            raise ValueError('UTS_RELEASE differs from the requested kernel')
        # Keep ARM64 elfconfig/devicetable offsets and Module.symvers intact.
        cc = os.environ.get('HOSTCC', 'cc')
        subprocess.run([cc, '-O2', '-I', 'scripts/include', '-o', 'scripts/basic/fixdep',
                        'scripts/basic/fixdep.c'], cwd=prepared, check=True)
        subprocess.run([cc, '-O2', '-I', 'scripts/include', '-o', 'scripts/mod/modpost',
                        *['scripts/mod/' + name + '.c' for name in ('modpost', 'file2alias', 'sumversion', 'symsearch')]],
                       cwd=prepared, check=True)
        (prepared / '.cm311-ci-headers.json').write_text(json.dumps(
            {**config, 'skipped_archive_links': links}, indent=2) + '\n')
        prepared.rename(area)
    if env_file := os.environ.get('GITHUB_ENV'):
        with Path(env_file).open('a') as stream:
            stream.write(f'KDIR={area}\nKERNEL_RELEASE={release}\n')
    if output_file := os.environ.get('GITHUB_OUTPUT'):
        with Path(output_file).open('a') as stream:
            stream.write(f'kernel_release={release}\n')
    print(f'KERNEL_RELEASE={release}\nKDIR={area}')


if __name__ == '__main__':
    main()
