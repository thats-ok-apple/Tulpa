# SL 扩展工具与实时私聊

本版对照 SnowLuma 1.14.22 接入扩展接口。工具仍由 Tulpa 负责授权、账号绑定、
会话与人格、发送队列和操作回执；不需要给 Agent 再配置一个 SL MCP。
接口列表与动作参数以当前连接的 `tools/list` 为准，`get_qq_capabilities` 可列出权限和动作。
接口接入和隔离测试通过，不代表每项操作已在真实 QQ 账号上执行；腾讯侧权限、媒体格式、
时效及服务可用性仍由 SL / QQ 返回结果。

## 授权与 CLI

旧连接不会自动获得新增的独立权限。已有连接可在终端「MCP 授权」中选中后按 `e` 修改权限；
首次连接则使用「新建」、CLI 或本机管理 API；
配置里不要使用聊天消息提供的群号或账号，先核对实际目录。

```sh
./tulpa connections permissions
./tulpa groups
./tulpa friends
./tulpa connections create --name media-agent \
  --conversation ACCOUNT:group:GROUP --conversation ACCOUNT:direct:FRIEND \
  --chat --send --chat-images --chat-sticker-send \
  --chat-voice --chat-video --chat-files --qq-messages --qq-interactions \
  --output ./media-agent-mcp.json
```

`--group` 是 `--conversation` 的兼容名称。`--all-conversations` 会明确授权当前账号全部会话，
与逐个选择互斥。`--output` 只新建权限 600 的文件，配置含一次性展示的 Token。
附件权限依赖 `--chat --send`；历史语音转写的 `--voice` 与实时语音 `--chat-voice` 分开。

| CLI 权限 | 范围与能力 |
| --- | --- |
| `--chat-voice` | 授权会话的语音读取、QQ 转写、发送及群 AI 语音 |
| `--chat-video` | 授权会话的视频读取、发送 |
| `--chat-files` | 授权会话的文件读取、发送，以及授权群的文件/文件夹管理 |
| `--qq-messages` | 授权会话的消息/历史读取、合并转发读取、撤回、转发、分享卡片、已读 |
| `--qq-interactions` | 授权会话的戳一戳、资料点赞、群签到 |
| `--manage` | 既有群管理权限；新增全员禁言、管理员、名片、头衔、头像、公告、精华与群设置 |
| `--qq-albums` | 授权群的相册目录、媒体列表、图片/视频上传、评论、点赞、删除 |
| `--qq-friends` | **账号级**好友目录、实时好友申请处理、备注、分组、删除 |
| `--qq-profile` | **账号级**个人资料、头像、签名、在线状态 |
| `--qq-qzone` | **账号级**空间读取、发表、删除、可见范围、点赞、评论 |

账号级权限不局限于所选群，需分别明确开启。终端授权表单同时提供群和好友选择，
新增权限位于「SL 扩展权限」中，账号级选项另有范围提示。语音、视频和文件开关
需先勾选发送与持续聊天。所有新增开关默认关闭；表单可滚动，创建和取消按钮固定显示。
升级后按 `q` 退出旧终端界面，再运行 `./tulpa`；`r` 只刷新数据，不会加载新界面代码。
若连接的是旧后台，表单会提示新接口不可用，并继续提供原有群与权限选择。

### 修改已有连接

选中连接按 `e`，调整预填的权限开关，按 `Ctrl+S` 保存，`Esc` 取消。
保存保留原 Token、连接编号、账号、群/好友范围及日期，无需重新填写 Agent 的连接配置。
也可只修改指定的开关：

```sh
./tulpa connections edit CONNECTION_ID --send --chat --chat-voice
./tulpa connections edit CONNECTION_ID --no-qq-qzone
```

未指定的权限保持原值，`--no-` 前缀表示关闭。开启附件权限仍需发送和持续聊天权限；
CLI 关闭前置权限时需同时关闭其依赖项，TUI 会联动取消勾选。
新开启实时 QQ 权限时会核对 SL 账号；仅关闭权限可在 SL 离线时保存。
已经撤销的连接不能恢复，权限编辑也不会重置原有的数据源/账号绑定。

实际权限变化会停止该连接的持续会话、取消旧回复计划和处理中调用；其他连接不受影响。
需要聊天时重新开始会话。已交给 QQ 的操作仍以原回执为准，不能当作未执行后重发。
后台随即按新权限校验；客户端若缓存了工具列表，刷新或重连 MCP 即可，原凭证不变。
无改动保存不会停止会话。若其他窗口已修改权限，保存会提示版本冲突，刷新列表后重新编辑。
修改群/好友范围或日期仍需新建连接。

## 私聊

`list_chat_friends` 只列出本连接授权的好友。把返回的 `ACCOUNT:direct:FRIEND` 传入原有
`start_chat_session`，再使用同样的 get/wait → plan → send 流程与人格。
支持私聊文本、原生引用、图片、语音、视频、文件；不发送临时会话。
原生群成员 @、消息表情回应、QQ AI 语音及群管理只用于群。

私聊自身消息按 `target_id` 定位对方，不能把自己的 `user_id` 当成会话对象。
学习库按账号和完整会话隔离；同一连接可以在多个已授权会话学习，不跨会话复用学习内容。
不同连接访问同账号同会话时共享其本地学习库。外部 Agent 的对话上下文仍由宿主管理。

## 附件读取与发送

