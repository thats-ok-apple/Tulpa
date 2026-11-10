"""Independent processes/accounts; local fake SL only, never live Docker or QQ."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from chatlocal.linux_distribution import linux_readme, source_files
from chatlocal.linux_service import service_name, unit_text
from chatlocal.mcp_access import MCPAccess
from chatlocal.snowluma_docker import DockerRuntime
from chatlocal.store import Store
from chatlocal.terminal_client import TerminalClient, TerminalError
from chatlocal.terminal_instances import InstanceManager, child_environment, metadata, validate_mcp_port
from chatlocal.terminal_launcher import ensure_backend, stop_backend
from chatlocal.terminal_server import create_app, ensure_token


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class InstancesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='instances with spaces ', dir=ROOT / '.tmp')
        self.root = Path(self.temp.name)
        for source in source_files(ROOT):
            target = self.root / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        (self.root / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
        (self.root / '.env').write_text('API_KEY=hub-private-fixture\n')
        (self.root / 'imports').mkdir()
        self.token = ensure_token(self.root / 'data/terminal-admin.token')
        self.manager = InstanceManager(self.root)
        self.management = free_port()
        self.mcp = free_port()
        self.url = f'http://127.0.0.1:{self.management}'
        self.store = Store(self.root / 'data/chats.sqlite3')
        MCPAccess(self.store).configure(False, self.mcp)
        self.overview = dict(storage=str(self.root / 'data'), mcp=dict(port=self.mcp, enabled=False, running=False))
        self.systemd = patch('chatlocal.linux_service.service_status', return_value={'available': False, 'installed': False})
        self.systemd.start()
        self.cleanups = []

    def tearDown(self):
        for cleanup in reversed(self.cleanups):
            cleanup()
        self.systemd.stop()
        self.temp.cleanup()

    def create(self, instance_id='deepseek', **options):
        return self.manager.create(dict(id=instance_id, name=instance_id, **options), self.url, self.overview)

    def test_clean_creation_and_independent_docker_service_identity(self):
        first, second = self.create(), self.create('claude')
        self.assertEqual(len({first['management_port'], second['management_port'], first['mcp_port'], second['mcp_port']}), 4)
        self.assertFalse(first['running'])
        roots = [self.root, Path(first['root']), Path(second['root'])]
        tokens = [(r / 'data/terminal-admin.token').read_text() for r in roots]
        self.assertEqual(len(set(tokens)), 3)
        for item in (first, second):
            child = Path(item['root'])
            self.assertFalse((child / '.env').exists())
            readme = linux_readme(ROOT)
            self.assertEqual((child / 'README.md').read_bytes(), readme.read_bytes())
            self.assertFalse((child / 'README.linux.md').exists())
            self.assertFalse((child / 'data/snowluma-managed').exists())
            self.assertEqual((child / '.venv').resolve(), (ROOT / '.venv').resolve())
            self.assertEqual((child / 'data/terminal-admin.token').stat().st_mode & 0o777, 0o600)
            access = MCPAccess(Store(child / 'data/chats.sqlite3'))
            self.assertEqual(access.settings(), dict(enabled=False, port=item['mcp_port']))
            self.assertEqual(access.list()['connections'], [])
            self.assertIn('UnsetEnvironment=', unit_text(child, item['management_port']))
            validate_mcp_port(child, item['mcp_port'])
            with self.assertRaises(ValueError): validate_mcp_port(child, self.mcp)
            with self.assertRaises(ValueError): validate_mcp_port(self.root, item['mcp_port'])
        self.assertEqual(service_name(self.root), 'tulpa.service')
        self.assertEqual(len({service_name(r) for r in roots}), 3)
        # Even identical random SL instance IDs still cannot share container/volume names.
        self.assertEqual(len({DockerRuntime(r).name('a' * 32) for r in roots}), 3)
        shipped = [p.relative_to(self.root).parts[0] for p in source_files(self.root)]
        self.assertNotIn('instances', shipped)
        with patch.dict(os.environ, dict(API_KEY='hub-private', REPLY_ONEBOT_TOKEN='hub-token',
                                       TULPA_TOKEN_FILE='hub-file', PYTHONPATH='hub-path')):
            env = child_environment(self.root)
            self.assertFalse({'API_KEY', 'REPLY_ONEBOT_TOKEN', 'TULPA_TOKEN_FILE', 'PYTHONPATH'} & env.keys())

    def test_authenticated_api_validation_rename_and_port_reservation(self):
        app = create_app(root=self.root, store=self.store, token=self.token)
        with TestClient(app, base_url=self.url) as client:
            self.assertEqual(client.get('/api/terminal/instances').status_code, 401)
            client.headers.update(TerminalClient.headers(self.token))
            for body in ({'id': '../escape', 'name': 'bad'}, {'id': 'default', 'name': 'bad'},
                         {'id': 'ok', 'name': ''}, {'id': 'ok', 'name': 'n', 'management_port': True},
                         {'id': 'ok', 'name': 'n', 'mcp_port': self.mcp},
                         {'id': 'ok', 'name': 'n', 'root': '/elsewhere'}):
                self.assertEqual(client.post('/api/terminal/instances', json=body).status_code, 400)
            created = client.post('/api/terminal/instances', json={'id': 'one', 'name': '一号'})
            self.assertEqual(created.status_code, 200, created.text)
            item = created.json()
            self.assertNotIn(self.token, created.text)
            self.assertEqual(client.put('/api/terminal/instances/one', json={'name': '二号'}).json()['name'], '二号')
            self.assertEqual(metadata(Path(item['root']))['name'], '二号')
            self.assertEqual(client.put('/api/terminal/instances/default', json={'name': '白龙娘'}).json()['name'], '白龙娘')
            self.assertEqual(client.get('/api/terminal/overview').json()['instance']['name'], '白龙娘')
            self.assertEqual(client.post('/api/terminal/instances/default/stop', json={}).status_code, 400)
            self.assertEqual(client.post('/api/terminal/instances/one/start', json={'unexpected': True}).status_code, 400)
            self.assertEqual(client.post('/api/terminal/instances', json={'id': 'one', 'name': '重复'}).status_code, 400)

    def test_parallel_creation_and_foreign_directories(self):
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(self.create, ['one', 'two']))
        self.assertEqual(len({r[k] for r in rows for k in ('management_port', 'mcp_port')}), 4)
        with self.assertRaises(ValueError): self.create('three', management_port=rows[0]['mcp_port'])
        target = self.root / 'instances/foreign'
        target.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): self.create('foreign')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            with self.assertRaises(ValueError): self.create('occupied', management_port=sock.getsockname()[1])
        child = Path(rows[0]['root'])
        info = metadata(child); info['hub_root'] = '/another/hub'
        (child / 'data/instance.json').write_text(json.dumps(info))
        with self.assertRaises(ValueError): self.manager.action('one', 'start')

    def test_three_processes_isolate_accounts_credentials_data_and_lifecycle(self):
        main = TerminalClient(self.url, self.root / 'data/terminal-admin.token')
        ensure_backend(self.url, main.token_file, root=self.root, environment=child_environment(self.root))
        self.cleanups.append(lambda: stop_backend(main, root=self.root))
        descriptors = [self.create('one'), self.create('two')]
        clients = []
        for item in descriptors:
            self.cleanups.append(lambda i=item: self.manager.action(i['id'], 'stop'))
            self.assertTrue(self.manager.action(item['id'], 'start')['running'])
            clients.append(main.for_instance(item))
        for client, item in zip(clients, descriptors):
            self.assertEqual(client.get('/overview')['storage'], str(Path(item['root']) / 'data'))
            self.assertEqual(client.get('/overview')['instance']['id'], item['id'])
            self.assertTrue(client.put('/mcp', dict(enabled=True, port=item['mcp_port']))['running'])
            with self.assertRaises(TerminalError): client.get('/instances')
        wrong = TerminalClient(clients[1].base_url, clients[0].token_file)
        with self.assertRaises(TerminalError) as error: wrong.get('/overview')
        self.assertEqual(error.exception.status_code, 401)
        for account, client in zip(('111', '222'), clients):
            class SL(BaseHTTPRequestHandler):
                own_account = account
                def log_message(self, *args): pass
                def do_POST(self):
                    self.rfile.read(int(self.headers.get('Content-Length', '0')))
                    data = dict(user_id=int(self.own_account)) if self.path == '/get_login_info' else [dict(group_id=555, group_name='fixture')]
                    raw = json.dumps(dict(status='ok', retcode=0, data=data)).encode()
                    self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
            server = ThreadingHTTPServer(('127.0.0.1', 0), SL)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            self.cleanups.append(lambda s=server: (s.shutdown(), s.server_close()))
            client.put('/sl', dict(http_url=f'http://127.0.0.1:{server.server_port}',
                                  ws_url=f'ws://127.0.0.1:{server.server_port}', http_token='fake-token', ws_token='fake-ws'))
            self.assertEqual(client.get('/groups')['items'][0]['account'], account)
        target = Path(descriptors[0]['root'])
        (target / 'imports/messages.json').write_text(json.dumps([dict(platform='qq', conversation_id='course',
            conversation='课程群', sender='老师', sender_id='teacher', source_id='isolation', timestamp='2026-10-02',
            content='课程设计使用 Tulpa 终端工具')]))
        clients[0].post('/import', dict(path='imports/messages.json'))
        self.assertEqual(clients[0].get('/overview')['counts']['messages'], 1)
        self.assertEqual(clients[1].get('/overview')['counts']['messages'], 0)
        self.assertEqual(main.get('/overview')['counts']['messages'], 0)
        fixture = self.root / 'chatlocal/_instance_upgrade_fixture.py'
        fixture.write_text('VALUE = "new public source"\n')
        persona = target / 'chatlocal/prompts/mcp_chat/dragon_girl.md'
        persona.write_text('# 这个账号的人设\n独立人格内容。\n')
        self.assertTrue(self.manager.action('one', 'start')['running'])
        self.assertFalse((target / 'chatlocal' / fixture.name).exists())  # reuse, no hot replacement
        self.assertFalse(self.manager.action('one', 'stop')['running'])
        self.assertEqual(clients[1].get('/groups')['items'][0]['account'], '222')
        self.assertTrue(main.get('/overview')['storage'])
        self.assertTrue(self.manager.action('one', 'start')['running'])
        self.assertTrue((target / 'chatlocal' / fixture.name).exists())
        self.assertIn('独立人格内容', persona.read_text())
        self.assertEqual(clients[0].get('/overview')['counts']['messages'], 1)
        self.assertEqual(clients[0].get('/groups')['items'][0]['account'], '111')
        cli = subprocess.run([str(target / 'tulpa'), '--json', 'status'], cwd=target,
                             env=child_environment(self.root), text=True, capture_output=True, timeout=15)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout)['instance']['id'], 'one')
        code = '''
from unittest.mock import patch
from chatlocal.terminal_cli import main
def ui(base_url=None, token_file=None, initial_instance=None):
    assert base_url == %r
    assert str(token_file) == %r
    assert initial_instance['id'] == 'one'
with patch('chatlocal.tui.run', side_effect=ui) as opened:
    assert main([]) == 0
    assert opened.call_count == 1
''' % (self.url, str(main.token_file))
        cli = subprocess.run([sys.executable, '-c', code], cwd=target, env=child_environment(self.root),
                             text=True, capture_output=True, timeout=20)
        self.assertEqual(cli.returncode, 0, cli.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
