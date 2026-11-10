"""Local CLI access to the running MCP desktop, with no second application core."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .config import ROOT
from .platform_lock import file_lock
from .snowluma_secure import SecretVault, atomic_bytes, local_path
from .terminal_client import TerminalError, validate_url
from .version import VERSION

PROTOCOL = 'tulpa-cli-v1'
PREFIX = '/api/cli/manage'


def vault(root=ROOT):
    return SecretVault(Path(root)/'data/cli-access')


def management_token(root=ROOT, *, create=False):
    with file_lock(Path(root)/'.tmp/cli-credential.lock', blocking=True):
        value = vault(root).read()
        if not value.get('management') and create:
            value['management'] = secrets.token_urlsafe(48)
            vault(root).write(value)
        token = value.get('management')
        if not isinstance(token, str) or len(token) < 32:
            raise TerminalError('请先运行 Tulpa.CLI.exe start。')
        return token


def managed_path(path):
    """Configuration APIs only. Tool calls go through the real MCP transport."""
    return (isinstance(path, str) and not any(x in path for x in ('..', '%', '\\', '?', '#', '://'))
            and (path == '/api/mcp' or path.startswith('/api/mcp/')
                 or path in ('/api/desktop/status', '/api/desktop/settings', '/api/desktop/preferences',
                             '/api/desktop/onebot/test', '/bilibili/api/status', '/bilibili/api/action',
                             '/api/data/settings','/api/data/accounts','/api/data/catalog','/api/data/read',
                             '/api/refresh-status','/api/live-status','/api/live-settings')
                 or path.startswith('/api/watch-jobs/')
                 or path.startswith('/api/desktop/snowluma/')))


def install(app, *, root=ROOT, token=None):
    root = Path(root).resolve()
    token = token or management_token(root, create=True)

    @app.middleware('http')
    async def cli_gate(request: Request, call_next):
        path = request.url.path
        if not (path == '/api/cli' or path.startswith('/api/cli/')):
            return await call_next(request)
        from urllib.parse import urlparse
        base = str(request.base_url).rstrip('/')
        if (urlparse(base).hostname not in ('127.0.0.1', 'localhost', '::1', 'testserver')
                or request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient')
                or request.headers.get('origin') not in (None, base)
                or request.headers.get('sec-fetch-site') == 'cross-site'):
            return JSONResponse({'error': 'local_only'}, status_code=403)
        if not secrets.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer '+token).encode()):
            return JSONResponse({'error': 'cli_unauthorized'}, status_code=401)
        if request.method not in ('GET', 'HEAD'):
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'error': 'json_required'}, status_code=415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 512000:
                    return JSONResponse({'error': 'request_too_large'}, status_code=413)
            request._body = bytes(body)
        if path.startswith(PREFIX+'/'):
            target = path[len(PREFIX):]
            if not managed_path(target):
                return JSONResponse({'error': 'unsupported_management_route'}, status_code=404)
            request.scope['path'] = target
            request.scope['raw_path'] = target.encode('utf-8')
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/cli/identity')
    def identity():
        return dict(protocol=PROTOCOL, version=VERSION, root=str(root), pid=os.getpid())

    @app.post('/api/cli/stop')
    def stop(body: dict):
        if body:
            raise HTTPException(400, '停止不接受额外参数。')
        callback = getattr(app.state, 'cli_shutdown', None)
        if callback is None:
            raise HTTPException(409, '请由原启动器停止当前服务。')
        callback()
        return dict(stopping=True)

    @app.post('/api/cli/bind')
    def bind(body: dict):
        from .mcp_access import AccessDenied
        if set(body) != {'id','token'} or not all(isinstance(v,str) for v in body.values()):
            raise HTTPException(400, '连接字段无效。')
        try:
            grant = app.state.tulpa_mcp.access.authorize(body['token'], source=False)
        except AccessDenied:
            raise HTTPException(403, '连接凭据无效。') from None
        if grant['id'] != body['id']:
            raise HTTPException(403, '凭据属于另一条连接，未导入。')
        return dict(connection_id=grant['id'], credential_saved=True)


def publish(url, root=ROOT):
    path = Path(root)/'.tmp/cli-endpoint.json'
    atomic_bytes(path, json.dumps(dict(url=validate_url(url), pid=os.getpid(), protocol=PROTOCOL)).encode())


class Client:
    def __init__(self, root=ROOT, *, timeout=15):
        self.root = Path(root).resolve()
        self.timeout = timeout
        try:
            info = json.loads(local_path(self.root/'.tmp/cli-endpoint.json').read_text(encoding='utf-8'))
            self.url = validate_url(info['url'])
            if info['protocol'] != PROTOCOL:
                raise ValueError()
        except (OSError, ValueError, KeyError):
            raise TerminalError('本目录的 Tulpa 尚未启动或需升级；请运行 Tulpa.CLI.exe start。') from None
        self.token = management_token(self.root)
        identity = self.request('GET', '/api/cli/identity', internal=True)
        if identity.get('protocol') != PROTOCOL or Path(identity.get('root', '')).resolve() != self.root:
            raise TerminalError('端口属于其他 Tulpa 安装，未接入。')

    def request(self, method, path, body=None, *, params=None, internal=False):
        if not internal and not managed_path(path):
            raise TerminalError('不是受支持的配置接口；业务调用请使用 tools / call。')
        headers = {'Authorization': 'Bearer '+self.token, 'X-ChatWeave-UI': '1', 'X-Tulpa-Bilibili': '1'}
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False, follow_redirects=False) as http:
                response = http.request(method, self.url+('' if internal else PREFIX)+path,
                                        json=body, params=params, headers=headers)
            if response.is_redirect:
                raise TerminalError('拒绝重定向，请核对当前 Tulpa 安装。')
            result = response.json()
            if response.is_error:
                detail = result.get('detail', result.get('error', '配置操作未完成。')) if isinstance(result, dict) else '响应无效。'
                raise TerminalError(str(detail), status_code=response.status_code)
            return result
        except httpx.TimeoutException:
            raise TerminalError('请求超时，操作结果未知；请先检查状态，不要自动重复写入。') from None
        except httpx.HTTPError:
            raise TerminalError('无法连接本目录的 Tulpa；请运行 start 或检查服务状态。') from None
        except ValueError as exc:
            if isinstance(exc, TerminalError):
                raise
            raise TerminalError('本机服务响应格式无效。') from None


def start(root=ROOT, *, timeout=90):
    root = Path(root).resolve()
    try:
        client = Client(root)
        return dict(running=True, started=False, url=client.url)
    except TerminalError:
        pass
    exe = root/'Tulpa.exe'
    if not exe.is_file():
        raise TerminalError('开发态请运行 python -m chatlocal.mcp_cli serve；安装包中使用 start。')
    # Tulpa.exe has a per-installation mutex; concurrent starts cannot create a second backend.
    subprocess.Popen([str(exe), '--background'], cwd=root,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            client = Client(root)
            return dict(running=True, started=True, url=client.url)
        except TerminalError:
            time.sleep(.3)
    raise TerminalError('Tulpa 启动未完成；若旧版仍在运行，请先退出再试。未更改现有配置。')


def serve():
    """Development entry; shares the same lock and app as the desktop launcher."""
    import socket
    import threading
    import uvicorn
    from app_mcp import create_app
    with file_lock(ROOT/'.tmp/mcp-application.lock'):
        app = create_app()
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning'))
        app.state.cli_shutdown = lambda: setattr(server, 'should_exit', True)
        app.state.tulpa_mcp.source_base = f'http://127.0.0.1:{port}'
        def announce():
            while not server.started:
                if server.should_exit:
                    return
                time.sleep(.1)
            publish(app.state.tulpa_mcp.source_base)
        threading.Thread(target=announce, daemon=True).start()
        try:
            server.run(sockets=[listener])
        finally:
            listener.close()
