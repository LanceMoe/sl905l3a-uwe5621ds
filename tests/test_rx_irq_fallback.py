#!/usr/bin/env python3
"""Exercise actual runtime activation and RX waits with real thread completions."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def function(source, name):
    match = re.search(r'^(?:static )?(?:void|int) ' + name + r'\([^;]*?\)\n\{', source, re.M)
    assert match, name
    pos, depth = match.end(), 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos]


PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#define CONFIG_MESON_UWE5621 1
#define SDIOHAL_RX_POLL_MS 20
#define SDIOHAL_RX_INBAND_IRQ 1
#define SDIOHAL_RX_EXTERNAL_IRQ 0
#define MMC_CAP_SDIO_IRQ 1
#define FUNC_1 1
#define SDIOHAL_BLK_SIZE 512
#define WCN_CARD_EXIST(p) (*(p) < 100)
#define READ_ONCE(x) __atomic_load_n(&(x), __ATOMIC_ACQUIRE)
#define WRITE_ONCE(x,v) __atomic_store_n(&(x), (v), __ATOMIC_RELEASE)
#define sdiohal_info(...) ((void)0)
#define sdiohal_err(...) ((void)0)
#define msecs_to_jiffies(x) (x)
#define atomic_set(p,v) (*(p)=(v))
#define atomic_xchg(p,v) __atomic_exchange_n((p),(v),__ATOMIC_SEQ_CST)
#define THIS_MODULE 0
struct completion { pthread_mutex_t mutex; pthread_cond_t cond; unsigned done; };
struct sdio_func { int dev; unsigned max_blksize; };
struct mmc_host { unsigned caps; };
struct sdiohal_data_t {
    struct completion rx_completed;
    bool rx_poll_active, pwrseq, card_dump_flag;
    struct mmc_host *sdio_dev_host;
    struct sdio_func *sdio_func[2];
    int xmit_cnt, xmit_start, irq_type, irq_num;
};
static struct sdiohal_data_t data;
static struct sdiohal_data_t *sdiohal_get_data(void) { return &data; }
static int irq_error, pm_error, enable_error, poll_timeouts, idle_calls;
static int sdiohal_quarantined;
static int sdiohal_irq_handler_data;
static void sdio_claim_host(struct sdio_func *f) { (void)f; }
static void sdio_release_host(struct sdio_func *f) { (void)f; }
static int sdio_claim_irq(struct sdio_func *f, int handler) {
    (void)f; (void)handler;
    assert(!READ_ONCE(data.rx_poll_active));
    return irq_error;
}
static void sdio_release_irq(struct sdio_func *f) { (void)f; }
static void enable_irq(int irq) { (void)irq; }
static void disable_irq(int irq) { (void)irq; }
static int pm_runtime_get_sync(int *dev) { (void)dev; return pm_error; }
static int pm_runtime_put_sync(int *dev) { (void)dev; return 0; }
static int sdio_enable_func(struct sdio_func *f) { (void)f; return enable_error; }
static void sdio_set_block_size(struct sdio_func *f, int size) { (void)f; (void)size; }
static void sdiohal_set_cp_pin_status(void) { }
static void sdiohal_enable_slave_irq(void) { }
static void sdiohal_rx_poll_timeout(void) { poll_timeouts++; }
static int sdiohal_wait_idle(struct sdiohal_data_t *p) {
    assert(!READ_ONCE(p->rx_poll_active)); idle_calls++; return 0;
}
static void __module_get(int module) { (void)module; assert(0); }
static void complete(struct completion *c) {
    pthread_mutex_lock(&c->mutex); c->done++;
    pthread_cond_broadcast(&c->cond); pthread_mutex_unlock(&c->mutex);
}
static long wait_for_completion_interruptible_timeout(struct completion *c, unsigned ms) {
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_nsec += ms * 1000000UL;
    until.tv_sec += until.tv_nsec / 1000000000UL;
    until.tv_nsec %= 1000000000UL;
    pthread_mutex_lock(&c->mutex);
    int rc = 0;
    while (!c->done && !rc)
        rc = pthread_cond_timedwait(&c->cond, &c->mutex, &until);
    bool done = c->done > 0;
    if (done) c->done--;
    pthread_mutex_unlock(&c->mutex);
    assert(rc == 0 || rc == ETIMEDOUT);
    return done ? 1 : 0;
}
static int wait_for_completion_interruptible(struct completion *c) {
    pthread_mutex_lock(&c->mutex);
    while (!c->done) pthread_cond_wait(&c->cond, &c->mutex);
    c->done--; pthread_mutex_unlock(&c->mutex); return 0;
}
void sdiohal_rx_up(void);
'''

MAIN = r'''
static int returned;
static void *receiver(void *arg) {
    (void)arg; sdiohal_rx_down(); WRITE_ONCE(returned, 1); return NULL;
}
int main(void) {
    struct mmc_host host = {0};
    struct sdio_func func = {0};
    data.rx_completed.mutex = (pthread_mutex_t)PTHREAD_MUTEX_INITIALIZER;
    data.rx_completed.cond = (pthread_cond_t)PTHREAD_COND_INITIALIZER;
    data.sdio_dev_host = &host; data.sdio_func[1] = &func;
    data.irq_type = SDIOHAL_RX_INBAND_IRQ; data.xmit_start = 1;
    pthread_t rx;

    // During firmware download there must be no periodic FIFO reads.
    pthread_create(&rx, NULL, receiver, NULL);
    usleep(60000);
    assert(!READ_ONCE(returned) && !poll_timeouts);
    irq_error = -EIO;
    assert(sdiohal_runtime_get() == -1);
    assert(!READ_ONCE(data.rx_poll_active) && !READ_ONCE(returned));

    // Successful activation releases the receiver's original indefinite wait.
    irq_error = 0;
    assert(sdiohal_runtime_get() == 0);
    pthread_join(rx, NULL);
    assert(READ_ONCE(data.rx_poll_active) && !poll_timeouts);

    // No IRQ: the next receive opportunity arrives within a bounded wait.
    sdiohal_rx_down();
    assert(poll_timeouts == 1);
    // Normal IRQ: consume it immediately without counting a fallback timeout.
    sdiohal_rx_up(); sdiohal_rx_down();
    assert(poll_timeouts == 1);
    assert(sdiohal_runtime_put() == 0 && idle_calls == 1);
    assert(!READ_ONCE(data.rx_poll_active));

    // Hosts with native IRQ support retain normal interrupt-driven waits.
    host.caps = MMC_CAP_SDIO_IRQ;
    assert(sdiohal_runtime_get() == 0 && !READ_ONCE(data.rx_poll_active));
    WRITE_ONCE(returned, 0);
    pthread_create(&rx, NULL, receiver, NULL); usleep(60000);
    assert(!READ_ONCE(returned));
    sdiohal_rx_up(); pthread_join(rx, NULL);
    assert(poll_timeouts == 1);

    // The runtime-PM path also arms only after all startup steps succeed.
    host.caps = 0; data.pwrseq = true;
    pm_error = -EIO;
    assert(sdiohal_runtime_get() == -EIO && !READ_ONCE(data.rx_poll_active));
    pm_error = 0; enable_error = -EIO;
    assert(sdiohal_runtime_get() == -EIO && !READ_ONCE(data.rx_poll_active));
    enable_error = 0;
    assert(sdiohal_runtime_get() == 0 && READ_ONCE(data.rx_poll_active));
    sdiohal_rx_down(); // consumes startup wake
    assert(sdiohal_runtime_put() == 0 && idle_calls == 2);
    assert(!READ_ONCE(data.rx_poll_active));

    data.pwrseq = false; data.irq_type = SDIOHAL_RX_EXTERNAL_IRQ;
    assert(sdiohal_runtime_get() == 0 && !READ_ONCE(data.rx_poll_active));
    puts("PASS: no reads before startup; missing IRQ is bounded; normal IRQ and failure/stop paths preserved");
    return 0;
}
'''


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622'
    main = (source / 'unisocwcn/sdio/sdiohal_main.c').read_text()
    common = (source / 'unisocwcn/sdio/sdiohal_common.c').read_text()
    body = '\n'.join(function(common, name) for name in ('sdiohal_rx_down', 'sdiohal_rx_up'))
    body += '\n' + '\n'.join(function(main, name) for name in
                            ('cm311_rx_poll_start', 'sdiohal_runtime_get', 'sdiohal_runtime_put'))
    with tempfile.TemporaryDirectory(prefix='cm311-rx-fallback-') as folder:
        code, binary = Path(folder) / 'test.c', Path(folder) / 'test'
        code.write_text(PRELUDE + '\n' + body + '\n' + MAIN)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                        '-Werror', '-pthread', str(code), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=5)
