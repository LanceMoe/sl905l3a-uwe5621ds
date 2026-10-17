#!/usr/bin/env python3
"""Run actual command send functions with fake transport and failure injection.

ASan/UBSan check the timeout path after the transport releases the TX buffer.
Requires a native GCC/Clang with sanitizers; does not exercise real firmware.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from test_cmd_lifecycle import function

PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define WIFI_CMD_OPEN 1
#define WIFI_CMD_CLOSE 2
#define WIFI_CMD_SYNC_VERSION 3
#define CMD_RSP_TIMEOUT_ERROR 4
#define HANG_RECOVERY_END 0
#define SPRDWL_MODE_NONE 0
#define SPRDWL_HEAD_RSP 1
#define CMD_WAIT_TIMEOUT 20
#define min(a,b) ((a)<(b)?(a):(b))
#define le16_to_cpu(x) (x)
#define le32_to_cpu(x) (x)
static void fake_log(const char *format, ...) { (void)format; }
#define wl_err(...) fake_log(__VA_ARGS__)
#define wl_info(...) fake_log(__VA_ARGS__)
#define wl_warn(...) fake_log(__VA_ARGS__)
struct sprdwl_cmd_hdr {
    u8 cmd_id;
    struct { u8 ctx_id; } common;
    u16 plen;
    u32 mstime;
    u8 paydata[8];
};
struct sprdwl_cmd { void *data; u32 mstime; int completed; };
static struct sprdwl_cmd g_sprdwl_cmd;
struct sprdwl_tx_msg { int cmd_send, hang_recovery_status; };
struct sprdwl_intf { void *sprdwl_tx; int cp_asserted; };
struct sprdwl_priv { void *hw_priv; int hw_offset; };
struct sprdwl_vif { struct sprdwl_priv *priv; };
struct sprdwl_msg_buf { void *tran_data, *data; };
struct sprdwl_cmd_api_t { u8 version[4]; };
static int api_error, lock_error, lock_calls, send_error, response_error;
static int sends, releases, unlocks, free_buffers, response_length = 4, fill_calls;
static void *expected_free;
static struct sprdwl_cmd_hdr response;
static int sprdwl_api_available_check(struct sprdwl_priv *p, struct sprdwl_msg_buf *m) {
    (void)p; (void)m; return api_error;
}
static int sprdwl_cmd_lock(struct sprdwl_cmd *c) { (void)c; ++lock_calls; return lock_error; }
static void sprdwl_cmd_unlock(struct sprdwl_cmd *c) { (void)c; ++unlocks; }
static void sprdwl_intf_free_msg_buf(struct sprdwl_priv *p, struct sprdwl_msg_buf *m) {
    (void)p; ++releases;
    m->tran_data = (void *)1; /* Releasing the pool entry invalidates its fields. */
}
static void fake_kfree(void *p) { assert(p == expected_free); ++free_buffers; free(p); }
#define kfree fake_kfree
static void reinit_completion(int *p) { *p = 0; }
static const char *cmd2str(int id) { (void)id; return "test"; }
static int sprdwl_cmd_send_to_ic(struct sprdwl_priv *p, struct sprdwl_msg_buf *m) {
    (void)p; ++sends; g_sprdwl_cmd.mstime = 123;
    free(m->tran_data); /* TX ownership transfers to transport, even on error. */
    m->tran_data = NULL;
    return send_error;
}
static int sprdwl_timeout_recv_rsp(struct sprdwl_priv *p, unsigned int t) {
    (void)p; (void)t;
    response.plen = sizeof(response) + response_length;
    memcpy(response.paydata, "abcd", 4);
    g_sprdwl_cmd.data = &response;
    return response_error;
}
static int sprdwl_atcmd_assert(struct sprdwl_priv *p, u8 ctx, u8 id, u8 why) {
    (void)p; (void)ctx; (void)id; (void)why; assert(false); return 0;
}
static struct sprdwl_vif *ctx_id_to_vif(struct sprdwl_priv *p, u8 ctx) { (void)p; (void)ctx; return NULL; }
static void sprdwl_send_assert_cmd(struct sprdwl_vif *v, u8 id, u8 why) { (void)v; (void)id; (void)why; assert(false); }
static void sprdwl_put_vif(struct sprdwl_vif *v) { (void)v; }
static struct sprdwl_msg_buf message;
static struct sprdwl_msg_buf *sprdwl_cmd_getbuf(struct sprdwl_priv *p, unsigned int len, int mode, int rsp, int id) {
    struct sprdwl_cmd_hdr *h;
    (void)p; (void)len; (void)mode; (void)rsp;
    h = calloc(1, sizeof(*h)); assert(h); h->cmd_id = id;
    message.tran_data = h; message.data = h->paydata;
    expected_free = h; return &message;
}
static void sprdwl_fill_drv_api_version(struct sprdwl_priv *p, struct sprdwl_cmd_api_t *a) { (void)p; (void)a; }
static void sprdwl_fill_fw_api_version(struct sprdwl_priv *p, struct sprdwl_cmd_api_t *a) { (void)p; (void)a; ++fill_calls; }
'''

MAIN = r'''
int main(void) {
    struct sprdwl_tx_msg tx = {0};
    struct sprdwl_intf intf = {.sprdwl_tx = &tx};
    struct sprdwl_priv priv = {.hw_priv = &intf};
    struct sprdwl_msg_buf *msg;
    u16 len = 4;
    u8 buf[4] = {0};
    lock_error = -ESHUTDOWN;
    msg = sprdwl_cmd_getbuf(&priv, 4, 0, 1, WIFI_CMD_SYNC_VERSION);
    assert(sprdwl_cmd_send_recv(&priv, msg, 20, buf, &len) == -ESHUTDOWN);
    assert(!len && releases == 1 && free_buffers == 1 && !sends);
    api_error = -EOPNOTSUPP;
    lock_error = 0;
    msg = sprdwl_cmd_getbuf(&priv, 4, 0, 1, WIFI_CMD_SYNC_VERSION);
    assert(sprdwl_cmd_send_recv(&priv, msg, 20, NULL, NULL) == -EOPNOTSUPP);
    assert(lock_calls == 1 && releases == 2 && free_buffers == 2 && !sends);
    api_error = 0;
    lock_error = -EBUSY;
    msg = sprdwl_cmd_getbuf(&priv, 4, 0, 1, WIFI_CMD_SYNC_VERSION);
    assert(sprdwl_cmd_send_recv_no_wait(&priv, msg) == -EBUSY);
    assert(releases == 3 && free_buffers == 3 && !sends);
    lock_error = 0;
    response_error = -1;
    msg = sprdwl_cmd_getbuf(&priv, 4, 0, 1, WIFI_CMD_SYNC_VERSION);
    assert(sprdwl_cmd_send_recv(&priv, msg, 20, NULL, NULL) == -1);
    assert(sends == 1 && unlocks == 1);
    response_error = 0;
    response_length = 0;
    assert(sprdwl_sync_version(&priv) == -EPROTO && !fill_calls);
    response_length = 4;
    assert(sprdwl_sync_version(&priv) == 0 && fill_calls == 1);
    msg = sprdwl_cmd_getbuf(&priv, 4, 0, 1, WIFI_CMD_SYNC_VERSION);
    len = sizeof(buf);
    assert(sprdwl_cmd_send_recv(&priv, msg, 20, buf, &len) == 0);
    assert(len == 4 && !memcmp(buf, "abcd", 4));
    puts("PASS: command lock errors, short API reply, TX buffer ownership on timeout");
    return 0;
}
'''


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622'
    cmd = (source / 'unisocwifi/cmdevt.c').read_text()
    body = '\n'.join(function(cmd, name) for name in (
        'sprdwl_cmd_send_recv', 'sprdwl_cmd_send_recv_no_wait', 'sprdwl_sync_version'))
    with tempfile.TemporaryDirectory(prefix='cm311-command-errors-') as directory:
        src, binary = Path(directory) / 'test.c', Path(directory) / 'test'
        src.write_text(PRELUDE + '\n' + body + '\n' + MAIN)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(src), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
