#!/usr/bin/env python3
"""Exercise the RX block-ack work item, including DELBA after a reorder timeout."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622'
source /= 'unisocwifi'
reorder = (source / 'reorder.c').read_text()
work = (source / 'work.c').read_text()
header = (source / 'cmdevt.h').read_text()
send_delba = reorder[reorder.index('static void send_delba('):
                     reorder.index('static int wlan_addba_event(')]
worker_case = work[work.index('case SPRDWL_WORK_BA_MGMT:'):
                   work.index('case SPRDWL_WORK_ADDBA:')]
types = header[header.index('struct sprdwl_cmd_ba {'):
               header.index('struct win_param {')]

test = r'''
#include <assert.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
#define __packed __attribute__((packed))
#define container_of(ptr, type, field) ((type *)((char *)(ptr) - offsetof(type, field)))
#define ether_addr_copy(dest, src) memcpy(dest, src, 6)
#define wl_err(...) ((void)0)
enum { SPRDWL_WORK_BA_MGMT = 6 };
struct sprdwl_peer_entry { struct { u8 da[6]; } tx; u8 ctx_id; };
struct sprdwl_intf { struct sprdwl_peer_entry peer; };
struct sprdwl_rx_ba_entry { int marker; };
struct sprdwl_rx_if { struct sprdwl_intf *intf; struct sprdwl_rx_ba_entry ba_entry; };
struct sprdwl_vif { u8 ctx_id; };
struct sprdwl_work { size_t len; u8 data[]; };
''' + types + r'''
static struct sprdwl_work *queued;
static struct sprdwl_rx_ba_entry *expected_entry;
static int commands, activations, send_error;
static u8 last_type;

static struct sprdwl_peer_entry *
sprdwl_find_peer_entry_using_lut_index(struct sprdwl_intf *intf, u8 idx)
{
    return idx == 6 ? &intf->peer : NULL;
}

static void sprdwl_rx_send_cmd(struct sprdwl_intf *intf, void *data, int len,
                              u8 id, u8 ctx_id)
{
    assert(id == SPRDWL_WORK_BA_MGMT && ctx_id == intf->peer.ctx_id);
    queued = malloc(sizeof(*queued) + len);
    assert(queued);
    queued->len = len;
    memcpy(queued->data, data, len);
}

static int sprdwl_send_ba_mgmt(void *priv, u8 ctx_id, struct sprdwl_cmd_ba *msg,
                              size_t len)
{
    assert(!priv && ctx_id == 1 && len == sizeof(*msg) && msg->tid == 3);
    assert(msg->da[0] == 2);
    last_type = msg->type;
    commands++;
    return send_error;
}

static void sprdwl_active_ba_node(struct sprdwl_rx_ba_entry *entry, u8 idx, u8 tid)
{
    assert(entry == expected_entry && idx == 6 && tid == 3);
    activations++;
}

static void process_work(struct sprdwl_work *sprdwl_work)
{
    struct sprdwl_ba_event_data *ba_data;
    struct sprdwl_vif vif_data = { .ctx_id = 1 };
    struct sprdwl_vif *vif = &vif_data;
    void *priv = NULL;
    switch (SPRDWL_WORK_BA_MGMT) {
    ''' + worker_case + r'''
    }
}
''' + send_delba + r'''
int main(void)
{
    struct sprdwl_intf intf = { .peer = { .tx.da = { 2 }, .ctx_id = 1 } };
    struct sprdwl_rx_if rx = { .intf = &intf };
    struct sprdwl_ba_event_data *payload;
    expected_entry = &rx.ba_entry;

    send_delba(expected_entry, 3, 6);
    assert(queued && queued->len == sizeof(*payload));
    process_work(queued);
    assert(commands == 1 && last_type == SPRDWL_DELBA_CMD && activations == 0);

    payload = (struct sprdwl_ba_event_data *)queued->data;
    payload->addba_rsp.type = SPRDWL_ADDBA_RSP_CMD;
    payload->ba_entry = expected_entry;
    payload->sta_lut_index = 6;
    queued->len--;
    process_work(queued);
    assert(commands == 1 && activations == 0);
    queued->len++;
    payload->addba_rsp.success = 0;
    process_work(queued);
    assert(commands == 2 && activations == 0);

    payload->addba_rsp.success = 1;
    send_error = -1;
    process_work(queued);
    assert(commands == 3 && activations == 0);
    send_error = 0;
    process_work(queued);
    assert(commands == 4 && activations == 1);
    free(queued);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='cm311-rx-ba-') as folder:
    code, binary = Path(folder) / 'test.c', Path(folder) / 'test'
    code.write_text(test)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                    '-Werror', str(code), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
