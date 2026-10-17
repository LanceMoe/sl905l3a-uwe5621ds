#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
release=${KERNEL_RELEASE:-$(uname -r)}
kdir=${KDIR:-/lib/modules/$release/build}
jobs=${JOBS:-2}

[[ $release == 6.18.* ]] || {
    echo "This experimental port targets Linux 6.18; got $release" >&2
    exit 1
}
[[ -f "$kdir/Makefile" && -f "$kdir/include/generated/autoconf.h" && -s "$kdir/Module.symvers" ]] || {
    echo "Missing prepared headers/Module.symvers: $kdir" >&2
    echo 'Install the headers matching the running ophub kernel; a build symlink alone is insufficient.' >&2
    exit 1
}
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be a positive integer.' >&2; exit 1; }
if [[ -z ${CROSS_COMPILE:-} && $(uname -m) != aarch64 ]]; then
    echo 'Build on the ARM64 box, or supply CROSS_COMPILE, KDIR and KERNEL_RELEASE.' >&2
    exit 1
fi
# ophub's exported headers can omit .config/localversion files. `make
# kernelrelease` then drops -ophub; the generated UTS_RELEASE is authoritative.
actual=$(sed -n 's/^#define UTS_RELEASE "\([^"]*\)"/\1/p' "$kdir/include/generated/utsrelease.h")
[[ $actual == "$release" ]] || {
    echo "Headers are for $actual, requested $release; refusing an ABI mismatch." >&2
    exit 1
}

bash "$root/scripts/prepare.sh"
source_dir="$root/build/uwe5622"
mkdir -p "$root/dist/$release"
compat_flags=$(python3 "$root/scripts/detect-cfg80211.py" "$kdir")
echo "cfg80211 compatibility flags: ${compat_flags:-(legacy API)}"

# Clear other vendor selections even if they are present in the kernel config.
# Do not set CONFIG_WCN_BSP_DRIVER_BUILDIN: both components must be modules.
make -C "$kdir" -j"$jobs" ARCH=arm64 "CROSS_COMPILE=${CROSS_COMPILE:-}" \
    KERNELRELEASE="$release" M="$source_dir" CONFIG_MESON_UWE5621=y \
    CONFIG_AW_WIFI_DEVICE_UWE5622= CONFIG_RK_WIFI_DEVICE_UWE5622= \
    CONFIG_RK_WIFI_DEVICE_UWE5621= CFG_AML_WIFI_DEVICE_UWE5621= \
    CFG_AML_WIFI_DEVICE_UWE5622= CONFIG_WCN_BSP_DRIVER_BUILDIN= \
    CONFIG_WLAN_UWE5622=m CONFIG_TTY_OVERY_SDIO= \
    CM311_COMPAT_CFLAGS="$compat_flags" \
    UNISOC_WIFI_CUS_CONFIG=/lib/firmware/uwe5621ds TARGET_BUILD_VARIANT=user \
    modules 2>&1 | tee "$root/dist/$release/build.log"

cp -- "$source_dir/unisocwcn/uwe5621_bsp_sdio.ko" \
    "$source_dir/unisocwifi/sprdwl_ng.ko" "$root/dist/$release/"
printf '%s\n' "$release" > "$root/dist/$release/kernel-release"
cp -- "$source_dir/.cm311-source" "$root/dist/$release/source-stamp"
(
    cd -- "$root/dist/$release"
    sha256sum -- uwe5621_bsp_sdio.ko sprdwl_ng.ko > SHA256SUMS
)
echo "Built modules (not loaded): $root/dist/$release"
