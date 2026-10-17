#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
case $(uname -m) in
    aarch64)
        host=aarch64
        checksum=8020be7aa1013704756158400cb5ba438d6f09b2909795d13617b9e7bac53286
        ;;
    x86_64)
        host=x86_64
        checksum=ea12d99f87848009270277afe5d03fac873cb869784a39387183835f743014d9
        ;;
    *) echo 'Toolchain is available for ARM64 and x86_64 Linux hosts.' >&2; exit 1 ;;
esac
target="$root/.cache/toolchain"
if [[ -f "$target/.cm311-toolchain" ]] && [[ $(cat "$target/.cm311-toolchain") == "$checksum" ]]; then
    "$target/bin/aarch64-none-linux-gnu-gcc" --version | head -n 1
    exit 0
fi
[[ ! -e "$target" ]] || { echo "Refusing to overwrite $target" >&2; exit 1; }
mkdir -p "$root/.cache"
staging=$(mktemp -d "$root/.cache/.toolchain.XXXXXX")
trap 'rm -rf -- "$staging"' EXIT
archive="arm-gnu-toolchain-15.3.rel1-$host-aarch64-none-linux-gnu.tar.xz"
curl --fail --location --retry 3 \
    "https://github.com/ophub/kernel/releases/download/toolchain/$archive" \
    --output "$staging/toolchain.tar.xz"
printf '%s  %s\n' "$checksum" "$staging/toolchain.tar.xz" | sha256sum --check
mkdir "$staging/extracted"
tar -xJf "$staging/toolchain.tar.xz" --strip-components=1 -C "$staging/extracted"
"$staging/extracted/bin/aarch64-none-linux-gnu-gcc" --version | head -n 1
printf '%s\n' "$checksum" > "$staging/extracted/.cm311-toolchain"
mv -- "$staging/extracted" "$target"
echo "Compiler prefix: $target/bin/aarch64-none-linux-gnu-"
