![Tulpa](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/assets/title.png)

# Tulpa MCP · Linux 终端版

把 QQ / 微信资料与 B站视频接入外部 Agent，也让它带着自定义人格参与 QQ 群聊。
这是 [Tulpa](https://github.com/fumingyang2004/Tulpa) 0.5.7 的 Linux MCP 终端源码发行版，
包含 CLI、终端界面、MCP 服务、资料检索和 SnowLuma（SL）托管。
当前只维护 MCP 版，复用外部 Agent，无需在 Tulpa 里填写模型 API Key。

## 安装与启动

需要 Linux、Python 3.13 或 `uv`，首次安装依赖需要联网。解压发行包，进入 `Tulpa` 目录后运行：

```sh
./tulpa
```

首次自动安装依赖、启动后台并打开终端界面；后续运行复用同一后台。
如果解压工具丢失可执行权限，先执行 `chmod +x tulpa`。
Python 方式需要系统提供 `python3.13` 及其 `venv` / `ensurepip`；使用 `uv` 时由它准备 Python 环境。

按 `q` 退出界面后，后台继续运行。常用命令：

```sh
./tulpa start       # 只启动后台
./tulpa stop        # 停止本安装目录的后台及其托管的 SL
./tulpa status     # 查看状态
./tulpa --help
```

默认管理地址为 `http://127.0.0.1:7861`。需要换端口时使用
`./tulpa --url http://127.0.0.1:7862`；后续命令也传入同一地址。
安装、后台服务、快捷键和 CLI 详见 [Linux 使用说明](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_TERMINAL.md)。

## 连接 QQ：SL Docker 托管

适合没有图形桌面的 Linux 主机。先安装并启动 Docker，确认当前用户可以访问本机 Docker 引擎。

1. 在终端界面打开「SL 托管」，点击「启用 Docker 托管」，阅读并确认协议。
2. 等待镜像下载与 QQ 启动，点击「扫码登录」，取得本实例的 noVNC 地址和密码。
3. 在浏览器打开虚拟桌面，用手机 QQ 扫码。远程主机按弹窗中的 SSH 转发说明连接。
4. 等待显示「QQ 已就绪」，再到 MCP 授权页选择测试群和所需权限。

停止后保留容器的登录数据；再次启动恢复原实例。是否需要重新扫码由 QQ 决定。
手机浏览器请使用弹窗给出的完整链接；遇到黑屏可重新打开「扫码登录」并刷新浏览器。
镜像及 QQ、SL 均由使用者独立下载，源码包不附带它们或任何账号登录状态。

已有独立 SL 时，可在「SL 连接」填写同一账号的 OneBot HTTP 和正向 WebSocket 地址与 Token，
也可运行 `./tulpa setup`。有桌面的 Linux 主机还可选择适配本机架构的官方 SL 完整包进行本地托管。
三种接入方式的操作见 [SL 连接与托管](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_TERMINAL.md#连接已有-sl)。

多个 QQ 同时使用：按 `F2` 进入「实例管理」，新建并启动实例，切换后分别配置 SL
和 MCP。每个实例有独立的容器、端口、账号数据和授权；可以在一个 TUI 管理白龙娘、
大肥鱼、Claude 大小姐等多个账号。详见 [多实例说明](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_TERMINAL.md#同时运行多个-qq--sl-实例)。

## 接入 Agent 与人格群聊

1. 启用 MCP，为每个 Agent 创建独立连接，选择平台、群和权限。
2. 保存只显示一次的连接配置和 Token，按 [MCP 接入说明](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_AGENT_SETUP.md) 配置外部 Agent。
3. 先验证工具能读取已授权的群。持续群聊需额外开启持续聊天和发送权限，并保持外部 Agent 执行。
4. 让 Agent 列出人格和授权群，核对目标后开始聊天。例如：

> 在我授权的测试群里使用小鲸鱼2号聊天，先看上下文，接得上话再回复，直到我停止。

随包提供 `little_whale`、`little_whale_v2`、`dragon_girl` 等人格，
也可以把自己的 UTF-8 Markdown 人格放进 `chatlocal/prompts/mcp_chat/`。
人格只影响表达，不会改变外部 Agent 的底层模型。
Tulpa 准备会话后会显示「等待 Agent」；外部 Agent 退出或停止执行后，Tulpa 不会自行继续推理。
外部 Agent 生成的图片可用 `import_chat_image` 导入，再经 Tulpa 的发送计划交给 SL 发到 QQ。
本机文件上传脚本及调用流程见 [发送生成的图片](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_AGENT_SETUP.md#发送生成的图片)。
与 Windows 共用 MCP 实现：等待与补读可交付当前消息的图片、引用和转发材料，复用经内容校验的图片；学习提交保留回执并及时交接新消息。
实时聊天也支持授权好友私聊。语音、视频、文件发送，以及撤回、转发、戳戳、空间、好友、
群相册和扩展群管理见 [SL 扩展工具](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/SL_TOOLS.md)；新增独立权限通过 CLI 或管理 API 开启。

## 资料与可选组件

终端界面可管理本机数据、聊天记录、搜索与 MCP 连接。
QQ / 微信历史读取需要实际可读的数据源及有效密钥；OneBot 实时接入不会自动补齐全部历史。
本机客户端路径、导入和取钥限制见 [Linux 原生数据说明](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_TERMINAL.md#原生-qq--微信数据)。

需要原生读取器、OCR、Office 或本地语音时，可在「组件服务」按需安装，或一次安装全部：

```sh
sh setup-linux.sh --full
```

这里的 `--full` 指安装可选读取与媒体组件，不是另一个完整版发行包；安装会下载依赖、语音程序和模型。
用户级常驻服务可用 `./tulpa service install --start`，桌面终端快捷入口可用 `./tulpa desktop-install`。

## B站浏览

本版包含与桌面 MCP 共用的 B站只读工具：搜索、推荐、详情与分 P、字幕/本地 ASR、真实抽帧和评论。
B站权限仍属于原 MCP 连接，不另建服务，也不要求导入聊天资料。
登录和媒体处理能力、工具协议及限制见 [B站说明](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/BILIBILI_MCP.md)。
Linux 已覆盖离线接口回归；真实账号扫码和媒体读取的实测记录来自 Windows，不能视作 Linux 实机通过。
QQ 原生视频卡片跳转未通过验收，本版不开放为可用分享功能。

## 测试范围与数据

本版的验证分为本机 WSL2 / Ubuntu x86_64 安装启动、隔离接口回归，以及 GitHub Actions 的 x86_64 / ARM64 测试。
这些检查不代表在所有 Linux 发行版、QQ / 微信版本上验证了真实账号读取；本次不进行真实 QQ 发言或扫码。
原生客户端版本、权限和密钥会影响读取能力，ARM64 不提供未经验证的主动启动取钥。
早期移植的真实环境结果见 [Linux 验收记录](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_ACCEPTANCE.md) 与 [Docker 验收记录](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/DOCKER_ACCEPTANCE.md)。

干净发行包没有账号、聊天记录、记忆、API Key、MCP 授权、SL/VNC 密码、运行环境或缓存。
首次运行会创建本实例的数据目录和管理凭据。资料保存在本机，但发送给外部 Agent 或模型服务的内容
仍受所用服务的数据处理规则约束。MCP 发送与群管理是独立的持续授权，勾选后调用直接执行。
使用过的目录含私人数据，不要直接压缩发给别人。

## 文档与维护

- [Linux 使用说明](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_TERMINAL.md)：安装、启动、SL、MCP、原生数据、CLI。
- [Agent 接入](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_AGENT_SETUP.md)：连接凭据、验证、群聊与排错。
- [保留数据升级](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_UPGRADE.md)：同目录更新、备份与服务恢复。
- [开发与打包](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_CONTRIBUTING.md)：Linux 检查及干净源码包。
- [模型与分工](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_CONTRIBUTORS.md)：Codex 与 Claude Code 负责的功能模块。
- [终端后端 API](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/TERMINAL_API.md)、[安全报告](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/SECURITY.md)、[第三方组件](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX_THIRD_PARTY.md)。

## 许可与致谢

Tulpa 自有代码采用 [MIT License](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/LICENSE)。本 Linux 移植保留上游版权与第三方许可。
小鲸鱼人格改编自 [qq-bridge](https://github.com/Derpyu520/qq-bridge)，
表情链路设计参考 [QQ-agent](https://github.com/K0nd1us/QQ-agent)。
[SnowLuma](https://github.com/SnowLuma/SnowLuma)、QQ、微信及模型服务需独立遵循各自条款。
已取得 SnowLuma 主开发作者对自动部署的书面同意，前提是不修改 SnowLuma 本体；[查看授权记录](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/assets/SnowLuma.png)。
本项目不是腾讯官方产品。
