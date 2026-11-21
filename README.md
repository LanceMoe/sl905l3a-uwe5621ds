# CM311-1a YST（S905L3A）UWE5621DS Wi-Fi 驱动

[![CI](https://github.com/LanceMoe/sl905l3a-uwe5621ds/actions/workflows/ci.yml/badge.svg)](https://github.com/LanceMoe/sl905l3a-uwe5621ds/actions/workflows/ci.yml)

本项目为搭载 Unisoc UWE5621DS（Marlin3E）的 CM311-1a YST 提供 Linux 6.18 ARM64 Wi-Fi 驱动。
源码基于 Armbian `uwe5622` 的 `d2d6bc45c5ef9cefdd53e6c51cec3ba948b84972` 重做，
通过 `SD_EMMC_B`（`ffe05000.mmc`）连接无线芯片。当前源码版本为 `0.9.0`。

## 实机结论

- **2.4 GHz 可用。** 重做版在 `6.18.37-ophub` 上完成加载、关联和 DHCP。双向 TCP 各传输 48 MiB，接收数据的 SHA-256 均与发送端一致。
- **5 GHz 可扫描，连接尚未通过。** 锁定同名双频网络的信道 36 后，固件仍关联到 2.4 GHz 的信道 11，随后握手超时。另一个信道 36 的接入点在 WPA2-PSK 握手前返回 `ASSOC-REJECT status_code=10`，WPA3-SAE 尝试也未连上。Windows 能用 WPA3-Personal H2E 连上同一接入点，具体能力差异尚未定位。
- **验证范围。** 实机使用 `SD_EMMC_B` 专供 Wi-Fi。蓝牙、系统挂起恢复及长时间稳定性尚未验证。

## 编译与安装

使用 ARM64 Linux 6.18 及与目标内核完全匹配的头文件和 `Module.symvers`。盒子需已有以下固件与校准文件。

- `/lib/firmware/uwe5621ds/wcnmodem.bin`
- `/lib/firmware/uwe5621ds/wifi_56630001_3ant.ini`

在盒子上通过有线 SSH 确认 SDIO 卡所在的控制器。

```bash
for card in /sys/bus/sdio/devices/*; do
    if grep -qs '^OF_COMPATIBLE_0=sprd,unisoc-wifi$' "$card/uevent"; then
        readlink -f "$card"
    fi
done
```

路径应包含 `ffe05000.mmc`。若仍使用 `ffe03000.mmc`，可在从 eMMC 启动的设备上按安装说明切换控制器。切换会占用 SD 卡槽。[SD_EMMC_B 配置步骤](INSTALL.md#切换到-sd_emmc_b)

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends build-essential git python3 kmod iw curl xz-utils
test -s "/lib/modules/$(uname -r)/build/Module.symvers"
bash scripts/fetch-toolchain.sh
CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
sudo bash scripts/install.sh
```

编译产物是 `dist/$(uname -r)/` 下的 `uwe5621_bsp_sdio.ko` 和 `sprdwl_ng.ko`。从旧版升级时，先移走旧的 `build/uwe5622/` 并重新编译两块模块。安装完成后正常重启，`cm311-wifi.service` 会加载驱动。

## 连接与验证

```bash
systemctl status cm311-wifi.service --no-pager
nmcli device wifi list ifname wlan0
sudo nmcli --ask device wifi connect "你的SSID" ifname wlan0
iw dev wlan0 link
ip -4 addr show dev wlan0
ping -I wlan0 -c 4 -W 2 1.1.1.1
```

二进制包安装、卸载与离线镜像集成见 [安装说明](INSTALL.md)。链路测试与日志采集见 [诊断指南](DIAGNOSTICS.md)。构建与发布流程见 [CI 说明](docs/CI.md)。

## 来源与许可

移植参考了 unifreq 的 Amlogic SDIO 适配和 ophub 的平台及固件选择。[unifreq PR #96](https://github.com/unifreq/linux-6.18.y/pull/96) · [ophub `9bcb8ea`](https://github.com/ophub/linux-6.18.y/commit/9bcb8ea5064a0847e236d50ebcd771084abfb820)

上游代码保留原始版权与许可证声明。项目脚本和文档采用 GPL-2.0-only。[Armbian 上游提交](https://github.com/armbian/uwe5622/commit/d2d6bc45c5ef9cefdd53e6c51cec3ba948b84972) · [第三方来源](THIRD_PARTY.md) · [许可证](LICENSE)
