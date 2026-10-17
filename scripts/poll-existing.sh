#!/usr/bin/env bash
# Inspect the failed v4 probe in place using the vendor's RX polling control.
set -euo pipefail
umask 077
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
control=/sys/kernel/debug/sdiohal_debug/at_cmd
fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run as root.'
[[ $(uname -m) == aarch64 ]] || fail 'This script is for the ARM64 box.'
[[ -f $root/scripts/diagnose.py ]] || fail 'Missing scripts/diagnose.py.'
[[ ! -e /sys/module/uwe5621_bsp_sdio/parameters/sdio_rx_poll_timeouts ]] ||
    fail 'v5 already has automatic RX fallback; use diagnose.sh --snapshot.'
for module in uwe5621_bsp_sdio sprdwl_ng; do
    state=/sys/module/$module/initstate
    [[ -r $state && $(cat "$state") == live ]] ||
        fail "$module must already be loaded and live. Do not load it again for this script."
done
[[ ! -L /sys/bus/platform/drivers/unisoc_wifi/unisoc_wifi ]] ||
    fail 'Wi-Fi is bound; this script is only for the failed probe.'
limit_file=/sys/module/uwe5621_bsp_sdio/parameters/sdio_packet_limit
[[ -r $limit_file ]] || fail 'The loaded BSP lacks the v4 packet limit.'
limit=$(cat "$limit_file")
if [[ ! $limit =~ ^[0-9]+$ ]] || (( limit <= 0 || limit > 1536 )); then
    fail 'Unexpected packet limit; collect with diagnose.sh --snapshot instead.'
fi
taint=$(cat /proc/sys/kernel/tainted)
(( (taint & 128) == 0 )) || fail 'Kernel has an Oops marker; collect a snapshot instead.'
if pgrep -x 'insmod|modprobe' >/dev/null; then
    fail 'A module loader is still running; collect a snapshot instead.'
fi
[[ -w $control ]] || fail 'Existing SDIO debugfs control is unavailable.'
mkdir -p "$root/dist"
progress=$root/dist/poll-existing-progress.log
exec > >(tee -a "$progress") 2>&1
printf '\n%s Polling existing v4 transport; no new probe is attempted.\n' "$(date --iso-8601=seconds)"
printf '\n=== Before polling ===\n'
bash "$root/scripts/collect-state.sh"
# Both strings are handled locally by at_cmd_write; neither sends an AT command.
printf 'log_level=11\n' > "$control"
printf 'switch_irq\n' > "$control"
printf 'RX polling selected. This setting lasts until the driver is reloaded or the box reboots.\n'
sleep 4
printf '\n=== After polling ===\n'
bash "$root/scripts/collect-state.sh"
bash "$root/scripts/diagnose.sh" --snapshot
