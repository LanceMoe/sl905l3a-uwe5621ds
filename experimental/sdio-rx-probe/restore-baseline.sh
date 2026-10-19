#!/usr/bin/env bash
# Restore next-boot configuration only. Never unload a running WCN driver.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
kernel=6.18.37-ophub
[[ $(uname -r) == "$kernel" ]] || exit 1
backup=/var/lib/cm311-wifi/recovery-20261018-rx-candidate/uwe5621_bsp_sdio.ko
target=/lib/modules/$kernel/updates/cm311/uwe5621_bsp_sdio.ko
[[ -s $backup && -f $target ]] || exit 1
modinfo -F vermagic "$backup" | grep -q "^$kernel "
archive=/var/lib/cm311-wifi/failed-sram-candidate
mkdir -p "$archive"
if [[ ! -e $archive/uwe5621_bsp_sdio.ko ]]; then
    cp -a "$target" "$archive/uwe5621_bsp_sdio.ko"
fi
systemctl disable cm311-sdio-rx.service
dropin=/etc/systemd/system/cm311-wifi.service.d/10-sdio-rx.conf
if [[ -f $dropin ]]; then
    mv "$dropin" "$archive/10-sdio-rx.conf"
fi
cp -a "$backup" "$target"
cmp "$backup" "$target"
depmod -a "$kernel"
systemctl daemon-reload
echo 'Baseline BSP and boot configuration restored; running modules unchanged. Reboot required.'
