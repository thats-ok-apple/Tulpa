# Windows MCP 桌面版与构建

当前只维护 MCP 版，不构建或发布旧完整版。

## 运行与升级

Windows 10 / 11 x64、.NET Framework 4.8、系统 WebView2 Evergreen Runtime。完整解压后运行 `Tulpa/Tulpa.exe`，或使用 `Tulpa.CLI.exe` 进入命令行。程序监听本机随机端口；CLI 与 GUI 共用本安装的后台。详见 [MCP 使用](MCP_LITE.md)和[命令行](MCP_CLI.md)。

开启托盘运行后，关闭窗口保留后台，通过托盘菜单「彻底退出」停止；开机启动默认关闭。移动安装目录后需重新设置。不要同时运行两个指向同一数据目录的实例。

升级按 [升级教程](UPGRADE.md) 用新包的 `scripts/upgrade_installation.py` 更新原 MCP 目录，自动备份并保留个人配置、授权、数据、学习库和自定义人格。不要将整个新包直接覆盖已有用户目录。

## 开发与构建

使用 Python 3.13 x64。仅运行 MCP 页面时，安装 `requirements/mcp.txt` 后执行 `python app_mcp.py`，访问 `http://127.0.0.1:7861/`；历史读取器和完整构建环境另需准备。构建机还需要 Visual Studio Build Tools 的 C++ x64 工具链和 Windows .NET Framework C# 编译器。

```powershell
.\scripts\windows\setup.ps1
.\.venv\Scripts\python.exe -m pip install -r requirements/mcp.txt
.\.venv\Scripts\python.exe desktop/fetch_runtime.py
# 准备现有 Whisper.cpp 媒体工具（主包不附带语音模型）。
# 审查并提交源码后执行正式构建：
.\.venv\Scripts\python.exe desktop/build.py --mcp-only --bundle-snowluma
.\.venv\Scripts\python.exe scripts/check_publication.py --archive release/Tulpa-MCP-0.5.7-win-x64.zip
```

输出目录为 `release/0.5.7-mcp/Tulpa/`，发行归档为 `release/Tulpa-MCP-0.5.7-win-x64.zip`。`--no-zip` 只生成目录；`--qa --no-zip` 仅用于本机隔离测试，不能作为正式发行包。

`--bundle-snowluma` 只加入按清单哈希验证的未初始化官方归档，不复制正在运行的服务、用户配置或凭据，不修改 SnowLuma 本体。不加该选项时，用户同意后仍可由 Tulpa 自动下载配置。

构建会运行随包 Python 的 MCP、B站、CLI、聊天、表情、学习、授权与更新回归。`build-manifest.json` 记录版本、来源提交、依赖和文件哈希。ZIP 根据本次文件清单生成，不能从使用过的目录整体压缩。

## 发布校验

运行 [验证说明](VALIDATION.md) 中适用的检查，核对归档内容、CRC、SHA256 与人格卡哈希。Linux 另用 `scripts/build_linux_release.py` 构建源码包并在 Linux 中验证，不能将 Windows 依赖复制过去。

发布的 `SHA256SUMS.txt` 应包含 Windows MCP、Linux MCP 及随附的 FFmpeg 对应源码。包内不含 QA 调试入口、聊天数据库、Token、Cookie 或私人测试记录。实际 EXE 启动与隔离回归不等同于全部 QQ / 微信版本或真实媒体的兼容保证。
