# E900V22C reference comparison

Inspected repository commit `c8ef6655fa9b30b962ae047fe32cd045a2d33691`:
https://github.com/ZLRomi/E900V22C-FNNAS-4GRAM-WIFI

Inspected its referenced original driver at commit
`0c12c46df48da9592abc7848335482e68d23e28a`:
https://github.com/KryptonLee/uwe5621ds-aml

## Actual differences relevant to this box

| Item | Running CM311 | Reference E900V22C DTB |
| --- | --- | --- |
| Controller | ffe03000, meson-axg-mmc | Same |
| DRAM quirk | Present | Present |
| Bus | 4-bit, SDR50, maximum 100 MHz | Same |
| Supplies | 3.3 V main / 1.8 V I/O | Same nominal voltage |
| External clock | 32768 Hz PWM, period 30518 ns | Same |
| `cap-sdio-irq` | Absent | Present |
| `post-power-on-delay-ms` | Absent | 1000 |
| `power-off-delay-us` | Absent | 10000 |
| Wi-Fi child compatible | sprd,unisoc-wifi | uwcnmodem,we5621ds |

The child compatible must remain the value accepted by this project's
driver. GPIO phandles are tree-local: identical numeric phandles are not
required, nor is copying the reference's whole device tree appropriate.
Its memory/eMMC changes target another board and are outside this fix.

The exact 6.18.37 Meson host implementation already includes
`enable_sdio_irq`, `ack_sdio_irq`, IRQ_SDIO handling, and sets
MMC_CAP2_SDIO_IRQ_NOTHREAD when MMC_CAP_SDIO_IRQ is parsed from the DT.
The running caps are `0x40040105` (SDIO_IRQ bit 3 absent).
The local BSP enables its 20 ms RX fallback when that capability is absent.
Consequently the earlier wording that this host *lacks native SDIO IRQ
support* was too broad: the implementation supports it, but the running
device tree does not enable it. Hardware operation still requires testing.

This is a concrete missing board declaration worth an isolated test.
It is not proof that enabling it resolves an oversized packet FIFO stall.

## Driver and release qualifications

The reference contains prebuilt `6.12.41-trim` modules; `modinfo` confirms
their vermagic. They cannot load on this box's 6.18.37 kernel.
README describes those modules and dev_addr/connection leak patches, but
the current `build-image.yml` pins 6.18.18 and explicitly says drivers must
be compiled by the user. Do not treat its current workflow as an automated
verified 6.18 Wi-Fi driver build.

The referenced original driver still uses `sdio_readsb` and one-trailer
SDMA parsing. No explicit solution for a 1536-byte limited host was found.
Native IRQ may reduce queue buildup/latency; it does not increase SRAM.

## Isolated experiment and result

`dist/e900v22c-comparison/cm311-native-irq.dtb` is derived from the actual
CM311 boot DTB with exactly one empty `cap-sdio-irq` property added at
`/soc/mmc@ffe03000`. Sorted decompilation was compared programmatically;
every other decoded property is unchanged. A readable diff is next to it.

Original SHA256:
`c43064088e418574efa4b1e3c56c15ac53fc751555e656357409372806cb58fa`

Candidate SHA256:
`c13b8e97edc469b23c5acbab7263976316b2954a77c40bbac47b789c95a0af6b`

The candidate was installed with a verified backup and booted at
2026-10-18 23:57 Asia/Shanghai. Runtime caps changed from `0x40040105` /
`0x40000` to `0x4004010d` / `0x60000`. Native SDIO IRQs genuinely worked:
an initial snapshot showed IRQ count 566, packet RX 568, no-progress 0,
and no fallback-arm log.

At 2026-10-19 00:00, a bounded Ethernet-disconnected test confirmed the
only default route and source address were WLAN / 172.16.80.154. Gateway
ping received 3/4 replies, external ping 0/4, DNS and HTTPS timed out.
RX again returned valid=0 with outstanding length increasing to 4160.
Afterward IRQ count was 674, RX 1471, no-progress 795. Ethernet was restored
automatically. Full test output is saved at
`dist/e900v22c-comparison/wireless-only-native-irq.log`.

Conclusion: the missing native IRQ declaration is real, but fixing it alone
does NOT fix independent Wi-Fi networking. Do not claim that IRQ counters
or lower initial latency establish the packet transport fix.

`restore-native-irq-dtb.sh` restored the original boot DTB from the verified
backup after this failed acceptance test. Keep the failed stream/DDR
candidates disabled. No new driver release or verified fix was produced.
