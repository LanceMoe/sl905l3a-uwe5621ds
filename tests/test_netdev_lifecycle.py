#!/usr/bin/env python3
"""Replay the observed UP-netdev/wiphy teardown deadlock and native ioctl ABI."""
from pathlib import Path
import os
import re
import subprocess
import tempfile


def extract(source, name):
    match = re.search(r'^static (?:int|void) ' + name + r'\([^;]*?\)\n\{', source, re.M)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stddef.h>
#define CONFIG_MESON_UWE5621 1
#define CONFIG_COMPAT 1
#define __user
struct net_device { bool up; };
struct ifreq { void *ifr_data; };
struct wiphy { int unused; };
struct sprdwl_priv;
struct sprdwl_vif;
struct list_head { struct sprdwl_priv *owner; struct sprdwl_vif *vif; };
struct sprdwl_priv { int list_lock; struct list_head vif_list; struct wiphy *wiphy; struct sprdwl_vif *last; };
struct sprdwl_vif { struct net_device *ndev; struct list_head vif_node; struct sprdwl_vif *prev; };
static bool rtnl_held, wiphy_held, compat;
static unsigned closes, removals, calls;
static void *expected_data;
static struct net_device *expected_ndev;
static int expected_cmd;
#define spin_lock_bh(lock) ((void)(lock))
#define spin_unlock_bh(lock) ((void)(lock))
#define list_for_each_entry_reverse(vif, head, member) \
    for ((vif) = (head)->owner->last; (vif); (vif) = (vif)->prev)
static void list_del(struct list_head *node) { node->owner->last = node->vif->prev; }
static void rtnl_lock(void) { assert(!rtnl_held); rtnl_held = true; }
static void rtnl_unlock(void) { assert(rtnl_held && !wiphy_held); rtnl_held = false; }
static void wiphy_lock(struct wiphy *w) { (void)w; assert(rtnl_held && !wiphy_held); wiphy_held = true; }
static void wiphy_unlock(struct wiphy *w) { (void)w; assert(wiphy_held); wiphy_held = false; }
static void dev_close(struct net_device *ndev) {
    assert(rtnl_held && !wiphy_held);
    /* NETDEV_GOING_DOWN acquires the wiphy mutex recursively if caller holds it. */
    struct wiphy w;
    wiphy_lock(&w); wiphy_unlock(&w);
    ndev->up = false; ++closes;
}
static int sprdwl_del_iface(struct sprdwl_priv *priv, struct sprdwl_vif *vif) {
    (void)priv;
    assert(rtnl_held && wiphy_held);
    assert(!vif->ndev || !vif->ndev->up);
    ++removals; return 0;
}
static bool in_compat_syscall(void) { return compat; }
static int sprdwl_ioctl(struct net_device *ndev, struct ifreq *ifr, int cmd) {
    assert(ndev == expected_ndev && ifr->ifr_data == expected_data && cmd == expected_cmd);
    ++calls; return -EIO;
}
'''
MAIN = r'''
int main(void) {
    struct wiphy w = {0};
    struct net_device netdev = {true};
    struct sprdwl_priv priv = {.wiphy=&w};
    struct sprdwl_vif net = {.ndev=&netdev};
    struct sprdwl_vif p2p = {.prev=&net};
    priv.last=&p2p; priv.vif_list.owner=&priv;
    net.vif_node.owner=p2p.vif_node.owner=&priv;
    net.vif_node.vif=&net; p2p.vif_node.vif=&p2p;
    sprdwl_del_all_ifaces(&priv);
    assert(closes == 1 && removals == 2 && !priv.last);
    assert(!rtnl_held && !wiphy_held);
    sprdwl_del_all_ifaces(&priv);
    assert(closes == 1 && removals == 2);
    char command[]="SETSUSPENDMODE 0";
    struct ifreq ifr = {NULL};
    expected_ndev=&netdev; expected_data=command; expected_cmd=0x89f4;
    assert(sprdwl_meson_siocdevprivate(&netdev,&ifr,command,expected_cmd) == -EIO);
    assert(calls == 1);
    compat=true; ifr.ifr_data=NULL;
    assert(sprdwl_meson_siocdevprivate(&netdev,&ifr,command,expected_cmd) == -EOPNOTSUPP);
    assert(calls == 1 && !ifr.ifr_data);
    return 0;
}
'''

if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    source = (root / 'build/uwe5622/unisocwifi/main.c').read_text()
    code = STUBS + extract(source, 'sprdwl_del_all_ifaces') + '\n'
    code += extract(source, 'sprdwl_meson_siocdevprivate') + MAIN
    with tempfile.TemporaryDirectory(prefix='cm311-netdev-') as tmp:
        path = Path(tmp)
        (path / 'test.c').write_text(code)
        binary = path / 'test'
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(path / 'test.c'), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=5)
    print('PASS: UP netdev closes before wiphy lock; P2P and empty-list teardown; native/compat ioctl dispatch')
