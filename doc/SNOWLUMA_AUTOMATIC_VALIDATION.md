# SnowLuma 自动部署开发验证

开发基线：`cf15cf5987466c97a493bf1f1771dae99b1fcac8`。2026-10-21 的自动部署开发先保留本地；2026-10-22 在适配新版并验证后上传开发源码，不发布 GitHub Release、不升级用户原有运行包。

## 2026-10-22：SnowLuma 2.1.1 更新验证

官方固定版本：[2.1.1](https://github.com/SnowLuma/SnowLuma/releases/tag/v2.1.1) / `f8cb15c27bca3d2d1bd044a921e55bfc44bb5e60`。该版于 2026-10-21 17:07 UTC 发布。升级清单包含 Windows x64、Linux x64 和 ARM64 官方完整包；逐包重新下载，核对官方资产大小、SHA256、运行文件、版本及协议原文。摘要见 `chatlocal/snowluma_manifest.json`，没有以本地计算值代替官方摘要。

实际兼容缺陷：原适配器仅对 `2.0.3` 使用 `/api/processes` 账号字段，新版会落入已经移除的 `probe-login` 路径。现按明确核对的 `2.0.3 / 2.1.0 / 2.1.1` 选择新版接口，未知版本不自动认定兼容。回归覆盖 PID 不存在、重复 PID、加载前账号字段和加载后原生 UIN。

管理认证、协议确认及 OneBot 配置结构相对于 2.0.3 保持兼容，使用细则、隐私政策和许可证哈希均未改变。2.1.1 完整包带 Node 24.22.1；使用随包运行时，不依赖系统旧 Node。Docker 仍独立固定于 1.14.22，本次没有更新容器镜像。

| 环境与范围 | 已执行结果 | 边界 |
| --- | --- | --- |
| Windows x64，独立空数据目录 | 通过生产设置 API 启动；官方冷下载 42,694,125 字节；约 40 秒到就绪；官方初始化、管理认证、进程身份、HTTP 和 WS 同账号验证通过 | 没有复用旧配置、旧缓存或随包文件；WS 只记录 lifecycle/heartbeat 类型 |
| Windows 同版本重启 | 正常退出后仅托管进程退出、QQ 保留；约 10 秒自动重连；实例、QQ 身份、账号哈希和受保护凭据文件哈希保持不变 | 未模拟机器重启或跨版本原地升级 |
| Windows 既有用户资料 | 11 个保护基线文件未变化；托管凭据在所检查应用与上游日志中匹配数为 0 | 当前 QQ 与前日样本不是同一账号；测试用独立实例，未改旧授权 |
| Linux x64，隔离 WSL | 真实官方包安全解压、Node 24.22.1 启动、bootstrap、协议确认及管理认证通过；停在 `waiting_qq` 后取消并停止自有进程 | 没有 Linux QQ，不能算 QQ 连接通过 |
| Linux ARM64 | 官方压缩包下载摘要、文件布局、版本和协议核对通过 | 未执行 ARM64 SnowLuma 原生运行时 |
| Windows full/lite UI | Edge 使用实际设置组件和模拟接口，自动/本地两条路径、勾选、唯一按钮、取消重试、窄屏通过 | 是浏览器 fixture；2.1.1 实机部署由生产设置 API 驱动，没有重做人工浏览器首登 |

本轮真实 QQ 写动作数为 0。发送、媒体、引用和 reaction 仅在 mock 中回归，不把接口兼容和元事件连接当作真实群聊发言验收。

Windows 通过 `check_snowluma_download.py`、管理生命周期、传输、已有连接导入，以及 `check_mcp_onebot.py / check_mcp_actions.py / check_mcp_chat_targets.py / check_mcp_reactions.py / check_mcp_chat_media.py / check_mcp_chat_output.py / check_mcp_image_reuse.py`。Linux 通过管理生命周期、传输、下载、已有连接导入、Docker、Docker TUI、OneBot 和聊天材料增强共 8 项检查。命令均为 `python scripts/check_<名称>.py`，Linux 也可运行 `python scripts/check_linux_suite.py snowluma_managed snowluma_managed_transport snowluma_download snowluma_setup snowluma_docker snowluma_docker_tui mcp_onebot mcp_chat_enrichment`。

此前失败的 `check_mcp_chat_enrichment.py` 本轮在 Windows 和 Linux 均通过；仅补充断言失败时的材料状态，未改生产逻辑或放宽断言。Windows fixture 仍曾输出合成 WS 1011 断开，因此不能宣称先前间歇故障的根因已修复。本轮未重新构建 EXE，不将开发态测试描述为完整安装包验证。

`real_pc_samples` 分别记录 Windows QQ 样本和 Linux 管理服务样本；总体 `real_pc_verified` 仍为 false。旧托管实例原地迁移、真实多账号、系统重启、强制断网和 Linux QQ 尚未完成实机验证。所有私人凭据、实例状态、账号、日志和截图只保存在本地，未进入源码。

## 可重复的离线检查

```powershell
.\.venv\Scripts\python.exe scripts/check_snowluma_download.py
.\.venv\Scripts\python.exe scripts/check_snowluma_managed.py
.\.venv\Scripts\python.exe scripts/check_snowluma_managed_transport.py
.\.venv\Scripts\python.exe scripts/check_snowluma_setup.py
node scripts/check_snowluma_managed_ui.cjs
node scripts/check_snowluma_ui.cjs
```

浏览器脚本沿用项目 Playwright Core / Edge；可设置 `PLAYWRIGHT_MODULE`。后端检查可使用 `--package <目录>` 校验随包 Python 和新模块。

已通过的新离线检查包括：同意前无下载/执行；固定来源和 SHA256；断点续传；缓存与随包文件重新校验；损坏缓存隔离后重试；ZIP/TAR 路径穿越、链接、重复路径、Windows 特殊路径和预置用户数据拒绝；取消后不执行；重复点击不重复启动；重启复用凭据和运行文件；撤权失效；2.0.3 进程账号字段。

Windows 开发环境中的 `check_snowluma_setup.py`、`check_mcp_onebot.py`、`check_mcp_chat_targets.py`、`check_mcp_reactions.py` 通过。Linux Docker 脚本不能以 Windows 的权限/平台检查结果作为验收；已在隔离 Linux 环境重新运行，28 项通过。新版下载与生命周期 fixtures 在 Linux 也通过，均未访问真实 QQ。

真实 Edge 加载 full/lite 共用组件，使用合成接口验证：无需路径的协议勾选与唯一主按钮、下载进度、原有本地导入、QQ 多进程选择、失败重试、撤权、协议变化、窄屏。这里的“就绪”使用模拟账号，不能当作真实 QQ 通过。

## 2026-10-21：SnowLuma 2.0.3 原始验证记录

当时固定版本：2.0.3 / `db142db9e6398c779283eab9afc3552da1e2c361`。Windows 完整包 42,476,694 字节，SHA256 `14614d49b89abb59b3a4cff3e98f9932b95e9c047ae9d0855998abac6179d6f0`。以下保留原始结果，不代表 2.1.1 重复执行了所有步骤。

2026-10-21 已完成真实 Windows x64 首次部署与 Tulpa 重启复连。开始前核对旧实例，完整备份、校验并隔离原活动目录。用户在本次验收时明确授权代为勾选 2.0.3 使用细则、隐私政策并执行一键配置。

第一次在原开发数据目录执行时，下载、初始化完成，但现存来源/MCP 授权绑定的 QQ 与当前在线 QQ 不同；服务返回 `scope_mismatch`，未接入其他账号或扩大旧授权。保留该次记录，停止其托管进程并隔离测试生成的数据。随后将相同开发源码复制到独立、无 `.env`、无聊天、无授权、无 SnowLuma 配置/缓存/随包文件的本地目录，以当前在线 QQ 完成冷启动；没有改变原检查条件。

| 真实步骤 | 结果 |
| --- | --- |
| 授权页操作 | 默认未勾选；勾选后只点一次“一键配置”，未填写路径、端口、密码或 Token |
| 官方冷下载 | 21:57:03 开始，21:57:29 完成校验；42,476,694 字节及 SHA256 与固定官方摘要一致，没有借用研究包或构建缓存 |
| 初始化与绑定 | 官方 bootstrap、登录和协议确认接口通过；自动选择唯一 QQ 主进程，核对其进程身份和登录账号 |
| 首次就绪 | 21:57:43；管理认证、HTTP 登录和 WS lifecycle/heartbeat 为同一 QQ；从开始下载到就绪约 40 秒 |
| Tulpa 重启 | 先正常退出，托管子进程退出，原 QQ 进程保留；22:00:24 自动恢复，22:00:32 就绪，约 8 秒 |
| 接续状态 | 新托管进程，原实例、QQ 进程身份、账号及 DPAPI 凭据文件哈希不变；未再次勾选或配置 |
| 网络与文件权限 | 三个监听端口均为 127.0.0.1；托管目录 88 个路径无 Everyone / Users / Authenticated Users 宽泛 ACE |
| 独立只读复核 | 初始化后和重启后各登录管理接口并建立独立 WS 检查；仅保存元事件类型，不保存聊天正文；未调用 QQ 写动作 |
| 原用户数据 | 人物卡、原配置及学习库的 11 个基线文件均未变化；在开发日志及上游日志中扫描托管凭据，匹配数 0 |

脱敏检查记录和界面截图仅留本机，不提交公开源码。清单中的 `real_pc_samples` 记录以上 Windows 样本；`real_pc_verified` 仍为 false，表示没有完成全平台与异常矩阵，不能把一次 Windows 首次部署推广为所有情况均通过。

尝试独立 Windows MCP QA 构建，随包 Python 中的 SnowLuma 管理、传输和自动下载检查通过；此前的聊天、学习、表情包、引用及 reaction 检查也通过。但现有 `check_mcp_chat_enrichment.py` 中断了完整构建：首次为嵌套转发材料 `incomplete` 断言，单独重跑进入更后面的测试时又遇到合成 WS 断开，发送被安全拒绝；真实接入测试后再次独立执行仍在第 222 行 `incomplete` 断言失败。该用例和对应聊天模块本轮未修改，根因尚未定位，不能断言没有间接影响。未绕过构建门槛、未生成可发布的 EXE/归档，不能将本次标为整包验收通过。留存的候选运行时仅用于隔离检查，不能替换用户正在使用的 release。

开发前检查点：本地分支 `codex/checkpoint-sl-auto-20261021`。本地备份清单含逐文件大小、SHA256、目录及权限；原目录移动到受当前用户/SYSTEM 保护的隔离备份，另保留已核验副本。QQ 登录目录不在清理范围，人物卡、现有配置与学习库的 11 个基线文件校验无变化。恢复脚本在本地交付说明中给出，未执行恢复；不把私人清单、日志或配置提交源码。

系统重启、真实多账号、真实扫码失败、真实强制断网/崩溃、跨版本自动升级及 Linux QQ 原生组件未验证。它们的模拟覆盖与上述真实样本分开；没有执行的项目不能记为真实通过。Linux Docker 保持原固定版本。本轮真实测试没有向群聊或私聊发消息。
