# SnowLuma Docker 托管验收

日期：2026-10-14。命令均在当前 Tulpa 项目目录执行。
环境：Arch Linux ARM64 / Droidspaces，Android 6.12 内核；Python 3.13.16。

## 实现

- 后端：本机 Docker 检测、官方镜像下载和摘要检查、容器归属与三个持久卷、
  启停与恢复、协议确认、noVNC 登录入口、OneBot 自动配置、原账号验证和事件重连。
- 前端：实际 Claude Code 修改「SL 托管」，顶部提供「启用/停止 Docker 托管」、
  「扫码登录」「查看日志」按钮；保留 `d` / `b` / `l` 等快捷键。
  侧栏「SL 托管」紧邻「SL 连接」，后者顶部也提供「SL Docker 托管」直达按钮。
  停止/撤销文案区分本地和 Docker，保留原流程。
- Docker 操作没有作为 MCP 工具提供；管理路由沿用本机认证与同源检查。
- 官方镜像 `motricseven7/snowluma:v1.14.22`，固定 ARM64/AMD64 摘要；
  创建后、启动前核对镜像内实际包版本和协议 SHA256。

## 自动检查

- `check_snowluma_docker.py`：27 项隔离检查通过。覆盖授权与引擎变化、登录等待、
  密文与状态脱敏、停止/恢复、撤销、后端恢复、账号变化、多账号、事件身份、
  容器重启与管理会话过期、协议变化、取消竞态、凭据丢失、容器归属、API 参数与同源检查；
  切换到外部连接后主动重试，能恢复托管连接与原容器。
  新增扫码前唤醒桌面、停止/重试后重新禁用屏保、等待扫码期间容器重启后恢复，
  以及桌面操作的容器归属和取消检查；状态和登录入口均返回带 `resize=scale` 的 URL。
- `check_snowluma_docker_tui.py`：80×24、100×32 两种终端尺寸通过。
  实际键盘触发检测、未勾选拒绝启动、登录弹窗关闭后清除密码、日志、停止、重试与撤销；
  等待和就绪阶段都继续轮询。新增鼠标点击及 Tab/Enter 验证：从 SL 连接进入托管页、
  顶部按钮在屏幕内、启停/扫码/日志可用、取消同意不启动、有效授权恢复无需再次同意、
  撤销后重新展示未勾选的同意框；状态刷新不会自动启停，未就绪和本地已绑定时按钮正确禁用。
  登录弹窗过滤 URL 中的敏感参数，同时保留 `resize=scale`。
- 修复顶部按钮在请求期间禁用导致焦点丢失的问题，操作失败后仍能用原快捷键重试。
- 原本地托管、传输、已有连接导入、终端后端、原 TUI 与 HTTP/WS 集成检查通过。

## 本机实测

- 安装 Docker Engine 29.9.0、RootlessKit 3.2.0、slirp4netns、fuse-overlayfs。
- 使用用户级 Docker，socket 为 `/run/user/1000/docker.sock`，用户服务已启用。
  Docker CLI context 为 `tulpa-rootless`；Tulpa 仍显式选择本机 socket，不依赖 context。
- 官方镜像已下载，实际运行包版本及实际管理 API 返回的协议哈希核对通过。
- 真实 QQ、SnowLuma 进程进入 RUNNING；noVNC 与管理页面返回 HTTP 200。
- noVNC WebSocket/RFB 密码认证成功，读取 1920×1080 帧缓冲，目视确认 QQ 扫码登录窗口。
- 容器 DNS、TLS、腾讯站点 HTTP 访问通过。
- 停止再启动容器、重启用户级 Docker 服务后，容器自动恢复，端口、原密码和三个
  数据卷保持一致；持久目录中的测试标记仍在，管理登录和 noVNC 仍可用。
- Tulpa 与用户级 Docker 服务均为 active/enabled；实际 TUI 连接正式后端，按 `d`
  成功检测 ARM64 镜像并展示默认未勾选的协议确认框，退出后未启动正式托管。

测试使用临时容器和隔离持久卷，没有登录真实账号，没有接受测试实例的上游协议，
没有发送 QQ 消息。测试镜像保留供正式托管复用；测试容器、六个测试命名卷、
临时凭据和扫码截图已清理。
不能把扫码窗口可用等同于已验证真实账号的长期收发和登录稳定性。

## 环境修复

Android 共享的策略路由和 `tetherctrl_FORWARD` 会阻止普通 Docker 桥接出网；
最终使用 rootless 用户态网络。排查时增加的 Docker 网段路由和 NAT 规则已经撤销，
普通系统级 Docker 已停止，其测试网桥和 nftables 表已清理，用户级 Docker 负责实际运行。

本机镜像原有 `newuidmap/newgidmap` 缺失 file capabilities，已恢复它们各自所需的
`cap_setuid=ep` / `cap_setgid=ep`。原有 subuid/subgid 配置保持使用。
Rootless 启动脚本取自 Moby v28.3.3 的官方 `contrib/dockerd-rootless.sh`，
仅安装到本机 `~/.local/bin`；实际 Docker Engine 使用系统包 29.9.0。

