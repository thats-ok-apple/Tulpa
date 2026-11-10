# MCP 命令行

Windows MCP 包内双击 `Tulpa.CLI.exe` 打开终端。也可从 Tulpa 托盘选择“打开命令行”，或运行 `Tulpa.exe --cli`。终端沿用 Linux 版的青色主色、紫色状态及分组菜单；原 Linux CLI/TUI 保持不变。

Agent 无需操作图形界面。命令行复用本目录当前运行的 Tulpa，管理与 GUI 相同的连接、QQ 设置、B站账号和学习库，不启动第二套聊天循环。

```powershell
cd C:\你的路径\Tulpa
.\Tulpa.CLI.exe start
.\Tulpa.CLI.exe --json status
.\Tulpa.CLI.exe --json connections list
.\Tulpa.CLI.exe --json sl status
.\Tulpa.CLI.exe --json personas
```

`start` 复用正在运行的服务；未运行时通过原桌面启动器在后台启动，仍需要 Windows 包的 WebView2。若不需要桌面运行时，`serve` 在当前终端直接运行同一 MCP 后台，关闭终端或 Ctrl+C 会停止它；此时不要再启动同目录 GUI。两种启动入口共用进程锁，不允许两个后台同时访问同目录。开发态使用 `.venv\Scripts\python.exe -X utf8 -m chatlocal.mcp_cli serve`，另一终端将以下 exe 命令替换为该 Python 模块入口。

`stop` 彻底停止当前安装的服务，包含已有 MCP 会话；`exit` 只退出交互终端。除 `start`、`serve` 外，普通命令不会偷偷启动服务。

## 创建与管理 MCP 连接

先输出可编辑的授权模板，检查后创建。例如仅开放 B站只读：

```powershell
.\Tulpa.CLI.exe --json connections template bilibili > bili.json
.\Tulpa.CLI.exe mcp enable
.\Tulpa.CLI.exe --json connections create --file bili.json
```

文件支持 UTF-8（可带 BOM）。Windows PowerShell 5.1 的 `>` 默认写 UTF-16，请改用 `| Set-Content -Encoding UTF8 bili.json`；PowerShell 7 可直接重定向。创建返回连接 ID 与 `credential_saved: true`，不返回 Token。空名称自动生成。三个 `modules` 开关与 GUI 完全相同，关闭模块会忽略它的遗留权限；仅聊天或 B站读取无需导入数据。

仅指定一个 QQ 群聊天：

```powershell
.\Tulpa.CLI.exe --json groups
.\Tulpa.CLI.exe --json connections template qq-chat --group QQ账号:group:群号 > chat.json
.\Tulpa.CLI.exe --json connections create --file chat.json
```

请把示例中的 `QQ账号:group:群号` 替换成 `groups` 返回的真实 `conversation_id`。模板只开放该群的发送与持续聊天；图片、收藏、reaction 等可根据用户授权在 JSON 中明确开启。`connections permissions` 显示可用字段。模板还支持 `qq-history`、`wechat-history`，未导入所选历史平台会返回导入提示，不会改为自动读取整库。

```powershell
.\Tulpa.CLI.exe connections disable 连接ID
.\Tulpa.CLI.exe connections enable 连接ID
.\Tulpa.CLI.exe connections edit 连接ID --file permissions.json
.\Tulpa.CLI.exe connections export 连接ID
.\Tulpa.CLI.exe connections delete 连接ID
```

编辑文件为权限增量，例如 `{"chat_images": true}`；默认带当前 revision 防止并发覆盖。`disable` 可恢复，`revoke` 不可恢复，`delete` 彻底删除这条连接，不删除学习库。`export` 将宿主配置保存到本机受保护目录，仅输出路径，不把密钥打印到终端。

GUI 创建的旧连接只有凭据哈希，CLI 不恢复其明文。用户可显式提供此前保存的 MCP JSON：

```powershell
.\Tulpa.CLI.exe connections import 连接ID --file client.json --server tulpa
```

导入会核对本安装的 MCP 地址、凭据与连接 ID；不会扫描浏览器、Codex 或 DSH 配置搜密钥。

## 直接调用工具

