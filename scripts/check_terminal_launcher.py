"""Real isolated launcher processes; no live QQ, model calls or service changes."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatlocal.terminal_client import TerminalClient, TerminalError
from chatlocal.terminal_launcher import ensure_backend, installed_service, stop_backend


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='terminal-launch with spaces ', dir=ROOT / '.tmp')
        self.root = Path(self.tmp.name)
        # The unified MCP backend imports both packages, even when this empty
        # fixture grants no Bilibili tools. Keep its installation self-contained.
        for package in ('chatlocal', 'tulpa_bilibili'):
            shutil.copytree(ROOT / package, self.root / package, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / 'requirements', self.root / 'requirements')
        for name in ('tulpa', 'setup-linux.sh'):
            shutil.copy2(ROOT / name, self.root / name)
        (self.root / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
        self.port = free_port()
        self.url = f'http://127.0.0.1:{self.port}'
        self.token = self.root / 'data/terminal-admin.token'
        self.env = os.environ.copy()
        for key in list(self.env):
            if key in ('API_BASE', 'API_KEY', 'MODEL', 'PYTHONPATH') or key.startswith(
                    ('TULPA_', 'REPLY_ONEBOT', 'ARTIFACT_ONEBOT', 'VISION_', 'AGENT_')):
                self.env.pop(key, None)

    def tearDown(self):
        if self.token.exists():
            try:
                self.command('stop')
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.tmp.cleanup()

    def args(self, *args):
        return [str(self.root / 'tulpa'), '--json', '--url', self.url, *args]

    def command(self, *args):
        return subprocess.run(self.args(*args), cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=40)

    def success(self, *args):
        result = self.command(*args)
        details = result.stderr
        log = self.root / '.tmp/terminal-service.log'
        if result.returncode and log.is_file():
            details += '\n' + log.read_text(errors='replace')[-6000:]
        self.assertEqual(result.returncode, 0, details)
        return json.loads(result.stdout)

    def test_start_reuse_private_files_and_graceful_stop(self):
        self.assertTrue(self.success('start')['started'])
        token = self.token.read_text()
        self.assertEqual(self.token.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / '.tmp/terminal-service.log').stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.success('start')['started'])
        self.assertEqual(token, self.token.read_text())
        overview = self.success('status')
        self.assertEqual(overview['counts'], dict(messages=0, connections=0, active_sessions=0))
        self.assertFalse(overview['mcp']['enabled'])
        self.assertTrue(self.success('stop')['stopped'])
        self.assertTrue(self.success('stop')['already_stopped'])

    def test_shutdown_poll_does_not_authenticate_after_listener_starts_closing(self):
        client = TerminalClient(self.url, self.token)
        stopping = False

        def overview(path):
            if stopping:
                raise TerminalError('listener closed between health and authenticated request')
            self.assertEqual(path, '/overview')
            return dict(storage=str(self.root / 'data'))

        def shutdown(path):
            nonlocal stopping
            self.assertEqual(path, '/shutdown')
            stopping = True
            return dict(stopping=True)

        with patch.object(client, 'get', side_effect=overview) as get, \
                patch.object(client, 'post', side_effect=shutdown), \
                patch('chatlocal.terminal_launcher.health_ready', side_effect=[True, True, False]), \
                patch('chatlocal.terminal_launcher.service_busy', return_value=False), \
                patch('chatlocal.linux_service.service_status', return_value={}):
            self.assertTrue(stop_backend(client, root=self.root)['stopped'])
        self.assertEqual(get.call_count, 2)

    def test_default_entry_opens_ui_after_backend_and_keeps_it_running(self):
        code = '''
import sys
from unittest.mock import patch
from chatlocal.terminal_cli import main
from chatlocal.terminal_client import TerminalClient
def ui(base_url=None,token_file=None):
    assert TerminalClient(base_url,token_file).get('/overview')['counts']['messages']==0
    print('UI received a ready backend')
with patch('chatlocal.tui.run',side_effect=ui) as opened:
    assert main(['--url',sys.argv[1]])==0
    assert opened.call_count==1
'''
        result = subprocess.run([sys.executable, '-c', code, self.url], cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('UI received a ready backend', result.stdout)
        self.assertFalse(self.success('start')['started'])

    def test_simultaneous_launches_start_one_backend(self):
        processes = [subprocess.Popen(self.args('start'), cwd=self.root, env=self.env,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = []
        for process in processes:
            out, err = process.communicate(timeout=40)
            self.assertEqual(process.returncode, 0, err)
            results.append(json.loads(out))
        self.assertEqual(sorted(r['started'] for r in results), [False, True])
        self.assertEqual((self.root / '.tmp/terminal-service.log').read_text().count('Started server process'), 1)

    def test_foreign_port_receives_no_credential_and_is_not_replaced(self):
        requests = []
        class OtherService(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                requests.append((self.path, self.headers.get('Authorization')))
                raw = b'{"product":"some other app"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', self.port), OtherService)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.command('start')
            self.assertEqual(result.returncode, 1)
            self.assertIn('其他服务占用', result.stderr)
            self.assertFalse(self.token.exists())
            self.assertEqual(requests, [('/health', None)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_wrong_token_does_not_restart_existing_backend(self):
        self.success('start')
        token = self.token.read_text()
        try:
            self.token.write_text('wrong-token-' * 6)
            result = self.command('start')
            self.assertEqual(result.returncode, 1)
            self.assertIn('凭据与服务不匹配', result.stderr)
        finally:
            self.token.write_text(token)
        self.assertFalse(self.success('start')['started'])
        self.assertEqual((self.root / '.tmp/terminal-service.log').read_text().count('Started server process'), 1)

    def test_stop_cannot_stop_a_different_installation(self):
        from chatlocal.terminal_launcher import stop_backend
        self.success('start')
        client = TerminalClient(self.url, self.token)
        with self.assertRaisesRegex(TerminalError, '另一个 Tulpa 目录'):
            stop_backend(client, root=self.root / 'another installation')
        self.assertFalse(self.success('start')['started'])

    def test_running_other_port_is_not_duplicated(self):
        self.success('start')
        another = f'http://127.0.0.1:{free_port()}'
        with self.assertRaisesRegex(TerminalError, '其他端口运行'):
            ensure_backend(another, self.token, root=self.root, timeout=.4)
        self.assertFalse(self.success('start')['started'])

    def test_failed_dependency_install_does_not_start_backend(self):
        (self.root / 'requirements/tui.txt').write_text('not-a-real-tulpa-test-package==0.0.0\n')
        (self.root / 'setup-linux.sh').write_text('#!/bin/sh\nprintf attempted > .tmp/install-attempt\nexit 42\n')
        result = self.command('start')
        self.assertEqual(result.returncode, 1)
        self.assertIn('依赖安装未完成', result.stderr)
        self.assertTrue((self.root / '.tmp/install-attempt').exists())
        self.assertFalse(self.token.exists())

    def test_backend_start_failure_reports_log_and_releases_lock(self):
        (self.root / 'chatlocal/terminal_server.py').write_text(
            'def run(*args):\n    raise ValueError("isolated startup failure")\n')
        result = self.command('start')
        self.assertEqual(result.returncode, 1)
        self.assertIn('terminal-service.log', result.stderr)
        self.assertIn('isolated startup failure', (self.root / '.tmp/terminal-service.log').read_text())
        self.assertTrue(self.success('stop')['already_stopped'])

    def test_existing_systemd_unit_selection_preserves_configuration(self):
        from chatlocal.linux_service import unit_text
        unit = self.root / 'tulpa.service'
        original = unit_text(self.root, self.port)
        unit.write_text(original)
        with patch('chatlocal.linux_service.service_status', return_value={'available': True, 'unit': str(unit)}):
            self.assertTrue(installed_service(self.root, self.port, self.token))
            with self.assertRaises(TerminalError):
                installed_service(self.root, self.port + 1, self.token)
            unit.write_text(unit_text(self.root / 'another copy', self.port))
            self.assertFalse(installed_service(self.root, self.port, self.token))


if __name__ == '__main__':
    unittest.main(verbosity=2)
