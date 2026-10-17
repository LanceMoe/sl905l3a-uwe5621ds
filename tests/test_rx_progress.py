#!/usr/bin/env python3
"""Actual SDMA helpers: stale trailers, zero progress and bounded draining."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def function(source, name):
    match = re.search(r'^(?:static )?(?:int|void) ' + name + r'\([^;]*?\)\n\{', source, re.M)
    assert match, name
    pos, depth = match.end(), 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos]


PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static void fake_log(const char *format, ...) { (void)format; }
#define pr_err_ratelimited(...) fake_log(__VA_ARGS__)
static int io_status, parse_status, reads, parses, no_progress;
static unsigned expected_valid, expected_pending;
static void sdiohal_rx_no_progress(void) { ++no_progress; }
static int sdiohal_sdio_pt_read(unsigned char *buf, unsigned length) {
    ++reads;
    if (io_status) return io_status; // old contents deliberately remain intact
    *(unsigned *)(buf+length-8) = expected_valid;
    *(unsigned *)(buf+length-4) = expected_pending;
    return 0;
}
static int sdiohal_rx_buf_parser(char *buf, unsigned valid, unsigned capacity) {
    (void)buf;
    assert(valid == expected_valid && capacity == 1528);
    ++parses; return parse_status;
}
struct net_device { unsigned mtu, max_mtu; };
'''

MAIN = r'''
int main(void) {
    uint32_t buffer[384] = {0};
    unsigned valid=99, pending=8188, drained=0;
    assert(sdiohal_sdma_rx_packet((char *)buffer,7,&valid,&pending) == -EINVAL);
    assert(!valid && !pending && !reads && !parses);

    expected_valid=64; expected_pending=8188;
    assert(sdiohal_sdma_rx_packet((char *)buffer,1536,&valid,&pending) == 0);
    assert(valid == 64 && pending == 8188 && reads == 1 && parses == 1);
    assert(sdiohal_rx_drain_next(0,valid,pending,&drained) == 1);

    // A failed read must not re-dispatch an old response or re-use pending=8188.
    io_status = -EILSEQ;
    assert(sdiohal_sdma_rx_packet((char *)buffer,1536,&valid,&pending) == -EILSEQ);
    assert(!valid && !pending && parses == 1);
    assert(sdiohal_rx_drain_next(-EILSEQ,valid,pending,&drained) < 0);

    io_status = 0; expected_valid=0; expected_pending=8188;
    assert(sdiohal_sdma_rx_packet((char *)buffer,1536,&valid,&pending) == 0);
    drained=0;
    assert(sdiohal_rx_drain_next(0,valid,pending,&drained) < 0);
    assert(no_progress == 1 && drained == 0);
    // In the recorded state the inner loop now ends after ONE empty read.
    unsigned attempts=0;
    do { ++attempts; } while (sdiohal_rx_drain_next(0,0,8188,&drained) > 0);
    assert(attempts == 1);

    expected_valid=9999; parse_status=-EPROTO;
    assert(sdiohal_sdma_rx_packet((char *)buffer,1536,&valid,&pending) == -EPROTO);
    assert(pending == 0);
    assert(sdiohal_rx_drain_next(-EPROTO,valid,pending,&drained) < 0);

    // Even an endlessly productive FIFO must yield within 64 reads.
    drained=0; attempts=0;
    do { ++attempts; } while (sdiohal_rx_drain_next(0,64,8188,&drained) > 0);
    assert(attempts == 64);
    assert(sdiohal_rx_drain_next(0,0,0,&drained) == 0);

    struct net_device dev = {1500,65535};
    sprdwl_meson_limit_mtu(&dev);
    assert(dev.mtu == 1400 && dev.max_mtu == 1400);
    puts("PASS: no stale-trailer dispatch; zero-payload and productive loops bounded; experimental MTU capped");
    return 0;
}
'''


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622'
    rx = (source / 'unisocwcn/sdio/sdiohal_rx.c').read_text()
    main = (source / 'unisocwifi/main.c').read_text()
    body = '\n'.join(function(rx, name) for name in ('sdiohal_sdma_rx_packet', 'sdiohal_rx_drain_next'))
    body += '\n' + function(main, 'sprdwl_meson_limit_mtu')
    with tempfile.TemporaryDirectory(prefix='cm311-rx-progress-') as folder:
        code, binary = Path(folder) / 'test.c', Path(folder) / 'test'
        code.write_text(PRELUDE + '\n' + body + '\n' + MAIN)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-DCONFIG_MESON_UWE5621=1', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', str(code), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=5)
