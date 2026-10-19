#!/usr/bin/env python3
"""Build reproducible source, integration-update or kernel-specific module archives."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def files(root, kind, release):
    paths = [root / name for name in ('README.md', 'DIAGNOSTICS.md', 'INSTALL.md', 'CHANGELOG.md',
                                      'LICENSE', 'THIRD_PARTY.md', 'VERSION', 'PORT_VERSION',
                                      '.gitattributes', '.gitignore')]
    for directory in ('scripts', 'tests', 'driver', 'experimental', 'docs', 'packaging', 'ci', '.github'):
        paths.extend(path for path in (root / directory).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix != '.pyc')
    if kind != 'integration':
        subprocess.run(['bash', str(root / 'scripts/prepare.sh')], check=True)
        source = root / 'build/uwe5622'
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(source)
            if not path.is_file() or any(part.startswith('.') for part in relative.parts):
                continue
            if path.name.endswith('.mod.c') or 'fw' in relative.parts:
                continue
            if len(relative.parts) > 1 and relative.parts[0] not in ('unisocwcn', 'unisocwifi', 'tty-sdio'):
                continue
            if path.suffix in ('.c', '.h', '.md', '.txt') or path.name in ('Makefile', 'Kconfig'):
                paths.append(path)
        paths.append(source / '.cm311-source')
    if kind == 'modules':
        output = root / 'dist' / release
        if (output / 'kernel-release').read_text().strip() != release:
            raise ValueError('Module build does not match the requested release')
        stamp = (root / 'build/uwe5622/.cm311-source').read_text()
        if (output / 'source-stamp').read_text() != stamp:
            raise ValueError('Module source stamp differs from packaged source')
        recorded = {}
        for line in (output / 'SHA256SUMS').read_text().splitlines():
            checksum, name = line.split(maxsplit=1)
            name = Path(name.lstrip('*')).name
            if name in recorded:
                raise ValueError('Duplicate module checksum')
            recorded[name] = checksum
        for name in ('uwe5621_bsp_sdio.ko', 'sprdwl_ng.ko'):
            if recorded.get(name) != digest((output / name).read_bytes()):
                raise ValueError('Module checksum mismatch: ' + name)
        paths.extend(output / name for name in ('uwe5621_bsp_sdio.ko', 'sprdwl_ng.ko', 'kernel-release',
                                                'source-stamp', 'SHA256SUMS', 'build.log'))
    return sorted(set(paths))


def package(root, kind, release=None):
    root = Path(root).resolve()
    version = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('VERSION must be a numeric semantic version')
    if kind == 'modules' and (not release or not re.fullmatch(r'6\.18\.[A-Za-z0-9._+-]+', release)):
        raise ValueError('An explicit supported --kernel-release is required for modules')
    payload = {path.relative_to(root).as_posix(): path.read_bytes() for path in files(root, kind, release)}
    manifest = {'format': 1, 'version': version, 'port_version': (root / 'PORT_VERSION').read_text().strip(),
                'kind': kind, 'kernel_release': release if kind == 'modules' else None,
                'upstream_commit': 'fa9d1a6703a93a85bcf68e865ebc2726474076ad',
                'files': {name: {'size': len(data), 'sha256': digest(data)} for name, data in payload.items()}}
    payload['PACKAGE_MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    suffix = release if kind == 'modules' else kind
    output = root / 'dist' / f'cm311-uwe5621ds-{version}-{suffix}.tar.gz'
    output.parent.mkdir(exist_ok=True)
    with output.open('wb') as file, gzip.GzipFile(filename='', fileobj=file, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for name, data in sorted(payload.items()):
                info = tarfile.TarInfo('cm311-uwe5621ds/' + name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith('.sh') or name == 'packaging/cm311-wifi-load' else 0o644
                info.mtime = info.uid = info.gid = 0
                info.uname = info.gname = 'root'
                archive.addfile(info, io.BytesIO(data))
    checksum = digest(output.read_bytes())
    output.with_name(output.name + '.sha256').write_text(f'{checksum}  {output.name}\n')
    print(f'{output}: {output.stat().st_size} bytes; SHA-256 {checksum}')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('source', 'integration', 'modules'), default='source')
    parser.add_argument('--kernel-release')
    args = parser.parse_args()
    package(Path(__file__).resolve().parent.parent, args.kind, args.kernel_release)


if __name__ == '__main__':
    main()
