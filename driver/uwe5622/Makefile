ifneq ($(CONFIG_MESON_UWE5621)$(CONFIG_AW_WIFI_DEVICE_UWE5622)$(CONFIG_RK_WIFI_DEVICE_UWE5622),)
obj-y += unisocwcn/
endif
obj-$(CONFIG_WLAN_UWE5622)    += unisocwifi/
obj-$(CONFIG_TTY_OVERY_SDIO)  += tty-sdio/

UNISOCWCN_DIR := $(shell cd $(src)/unisocwcn/ && /bin/pwd)
UNISOC_BSP_INCLUDE := $(UNISOCWCN_DIR)/include
export UNISOC_BSP_INCLUDE

ifeq ($(CONFIG_MESON_UWE5621),y)
UNISOC_FW_PATH_CONFIG := "/lib/firmware/uwe5621ds/"
else
UNISOC_FW_PATH_CONFIG := "/lib/firmware/uwe5622/"
endif
export UNISOC_FW_PATH_CONFIG