```powershell
.\Tulpa.CLI.exe --json tools --connection 连接ID
.\Tulpa.CLI.exe --json call get_bilibili_status --connection 连接ID
.\Tulpa.CLI.exe --json call search_videos --connection 连接ID --args-file search.json
```

先读取 `tools` 返回的实际 schema，再准备参数；不要假定未授权工具存在。`--args-file -` 从 stdin 接收 UTF-8 JSON，省去 shell 引号转义。工具返回原 MCP 结构，包括文字、证据和图片内容。此路径执行真实 MCP 初始化与 tools/call，不调用任意 OneBot action，也不绕过群范围、输入批次、等待、引用、频控或幂等规则。

持续聊天仍由外部 Agent 运行 `wait → plan → reply/沉默` 循环，CLI 不增加模型服务。调用超时或中断不会自动重发；写操作须检查回执并沿用业务幂等键。

## QQ、B站和学习管理

```powershell
.\Tulpa.CLI.exe --json sl status
.\Tulpa.CLI.exe sl configure --accept 用户已同意的consent_fingerprint
.\Tulpa.CLI.exe sl select 进程选择ID
.\Tulpa.CLI.exe sl test
.\Tulpa.CLI.exe sl cancel
```

`sl status` 提供当前协议、动作范围和 `consent_fingerprint`。取得用户对这些内容的授权后才能提交它；协议变更仍需重新同意，不由 CLI 自动勾选。多个 QQ 或待扫码时按照返回的状态继续；`retry`、`revoke` 复用原功能。已有外部 OneBot 可通过 `sl settings --file onebot.json` 保存，字段为 `sender_url`、`events_url` 等原配置格式；敏感值从文件或 stdin 输入，不放进命令行。

```powershell
.\Tulpa.CLI.exe --json bilibili status
.\Tulpa.CLI.exe bilibili login --consent
.\Tulpa.CLI.exe bilibili poll
.\Tulpa.CLI.exe bilibili logout
.\Tulpa.CLI.exe --json learning accounts
.\Tulpa.CLI.exe --json learning groups --account QQ账号
.\Tulpa.CLI.exe --json learning library --account QQ账号 --group 群号
```

B站登录生成受保护的本地二维码 PNG，由用户扫码；不转移其他程序凭据。`learning edit --file change.json` 使用原管理接口的 `account/group/change` 格式，学习库仍按账号与群归属，不依附 CLI 或 MCP 连接。

可选历史资料命令：`data accounts`、`data status`、`data catalog --file catalog.json`、`data read --file import.json`、`data job 任务ID`、`data cancel 任务ID`。catalog/read 使用原 GUI 数据接口的 JSON，任务异步执行；仅实时聊天的用户无需执行这些命令。高级 `api METHOD /api/... --file body.json --param key=value` 仅访问支持的本机配置路由，不增加通用 OneBot 调用入口。

## 退出码、数据与验证

- `--json` 可放在命令前后；成功结果写 stdout，命令错误写 stderr。
- 退出码 0：命令成功；1：参数、认证或传输错误；2：工具/业务结果报告失败；130：用户中断。失败不自动重试。
- 管理凭据位于 `data/cli-access`；显式创建或导入的 MCP 凭据位于 `data/cli-connections`。Windows 使用 CurrentUser DPAPI，并限制目录 ACL；Linux 使用项目已有本机加密存储。复制到另一 Windows 用户不能直接解密。
- `data/cli-exports` 是用户主动导出的宿主配置，含必要明文 Token，目录仅限当前用户和系统访问。不要提交到 Git 或粘贴进模型上下文。
- 升级前备份安装目录；人格卡、账号、授权与学习库沿用原位置，不迁移。旧程序可忽略新增 CLI 文件；停止新程序后恢复升级备份内的程序文件即可回滚。
- 自动验收：`python -X utf8 scripts/check_mcp_cli.py`；打包态追加 `--package 安装目录`。隔离库覆盖网关认证、跨安装拒绝、模块授权、凭据绑定与加密、暂停/撤销/删除、真实本机 MCP HTTP 调用、审计与超时不重试，不向真实 QQ 发送消息。
