#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
target=/boot/dtb/amlogic/meson-g12a-s905l3a-cm311.dtb
backup=/var/lib/cm311-wifi/recovery-20261018-native-irq/cm311-before-native-irq.dtb
[[ $(sha256sum "$backup" | cut -d' ' -f1) == c43064088e418574efa4b1e3c56c15ac53fc751555e656357409372806cb58fa ]]
[[ $(sha256sum "$target" | cut -d' ' -f1) == c13b8e97edc469b23c5acbab7263976316b2954a77c40bbac47b789c95a0af6b ]]
cp "$backup" "$target.baseline-new"
cmp "$backup" "$target.baseline-new"
mv "$target.baseline-new" "$target"
sync
cmp "$backup" "$target"
echo 'Restored original CM311 DTB; normal reboot required.'
