# 移植过程与历史验证记录

此文保留各实验版本的排查过程；系统集成请以 [安装说明](../INSTALL.md) 为准。

# CM311-1a YST / UWE5621DS 主线 Linux 移植实验（v8）

完整采集入口见 [一次采集诊断包](../DIAGNOSTICS.md)：`bash scripts/diagnose.sh --run`。
仅采集当前现场可用 `--snapshot`，无需重启。

目标设备是 S905L3A 的 CM311-1a YST，当前运行 `6.18.37-ophub`，启动文件为
`/boot/dtb/amlogic/meson-g12a-s905l3a-cm311.dtb`。

**当前状态：v8 已在 6.18.37-ophub 实机完成启动、连接和三阶段对照。
MTU 1400、三十秒 ADDBA 重试时，三分钟网关丢包 0.56%、平均 RTT 44 ms，
公网丢包 1.11%、平均 RTT 38 ms；原三秒重试时分别约 151/157 ms。
暂停新协商时丢包明显增多，因此保持默认三十秒间隔。测试期间 SDIO 包错误计数未增长，
未出现主机命令超时或 CP2 复位。当前基本可用，尚有丢包和周期性延迟，
未实现完整 MTU 1500 支持，也未证明长期稳定或测量吞吐。**
第一阶段仅构建 Wi-Fi 和 WCN 底层模块。

2026-10-17 实机记录见 [v7 短测分析](v7-link-validation.md)。
随后十分钟的结果见 [v7 持续测试分析](v7-soak-validation.md)。
v8 修改与实机对照步骤见 [ADDBA 重试对照](v8-addba-comparison.md)。
返回的三阶段结果见 [v8 实机分析](v8-validation.md)。
无需重新编译或重启的持续采集入口见 [诊断说明](../DIAGNOSTICS.md)。

## 已确认的信息

用户提供的日志显示：

- `ffe03000.mmc` 已分配 `mmc-pwrseq`，并枚举出 SDR50 SDIO 卡。
- 设备是 `mmc0:8800:1`，`SDIO_ID=0000:0000`，节点兼容字符串为 `sprd,unisoc-wifi`。
- `/lib/modules/6.18.37-ophub` 中没有找到 uwe、sprd、unisoc 模块文件。
- `/lib/firmware/uwe5621ds/` 中有 `wcnmodem.bin` 及多种芯片/天线 INI 文件。
- 存在内核 build 符号链接；其目标目录是否含完整头文件和 `Module.symvers` 由构建脚本检查。
- v2 实际读取的 Chip ID 是 `0x56630001`，驱动识别为 Marlin3E。
- 固件组合镜像解析、下载以及校准同步握手完成；之后查询 CP2 版本、关闭 CP2 日志等命令无应答。
- 目标头文件定义了 `CONFIG_PM=1`，没有定义 `CONFIG_PM_SLEEP`。
- 实际设备树启用了 `amlogic,dram-access-quirk`；该控制器使用 1536 字节 SRAM 缓冲区。

`0000:0000` 符合上游驱动已有的匹配表；内部 Chip ID 由后续寄存器读取确认。
固件下载成功表明已有 SDIO 数据传输能够工作，但仍不能证明运行时收发、中断或 Wi-Fi 功能可靠。

## 实验改动

