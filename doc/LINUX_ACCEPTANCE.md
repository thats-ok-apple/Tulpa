# Linux 移植验收记录

## 上游集成回归（2026-10-15）

环境：Linux ARM64，Python 3.13.16。以下验证针对整合上游更新后的 Linux 分支。

- `scripts/check_linux_suite.py` 的 **74 份回归脚本全部通过**，包括原有 MCP / 业务检查、
  TUI、多实例、Docker、扩展媒体与权限，以及上游新增的共享学习库和黑话管线检查。
- `scripts/check_linux_components.py` 实际执行 DOCX / PPTX / XLSX / PDF 解析、离线 OCR、
  FFmpeg 和 whisper.cpp 的合成静音推理，全部通过。
- 实际运行 `scripts/install_readers.py`，下载并安装 44 个固定版本的读取器及许可文件。
  安装夹具同时覆盖 Linux 归档校验、目标目录边界和规范化路径。
- 发行检查覆盖 Linux README 替换、解包后再次打包、中文测试说明文件名、文档链接、
  私人目录排除及解包后的后端启动。源码发布检查、Python 编译和 Shell 语法检查通过。
- CI 新增 Linux x86_64 / ARM64 完整安装和回归，并保留 Windows 检查；
  远程结果以 PR 的实际检查状态为准，本机 ARM64 结果不代表其他平台已经通过。

本轮使用隔离数据和模拟 OneBot / QQ 服务，没有向真实 QQ 发送消息或调用外部模型。
它验证集成后的接口和回归行为，不证明所有客户端版本或账号的长期登录稳定性。

## 首次移植验收（2026-10-14）

日期：2026-10-14。环境：Arch Linux ARM64，Python 3.13.16，Textual 8.2.8。
前端主要由 Claude Code（`claude-opus-5`）完成；后端、原生读取适配和发行主要由
Codex（`gpt-6-astra`）完成。功能分工见 [模型贡献记录](LINUX_CONTRIBUTORS.md)。

追加：无桌面环境的 Docker 托管已补充，详见 [DOCKER_ACCEPTANCE.md](DOCKER_ACCEPTANCE.md)。
下列 58 份回归记录属于此前完整移植验收；Docker 新增检查另列，未将此前结果重复计为本轮执行。

## 已完成

- 原版业务 API 接入终端服务：模型对话、聊天、回复与群管理审批、记忆、关注卡、
  文件、集合、知识资料、工作区、媒体、MCP 和 SL。
- TUI 共 18 页；保留 1–7，增加 8/9/0 和可滚动侧栏，包含输入保护、确认与取消。
- Linux QQ/微信来源和密钥适配、POSIX 锁、加密页及 WAL、进程身份与退出清理。
- SL 官方 Linux x64/ARM64 完整包检查、托管与本机加密凭据；已有 OneBot 连接配置。
- 完整安装、可选组件任务、用户级 systemd 服务、桌面终端入口命令、源码打包。

## 验收结果

- `scripts/check_linux_suite.py` 中 58 份适用于 Linux 的回归脚本全部通过。
  第一轮收集失败后进行了定向修复和复测；没有把初次失败当成通过。
- `scripts/check_linux_full.py` 的 10 项验收通过：完整 API、对话生命周期、来源及
  密钥权限、凭据防篡改、任务退出、父进程退出、SL 归属、加密页和大 QQ 库锁页边界、
  微信密钥适配、服务与源码包边界。
- TUI 自动交互覆盖全部页面、窄终端布局、输入焦点、模型设置、回复预览/确认、
  记忆与关注卡、删除预览令牌；真实 FastAPI/HTTP/WS 联调的 2 项测试通过。
- 实际组件执行通过：DOCX、PPTX、XLSX、PDF 原生解析，离线 OCR，FFmpeg，
  whisper.cpp 使用约 190 MB 固定模型完成合成静音推理；均使用本地合成文件。
- DeepSeek Harness ARM64 原生运行时 `--version` 返回 `0.1.5-rc.1`，退出码 0。
- `setup-linux.sh --full` 在修复后完整执行成功，已有安装可重复运行。
- systemd 配置验证、实际安装/重启通过；TUI 在真实 PTY 中连接后台、打开 Agent 页，
  正常退出码 0，退出 TUI 后后台服务仍运行。
- Python 编译检查和 `git diff --check` 通过。

本地日志：`.tmp/linux-acceptance/consolidated-results.json`、
`.tmp/ui-final-tui.log`、`.tmp/ui-final-integration.log`、
`.tmp/linux-components-runtime.log`、`.tmp/linux-full-install-final.log`。
日志属于本机验收产物，不随源码包分发。

## 本轮集中修复

- Python 子进程引导模块与标准库 `collections` 的命名冲突。
- 语音构建缺少 Make 时的 Ninja 选择、含空格的编译器路径。
- OCR 的桌面 OpenCV 依赖改用唯一的 headless cv2 提供包。
- systemd 的 WorkingDirectory 与 ExecStart 使用不同的路径引用规则。
- 原管理 API 的请求大小限制、后台安装任务退出时的进程归属。
- Textual 内置属性冲突、不支持的 CSS 选择器、下拉框值/标签顺序和空值处理。

## 未作真实账号验证的范围

- 本机 SL 连接尚未配置，未登录或操作真实 QQ/微信，未调用付费模型。
  登录稳定性、真实发送、长期收发和特定客户端版本的数据库解密需使用实际账号验收。
- ARM64 的主动启动取钥路径尚未获上游验证，因此明确禁用；可以使用 SL、经数据库
  验证的已有密钥、缓存或系统权限允许的被动读取。不能据本轮测试宣称所有版本可自动取钥。
- 本轮在 ARM64 本机实测；x86_64/ARM64 CI 已配置，未在本机声称执行了远程 CI。
- 原 Windows EXE 专用升级修复工具、Windows ACL 测试与 Gradio 网页专用测试不属于
  Linux 终端验收集合。Linux 使用独立安装、来源配置、systemd 和 TUI 流程。

运行说明见 [LINUX_TERMINAL.md](LINUX_TERMINAL.md)。
