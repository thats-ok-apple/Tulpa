# 无桌面 Linux 的 SnowLuma Docker 托管

目标：在「SL 托管」中完成 Docker 检测、固定版本镜像启动、浏览器扫码、账号验证、OneBot 自动连接及恢复。已有本地目录托管与已有连接导入继续可用。

## 实施顺序

1. 后端：增加 Docker 运行适配器及独立的容器状态流程。只操作具有本 Tulpa 实例归属标签的容器和三个持久卷；端口绑定宿主机回环地址；保留登录数据。Docker 服务安装与应用生命周期分开。
2. 登录：通过 noVNC 展示 QQ 窗口。登录入口单独获取密码，常规状态和日志不含凭据。第一次使用仍由用户阅读并确认 SnowLuma 协议。
3. 接入：通过容器管理 API 核对唯一账号，生成受保护的 HTTP/WS 凭据，验证真实事件身份后发布连接。重启恢复原容器和账号，账号变化时暂停工具。
4. 前端（Claude Code）：扩展已有「SL 托管」页面，提供 Docker 检测、协议确认、扫码入口、日志、停止和重试。Codex 负责后端与验收。
5. 集中验证：容器归属、取消竞态、持久化、重启、协议变化、账号变化、事件重连及凭据隐藏；再运行原托管/API/TUI 回归，并检查本机 Docker 可运行条件。

## 前端接口约定

基础路径：`/api/desktop/snowluma/managed`。已有本地接口不变。

- `GET` 状态新增 `backend: local|docker`、`supported_backends`。Docker 模式增加 `container_name`、`container_id`、`image`、`platform`、`novnc_url`、`webui_url`；状态不含密码。阶段新增 `pulling`、`waiting_login` 保持轮询；就绪也应继续轮询，以显示断线。
- `POST /docker-inspect {}`：只检测 Docker 引擎和当前绑定。成功返回 `ok, mode: docker, image, platform, version, message, selection_id, consent_fingerprint, agreements, scope`；缺少 Docker 或权限不足通过 `ok:false, code, message` 返回。
- `POST /docker-start {accepted:true, fingerprint, selection_id}`：开始后台拉取/启动，立即返回状态。确认界面必须显示该次 preview 的协议及 scope，checkbox 默认未勾选。
- `POST /docker-login {}`：仅主动打开登录弹窗时读取 `novnc_url, webui_url, password`。密码只在弹窗展示，关闭即移除；不能写入状态卡、通知或日志。提供本地地址及 SSH 端口转发说明，URL 不附密码。
- `POST /docker-logs {}`：返回 `lines`（字符串列表，已脱敏）、`container_name`。只显示最近日志，不写文件。
- `POST /retry {}`、`/cancel {}`、`/revoke {}`：按已绑定 backend 分派。停止 Docker 会停止容器中的 QQ 与 SL，保留三个数据卷和授权；撤销还清除协议授权，但仍保留数据卷。不得使用本地模式“不会关闭 QQ”的文案。
- `POST /select {choice_id}` 仅供原本地托管模式。Docker 只绑定容器内单一账号，多账号返回明确错误。

Docker scope：`pull_official_pinned_image`、`manage_owned_container`、`persist_qq_data`、`loopback_admin_http_ws_novnc`、`manage_random_credentials`、`bind_one_verified_qq_account`、`resume_same_verified_account`。

## 验收边界

自动测试使用隔离数据和模拟 QQ 账号，不替用户扫码、不发送真实 QQ 消息。宿主内核若禁止嵌套容器，必须明确报告；该限制不能用模拟测试掩盖。实际 QQ 登录完成后才能标记真实账号联调通过。
