# 系统集成

交付版本 0.8.0，沿用已实测的 v8 驱动补丁。本次交付增加安装/回退和 CI，没有修改驱动行为。
支持目标为 CM311-1a YST / UWE5621DS / ARM64 / Linux 6.18，实机验证内核为 `6.18.37-ophub`。

## 已经构建并测试过 v8 的盒子

现有盒子无需为安装工具重新编译。将 `cm311-uwe5621ds-0.8.0-integration.tar.gz`
放到 `/root`，在此前 v8 项目目录中覆盖更新项目文件：

```bash
cd /root/cm311-v8/cm311-uwe5621ds
tar -xzf /root/cm311-uwe5621ds-0.8.0-integration.tar.gz --strip-components=1
bash scripts/install.sh
```

归档不包含 `build/`、`.cache/` 或现有 `dist/`，保留此前的模块、工具链和日志。
安装检查当前内核版本、两个模块的名称/ARM64 vermagic、校验和、以及是否来自当前 v8 补丁。
校验不通过时先修复或重新构建，不强行修改模块版本字符串。

## 新系统从源码构建

解包 `cm311-uwe5621ds-0.8.0-source.tar.gz` 并进入 `cm311-uwe5621ds`，或克隆本仓库。
应先按实际设备配置好原有 DTB，保留能联网的有线连接。

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends build-essential git python3 kmod iw curl xz-utils
test -s "/lib/modules/$(uname -r)/build/Module.symvers"
bash scripts/fetch-toolchain.sh
CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
sudo bash scripts/install.sh
```

复用现有编译器时跳过 fetch，设置此前成功使用的 `CROSS_COMPILE` 前缀。
头文件须精确匹配运行的 ophub 内核，构建脚本会检查生成的 `UTS_RELEASE`。
源码包包含固定上游已应用补丁的源码；从 Git 构建则由 `prepare.sh` 获取并应用同一补丁。

安装要求系统已有以下非空文件，且保留其现有内容：

```text
/lib/firmware/uwe5621ds/wcnmodem.bin
/lib/firmware/uwe5621ds/wifi_56630001_3ant.ini
```

实测固件 SHA-256 为
`008490d209f00603b163dcdf84f0f5041debba157426e08aa3f9ff07210d2695`。
驱动仍按实际芯片与天线配置选择 INI；不要用 UWE5622 的单天线文件冒充。
本发行包不携带固件；需要从有使用权的原系统保留该目录。

## 安装到哪里、如何开机加载

| 文件 | 用途 |
| --- | --- |
| `/lib/modules/<内核>/updates/cm311/uwe5621_bsp_sdio.ko` | WCN/SDIO 模块 |
| `/lib/modules/<内核>/updates/cm311/sprdwl_ng.ko` | Wi-Fi 模块 |
| `/etc/modprobe.d/cm311-wifi.conf` | 日志 WARN、ADDBA 重试 30000 ms |
| `/usr/local/libexec/cm311-wifi-load` | 检查并按依赖加载模块，确认平台绑定 |
| `/etc/systemd/system/cm311-wifi.service` | 本地文件系统就绪后、NetworkManager 启动前加载 |
| `/etc/systemd/system/multi-user.target.wants/cm311-wifi.service` | 开机启用链接 |
| `/var/lib/cm311-wifi/install.json` | 版本、内核、受管文件哈希及原文件备份索引 |
| `/var/lib/cm311-wifi/backups/` | 首次安装前同名文件的备份 |

安装执行 `depmod` 和 systemd daemon-reload，通过启用链接配置下次开机加载。
不立即启动服务、不卸载当前模块、不重启、不修改 DTB/内核/固件/网络连接。
日常日志使用 WARN（`log_level=2`），避免 INFO 级别逐包日志长期刷写存储；
诊断脚本会按需临时提高日志级别，结束后恢复原级别。
加载顺序为 `cfg80211` → `uwe5621_bsp_sdio` → `sprdwl_ng`；服务检查平台是否确实绑定，
不把 modprobe 返回成功直接当作 Wi-Fi 可用。服务超时 45 秒，不自动反复重载失败驱动。
启动时只读等待预期 SDIO 设备最多五秒，不触发重枚举或重复 probe。
Wi-Fi 服务失败不会以 Requires 关系阻止 NetworkManager 管理有线网络。

不要再给同一模块添加另一份 modules-load.d、rc.local 或自定义加载服务，避免重复入口。
已有手写启动脚本时应先自行停用该旧入口，再使用这里的服务。

## NetworkManager 自动连接

已有保存的连接和密码继续使用，不需要再次输入密码。先查找所需连接的 UUID：

```bash
nmcli -f NAME,UUID,TYPE connection show
```

把下面的 `你的连接UUID` 替换为实际值；这些命令只修改指定的 Wi-Fi 配置：

```bash
nmcli connection modify uuid '你的连接UUID' connection.autoconnect yes \
    802-11-wireless.mtu 1400 802-11-wireless.powersave 2
