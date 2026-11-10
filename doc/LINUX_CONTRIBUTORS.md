# Linux 功能与模型分工

本次增加 Linux CLI / TUI、SL 本地与 Docker 托管、独立多实例、MCP 扩展工具、
已有授权编辑及 Linux 安装和发行流程。

| 工具与模型 | 本次负责的工作 |
| --- | --- |
| Codex · `gpt-6-astra` | Linux 平台与读取器适配，业务后端、CLI、统一启动器、SL 本地与 Docker 托管、noVNC 显示适配、MCP 新工具与授权编辑、多实例后端、媒体导入、可选组件、安装和打包；接口设计、集成修复、回归测试与技术文档。 |
| Claude Code · `claude-opus-5` | Textual TUI 的主要实现与前端修复：业务页面、SL Docker 入口与登录交互、扩展权限与私聊选择、已有授权编辑、图片彩蛋、多实例选择和管理，以及相应的 TUI 交互测试。主要代码位于 `chatlocal/tui/`。 |

前端通过 `TerminalClient` 调用共享业务 API。Codex 负责后端集成和整体验证，
也参与 TUI 的接口衔接与集成修复。

使用说明见 [Linux 终端版](LINUX_TERMINAL.md)，扩展能力见 [SL 工具](SL_TOOLS.md)，
测试与发行入口见 [Linux 开发与打包](LINUX_CONTRIBUTING.md)。
