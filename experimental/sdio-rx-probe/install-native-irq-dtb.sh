#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 && $(uname -r) == 6.18.37-ophub ]] || exit 1
target=/boot/dtb/amlogic/meson-g12a-s905l3a-cm311.dtb
candidate=/root/sl905l3a-uwe5621ds/dist/e900v22c-comparison/cm311-native-irq.dtb
backup=/var/lib/cm311-wifi/recovery-20261018-native-irq
[[ $(sha256sum "$target" | cut -d' ' -f1) == c43064088e418574efa4b1e3c56c15ac53fc751555e656357409372806cb58fa ]]
[[ $(sha256sum "$candidate" | cut -d' ' -f1) == c13b8e97edc469b23c5acbab7263976316b2954a77c40bbac47b789c95a0af6b ]]
grep -qx 'FDT=/dtb/amlogic/meson-g12a-s905l3a-cm311.dtb' /boot/uEnv.txt
mkdir -p "$backup"
[[ ! -e $backup/cm311-before-native-irq.dtb ]] || cmp "$target" "$backup/cm311-before-native-irq.dtb"
cp -a "$target" "$backup/cm311-before-native-irq.dtb"
cp -a /boot/uEnv.txt "$backup/uEnv.txt"
cp "$candidate" "$target.native-irq-new"
cmp "$candidate" "$target.native-irq-new"
mv "$target.native-irq-new" "$target"
sync
cmp "$candidate" "$target"
echo 'Installed one-property native SDIO IRQ DTB. Backup verified. Normal reboot required.'