托管兼容性修复：数据卷使用 `volume-nocopy`，避免 rootless Docker 复制镜像目录的
SELinux 属性失败；文件句柄上限设为 65536，避免超出用户服务继承的硬限制。

### VNC 屏保导致黑屏

实际用户实例复现：VNC 密码认证和 QQ 进程正常，但读取 RFB 桌面帧为全黑。
执行 `xset s reset` 后恢复 QQ 扫码窗口，确认是 Xvfb 屏保空白状态。
托管现在在虚拟桌面就绪后执行 `xset s off`、`xset s noblank`、`xset s reset`，
并在操作者打开扫码入口时重新执行。所有操作限于通过归属验证的托管容器。
等待扫码期间也检测容器启动时间变化，重新初始化管理会话和桌面后再继续等待。
修复后四组相关回归检查通过；验收日志为 `.tmp/vnc-fix-regression.log` 和
`.tmp/vnc-restart-verification.log`。

### 手机端裁剪导致只看到黑色背景

关闭屏保后用户仍报告黑屏。读取完整 RFB 帧确认 QQ 窗口存在，而使用实际 Chromium
以 Android 触屏、390×844 视口复现后，发现 noVNC 默认不缩放，并在手机上裁剪画布。
画布只有 390×844，只显示 1920×1080 桌面的左上角；居中的 QQ 窗口完全在屏幕外。

同一桌面改用 `?resize=scale` 后，画布恢复 1920×1080，缩放至约 390×219，
`scaleViewport=true`、`clipViewport=false`，浏览器截图可见 QQ 登录窗口，无脚本异常。
后端统一为状态和扫码接口生成带此参数的链接，终端弹窗保留它；已有无参数页面需打开
新链接才能应用。对比日志为 `.tmp/vnc-mobile-browser-check.log`。
重启正式后端后，状态与扫码接口均返回带参数的新链接，原容器、端口和凭据保持一致。
再次直接打开后端返回的链接，通过同一手机视口的浏览器验证，并目视确认 QQ 二维码窗口。
日志为 `.tmp/vnc-mobile-service-verification.log`、`.tmp/vnc-mobile-actual-check.log`；
Docker 后端 27 项检查及两种终端尺寸的界面回归通过。
测试只连接虚拟桌面并读取画面，没有操作 QQ 登录或发送消息。

### 空工作区、旧浏览器设置与连接占用

后续再次出现黑屏时，屏保仍为关闭状态。RFB 原始画面和 X11 根窗口属性确认桌面
位于第 2 个空工作区，QQ 窗口仍在第 1 个。Fluxbox 默认将桌面滚轮事件绑定为切换
工作区，手机滑动可能触发它。另观察到客户端要求远程改变分辨率，但固定 Xvfb
拒绝该请求；x11vnc 默认的独占连接也会拒绝仍有旧标签页连接时的新客户端。

托管的桌面准备步骤现写入单工作区配置，通过 X11 窗口管理器请求切回第 1 个工作区，
无需重启 QQ。noVNC 使用官方支持的 `mandatory.json` 将 `resize` 固定为 `scale`，
覆盖无参数旧书签、已保存的远程缩放设置以及显式 `resize=off/remote` 链接。
VNC 保留密码认证，并启用共享连接、请求重绘；容器启动与打开扫码入口均应用这些设置。
已有浏览器页面需要刷新才能加载新的缩放配置。

本机已在原容器应用配置，QQ 进程未重启。真实 Chromium 手机视口（390×844）分别
验证当前链接、无参数旧书签/已保存设置、`off` 和 `remote` 四种入口，均得到完整
1920×1080 画布、启用缩放、关闭裁剪且无脚本异常，QQ 扫码窗口可见。
桌面滚动后仍只有一个工作区。Docker 后端 28 项检查与两种终端尺寸回归通过。
私有验收输出为 `.tmp/vnc-mobile-regression.log`、`.tmp/vnc-docker-regression.log`
和 `.tmp/vnc-tui-regression.log`，不包含在源码包中。

## 操作

更新界面后按 `q` 退出，再运行 `./tulpa`。侧栏第三项为「SL 托管」，也可从
「SL 连接」顶部点击「SL Docker 托管」。点击「启用 Docker 托管」检测和确认协议，
待初始化完成后点击「扫码登录」。首次正式协议确认及真实账号扫码由操作者完成。

计划与接口：[DOCKER_MANAGED_PLAN.md](DOCKER_MANAGED_PLAN.md)。
主要本机日志：`.tmp/check-snowluma-docker.log`、`.tmp/check-snowluma-docker-tui.log`、
`.tmp/docker-final-regression.log`、`.tmp/docker-restart-verification.log`、
`.tmp/docker-live-tui-verification.log`、`.tmp/docker-visible-controls-check.log`、
`.tmp/terminal-visible-controls-regression.log`、`.tmp/docker-visible-live-ui.log`。
日志、截图、凭据和容器均不随源码包分发。
