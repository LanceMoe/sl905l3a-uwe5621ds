# CM311-1a YST / UWE5621DS Wi-Fi 驱动

[![CI](https://github.com/LanceMoe/sl905l3a-uwe5621ds/actions/workflows/ci.yml/badge.svg)](https://github.com/LanceMoe/sl905l3a-uwe5621ds/actions/workflows/ci.yml)

面向 CM311-1a YST（S905L3A）的紫光展锐 UWE5621DS，提供主线 Linux 6.18 的
Wi-Fi/WCN 驱动补丁、构建、系统安装和诊断工具。交付版本 **0.8.0**，驱动版本 **v8**。

## 支持范围

| 项目 | 已验证配置 |
| --- | --- |
| 盒子 | CM311-1a YST / S905L3A |
| 芯片 | UWE5621DS，Chip ID 0x56630001 / Marlin3E |
| 实机内核 | 6.18.37-ophub，ARM64 |
| DTB | meson-g12a-s905l3a-cm311.dtb |
| 固件目录 | /lib/firmware/uwe5621ds |
| 配置文件 | wifi_56630001_3ant.ini，由实际芯片/天线查询选择 |
| 默认参数 | MTU 1400；TX ADDBA 重试间隔 30000 ms |

已完成初始化、扫描、关联、DHCP、网关/公网 ICMP 和运行时参数对照。
三十秒重试阶段，三分钟网关丢包 0.56%、平均 RTT 44 ms，公网丢包 1.11%、平均 RTT 38 ms；
测试期间未出现新的 SDIO 包错误、主机命令超时或 CP2 复位。
仍有少量丢包和延迟尖峰，尚未验证吞吐、长期运行、休眠恢复和蓝牙；不支持完整 MTU 1500。
详细数据见 [v8 实机验证](docs/v8-validation.md)。

## 集成到现有系统

如果已经在盒子上成功构建并测试 v8，使用现有 `dist/$(uname -r)` 模块即可，
安装工具会验证内核、模块架构、源码标记及 SHA-256：

```bash
sudo bash scripts/install.sh
```

安装到当前内核的 `updates/cm311`，写入 modprobe 参数，启用 `cm311-wifi.service`。
安装过程不加载/卸载模块，不重启，不修改固件或 NetworkManager 连接。
下次正常开机由独立服务按依赖加载，NetworkManager 使用已有连接自动联网。
完整步骤、离线镜像集成、内核升级和回退见 **[INSTALL.md](INSTALL.md)**。

## 从源码构建

目标盒子必须有与运行内核精确匹配的已准备头文件和 `Module.symvers`。
普通发行版同版本头文件或只有 `build` 符号链接都不够。

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends build-essential git python3 kmod iw curl xz-utils
bash scripts/fetch-toolchain.sh
CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
sudo bash scripts/install.sh
```

可以复用此前下载的 Arm GNU 15.3 编译器，设置同一个 `CROSS_COMPILE` 前缀。
从 Git 克隆时首次准备会获取固定上游提交；源码发行包已包含应用补丁的驱动源码。
默认输出 `dist/$(uname -r)/uwe5621_bsp_sdio.ko` 和 `sprdwl_ng.ko`。

## 开机验证与卸载

正常重启后检查：

```bash
systemctl status cm311-wifi.service --no-pager
journalctl -u cm311-wifi.service -b --no-pager
iw dev wlan0 link
cat /sys/module/sprdwl_ng/parameters/tx_addba_retry_ms
```

回退：

```bash
sudo bash scripts/uninstall.sh
```

卸载根据安装记录恢复原文件，保留固件、连接配置和备份；已加载模块在正常重启后退出。
检测到手工修改过的受管文件时，先停止操作并提示冲突，避免覆盖用户修改。

## GitHub CI 与交付文件

Push、Pull Request 和手动运行会检查 Shell/Python/工作流、运行故障注入及安装回退测试，
并实际交叉编译两个 ARM64 模块。默认编译目标固定为 `6.18.56-ophub`，下载归档与工具链均校验 SHA-256。
它是编译回归目标，不能把该内核的模块装到 `6.18.37-ophub`。
手动工作流支持提供其他 6.18 内核的精确头文件 URL、版本及校验和。

Actions 产物包含源码包、集成更新包、匹配内核的模块包及日志。
模块包同时包含对应源码；固件及本地诊断日志不会打包。
仓库中的 CI 需要提交并推送后才会在 GitHub 执行，不自动创建 Release。
配置、复现和打包说明见 [CI 与发行](docs/CI.md)。

## 诊断与来源

- [一次采集诊断包](DIAGNOSTICS.md)：`bash scripts/diagnose.sh --snapshot`。
- [移植过程与故障修复](docs/PORTING.md)。
- [三阶段聚合协商对照](docs/v8-addba-comparison.md)。
- 上游：[armbian/uwe5622](https://github.com/armbian/uwe5622/tree/fa9d1a6703a93a85bcf68e865ebc2726474076ad)，固定提交 `fa9d1a6703a93a85bcf68e865ebc2726474076ad`。

本项目补丁、脚本和文档采用 GPL-2.0-only，保留上游文件各自的版权和许可证。
参见 [LICENSE](LICENSE) 与 [第三方来源](THIRD_PARTY.md)。固件由已有系统提供，不在本项目中重新分发。
