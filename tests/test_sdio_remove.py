#!/usr/bin/env python3
"""Replay direct SDIO unregister without the legacy remove-card wait path."""
from pathlib import Path
import os
import subprocess
import tempfile
from test_netdev_lifecycle import extract

STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#define CONFIG_MESON_UWE5621 1
#define SDIOHAL_RX_INBAND_IRQ 1
#define SDIOHAL_RX_EXTERNAL_IRQ 0
#define SDIOHAL_REMOVE_CARD_VAL 0x8000
#define FUNC_1 1
#define WRITE_ONCE(field, value) ((field)=(value))
#define sdiohal_info(...) ((void)0)
struct completion { bool initialized; unsigned done; };
struct sdio_func { int dev; };
struct sdiohal_data_t {
    struct completion tx_completed, rx_completed, scan_done, remove_done;
    bool rx_poll_active;
    int xmit_cnt, irq_type, irq_num;
    struct sdio_func *sdio_func[2];
};
static struct sdiohal_data_t data;
static struct sdiohal_data_t *sdiohal_get_data(void) { return &data; }
static void init_completion(struct completion *c) { c->initialized=true; c->done=0; }
static void complete(struct completion *c) { assert(c->initialized); ++c->done; }
#define WCN_CARD_EXIST(counter) (*(counter) < SDIOHAL_REMOVE_CARD_VAL)
static void atomic_add(int n, int *counter) { *counter += n; }
static unsigned claims, releases, irq_releases;
static void sdio_claim_host(struct sdio_func *func) { assert(func && !claims); ++claims; }
static void sdio_release_irq(struct sdio_func *func) { assert(func && claims == 1); ++irq_releases; }
static void sdio_release_host(struct sdio_func *func) { assert(func && claims == 1); --claims; ++releases; }
static void free_irq(int irq, void *dev) { (void)irq; (void)dev; assert(0); }
'''
MAIN = r'''
int main(void) {
    struct sdio_func func={0};
    memset(&data,0,sizeof(data));
    data.rx_poll_active=true; data.irq_type=SDIOHAL_RX_INBAND_IRQ;
    data.sdio_func[FUNC_1]=&func;
    sdiohal_completion_init();
    /* Direct unregister invokes remove without initializing remove_done again. */
    sdiohal_remove(&func);
    assert(data.remove_done.done == 1 && !data.rx_poll_active);
    assert(!WCN_CARD_EXIST(&data.xmit_cnt));
    assert(!claims && irq_releases == 1 && releases == 1);
    assert(data.tx_completed.initialized && data.rx_completed.initialized && data.scan_done.initialized);
    return 0;
}
'''
if __name__ == '__main__':
    root=Path(__file__).resolve().parent.parent
    src=root/'build/uwe5622/unisocwcn/sdio'
    code=STUBS+extract((src/'sdiohal_common.c').read_text(),'sdiohal_completion_init')+'\n'
    code+=extract((src/'sdiohal_main.c').read_text(),'sdiohal_remove')+MAIN
    with tempfile.TemporaryDirectory(prefix='cm311-sdio-remove-') as tmp:
        p=Path(tmp); (p/'test.c').write_text(code)
        subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',
                        '-fsanitize=address,undefined','-fno-omit-frame-pointer',str(p/'test.c'),'-o',str(p/'test')],check=True)
        subprocess.run([str(p/'test')],check=True,timeout=5)
    print('PASS: direct SDIO unregister completes initialized removal waitqueue and releases in-band IRQ')
