// SPDX-License-Identifier: GPL-2.0-only
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/mmc/host.h>
#include <linux/mmc/core.h>
#include <linux/mmc/card.h>
#include <linux/mmc/sdio_func.h>
#include <linux/io.h>
#include <linux/interrupt.h>
#include <linux/delay.h>
#include <linux/ktime.h>
/* Read only the verified leading layout of the matching candidate host. */
struct probe_host {
 struct device *dev;
 const void *soc_data;
 struct mmc_host *mmc;
 struct mmc_command *cmd;
 void __iomem *regs;
};
static int __init cm311_probe_init(void)
{
 struct device *dev;
 struct probe_host *host;
 struct resource *res;
 struct platform_device *pdev;
 u32 *saved, cfg, irq_en, status, rsp, cmd;
 unsigned int mode,i,bank,changed[3];
 int irq,error=0;
 u64 end;
 void __iomem *r;
 dev=bus_find_device_by_name(&platform_bus_type,NULL,"ffe03000.mmc");
 if(!dev) return -ENODEV;
 if(!dev->driver || strcmp(dev->driver->name,"cm311-sdio-rx")) { error=-ENODEV;goto put; }
 host=dev_get_drvdata(dev); pdev=to_platform_device(dev);
 res=platform_get_resource(pdev,IORESOURCE_MEM,0);
 if(!host || !res || res->start!=0xffe03000 || host->dev!=dev) { error=-EINVAL;goto put; }
 irq=platform_get_irq(pdev,0);if(irq<0){error=irq;goto put;}
 if(!host->mmc->card || !host->mmc->card->sdio_func[0]) {error=-ENODEV;goto put;}
 saved=kmalloc(1536,GFP_KERNEL);if(!saved){error=-ENOMEM;goto put;}
 sdio_claim_host(host->mmc->card->sdio_func[0]);
 if(host->cmd) {error=-EBUSY;goto release;}
 r=host->regs;
 disable_irq(irq);
 irq_en=readl(r+0x4c);cfg=readl(r+0x44);
 writel(0,r+0x4c);writel(0,r+0x40);
 for(i=0;i<384;i++)saved[i]=readl(r+0x200+i*4);
 /* One 512-byte read. It cannot consume the currently oversized CP head. */
 cmd=(1U<<28)|(1U<<27)|(0x20U<<9)|1;
 for(mode=0;mode<8;mode++) {
  unsigned int target=(mode==1 || mode==3)?0x600:0x400;
  u32 request = mode>=5 ? (cmd & ~0x1ffU) | 2 : cmd;
  for(i=0;i<384;i++)writel(0xa5110000U+i,r+0x200+i*4);
  writel(0x3fff,r+0x48);
  if(mode<2) {
   writel(0xb5447a01,r+0x50); /* CMD53, OWNER, DATA_IO, R5, 1 block, EOC */
   writel(res->start+target,r+0x58);
   writel(0,r+0x5c);
   wmb();writel(request,r+0x54);
  } else {
   /* Same request, descriptor in its documented SRAM region. */
   writel(0xb5447a01,r+0x200);
   writel(request,r+0x204);
   writel((res->start+target)|(mode==4?1:0),r+0x208);
   writel(0,r+0x20c);
   if(mode>=5) {
    writel(0xb5447201,r+0x200);
    writel(0xb5467a01,r+0x210);
    writel(request,r+0x214);
    writel(res->start+0x600,r+0x218);
    writel(0,r+0x21c);
   }
   if(mode==6) {
    writel(0xb5407202,r+0x200);
    writel(res->start+0x400,r+0x208);
    writel(0xb5467201,r+0x210);writel(request,r+0x214);
    writel(res->start+0x400,r+0x218);writel(0,r+0x21c);
    writel(0xb5467a01,r+0x220);writel(request,r+0x224);
    writel(res->start+0x600,r+0x228);writel(0,r+0x22c);
   }
   if(mode==7) {
    writel((res->start+0x400)|1,r+0x208);
    writel((res->start+0x600)|1,r+0x218);
   }
   wmb();writel((res->start+0x200)|3,r+0x40);
  }
  end=ktime_get_ns()+50000000;
  do {
   status=readl(r+0x48);
   if(status&0x3fff) break;
   cpu_relax();
  } while(ktime_get_ns()<end);
  rsp=readl(r+(mode<2?0x5c:0x20c));
  writel(0,r+0x40);
  udelay(10);
  for(bank=0;bank<3;bank++) {
   changed[bank]=0;
   for(i=0;i<128;i++)
    if(readl(r+0x200+bank*512+i*4)!=(0xa5110000U+bank*128+i))changed[bank]++;
  }
  pr_info("cm311_sram_probe mode=%u target=%x status=%08x rsp=%08x changed=%u/%u/%u heads=%08x/%08x/%08x desc=%08x\n",
   mode,target,status,rsp,changed[0],changed[1],changed[2],
   readl(r+0x200),readl(r+0x400),readl(r+0x600),readl(r+0x600));
  if(mode>=5) pr_info("cm311_sram_probe detail mode=%u rx=%08x tx=%08x second=%08x/%08x/%08x/%08x\n",
   mode,readl(r+0x90),readl(r+0x94),readl(r+0x210),readl(r+0x214),readl(r+0x218),readl(r+0x21c));
  if(!(status&BIT(13)) || (status&0x1fff)){error=-EIO;break;}
 }
 writel(0,r+0x40);udelay(10);
 for(i=0;i<384;i++)writel(saved[i],r+0x200+i*4);
 writel(0x3fff,r+0x48);writel(cfg,r+0x44);writel(irq_en,r+0x4c);
 enable_irq(irq);
release:
 sdio_release_host(host->mmc->card->sdio_func[0]);kfree(saved);
put:
 put_device(dev);
 return error;
}
static void __exit cm311_probe_exit(void) {}
module_init(cm311_probe_init);module_exit(cm311_probe_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("One-shot bounded CM311 SRAM address probe; matching candidate host only");
