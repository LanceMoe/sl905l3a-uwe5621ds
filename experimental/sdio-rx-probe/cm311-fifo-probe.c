// SPDX-License-Identifier: GPL-2.0-only
/* Diagnostic for an already stalled FIFO. Native MMC requests only. */
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/mmc/host.h>
#include <linux/mmc/core.h>
#include <linux/mmc/card.h>
#include <linux/mmc/sdio.h>
#include <linux/mmc/sdio_func.h>
#include <linux/scatterlist.h>
#include <linux/slab.h>
#include <linux/crc32.h>
#include <linux/unaligned.h>

struct probe_host {
	struct device *dev;
	const void *soc_data;
	struct mmc_host *mmc;
	struct mmc_command *cmd;
	void __iomem *regs;
};

static bool stalled;
module_param(stalled, bool, 0400);
MODULE_PARM_DESC(stalled, "Explicitly confirm FIFO is already stalled; may consume packets");
static bool abort_once;
module_param(abort_once, bool, 0400);
MODULE_PARM_DESC(abort_once, "Issue vendor-standard function-1 CCCR Abort once before reads");

static int __init fifo_probe_init(void)
{
	static const unsigned int lengths[] = {1536, 512, 1536, 512};
	struct device *dev;
	struct probe_host *host;
	struct resource *res;
	struct sdio_func *func;
	u8 *buffer;
	unsigned int i;
	int error = 0;

	if (!stalled)
		return -EPERM;
	dev = bus_find_device_by_name(&platform_bus_type, NULL, "ffe03000.mmc");
	if (!dev)
		return -ENODEV;
	if (!dev->driver || strcmp(dev->driver->name, "meson-gx-mmc")) {
		error = -ENODEV;
		goto put;
	}
	host = dev_get_drvdata(dev);
	res = platform_get_resource(to_platform_device(dev), IORESOURCE_MEM, 0);
	if (!host || host->dev != dev || !res || res->start != 0xffe03000 ||
	    !host->mmc->card || !host->mmc->card->sdio_func[0]) {
		error = -ENODEV;
		goto put;
	}
	func = host->mmc->card->sdio_func[0];
	if (func->num != 1 || func->cur_blksize != 512 ||
	    host->mmc->max_req_size != 1536 || host->mmc->max_seg_size < 1536 ||
	    host->mmc->max_blk_count < 3) {
		error = -EINVAL;
		goto put;
	}
	buffer = kmalloc(1536, GFP_KERNEL);
	if (!buffer) {
		error = -ENOMEM;
		goto put;
	}
	sdio_claim_host(func);
	if (abort_once) {
		struct mmc_command abort_command = {};

		/* Vendor sdiohal_abort(): CMD52 function 0, CCCR 0x06, value 1.
		 * Abort function 1 only; bit 3 (whole-card reset) is never set.
		 */
		abort_command.opcode = SD_IO_RW_DIRECT;
		abort_command.arg = BIT(31) | (SDIO_CCCR_ABORT << 9) | 1;
		abort_command.flags = MMC_RSP_SPI_R5 | MMC_RSP_R5 | MMC_CMD_AC;
		error = mmc_wait_for_cmd(host->mmc, &abort_command, 0);
		if (!error && (abort_command.resp[0] &
			(R5_ERROR | R5_FUNCTION_NUMBER | R5_OUT_OF_RANGE)))
			error = -EIO;
		pr_info("cm311_fifo_probe: one CCCR function-1 abort error=%d response=%08x\n",
			error, abort_command.resp[0]);
		if (error)
			goto release;
	}
	for (i = 0; i < ARRAY_SIZE(lengths); i++) {
		struct mmc_request request = {};
		struct mmc_command command = {};
		struct mmc_data data = {};
		struct scatterlist sg;
		unsigned int length = lengths[i], j, non_ff = 0;
		u32 valid, pending, header;

		memset(buffer, 0xa5, length);
		sg_init_one(&sg, buffer, length);
		command.opcode = SD_IO_RW_EXTENDED;
		command.arg = BIT(28) | BIT(27) | (0x20 << 9) | (length / 512);
		command.flags = MMC_RSP_SPI_R5 | MMC_RSP_R5 | MMC_CMD_ADTC;
		data.blksz = 512;
		data.blocks = length / 512;
		data.flags = MMC_DATA_READ;
		data.sg = &sg;
		data.sg_len = 1;
		mmc_set_data_timeout(&data, func->card);
		request.cmd = &command;
		request.data = &data;
		mmc_wait_for_req(host->mmc, &request);
		error = command.error ? command.error : data.error;
		if (!error && (command.resp[0] & R5_ERROR))
			error = -EIO;
		if (error || data.bytes_xfered != length) {
			pr_warn("cm311_fifo_probe: read=%u error=%d bytes=%u; stopping\n",
				length, error, data.bytes_xfered);
			error = error ? error : -EIO;
			break;
		}
		valid = get_unaligned_le32(buffer + length - 8);
		pending = get_unaligned_le32(buffer + length - 4);
		header = get_unaligned_le32(buffer);
		for (j = 0; j < length - 8; j++)
			non_ff += buffer[j] != 0xff;
		pr_info("cm311_fifo_probe: seq=%u read=%u valid=%u pending=%u first=%08x body_crc=%08x non_ff=%u\n",
			i, length, valid, pending, header,
			crc32_le(~0U, buffer, length - 8), non_ff);
		/* Do not intentionally take additional valid traffic from the BSP. */
		if (valid || !pending) {
			pr_info("cm311_fifo_probe: valid traffic or no backlog; stopping\n");
			break;
		}
	}
release:
	sdio_release_host(func);
	kfree(buffer);
put:
	put_device(dev);
	return error;
}

static void __exit fifo_probe_exit(void) {}
module_init(fifo_probe_init);
module_exit(fifo_probe_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Bounded native CMD53 diagnostic for stalled CM311 packet FIFO");
