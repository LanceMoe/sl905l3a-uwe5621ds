#!/usr/bin/env python3
"""Exercise actual RX/TX code with the kernel's real CMD53 splitting helper.

Fake CP packets each carry their own trailer. This reproduces a response lost
by reading only the trailer of the last split, without emulating RF hardware.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def function(source, name):
    match = re.search(r'^(?:static )?(?:unsigned int|int|void) ' + name + r'\([^;]*?\)\n\{', source, re.M)
    if not match:
        raise ValueError(name)
    pos, depth = match.end(), 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos]


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
#define CONFIG_MESON_UWE5621 1
#define BITS_PER_LONG 64
#define PAGE_SIZE 4096
#define MAX_PAC_SIZE 2048U
#define SDIOHAL_TX_SENDBUF_LEN 204800U
#define SDIOHAL_RX_RECVBUF_LEN 204800U
#define SDIOHAL_BLK_SIZE 512U
#define SDIOHAL_ALIGN_BLK(x) (((x)+511U)&~511U)
#define SDIOHAL_ALIGN_4BYTE(x) (((x)+3U)&~3U)
#define SDIO_PUB_HEADER_SIZE 4
#define SDIO_CHN_TX_NUM 12
#define SDIO_CHANNEL_NUM 26
#define SDIOHAL_DATA_LEVEL 8
#define FUNC_1 1
#define MMC_QUIRK_BROKEN_BYTE_MODE_512 1
#define min(a,b) ((a)<(b)?(a):(b))
#define min_t(t,a,b) min((t)(a),(t)(b))
#define mmc_blksz_for_byte_mode(c) false
#define mmc_card_broken_byte_mode_512(c) ((c)->quirks & MMC_QUIRK_BROKEN_BYTE_MODE_512)
static void fake_log(const char *format, ...) { (void)format; }
#define pr_err_ratelimited(...) fake_log(__VA_ARGS__)
#define sdiohal_debug(...) fake_log(__VA_ARGS__)
struct device_driver { const char *name; };
struct device { struct device_driver *driver; };
struct mmc_host { unsigned max_blk_count, max_req_size, max_seg_size, max_blk_size; struct device *parent; };
struct mmc_card { struct mmc_host *host; unsigned quirks; struct { bool multi_block; } cccr; };
struct sdio_func { struct mmc_card *card; unsigned cur_blksize, max_blksize, num; };
struct mbuf_t { struct mbuf_t *next; unsigned char *buf; unsigned short len; };
struct sdiohal_list_t { struct mbuf_t *mbuf_head, *mbuf_tail; unsigned node_num; };
struct sdiohal_sendbuf_t { unsigned char *buf; unsigned used_len; };
struct sdiohal_data_t {
    struct sdio_func *sdio_func[2];
    unsigned dtbs, rx_packer_cnt;
    struct { int pagecnt_bias; } frag_ctl;
    struct sdiohal_sendbuf_t send_buf;
    unsigned char *eof_buf;
};
static struct device_driver driver = {.name="meson-gx-mmc"};
static struct device parent = {.driver=&driver};
static struct mmc_host host = {3,1536,1536,512,&parent};
static struct mmc_card card = {.host=&host, .cccr.multi_block=true};
static struct sdio_func func = {&card,512,512,1};
static struct sdiohal_data_t data;
static struct sdiohal_data_t *sdiohal_get_data(void) { return &data; }
#define kfree free
#define sdiohal_sdma_enter() ((void)0)
#define sdiohal_sdma_leave() ((void)0)
static int assignments, last_channel, last_length;
static bool fail_allocation;
static struct mbuf_t rx_nodes[16];
static struct sdiohal_list_t *sdiohal_get_rx_mbuf_node(int n) {
    struct sdiohal_list_t *list;
    assert(n == 1 && assignments < 16);
    if (fail_allocation) return NULL;
    list = calloc(1,sizeof(*list)); assert(list);
    list->mbuf_head = list->mbuf_tail = &rx_nodes[assignments]; return list;
}
static int sdiohal_hwtype_to_channel(int inout, unsigned type, unsigned subtype) {
    (void)type; assert(!inout); return subtype + 12;
}
static void sdiohal_print_list_data(int ch, struct sdiohal_list_t *list, const char *fn, int level) {
    (void)ch; (void)list; (void)fn; (void)level;
}
'''

STUBS = r'''
static void sdiohal_data_list_assignment(struct mbuf_t *m, struct sdio_puh_t *h, int channel) {
    assert(m && h); ++assignments; last_channel = channel; last_length = h->len;
}
static unsigned requests, request_bytes[16], sent_packets, popped, pop_calls;
static bool response_on_first;
static int send_error;
static int mmc_io_rw_extended(struct mmc_card *c, int write, unsigned fn,
    unsigned addr, int increment, u8 *buf, unsigned blocks, unsigned blocksize) {
    unsigned bytes = blocks ? blocks * blocksize : blocksize;
    (void)c; (void)fn; (void)addr; (void)increment; (void)write;
    assert(requests < 16 && bytes <= 1536);
    request_bytes[requests++] = bytes;
    memset(buf,0,bytes);
    if (response_on_first && requests == 1) {
        struct sdio_puh_t *h = (void *)buf;
        h->len = 64; h->subtype = 10;
        memcpy(buf + 4,"RESPONSE",8);
        *(uint32_t *)(buf + bytes - 8) = 64;
    }
    return 0;
}
static int sdiohal_send(struct sdiohal_sendbuf_t *buf, struct sdiohal_list_t *list) {
    unsigned offset = 0, span, bytes = SDIOHAL_ALIGN_BLK(buf->used_len);
    struct sdio_puh_t *h;
    (void)list;
    assert(bytes <= 1536 && bytes >= 512 && requests < 16);
    request_bytes[requests++] = bytes;
    for (;;) {
        assert(offset + 4 <= buf->used_len);
        h = (void *)(buf->buf + offset);
        if (h->eof) break;
        span = 4 + SDIOHAL_ALIGN_4BYTE(h->len);
        assert(offset + span <= bytes && h->len);
        offset += span; ++sent_packets;
    }
    return send_error;
}
static int sdiohal_tx_list_denq(struct sdiohal_list_t *list) {
    popped += list->node_num; ++pop_calls; return 0;
}
'''

MAIN = r'''
static void reset(void) {
    assignments = last_channel = last_length = 0;
    requests = sent_packets = popped = pop_calls = 0;
    memset(request_bytes,0,sizeof(request_bytes));
    data.frag_ctl.pagecnt_bias = 100;
    data.rx_packer_cnt = 0;
    fail_allocation = false; send_error = 0;
}
static int transmit(const unsigned *lengths, unsigned count, bool reclaim) {
    struct sdiohal_list_t list = {.node_num=count};
    struct mbuf_t nodes[8] = {0};
    unsigned char sendbuf[4096] = {0}, eof[4] = {0};
    unsigned i;
    int ret;
    assert(count <= 8);
    data.send_buf.buf = sendbuf;
    data.eof_buf = eof;
    ((struct sdio_puh_t *)eof)->eof = 1;
    for (i=0;i<count;i++) {
        nodes[i].len = lengths[i];
        nodes[i].buf = calloc(1,lengths[i]+8); assert(nodes[i].buf);
        ((struct sdio_puh_t *)nodes[i].buf)->len = lengths[i];
        nodes[i].next = i+1<count ? &nodes[i+1] : NULL;
    }
    list.mbuf_head = count ? &nodes[0] : NULL;
    list.mbuf_tail = count ? &nodes[count-1] : NULL;
    ret = sdiohal_meson_tx_send(&list,reclaim);
    assert(data.send_buf.used_len == 0);
    for (i=0;i<count;i++) free(nodes[i].buf);
    return ret;
}
int main(void) {
    unsigned char buf[2048] = {0};
    struct sdio_puh_t *h;
    const unsigned grouped[] = {700,700,700};
    const unsigned oversize[] = {1530,100,700};
    const unsigned exact[] = {1528};
    data.sdio_func[1] = &func;
    assert(sdiohal_packet_limit() == 1536);
    driver.name = "cm311-sdio-rx";
    host.max_req_size = host.max_seg_size = 4096; host.max_blk_count = 8;
    assert(sdiohal_packet_limit() == 1536 && sdiohal_rx_packet_limit() == 4096);
    driver.name = "meson-gx-mmc";
    assert(sdiohal_rx_packet_limit() == 1536);
    host.max_req_size = host.max_seg_size = 1536; host.max_blk_count = 3;
    host.max_seg_size = 1024; assert(sdiohal_packet_limit() == 1024);
    host.max_seg_size = 1536; host.max_req_size = 1024;
    assert(sdiohal_packet_limit() == 1024);
    host.max_req_size = 1536;
    card.cccr.multi_block = false; assert(sdiohal_packet_limit() == 512);
    card.quirks = MMC_QUIRK_BROKEN_BYTE_MODE_512;
    assert(sdiohal_packet_limit() == 0);
    card.cccr.multi_block = true; card.quirks = 0;

    /* Exact old behavior: kernel splits 2048 -> 1536 + 512. The response
     * trailer at offset 1528 disappears when only offset 2040 is inspected. */
    reset(); response_on_first = true;
    assert(sdio_io_rw_ext_helper(&func,0,0x20,0,buf,2048) == 0);
    assert(requests == 2 && request_bytes[0] == 1536 && request_bytes[1] == 512);
    assert(*(uint32_t *)(buf+1528) == 64 && *(uint32_t *)(buf+2040) == 0);
    assert(sdiohal_rx_buf_parser((char *)buf,*(uint32_t *)(buf+2040),2040) == 0);
    assert(!assignments);

    reset(); sdiohal_rx_adapt_set_dtbs(0);
    assert(data.dtbs == 1536 && sdiohal_rx_adapt_get() == 1536);
    assert(sdio_io_rw_ext_helper(&func,0,0x20,0,buf,sdiohal_rx_adapt_get()) == 0);
    assert(requests == 1 && request_bytes[0] == 1536);
    assert(sdiohal_rx_buf_parser((char *)buf,*(uint32_t *)(buf+1528),1528) == 0);
    assert(assignments == 1 && last_channel == 22 && last_length == 64);
    sdiohal_rx_adapt_set_dtbs(1424); assert(sdiohal_rx_adapt_get() == 1536);
    sdiohal_rx_adapt_set_dtbs(100000); assert(sdiohal_rx_adapt_get() == 1536);

    reset(); memset(buf,0,sizeof(buf)); h = (void *)buf;
    /* Hardware log shape: valid=40 next=1516 in a 1536 read. The 40-byte
     * packet is wholly present; the following head is not and must not
     * fail or consume the completed packet. */
    h->len = 40; h->subtype = 10; memcpy(buf+4, "SMALL", 5);
    ((struct sdio_puh_t *)(buf+44))->len = 1600;
    ((struct sdio_puh_t *)(buf+44))->subtype = 10;
    ((struct sdio_puh_t *)(buf+44))->type = 0;
    assert(sdiohal_rx_buf_parser((char *)buf,40+1600,1528) == 0);
    assert(assignments == 1 && last_length == 40 && last_channel == 22);
    h->len = 2000; h->subtype = 10;
    assert(sdiohal_rx_buf_parser((char *)buf,2000,1528) == 0 && assignments == 1);
    assert(sdiohal_rx_buf_parser((char *)buf,64,1528) == 0 && assignments == 1);
    h->len = 0; assert(sdiohal_rx_buf_parser((char *)buf,4,1528) == 0);
    h->len = 64; h->subtype = 15;
    assert(sdiohal_rx_buf_parser((char *)buf,64,1528) == -EPROTO);
    h->subtype = 10; fail_allocation = true;
    assert(sdiohal_rx_buf_parser((char *)buf,64,1528) == -ENOMEM);
    fail_allocation = false;
    h->len = 3; h->check_sum = 1;
    assert(sdiohal_rx_buf_parser((char *)buf,3,1528) == 0 && last_length == 5);

    reset(); assert(transmit(grouped,3,true) == 0);
    assert(requests == 2 && request_bytes[0] == 1536 && request_bytes[1] == 1024);
    assert(sent_packets == 3 && popped == 3 && pop_calls == 1);
    reset(); assert(transmit(oversize,3,true) == -EMSGSIZE);
    assert(requests == 1 && sent_packets == 2 && popped == 3);
    reset(); assert(transmit(exact,1,false) == 0);
    assert(requests == 1 && request_bytes[0] == 1536 && sent_packets == 1 && !pop_calls);
    reset(); send_error = -EIO;
    assert(transmit(grouped,3,true) == -EIO && popped == 3);
    puts("PASS: kernel split reproduces lost trailer; capped RX, bounded parsing and packet-boundary TX");
    return 0;
}
'''


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622'
    main = (source / 'unisocwcn/sdio/sdiohal_main.c').read_text()
    rx = (source / 'unisocwcn/sdio/sdiohal_rx.c').read_text()
    tx = (source / 'unisocwcn/sdio/sdiohal_tx.c').read_text()
    common = (source / 'unisocwcn/sdio/sdiohal_common.c').read_text()
    bus = (source / 'unisocwcn/include/wcn_bus.h').read_text()
    header = re.search(r'struct sdio_puh_t \{.*?\n\};', bus, re.S).group()
    kernel = (root / 'tests/fixtures/sdio_io_6_18_37.c').read_text()
    body = function(main, 'sdiohal_packet_limit') + '\n' + function(main, 'sdiohal_rx_packet_limit')
    body += '\n' + '\n'.join(function(rx, name) for name in (
        'sdiohal_rx_adapt_get', 'sdiohal_rx_adapt_set_dtbs',
        'sdiohal_rx_dispatch_span', 'sdiohal_rx_buf_parser'))
    body += '\n' + '\n'.join(function(common, name) for name in (
        'sdiohal_tx_packer', 'sdiohal_tx_set_eof'))
    body += '\n' + function(tx, 'sdiohal_meson_tx_send')
    with tempfile.TemporaryDirectory(prefix='cm311-sdio-packet-') as directory:
        src, binary = Path(directory) / 'test.c', Path(directory) / 'test'
        src.write_text(PRELUDE + '\n' + header + '\n' + STUBS + '\n' + kernel + '\n' + body + '\n' + MAIN)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(src), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