```

`powersave 2` 表示关闭 802.11 省电，与已经实测的 off 一致，不代表禁用固件独立休眠。
驱动本身默认/最大 MTU 也是 1400。首次建立连接可使用：

```bash
nmcli --ask device wifi connect '你的SSID' ifname wlan0
```

保存配置后，在方便时正常重启一次，验证完整开机路径。安装工具不会替你重启。

```bash
systemctl is-enabled cm311-wifi.service
systemctl status cm311-wifi.service --no-pager
journalctl -u cm311-wifi.service -b --no-pager
modinfo -n sprdwl_ng
iw dev wlan0 link
ip -4 address show dev wlan0
cat /sys/module/sprdwl_ng/parameters/tx_addba_retry_ms
ping -I wlan0 -c 4 -W 2 1.1.1.1
```

预期服务 active (exited)、模块路径在当前内核的 updates/cm311、参数为 30000。
`iw link` 成功仅证明关联；地址、绑定 wlan0 的通信和日志应一起确认。
如开机失败，在项目中 `bash scripts/diagnose.sh --snapshot` 保存现场，不反复重载。

## 卸载与回退

```bash
cd /root/cm311-v8/cm311-uwe5621ds
bash scripts/uninstall.sh
```

卸载先校验受管文件是否仍与安装记录一致，再恢复首次安装前的原文件或删除原本不存在的文件。
重装/升级保留第一次备份，不会把当前安装错误地变成回退基线。
如果用户改过受管文件，脚本拒绝继续并列出冲突；先保存修改、对照备份处理，不使用通配符删除。
卸载不动固件、NetworkManager 配置或当前加载的模块。正常重启后不再由此服务加载。
备份保留在 `/var/lib/cm311-wifi/backups`，可按需要另行归档。

不建议在正在用 Wi-Fi 的 SSH 会话里手工 rmmod 这组实验驱动。

## 升级内核

本方案按内核版本安装，**不提供 DKMS 自动适配**。升级内核后不能复用旧 `.ko`。
在切换到新内核前，为新内核准备精确头文件，构建并安装对应模块：

```bash
KERNEL_RELEASE='新6.18内核版本' KDIR='/对应的新头文件目录' \
  CROSS_COMPILE='/已使用的编译器前缀/aarch64-none-linux-gnu-' bash scripts/build.sh
bash scripts/install.sh --kernel-release '新6.18内核版本'
```

安装记录可以保留多个 6.18 内核的模块，开机自动使用运行内核目录里的版本。
6.18 之外或没有匹配模块的内核需要重新适配；服务将失败并保留日志，不反复重试。
CI 默认生成的 `6.18.56-ophub` 包只能用于该内核，不能安装到实机的 `6.18.37-ophub`。

## 集成到离线 Armbian 根文件系统

在 Linux 构建机上先交叉编译目标内核模块，再指定离线根目录：

```bash
sudo bash scripts/install.sh --root /mnt/armbian-rootfs \
    --kernel-release 6.18.37-ophub --modules "$PWD/dist/6.18.37-ophub"
```

离线根文件系统应已有固件及目标内核模块目录。此模式用 `depmod -b` 生成目标索引，
写入相同的服务、参数和启用链接，不调用构建机 systemctl，也不加载 ARM64 模块。
目标中的父路径符号链接必须仍落在该根文件系统内；拒绝通过链接写到构建机其他目录。
该入口可放在 Armbian 镜像定制的根文件系统收尾阶段。
