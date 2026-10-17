# CI 与发行

工作流为 `.github/workflows/ci.yml`，需要将文件提交、推送到 GitHub 后才会执行。
普通分支推送、PR 和手动运行只检查、构建及保存 Actions 产物。
**推送与 VERSION 一致的版本标签会在 CI 成功后自动创建并公开 GitHub Release。**
工作流不会自行提交代码、推送标签或发送消息。

## 自动运行

Push / Pull Request / 手动 Run workflow 都执行：

1. Bash 语法、ShellCheck、Python 编译、git diff 空白检查和 actionlint。
2. 获取固定上游、应用项目补丁，运行所有 `tests/test_*.py`。
   包括实际驱动函数的 ASan/UBSan 故障注入、IRQ/接收/启动/命令回归、诊断采集、
   参数恢复和安装/重装/升级/卸载事务测试。
3. 生成源码包和集成更新包，上传测试日志。
4. 下载明确版本的编译器与内核头文件，验证 SHA-256，实际编译两个 ARM64 模块及 MODPOST。
5. 生成匹配内核的模块包（含对应源码、配置、安装工具和文件清单），上传构建日志。

默认目标在 `ci/kernel.json`：`6.18.56-ophub`，用于编译回归。
`6.18.37-ophub` 是实机验证目标，当前仓库不提供其他内核冒充该版本的模块。
CI 成功不代表已验证真实 SDIO 硬件、启动顺序或无线链路质量。

下载默认头文件时，校验整个 6.18.56 内核归档，再仅提取其中的 header 归档。
使用安全 tar 提取规则，并检查 UTS_RELEASE、autoconf 和 Module.symvers。
不会跟随归档中的 DT/include-prefix 符号链接；这些别名不参与当前外部模块构建。
只重建 x86_64 主机执行的 fixdep/modpost，不修改 ARM64 的 elfconfig、设备表偏移或符号版本表。
上游删除该固定归档时工作流会失败，需要维护者明确更新版本与 SHA，不静默切换到最新版。

GitHub Actions 使用固定提交 SHA，检查/构建权限为 `contents: read`，checkout 不保留凭据。
仅标签发布任务有 `contents: write`，仅其最后一步接收 GH_TOKEN。
PR 使用 `pull_request`，不会以 `pull_request_target` 执行提交的代码。
工作流输入通过环境变量交给 Python 校验，不插入 Shell 源码。

## 手动为其他精确内核编译

进入仓库 Actions → CI → Run workflow，三项同时填写：

- `kernel_release`：例如 `6.18.37-ophub`。
- `kernel_url`：有使用权且可信的 HTTPS 头文件归档 URL，需含精确的生成头文件和 Module.symvers。
- `kernel_sha256`：下载文件的 SHA-256。

工作流输入优先于同名仓库变量。三项都未提供且没有仓库变量时，使用固定默认值；
最终解析出的三项必须完整成组，否则拒绝构建。
支持根目录就是准备好的头文件树的归档，或内含 `header-<内核版本>.tar.gz` 的 ophub 内核归档。
不能用别的内核的头文件后仅修改版本字符串。

Actions 页面下载 `source-and-tests` 或 `arm64-modules-<内核版本>` 产物。
平台下载的外层 zip 内才是项目 tar.gz 和 sha256 文件。
只安装与盒子运行内核一致的模块包；source 包可用于盒子本机重新构建。

## 自动创建 Release

将所有项目和工作流文件提交、推送后，再推送版本标签：

```bash
git tag v0.8.0
git push origin v0.8.0
```

`v0.8.0` 必须与 `VERSION` 中的 `0.8.0` 一致，否则检查阶段就会失败。
之后自动运行完整检查和 ARM64 编译，再从**本次运行**下载源码/集成包和内核二进制包：

1. 验证每个外层归档 SHA-256、内部逐文件清单及版本/类型/内核身份。
2. 对实际 `.ko` 运行 modinfo，核对真实 vermagic 和 ARM64 架构，避免附件标错内核。
3. 生成 `INSTALL.md` 附件、`RELEASE.json`、总 `SHA256SUMS` 和标明支持内核的发布说明。
4. 创建草稿并上传全部附件，上传成功后自动公开。

首次上传失败会保留可恢复草稿，重新运行该标签 CI 即可继续。
已公开的同标签 Release 不覆盖附件；需要改变已发布内容时应递增 VERSION 并推送新标签。
未通过检查、编译或附件校验时不公开 Release。只有版本标签 push 能执行发布，PR 和手动构建不能发布。
仓库无需额外 PAT，使用 GitHub 提供给该发布任务的 GITHUB_TOKEN；需允许 Actions 使用 contents 写权限。

