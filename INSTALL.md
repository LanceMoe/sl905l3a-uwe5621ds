# 安装与系统集成说明

本文档详细说明如何将 CM311-1a YST（S905L3A）的 UWE5621DS Wi-Fi 驱动安装到盒子系统中、配置开机自动加载与卸载回退，以及集成到离线 Armbian 镜像。

若需查看从编译到连接 Wi-Fi 的快速上手流程，请先阅读 **[README.md](README.md)**。

---

## 目录

1. [二进制包安装（无需编译）](#二进制包安装无需编译)
2. [从源码编译并安装](#从源码编译并安装)
3. [安装位置与开机自启机制](#安装位置与开机自启机制)
4. [NetworkManager 自动连接与开机验证](#networkmanager-自动连接与开机验证)
5. [卸载与回退](#卸载与回退)
6. [内核升级说明](#内核升级说明)
7. [集成到离线 Armbian 根文件系统](#集成到离线-armbian-根文件系统)

---

## 二进制包安装（无需编译）

打开 [GitHub Releases](https://github.com/LanceMoe/sl905l3a-uwe5621ds/releases)，先在盒子上确认架构与完整内核版本号：

```bash
uname -m   # 必须为 aarch64
uname -r   # 例如 6.18.37-ophub 或 6.18.56-ophub
```

> **重要提示**：只下载文件名与 `uname -r` **完全一致**的 ARM64 二进制包。例如 `cm311-uwe5621ds-0.9.0-6.18.56-ophub.tar.gz` 只能安装到 `6.18.56-ophub`，**不能**用于 `6.18.37-ophub`。同一 `6.18` 大版本下不同小版本的内核模块不可混用，请勿强行修改 `vermagic`。若 Release 中没有你当前内核的二进制包，请使用下一节的**[从源码编译并安装](#从源码编译并安装)**。

| Release 附件名称 | 用途与选择方式 |
| --- | --- |
| `cm311-uwe5621ds-<版本>-<完整内核版本>.tar.gz` | 预编译 `.ko` 模块、对应驱动源码及安装/卸载工具（免编译） |
| 同名 `.tar.gz.sha256` | 对应压缩包的 SHA-256 校验文件 |
| `cm311-uwe5621ds-<版本>-source.tar.gz` | 完整源码包（已打好补丁），用于在盒子上针对当前内核编译 |
| `cm311-uwe5621ds-<版本>-integration.tar.gz` | 系统集成文件及源码，不含预编译模块 |

二进制安装无需 GCC、make 或内核头文件，只需确保系统具备基础运行工具并保留原有的 `/lib/firmware/uwe5621ds` 固件目录：

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends python3 kmod bash tar curl iw
```

当 Release 中有匹配你当前 `uname -r` 的二进制包时，可执行以下脚本自动下载、校验并安装（按需修改 `version`）：

```bash
bash <<'SH'
set -euo pipefail
version=0.9.0
repo=LanceMoe/sl905l3a-uwe5621ds
kernel=$(uname -r)
if [[ $(uname -m) != aarch64 ]]; then
    echo '二进制包仅用于 ARM64 盒子。' >&2
    exit 1
fi
file="cm311-uwe5621ds-${version}-${kernel}.tar.gz"
base="https://github.com/${repo}/releases/download/v${version}"
work=$(mktemp -d "$HOME/cm311-binary.XXXXXX")
cd "$work"
curl --fail --location --retry 3 "$base/$file" --output "$file"
curl --fail --location --retry 3 "$base/$file.sha256" --output "$file.sha256"
sha256sum --check "$file.sha256"
tar -xzf "$file"
cd cm311-uwe5621ds
sudo bash scripts/install.sh
printf '已安装；项目及卸载工具保留在：%s\n' "$PWD"
SH
```

也可以在电脑浏览器下载匹配的 `.tar.gz` 和 `.tar.gz.sha256` 上传到盒子，执行 `sha256sum -c *.sha256` 校验通过后解压，进入目录运行 `sudo bash scripts/install.sh`。

---

## 从源码编译并安装

当盒子内核（例如 `6.18.37-ophub`）没有现成的预编译二进制包时，克隆本仓库或解压 `cm311-uwe5621ds-0.9.0-source.tar.gz` 后，在盒子上执行：

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends build-essential git python3 kmod iw curl xz-utils

# 确认当前内核头文件与 Module.symvers 存在
test -s "/lib/modules/$(uname -r)/build/Module.symvers"

# 下载 Arm GNU 15.3 工具链并编译、安装
bash scripts/fetch-toolchain.sh
CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
sudo bash scripts/install.sh
```

- 如果此前已经下载过 Arm GNU 15.3 工具链，可跳过 `fetch-toolchain.sh`，直接将 `CROSS_COMPILE` 指向已有的 `aarch64-none-linux-gnu-` 路径。
- 安装前要求系统已存在以下非空固件文件（本项目不分发固件）：
  - `/lib/firmware/uwe5621ds/wcnmodem.bin`（实测 SHA-256：`008490d209f00603b163dcdf84f0f5041debba157426e08aa3f9ff07210d2695`）
  - `/lib/firmware/uwe5621ds/wifi_56630001_3ant.ini`

---

## 安装位置与开机自启机制

`sudo bash scripts/install.sh` 会写入并管理以下系统路径：

| 系统路径 | 用途说明 |
| --- | --- |
| `/lib/modules/<内核>/updates/cm311/uwe5621_bsp_sdio.ko` | WCN / SDIO 底层传输模块 |
| `/lib/modules/<内核>/updates/cm311/sprdwl_ng.ko` | Wi-Fi 无线驱动模块 |
| `/etc/modprobe.d/cm311-wifi.conf` | Wi-Fi SDIO 主控设置为 `ffe05000.mmc`，日志级别为 `debug_level=2` |
| `/usr/local/libexec/cm311-wifi-load` | 开机加载脚本：等待 SDIO 设备就绪、按序 `modprobe` 并验证平台绑定 |
| `/etc/systemd/system/cm311-wifi.service` | systemd 服务单元（在本地文件系统就绪后、`NetworkManager` 启动前运行） |
| `/etc/systemd/system/multi-user.target.wants/cm311-wifi.service` | 开机自启符号链接 |
| `/var/lib/cm311-wifi/install.json` | 安装事务清单（记录版本、内核、受管文件哈希与备份索引） |
| `/var/lib/cm311-wifi/backups/` | 首次安装前同名原始文件的备份目录 |

### 设计说明

- **安全安装**：安装过程仅写入文件、执行 `depmod` 和 `systemctl daemon-reload`，**不会**立即卸载/加载当前内核模块，也**不会**自动重启或修改 DTB、固件和网络连接配置。
- **可靠的开机加载顺序**：下次开机时，`cm311-wifi.service` 先最多只读等待 5 秒确认 SDIO 总线上出现 `sprd,unisoc-wifi` SDIO 卡，随后按顺序加载 `cfg80211` → `uwe5621_bsp_sdio` → `sprdwl_ng`，并检查 `/sys/bus/platform/drivers/unisoc_wifi/unisoc_wifi` 确实完成绑定后才算成功。
- **不阻塞有线网络**：服务超时设为 45 秒，失败时不反复无限重载，也不会通过强依赖（`Requires`）阻塞 `NetworkManager` 管理有线网卡 `eth0`。
- **避免重复配置**：请勿再额外配置 `/etc/modules-load.d/`、`rc.local` 或其他自定义加载脚本；如有旧的手写加载脚本请先停用。

---

## NetworkManager 自动连接与开机验证

### 切换到 SD_EMMC_B

CM311-1a YST 从 eMMC 启动时，可将内置 Wi-Fi 改接 SD_EMMC_B，使用控制器的 DMA 传输。重做版按此配置验证。SD_EMMC_B 专用于 Wi-Fi，原有 SD 卡槽将停用。已使用 `ffe05000.mmc` 的设备可直接安装驱动。

先按本文安装同一内核版本的两个 Wi-Fi 模块。在源码目录使用同一套编译器和头文件编译引脚复用模块。生成设备树需要 `device-tree-compiler`。

```bash
sudo apt-get install --no-install-recommends device-tree-compiler
release=$(uname -r)
compiler=${CROSS_COMPILE:-"$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-"}
mkdir -p build/cm311-sdio-route
make -C "/lib/modules/$release/build" ARCH=arm64 CROSS_COMPILE="$compiler" \
    KERNELRELEASE="$release" \
    M="$PWD/packaging/cm311-sdio-route" MO="$PWD/build/cm311-sdio-route" modules
sudo install -m 0644 build/cm311-sdio-route/cm311-sdio-route.ko \
    "/lib/modules/$release/updates/cm311/"
sudo depmod -a "$release"
echo cm311_sdio_route | sudo tee /etc/modules-load.d/cm311-sdio-route.conf

dtbs=/boot/dtb/amlogic
sudo bash packaging/cm311-sdio-route/make-dtb.sh \
    "$dtbs/meson-g12a-s905l3a-cm311.dtb" \
    "$dtbs/meson-g12a-s905l3a-cm311-sd-emmc-b.dtb"
```

备份 `/boot/uEnv.txt`，将其中的 `FDT=` 改为 `FDT=/dtb/amlogic/meson-g12a-s905l3a-cm311-sd-emmc-b.dtb` 后重启。用 `readlink -f /sys/class/mmc_host/mmc0` 确认 Wi-Fi 已切到 `ffe05000.mmc`，并用 `ip link show wlan0` 确认 MTU 为 `1500`。为新内核重新编译 Wi-Fi 模块时，请一并编译并安装此引脚复用模块。

要恢复 SD_EMMC_A，将 `FDT=` 改回原设备树，移除 `/etc/modules-load.d/cm311-sdio-route.conf`，将保存的 Wi-Fi MTU 配置改回 `1400`，并安装与该设备树匹配的旧版驱动，然后重启。

### 1. 连接并配置 MTU 与省电关闭

首次连接 Wi-Fi 可使用：

```bash
sudo nmcli --ask device wifi connect "你的SSID" ifname wlan0
```

查看已保存连接的 UUID，关闭 802.11 省电（`powersave 2`）并开启自动连接。SD_EMMC_B 可使用 MTU `1500`。

```bash
nmcli -f NAME,UUID,TYPE,DEVICE connection show

sudo nmcli connection modify uuid "你的连接UUID" \
    connection.autoconnect yes \
    802-11-wireless.mtu 1500 \
    802-11-wireless.powersave 2
```

### 2. 重启验证

正常重启盒子后，可通过以下命令确认开机自启与无线通信正常：

```bash
systemctl is-enabled cm311-wifi.service
systemctl status cm311-wifi.service --no-pager
journalctl -u cm311-wifi.service -b --no-pager
modinfo -n sprdwl_ng
iw dev wlan0 link
ip -4 address show dev wlan0
cat /sys/module/uwe5621_bsp_sdio/parameters/sdio_host
ping -I wlan0 -c 4 -W 2 1.1.1.1
```

预期 `cm311-wifi.service` 状态为 `active (exited)`，模块路径位于当前内核的 `updates/cm311/`，SDIO 主控参数为 `ffe05000.mmc`。若开机加载失败，请运行 `bash scripts/diagnose.sh --snapshot` 保存诊断包。

---

## 卸载与回退

在项目目录中执行：

```bash
sudo bash scripts/uninstall.sh
```

- 卸载脚本会先校验受管文件的 SHA-256 是否与 `install.json` 一致，随后恢复首次安装前备份的原文件（或删除原本不存在的文件）。
- 若检测到受管文件被用户手动修改过，脚本会停止操作并列出冲突文件，防止误删用户修改。
- 卸载不会触碰 `/lib/firmware/uwe5621ds` 固件、不会删除 `NetworkManager` 保存的 Wi-Fi 连接配置，也不会在运行中强行 `rmmod` 当前已加载的模块（正常重启后即不再加载）。

---

## 内核升级说明

本方案按精确内核版本安装，**不使用 DKMS**。升级到新的 Linux `6.18.x` 内核后，旧内核目录中的 `.ko` 无法直接用于新内核。

在切换或升级到新内核时，请准备好与新内核精确匹配的头文件，构建并安装对应版本的模块：

```bash
KERNEL_RELEASE="新6.18内核版本" KDIR="/对应的新头文件目录" \
    CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
sudo bash scripts/install.sh --kernel-release "新6.18内核版本"
```

安装事务记录支持同时保留多个 `6.18.x` 内核的模块，开机时会自动加载当前运行内核目录下的对应模块。

---

## 集成到离线 Armbian 根文件系统

如果你在 Linux 构建机上定制 Armbian 镜像，可以先交叉编译出目标内核的模块，然后通过 `--root` 将模块与开机服务直接写入挂载的离线根文件系统（rootfs）：

```bash
sudo bash scripts/install.sh \
    --root /mnt/armbian-rootfs \
    --kernel-release 6.18.37-ophub \
    --modules "$PWD/dist/6.18.37-ophub"
```

- 目标离线根文件系统中需已包含 `/lib/firmware/uwe5621ds/` 固件及 `/lib/modules/<目标内核>` 目录。
- 离线模式会调用 `depmod -b` 生成模块依赖索引并创建 systemd 启用链接，不会调用宿主机的 `systemctl`，也不会在构建机上加载 ARM64 模块。

