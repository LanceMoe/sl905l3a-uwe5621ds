# v8：减少重复失败的 TX ADDBA 协商

v7 十分钟日志里有 200 次发送聚合协商失败，每次发送到固件回复约 400–422 ms，
而原驱动失败后每隔三秒重新发起。这些请求与固件唤醒、接收聚合管理共享串行工作列表，
因此会占用同一队列。用户已确认 `iw get power_save` 为 off，不重复关闭 802.11 省电的试验。

v8 默认将 Meson 的重试间隔设为 30000 ms，成功建立的会话沿用原行为。
新增参数 `/sys/module/sprdwl_ng/parameters/tx_addba_retry_ms`，root 可直接写入：

- `3000`：原先三秒间隔，作为对照。
- `0`：暂停发起新的 TX ADDBA，不拆除已建立的聚合，不禁用 RX 重排。
- `30000`：v8 默认三十秒间隔；其他非零值内部上限为 300000 ms。

已有在途或排队请求仍会按原路径完成，不伪造成功回复或强制释放命令锁。
使用单调时钟记录各 TID 的上次尝试，数组覆盖 TID 0–7；新 peer 事件清空旧尝试时间。
不改固件、DTB、MTU、SDIO 中断/轮询和 RX 重排窗口。

## 在盒子上执行

先用与 v7 相同的内核头文件和编译器构建 v8。源码包包含已应用补丁的源文件；
建议新目录解包，保留 v7 作为回退。可复用旧目录 `.cache/toolchain` 的编译器，
无需重新下载。比如旧 v7 的本地工具链存在时：

```bash
mkdir -p /root/cm311-v8
tar -xzf /root/cm311-uwe5621ds-v8-source.tar.gz -C /root/cm311-v8
cd /root/cm311-v8/cm311-uwe5621ds
CROSS_COMPILE=/root/cm311-v7/cm311-uwe5621ds/.cache/toolchain/bin/aarch64-none-linux-gnu- bash scripts/build.sh
```

若此前工具链在其他目录，沿用此前成功构建 v7 的 `CROSS_COMPILE` 前缀。
本地验证使用 6.18.56-ophub ARM64 头文件；盒子须使用实际 6.18.37-ophub 头文件构建，
不能加载本地交叉编译的其他内核版本模块。

构建成功后正常重启一次，以切换已加载的 v7 模块。重启后执行：

```bash
cd /root/cm311-v8/cm311-uwe5621ds
bash scripts/diagnose.sh --run &&
nmcli con up uuid 42adda02-e7fa-47ea-b210-9635fefabf98 ifname wlan0 &&
bash scripts/test-link.sh --addba-compare-seconds 180
```

脚本 v4 在同一连接上自动执行三阶段，各 180 秒，总计约九分钟及采集开销：
3000 ms → 0 → 30000 ms。每阶段向 wlan0 网关和公网各每秒发一包，
IPv4 总长不超过 1400，保存独立的原始 ping、每 30 秒状态及整个过程的内核日志。
不改连接配置、不重连、不修改 MTU。切换参数后留一秒让在途请求自然完成；
已经建立的 BA 会话可能跨阶段保留，因此对照只涉及发起新协商的策略。

正常结束、异常或 Ctrl+C 均尝试恢复测试前的参数和 Wi-Fi 日志级别。
如果参数恢复失败，会记录并打印手动恢复命令。最后只需发回
`cm311-linktest-*.tar.gz`；该包也收录现有加载日志。若加载失败则 `&&` 链停止，
发回当时的 `cm311-diag-*.tar.gz`，不用继续连接或重复加载。

这次需要编译并重启一次是因为 v7 没有该运行时入口；v8 三阶段之间无需重启。
如需进一步比较同一入口，也可以在已加载的 v8 上重复测试。

## 验证范围

已通过本地 ARM64 模块构建、实际 `prepare_addba()` 函数的 ASan/UBSan 测试，
覆盖八个 TID、重试边界、最大值、运行时暂停/恢复、已建立会话、非法索引及重复请求；
采集脚本验证三阶段输出隔离、异常/中断恢复与选项限制。
v8 实机结果见 [三阶段分析](v8-validation.md)：三十秒重试改善延迟并减少失败协商，
暂停新协商阶段的丢包明显增多。保持默认三十秒；仍未消除延迟、丢包或解决根本的 AP 协商不响应。