Release 附件包括：

- 源码 tar.gz、集成更新 tar.gz、明确内核版本的二进制 tar.gz。
- 每个 tar.gz 的独立 `.sha256`，以及汇总 `SHA256SUMS`。
- 可直接阅读的 `INSTALL.md` 和包含可用二进制内核/源提交的 `RELEASE.json`。

模块包本身包含对应源码。Release 说明会明确列出实际构建内核，
不会把 `6.18.56-ophub` 二进制描述为适用于实机 `6.18.37-ophub`。

### 让自动 Release 构建实机 6.18.37 的二进制

目前固定上游下载配置只提供已校验的 6.18.56 构建输入。
要发布 6.18.37 二进制，须提供这台盒子精确的、已准备的头文件与 Module.symvers，不能拿其他版本替代。
可在盒子上打包，不会加载驱动或重启：

```bash
kernel=$(uname -r)
headers=$(readlink -f "/lib/modules/$kernel/build")
tar -C "$headers" -czf "/root/header-$kernel.tar.gz" .
sha256sum "/root/header-$kernel.tar.gz"
```

把该归档放到可信且 CI 可直接 HTTPS 下载的位置，然后在仓库 Settings → Secrets and variables →
Actions → Variables 设置三项非秘密变量：

| 变量 | 示例 |
| --- | --- |
| `CI_KERNEL_RELEASE` | `6.18.37-ophub` |
| `CI_KERNEL_URL` | 精确头文件归档的 HTTPS URL |
| `CI_KERNEL_SHA256` | 上一步归档的 SHA-256 |

或直接在 `ci/kernel.json` 中更新对应的 release/url/sha256 并提交。
**在推送版本标签前配置好**，标签自动构建与 Release 会使用该输入，并按实际内核命名附件。
若当前版本已发布，先更新 VERSION，使用新标签发布；不要移动或覆盖旧标签。

没有 6.18.37 头文件时，你现有的已构建 v8 模块仍可通过集成更新包安装。
这份工作流不会生成冒用 6.18.37 版本字符串的预编译模块。

## 本地复现

在 Linux / WSL 中安装 gcc、make、python3、git、kmod、curl、xz-utils、ShellCheck 和 Go：

```bash
bash scripts/check.sh
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7 .github/workflows/*.yml
bash scripts/run-tests.sh
bash scripts/fetch-toolchain.sh
python3 ci/prepare-headers.py
# 上一步打印 KDIR 和 KERNEL_RELEASE；设置为相应值后构建。
KERNEL_RELEASE=6.18.56-ophub KDIR="$PWD/.cache/ci-headers-6.18.56-ophub" \
  CROSS_COMPILE="$PWD/.cache/toolchain/bin/aarch64-none-linux-gnu-" bash scripts/build.sh
```

准备脚本拒绝覆盖已有目标目录。重新运行时可保留并使用已准备的头文件，直接构建。
不用在盒子上安装 CI 默认头文件或执行这些交叉构建步骤。

## 生成交付文件

```bash
python3 scripts/package.py --kind source
python3 scripts/package.py --kind integration
python3 scripts/package.py --kind modules --kernel-release 6.18.56-ophub
```

- `cm311-uwe5621ds-0.8.0-source.tar.gz`：项目和应用补丁后的源码，可离线开始构建。
- `cm311-uwe5621ds-0.8.0-integration.tar.gz`：给已有 v8 目录更新安装/CI/文档，不覆盖模块和工具链。
- `cm311-uwe5621ds-0.8.0-<内核版本>.tar.gz`：已构建模块加对应项目和源码。

每个文件都有 `.sha256`；归档内 `PACKAGE_MANIFEST.json` 记录类型、内核、上游提交和逐文件哈希。
固定文件内容相同时打包结果可复现（归档时间、所有者及文件模式固定）。
不会收录 `.cache/`、私有日志、诊断包、固件、下载的工具链或其他内核的模块。
模块只从明确的 `dist/<内核版本>` 读取，不依赖通配符猜测。

参考：[GitHub 工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、
[工作流产物](https://docs.github.com/en/actions/tutorials/store-and-share-data)、
[gh release create](https://cli.github.com/manual/gh_release_create)、
[gh release upload](https://cli.github.com/manual/gh_release_upload)、
[ophub 内核发布](https://github.com/ophub/kernel/releases/tag/kernel_stable)。
