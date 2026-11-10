"""Protected connection references and real MCP calls. No auto retry of writes."""
import json
from pathlib import Path
import re
import uuid

import httpx

from .config import ROOT
from .platform_lock import file_lock
from .snowluma_secure import SecretVault, atomic_bytes, private_directory
from .terminal_client import TerminalError, validate_url


def connection_vault(root=ROOT):
    return SecretVault(Path(root)/'data/cli-connections')


def reference(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise TerminalError('连接 ID 无效。')
    return value


def remember(gid, token, root=ROOT):
    reference(gid)
    if not isinstance(token, str) or not 20 <= len(token) <= 256 or any(c.isspace() for c in token):
        raise TerminalError('连接凭据格式无效。')
    with file_lock(Path(root)/'.tmp/cli-connections.lock', blocking=True):
        store = connection_vault(root)
        values = store.read()
        values[gid] = token
        store.write(values)


def forget(gid, root=ROOT):
    reference(gid)
    with file_lock(Path(root)/'.tmp/cli-connections.lock', blocking=True):
        store = connection_vault(root)
        values = store.read()
        values.pop(gid, None)
        store.write(values)


def credential(gid, root=ROOT):
    value = connection_vault(root).read().get(reference(gid))
    if not value:
        raise TerminalError('本机 CLI 尚未保存此连接凭据。请用 connections import 显式导入该连接的 MCP 配置，或创建新连接。')
    return value


def current_connection(client, gid):
    reference(gid)
    status = client.request('GET', '/api/mcp')
    row = next((r for r in status['connections'] if r['id'] == gid), None)
    if not row or row.get('revoked') or row.get('paused'):
        raise TerminalError('连接不存在、已关闭或已撤销。')
    if not status['running']:
        raise TerminalError('MCP 服务未开启；请使用 mcp enable。')
    return status, row


def rpc(url, token, method, params, *, timeout=245, transport=None):
    if not url.endswith('/mcp'):
        raise TerminalError('MCP 地址无效。')
    validate_url(url[:-4])
    headers = {'Authorization': 'Bearer '+token, 'Accept': 'application/json, text/event-stream'}
    try:
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False, transport=transport) as http:
            def request(name, payload):
                response = http.post(url, headers=headers, json=dict(jsonrpc='2.0', id=uuid.uuid4().hex, method=name, params=payload))
                if response.status_code == 401:
                    raise TerminalError('MCP 凭据已失效，请检查连接授权。')
                if response.is_error or response.is_redirect:
                    raise TerminalError(f'MCP 返回 HTTP {response.status_code}；未自动重试。')
                value = response.json()
                if not isinstance(value, dict) or 'result' not in value or 'error' in value:
                    raise TerminalError('MCP 协议返回错误，未自动重试。')
                return value['result']
            info = request('initialize', dict(protocolVersion='2025-03-26', capabilities={},
                           clientInfo=dict(name='tulpa-cli', version='1')))
            headers['MCP-Protocol-Version'] = info['protocolVersion']
            return request(method, params)
    except httpx.TimeoutException:
        raise TerminalError('MCP 调用超时，结果未知；可能已执行。请查回执，勿更换幂等键重试。') from None
    except httpx.HTTPError:
        raise TerminalError('MCP 连接中断，结果未知；未自动重试。请先查操作回执。') from None
    except (ValueError, KeyError) as exc:
        if isinstance(exc, TerminalError):
            raise
        raise TerminalError('MCP 响应不完整，结果未知；未自动重试。') from None


def invoke(client, gid, method, params):
    status, _ = current_connection(client, gid)
    return rpc(status['url'], credential(gid, client.root), method, params)


def export_config(client, gid):
    status, row = current_connection(client, gid)
    directory = client.root/'data/cli-exports'
    private_directory(directory)
    path = directory/(reference(gid)+'.json')
    value = dict(mcpServers={row['name']: dict(type='http', url=status['url'],
                 headers={'Authorization': 'Bearer '+credential(gid, client.root)})})
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))
    return dict(config_file=str(path), connection_id=gid)


def tool_failed(result):
    if not isinstance(result, dict):
        return False
    if result.get('isError'):
        return True
    for item in result.get('content', []):
        if item.get('type') == 'text':
            try:
                value = json.loads(item['text'])
            except (ValueError, KeyError):
                continue
            if isinstance(value, dict) and tool_failed(value):
                return True
    return (result.get('ok') is False or bool(result.get('error'))
            or str(result.get('status','')).lower() in ('failed','unknown','rejected','cancelled'))
