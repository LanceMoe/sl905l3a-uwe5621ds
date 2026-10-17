# CM311-1a YST (S905L3A) — UWE5621DS Wi-Fi 驱动 (Linux 6.18)

[![CI](https://github.com/LanceMoe/sl905l3a-uwe5621ds/actions/workflows/ci.yml/badge.svg)](https://github.com/LanceMoe/sl905l3a-uwe5621ds/actions/workflows/ci.yml)

本项目面向搭载紫光展锐 **UWE5621DS（Marlin3E）** 无线芯片的 **CM311-1a YST（晶晨 S905L3A）** 机顶盒，提供适配主线 **Linux 6.18**（如 Armbian `6.18.x-ophub`）的 Wi-Fi 与 WCN 底层驱动补丁、自动化编译脚本、系统开机自启服务以及故障诊断工具。

当前发行版本为 **0.8.0**（驱动补丁版本 **v8**）。

---

## 目录

1. [硬件支持与当前状态](#1-硬件支持与当前状态)
2. [前置准备（编译与安装前必看）](#2-前置准备编译与安装前必看)
3. [第一步：获取或编译驱动](#3-第一步获取或编译驱动)
   - [方式 A：在盒子上从源码编译（推荐）](#方式-a在盒子上从源码编译推荐)
   - [方式 B：使用预编译二进制包（免编译）](#方式-b使用预编译二进制包免编译)
4. [第二步：安装到系统并配置开机自启](#4-第二步安装到系统并配置开机自启)
5. [第三步：连接与使用 Wi-Fi](#5-第三步连接与使用-wi-fi)
6. [日常维护：状态检查、卸载与内核升级](#6-日常维护状态检查卸载与内核升级)
7. [文档索引与开源许可](#7-文档索引与开源许可)

---

## 1. 硬件支持与当前状态

| 项目 | 已验证配置 |
| --- | --- |
| **设备型号** | CM311-1a YST（SoC：Amlogic S905L3A） |
| **无线芯片** | Unisoc UWE5621DS（Chip ID `0x56630001` / Marlin3E，SDIO 接口） |
| **验证内核** | `6.18.37-ophub`（ARM64，适配 Linux `6.18.x` 系列） |
| **设备树 (DTB)** | `meson-g12a-s905l3a-cm311.dtb` |
| **系统固件路径** | `/lib/firmware/uwe5621ds/`（`wcnmodem.bin` + `wifi_56630001_3ant.ini`） |
| **驱动模块** | `uwe5621_bsp_sdio.ko`（WCN/SDIO 传输层）+ `sprdwl_ng.ko`（Wi-Fi 驱动层） |
| **默认运行参数** | 网卡 MTU `1400`；TX ADDBA 协商重试间隔 `30000 ms` |

### 可用性说明与已知限制

- **已验证功能**：固件下载、芯片校准握手、2.4G/5G 扫描、WPA2/WPA3 关联、DHCP 获取地址、局域网与公网通信。在 v8 实机持续测试中，网关丢包率约 0.56%（平均 RTT 44 ms），公网丢包率约 1.11%（平均 RTT 38 ms），无内核崩溃或固件复位。
- **MTU 1400 限制**：受晶晨 G12A SDIO 控制器 `amlogic,dram-access-quirk` 的 1536 字节单次 CMD53 SRAM 缓冲限制，网卡默认及最大 MTU 设为 **1400**（不支持完整 MTU 1500）。
- **未支持范围**：目前仅支持 Wi-Fi，暂不支持蓝牙与系统休眠恢复（Suspend/Resume）。

---

## 2. 前置准备（编译与安装前必看）

在开始操作前，请先通过有线网络（SSH）登录盒子，确认以下三项条件已满足：

1. **确认内核架构与版本为 Linux 6.18 (ARM64)**：
   ```bash
   uname -m   # 应输出 aarch64
   uname -r   # 应为 6.18.x（例如 6.18.37-ophub）
   ```
2. **确认设备树已正确枚举 SDIO 无线卡**：
   ```bash
   grep -H '^OF_COMPATIBLE_0=sprd,unisoc-wifi$' /sys/bus/sdio/devices/*/uevent
   ```
   若无输出，请检查 `/boot/armbianEnv.txt`（或 uEnv）中是否已指定正确的 DTB（如 `meson-g12a-s905l3a-cm311.dtb`）。
3. **确认系统自带固件文件存在且非空**：
   本项目不重新分发芯片专有固件，依赖原系统已有的固件与校准文件：
   ```bash
   ls -lh /lib/firmware/uwe5621ds/wcnmodem.bin /lib/firmware/uwe5621ds/wifi_56630001_3ant.ini
   ```
   > **提示**：驱动会根据硬件读取到的真实 Chip ID（`0x56630001`）和天线数自动加载 `wifi_56630001_3ant.ini`，请勿使用 UWE5622 的单天线配置文件冒充。

---

## 3. 第一步：获取或编译驱动

Linux 内核模块（`.ko`）与内核版本严格绑定。你可以选择 **在盒子上从源码编译**（通用性最好），或者在内核版本完全一致时直接 **下载预编译二进制包**。

### 方式 A：在盒子上从源码编译（推荐）

如果你的盒子运行的是 `6.18.37-ophub` 或其他 `6.18.x` 内核，只要系统中安装了与当前运行内核精确匹配的内核头文件，即可在盒子上直接编译。

#### 1. 安装基础依赖并检查内核头文件

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends build-essential git python3 kmod iw curl xz-utils

# 检查当前运行内核的头文件与符号表是否完整（无报错即表示正常）
test -s "/lib/modules/$(uname -r)/build/Module.symvers"
```

> **注意**：`/lib/modules/$(uname -r)/build` 必须指向包含 `include/generated/autoconf.h` 和 `Module.symvers` 的完整头文件目录，仅有空符号链接或安装其他小版本的通用头文件无法通过 ABI 校验。

#### 2. 获取项目源码

克隆本仓库（或解压 Release 中的 `*-source.tar.gz` 源码包）并进入目录：

```bash
git clone https://github.com/LanceMoe/sl905l3a-uwe5621ds.git
cd sl905l3a-uwe5621ds
```

#### 3. 下载编译器并构建驱动模块

ophub 内核通常由较新版本的 GCC 构建，系统自带的 GCC 可能因不识别新编译选项而报错。建议运行脚本获取经过 SHA-256 校验的 Arm GNU 15.3 工具链（约 160 MB，缓存在 `.cache/toolchain` 中可反复复用）：

```bash
# 下载 Arm GNU 15.3 编译器
bash scripts/fetch-toolchain.sh

# 编译驱动模块
CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
```

编译完成后，会在 `dist/$(uname -r)/` 下生成两个内核模块及校验清单：
- `dist/$(uname -r)/uwe5621_bsp_sdio.ko`（底层 WCN / SDIO 驱动）
- `dist/$(uname -r)/sprdwl_ng.ko`（上层 Wi-Fi 驱动）

*(注：`build.sh` 仅负责编译，不会自动加载模块。如果想在正式安装前临时加载测试一次，可执行 `sudo bash scripts/load-test.sh`。)*

---

### 方式 B：使用预编译二进制包（免编译）

如果你不想安装编译器和内核头文件，可以查看 [GitHub Releases](https://github.com/LanceMoe/sl905l3a-uwe5621ds/releases) 中是否有与你当前 `uname -r` **完全一致**的二进制包（文件名格式为 `cm311-uwe5621ds-<版本>-<完整内核版本>.tar.gz`）。

- **版本必须严格一致**：目前 CI 默认构建的目标是 `6.18.56-ophub`。如果你的盒子是 `6.18.37-ophub`，不能混用 `6.18.56-ophub` 的模块，请使用上方**方式 A** 编译。
- 若存在匹配你内核版本的二进制包，直接下载、校验并解压后即可跳到**第二步**执行 `sudo bash scripts/install.sh`。一键下载安装脚本见 [INSTALL.md](INSTALL.md#二进制包安装无需编译)。

---

## 4. 第二步：安装到系统并配置开机自启

当 `dist/$(uname -r)/` 目录下已有匹配的 `.ko` 模块后，在项目根目录执行：

```bash
sudo bash scripts/install.sh
```

### 安装脚本做了什么？

1. **严格校验**：核对当前运行内核版本、`.ko` 模块的 ARM64 架构与 `vermagic`、SHA-256 校验和、补丁版本标记以及 `/lib/firmware/uwe5621ds/` 固件文件。
2. **安装模块与参数**：
   - 将两个 `.ko` 复制到 `/lib/modules/$(uname -r)/updates/cm311/` 并执行 `depmod`。
   - 写入 `/etc/modprobe.d/cm311-wifi.conf`：将日常日志级别设为 `WARN`（`log_level=2`，防止高频包日志磨损存储），并将 `tx_addba_retry_ms` 设为 `30000`。
3. **注册开机服务**：
   - 安装加载器 `/usr/local/libexec/cm311-wifi-load` 与系统服务 `/etc/systemd/system/cm311-wifi.service`。
   - 开机时在 `NetworkManager` 启动前按顺序加载 `cfg80211` → `uwe5621_bsp_sdio` → `sprdwl_ng`，并确认平台驱动实际完成绑定。
4. **保存回退基线**：在 `/var/lib/cm311-wifi/` 记录安装事务与原文件备份，以便随时干净卸载。

> **重要说明**：为保证状态干净，`install.sh` **不会**在安装瞬间动态卸载/重载驱动，也**不会**自动重启盒子。请勿自行再添加 `rc.local` 或 `/etc/modules-load.d/` 项以免重复加载。

安装完成后，请**正常重启一次盒子**让服务在开机时自动加载驱动：

```bash
sudo reboot
```

---

## 5. 第三步：连接与使用 Wi-Fi

重启后重新登录盒子，按以下步骤确认驱动状态并连接 Wi-Fi。

### 5.1 确认驱动已成功加载并生成 `wlan0`

```bash
# 1. 查看开机加载服务状态（应显示 active (exited)）
systemctl status cm311-wifi.service --no-pager

# 2. 查看无线网卡接口（应能看到 wlan0）
iw dev
```

### 5.2 扫描并连接 Wi-Fi 网络

Armbian 默认使用 `NetworkManager` 管理网络，推荐直接使用 `nmcli` 命令扫描并连接：

```bash
# 1. 扫描附近的 2.4G / 5G Wi-Fi 热点
nmcli device wifi list ifname wlan0

# 2. 连接指定的 Wi-Fi（加 --ask 会交互式提示输入密码，避免密码留在命令历史中）
sudo nmcli --ask device wifi connect "你的Wi-Fi名称(SSID)" ifname wlan0
```

### 5.3 配置 MTU 1400、关闭省电与开机自动重连（重要）

连接成功后，建议固定该 Wi-Fi 配置的 **MTU 为 1400**、**关闭 802.11 协议栈省电模式**，并开启**开机自动连接**：

```bash
# 1. 查看已保存的 Wi-Fi 连接名称 (NAME) 与 UUID
nmcli -f NAME,UUID,TYPE,DEVICE connection show

# 2. 修改该连接的参数（请将 <你的连接UUID> 替换为上一步查到的实际 UUID）
sudo nmcli connection modify uuid "<你的连接UUID>" \
    connection.autoconnect yes \
    802-11-wireless.mtu 1400 \
    802-11-wireless.powersave 2

# 3. 重新激活该连接使配置立即生效
sudo nmcli connection up uuid "<你的连接UUID>" ifname wlan0
```

> **参数说明**：
> - `802-11-wireless.mtu 1400`：配合驱动的 1400 字节上限，确保本机 IP 分片与 TCP MSS 协商不会触发超过 1536 字节的 SDIO 单次 CMD53 传输。
> - `802-11-wireless.powersave 2`：`2` 代表关闭（disable）802.11 无线省电，减少省电状态切换带来的延迟波动。

### 5.4 验证无线网络连通性

因为盒子可能同时插着网线（`eth0`），测试时请使用 `-I wlan0` 指定通过无线网卡发包：

```bash
# 查看当前关联的 AP 信号与速率
iw dev wlan0 link

# 查看 wlan0 获取到的 IPv4 地址
ip -4 addr show dev wlan0

# 确认模块参数已生效（应输出 30000）
cat /sys/module/sprdwl_ng/parameters/tx_addba_retry_ms

# 指定从 wlan0 网卡 ping 外网测试连通性
ping -I wlan0 -c 4 -W 2 1.1.1.1
```

配置完成后，后续每次开机 `cm311-wifi.service` 会自动加载驱动，`NetworkManager` 会自动连接该 Wi-Fi，无需再次手动操作。

---

## 6. 日常维护：状态检查、卸载与内核升级

### 查看开机加载日志与故障诊断

如果开机后未出现 `wlan0` 或连接异常，请勿反复手动 `rmmod` / `modprobe`，可先查看服务日志或一键生成诊断快照包：

```bash
# 查看本次启动的服务日志
journalctl -u cm311-wifi.service -b --no-pager

# 一键采集当前内核、SDIO 计数、固件与网络状态快照（输出到 dist/ 目录）
bash scripts/diagnose.sh --snapshot
```

更多链路探测与压测工具用法请参见 [DIAGNOSTICS.md](DIAGNOSTICS.md)。

### 卸载驱动与回退

如需卸载本项目安装的模块与开机服务，在项目目录执行：

```bash
sudo bash scripts/uninstall.sh
```

卸载器会校验受管文件是否被手动修改，恢复首次安装前的备份并移除 `cm311-wifi.service`，同时保留系统固件和已保存的 `NetworkManager` Wi-Fi 密码配置。正常重启后旧模块即不再加载。

### 升级系统内核后的处理

本项目按精确内核版本安装，**不使用 DKMS**。如果升级了盒子的内核（例如从 `6.18.37-ophub` 升级到新的 `6.18.x-ophub`），旧内核目录下的 `.ko` 不会在新内核中生效。请在新内核对应的头文件准备好后，重新执行一次编译与安装：

```bash
CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
sudo bash scripts/install.sh
```

---

## 7. 文档索引与开源许可

- **[安装与系统集成说明 (INSTALL.md)](INSTALL.md)**：预编译二进制包安装、已有环境更新、离线 Armbian 根文件系统（rootfs）集成及安装文件清单。
- **[诊断与链路测试工具 (DIAGNOSTICS.md)](DIAGNOSTICS.md)**：`diagnose.sh` 现场采集与 `test-link.sh` 多包长/持续稳定性/ADDBA 对照测试说明。
- **[移植过程与 v1–v8 实验记录 (docs/PORTING.md)](docs/PORTING.md)**：从零移植 UWE5621DS 到主线 Linux 6.18 的全部补丁机制分析，以及 v1 至 v8 八轮实机排障与测试数据汇总。
- **[CI 与自动发布说明 (docs/CI.md)](docs/CI.md)**：GitHub Actions 交叉编译、ASan/UBSan 单元测试与 Release 打包流程。
- **[更新日志 (CHANGELOG.md)](CHANGELOG.md)**：版本变更记录。

### 开源许可与来源

- 上游驱动源码来自 [armbian/uwe5622](https://github.com/armbian/uwe5622/tree/fa9d1a6703a93a85bcf68e865ebc2726474076ad)（固定提交 `fa9d1a6703a93a85bcf68e865ebc2726474076ad`）。
- 本项目编写的补丁、构建/安装脚本、测试与文档采用 **GPL-2.0-only** 许可证，详见 [LICENSE](LICENSE) 与 [第三方来源说明 (THIRD_PARTY.md)](THIRD_PARTY.md)。

