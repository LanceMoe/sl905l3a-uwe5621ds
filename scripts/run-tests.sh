#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
bash "$root/scripts/prepare.sh"
for test in "$root"/tests/test_*.py; do
    python3 "$test"
done
