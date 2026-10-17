#!/usr/bin/env python3
"""Compile actual SDIO functions with fake I/O to test error-reference balance.

This exercises control flow, not SDIO hardware or the kernel's scheduler/locks.
Run after scripts/prepare.sh. Requires a native C compiler (CC or cc).
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def function(source, name):
    match = re.search(r'^(?:static )?(?:int|void) ' + name + r'\([^;]*?\)\n\{', source, re.M)
    if not match:
        raise ValueError(f'Cannot find {name}')
    depth = 1
    pos = match.end()
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos]


PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#define CONFIG_ARCH_SUNXI 1 /* Broad ARM64 config, also used by the Meson image. */
#define SDIOHAL_REMOVE_CARD_VAL 0x8000
#define IS_BYPASS_WAKE(addr) false
#define DT_WRITEL 0
#define DT_READL 1
#define DT_WRITE 2
#define DT_READ 3
#define SDIOHAL_RX_INBAND_IRQ 1
#define SDIOHAL_RX_EXTERNAL_IRQ 0
#define FUNC_1 1
#define SDIOHAL_DT_MODE_ADDR 0x100
#define WRITE_ONCE(x,v) ((x)=(v))
#define min(a,b) ((a) < (b) ? (a) : (b))
static void fake_log(const char *format, ...) { (void)format; }
#define sdiohal_err(...) fake_log(__VA_ARGS__)
#define sdiohal_info(...) fake_log(__VA_ARGS__)
typedef struct { int value; } atomic_t;
#define ATOMIC_INIT(x) {x}
static int atomic_read(atomic_t *p) { return p->value; }
static int atomic_inc_return(atomic_t *p) { return ++p->value; }
static void atomic_dec(atomic_t *p) { --p->value; }
static void atomic_set(atomic_t *p, int value) { p->value = value; }
static int atomic_xchg(atomic_t *p, int value) { int old = p->value; p->value = value; return old; }
struct sdio_func { unsigned int cur_blksize; int dev; };
struct sdiohal_data_t {
    atomic_t xmit_cnt, xmit_start;
    bool dt_rw_fail, pwrseq, card_dump_flag, rx_poll_active;
    int irq_type, irq_num;
    struct sdio_func *sdio_func[2];
};
static struct sdio_func card = {.cur_blksize = 512};
static struct sdiohal_data_t data;
static struct sdiohal_data_t *sdiohal_get_data(void) { return &data; }
static int io_result, io_calls, addr_result, drain_after, module_pins, dump_calls;
static atomic_t sdiohal_quarantined = ATOMIC_INIT(0);
#define WCN_CARD_EXIST(x) (atomic_read(x) < SDIOHAL_REMOVE_CARD_VAL)
#define sdio_release_irq(x) ((void)0)
#define disable_irq(x) ((void)0)
#define __module_get(x) (++module_pins)
static int pm_runtime_put_sync(int *dev) { (void)dev; return 0; }
static unsigned long jiffies;
#define msecs_to_jiffies(x) (x)
#define time_after_eq(a,b) ((long)((a)-(b)) >= 0)
static void usleep_range(int low, int high) {
    (void)low; (void)high;
    ++jiffies;
    if (drain_after > 0 && --drain_after == 0) data.xmit_cnt.value = 0;
}
#define sdiohal_resume_check() ((void)0)
#define sdiohal_cp_tx_wakeup(x) ((void)0)
#define sdiohal_cp_rx_wakeup(x) ((void)0)
#define sdiohal_cp_tx_sleep(x) ((void)0)
#define sdiohal_cp_rx_sleep(x) ((void)0)
#define sdiohal_op_enter() ((void)0)
#define sdiohal_op_leave() ((void)0)
#define sdio_claim_host(x) ((void)0)
#define sdio_release_host(x) ((void)0)
#define sdiohal_dump_aon_reg() (++dump_calls)
#define sdiohal_abort() ((void)0)
static int sdiohal_dt_set_addr(unsigned int a) { (void)a; return addr_result; }
static void sdio_writel(struct sdio_func *f, unsigned int b, int a, int *ret) {
    (void)f; (void)b; (void)a; ++io_calls; *ret = io_result;
}
static unsigned int sdio_readl(struct sdio_func *f, int a, int *ret) {
    (void)f; (void)a; ++io_calls; *ret = io_result; return 0;
}
static int sdio_memcpy_toio(struct sdio_func *f, int a, void *b, unsigned int n) {
    (void)f; (void)a; (void)b; (void)n; ++io_calls; return io_result;
}
static int sdio_memcpy_fromio(struct sdio_func *f, void *b, int a, unsigned int n) {
    return sdio_memcpy_toio(f, a, b, n);
}
static unsigned int max_bytes(struct sdio_func *f) { (void)f; return 512; }
'''

MAIN = r'''
static void reset(void) {
    data = (struct sdiohal_data_t){.xmit_start = {1}, .sdio_func = {NULL, &card}};
    io_result = io_calls = addr_result = drain_after = 0;
    module_pins = sdiohal_quarantined.value = 0;
    dump_calls = 0;
    jiffies = 0;
}
static int invoke(int which) {
    unsigned int word = 0;
    switch (which) {
    case 0: return sdiohal_writel(0x100, &word);
    case 1: return sdiohal_readl(0x100, &word);
    case 2: return sdiohal_dt_write(0x100, &word, sizeof(word));
    default: return sdiohal_dt_read(0x100, &word, sizeof(word));
    }
}
int main(void) {
    for (int which = 0; which != 4; ++which) {
        reset();
        assert(invoke(which) == 0);
        assert(data.xmit_cnt.value == 0);

        reset();
        io_result = -ETIMEDOUT;
        assert(invoke(which) == -ETIMEDOUT);
        assert(data.xmit_cnt.value == 0);
#ifdef CONFIG_MESON_UWE5621
        assert(dump_calls == 0);
#else
        assert(dump_calls == 1);
#endif

        reset();
        addr_result = -EIO;
        assert(invoke(which) == -EIO);
        assert(data.xmit_cnt.value == 0);

        reset();
        data.dt_rw_fail = true;
        int ret = invoke(which);
        /* The old early return leaked exactly one reference here. */
        assert(data.xmit_cnt.value == 0);
#ifdef CONFIG_AW_BOARD
        assert(ret == -EIO && io_calls == 0);
#else
        /* ARCH_SUNXI alone must not select the Allwinner shortcut on Meson. */
        assert(ret == 0 && io_calls > 0);
#endif
    }
    reset();
    assert(sdiohal_wait_idle(&data) == 0);
    assert(jiffies == 0);
    data.xmit_cnt.value = 1;
    drain_after = 3;
    assert(sdiohal_wait_idle(&data) == 0 && jiffies == 3);

    reset();
    data.xmit_cnt.value = 1;
    assert(sdiohal_wait_idle(&data) == -ETIMEDOUT);
    assert(jiffies == 2000 && data.xmit_cnt.value == 1);

    reset();
    jiffies = ULONG_MAX - 1000;
    data.xmit_cnt.value = 1;
    assert(sdiohal_wait_idle(&data) == -ETIMEDOUT);
    assert(data.xmit_cnt.value == 1);

    reset();
    data.irq_type = SDIOHAL_RX_INBAND_IRQ;
    assert(sdiohal_runtime_put() == 0 && module_pins == 0);
    assert(data.xmit_start.value == 0);
    reset();
    data.irq_type = SDIOHAL_RX_INBAND_IRQ;
    data.xmit_cnt.value = 1;
    assert(sdiohal_runtime_put() == -ETIMEDOUT);
    assert(data.xmit_start.value == 0 && data.card_dump_flag);
    assert(data.xmit_cnt.value == 1 && module_pins == 1);
    assert(sdiohal_runtime_put() == -ETIMEDOUT && module_pins == 1);
    puts("PASS: SDIO error references and bounded drain");
    return 0;
}
'''


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622/unisocwcn/sdio/sdiohal_main.c'
    source = path.read_text()
    body = '\n'.join(function(source, name) for name in (
        'sdiohal_card_lock', 'sdiohal_card_unlock', 'sdiohal_writel', 'sdiohal_readl',
        'sdiohal_dt_write', 'sdiohal_dt_read', 'sdiohal_wait_idle', 'sdiohal_runtime_put'))
    with tempfile.TemporaryDirectory(prefix='cm311-sdio-regression-') as directory:
        src, binary = Path(directory) / 'test.c', Path(directory) / 'test'
        src.write_text(PRELUDE + '\n' + body + '\n' + MAIN)
        for flags in (['-DCONFIG_MESON_UWE5621=1'], ['-DCONFIG_AW_BOARD=1']):
            print('Testing ' + ('Allwinner error shortcut' if 'AW_BOARD' in flags[0] else 'Meson with multi-platform config'), flush=True)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', *flags,
                            str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
