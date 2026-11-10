# B站只读与 QQ 视频分享

本功能复用 Tulpa **同一 MCP 服务、连接凭据和统一授权页**。代码、媒体任务和账号凭据独立于持续水群，不调用水群人格、记忆、学习或调度器，不额外启动回答模型。

## 开始使用

1. 桌面 MCP 首页点击「开始创建链接」，启用「哔哩哔哩」模块并勾选只读能力。QQ、微信可以分别启用或关闭。
2. 左侧「B站账号设置」打开扫码窗口，通过 B站 App 确认登录。此窗口不创建另一份 MCP。
3. 点击「一键开启 MCP」，将连接配置交给现有 Agent。只用 B站无需选择 QQ 群，也无需导入 QQ / 微信历史。
4. 在 Agent 中重新连接或刷新工具列表。可以说：「搜索 AI 水群相关视频，先看真实内容，再给我候选和依据。」

原生视频分享入口已从创建界面移除，下游跳转未通过验收。Linux 共用工具后端，保留原 CLI/TUI；桌面配置页步骤不代表 Linux 终端界面具有相同布局。

旧连接不会自动获得 B站或分享权限。创建新连接，或使用已有权限编辑入口显式修改；改变平台或群范围须新建连接。撤销连接只影响该连接的工具与未完成任务，不改变其他连接或聊天数据库。

**当前分享限制：** 2026-10-24 未修改的 SnowLuma 2.1.1 能返回原生 Ark、发送成功并给出 message_id，但用户点击测试卡片后报告「小程序已经下架」。其返回的 `com.tencent.miniapp_01` 只有不透明 QQ 短跳转，不能核对目标 BV，亦无 `webUrl`。因此该形态现被阻止发送，返回 `native_card_unverified`；不会降级为裸链接、伪造卡片或自动重试。原生分享两端跳转验收**未通过**，不应作为已支持的可用分享功能宣传。B站只读不受影响。

## 工具协议

精确 JSON Schema 由 `tulpa_bilibili/tools.py:REGISTRY` 定义；MCP `tools/list` 根据当前连接裁剪。只读授权发现 9 个工具，另有分享授权时发现第 10 个。

| 工具 | 主要输入 | 返回与边界 |
| --- | --- | --- |
| `search_videos` | query、order、limit、cursor | BV、标题、作者、时长、简介、URL、下一页；不把元数据当理解 |
| `get_recommended_videos` | limit、cursor、refresh | 登录账号的当前推荐批；批内去重，游标不重复拉全批 |
| `get_video_info` | BV/受支持 URL、page | 标准 BV、分 P/cid、元数据、全部 P 的有界目录 |
| `read_video` | video、page、可选起止秒 | 立即排队返回 job_id；平台字幕优先，缺失才本地 ASR，同时真实抽帧 |
| `get_video_frames` | video、page、时间点或范围 | 排队抽取更多真实画面，复用有界缓存 |
| `get_video_comments` | video、hot/newest、limit、cursor、可选 reply_to | 有界评论/回复页；观众观点不等于视频事实 |
| `get_video_job` | job_id | 当前连接任务状态、证据、图片；跨连接或权限版本拒绝读取 |
| `cancel_video_job` | job_id | 协作取消下载/处理，不影响聊天工具 |
| `get_bilibili_status` | 无 | 登录、当前连接分享范围、媒体限制和原生分享验证状态 |
| `share_video_to_qq_group` | qq_account、group_id、bvid、idempotency_key | 范围校验、原生 Ark 校验、单次发送；当前不透明 QQ 卡片被阻止 |

成功只读返回 `ok:true`。错误返回 `ok:false,error:{code,message,retryable,upstream_status}`。登录缺失、过期、参数不符、URL/目标地址被拒绝、限频、资源限制、取消、权限变化、媒体失败等有独立原因。不得重试 `retryable:false` 的相同请求。工具不存在时由统一 MCP 返回 `tool_unavailable`。

异步状态包含 queued/running/succeeded/failed/cancelled/interrupted。领取任务后按返回的 `poll_after_seconds` 查询，不重复创建。重启取消未完成任务，不假装续跑。任务属于当前 grant + revision；撤权、改权限、停服务或更换 B站凭据后旧任务失效。短只读请求有界，处理耗时不会占住持续水群的等待线程。

字幕证据含 BV、page、cid、源类型、起止秒、获取时间、截断标识。帧含实际解码时间戳并以 MCP ImageContent 返回。机器字幕和本地 ASR 有误识别可能；少量帧不代表看过所有画面。只有画面时返回 frames_only；只有元数据不表示读过视频。宿主无视觉时只能使用字幕/ASR。

## 默认资源限制与配置

配置文件为安装目录 `data/bilibili/settings.json`，更改限制后重启。没有凭据的示例：

