#!/usr/bin/env bash
# Read-only snapshot: safe to run from another SSH session during a blocked load.
set -uo pipefail
printf '=== Kernel ===\n'
uname -a
printf '\n=== Power management and debug configuration ===\n'
config="/lib/modules/$(uname -r)/build/include/generated/autoconf.h"
if [[ -r $config ]]; then
    grep -E '^#define CONFIG_(PM(_SLEEP)?|DEBUG_FS|DEBUG_FS_ALLOW_ALL) ' "$config" || true
else
    printf 'Cannot read %s\n' "$config"
fi
printf '\n=== SDIO trace control ===\n'
if [[ -e /sys/kernel/debug/sdiohal_debug/at_cmd ]]; then
    ls -l /sys/kernel/debug/sdiohal_debug/at_cmd
else
    printf 'SDIO debugfs control is not visible.\n'
fi
printf '\n=== SDIO devices ===\n'
for device in /sys/bus/sdio/devices/*; do
    [[ -r "$device/uevent" ]] || continue
    printf '%s\n' "$device"
    cat "$device/uevent"
    if [[ -L "$device/driver" ]]; then
        readlink "$device/driver"
    fi
done
printf '\n=== Transport counters (packet I/O attempts, not bytes) ===\n'
for parameter in /sys/module/uwe5621_bsp_sdio/parameters/sdio_*; do
    [[ -r $parameter ]] || continue
    printf '%s=' "${parameter##*/}"
    cat "$parameter"
done
printf '\n=== Wi-Fi platform binding ===\n'
if [[ -L /sys/bus/platform/drivers/unisoc_wifi/unisoc_wifi ]]; then
    readlink /sys/bus/platform/drivers/unisoc_wifi/unisoc_wifi
else
    printf 'Wi-Fi platform device is not bound.\n'
fi
