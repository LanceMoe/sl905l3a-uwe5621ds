// SPDX-License-Identifier: GPL-2.0-only
/* Diagnostic only: compare immutable CIS bytes, never the Wi-Fi RX FIFO. */
#include <linux/module.h>
#include <linux/dma-mapping.h>
#include <linux/platform_device.h>
#include <linux/mmc/host.h>
#include <linux/mmc/card.h>
#include <linux/mmc/sdio_func.h>
#include <linux/io.h>
#include <linux/interrupt.h>
#include <linux/delay.h>
#include <linux/iopoll.h>

#define PROBE_LEN 256
#define STATUS 0x48
#define START 0x40
#define CFG 0x44
#define IRQ_EN 0x4c
#define SRAM 0x400

/* Leading layout of this repository's matching experimental host only. */
struct probe_host {
	struct device *dev;
	const void *soc_data;
	struct mmc_host *mmc;
	struct mmc_command *cmd;
	void __iomem *regs;
};

static int cis_transfer(void __iomem *r, u32 address, u32 target)
{
	u32 status, response;
	int error;

	writel(0x3fff, r + STATUS);
	/* CMD53, byte mode, incrementing function 0 address, R5 response. */
	writel((53U << 24) | BIT(31) | BIT(22) | BIT(18) |
	       BIT(11) | (7U << 12) | PROBE_LEN, r + 0x50);
	writel(target, r + 0x58);
	writel(0, r + 0x5c);
	wmb();
	writel(BIT(26) | (address << 9) | PROBE_LEN, r + 0x54);
	error = readl_poll_timeout(r + STATUS, status,
				  status & 0x3fff, 10, 200000);
	response = readl(r + 0x5c);
	writel(0, r + START);
	pr_info("cm311_dram_probe: target=%08x status=%08x response=%08x\n",
		target, status, response);
	if (error || !(status & BIT(13)) || (status & 0x1fff) ||
	    (response & 0xcb00))
		return -EIO;
	return 0;
}

static int __init cm311_probe_init(void)
{
	struct device *dev;
	struct probe_host *host;
	struct platform_device *pdev;
	struct resource *res;
	struct sdio_func *func;
	void *buffer = NULL;
	dma_addr_t dma;
	u8 reference[PROBE_LEN], sram_bytes[PROBE_LEN];
	u32 saved[PROBE_LEN / 4], cfg, irq_en, cis = 0, status;
	unsigned int i, diverse = 0;
	int error = 0, irq;
	void __iomem *r;

	dev = bus_find_device_by_name(&platform_bus_type, NULL, "ffe03000.mmc");
	if (!dev)
		return -ENODEV;
	if (!dev->driver || strcmp(dev->driver->name, "cm311-sdio-rx")) {
		error = -ENODEV;
		goto put;
	}
	host = dev_get_drvdata(dev);
	pdev = to_platform_device(dev);
	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!host || !res || res->start != 0xffe03000 || host->dev != dev ||
	    !host->mmc->card || !host->mmc->card->sdio_func[0]) {
		error = -ENODEV;
		goto put;
	}
	irq = platform_get_irq(pdev, 0);
	if (irq < 0) {
		error = irq;
		goto put;
	}
	buffer = dma_alloc_coherent(dev, PROBE_LEN, &dma, GFP_KERNEL);
	if (!buffer) {
		error = -ENOMEM;
		goto put;
	}
	if (upper_32_bits(dma) || (dma & 3)) {
		error = -ERANGE;
		goto free;
	}
	func = host->mmc->card->sdio_func[0];
	sdio_claim_host(func);
	r = host->regs;
	if (host->cmd || (readl(r + STATUS) & BIT(30))) {
		error = -EBUSY;
		goto release;
	}
	/* Common CIS pointer at CCCR 0x09..0x0b, obtained via normal CMD52. */
	for (i = 0; i < 3; i++) {
		u8 byte = sdio_f0_readb(func, 9 + i, &error);
		if (error)
			goto release;
		cis |= (u32)byte << (8 * i);
	}
	if (!cis || cis > 0x20000 - PROBE_LEN) {
		error = -ERANGE;
		goto release;
	}
	for (i = 0; i < PROBE_LEN; i++) {
		reference[i] = sdio_f0_readb(func, cis + i, &error);
		if (error)
			goto release;
		diverse += reference[i] != reference[0];
	}
	if (diverse < 8) {
		pr_warn("cm311_dram_probe: inconclusive, CIS reference lacks diversity\n");
		error = -ENODATA;
		goto release;
	}
	disable_irq(irq);
	cfg = readl(r + CFG);
	irq_en = readl(r + IRQ_EN);
	writel(0, r + IRQ_EN);
	for (i = 0; i < ARRAY_SIZE(saved); i++)
		saved[i] = readl(r + SRAM + 4 * i);
	error = cis_transfer(r, cis, res->start + SRAM);
	if (!error) {
		for (i = 0; i < ARRAY_SIZE(saved); i++)
			((u32 *)sram_bytes)[i] = readl(r + SRAM + 4 * i);
		if (memcmp(reference, sram_bytes, PROBE_LEN))
			error = -EILSEQ;
	}
	if (error) {
		pr_warn("cm311_dram_probe: inconclusive, SRAM control failed (%d); DDR not tested\n", error);
	} else {
		/* Every unchanged destination byte must fail comparison. */
		for (i = 0; i < PROBE_LEN; i++)
			((u8 *)buffer)[i] = reference[i] ^ 0xff;
		dma_wmb();
		error = cis_transfer(r, cis, lower_32_bits(dma));
		dma_rmb();
		if (!error && memcmp(reference, buffer, PROBE_LEN))
			error = -EILSEQ;
		pr_info("cm311_dram_probe: SRAM control passed; DDR exact comparison %s (%d), bytes=%u\n",
			error ? "FAILED" : "PASSED", error, PROBE_LEN);
	}
	writel(0, r + START);
	udelay(10);
	status = readl(r + STATUS);
	for (i = 0; i < ARRAY_SIZE(saved); i++)
		writel(saved[i], r + SRAM + 4 * i);
	writel(0x3fff, r + STATUS);
	writel(cfg, r + CFG);
	writel(irq_en, r + IRQ_EN);
	enable_irq(irq);
	if (status & BIT(30)) {
		/* Never free a target while the engine may still reference it. */
		pr_err("cm311_dram_probe: engine busy; retaining DMA buffer until reboot\n");
		buffer = NULL;
	}
release:
	sdio_release_host(func);
free:
	if (buffer)
		dma_free_coherent(dev, PROBE_LEN, buffer, dma);
put:
	put_device(dev);
	return error;
}

static void __exit cm311_probe_exit(void) {}
module_init(cm311_probe_init);
module_exit(cm311_probe_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("CM311 CIS reference/SRAM/DDR comparison; diagnostic only");
