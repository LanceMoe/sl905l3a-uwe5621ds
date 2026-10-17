#!/usr/bin/env python3
"""Run the real TX ADDBA gate and peer layout under ASan/UBSan."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622'
tx = (source / 'unisocwifi/tx_msg.c').read_text()
gate = tx[tx.index('static void prepare_addba('):tx.index('\nint sprdwl_tx_msg_func(')]
header = (source / 'unisocwifi/wl_core.h').read_text()
start = header.index('struct sprdwl_peer_entry {')
peer = header[start:header.index('\n};', start) + 3]

prelude = r'''
#include <assert.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef int64_t s64;
#define ETH_ALEN 6
#define MAX_LUT_NUM 16
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define READ_ONCE(x) (x)
#define min(a,b) ((a) < (b) ? (a) : (b))
#define NSEC_PER_MSEC 1000000LL
#define wl_debug(...) do {} while (0)
struct timespec64 { int64_t tv_sec, tv_nsec; };
struct tx_address { u8 da[6], sa[6]; };
struct rx_address { u8 sa[6], da[6]; };
static struct timespec64 clock_now;
static unsigned int tx_addba_retry_ms = 30000;
static int calls, raced;
static void ktime_get_ts64(struct timespec64 *now) { *now = clock_now; }
static s64 timespec64_to_ns(const struct timespec64 *t) { return t->tv_sec * 1000000000LL + t->tv_nsec; }
static int test_bit(unsigned bit, const unsigned long *map) { return !!(*map & (1UL << bit)); }
static int test_and_set_bit(unsigned bit, unsigned long *map) {
    int old = test_bit(bit, map) || raced;
    *map |= 1UL << bit;
    return old;
}
static int sprdwl_is_group(const u8 *address) { return address[0] & 1; }
struct sprdwl_intf { unsigned tx_num[MAX_LUT_NUM]; };
struct sk_buff { u8 *data; };
'''
callback = r'''
static void sprdwl_tx_addba(struct sprdwl_intf *intf,
                           struct sprdwl_peer_entry *peer, unsigned char tid) {
    (void)intf;
    assert(tid < 8 && test_bit(tid, &peer->ba_tx_done_map));
    ++calls;
}
static void clock_ms(int64_t ms) {
    clock_now.tv_sec = ms / 1000;
    clock_now.tv_nsec = ms % 1000 * NSEC_PER_MSEC;
}
'''
main = r'''
int main(void) {
    struct sprdwl_intf intf = {0};
    struct sprdwl_peer_entry peer = {0};
    u8 address[6] = {2};
    struct sk_buff skb = {address};
    peer.ht_enable = 1;
    intf.tx_num[6] = 10;
    clock_ms(100000);
    for (unsigned tid = 0; tid < 8; tid++) {
        prepare_addba(&intf, 6, &skb, &peer, tid);
        assert(calls == (int)tid + 1);
        prepare_addba(&intf, 6, &skb, &peer, tid);
        assert(calls == (int)tid + 1);  /* pending or established BA stays intact */
    }
    peer.ba_tx_done_map = 0; /* firmware replied unsuccessful */
    clock_ms(103001);
    prepare_addba(&intf, 6, &skb, &peer, 0);
    assert(calls == 8); /* recorded three-second retry storm is suppressed */
    clock_ms(129999);
    prepare_addba(&intf, 6, &skb, &peer, 0);
    assert(calls == 8);
    clock_ms(130000);
    prepare_addba(&intf, 6, &skb, &peer, 0);
    assert(calls == 9);
    tx_addba_retry_ms = 0;
    unsigned long established = peer.ba_tx_done_map;
    clock_ms(1000000);
    prepare_addba(&intf, 6, &skb, &peer, 1);
    assert(calls == 9 && peer.ba_tx_done_map == established);
    tx_addba_retry_ms = 3000; /* runtime legacy comparator; no reconnect */
    peer.ba_tx_done_map = 0;
    prepare_addba(&intf, 6, &skb, &peer, 1);
    assert(calls == 10);
    peer.ba_tx_done_map = 0;
    clock_ms(1002999);
    prepare_addba(&intf, 6, &skb, &peer, 1);
    assert(calls == 10);
    clock_ms(1003000);
    prepare_addba(&intf, 6, &skb, &peer, 1);
    assert(calls == 11);
    peer.ba_tx_done_map = 0;
    tx_addba_retry_ms = UINT_MAX; /* clamp rather than overflow the ns comparison */
    clock_ms(1302999);
    prepare_addba(&intf, 6, &skb, &peer, 1);
    assert(calls == 11);
    clock_ms(1303000);
    prepare_addba(&intf, 6, &skb, &peer, 1);
    assert(calls == 12);
    peer.ba_tx_done_map = 0;
    tx_addba_retry_ms = 30000;
    prepare_addba(&intf, 255, &skb, &peer, 0);
    prepare_addba(&intf, 6, &skb, &peer, 8);
    prepare_addba(&intf, 6, &skb, NULL, 0);
    assert(calls == 12);
    memset(peer.time, 0, sizeof(peer.time)); /* newly associated peer */
    peer.vowifi_enabled = 1;
    prepare_addba(&intf, 6, &skb, &peer, 0);
    peer.vowifi_enabled = 0;
    peer.ht_enable = 0;
    prepare_addba(&intf, 6, &skb, &peer, 0);
    peer.ht_enable = 1;
    address[0] = 1;
    prepare_addba(&intf, 6, &skb, &peer, 0);
    assert(calls == 12);
    peer.ip_acquired = 1;
    raced = 1; /* another producer claimed the bit after our initial check */
    prepare_addba(&intf, 6, &skb, &peer, 0);
    assert(calls == 12);
    raced = 0;
    peer.ba_tx_done_map = 0;
    prepare_addba(&intf, 6, &skb, &peer, 0);
    assert(calls == 13);
    puts("PASS: actual ADDBA gate; TIDs 0-7; bounded retries; runtime pause/legacy; established BA retained");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='cm311-addba-') as folder:
    code, binary = Path(folder) / 'test.c', Path(folder) / 'test'
    code.write_text(prelude + peer + callback + gate + main)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer', str(code), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
