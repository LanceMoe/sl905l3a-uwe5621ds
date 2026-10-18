#!/usr/bin/env python3
"""Exercise actual firmware sleep control, wire replies, and cfg80211 errors."""
import os
from pathlib import Path
import subprocess
import tempfile
from test_sdio_failure_paths import function

STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#define CONFIG_MESON_UWE5621 1
#define __packed __attribute__((packed))
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t __le32;
#define cpu_to_le16(x) (x)
#define cpu_to_le32(x) (x)
#define le16_to_cpu(x) (x)
#define le32_to_cpu(x) (x)
#define SPRDWL_HT2CP_CMD 1
#define SPRDWL_CP2HT_REPLY 2
#define SPRDWL_NPI_CMD_SET_WLAN_CAP 40
#define SPRDWL_STA_GC_EN_SLEEP 3
#define SPRDWL_STA_GC_NO_SLEEP 0
#define SPRDWL_SET_PS_STATE 4
#define L_DBG 0
#define wl_ndev_log(...) ((void)0)
struct sprdwl_npi_cmd_hdr { u8 type,subtype; u16 len; } __packed;
struct sprdwl_npi_cmd_resp_hdr { u8 type,subtype; u16 len; int status; } __packed;
struct sprdwl_priv { int unused; };
struct sprdwl_vif { struct sprdwl_priv *priv; u8 ctx_id; };
struct net_device { struct sprdwl_vif *vif; };
struct wiphy { int unused; };
#define netdev_priv(ndev) ((ndev)->vif)
static struct sprdwl_priv priv;
static u8 reply_bytes[8], expected_capability;
static u16 reply_length;
static int transport_result, legacy_result, npi_calls, legacy_calls;
static int sprdwl_npi_send_recv(struct sprdwl_priv *p,u8 ctx,u8 *send,u16 len,u8 *reply,u16 *rlen) {
    const u8 expected[8]={1,40,4,0,expected_capability,0,0,0};
    assert(p==&priv && ctx==7 && len==8 && *rlen==8);
    assert(!memcmp(send,expected,8));
    ++npi_calls;
    memcpy(reply,reply_bytes,8); *rlen=reply_length;
    return transport_result;
}
static int sprdwl_power_save(struct sprdwl_priv *p,u8 ctx,u8 subtype,u8 enabled) {
    assert(p==&priv && ctx==7 && subtype==4 && enabled==(expected_capability!=0));
    ++legacy_calls; return legacy_result;
}
static void reset(void) {
    const u8 valid[8]={2,40,4,0,0,0,0,0};
    memcpy(reply_bytes,valid,8); reply_length=8;
    transport_result=legacy_result=npi_calls=legacy_calls=0;
}
'''
MAIN = r'''
int main(void) {
    struct sprdwl_vif vif={&priv,7}; struct net_device dev={&vif};
    for (int enable=0;enable<2;enable++) {
        reset(); expected_capability=enable?3:0;
        assert(sprdwl_set_sleep_capability(&vif,enable)==0 && npi_calls==1);
        assert(sprdwl_cfg80211_set_power_mgmt(NULL,&dev,enable,0)==0);
        assert(npi_calls==2 && legacy_calls==1);
    }
    reset(); expected_capability=0; legacy_result=-ETIMEDOUT;
    assert(sprdwl_cfg80211_set_power_mgmt(NULL,&dev,false,0)==-ETIMEDOUT);
    assert(!npi_calls);
    reset(); transport_result=-ETIMEDOUT;
    assert(sprdwl_cfg80211_set_power_mgmt(NULL,&dev,false,0)==-ETIMEDOUT);
    for (int badlen=0;badlen<10;badlen++) {
        if (badlen==8) continue;
        reset(); reply_length=badlen;
        assert(sprdwl_set_sleep_capability(&vif,false)==-EPROTO);
    }
    for (int field=0;field<3;field++) {
        reset(); reply_bytes[field]=0;
        assert(sprdwl_set_sleep_capability(&vif,false)==-EPROTO);
    }
    reset(); reply_bytes[4]=1;
    assert(sprdwl_set_sleep_capability(&vif,false)==-EIO);
    reset(); memset(reply_bytes+4,255,4);
    assert(sprdwl_set_sleep_capability(&vif,false)==-EIO);
    reset(); reply_bytes[1]=39;
    assert(sprdwl_cfg80211_set_power_mgmt(NULL,&dev,false,0)==-EPROTO);
    return 0;
}
'''
if __name__ == '__main__':
    root=Path(__file__).resolve().parent.parent
    source=root/'build/uwe5622/unisocwifi'
    code=STUBS+function((source/'npi.c').read_text(),'sprdwl_set_sleep_capability')+'\n'
    code+=function((source/'cfg80211.c').read_text(),'sprdwl_cfg80211_set_power_mgmt')+MAIN
    with tempfile.TemporaryDirectory(prefix='cm311-sleep-cap-') as tmp:
        p=Path(tmp); (p/'test.c').write_text(code)
        subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',
                        '-Wno-unused-parameter','-fsanitize=address,undefined','-fno-omit-frame-pointer',
                        str(p/'test.c'),'-o',str(p/'test')],check=True)
        subprocess.run([str(p/'test')],check=True,timeout=5)
    print('PASS: sleep capability wire format, on/off mapping, malformed replies, and cfg80211 error propagation')
