#!/usr/bin/env python3
"""Validate CI artifacts, prepare release notes and publish a version tag release."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile


def command(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_tag(project, tag):
    version = (Path(project) / 'VERSION').read_text().strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) or tag != 'v' + version:
        raise ValueError(f'Release tag must exactly match VERSION: v{version}')
    return version


def verify_package(path, version, kind, kernel, runner=command):
    prefix = 'cm311-uwe5621ds/'
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > 256 * 1024 * 1024:
            raise ValueError('Release archive exceeds the unpacked size limit')
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive member')
        for member in members:
            name = PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or '..' in name.parts or not member.name.startswith(prefix):
                raise ValueError('Unexpected release archive member: ' + member.name)
        manifest = json.load(archive.extractfile(prefix + 'PACKAGE_MANIFEST.json'))
        if (manifest['version'], manifest['kind'], manifest['kernel_release']) != (version, kind, kernel):
            raise ValueError('Release package version/type/kernel mismatch')
        expected = {prefix + name for name in manifest['files']} | {prefix + 'PACKAGE_MANIFEST.json'}
        if expected != set(names):
            raise ValueError('Package manifest does not describe every member')
        for name, info in manifest['files'].items():
            data = archive.extractfile(prefix + name).read()
            if digest(data) != info['sha256'] or len(data) != info['size']:
                raise ValueError('Inner package checksum mismatch: ' + name)
        if kind == 'modules':
            with tempfile.TemporaryDirectory(prefix='cm311-release-modinfo-') as folder:
                for module in ('uwe5621_bsp_sdio', 'sprdwl_ng'):
                    name = f'dist/{kernel}/{module}.ko'
                    path = Path(folder) / (module + '.ko')
                    path.write_bytes(archive.extractfile(prefix + name).read())
                    magic = runner(['modinfo', '-F', 'vermagic', str(path)]).split()
                    if not magic or magic[0] != kernel or 'aarch64' not in magic:
                        raise ValueError('Binary vermagic/architecture does not match its release label')


def prepare(project, inputs, output, tag, kernel, repository, commit, runner=command):
    project, inputs, output = Path(project), Path(inputs), Path(output)
    version = validate_tag(project, tag)
    if not re.fullmatch(r'6\.18\.[A-Za-z0-9._+-]+', kernel):
        raise ValueError('Invalid binary kernel release')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Expected OWNER/REPOSITORY')
    if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
        raise ValueError('A full source commit SHA is required')
    output.mkdir(parents=True, exist_ok=False)
    assets = []
    for suffix, kind, target in (('source', 'source', None), ('integration', 'integration', None),
                                 (kernel, 'modules', kernel)):
        filename = f'cm311-uwe5621ds-{version}-{suffix}.tar.gz'
        candidates = list(inputs.rglob(filename))
        checksums = list(inputs.rglob(filename + '.sha256'))
        if len(candidates) != 1 or len(checksums) != 1:
            raise ValueError('Expected exactly one archive and checksum: ' + filename)
        archive, checksum = candidates[0], checksums[0]
        record = checksum.read_text().split()
        if record != [digest(archive.read_bytes()), filename]:
            raise ValueError('Release asset SHA-256 mismatch: ' + filename)
        verify_package(archive, version, kind, target, runner)
        for source in (archive, checksum):
            shutil.copyfile(source, output / source.name)
            assets.append(source.name)
    shutil.copyfile(project / 'INSTALL.md', output / 'INSTALL.md')
    assets.append('INSTALL.md')
    metadata = {'format': 1, 'version': version, 'tag': tag, 'repository': repository,
                'source_commit': commit, 'binary_kernels': [kernel], 'architecture': 'arm64',
                'files': {name: digest((output / name).read_bytes()) for name in sorted(assets)}}
    (output / 'RELEASE.json').write_text(json.dumps(metadata, indent=2) + '\n')
    assets.append('RELEASE.json')
    (output / 'SHA256SUMS').write_text(''.join(
        f'{digest((output / name).read_bytes())}  {name}\n' for name in sorted(assets)))
    asset = f'cm311-uwe5621ds-{version}-{kernel}.tar.gz'
    notes = f'''# CM311 UWE5621DS {tag}

交付沿用已实测的 v8 驱动：MTU 1400，TX ADDBA 重试间隔 30000 ms。

**本次预编译模块仅适用于 ARM64 / Linux `{kernel}`。**
内核以 `uname -r` 为准，必须完全一致。`6.18.37-ophub` 与 `6.18.56-ophub` 的模块不能互换。
若当前内核没有对应二进制包，请下载源码包在精确头文件上构建，或使用已实测的本机 v8 模块及集成更新包。

| 附件 | 用途 |
| --- | --- |
| `{asset}` | 两个预编译模块、对应源码与安装/回退工具 |
| `cm311-uwe5621ds-{version}-source.tar.gz` | 完整源码，无预编译模块 |
| `cm311-uwe5621ds-{version}-integration.tar.gz` | 给现有 v8 目录更新系统集成工具 |
| `INSTALL.md` | 二进制下载、校验、安装、开机验证与卸载说明 |
| `SHA256SUMS` / 各文件 `.sha256` | 交付文件校验 |
| `RELEASE.json` | 二进制内核列表与源提交身份 |

二进制用户无需安装编译器或内核头文件。保留已有 `/lib/firmware/uwe5621ds`，
下载匹配内核的包及其 `.sha256`，校验后解包并运行 `sudo bash scripts/install.sh`。
安装不会立即重载驱动或重启；在方便时正常重启验证自动加载。
详见附件 INSTALL.md，或 [安装说明](https://github.com/{repository}/blob/{tag}/INSTALL.md#二进制包安装无需编译)。

仍有少量丢包和延迟尖峰；吞吐、长期运行、完整 MTU 1500、休眠恢复及蓝牙尚未验证。
CI 编译成功不代表在该二进制内核上完成了盒子硬件测试。

源提交：`{commit}`。
'''
    (output / 'RELEASE_NOTES.md').write_text(notes)
    return metadata


def publish(output, repository, tag, runner=command):
    """Resume drafts; never clobber an already published release."""
    output = Path(output)
    metadata = json.loads((output / 'RELEASE.json').read_text())
    if (metadata['repository'], metadata['tag']) != (repository, tag):
        raise ValueError('Prepared release identity differs from the publish request')
    assets = []
    for line in (output / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split()
        if not re.fullmatch(r'[A-Za-z0-9._+-]+', name) or (output / name).is_symlink():
            raise ValueError('Invalid prepared release asset path')
        if digest((output / name).read_bytes()) != checksum:
            raise ValueError('Prepared release asset changed: ' + name)
        assets.append(output / name)
    expected = set(metadata['files']) | {'RELEASE.json'}
    if len(assets) != len(expected) or {path.name for path in assets} != expected:
        raise ValueError('Prepared release asset set is incomplete or duplicated')
    assets.append(output / 'SHA256SUMS')
    try:
        existing = json.loads(runner(['gh', 'api', f'repos/{repository}/releases/tags/{tag}']))
    except subprocess.CalledProcessError as exc:
        if 'HTTP 404' not in (exc.stderr or ''):
            raise
        existing = None
    if existing and not existing['draft']:
        print('Release already published; preserving its assets: ' + existing['html_url'])
        return existing['html_url']
    notes = output / 'RELEASE_NOTES.md'
    title = 'CM311 UWE5621DS ' + tag
    if existing is None:
        runner(['gh', 'release', 'create', tag, '--repo', repository, '--verify-tag', '--draft',
                '--target', metadata['source_commit'], '--title', title, '--notes-file', str(notes)])
    else:
        runner(['gh', 'release', 'edit', tag, '--repo', repository, '--draft',
                '--title', title, '--notes-file', str(notes)])
    # Clobber is confined to drafts; failed uploads leave a resumable draft.
    runner(['gh', 'release', 'upload', tag, '--repo', repository, '--clobber', *map(str, assets)])
    runner(['gh', 'release', 'edit', tag, '--repo', repository, '--draft=false'])
    return f'https://github.com/{repository}/releases/tag/{tag}'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate-tag', 'prepare', 'publish'))
    parser.add_argument('--tag', required=True)
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--output', type=Path, default=Path('release-assets'))
    parser.add_argument('--kernel-release')
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', ''))
    parser.add_argument('--commit', default=os.environ.get('GITHUB_SHA', ''))
    args = parser.parse_args(argv)
    project = Path(__file__).resolve().parent.parent
    validate_tag(project, args.tag)
    if args.action == 'prepare':
        if args.inputs is None or not args.kernel_release:
            parser.error('prepare requires --inputs and --kernel-release')
        prepare(project, args.inputs, args.output, args.tag, args.kernel_release, args.repository, args.commit)
    elif args.action == 'publish':
        if (os.environ.get('GITHUB_EVENT_NAME') != 'push' or
                os.environ.get('GITHUB_REF') != 'refs/tags/' + args.tag or not os.environ.get('GH_TOKEN')):
            parser.error('Publishing requires a tag push workflow and GH_TOKEN')
        metadata = json.loads((args.output / 'RELEASE.json').read_text())
        if metadata['source_commit'] != args.commit:
            parser.error('Prepared assets must match the triggering commit')
        print(publish(args.output, args.repository, args.tag))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
