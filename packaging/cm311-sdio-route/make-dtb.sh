#!/usr/bin/env bash
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $# != 2 ]]; then
    echo 'Usage: bash make-dtb.sh ORIGINAL.dtb ROUTED.dtb' >&2
    exit 2
fi
original=$1
output=$2
[[ ! -e $output ]] || { echo "Output already exists: $output" >&2; exit 1; }

sdio=$(fdtget "$original" /__symbols__ sd_emmc_a)
sdcard=$(fdtget "$original" /__symbols__ sd_emmc_b)
if [[ $(fdtget "$original" / model) != CM311-1a-YST ||
      $sdio != /soc/mmc@ffe03000 || $sdcard != /soc/mmc@ffe05000 ||
      $(fdtget "$original" /aliases mmc2) != /soc/mmc@ffe07000 ||
      $(fdtget "$original" "$sdio/wifi@1" compatible) != sprd,unisoc-wifi ]]; then
    echo 'Expected the CM311-1a-YST device tree with Wi-Fi on SD_EMMC_A.' >&2
    exit 1
fi
fdtget -p "$original" "$sdio" | grep -Fx amlogic,dram-access-quirk >/dev/null

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
dtc -@ -I dts -O dtb -o "$work/route.dtbo" "$here/cm311-sdio-route.dtso"
fdtoverlay -i "$original" -o "$work/routed.dtb" "$work/route.dtbo"
routed=$work/routed.dtb

for name in vmmc-supply vqmmc-supply mmc-pwrseq max-frequency; do
    cell=$(fdtget -t x "$original" "$sdio" "$name")
    [[ $cell =~ ^[0-9a-f]+$ ]]
    fdtput -t x "$routed" "$sdcard" "$name" "$cell"
done
for name in cd-gpios pinctrl-1; do
    if fdtget "$routed" "$sdcard" "$name" >/dev/null 2>&1; then
        fdtput -d "$routed" "$sdcard" "$name"
    fi
done
fdtput -t s "$routed" "$sdio" status disabled
fdtput -t s "$routed" "$sdcard" status okay
fdtput -t s "$routed" /aliases mmc0 "$sdcard"
fdtput -t s "$routed" /aliases mmc1 "$sdio"

cp -- "$routed" "$output"
