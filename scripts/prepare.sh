#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
revision=fa9d1a6703a93a85bcf68e865ebc2726474076ad
source_dir="$root/build/uwe5622"
patch_file="$root/patches/0001-mainline-meson-uwe5621.patch"
patch_hash=$(sha256sum "$patch_file" | cut -d ' ' -f 1)
expected="$revision $patch_hash"

if [[ -f "$source_dir/.cm311-source" ]]; then
    [[ $(cat "$source_dir/.cm311-source") == "$expected" ]] || {
        echo 'Source/patch version changed. Use a new checkout or move build/uwe5622 aside.' >&2
        exit 1
    }
    echo "Prepared source: $source_dir"
    exit 0
fi
if [[ -e "$source_dir" ]]; then
    echo "Refusing to overwrite an existing unmarked directory: $source_dir" >&2
    exit 1
fi

mkdir -p "$root/build"
staging=$(mktemp -d "$root/build/.prepare.XXXXXX")
trap 'rm -rf -- "$staging"' EXIT
git -C "$staging" init -q
git -C "$staging" -c core.autocrlf=false fetch --depth 1 \
    https://github.com/armbian/uwe5622.git "$revision"
git -C "$staging" -c core.autocrlf=false checkout -q --detach FETCH_HEAD
[[ $(git -C "$staging" rev-parse HEAD) == "$revision" ]]
git -C "$staging" apply --check "$patch_file"
git -C "$staging" apply "$patch_file"
printf '%s\n' "$expected" > "$staging/.cm311-source"
mv -- "$staging" "$source_dir"
echo "Prepared source: $source_dir"
