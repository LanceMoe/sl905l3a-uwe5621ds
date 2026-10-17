#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
for script in scripts/*.sh packaging/cm311-wifi-load; do
    bash -n "$script"
done
shellcheck scripts/*.sh packaging/cm311-wifi-load
python3 -m compileall -q scripts tests ci
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git diff --check
fi
