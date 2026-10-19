#!/usr/bin/env python3
"""Execute the SRAM RX chain with a clocked fake controller; hardware test still required."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
from test_rx_progress import function
ROOT=Path(__file__).resolve().parent.parent
source=(ROOT/'experimental/sdio-rx/cm311-sdio-rx.c').read_text()
constants='\n'.join(x for x in source.splitlines() if re.match(r'#define\s+(?:SD_EMMC_(?:START|CFG|STATUS|IRQ_EN)|START_DESC_\w+|CMD_CFG_\w+|CMD_DATA_SRAM|CFG_(?:CLK_ALWAYS_ON|AUTO_CLK)|IRQ_(?:\w+))\b',x))
# Multiline IRQ definitions are spelled out below.
constants='\n'.join(x for x in constants.splitlines() if not x.rstrip().endswith('\\') and not re.match(r'#define\s+IRQ_(?:CRC_ERR|EN_MASK|TIMEOUTS)\b',x))
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32; typedef uint64_t u64;
#define __iomem
#define BIT(x) (1U<<(x))
#define GENMASK(h,l) ((~0U>>(31-(h)))&(~0U<<(l)))
#define FIELD_PREP(m,x) (((x)<<__builtin_ctz(m))&(m))
#define IRQ_CRC_ERR 0x7ff
#define IRQ_TIMEOUTS (BIT(11)|BIT(12))
#define IRQ_EN_MASK 0x7fff
#define dev_err_ratelimited(...) ((void)0)
#define print_hex_dump(...) ((void)0)
#define cm311_rx_record(...) ((void)0)
#define wmb() ((void)0)
struct scatterlist { unsigned char *buf; unsigned size; };
struct mmc_data { unsigned blocks,bytes_xfered,sg_len; struct scatterlist *sg; int error; };
struct mmc_command { unsigned opcode,arg,resp[4];int error; };
struct mmc_request { struct mmc_command *cmd;struct mmc_data *data; };
struct mmc_host { void *priv; };
struct meson_host { void *regs;unsigned bounce_dma_addr;int irq;void *dev; };
static u32 regs[512];static unsigned char wire[4096];
static unsigned ticks,interval,produced,blocks,irq,atomic,finished,mode;
static bool busy; static u64 now;
static struct mmc_host mmc;static struct meson_host host;
static void *mmc_priv(struct mmc_host *m) { return m->priv; }
static void disable_irq(int n) { (void)n;assert(!irq);irq=1; }
static void enable_irq(int n) { (void)n;assert(irq);irq=0; }
#define local_irq_save(f) do { (f)=0;assert(!atomic);atomic=1; } while(0)
#define local_irq_restore(f) do { (void)(f);assert(atomic);atomic=0; } while(0)
#define preempt_disable() ((void)0)
#define preempt_enable() ((void)0)
static void cpu_relax(void) {}
static u64 ktime_get_ns(void) { now+=100000;return now; }
static void meson_mmc_set_blksz(struct mmc_host *m,unsigned b) { (void)m;assert(b==512); }
static void meson_mmc_set_response_bits(struct mmc_command *c,u32 *cfg);
static void mmc_request_done(struct mmc_host *m,struct mmc_request *r) { (void)m;(void)r;assert(!atomic&&!irq);finished++; }
static int meson_mmc_wait_desc_stop(struct meson_host *h) { (void)h;busy=false;return 0; }
static unsigned sg_pcopy_from_buffer(struct scatterlist *s,unsigned n,void *b,unsigned len,unsigned skip) {
 (void)n;if(skip+len>s->size)return 0;memcpy(s->buf+skip,b,len);return len;
}
static u32 readl(void *p);
static void writel(u32 v,void *p);
'''
stubs=r'''
static void meson_mmc_set_response_bits(struct mmc_command *c,u32 *cfg) {(void)c;*cfg |= CMD_CFG_RESP_NUM;}
static void writel(u32 v,void *p) {
 unsigned off=(char *)p-(char *)regs;assert(off<sizeof(regs)&&off%4==0);
 if(off==SD_EMMC_STATUS) { regs[off/4]&=~v;return; }
 regs[off/4]=v;
 if(off==SD_EMMC_START) {
  busy=!!(v & START_DESC_BUSY);if(!busy)return;
  assert(atomic && irq && v==(0xffe03200U|START_DESC_INIT|START_DESC_BUSY));
  assert(!(regs[0x200/4]&CMD_CFG_DATA_IO));
  assert(!(regs[0x200/4]&CMD_CFG_NO_CMD));
  regs[0x200/4]&=~CMD_CFG_OWNER;regs[0x20c/4]=0x1000;
  blocks=0;
  while(blocks<8) {
   u32 c=regs[(0x210+16*blocks)/4];assert(c & CMD_CFG_OWNER);
   assert((c&CMD_CFG_LENGTH_MASK)==1);
   assert(regs[(0x218+16*blocks)/4]==(0xffe03400U+(blocks&1)*512));
   assert(c&CMD_CFG_NO_CMD);
   blocks++;if(c&CMD_CFG_END_OF_CHAIN)break;
  }
 }
}
static u32 readl(void *p) {
 unsigned off=(char *)p-(char *)regs;assert(off<sizeof(regs)&&off%4==0);
 if(busy && ++ticks%interval==0 && produced<blocks && mode!=2) {
  if(mode==1){regs[SD_EMMC_STATUS/4]|=BIT(0);return regs[off/4];}
  memcpy((char *)regs+0x400+(produced&1)*512,wire+produced*512,512);
  regs[(0x210+produced*16)/4]&=~CMD_CFG_OWNER;
  produced++;
  if(produced==blocks) { regs[SD_EMMC_STATUS/4]|=IRQ_END_OF_CHAIN;regs[0x20c/4]=0x1000;busy=false; }
 }
 return regs[off/4];
}
'''
main=r'''
static void check(unsigned n,unsigned testmode,unsigned pace,int error) {
 unsigned char result[4096]={0};
 memset(regs,0,sizeof(regs));ticks=produced=irq=atomic=finished=0;now=0;mode=testmode;interval=pace;busy=false;
 for(unsigned i=0;i<sizeof(wire);i++)wire[i]=(i*13+17)&255;
 regs[SD_EMMC_CFG/4]=CFG_CLK_ALWAYS_ON;regs[SD_EMMC_IRQ_EN/4]=0x1234;
 struct scatterlist sg={result,n};struct mmc_data d={n/512,0,1,&sg,0};struct mmc_command c={.opcode=53,.arg=(1U<<28)|(0x20<<9)|(n/512)};
 struct mmc_request r={&c,&d};host=(struct meson_host){regs,0xffe03200U,9,0};mmc.priv=&host;
 cm311_rx_ring(&mmc,&r);
 assert(finished==1&&c.error==error&&d.error==error&&!irq&&!atomic);
 assert(regs[SD_EMMC_CFG/4]==CFG_CLK_ALWAYS_ON&&regs[SD_EMMC_IRQ_EN/4]==0x1234);
 if(!error)assert(d.bytes_xfered==n&&!memcmp(result,wire,n)&&c.resp[0]==0x1000);
}
int main(void) {
 for(unsigned n=2048;n<=4096;n+=512)check(n,0,300,0);
 check(2048,1,300,-EILSEQ);check(2048,2,300,-ETIMEDOUT);check(4096,0,8,-EOVERFLOW);
 puts("PASS: actual SRAM RX chain; one CMD53; alternate banks; CRC/timeout/overwrite errors; IRQ restoration");
}
'''
if __name__=='__main__':
 with tempfile.TemporaryDirectory(prefix='cm311-rx-chain-') as folder:
  c,b=Path(folder)/'test.c',Path(folder)/'test'
  c.write_text(pre+constants+'\n'+stubs+function(source,'cm311_rx_ring')+main)
  subprocess.run([os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(c),'-o',str(b)],check=True)
  subprocess.run([str(b)],check=True)
