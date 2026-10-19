# SDIO RX protocol investigation

Date: 2026-10-18. Research only; no new candidate deployed.

## Findings supported by source

1. A similar host limit has a real upstream solution for **rtw88**, using
   consecutive reads and draining the remaining transfer after errors:
   https://lkml.iu.edu/2308.0/07164.html
   This is evidence against calling the 1536-byte host limit universally
   impossible to work around. It is not evidence that Unisoc's packet FIFO
   implements the same continuation semantics.

2. The current Armbian Unisoc RX transport still reads fixed function-1
   address `0x20`. Its SDMA entry calls `sdio_readsb`; its ADMA entry builds
   one CMD53 for a scatterlist, with packet slots and an additional trailer
   block. Merely selecting ADMA does not supply a small-transfer continuation
   implementation for this host:
   https://raw.githubusercontent.com/armbian/uwe5622/master/unisocwcn/sdio/sdiohal_main.c
   Local reference: `sdiohal_config_packer_chain`, `sdiohal_adma_pt_read`,
   and `sdiohal_rx_list_parser` in `build/uwe5622/unisocwcn/sdio/`.

3. The firmware configuration explicitly describes SDMA/ADMA RX selection
   and 512/840-byte blocks. It does not document a packet continuation bit:
   https://raw.githubusercontent.com/armbian/uwe5622/master/unisocwcn/platform/wcn_boot.c
   This does not prove no continuation mechanism exists in the firmware.

4. The vendor Amlogic integration requests a 512 KiB host limit via
   `sdio_set_max_reqsz(0x80000)`. Copying that call to mainline or increasing
   metadata alone cannot enlarge the physical SRAM buffer. The examined
   vendor MMC PIO code uses one descriptor and a bounded internal buffer;
   it does not contain the proposed two-buffer streaming implementation:
   https://android.googlesource.com/kernel/arm64/+/f5269100977385d1fd4a5ef68e49631892cf4fe4/drivers/amlogic/mmc/aml_sd_emmc.c

5. The installed firmware contains the diagnostic assertion string
   `(processed_link_ptr_temp->len <= sdiom_config.data_len_max)` and source
   path `chip_drv_pm/chip_module/sdiom/sdiom_cp.c`. This establishes a firmware
   internal length constraint only. No host command or register for changing
   that setting was identified. Do not patch a binary constant or advertise
   this as a discovered configuration switch.

## Rechecked hardware evidence

The archived SRAM candidate packet trials contain these results. Counts
are sent / observed on wlan0 / payload exactly matched; these are small
diagnostic samples, not sustained throughput measurements.

| Candidate log | IP bytes | Sent | Received | Exact payload |
| --- | ---: | ---: | ---: | ---: |
| rx-chain-packets.jsonl | 60 | 5 | 5 | 5 |
| rx-chain-packets.jsonl | 1440 | 10 | 8 | 8 |
| rx-chain-packets.jsonl | 1480 | 3 | 0 | 0 |
| rx-command-packets.jsonl | 60 | 5 | 5 | 5 |
| rx-command-packets.jsonl | 1440 | 10 | 6 | 6 |
| rx-command-packets.jsonl | 1480 | 1 | 0 | 0 |

Sources: ignored artifacts in `dist/inbound-ping/`. These tests do not
establish a precise safe packet threshold: their queues carry earlier
traffic, sample sizes are small, and both candidates subsequently stall.

The repeated `pending=3440` value proves an outstanding length and lack of
progress. It does **not**, by itself, identify one 3440-byte head packet;
the trailer's pending value can describe accumulated outstanding traffic.

## Decision

Do not deploy another descriptor-chain flag variation, remove the DRAM
quirk, or switch blindly to ADMA. The exact CIS DDR comparison failed under
the tested configuration (see RESULTS.md), and the SRAM candidates failed
packet integrity/continued reception checks.

The open protocol question is whether this exact firmware offers a way to
consume **one oversized packet across separate CMD53 transactions**, with
documented state and length handling. A real solution must verify that
question using vendor protocol information or a controlled hardware trace;
a simulated CP that assumes continuation is not sufficient evidence.

Required acceptance remains 1500-byte inbound packet integrity, continued
small-packet reception afterward, Ethernet-disconnected DNS/HTTPS, and a
wireless-only boot. None is newly claimed to pass by this research.
