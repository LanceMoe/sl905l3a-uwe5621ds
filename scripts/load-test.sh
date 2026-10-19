#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
release=$(uname -r)
modules="$root/dist/$release"
trace=${SDIO_TRACE:-0}
wifi_level=${WIFI_LOG_LEVEL:-3}
[[ $wifi_level =~ ^[0-4]$ ]] || { echo 'WIFI_LOG_LEVEL must be 0..4.' >&2; exit 1; }
case "$trace" in
    0|1) ;;
    *) echo 'SDIO_TRACE must be 0 or 1.' >&2; exit 1 ;;
esac
[[ $EUID == 0 ]] || { echo 'Run with sudo or as root.' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo 'This test requires the ARM64 box.' >&2; exit 1; }
if [[ $trace == 1 ]] && ! grep -qw debugfs /proc/filesystems; then
    echo 'This kernel has no debugfs support; SDIO trace is unavailable.' >&2
    echo 'Run scripts/collect-state.sh and send its output.' >&2
    exit 1
fi
expected=$(python3 "$root/scripts/source-tree.py" --stamp)
if [[ ! -f "$modules/source-stamp" ]] || [[ $(cat "$modules/source-stamp") != "$expected" ]]; then
    echo 'Modules were not built with this driver source. Run scripts/build.sh first.' >&2
    exit 1
fi
[[ -s /lib/firmware/uwe5621ds/wcnmodem.bin ]] || {
    echo 'Missing /lib/firmware/uwe5621ds/wcnmodem.bin' >&2; exit 1;
}
for command in insmod modinfo modprobe iw; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
for module in uwe5621_bsp_sdio uwe5622_bsp_sdio sprdwl_ng sprdbt_tty; do
    [[ ! -d /sys/module/$module ]] || {
        echo "$module is already loaded. Reboot before a fresh test." >&2; exit 1;
    }
done
device_found=false
for device in /sys/bus/sdio/devices/*; do
    [[ -f "$device/uevent" ]] || continue
    if grep -q '^OF_COMPATIBLE_0=sprd,unisoc-wifi$' "$device/uevent" &&
       grep -q '^SDIO_ID=0000:0000$' "$device/uevent"; then
        [[ ! -L "$device/driver" ]] || { echo 'SDIO device is already bound.' >&2; exit 1; }
        device_found=true
    fi
done
[[ $device_found == true ]] || { echo 'Expected CM311 SDIO device not found.' >&2; exit 1; }
for module in uwe5621_bsp_sdio sprdwl_ng; do
    [[ -f "$modules/$module.ko" ]] || { echo "Missing $modules/$module.ko" >&2; exit 1; }
    vermagic=$(modinfo -F vermagic "$modules/$module.ko")
    [[ ${vermagic%% *} == "$release" ]] || { echo "Wrong module ABI: $vermagic" >&2; exit 1; }
done

mkdir -p "$root/dist/$release"
suffix=
[[ $trace == 0 ]] || suffix=-trace
log="$modules/load-test$suffix.log"
progress="$modules/load-progress$suffix.log"
state="$modules/load-state$suffix.log"
reader=
finish() {
    result=$?
    trap - EXIT
    if [[ -n $reader ]]; then
        kill "$reader" 2>/dev/null || true
        wait "$reader" 2>/dev/null || true
    fi
    dmesg > "$log" || true
    bash "$root/scripts/collect-state.sh" > "$state" 2>&1 || true
    echo "Kernel log saved to $log"
    echo "Device state saved to $state"
    exit "$result"
}
stage() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" | tee -a "$progress"
}
trap finish EXIT
: > "$progress"
# Record while insmod runs: an EXIT-only snapshot is unavailable if it blocks.
if [[ ${CM311_CAPTURE_EXTERNAL:-0} != 1 ]]; then
    dmesg --follow > "$log" 2>&1 &
    reader=$!
fi
stage '[1/4] Loading cfg80211'
modprobe cfg80211
stage '[2/4] Loading uwe5621_bsp_sdio'
insmod "$modules/uwe5621_bsp_sdio.ko"
if [[ $trace == 1 ]]; then
    stage '[trace] Enabling existing SDIO packet and dispatch logs'
    debugfs=/sys/kernel/debug
    if [[ $(stat -f -c %T "$debugfs") != debugfs ]]; then
        mount -t debugfs debugfs "$debugfs"
    fi
    control="$debugfs/sdiohal_debug/at_cmd"
    [[ -e $control ]] || {
        echo "Missing SDIO debug control: $control" >&2
        exit 1
    }
    # The driver's at_cmd_write handles this locally before its send branch.
    # 1=normal, 2=RX sizes and status, 8=short packet hex dumps; omit list spam.
    # This does not send an AT command, register write, or firmware operation.
    printf 'log_level=11\n' > "$control"
fi
stage '[3/4] Loading sprdwl_ng'
insmod "$modules/sprdwl_ng.ko" "log_level=$wifi_level"
# A successful insmod does not imply that the platform driver's probe succeeded.
[[ -L /sys/bus/platform/drivers/unisoc_wifi/unisoc_wifi ]] || {
    echo "Wi-Fi platform probe failed. Read $log; reboot before retrying." >&2
    exit 1
}
stage '[4/4] Reading wireless interfaces'
iw dev
echo 'Probe completed. Verify scanning, association and traffic next.'
