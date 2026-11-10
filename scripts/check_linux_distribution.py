"""Linux archive boundaries, documentation and extracted runtime checks."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatlocal.linux_distribution import QUICKSTART_NAMES, LINUX_QUICKSTART, linux_readme, source_files
from chatlocal.linux_service import source_archive


class DistributionTests(unittest.TestCase):
    def test_excludes_desktop_scripts_private_files_and_symlinks(self):
        with tempfile.TemporaryDirectory(prefix='tulpa-package-check-') as folder:
            root = Path(folder)
            wanted = {'tulpa', 'setup-linux.sh', '.env.example', 'TESTING.txt',
                      'chatlocal/example.py', 'doc/LINUX_TERMINAL.md',
                      'desktop/diagnose.py', 'licenses/qq-bridge-MIT.txt',
                      'web/easter-egg/1.png', 'web/easter-egg/2.png'}
            unwanted = {'setup.ps1', 'start.ps1', 'stop.ps1', 'scripts/run.ps1',
                        'scripts/run.PS1', 'scripts/run.cmd', 'scripts/run.bat',
                        'scripts/prepare_reader_runtime.py', 'scripts/check_upgrade.py',
                        'desktop/build.py', 'desktop/Tulpa.exe', 'desktop/example.dll',
                        'doc/DESKTOP.md', 'doc/SNOWLUMA_SETUP.md', 'doc/releases/0.5.5.md',
                        'requirements/readers.txt', '.env', 'data/private.json',
                        '.venv/private.py', 'chatlocal/__pycache__/example.pyc',
                        'chatlocal/.private/key.json', 'chatlocal/data/secret.json',
                        'chatlocal/cache.db', 'chatlocal/private.key',
                        'chatlocal/run.ps1', 'scripts/test.log',
                        'instances/claude/.env', 'instances/deepseek/data/terminal-admin.token',
                        'instances/deepseek/chatlocal/config.py'}
            for name in wanted | unwanted:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('public fixture' if name in wanted else 'PRIVATE-FIXTURE')
            (root / 'tulpa').chmod(0o755)
            (root / 'chatlocal/linked.py').symlink_to(root / '.env')
            (root / 'chatlocal/linked-dir').symlink_to(root / 'data', target_is_directory=True)
            output = root / 'clean.tar.gz'
            result = source_archive(output, root)
            self.assertEqual(result['files'], len(wanted))
            with tarfile.open(output) as archive:
                self.assertEqual(set(archive.getnames()), {'Tulpa/' + p for p in wanted})
                for member in archive:
                    self.assertTrue(member.isfile())
                    self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, '', ''))
                    self.assertNotIn(b'PRIVATE-FIXTURE', archive.extractfile(member).read())
                self.assertEqual(archive.getmember('Tulpa/tulpa').mode, 0o755)
            previous = output.read_bytes()
            with self.assertRaises(FileExistsError):
                source_archive(output, root)
            self.assertEqual(output.read_bytes(), previous)

    def test_quickstart_names_survive_repacking(self):
        for names in (('TESTING.txt',), ('看我.txt',), tuple(sorted(QUICKSTART_NAMES))):
            with self.subTest(names=names), tempfile.TemporaryDirectory(prefix='tulpa-quickstart-check-') as folder:
                root = Path(folder)
                content = '自定义测试说明\n运行 ./tulpa\n'.encode('utf-8')
                for name in names:
                    (root / name).write_bytes(content)
                (root / '看我-private.txt').write_text('PRIVATE-FIXTURE')
                output = root / 'clean.tar.gz'
                source_archive(output, root)
                with tarfile.open(output) as archive:
                    self.assertEqual(set(archive.getnames()), {'Tulpa/' + name for name in names})
                    for member in archive:
                        self.assertEqual(archive.extractfile(member).read(), content)

    def test_linux_readme_replaces_shared_readme_and_survives_repacking(self):
        with tempfile.TemporaryDirectory(prefix='tulpa-readme-check-') as folder:
            root = Path(folder)
            (root / 'README.md').write_text('Windows: .\\setup.ps1\n')
            content = 'Linux: ./tulpa\n'.encode()
            (root / 'README.linux.md').write_bytes(content)
            source_archive(root / 'first.tar.gz', root)
            with tarfile.open(root / 'first.tar.gz') as archive:
                self.assertEqual(archive.getnames(), ['Tulpa/README.md'])
                self.assertEqual(archive.extractfile('Tulpa/README.md').read(), content)
                archive.extractall(root / 'unpacked', filter='data')
            source_archive(root / 'second.tar.gz', root / 'unpacked/Tulpa')
            with tarfile.open(root / 'second.tar.gz') as archive:
                self.assertEqual(archive.getnames(), ['Tulpa/README.md'])
                self.assertEqual(archive.extractfile('Tulpa/README.md').read(), content)

    def test_organized_guides_keep_package_root_and_repacking(self):
        with tempfile.TemporaryDirectory(prefix='tulpa-organized-docs-') as folder:
            root = Path(folder)
            (root / 'doc').mkdir()
            (root / 'README.md').write_text('Desktop source instructions')
            (root / 'README.linux.md').write_text('Older source guide')
            (root / 'doc/LINUX.md').write_text('Linux MCP: ./tulpa\n')
            (root / LINUX_QUICKSTART).write_text('Check ./tulpa start\n')
            source_archive(root / 'first.tar.gz', root)
            with tarfile.open(root / 'first.tar.gz') as archive:
                self.assertEqual(set(archive.getnames()), {'Tulpa/README.md', 'Tulpa/TESTING.txt'})
                self.assertEqual(archive.extractfile('Tulpa/README.md').read(), b'Linux MCP: ./tulpa\n')
                archive.extractall(root / 'unpacked', filter='data')
            source_archive(root / 'second.tar.gz', root / 'unpacked/Tulpa')
            with tarfile.open(root / 'second.tar.gz') as archive:
                self.assertEqual(set(archive.getnames()), {'Tulpa/README.md', 'Tulpa/TESTING.txt'})
                self.assertEqual(archive.extractfile('Tulpa/TESTING.txt').read(), b'Check ./tulpa start\n')

    def test_shipped_docs_links_and_linux_instructions(self):
        selected = {p.resolve() for p in source_files(ROOT)}
        docs = [p for p in selected if p.suffix == '.md' and
                (p.parent == ROOT or p.is_relative_to(ROOT / 'doc'))]
        self.assertGreaterEqual(len(docs), 10)
        for path in docs:
            content = path.read_text()
            self.assertNotRegex(content, r'(?i)```powershell|\.(?:ps1|cmd|bat)\b|Tulpa\.exe|win-x64\.zip|USERPROFILE')
            for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', content):
                url = urlsplit(link.strip('<>'))
                if url.scheme or url.netloc:
                    continue
                target = (path.parent / unquote(url.path)).resolve() if url.path else path
                self.assertIn(target, selected, f'{path.relative_to(ROOT)}: {link}')
                if url.fragment and target.suffix == '.md':
                    headings = re.findall(r'^#{1,6}\s+(.+?)\s*$', target.read_text(), re.M)
                    anchors = {re.sub(r'[^\w\- ]', '', h.lower()).replace(' ', '-') for h in headings}
                    self.assertIn(unquote(url.fragment), anchors, f'{path.name}: {link}')
        quickstarts = sorted(name for name in (*QUICKSTART_NAMES, LINUX_QUICKSTART) if (ROOT / name).resolve() in selected)
        self.assertTrue(quickstarts, '发行包需要 TESTING.txt 或 看我.txt 测试说明。')
        readme = linux_readme(ROOT).relative_to(ROOT).as_posix()
        for entry in (readme, 'doc/LINUX_TERMINAL.md', *quickstarts):
            self.assertIn('./tulpa', (ROOT / entry).read_text())

    def test_dependency_and_linux_suite_references_are_shipped(self):
        selected = {p.resolve() for p in source_files(ROOT)}
        for path in selected:
            if path.parent != ROOT / 'requirements':
                continue
            for line in path.read_text().splitlines():
                if line.startswith('-r '):
                    self.assertIn((path.parent / line[3:].strip()).resolve(), selected)
        from check_linux_suite import CHECKS
        for check in CHECKS:
            self.assertIn(ROOT / 'scripts' / ('check_' + check + '.py'), selected)
        for entry in ('tulpa', 'setup-linux.sh', 'desktop/diagnose.py',
                      'chatlocal/terminal_launcher.py', 'chatlocal/snowluma_desktop.py',
                      'chatlocal/tui/__init__.py', 'licenses/qq-bridge-MIT.txt',
                      'web/easter-egg/1.png', 'web/easter-egg/2.png'):
            self.assertIn(ROOT / entry, selected)

    def test_extracted_backend_including_shared_diagnostics(self):
        with tempfile.TemporaryDirectory(prefix='tulpa-extracted-check-') as folder:
            root = Path(folder)
            source_archive(root / 'clean.tar.gz', ROOT)
            with tarfile.open(root / 'clean.tar.gz') as archive:
                archive.extractall(root / 'unpacked', filter='data')
            target = root / 'unpacked/Tulpa'
            self.assertFalse((target / 'data').exists())
            for name in ('1.png', '2.png'):
                self.assertEqual((target / 'web/easter-egg' / name).read_bytes(),
                                 (ROOT / 'web/easter-egg' / name).read_bytes())
            env = dict(os.environ)
            env.pop('PYTHONPATH', None)
            code = '''
from pathlib import Path
from fastapi.testclient import TestClient
from chatlocal.terminal_server import create_app
from chatlocal.terminal_client import TerminalClient
app = create_app(root=Path.cwd(), token='distribution-fixture-' + 'x'*48)
with TestClient(app) as client:
    client.headers.update(TerminalClient.headers('distribution-fixture-' + 'x'*48))
    response = client.get('/api/terminal/overview')
    assert response.status_code == 200, response.text
    assert response.json()['counts'] == dict(messages=0, connections=0, active_sessions=0)
    assert client.get('/api/desktop/diagnostics').status_code == 200
'''
            result = subprocess.run([sys.executable, '-c', code], cwd=target, env=env,
                                    capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr[-5000:])


if __name__ == '__main__':
    unittest.main()
