#!/usr/bin/env python3
"""Test actual startup sync retries and worker/completion error propagation."""
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
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define CONFIG_WCN_SDIO 1
#define SYNC_ADDR 0x40525fa0
#define SYNC_CALI_WAITING 0xf0f0f0f1U
#define SYNC_CALI_WRITE_DONE 0xf0f0f0f2U
#define POWERUP_WAIT_MS 30000
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define WCN_INFO(...) ((void)0)
#define WCN_ERR(...) ((void)0)
#define WCN_DEBUG(...) ((void)0)
#define msecs_to_jiffies(x) (x)
struct completion { int done; };
struct work_struct { int value; };
struct wcn_sync_info_t { unsigned init_status, tsx_dac_data, sdio_config, prj_type; };
struct marlin_device {
    struct wcn_sync_info_t sync_f;
    int download_result;
    struct completion download_done;
    struct work_struct download_wq;
};
static struct marlin_device device;
static struct marlin_device *marlin_dev = &device;
static int errors[8], error_count, read_calls, direct_calls, sleeps;
static int config_calls, config_error, writes, fw_calls, release_calls;
static int fw_error, release_error, ready_error, runtime_error;
static int runtime_calls, version_calls, schedule_mode;
static int sprdwcn_bus_reg_read(unsigned addr, void *buf, unsigned len) {
    assert(addr == SYNC_ADDR && len == 4);
    int index = read_calls++;
    int error = index < error_count ? errors[index] : 0;
    if (!error) *(unsigned *)buf = SYNC_CALI_WAITING;
    return error;
}
static int sprdwcn_bus_direct_read(unsigned addr, void *buf, unsigned len) {
    assert(len > 4); ++direct_calls;
    return sprdwcn_bus_reg_read(addr,buf,4);
}
static int sprdwcn_bus_reg_write(unsigned addr, void *buf, unsigned len) {
    assert(addr == SYNC_ADDR && len == 4 && *(unsigned *)buf == SYNC_CALI_WRITE_DONE);
    ++writes; return 0;
}
static void usleep_range(int low, int high) { assert(low == 2000 && high == 4000); ++sleeps; }
static void msleep(int ms) { assert(ms == 20); }
static int marlin_send_sdio_config_to_cp(void) { ++config_calls; return config_error; }
static void wcn_get_chip_name(void) { }
static int btwifi_download_firmware(void) { ++fw_calls; return fw_error; }
static int marlin_start_run(void) { ++release_calls; return release_error; }
static int check_cp_clock_mode(void) { return -EIO; } // optional identification
static int check_cp_ready(void) { return ready_error; }
static int sprdwcn_bus_runtime_get(void) { ++runtime_calls; return runtime_error; }
static void get_cp2_version(void) { ++version_calls; }
static void switch_cp2_log(bool on) { assert(!on); }
static void complete(struct completion *c) { ++c->done; }
static void reinit_completion(struct completion *c) { c->done=0; }
static unsigned long wait_for_completion_timeout(struct completion *c, unsigned ms) {
    assert(ms == POWERUP_WAIT_MS); return c->done ? 1 : 0;
}
static void pre_btwifi_download_sdio(struct work_struct *work);
static bool schedule_work(struct work_struct *work) {
    if (schedule_mode == 1) return false;
    if (schedule_mode == 0) pre_btwifi_download_sdio(work);
    return true;
}
'''

MAIN = r'''
static void reset(void) {
    memset(&device,0,sizeof(device)); memset(errors,0,sizeof(errors));
    error_count=read_calls=direct_calls=sleeps=0;
    config_calls=config_error=writes=fw_calls=release_calls=0;
    fw_error=release_error=ready_error=runtime_error=0;
    runtime_calls=version_calls=schedule_mode=0;
}
int main(void) {
    unsigned buffer[4]={0};
    reset(); errors[0]=-EILSEQ; errors[1]=-ETIMEDOUT; error_count=2;
#ifdef CONFIG_MESON_UWE5621
    assert(marlin_read_sync(buffer,4) == 0);
    assert(read_calls == 3 && sleeps == 2 && !writes);
    reset(); errors[0]=-ETIMEDOUT; error_count=1;
    assert(marlin_read_sync(buffer,sizeof(buffer)) == 0);
    assert(direct_calls == 2 && sleeps == 1 && !writes);
#else
    assert(marlin_read_sync(buffer,4) == -EILSEQ && read_calls == 1 && !sleeps);
#endif
    reset(); errors[0]=-EIO; error_count=1;
    assert(marlin_read_sync(buffer,4) == -EIO && read_calls == 1 && !sleeps);
    reset(); errors[0]=errors[1]=errors[2]=-EILSEQ; error_count=3;
    assert(marlin_read_sync(buffer,4) == -EILSEQ);
#ifdef CONFIG_MESON_UWE5621
    assert(read_calls == 3 && sleeps == 2);
#else
    assert(read_calls == 1 && !sleeps);
#endif
    assert(!writes && !config_calls);

    reset(); assert(marlin_download_wait() == 0);
    assert(device.download_done.done == 1 && device.download_result == 0);
    assert(fw_calls == 1 && release_calls == 1 && config_calls == 1 && writes == 1);
    assert(runtime_calls == 1 && version_calls == 1);
#ifdef CONFIG_MESON_UWE5621
    reset(); errors[0]=-EILSEQ; error_count=1;
    assert(marlin_download_wait() == 0 && read_calls == 2 && sleeps == 1);
    assert(fw_calls == 1 && config_calls == 1 && writes == 1 && version_calls == 1);
#endif
    reset(); fw_error=-ENOENT;
    assert(marlin_download_wait() == -ENOENT && device.download_done.done == 1);
    assert(release_calls == 0 && !config_calls && !runtime_calls);
    reset(); release_error=-EIO;
    assert(marlin_download_wait() == -EIO && !config_calls && !runtime_calls);
    reset(); errors[0]=errors[1]=errors[2]=-EILSEQ; error_count=3;
    assert(marlin_download_wait() == -EILSEQ);
    assert(device.download_done.done == 1 && !config_calls && !writes && !runtime_calls);
    reset(); config_error=-ETIMEDOUT;
    assert(marlin_download_wait() == -ETIMEDOUT && config_calls == 1 && !writes && !runtime_calls);
    reset(); ready_error=-EIO;
    assert(marlin_download_wait() == -EIO && !runtime_calls && !version_calls);
    reset(); runtime_error=-ENODEV;
    assert(marlin_download_wait() == -ENODEV && runtime_calls == 1 && !version_calls);
    reset(); schedule_mode=1;
    assert(marlin_download_wait() == -EBUSY && !fw_calls);
    reset(); schedule_mode=2;
    assert(marlin_download_wait() == -ETIMEDOUT && !fw_calls);
    puts("PASS: bounded sync-only retries; no replayed writes; worker errors complete and propagate");
    return 0;
}
'''


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'build/uwe5622'
    boot = (source / 'unisocwcn/platform/wcn_boot.c').read_text()
    body = '\n'.join(function(boot, name) for name in
                     ('marlin_read_sync', 'marlin_write_cali_data', 'pre_btwifi_download_sdio', 'marlin_download_wait'))
    with tempfile.TemporaryDirectory(prefix='cm311-wcn-startup-') as folder:
        code, binary = Path(folder) / 'test.c', Path(folder) / 'test'
        code.write_text(PRELUDE + '\n' + body + '\n' + MAIN)
        for flags in (['-DCONFIG_MESON_UWE5621=1'], []):
            subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                            '-Wno-unused-parameter', '-Wno-unused-variable',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            *flags, str(code), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=5)