基于 [armbian/uwe5622](https://github.com/armbian/uwe5622) 的固定提交
`fa9d1a6703a93a85bcf68e865ebc2726474076ad`，补丁保存在
`patches/0001-mainline-meson-uwe5621.patch`。

1. 增加独立的 `CONFIG_MESON_UWE5621` 构建选择，不启用晶晨旧 BSP、全志或瑞芯微的板级代码。
2. 沿用现有 MMC core / `mmc-pwrseq-simple` 的供电、复位及外部时钟，不重复申请 GPIO。
   不需要添加 `uwe-bsp` 节点；本实验沿用驱动已有的合成 platform device。
3. 使用带内 SDIO 中断、512 字节块和非 ADMA 传输；启用实际 Chip ID 检测。
4. 固件 API 读取 `uwe5621ds/wcnmodem.bin`，Wi-Fi 校准目录为
   `/lib/firmware/uwe5621ds`。INI 名称由芯片 ID 和驱动查询到的天线配置决定，
   不预设 `0x56630001`，不把 UWE5622 的单天线 INI 复制过来冒充。
5. 保留供电并处理软件驱动解绑，不套用旧 BSP 的 SDIO 重枚举接口。
6. 检查 WCN 初始化状态，传递启动/固件下载失败，修正相关失败路径的内存释放。
7. 根据实际 `cfg80211.h` 声明选择驱动已有的接口兼容分支，处理 ophub/unifreq 回移的新接口。
8. v2 修复 SDIO 错误快速返回时漏减 `xmit_cnt` 的四处路径；全志专用行为使用
   `CONFIG_AW_BOARD`，避免多平台内核的 `ARCH_SUNXI` 误选。清理时等待计数归零最多
   两秒，失败后返回错误并保留底层模块，避免仍有操作时释放其代码/内存。
9. v3 在没有 `PM_SLEEP` 时照常初始化命令锁与 completion；检查初始化返回值，
   未初始化的对象不执行清理。命令加锁失败返回实际错误，避免错误地报告成功。
10. v3 传播固件 API 同步、INI 下载及固件信息查询失败；停止初始化并释放已创建资源，
    不在 INI 下载失败后触发 CP2 复位又继续创建网络接口。按当前 cfg80211 规则处理接口注册及锁。
11. v3 默认输出初始化日志，并提供只读的 SDIO IRQ、包读写尝试及 I/O 错误计数。
12. v4 根据 MMC host 的请求、段、块数上限限制每次 SDMA 读取，避免一次逻辑包请求
    被 MMC core 拆成多个 CMD53。发送聚合在包边界刷新，保留 EOF 空间；超限的单包
    明确记录并回收，底层收发入口拒绝会触发拆分的请求。
13. v4 校验 RX 有效长度和实际缓冲区边界，异常包不会在同一位置无限循环。
    probe 日志与只读参数 `sdio_packet_limit` 记录实际选中的上限。
14. v5 在没有原生 SDIO IRQ 能力的 Meson host 上保留带内 IRQ，并将接收等待限制为
    20 毫秒。只在固件启动后的 `runtime_get` 成功时启用，主动唤醒原来等待的线程；
    `runtime_put` 与 SDIO remove 时停用。只读参数 `sdio_rx_poll_timeouts` 记录等待超时次数，
    它不等于有效包或丢中断次数。固件下载阶段不轮询数据 FIFO。
15. v6 在 `valid_len=0` 且 pending 非零、I/O/解析失败、或单轮连续读取达到 64 次时，
    退出本轮连续读取并退让 20 毫秒，再安排下一次机会。I/O 失败不解析旧包尾；
    每次读取前清空上次的本地状态。`sdio_rx_no_progress` 记录空载但有 pending 的次数。
16. v6 将 Meson 实验网卡的默认/最大 MTU 设为 1400，限制常规 IP 发送及 TCP MSS；
    这不是 SDIO 大包分段支持，也不能保证阻止外部发来的所有超长帧。
17. v7 仅对启动同步共享内存的 `-EILSEQ` / `-ETIMEDOUT` 最多重读两次，间隔 2–4 毫秒；
    不重发固件、配置写入、FIFO 读取或普通数据包。Meson 的直接内存 I/O 失败保留错误日志
    与 SDIO abort，不再自动执行会写调试寄存器的 `dump_aon_reg`。
18. v7 检查校准、firmware-ready 与 runtime-get 返回值，工作线程在成功和失败时均通知
    completion，并单独保存真实结果。所有下载等待调用方检查该结果，不把“线程结束”当作启动成功。
19. v8 将 Meson 的 TX ADDBA 尝试间隔由三秒延长到三十秒，并通过可写模块参数
    `tx_addba_retry_ms` 允许运行时对照；0 暂停新的协商，非零值上限为 300000 ms。
    保留已建立的 TX BA 与 RX 重排，不清理在途命令。重试使用单调时钟，避免校时影响；
    补全八个 TID 的时间数组，原数组只有七项，并在 peer 新建事件时清空旧时间。

脚本按实际头文件检测接口，不使用强制忽略函数指针类型错误的编译选项。
该检测覆盖此实验遇到的 key/station、通知和 ROC/management cookie 接口，
并不是所有内核版本的通用兼容层。

## 在盒子上构建

将本项目或提供的源码压缩包复制到盒子本地目录，进入项目目录后执行。
以下步骤不会修改 `/boot`，也不会替换系统内核。

```bash
# 如果缺少这些构建/诊断工具，再安装；内核头文件须精确匹配 uname -r。
sudo apt-get update
sudo apt-get install --no-install-recommends build-essential git python3 kmod iw curl xz-utils

# 取适合盒子本机运行的 Arm GNU 15.3 编译器，约 160 MB，校验固定 SHA-256。
# 此编译器适合较新的 ophub 头文件，避免系统 GCC 过旧而不识别内核编译选项。
bash scripts/fetch-toolchain.sh
CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
```

构建默认使用 `/lib/modules/$(uname -r)/build`，`JOBS=2`。
检查 `include/generated/autoconf.h`、`include/generated/utsrelease.h` 和 `Module.symvers`，
拒绝头文件版本与目标版本不一致的构建。只有 build 符号链接不代表头文件完整。

提供的源码包已包含应用补丁的驱动源码；从仓库克隆时，首次构建会获取上述固定提交。
脚本不会覆盖已有且未标记的源码目录。更新补丁后，应把旧 `build/uwe5622` 移到别处再构建。

输出：

```text
dist/6.18.37-ophub/uwe5621_bsp_sdio.ko
dist/6.18.37-ophub/sprdwl_ng.ko
dist/6.18.37-ophub/build.log
```

若构建失败，保留终端输出和 `build.log`。若失败发生在编译开始前，可能尚无日志文件。
不要通过改模块版本字符串来加载其他版本的模块。

## 第一次实机测试

在保留有线连接、能够重启盒子的情况下执行：

```bash
sudo bash scripts/load-test.sh
```

该脚本检查 SDIO 设备与模块版本，先加载系统 `cfg80211`，再通过 `insmod` 临时加载
本次编译的 `uwe5621_bsp_sdio` 和 `sprdwl_ng`。不安装到系统模块目录，不设置开机加载。
失败时不自动反复卸载、重载；修改或重试前重启，以恢复干净的设备状态。

脚本还检查 Wi-Fi platform driver 是否实际绑定，避免将 `insmod` 返回成功误认为初始化成功。
内核日志在加载前开始持续记录到 `dist/$(uname -r)/load-test.log`，
每一步操作也会打印并记录到同目录 `load-progress.log`；脚本退出时保存
`load-state.log`，包含 PM 配置、设备绑定及 SDIO 计数。重点核对：

- SDIO `probe ok`；
- 实际 Chip ID 与固件读取路径；
- 固件下载及启动完成；
- 最终选择的 `wifi_<chipid>_<ant>ant.ini`；
- `iw dev` 中出现无线接口。

出现接口后，再使用 `iw dev <接口名> scan` 验证扫描，然后验证连接、DHCP 和实际流量。
接口名以 `iw dev` 为准。尚未验证扫描、连接、吞吐、休眠恢复、模块反复加载和蓝牙。
如果出现 `-110`、CMD53 超时，再依据日志评估 SDIO 降频和 DTB 修改。

### 加载命令卡住时

首次实机反馈：两个模块在 `6.18.37-ophub` 上编译成功，但运行加载脚本后原 SSH
窗口卡住，另一个 SSH 连接仍能登录。后续堆栈定位到启动失败清理中的
`sdiohal_runtime_put()`，`xmit_cnt=1` 无限等待；SDIO 收发线程均在等待唤醒。
旧源码中的四处错误快速返回路径会产生这种计数泄漏，本地故障注入已复现并验证修复。
多平台配置会使 `CONFIG_ARCH_SUNXI` 与 `CONFIG_ARCH_MESON` 同时启用，v2 改用
驱动自身的板级选择。用户盒子的最初启动错误被刷屏覆盖，`journalctl` 返回
`Failed to iterate through journal: Bad message`，目前仍不能确定最初 SDIO/固件失败的原因。

保留卡住的窗口，在新的 SSH 连接中获取以下输出，再决定补丁和恢复方式。
先不要重复加载、卸载模块或重启，以免丢失现场：

```bash
ps -eo pid,ppid,stat,wchan:36,comm,args | grep -E 'insmod|modprobe|load-test|iw dev|sdiohal|kworker' | grep -v grep
for p in $(pgrep -x insmod; pgrep -x modprobe); do
    echo "=== PID $p ==="
    cat /proc/$p/stack
done
dmesg | tail -n 200
```

旧版脚本只在退出时保存日志，阻塞期间日志文件可能不存在。此时直接读取 `dmesg`。
当前版本补充了阶段输出和持续日志；若再次阻塞，还可在新 SSH 中执行
`bash scripts/collect-state.sh`。该脚本只读取配置与 sysfs，不调用驱动加载或卸载。

### v2 的 Oops 与 v3 修复

v2 日志中的故障栈为 `complete -> sprdwl_cmd_deinit -> sprdwl_core_free -> sprdwl_probe`。
未启用 `CONFIG_PM_SLEEP` 时，内核的 `wakeup_source_register()` 是返回 NULL 的桩函数。
旧驱动将 NULL 当成分配失败，在初始化 completion 之前返回；调用方忽略了错误，
退出时仍调用 `complete()`，触发无效地址访问。v3 区分没有休眠支持与真正的分配失败，
并修复相关错误传递和资源回收路径。

这解释了 Oops，但不直接解释 WCN 层更早发生的 CP2 AT 命令无应答。v3 的计数用于
下一轮区分是否有发包、是否收到带内中断，以及是否尝试读取数据，尚未证明命令超时已解决。
计数统计包 I/O 的调用次数和返回错误，不是字节数，也不等于成功解析的 Wi-Fi 响应数。

已保存现场后先正常重启，清除发生 Oops 的内核状态；不要强制卸载正在初始化的模块。
把 `cm311-uwe5621ds-v3-source.tar.gz` 复制到盒子的 `/root`，在新目录解压并重新编译，
可以复用旧目录下载好的编译器：

```bash
mkdir -p /root/cm311-v3
tar -xzf /root/cm311-uwe5621ds-v3-source.tar.gz -C /root/cm311-v3
cd /root/cm311-v3/cm311-uwe5621ds
CROSS_COMPILE=/root/cm311-uwe5621ds/.cache/toolchain/bin/aarch64-none-linux-gnu- bash scripts/build.sh
bash scripts/load-test.sh
```

脚本核对模块构建记录中的补丁哈希，拒绝加载旧补丁生成的模块。若发生 SDIO 排空超时，
保留日志并重启后再测试，不要强行清零计数或强制卸载。两秒上限针对已定位的计数等待，
并不保证所有硬件访问或内核等待都能在两秒内返回。

### v3 实机反馈与通道日志（2026-10-17）

新日志中加载过程返回，未出现 Oops，初始化止于 `WIFI_CMD_SYNC_VERSION[9]` 超时。
固件下载和启动握手完成；此前的两条 WCN AT 查询也没有得到应答。
快照为 `sdio_irq_count=84`、`sdio_packet_rx=85`、`sdio_packet_tx=3`、
`sdio_packet_errors=0`。这些是底层调用次数，不能据此认定收到了对应命令的响应。
目前需要确认接收数据的格式、有效长度和分发通道。

新版加载脚本支持开启驱动已有的调试日志，无需重编译 v3 模块：

```bash
SDIO_TRACE=1 bash scripts/load-test.sh
```

先正常重启，再在原 v3 目录测试。脚本在 BSP 加载后、固件启动前，通过
`/sys/kernel/debug/sdiohal_debug/at_cmd` 设置 `log_level=11`，开启普通日志、
接收尺寸/状态和短包头转储。该分支只设置驱动日志变量，不向芯片发送 AT 命令。
如有 debugfs 支持但尚未挂载，脚本会将其挂载到 `/sys/kernel/debug`。
不支持 debugfs 的内核会在加载模块前明确报错。

本轮日志使用独立文件名，保留普通测试日志：

```text
dist/6.18.37-ophub/load-test-trace.log
dist/6.18.37-ophub/load-state-trace.log
dist/6.18.37-ophub/load-progress-trace.log
```

`cm311-uwe5621ds-v3-trace-scripts.tar.gz` 仅包含更新后的 `scripts/load-test.sh` 与
`scripts/collect-state.sh`，在原项目目录解压即可使用已有模块。此次脚本更新没有改变
驱动补丁哈希。通道日志用于继续定位失败原因，尚未证明固件 API 或运行时收发已正常。

## 本地验证记录

- 在 WSL2/x86_64 中，用 Arm GNU 15.3 交叉编译。
- 使用 ophub `kernel_stable` 发布的 `6.18.56-ophub` 头文件，两个模块通过编译及
  MODPOST，没有未解析符号错误。只重建了在 x86_64 主机执行的 `fixdep`/`modpost`，
  未更改头文件中的目标内核接口。
- 检查了 unifreq `v6.18.37` 的 `cfg80211.h`：其 wdev 回移接口与 6.18.56 的
  ROC/cookie 回移接口不同，检测脚本能够区分两者。
- Shell 脚本通过 `bash -n` 和 ShellCheck。
- v2 的两个模块同样通过 6.18.56 编译及 MODPOST。`tests/test_sdio_failure_paths.py`
  提取实际驱动函数，用假的 SDIO I/O 验证四条错误路径的引用平衡，以及排空超时、
  正常排空和 jiffies 回绕；分别覆盖 Meson 多平台配置及全志错误分支。
  这些测试不模拟真实硬件、线程调度或内核锁。
- v3 两个模块通过 6.18.56 编译及 MODPOST；上述 SDIO 回归测试继续通过。
  `tests/test_cmd_lifecycle.py` 提取实际命令初始化、清理与 core 创建/释放函数，
  分别在启用和不启用 PM_SLEEP 的桩环境下运行，验证未初始化的 completion 不被操作，
  以及唤醒源分配失败、wiphy 分配失败、工作队列创建失败时的错误返回与回收。
- `tests/test_cmd_errors.py` 在 ASan/UBSan 下验证命令加锁失败的返回值、消息释放顺序、
  不完整的固件 API 响应，以及发送缓冲区已释放后的命令超时路径。
- v4 两个模块通过 6.18.56 编译及 MODPOST，之前的回归测试继续通过。
  `tests/test_sdio_packet_boundaries.py` 使用实际驱动函数和原样保留的 6.18.37
  MMC core 拆分函数，在 ASan/UBSan 下复现 `2048 -> 1536 + 512` 导致中间回应
  包尾信息丢失的机制，验证受限读取可交付回应，以及发送聚合、EOF 空间、超限回收、
  异常 RX 包头/长度的处理。该测试模拟 CP 的按请求返回包尾行为，不模拟真实芯片。
- v5 两个模块通过 6.18.56 编译及 MODPOST，之前的回归测试继续通过。
  `tests/test_rx_irq_fallback.py` 提取实际接收等待、runtime_get/put 与兜底启用函数，
  使用 pthread completion 验证：固件下载期间不周期唤醒、启动失败不启用、启动成功
  唤醒原来的等待、无 IRQ 时定时返回、有 IRQ 时立即返回、原生 IRQ host 保留原行为，
  以及两个 runtime 分支的停用顺序。这不模拟实际芯片或 MMC 中断硬件。
- v6 两个模块通过 6.18.56 编译及 MODPOST，之前的回归测试继续通过。
  `tests/test_rx_progress.py` 使用实际 SDMA 接收与连续读取决策函数，在 ASan/UBSan 下验证
  I/O 失败不重复分发旧应答、`0/8188` 不立即重复读取、持续有效数据也在每轮 64 次内退让，
  以及实验性 MTU 设置。链路脚本覆盖过滤路由省略 dev 的实际 JSON 格式，以及 CP2 已失败
  但 NetworkManager 仍报告 connected 时的只采集分支。
- v7 两个模块通过 6.18.56 编译及 MODPOST。`tests/test_wcn_startup.py` 提取实际同步读取、
  校准、启动工作线程及下载等待函数，在 ASan/UBSan 下覆盖单次/持续读错、禁止重写、
  各必需启动阶段失败、completion 结果、调度冲突和等待超时；分别构建 Meson 与非 Meson 分支。
  SDIO 错误路径测试验证 Meson 不执行自动调试转储，全志分支仍保留原行为。
  采集器入口测试验证加载失败和仍运行时也先打包，再返回非零退出状态。

## v4：G12A 单次 CMD53 上限修复

完整诊断包 `cm311-diag-20261016-233942-sgi3_xs3` 记录了 85 次读取：84 次有效长度为 0，
只有一次 1536 字节请求读到了有效长度 1424 的固件日志，分发到通道 15。
Wi-Fi 同步命令和两条 WCN AT 命令都等待超时；MMC 错误统计没有 CRC、数据或命令超时。

运行设备树中 `mmc@ffe03000` 含有 `amlogic,dram-access-quirk`。
[目标版本 Meson 驱动](https://github.com/unifreq/linux-6.18.y/blob/v6.18.37/drivers/mmc/host/meson-gx-mmc.c)
在此模式下将请求限制为 1536 字节 SRAM 可容纳的块数。
[MMC SDIO helper](https://github.com/unifreq/linux-6.18.y/blob/v6.18.37/drivers/mmc/core/sdio_io.c)
据此把默认 2048 字节读取拆成 1536 和 512 字节两次 CMD53。
SDMA 的有效长度/下一次长度描述每次事务，旧驱动只查看整个 2048 字节缓冲区最后 8 字节，
会漏掉中间事务的描述。这是源码与日志共同支持的故障机制，本地已复现。

v4 使用实际 host 上限而非固定写死 1536。接收请求保持单次事务；发送聚合按包边界
拆成多个独立、各自有 EOF 的事务。保留设备树中的 SRAM 限制，使用原有 512 字节块、
固件、INI 和时钟设置。遇到单个 SDIO 包本身超过上限时记录错误，不尝试发送半个包。
这修复请求的传输边界问题，但 v4 实机仍因接收未被唤醒而命令超时；
大帧/聚合行为、扫描、连接和吞吐仍需实测，不能据此
宣称普通 MTU 下的全部流量已得到支持。

把 `cm311-uwe5621ds-v4-source.tar.gz` 放到盒子的 `/root`，可以先在当前系统上编译：

```bash
mkdir -p /root/cm311-v4
tar -xzf /root/cm311-uwe5621ds-v4-source.tar.gz -C /root/cm311-v4
cd /root/cm311-v4/cm311-uwe5621ds
CROSS_COMPILE=/root/cm311-uwe5621ds/.cache/toolchain/bin/aarch64-none-linux-gnu- bash scripts/build.sh
```

编译成功后正常重启一次，再进入新目录执行一键采集：

```bash
cd /root/cm311-v4/cm311-uwe5621ds
bash scripts/diagnose.sh --run
```

预期 probe 输出 `single CMD53 packet limit=1536`。无论加载成功或失败，都发送最后
打印的一个 `cm311-diag-*.tar.gz`。v4 源码包包含全部采集脚本，无需另外安装 v3 脚本包。

本地没有目标盒子的完整 `6.18.37-ophub` 头文件包，因此尚未完成该精确版本的完整编译，
也没有生成可宣称适用于目标盒子的预编译模块。源码包不携带 6.18.56 的测试模块；
应在盒子上针对当前头文件构建。未连接或操作目标盒子。

## v5：启动后的接收等待兜底

`cm311-diag-20261017-002102-75s25kr1` 证实 v4 请求上限为 1536 字节，发送 3 次，
IRQ 和读取均为 0。之后在同一运行现场执行 `switch_irq`，
`cm311-diag-20261017-034027-qsrm0qj9` 记录到立即读出的有效数据：

- 通道 13：`OK` 与固件版本字符串，对应此前 CP2 查询。
- 通道 22：`00 09 10 01 be 27 03 00 ...`，命令 9 与时间戳对应此前 Wi-Fi
  `SYNC_VERSION` 请求。此时 probe 已经退出、接收通道已注销，所以记录了 `no rx ops`。
- 通道 15：多段固件日志；队列排空后返回有效长度 0，没有传输错误。

这证明芯片已产生应答，但启动阶段没有及时读取；无需据此替换固件或调整 GPIO。
主机没有 `MMC_CAP_SDIO_IRQ`，内核通过 `ksdioirqd/mmc0` 处理带内通知。
尚未定位该通知没有唤醒接收线程的硬件/寄存器层原因。v5 先提供定时读取兜底，
保留原有中断注册及固件 IRQ 配置；空闲时仍会周期读取，对功耗、延迟及吞吐的影响待实测。

将 `cm311-uwe5621ds-v5-source.tar.gz` 放到 `/root`，先编译，成功后再正常重启一次：

```bash
mkdir -p /root/cm311-v5
tar -xzf /root/cm311-uwe5621ds-v5-source.tar.gz -C /root/cm311-v5
cd /root/cm311-v5/cm311-uwe5621ds
CROSS_COMPILE=/root/cm311-uwe5621ds/.cache/toolchain/bin/aarch64-none-linux-gnu- bash scripts/build.sh
```

当前加载的 v4 BSP 不能通过更新磁盘文件变成 v5；本次重启用于替换已加载的旧模块。
重启后执行：

```bash
cd /root/cm311-v5/cm311-uwe5621ds
bash scripts/diagnose.sh --run
```

无需运行 `poll-existing.sh`。预期日志出现 `RX IRQ fallback armed: 20 ms after firmware startup`，
并在原生 IRQ 缺失时增长 `sdio_rx_poll_timeouts`。无论成功或失败，发回最后的一份诊断包。
本地完整编译使用 6.18.56 头文件；精确的 6.18.37 构建与硬件行为仍以盒子测试为准。
源码包不携带其他版本的预编译模块。

### v5 实机初始化结果与连接测试

`cm311-diag-20261017-035235-1fk1nh3d` 的加载返回值为 0，platform driver 绑定成功，
`iw dev` 显示 managed 类型的 `wlan0`。运行内核仍为 `6.18.37-ophub`。
固件同步命令及时收到回应，实际选择 `wifi_56630001_3ant.ini`，三段 INI 下载均有应答。
软件/硬件 rfkill 均未阻止无线功能。后台扫描已收到 2.4 GHz 和 5 GHz 路由器的探测响应。

数据包计数中出现过一次 `sdio pt read fail ret:-84`，随后继续收到有效扫描响应；
目前不能宣称传输完全无错或长期稳定。API 映射有能力版本差异提示，当前初始化与扫描
接收仍能完成，不应仅凭这些提示修改 API 表或替换固件。

保持当前驱动加载状态，降低调试日志后直接测试连接，无需重新编译或重启：

```bash
printf 'log_level=1\n' > /sys/kernel/debug/sdiohal_debug/at_cmd
printf '3\n' > /sys/module/sprdwl_ng/parameters/log_level
nmcli device wifi list ifname wlan0
nmcli --ask device wifi connect '你的Wi-Fi名称' ifname wlan0
```

密码在盒子终端按提示输入。连接后查看关联、地址和绑定 wlan0 的数据流量：

```bash
iw dev wlan0 link
ip -4 address show dev wlan0
ping -I wlan0 -c 4 -W 2 1.1.1.1
ping -I wlan0 -c 4 -W 2 -M do -s 1472 1.1.1.1
```

第二组 ping 检查普通 1500 字节 IPv4 包；外部目标或网络可能过滤 ICMP，应结合网关测试
与驱动日志判断失败原因。绑定接口用于避免通过仍在使用的 eth0 得出 Wi-Fi 联网成功的结论。
如果连接或数据测试失败，保留命令输出，在同目录执行 `bash scripts/diagnose.sh --snapshot`，
发回现有状态诊断包。

`nmcli` 用法参考 [NetworkManager 官方手册](https://networkmanager.dev/docs/api/latest/nmcli.html)。

后续实机反馈已确认关联 2.4 GHz AP、获得 IPv4 地址，绑定 wlan0 的 4 次普通 ping
全部收到回应，RTT 约 9.6–421 毫秒；4 次 `-s 1472 -M do` 则均无应答。
不同大小只测公网目标尚不能排除路径/目标限制。TX 侧的 1500 字节 IP 包加上以太网头、
11 字节发送描述符和 OTT 保留空间已接近单次事务边界；RX 侧还需要容纳由固件指定的
`msdu_offset`（接收描述符结构为 28 字节），不能把 IP MTU 直接等同于 SDIO 请求尺寸。
这是待验证的接收边界假设，尚未凭当前 ping 结果确定丢失位置。

无需重启，可使用 `scripts/test-link.sh` 一次收集多包长、网关/公网对照及临时 MTU 1400
测试的结果。脚本结束会还原原 MTU，详见 [一次采集诊断包](../DIAGNOSTICS.md)。

## v6：故障后的无进展循环与受限 MTU 实验

`cm311-linktest-20261017-040713-t7sw1g17` 显示测试前已经发生：

- `WIFI_CMD_ADDBA_REQ[40]` 超时，调用 assert 路径并触发 CP2 复位；随后发送队列 watchdog 超时。
- `iw link` 返回 `Cannot allocate memory (-12)`。assert 路径置位 `cp_asserted` 后，
  命令分配器直接拒绝新命令；`get_station` 将 NULL 统一转换成 `-ENOMEM`。
  此时 `MemAvailable` 仍约 1.6 GB，不能据此认定整机内存耗尽。
- 接收线程重复读到 `valid_len=0, rx_dtbs=8188`，累计超过 450 万次读取；
  旧循环只看 pending 是否非零，未判断是否有进展。降低 MTU 后小包同样无应答。
- NetworkManager 仍报告 connected，不能单凭该状态认定固件收发健康。已有状态下的
  MTU 1400 测试不能证明从正常连接开始使用较低 MTU 是否有效。

v6 保留命令超时的原有故障处理，没有强行清除 asserted、重启已失败芯片或自动重载。
它修复接收循环和失败后旧数据使用，并先声明 1400 的实验 MTU。芯片队列堵塞的最初触发、
完整 1500 字节收发与超长外来帧的处理仍待解决，不能宣称 v6 已恢复稳定联网。

将 `cm311-uwe5621ds-v6-source.tar.gz` 放到 `/root`，先编译：

```bash
mkdir -p /root/cm311-v6
tar -xzf /root/cm311-uwe5621ds-v6-source.tar.gz -C /root/cm311-v6
cd /root/cm311-v6/cm311-uwe5621ds
CROSS_COMPILE=/root/cm311-uwe5621ds/.cache/toolchain/bin/aarch64-none-linux-gnu- bash scripts/build.sh
```

编译成功后正常重启一次，清除当前故障状态并替换已加载的 v5 模块。然后：

```bash
cd /root/cm311-v6/cm311-uwe5621ds
bash scripts/diagnose.sh --run
nmcli connection modify uuid '42adda02-e7fa-47ea-b210-9635fefabf98' 802-11-wireless.mtu 1400
nmcli connection up uuid '42adda02-e7fa-47ea-b210-9635fefabf98' ifname wlan0
bash scripts/test-link.sh
```

连接 UUID 来自当前盒子先前的成功激活输出；若已删除该配置，可用前述 `nmcli --ask`
重新创建。MTU 属性见 [NetworkManager 官方说明](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless.html)。
发回最后一份 `cm311-linktest-*.tar.gz`。v6 包含最新脚本，会正确提取省略 dev 字段的
wlan0 网关；发现 CP2 复位后无成功启动记录时，只采集现场，不修改 MTU 或继续发测试包。
MTU 1400 下的 1472 payload 测试被本机拒绝属于预期，不表示已经支持 1500 字节传输。

## v7：启动同步读取失败与错误传递

`cm311-linktest-20261017-042855-vuze0n9u` 保存了完整的 v6 加载失败日志：
固件写入完成后，在地址 `0x40525fa0` 的同步读取返回 `-84`，随后记录
`marlin_write_cali_data ... read SYNC_ADDR error:-84`，最终只显示约 30 秒后的下载超时。
这发生在运行时接收、INI 下载和网卡创建之前；本轮不能检验 MTU 1400 的效果。

旧工作线程忽略了 `marlin_write_cali_data()` 的返回值，继续检查 firmware-ready。
v7 修复该路径并单独传递工作线程结果，失败时不再继续后续阶段。针对已知可重复读取的
启动共享内存，只在两类传输错误时有限重读；没有证据据此认定底层错误的硬件原因。
没有改动 DTB、GPIO、SDIO 时钟或固件。若错误持续，将报告具体阶段和原始错误而退出。

将 `cm311-uwe5621ds-v7-source.tar.gz` 放到 `/root`，先编译：

```bash
mkdir -p /root/cm311-v7
tar -xzf /root/cm311-uwe5621ds-v7-source.tar.gz -C /root/cm311-v7
cd /root/cm311-v7/cm311-uwe5621ds
CROSS_COMPILE=/root/cm311-uwe5621ds/.cache/toolchain/bin/aarch64-none-linux-gnu- bash scripts/build.sh
```

编译成功后正常重启一次以替换已加载模块，然后执行下面整个命令组：

```bash
cd /root/cm311-v7/cm311-uwe5621ds
bash scripts/diagnose.sh --run &&
nmcli con up uuid 42adda02-e7fa-47ea-b210-9635fefabf98 ifname wlan0 &&
bash scripts/test-link.sh
```

此前已经把该连接的 MTU 设置为 1400，v7 保持该上限。`&&` 保证加载失败时不继续连接，
连接失败时不继续包长测试。发回最后生成的一个诊断包：加载失败时是 `cm311-diag-*`，
成功完成后是 `cm311-linktest-*`。如果连接命令本身失败，可执行 `bash scripts/test-link.sh`
保存当前网络状态，无需重启。不要只凭采集器打印了文件路径判断加载成功。

## 参考与来源

- [主线适配驱动固定提交](https://github.com/armbian/uwe5622/tree/fa9d1a6703a93a85bcf68e865ebc2726474076ad)
- [UWE5621 的 Linux 6.6 移植案例](https://github.com/adron-s/openwrt-rk3566-leopad-10s)
- [unifreq 的 CM311 设备树](https://github.com/unifreq/linux-6.18.y/blob/main/arch/arm64/boot/dts/amlogic/meson-g12a-s905l3a-cm311.dts)
- [unifreq v6.18.37 无线接口头文件](https://github.com/unifreq/linux-6.18.y/blob/v6.18.37/include/net/cfg80211.h)
- [ophub 内核构建产物](https://github.com/ophub/kernel/releases/tag/kernel_stable)
- [ophub 发布的 Arm 工具链](https://github.com/ophub/kernel/releases/tag/toolchain)

原驱动的版权和许可证声明保留在各源文件中；本项目补丁及脚本采用 GPL-2.0-only。
