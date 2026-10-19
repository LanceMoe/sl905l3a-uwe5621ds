# CM311 SDIO diagnostic results (2026-10-18)

These are failed experiments, not a deployable fix.

## CIS comparison, 23:24:21 Asia/Shanghai

Hardware reports `G12A (Unknown)`, revision `28:c - 70:2`.
The diagnostic used CMD52 to obtain 256 common-CIS reference bytes, then
CMD53 function 0 incrementing byte-mode reads to SRAM and coherent DDR.
The reference contained at least eight bytes different from its first byte.

```
target=ffe03400 status=01ff2000 response=00001000
target=0c1d8000 status=01ff3000 response=00001000
SRAM control passed; DDR exact comparison FAILED (-5), bytes=256
```

The SRAM read matched the CMD52 reference exactly. The DDR transaction
reported a controller error, so a successful DDR data comparison was not
obtained. This rejects deploying the proposed direct-DDR workaround under
the tested configuration. It does not prove all possible DDR configurations
are impossible, nor test large packet FIFO reads. Silicon revision alone
does not justify removing `amlogic,dram-access-quirk`.

## Recovery, 23:25

Ran `restore-baseline.sh` successfully, then performed a normal reboot.
The actual ffe03000 controller is again bound to `meson-gx-mmc`.
The failed `cm311-sdio-rx` startup service is disabled and its Wi-Fi dependency
drop-in archived. The BSP backup was restored and byte-compared before
reboot. The running upper Wi-Fi driver's previously validated sleep/latency
fix was retained.

The new boot reports `single CMD53 packet limit=1536` and firmware SDMA
configuration `0x80831`. Both Ethernet and WLAN obtained their existing
addresses. These observations are recovery checks, not evidence that
wireless-only boot or oversized inbound packets have been fixed.

## Remaining defect

The 1536-byte host transfer limit conflicts with observed firmware packet
heads larger than that limit. The experimental SRAM descriptor pipeline
failed on real hardware and must not be shipped. No working replacement
transport has been established. The current vendor SDIO configuration
documents RX SDMA/ADMA selection, but does not establish a FIFO stream or
packet-fragment mode. Do not invent such a switch or treat Linux MTU
changes as an inbound packet fix.
