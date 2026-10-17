#!/usr/bin/env python3
"""Exercise actual command/core lifecycle C functions with fake kernel objects.

The completion stub rejects use before initialization, as in the reported Oops.
This tests failure unwinding, not kernel concurrency or hardware communication.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def function(source, name):
    match = re.search(r'^(?:static )?(?:struct sprdwl_priv \*|int |void )' +
                      name + r'\([^;]*?\)\n\{', source, re.M)
    if not match:
        raise ValueError(f'Cannot find {name}')
    depth, pos = 1, match.end()
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos]


PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define CONFIG_PM 1
#define KERNEL_VERSION(a,b,c) (((a)<<16)|((b)<<8)|(c))
#define LINUX_VERSION_CODE KERNEL_VERSION(6,18,37)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,y) ((x)=(y))
#define SPRDWL_CMD_EXIT_VAL 0x8000
#define NET_IP_ALIGN 2
#define SPRDWL_SKB_HEAD_RESERV_LEN 16
#define ERR_PTR(x) ((void *)(intptr_t)(x))
#define PTR_ERR(x) ((intptr_t)(x))
#define IS_ERR_OR_NULL(x) (!(x) || (uintptr_t)(x) >= (uintptr_t)-4095)
static void fake_log(const char *format, ...) { (void)format; }
#define wl_err(...) fake_log(__VA_ARGS__)
#define wl_info(...) fake_log(__VA_ARGS__)
#define pr_err(...) fake_log(__VA_ARGS__)
typedef struct { int value; } atomic_t;
static void atomic_set(atomic_t *p, int n) { p->value = n; }
static void atomic_add(int n, atomic_t *p) { p->value += n; }
static int atomic_read(atomic_t *p) { return p->value; }
struct completion { bool initialized; int completed; };
static void init_completion(struct completion *p) { p->initialized = true; p->completed = 0; }
static void complete(struct completion *p) { assert(p->initialized); ++p->completed; }
static void mutex_init(int *p) { assert(!*p); *p = 1; }
static void mutex_destroy(int *p) { assert(*p); *p = 0; }
static void spin_lock_init(int *p) { *p = 1; }
static void spin_lock_bh(int *p) { assert(*p); }
static void spin_unlock_bh(int *p) { assert(*p); }
#define kfree free
static unsigned long jiffies;
#define msecs_to_jiffies(x) (x)
#define time_after(a,b) ((long)((a)-(b)) > 0)
static void usleep_range(int a, int b) { (void)a; (void)b; ++jiffies; }
struct wakeup_source { bool live; };
static struct wakeup_source wake_source;
static bool fail_wakeup;
static int wake_registers, wake_unregisters;
static struct wakeup_source *wakeup_source_register(void *dev, const char *name) {
    (void)dev; (void)name; ++wake_registers;
#ifdef CONFIG_PM_SLEEP
    if (fail_wakeup) return NULL;
    assert(!wake_source.live); wake_source.live = true; return &wake_source;
#else
    return NULL; /* Same result as the !PM_SLEEP kernel stub. */
#endif
}
static void wakeup_source_unregister(struct wakeup_source *p) {
    assert(p && p->live); p->live = false; ++wake_unregisters;
}
struct sprdwl_cmd {
    bool init_ok;
    void *data;
    struct wakeup_source *wake_lock;
    atomic_t refcnt;
    int lock, cmd_lock, mstime, cmd_id;
    struct completion completed;
};
static struct sprdwl_cmd g_sprdwl_cmd;
enum sprdwl_hw_type { SPRDWL_HW_SDIO };
struct sprdwl_if_ops { int unused; };
struct sprdwl_data_hdr { int unused; };
struct wiphy;
struct sprdwl_priv {
    struct wiphy *wiphy;
    enum sprdwl_hw_type hw_type;
    int skb_head_len;
    struct sprdwl_if_ops *if_ops;
    int scan_timer, scan_lock, sched_scan_lock, list_lock, vif_list;
};
struct wiphy { struct sprdwl_priv priv; };
static struct sprdwl_priv *g_sprdwl_priv;
static int sprdwl_cfg80211_ops;
static bool fail_wiphy;
static int work_error, wiphy_allocations, wiphy_frees, live_work;
static void sprdwl_check_intf_ops(struct sprdwl_if_ops *ops) { assert(ops); }
static struct wiphy *wiphy_new(void *ops, size_t size) {
    (void)ops; assert(size == sizeof(struct sprdwl_priv)); ++wiphy_allocations;
    assert(g_sprdwl_cmd.init_ok && g_sprdwl_cmd.completed.initialized);
    return fail_wiphy ? NULL : calloc(1, sizeof(struct wiphy));
}
static struct sprdwl_priv *wiphy_priv(struct wiphy *p) { return &p->priv; }
static void wiphy_free(struct wiphy *p) { assert(p); ++wiphy_frees; free(p); }
#define timer_setup(p,cb,flags) (*(p)=1)
#define INIT_LIST_HEAD(p) (*(p)=1)
static int sprdwl_init_work(struct sprdwl_priv *p) {
    assert(p); if (work_error) return work_error; ++live_work; return 0;
}
static void sprdwl_deinit_work(struct sprdwl_priv *p) { assert(p && live_work == 1); --live_work; }
'''

MAIN = r'''
int main(void) {
    struct sprdwl_if_ops ops = {0};
    struct sprdwl_priv *priv;
    /* Cleanup after an early failure must not complete an uninitialized object. */
    sprdwl_cmd_wake_upall();
    sprdwl_cmd_deinit();
    assert(!g_sprdwl_cmd.completed.initialized);
    assert(sprdwl_cmd_init() == 0);
    assert(g_sprdwl_cmd.init_ok && g_sprdwl_cmd.completed.initialized);
    assert(sprdwl_cmd_init() == -EBUSY);
    sprdwl_cmd_wake_upall();
    assert(g_sprdwl_cmd.completed.completed == 1);
    g_sprdwl_cmd.data = malloc(8);
    sprdwl_cmd_deinit();
    assert(!g_sprdwl_cmd.init_ok && !g_sprdwl_cmd.data && !g_sprdwl_cmd.wake_lock);
    assert(g_sprdwl_cmd.completed.completed == 2);
    sprdwl_cmd_deinit(); /* Must not destroy the mutex a second time. */
    sprdwl_cmd_wake_upall();
    assert(g_sprdwl_cmd.completed.completed == 2);
#ifdef CONFIG_PM_SLEEP
    assert(wake_registers == 1 && wake_unregisters == 1);
    fail_wakeup = true;
    assert(sprdwl_cmd_init() == -ENOMEM);
    sprdwl_cmd_deinit();
    priv = sprdwl_core_create(SPRDWL_HW_SDIO, &ops);
    assert(PTR_ERR(priv) == -ENOMEM && wiphy_allocations == 0);
    assert(!g_sprdwl_cmd.init_ok && !g_sprdwl_priv);
    fail_wakeup = false;
#else
    assert(wake_registers == 0 && wake_unregisters == 0);
#endif
    fail_wiphy = true;
    priv = sprdwl_core_create(SPRDWL_HW_SDIO, &ops);
    assert(PTR_ERR(priv) == -ENOMEM);
    assert(!g_sprdwl_cmd.init_ok && !g_sprdwl_priv && !live_work);
    fail_wiphy = false;
    work_error = -EIO;
    priv = sprdwl_core_create(SPRDWL_HW_SDIO, &ops);
    assert(PTR_ERR(priv) == -EIO);
    assert(wiphy_frees == 1 && !g_sprdwl_priv && !g_sprdwl_cmd.init_ok);
    work_error = 0;
    priv = sprdwl_core_create(SPRDWL_HW_SDIO, &ops);
    assert(!IS_ERR_OR_NULL(priv) && g_sprdwl_priv == priv && live_work == 1);
    sprdwl_core_free(NULL);
    sprdwl_core_free(ERR_PTR(-ENOMEM));
    assert(live_work == 1 && g_sprdwl_cmd.init_ok);
    sprdwl_core_free(priv);
    assert(!g_sprdwl_priv && !live_work && !g_sprdwl_cmd.init_ok && wiphy_frees == 2);
    assert(!wake_source.live);
    puts("PASS: command initialization and core allocation/unwind");
    return 0;
}
'''


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622'
    cmd = (source / 'unisocwifi/cmdevt.c').read_text()
    cfg = (source / 'unisocwifi/cfg80211.c').read_text()
    body = '\n'.join(function(cmd, name) for name in (
        'sprdwl_cmd_init', 'sprdwl_cmd_wake_upall', 'sprdwl_cmd_clean', 'sprdwl_cmd_deinit'))
    body += '\n' + '\n'.join(function(cfg, name) for name in (
        'sprdwl_core_create', 'sprdwl_core_free'))
    with tempfile.TemporaryDirectory(prefix='cm311-command-regression-') as directory:
        src, binary = Path(directory) / 'test.c', Path(directory) / 'test'
        src.write_text(PRELUDE + '\n' + body + '\n' + MAIN)
        for flags in ([], ['-DCONFIG_PM_SLEEP=1']):
            print('Testing ' + ('PM_SLEEP enabled' if flags else 'PM enabled, PM_SLEEP disabled'), flush=True)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                            '-Wno-unused-function', '-Wno-unused-variable', *flags,
                            str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