```json
{"schema":1,"enabled":true,"generation":0,"limits":{"max_video_seconds":1800,"max_download_bytes":268435456,"cache_bytes":1073741824,"cache_ttl_seconds":86400,"max_frames":8,"initial_frames":4,"max_jobs":2,"job_timeout_seconds":600,"frame_width":960,"asr_backend":"whispercpp","asr_model_path":""}}
```

最长 30 分钟/分 P、合计下载不超过 256 MiB、同时最多 2 个媒体任务、每任务 10 分钟。帧最多 8 张（首批 4 张），缩放至宽 960；每张压缩后最多 512 KiB。字幕最多 600 段/24000 字，截断时要求缩小范围。列表每次最多 20 条，分页缓存 32 批、5 分钟；推荐默认复用 60 秒，不承诺跨刷新唯一。

媒体缓存最大 1 GiB、24 小时 TTL，使用前核验 SHA256；两路媒体合计计入下载限额。临时下载受并发与单任务限制，可能额外占用一个下载上限。结果最多保留 32 个、约 64 MiB、24 小时。清理在任务/缓存访问或启动时执行，不承诺到期瞬间物理擦除；SQLite 空页可能保留文件体积。设置页可取消任务并清理缓存，退出登录会撤销两类 B站凭据并清理缓存。

ASR 默认复用 Tulpa 的本地 Whisper.cpp 和可选的 small-q5_1 模型，主包不额外附带模型。已安装模型可继续使用，首页的组件下载入口已隐藏。也支持已安装的 faster-whisper 和显式本地模型目录，`local_files_only`，不会后台下载模型或调用新 API。无字幕、模型缺失或媒体不可访问时诚实返回缺失项。真实 Windows 无字幕 ASR 尚未进行完整视频验收；时间偏移与失败边界由合成测试覆盖。

## 账号、网络与写操作

B站登录与 QQ 登录各自独立。扫码凭据用于内容 API；媒体默认匿名，不能自动把扫码 Cookie 复制到媒体层。需要受限媒体时，在账号设置另行确认并配置媒体 Cookie。不会扫描浏览器或其他安装的登录信息。

Windows 凭据使用已有 DPAPI 保险箱；上层 settings.json、MCP 参数、结果、操作记录和日志不含 Cookie。Linux 复用现有受限目录与本地密钥方案，不能抵御已控制同一用户账户的进程。本轮新功能未做 Linux 实机验收。

仅固定 B站只读 GET 路由及 QR 端点可访问。URL/重定向只允许官方视频和短链；CDN 白名单、HTTPS、公共地址检查与 DNS 固定用于阻止 SSRF，拒绝回环/私网/危险协议。系统 DNS 若仅返回代理的 198.18/15 假 IP，采用固定 AliDNS 加密公共解析并再次校验，不跳过 TLS。API Cookie 不转发给 CDN。无任何 B站社交写工具、通用 invoke_action 或任意 URL 请求工具。

QQ 侧仅允许读取登录/群列表/版本、`get_mini_app_ark` 与必要的 `send_group_msg`，从同一 OneBot 配置读取凭据。目标需符合当前连接的账号、群和日期；发送前再次校验。10 秒全局卡片冷却，持久记录账号 + 群 + BV + 业务幂等键。已确认送达不会重复；超时、断线或不可靠回执为 uncertain，重启仍不盲重试。发送接口成功不等于客户端展示和跳转成功。

## 验证和回退

激活当前平台的 Tulpa 虚拟环境后运行：

```sh
python scripts/check_bilibili.py
python scripts/check_mcp_history_optional.py
```

这些测试只用合成媒体、假 OneBot 和本地 MCP 服务，不访问真实 QQ/B站。包含统一授权、空历史、任务隔离与撤权、SSRF、分页、分 P、缓存、限额/取消、ASR 时间、分享去重/UNKNOWN、已知坏卡片拒绝、旧工具兼容。打包检查使用 `--package <Tulpa目录>`。

Windows 真实验收：经用户批准扫码；从「AI 水群」搜索挑选视频，读取推荐、评论分页、57 段平台字幕、4 个真实画面和 2 个缓存精确帧；统一只读连接 9 工具通过。另经明确确认仅发送 1 张指定视频原生卡片，接口成功，但用户报告点击提示小程序下架，原生分享未通过。临时分享连接随后撤销。未修改 SL、未发送其他消息、未进行 B站社交操作。原始证据和凭据只保存在本机测试目录，不进仓库。

恢复旧程序时先退出 Tulpa，从本机升级备份恢复程序；不要覆盖测试后新生成的数据。既有人格、学习库、账号及连接不迁移或清空。B站数据单独存储，旧程序忽略它；不需重新导入聊天。测试账号凭据不会自动复制给新安装，正式使用需在该安装里扫码。

内容组件的版本、来源及许可见 [B站组件来源](../tulpa_bilibili/_vendor/NOTICE.md)。
