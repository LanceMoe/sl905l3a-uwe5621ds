# 第三方来源与许可证

- 驱动源码来自 [armbian/uwe5622](https://github.com/armbian/uwe5622)，固定提交
  `d2d6bc45c5ef9cefdd53e6c51cec3ba948b84972`。源码发行包保留文件中的原始版权和许可证声明，
  已应用移植修改的驱动源码保存在 `driver/uwe5622/`。
- 本项目编写的补丁、脚本、测试和文档使用 GPL-2.0-only，完整文本见 LICENSE。
  文件自身声明适用时优先保留并遵守其条款。
- Arm GNU 编译器由下载脚本按版本及 SHA-256 获取，遵守工具链自身许可证；不打包进驱动发行包。
- CI 内核头文件及 Module.symvers 由 ophub/kernel 的明确版本归档获取，仅用于对应内核的构建。
  CI 重建头文件自带的主机 fixdep/modpost 工具以在 x86_64 runner 上运行，保持目标 ABI 数据不变。
- Wi-Fi 固件、校准 INI 和 Android 固件不随项目发行；使用已有系统中有权使用的文件。

含模块的交付归档同时包含对应驱动源码与源码指纹，便于审阅、重建和遵守 GPL 要求。

仓库仅纳入驱动构建源码，不导入上游 `reference/`、CI 配置或 `unisocwcn/fw/` 固件。原文件版权与许可声明保持不变。
