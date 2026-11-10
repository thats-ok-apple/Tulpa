# Tulpa MCP 版

0.5.7 只发布 MCP 版，复用 Codex、DeepSeek Harness、Claude Code 等外部 Agent，不运行第二个回答模型。旧完整版停止维护。Linux 使用原 CLI/TUI，见 [Linux 教程](https://github.com/fumingyang2004/Tulpa/blob/v0.5.7/doc/LINUX.md)。

## 第一次使用

Windows 完整解压 `Tulpa-MCP-0.5.7-win-x64.zip`，打开 `Tulpa/Tulpa.exe`。需要 .NET Framework 4.8 和系统 WebView2 Evergreen Runtime，不需要另外安装 Python 或 Node。

1. 首页点击 **开始创建链接**，按需要启用 QQ、微信、哔哩哔哩模块。
2. 在对应模块勾选能力。仅使用实时 QQ、水群或 B站时，不需要导入聊天资料；只有选中历史读取时才要求对应平台已有资料。
3. QQ 模块内勾选 SnowLuma 协议并点击 **一键配置**；B站从左侧账号设置扫码。已有连接可在高级选项导入。
4. 点击 **一键开启 MCP**，名称可留空。将生成的配置交给外部 Agent，重新连接并刷新工具列表。
5. 首页连接列表可以折叠，每条连接可单独启停或删除。左侧 **QQ 学习管理** 按机器人 QQ 与群号管理积累。

Windows 命令行打开 `Tulpa.CLI.exe`，支持配置和调用同一套已授权 MCP 工具，见 [CLI 教程](MCP_CLI.md)。GUI 与 CLI 共用本安装的服务、授权和数据。

## 能力

- QQ / 微信：按授权范围读取已导入的聊天和附件；只有 QQ 提供发送与群管理。
- 实时 QQ：使用 OneBot HTTP 与正向 WebSocket，支持持续群聊、人格、@、引用、图片、表情包、消息表情回应及按账号和群保存的学习库。
- B站：搜索、推荐、详情、字幕/可选本地 ASR、真实画面与评论，只读。原生视频卡片跳转未通过验收，创建界面不开放分享入口。[B站说明](BILIBILI_MCP.md)
- 文件：可经 OneBot 下载授权群文件，将原文件路径交给同机 Agent 的文件工具。不会因为下载而自动执行文件。

Windows MCP 包不附带 Gradio、内置 Harness、OCR/Office 框架或语音模型。保留 FFmpeg 与媒体后端；已安装的可选模型可继续使用，首页不再显示组件下载模块。模型是否能看图、是否持续执行及其工具审批由宿主决定。

## 授权与连接

三个模块的总开关独立；关闭模块时，其中的遗留选项不会进入新授权。发送、收藏、reaction 和群管理按能力分别授权，升级不会自动开放新权限。可见工具由当前连接实际权限和依赖状态决定，以 `tools/list` 为准。

写权限属于持续授权：符合范围的调用会直接执行，不会逐次返回 Tulpa 弹窗。发送仍保留目标校验、限流和幂等；结果未知时不自动重发。暂停或删除连接会停止后续访问，无法撤回已交给 QQ 的操作。

## 升级与数据

按 [保留数据升级](UPGRADE.md) 使用新版 `scripts/upgrade_installation.py` 更新原 MCP 安装。先从托盘彻底退出并备份；保留数据、附件、授权、学习库和自改人格，升级后重新连接 MCP。新包没有任何账号或登录凭据，不要分享使用过的整个程序目录。

## 开发与打包

安装 `requirements/mcp.txt` 后运行 `python app_mcp.py`，访问 `http://127.0.0.1:7861/`。可选历史读取器和构建依赖见 [构建说明](DESKTOP.md)。

使用 `python desktop/build.py --mcp-only` 构建 Windows MCP 包，`--bundle-snowluma` 可加入经固定哈希校验的未初始化官方运行包。正式构建要求干净 Git 提交；归档按清单生成，不复制个人数据。Linux 使用 `scripts/build_linux_release.py` 生成 MCP 终端源码包，不复制 Windows 运行时。