已授权的实时语音、视频和文件段带有 `attachment_index`。调用
`read_chat_attachment(session_id, event_id, index)` 获取本机文件、SHA256、原始文件名和 `asset_id`。
`event_id` 是本会话已读取的事件编号。语音还会返回实际音频，以及可用时的 QQ 机器转写；
`transcription_state=unavailable` 表示没有转写，不能据此猜内容。客户端不支持音频时可使用
返回的本机文件；远程客户端不能直接访问该路径。不会调用 Tulpa 的第二个回答模型。

外部文件先由客户端上传，最多 32 MiB；图片仍是 6 MiB：

```sh
.venv/bin/python scripts/import_qq_media.py \
  --config ./media-agent-mcp.json --kind voice ./voice.wav
```

`--kind` 可选 `voice`、`video`、`file`、`image`。语音支持 WAV/MP3/OGG/FLAC/AMR/SILK/M4A，
视频支持 MP4/WebM。Tulpa 校验大小、文件头和图片像素；能否编码发送仍以 SL 的回执为准。
脚本只读本机普通文件并调用 `import_qq_media`，不自动开启聊天或发送；服务端不接受客户端路径或 URL。
通用 `image` 资产用于头像、公告、相册和空间；聊天图片继续使用原有 `import_chat_image` 的预览流程。

取得 `asset_id` 后，读取最新批次，规划对应表达，然后提交气泡：

```json
{"kind":"voice","asset_id":"导入返回的32位编号"}
```

视频用 `kind="video"`，文件用 `kind="file"`；群文件可选 `folder_id`，默认根目录。
文件发送调用 SL 的群/私聊文件上传接口，并在回执中返回 `file_id`；群文件成功不保证有普通消息编号。
先 `list_qq_voice_characters` 查角色，再用 `expressions=["ai_voice"]` 和
`{"kind":"ai_voice","character_id":"角色编号","text":"正文"}` 发送 QQ AI 语音。

导入资产绑定连接和账号，收到的资产还绑定来源会话；跨连接不能借用。
缓存最多 256 MiB，旧原件会被淘汰，缺失时重新导入/读取，不替换成其他文件。
每连接每小时最多导入 30 次、读取实时附件 60 次；请求及下载都有字节和超时限制。

## 消息、互动、空间与管理

扩展工具为 `qq_message_action`、`qq_interact`、`qq_group_action`、`qq_file_action`、
`qq_friend_action`、`qq_profile_action`、`qq_qzone_action`、`qq_album_action`。
各工具使用 `action` 和严格的 `params`，会话工具还需要 `conversation_id`。
读取结果的顶层列表支持 `offset` / `limit`（默认50、最多100），按 `pagination` 的
`has_more` / `next_offset` 续读并保持原 `params`；SL 服务端分页游标也需要独立沿用。
目录是动态查询，不能把一次返回当成完整快照；嵌套内容有单独大小限制。
写操作必须提供稳定的 `idempotency_key`。原生动作名、必填字段和范围由 MCP schema 提供，
不提供任意 API、Cookie、SSO 包、文件路径或 URL 透传接口。

例如，在没有活跃聊天会话的已授权群戳指定成员：

```json
{
  "conversation_id":"ACCOUNT:group:GROUP",
  "action":"send_poke",
  "params":{"user_id":"MEMBER"},
  "idempotency_key":"user-request-poke-001"
}
```

活跃聊天中的戳戳、点赞、签到、转发和分享卡片必须走发送队列：规划 `expressions=["qq_action"]`，
然后提交 `{"kind":"qq_action","tool":"qq_interact","action":"send_poke","params":{"user_id":"MEMBER"}}`。
队列目标取自当前会话，不允许另填群号。撤回和管理操作仍通过相应工具执行。

消息操作使用已核对会话内的 OneBot `message_id`，支持负数编号。转发还须明确授权的
`source_conversation_id`；合并转发只引用真实消息，不伪造作者。
`history` 使用 `message_id` 锚点与 `reverse_order`，页面可能重复锚点，按消息编号去重；
它不导入本地历史，也不承诺覆盖所有离线消息。

空间发表必须明确 `ugc_right`：1 所有人、4 好友、16 部分好友、64 仅自己、128 部分好友不可见。
16/128 必须提供 `target_uins`。带图使用导入的 `asset_ids`，不传本机路径。
好友动态目前只读首页；未实现的深翻页和待核实的空间取消赞不包装成可用功能。
普通好友申请来自本服务收到的实时事件；`requests` 返回的 `request_id` 才能用于
`approve_request`，原始协议 flag 不开放给模型。服务停止期间的申请不保证补齐。

所有写入都有 `get_qq_operation` 回执。`SUCCEEDED` 表示收到 SL 成功响应；`UNKNOWN` 表示
结果不确定，不能换编号自动重发。停止会话会阻止尚未派发的聊天气泡；已交给 QQ 的操作
仍可能完成，以回执和 QQ 状态为准。撤销连接会同时撤销扩展权限。

## 验证

```sh
.venv/bin/python scripts/check_mcp_qq_extensions.py
```

该验收使用隔离数据、真实 MCP HTTP / OneBot HTTP / WS 与模拟 QQ 服务，覆盖消息路由、
附件字节、转写、原生参数、权限、回执幂等、UNKNOWN、停止竞争及账户切换；不会向真实 QQ 发消息。
