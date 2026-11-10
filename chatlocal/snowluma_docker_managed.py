"""Headless Docker workflow, sharing Tulpa's consent, vault and account guards."""
import re
import secrets
import time

from .snowluma_docker import DockerRuntime, DockerSnowLumaAPI, docker_manifest, release
from .snowluma_local import fingerprint
from .snowluma_runtime import free_port
from .snowluma_secure import ManagedError, file_lock, private_directory


class DockerManaged:
    def __init__(self, manager, *, runtime=None, api_factory=DockerSnowLumaAPI):
        self.m = manager
        self.runtime = runtime or DockerRuntime(manager.root)
        self.api_factory = api_factory

    @property
    def manifest(self):
        return docker_manifest(self.m.manifest)

    def inspect(self):
        from .snowluma_managed import consent_key
        state = self.m.state()
        if state.get('installation') and state.get('backend', 'local') != 'docker':
            raise ManagedError('backend_bound', '此 Tulpa 已绑定本地 SnowLuma；请保留该绑定，使用独立 Tulpa 实例托管 Docker。')
        receipt = dict(self.runtime.environment(self.manifest), mode='docker')
        receipt['fingerprint'] = fingerprint(receipt)
        receipt['message'] = '启动官方固定版本容器，通过浏览器扫码登录 QQ；停止容器时保留登录数据。'
        agreed = fingerprint([consent_key(self.manifest), receipt['fingerprint']])
        key = secrets.token_urlsafe(24)
        with self.m.mutex:
            now = time.monotonic()
            self.m.previews = {k:v for k,v in self.m.previews.items() if now-v[0]<600}
            if len(self.m.previews) >= 8: self.m.previews.pop(next(iter(self.m.previews)))
            self.m.previews[key] = (now, receipt, agreed)
        return dict(receipt, selection_id=key, consent_fingerprint=agreed,
                    agreements=self.manifest['agreements'], scope=self.manifest['scope'])

    def begin(self, body):
        from .snowluma_managed import consent_key
        if self.m.thread and self.m.thread.is_alive() and not self.m.state().get('enabled'):
            raise ManagedError('stopping', '上一轮容器托管正在停止，请稍候再启动。', retryable=True)
        if set(body) != {'accepted','fingerprint','selection_id'} or body.get('accepted') is not True:
            raise ManagedError('consent_required', '请先检测 Docker，并主动同意协议与容器托管授权。')
        with self.m.mutex:
            preview = self.m.previews.get(body['selection_id']) if isinstance(body['selection_id'],str) else None
        if not preview or time.monotonic()-preview[0]>=600:
            raise ManagedError('selection_expired', 'Docker 检测已过期，请重新检测。')
        _, receipt, agreed = preview
        if receipt.get('mode') != 'docker' or body['fingerprint'] != agreed:
            raise ManagedError('consent_required', '镜像或协议已变化，请重新检测并同意。')
        current = self.runtime.environment(self.manifest)
        if any(current.get(k) != receipt.get(k) for k in ('engine_id','image_ref','platform','version')):
            raise ManagedError('docker_environment_changed', 'Docker 引擎或目标版本已变化，请重新检测。')
        private_directory(self.m.directory)
        with file_lock(self.m.directory/'state.lock'):
            state = self.m.state()
            if state.get('installation') and state.get('backend', 'local') != 'docker':
                raise ManagedError('backend_bound', '已有本地托管绑定，未覆盖。')
            if state.get('installation') and any(state['installation'].get(k) != receipt.get(k) for k in ('engine_id','image_ref','platform')):
                raise ManagedError('installation_bound', '已有容器绑定的引擎或镜像不同，未替换登录数据。')
            duplicate = bool(state.get('enabled') and self.m._consented(state))
            if not duplicate:
                state.update(schema=1, mode='managed', backend='docker', instance=state.get('instance') or secrets.token_hex(16),
                    generation=state.get('generation',0)+1, enabled=True, phase='detecting', code='', message='',
                    installation=receipt, version=self.manifest['version'], retryable=False, choices=[], selection_required=False,
                    consent=dict(fingerprint=consent_key(self.manifest), source=receipt['fingerprint'],
                                 at=time.time(), scope=self.manifest['scope']))
                self.m._write(state)
        if not duplicate or state.get('phase') not in ('error','cancelled','revoked'): self.m.launch()
        return dict(self.m.status(), already_started=duplicate)

    def public(self, state):
        installation = state.get('installation', {})
        ports = state.get('ports', {})
        owner = state.get('owner') or {}
        return dict(container_name=self.runtime.name(state['instance']), container_id=owner.get('id',''),
                    image=installation.get('image',''), image_digest=installation.get('digest',''),
                    platform=installation.get('platform',''),
                    # noVNC defaults to clipping on mobile: a centered QQ window can be entirely off screen.
                    novnc_url=f'http://127.0.0.1:{ports["novnc"]}/?resize=scale' if ports.get('novnc') else '',
                    webui_url=f'http://127.0.0.1:{ports["admin"]}/' if ports.get('admin') else '',
                    login_available=bool(state.get('enabled') and owner and ports and state.get('initialized')),
                    security_note='容器端口仅开放给本机；QQ 登录数据保存在独立持久卷中。停止或撤销托管会停止容器，保留数据。')

    def login(self):
        state = self.m.state()
        if state.get('backend') != 'docker' or not state.get('owner'):
            raise ManagedError('docker_not_started', '请先启动 Docker 托管。')
        self.m._check(state['generation'])
        if not state.get('initialized'):
            raise ManagedError('docker_not_ready', '容器正在初始化，请稍候再打开扫码入口。', retryable=True)
        self.runtime.prepare_display(state['owner'], state['installation'], state['instance'], state['ports'],
                                     lambda: self.m._check(state['generation']))
        credentials = self.m.vault.read()
        if not credentials.get('vnc_password'):
            raise ManagedError('credential_unavailable', '扫码凭据缺失，请恢复本机托管凭据。')
        public = self.public(state)
        return dict(novnc_url=public['novnc_url'], webui_url=public['webui_url'], password=credentials['vnc_password'],
                    novnc_port=state['ports']['novnc'],
                    ssh_hint=f'ssh -N -L {state["ports"]["novnc"]}:127.0.0.1:{state["ports"]["novnc"]} 用户名@服务器地址')

    def logs(self):
        state = self.m.state()
        if state.get('backend') != 'docker' or not state.get('owner'):
            raise ManagedError('docker_not_started', '还没有可读取日志的托管容器。')
        return dict(container_name=self.runtime.name(state['instance']),
                    lines=self.runtime.logs(state['owner'], state['installation'], state['instance'], state['ports'], self.m.vault.read()))

    def work(self, generation):
        m = self.m
        check = lambda: m._check(generation)
        state = check()
        selection, instance = state['installation'], state['instance']
        current = self.runtime.environment(self.manifest)
        if any(current.get(k) != selection.get(k) for k in ('engine_id','image_ref','platform','version')):
            raise ManagedError('docker_environment_changed', '已授权 Docker 引擎或镜像发生变化，未继续。')
        credentials = m.vault.read()
        if not credentials:
            if state.get('owner') or state.get('initialized'):
                raise ManagedError('credential_unavailable', '容器凭据缺失，未重置密码。')
            credentials = dict(admin_password=secrets.token_urlsafe(36), http_token=secrets.token_urlsafe(32),
                               ws_token=secrets.token_urlsafe(32), vnc_password=secrets.token_urlsafe(12)[:8])
            check(); m.vault.write(credentials)
        if not all(credentials.get(k) for k in ('admin_password','http_token','ws_token','vnc_password')):
            raise ManagedError('credential_unavailable', '容器托管凭据不完整。')
        owner = state.get('owner')
        ports = state.get('ports')
        image_id = state.get('image_id')
        receiver = None
        try:
            if not owner:
                m._set(generation, 'pulling', message='正在准备官方 SnowLuma 镜像，首次下载可能需要几分钟。')
                image_id = self.runtime.ensure_image(selection, check)
                if not ports:
                    ports = {key:free_port() for key in ('admin','http','ws','novnc')}
                    while len(set(ports.values())) != 4:
                        ports = {key:free_port() for key in ('admin','http','ws','novnc')}
                m._set(generation, 'starting', ports=ports, image_id=image_id)
                owner = self.runtime.recover_created(selection, instance, ports, image_id)
                if owner is None:
                    owner = self.runtime.create(selection, instance, image_id, ports, credentials, check)
                m._set(generation, owner=owner)
            self.runtime.volumes(instance)
            self.runtime.validate(owner, selection, instance, ports)
            self.runtime.verify_package(owner, self.manifest)
            self.runtime.verify_credentials(owner, credentials, required=bool(state.get('initialized') or state.get('account')))
            if not state.get('initialized'): self.runtime.seed_config(owner, check)
            failures = 0
            while True:
                check()
                try:
                    self.runtime.start(owner, selection, instance, ports, check)
                    m._set(generation, 'initializing', message='正在启动 QQ 和扫码窗口。', code='')
                    m.api = self.api_factory(ports['admin'])
                    deadline = time.monotonic()+90
                    while True:
                        check()
                        try:
                            m.api.initialize(credentials['admin_password'], self.manifest, check)
                            break
                        except ManagedError as exc:
                            if exc.code != 'admin_unreachable' or time.monotonic() >= deadline: raise
                            m._pause(generation, min(m.poll_seconds,2))
                    row=self.runtime.validate(owner, selection, instance, ports, running=True)
                    self.runtime.prepare_display(owner, selection, instance, ports, check)
                    m._set(generation, initialized=True, container_started_at=row.get('State',{}).get('StartedAt'))
                    m._set(generation, 'waiting_login', message='请打开扫码入口，在浏览器中的 QQ 窗口扫码；登录后会自动连接。')
                    while True:
                        check()
                        login_row=self.runtime.validate(owner, selection, instance, ports, running=True)
                        if login_row.get('State',{}).get('StartedAt') != row.get('State',{}).get('StartedAt'):
                            raise ManagedError('docker_restarted', '容器已重启，正在恢复扫码窗口。', retryable=True)
                        account = self.account(m.api, check().get('account'))
                        if account: break
                        m._pause(generation, m.poll_seconds)
                    m.check_account(account)
                    m._set(generation, 'configuring', account=account, message='正在配置并核对这个 QQ 账号。')
                    check(); m.api.configure(account, ports, credentials)
                    http = dict(url=f'http://127.0.0.1:{ports["http"]}',token=credentials['http_token'])
                    ws = dict(url=f'ws://127.0.0.1:{ports["ws"]}',token=credentials['ws_token'])
                    m._set(generation, 'verifying', message='正在验证账号与实时事件通道。')
                    if m.probe: receiver=m.probe(http, ws, account, check)
                    else:
                        from .onebot import Client
                        from .onebot_events import EventReceiver
                        receiver=EventReceiver(lambda event:None, lambda phase:None, config_factory=lambda:dict(ws),
                                               client_factory=lambda **kw:Client(http,**kw), metadata_only=True)
                        receiver.start()
                    m.heartbeat=receiver
                    deadline=time.monotonic()+m.event_timeout
                    while receiver.status().get('state') != 'connected':
                        check()
                        if receiver.status().get('state') in ('account_mismatch','auth_error','handshake_error'):
                            raise ManagedError('event_identity_failed', '实时事件账号或认证不匹配，未启用 QQ 工具。')
                        if time.monotonic() >= deadline:
                            raise ManagedError('event_unverified', '还没有收到同账号的生命周期或心跳事件。', retryable=True)
                        m._pause(generation, .1)
                    if str(receiver.status().get('account','')) != account:
                        raise ManagedError('event_identity_failed', '实时事件返回了其他账号，未启用 QQ 工具。')
                    self.verify(check())
                    m._set(generation, 'ready', verified_at=time.time(), message='QQ 已登录，容器身份、账号和实时事件验证通过。', code='')
                    failures=0
                    disconnected=0
                    while True:
                        m._pause(generation, m.poll_seconds)
                        self.verify(check())
                        status=receiver.status()
                        if status.get('state')=='connected' and str(status.get('account',''))==account:
                            disconnected=0
                            m._set(generation, 'ready', verified_at=time.time(), message='QQ 托管连接正常。', code='')
                        else:
                            if status.get('state') in ('account_mismatch','auth_error','handshake_error') or (status.get('account') and str(status['account'])!=account):
                                raise ManagedError('event_identity_failed', '事件账号或认证已变化，托管已暂停。')
                            if not disconnected:
                                disconnected=time.monotonic()
                                m._set(generation, 'reconnecting', verified_at=0, message='事件连接中断，正在恢复原账号。')
                                m.on_invalidated()
                            if time.monotonic()-disconnected>45:
                                raise ManagedError('event_disconnected', '实时事件连接中断，正在重新核对连接。', retryable=True)
                except ManagedError as exc:
                    recoverable={'admin_unreachable','admin_session_expired','docker_stopped','docker_restarted',
                                 'qq_offline','event_disconnected','event_unverified','config_not_applied'}
                    if exc.code not in recoverable: raise
                    failures+=1
                    m._set(generation, 'reconnecting', verified_at=0, code=exc.code,
                           message=str(exc)+' 将自动重试，可随时停止托管。')
                    m.on_invalidated()
                    m._pause(generation, min(30, max(m.poll_seconds,failures*2)))
                finally:
                    if receiver: receiver.stop()
                    receiver=None; m.heartbeat=None
        finally:
            if receiver: receiver.stop()
            m.heartbeat=None
            # Even a create/start canceled after the daemon completed must be
            # recovered by exact labels and stopped. Volumes are never removed.
            if not owner and ports and image_id:
                owner=self.runtime.recover_created(selection, instance, ports, image_id)
                if owner:
                    with file_lock(m.directory/'state.lock'):
                        current=m.state()
                        if current.get('instance')==instance:
                            current['owner']=owner; m._write(current)
            if owner:
                self.runtime.stop(owner, selection, instance, ports)
            current=m.state()
            if current.get('generation')==generation and m.halt.is_set() and current.get('enabled'):
                m._set(generation, 'stopped', verified_at=0)

    def account(self, api, expected):
        accounts=api.accounts()
        if not accounts: return ''
        if len(accounts)!=1 or not re.fullmatch(r'[1-9]\d{4,9}', str(accounts[0])):
            raise ManagedError('unexpected_account', '此托管容器要求只登录一个 QQ 账号，请核对扫码窗口。')
        account=str(accounts[0])
        if expected and account!=expected:
            raise ManagedError('account_mismatch', '容器中的 QQ 账号与原绑定不同，已停止托管；不会自动切换账号。')
        self.m.check_account(account)
        return account

    def verify(self, state):
        row=self.runtime.validate(state['owner'], state['installation'], state['instance'], state['ports'], running=True)
        if state.get('container_started_at') != row.get('State',{}).get('StartedAt'):
            raise ManagedError('docker_restarted', '容器已重启，正在重新核对原账号。', retryable=True)
        try:
            if not self.account(self.m.api, state.get('account')):
                raise ManagedError('qq_offline', 'QQ 已离线，请通过扫码入口重新登录原账号。', retryable=True)
            if self.m.api.request('GET','/api/auth/state').get('mustChangePassword') is not False:
                raise ManagedError('admin_not_ready', '容器管理认证已变化，托管访问已暂停。')
        except ManagedError as exc:
            if exc.code=='admin_auth_failed':
                raise ManagedError('admin_session_expired', '管理会话失效，正在使用原凭据重新认证。', retryable=True) from None
            raise
