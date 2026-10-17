# 诊断采集与链路测试工具指南

本项目提供了一组非破坏性的状态采集与无线链路质量测试脚本，用于在盒子上排查驱动加载失败、SDIO 传输异常或测试不同参数下的丢包与延迟表现。

历史排障过程与 v1–v8 实机测试数据请参阅 **[移植与实验记录 (docs/PORTING.md)](docs/PORTING.md)**。

---

## 目录

1. [现场快照与加载过程采集 (`scripts/diagnose.sh`)](#1-现场快照与加载过程采集-scriptsdiagnosesh)
2. [无线链路与稳定性测试 (`scripts/test-link.sh`)](#2-无线链路与稳定性测试-scriptstest-linksh)
   - [模式 A：多包长与 MTU 边界探测（默认）](#模式-a多包长与-mtu-边界探测默认)
   - [模式 B：持续流量稳定性观察 (`--soak-seconds`)](#模式-b持续流量稳定性观察---soak-seconds)
   - [模式 C：TX ADDBA 重试间隔三阶段对照 (`--addba-compare-seconds`)](#模式-ctx-addba-重试间隔三阶段对照---addba-compare-seconds)
3. [辅助调试脚本 (`load-test.sh` / `collect-state.sh` / `poll-existing.sh`)](#3-辅助调试脚本-load-testsh--collect-statesh--poll-existingsh)
4. [诊断包收录内容与安全边界](#4-诊断包收录内容与安全边界)

---

## 1. 现场快照与加载过程采集 (`scripts/diagnose.sh`)

诊断包默认输出为 `dist/cm311-diag-<时间>-<随机后缀>.tar.gz`，每次运行使用独立目录，不会覆盖历史采集结果。

### 1.1 采集当前已加载状态（推荐日常排障使用）

当驱动已经由开机服务（`cm311-wifi.service`）或手动加载后，若需保存当前现场（无需重启或重载模块）：

```bash
bash scripts/diagnose.sh --snapshot
```

### 1.2 记录完整的单次驱动加载过程

如果在未加载驱动的干净状态下（例如尚未启用开机自启服务，或刚排查完问题重启后），需要完整录制从开启底层调试日志、`insmod` 加载两个模块到初始化完成的全过程：

```bash
bash scripts/diagnose.sh --run
```

- **行为说明**：加载模块前自动开启详细日志，采集加载前、中、后的状态并打包。若模块已加载或内核曾发生 Oops，`--run` 会拒绝重复加载并仅采集当前快照（在 `SUMMARY.json` 中记录实际是否执行了加载）。
- **退出码保证**：当加载失败、超时未返回或采集异常时，脚本会**先打包保存已收集到的现场资料**，然后返回非零退出码。因此可安全用于 `bash scripts/diagnose.sh --run && <后续命令>`。
- **加载卡住时的保护**：默认观察加载过程最多 45 秒（可通过 `--seconds 90` 延长）。达到上限后不会强杀 `insmod` 进程，而是直接保存阻塞线程堆栈并完成打包。

---

## 2. 无线链路与稳定性测试 (`scripts/test-link.sh`)

在 `wlan0` **已关联并获取 IPv4 地址**的状态下，可使用 `scripts/test-link.sh` 测试无线收发质量。脚本不加载/卸载模块，也不修改持久化的 `NetworkManager` 配置文件，测试完成后在 `dist/` 下生成一份 `cm311-linktest-*.tar.gz`。

### 模式 A：多包长与 MTU 边界探测（默认）

```bash
bash scripts/test-link.sh
```

- 绑定 `-I wlan0` 分别向 `wlan0` 的默认网关和公网 `1.1.1.1` 发送禁止分片（`-M do`）的探测包。
- 依次测试 ICMP payload `56`、`1200`、`1372`、`1440`、`1452`、`1472` 字节（对应 IPv4 总长 = payload + 28，即 `84` 至 `1500` 字节）。在默认 MTU 1400 下，超过 1400 字节的探测会被本机协议栈直接拒绝（`local error: message too long, mtu=1400`）。
- 若测试开始时网卡 MTU 大于 1400，脚本会临时将 MTU 设为 1400 补测 payload `56` 和 `1372`，并在结束或 `Ctrl+C` 中断时恢复原 MTU。
- 测试期间自动开启 SDIO 详细日志，结束后恢复普通日志；若系统安装了 `tcpdump`，还会自动抓取 ICMP 包前 128 字节头部。

### 模式 B：持续流量稳定性观察 (`--soak-seconds`)

用于观察一段时间内（支持 `30`–`3600` 秒）的持续小流量丢包率、RTT 波动及内核事件：

```bash
bash scripts/test-link.sh --soak-seconds 600
```

- 保持当前 MTU，向 `wlan0` 网关和 `1.1.1.1` 每秒各发 1 个禁止分片的 ping（payload 为 `min(1372, MTU-28)`）。
- 每 30 秒记录一次关联状态、网卡统计和 SDIO 计数，并持续记录内核日志。
- 测试期间将 Wi-Fi 日志临时设为 `INFO`（保持普通 SDIO 日志，避免逐包十六进制转储撑满磁盘），结束或 `Ctrl+C` 时自动恢复原日志级别并打包。

### 模式 C：TX ADDBA 重试间隔三阶段对照 (`--addba-compare-seconds`)

用于在 v8 驱动已加载并连接时，同一连接下自动对比三种 `tx_addba_retry_ms` 参数设置（每阶段支持 `30`–`1200` 秒，与 `--soak-seconds` 互斥）：

```bash
bash scripts/test-link.sh --addba-compare-seconds 180
```

- 按顺序自动切换 `/sys/module/sprdwl_ng/parameters/tx_addba_retry_ms`：
  1. `3000`（3 秒重试，持续 180 秒）
  2. `0`（暂停发起新 TX ADDBA，持续 180 秒）
  3. `30000`（v8 默认 30 秒重试，持续 180 秒）
- 阶段切换之间留 1 秒让在途命令自然完成，不重连、不重启，结束或异常中断时自动恢复测试前的参数值与日志级别。

---

## 3. 辅助调试脚本 (`load-test.sh` / `collect-state.sh` / `poll-existing.sh`)

| 脚本 | 用途说明 |
| --- | --- |
| `sudo bash scripts/load-test.sh` | 在未安装到系统前，使用 `insmod` 临时加载当前 `dist/$(uname -r)/` 下的模块并检查平台驱动绑定，日志输出到 `dist/$(uname -r)/load-*.log`。支持 `SDIO_TRACE=1` 开启底层包跟踪日志。 |
| `bash scripts/collect-state.sh` | 纯只读采集当前进程状态、内核栈、sysfs/debugfs 与 SDIO 计数，不执行任何模块加载或参数写入（适合在另一终端发生阻塞时安全取证）。 |
| `bash scripts/poll-existing.sh` | 历史 v4 排障工具：在未开启 RX 等待兜底且接收中断为 0 的现场，通过 `switch_irq` 触发轮询并打包（v5 及之后版本已内置 20 ms 接收等待兜底，日常无需使用）。 |

---

## 4. 诊断包收录内容与安全边界

生成的 `cm311-diag-*.tar.gz` 与 `cm311-linktest-*.tar.gz` 包含以下信息：

- **概览与清单**：`SUMMARY.json`（测试/加载结果摘要）与 `collection-manifest.json`（逐项命令执行状态、超时与截断记录）。
- **内核与系统日志**：启动以来完整 `dmesg`、采集期间持续日志、可读的 systemd journal 及历史构建/加载日志。
- **SDIO 与无线状态**：SDIO 计数（`sdio_irq_count`、`sdio_packet_rx/tx/errors`、`sdio_packet_limit`、`sdio_rx_poll_timeouts`、`sdio_rx_no_progress`）、模块参数、平台驱动绑定、`iw dev`/`wiphy`/`rfkill` 状态。
- **调用栈与硬件树**：阻塞进程、SDIO/WCN 及中断线程的 `/proc/<pid>/stack`；运行时的设备树属性（已剔除随机种子 `kaslr-seed`/`rng-seed`）、MMC 时钟/总线状态、供电与引脚复用信息。
- **构建与固件指纹**：当前内核 `autoconf.h`、符号表、固件文件大小与 SHA-256、固件头部及校准 INI 内容。

**安全与体积限制**：单文件日志保留末尾最多 4 MiB，CP2 历史日志最多 2 MiB，持续内核日志最多 32 MiB；采集脚本绝不主动触发固件内存转储（dump），也不读取会阻塞等待固件回复的 `/proc/mdbg` 命令节点。

