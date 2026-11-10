"""Explicitly authorized local Codex setup; never prints bearer credentials."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from chatlocal.store import Store
from chatlocal.mcp_access import MCPAccess
from chatlocal.mcp_connect import codex_home, codex_server_name, install_codex, test_connection


async def main():
    parser = argparse.ArgumentParser(description='Install a read-only connection for imported platforms.')
    parser.add_argument('--platform', action='append', choices=('qq', 'wechat'), required=True)
    parser.add_argument('--port', type=int, default=18778)
    parser.add_argument('--server-name', default='tulpa_mac_preview')
    args = parser.parse_args()
    name = codex_server_name(args.server_name)
    platforms = list(dict.fromkeys(args.platform))
    store = Store()
    with store.connect() as db:
        for platform in platforms:
            if not db.execute('SELECT 1 FROM messages WHERE platform=? LIMIT 1', (platform,)).fetchone():
                raise SystemExit(f'Import {platform} history before creating its connection.')
    config = codex_home()/'config.toml'
    existing = tomllib.loads(config.read_text()).get('mcp_servers', {}).get(name) if config.exists() else None
    access = MCPAccess(store)
    if existing:
        token = existing.get('http_headers', {}).get('Authorization', '').removeprefix('Bearer ')
        access.authorize(token)
        result = await test_connection(existing['url'], token)
        print(json.dumps(dict(configured=True, changed=False, **result)))
        return
    desktop = json.loads((ROOT/'.tmp/macos-service.json').read_text())['url']
    async with httpx.AsyncClient(base_url=desktop, trust_env=False, timeout=30,
                                headers={'X-ChatWeave-UI': '1'}) as http:
        status = (await http.get('/api/mcp')).json()
        port = status['port'] if status['enabled'] else args.port
        response = await http.put('/api/mcp', json=dict(enabled=True, port=port))
        response.raise_for_status()
        url = response.json()['url']
    grant = access.create(dict(name='Codex · 全部聊天资料（只读）', platforms=platforms,
                             all_conversations=True,
                             media=True, prepare=True, voice=False))
    try:
        tested = await test_connection(url, grant['token'])
        scope = access.authorize(grant['token'])['scope']
        installed = install_codex(url, grant['token'], scope=scope, server_name=name)
    except Exception:
        access.revoke(grant['id'])
        raise
    print(json.dumps(dict(configured=True, url=url, **installed, **tested), ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
