// SPDX-License-Identifier: GPL-2.0-only
#include <linux/bitfield.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/pinctrl/machine.h>
#include <linux/pinctrl/pinctrl.h>
#include <linux/pinctrl/pinmux.h>
#include <linux/platform_device.h>
#include <linux/slab.h>

/* G12A GPIOV_0, a virtual pin that selects SD_EMMC_B on the SDIO pads. */
#define CM311_ROUTE_REGISTER 0xff6346c8
#define CM311_ROUTE_MASK GENMASK(27, 24)
#define CM311_ROUTE_SD_EMMC_B 1

static const char * const route_groups[] = { "sdio-pads" };
static const struct pinctrl_pin_desc route_pins[] = {
	PINCTRL_PIN(0, "GPIOV_0"),
};

static int route_count(struct pinctrl_dev *pctldev)
{
	return 1;
}

static const char *route_group_name(struct pinctrl_dev *pctldev,
				    unsigned int selector)
{
	return route_groups[selector];
}

static int route_group_pins(struct pinctrl_dev *pctldev, unsigned int selector,
			    const unsigned int **pins, unsigned int *npins)
{
	static const unsigned int numbers[] = { 0 };

	*pins = numbers;
	*npins = ARRAY_SIZE(numbers);
	return 0;
}

static const char *route_function_name(struct pinctrl_dev *pctldev,
				       unsigned int selector)
{
	return "sd-emmc-b";
}

static int route_function_groups(struct pinctrl_dev *pctldev,
				 unsigned int selector,
				 const char * const **groups,
				 unsigned int *ngroups)
{
	*groups = route_groups;
	*ngroups = ARRAY_SIZE(route_groups);
	return 0;
}

static int route_node_to_map(struct pinctrl_dev *pctldev, struct device_node *np,
			     struct pinctrl_map **map, unsigned int *num_maps)
{
	struct pinctrl_map *entry;

	if (!of_node_name_eq(np, "sdio-pads"))
		return -EINVAL;
	entry = kzalloc(sizeof(*entry), GFP_KERNEL);
	if (!entry)
		return -ENOMEM;
	entry->type = PIN_MAP_TYPE_MUX_GROUP;
	entry->data.mux.group = route_group_name(pctldev, 0);
	entry->data.mux.function = route_function_name(pctldev, 0);
	*map = entry;
	*num_maps = 1;
	return 0;
}

static void route_free_map(struct pinctrl_dev *pctldev, struct pinctrl_map *map,
			    unsigned int num_maps)
{
	kfree(map);
}

static int route_set_mux(struct pinctrl_dev *pctldev, unsigned int function,
			 unsigned int group)
{
	void __iomem *reg = pinctrl_dev_get_drvdata(pctldev);
	u32 val = readl(reg);

	writel((val & ~CM311_ROUTE_MASK) |
	       FIELD_PREP(CM311_ROUTE_MASK, CM311_ROUTE_SD_EMMC_B), reg);
	return 0;
}

static const struct pinctrl_ops route_pinctrl_ops = {
	.get_groups_count = route_count,
	.get_group_name = route_group_name,
	.get_group_pins = route_group_pins,
	.dt_node_to_map = route_node_to_map,
	.dt_free_map = route_free_map,
};

static const struct pinmux_ops route_pinmux_ops = {
	.get_functions_count = route_count,
	.get_function_name = route_function_name,
	.get_function_groups = route_function_groups,
	.set_mux = route_set_mux,
};

static struct pinctrl_desc route_desc = {
	.name = "cm311-sdio-route",
	.pins = route_pins,
	.npins = ARRAY_SIZE(route_pins),
	.pctlops = &route_pinctrl_ops,
	.pmxops = &route_pinmux_ops,
	.owner = THIS_MODULE,
};

static int route_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct pinctrl_dev *pinctrl;
	void __iomem *reg;
	int ret;

	if (!of_machine_is_compatible("cm311,m401a,e900v22c"))
		return -ENODEV;

	/* This register is in the Meson pinctrl range. The GPIOV_0 nibble
	 * sits above the BOOT pin fields, which stay owned by that driver.
	 */
	reg = devm_ioremap(dev, CM311_ROUTE_REGISTER, sizeof(u32));
	if (!reg)
		return -ENOMEM;

	ret = devm_pinctrl_register_and_init(dev, &route_desc, reg, &pinctrl);
	if (ret)
		return ret;
	return pinctrl_enable(pinctrl);
}

static const struct of_device_id route_match[] = {
	{ .compatible = "cm311,sdio-route" },
	{ }
};
MODULE_DEVICE_TABLE(of, route_match);

static struct platform_driver route_driver = {
	.probe = route_probe,
	.driver = {
		.name = "cm311-sdio-route",
		.of_match_table = route_match,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(route_driver);

MODULE_DESCRIPTION("CM311 G12A SD_EMMC_B route to the SDIO pads");
MODULE_LICENSE("GPL");
