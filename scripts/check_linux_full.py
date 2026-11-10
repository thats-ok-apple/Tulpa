"""Full-port acceptance with isolated stores, encrypted native pages and local children."""
import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'tools/qq-reader'),str(ROOT/'tools/wechat-reader')]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_fixture_support import isolated_env
from chatlocal.store import Store
from chatlocal.terminal_server import create_app
from chatlocal.terminal_client import TerminalClient
from chatlocal.platform_process import owned_popen,linux_identity
from chatlocal.snowluma_secure import SecretVault,ManagedError
from fastapi.testclient import TestClient


class LinuxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='linux-full-check-')
        self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_full_api_and_agent_lifecycle(self):
        store=Store(self.root/'data/chats.sqlite3');token='terminal-test-'+'x'*48
        app=create_app(root=self.root,store=store,token=token)
        def agent(*args,**kwargs):
            yield dict(type='started',text='fixture')
            yield dict(type='done',status='completed',record=dict(result={'claims':[]},events=[],bundle={}))
        with patch.dict(os.environ,isolated_env(),clear=True),TestClient(app) as client:
            for path in ('/api/tulpa','/api/workspaces','/api/terminal/model','/api/desktop/snowluma/managed'):
                self.assertEqual(client.get(path).status_code,401,path)
            client.headers.update(TerminalClient.headers(token))
            paths=['/api/chat-conversations','/api/tulpa','/api/watch-cards','/api/artifacts/status',
                   '/api/voice/settings','/api/collections','/api/workspaces','/api/knowledge/status',
                   '/api/desktop/status','/api/desktop/snowluma/managed','/api/data/settings','/api/live-status',
                   '/api/terminal/model','/api/terminal/components','/api/agent-presets','/api/read-options']
            for path in paths:
                response=client.get(path);self.assertEqual(response.status_code,200,(path,response.text))
            with patch('chatlocal.voice_routes.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{"found":0}')):
                response=client.post('/api/voice/backfill',json=dict(platform='qq',start='2026-01-01',end='2026-01-02',
                    conversations=['fixture'],transcribe=False))
                self.assertEqual(response.status_code,200,response.text)
                for _ in range(100):
                    discovery=client.get('/api/voice/settings').json()['discovery']
                    if not discovery['running']:break
                    time.sleep(.02)
                self.assertIsNone(discovery['error']);self.assertEqual(discovery['result'],{'found':0})
            response=client.put('/api/terminal/model',json=dict(api_base='https://example.invalid/v1',model='fixture',api_key='secret-fixture'))
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn('secret-fixture',response.text)
            self.assertTrue(response.json()['has_api_key'])
            self.assertEqual((self.root/'.env').stat().st_mode&0o777,0o600)
            self.assertEqual(client.put('/api/terminal/model',json=dict(api_key='')).json()['has_api_key'],True)
            sid=client.post('/api/terminal/agent/sessions',json={}).json()['id']
            with patch('chatlocal.agent.run_agent',agent):
                response=client.post('/api/terminal/agent/sessions/'+sid+'/ask',json=dict(question='fixture',profile='standard'))
                self.assertEqual(response.status_code,200,response.text)
                for _ in range(100):
                    history=client.get('/api/terminal/agent/sessions/'+sid).json()
                    if history['turns'][0]['status']!='running':break
                    time.sleep(.02)
            self.assertEqual(history['turns'][0]['status'],'completed')
            self.assertEqual(client.request('DELETE','/api/terminal/agent/sessions/'+sid,json={}).status_code,200)
            self.assertEqual(client.put('/api/terminal/model',json=dict(clear_api_key=True)).json()['has_api_key'],False)
            self.assertEqual(client.put('/api/terminal/model',json=dict(api_base='http://external.invalid')).status_code,400)

    def test_linux_vault_does_not_reset_after_tampering(self):
        vault=SecretVault(self.root/'vault');value={'admin_password':'test-secret','http_token':'fixture'}
        vault.write(value);self.assertEqual(vault.read(),value)
        self.assertNotIn(b'test-secret',vault.path.read_bytes())
        for path in (vault.path,vault.directory/'vault.key'):self.assertEqual(path.stat().st_mode&0o777,0o600)
        raw=vault.path.read_bytes();vault.path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        with self.assertRaises(ManagedError):vault.read()
        key=(vault.directory/'vault.key').read_bytes()
        (vault.directory/'vault.key').unlink()
        with self.assertRaises(ManagedError):vault.read()
        self.assertFalse((vault.directory/'vault.key').exists())

    def test_explicit_native_source_and_private_key_reference(self):
        from chatlocal import native_sources as sources
        selected=self.root/'聊天数据 with spaces';selected.mkdir()
        keys=self.root/'keys.json';keys.write_text('{"fixture":"fixture-password"}');keys.chmod(0o600)
        result=sources.save_source('qq',dict(data_root=str(selected),keys_file=str(keys)),self.root)
        self.assertEqual(result['platforms']['qq']['data_root'],str(selected))
        self.assertNotIn('fixture-password',json.dumps(result))
        read_settings=sources.source_settings
        with patch.object(sources,'source_settings',side_effect=lambda root=self.root:read_settings(root)):
            self.assertEqual(sources.configured_root('qq'),selected)
            self.assertEqual(sources.supplied_keys('qq'),{'fixture':'fixture-password'})
            selected.rmdir()
            with self.assertRaises(ValueError):sources.configured_root('qq')
        keys.chmod(0o644)
        with self.assertRaises(ValueError):sources.read_keys(keys)
        keys.chmod(0o600)
        link=self.root/'link.json';link.symlink_to(keys)
        with self.assertRaises(OSError):sources.read_keys(link)

    def test_owned_child_cleanup(self):
        from chatlocal.voice_process import ChildJob
        process=owned_popen([sys.executable,'-c','import time;time.sleep(60)'])
        try:
            identity=linux_identity(process.pid);self.assertEqual(identity['pid'],process.pid)
            job=ChildJob(process);job.close();self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:process.kill();process.wait()

    def test_component_job_lifecycle(self):
        from chatlocal.linux_components import ComponentJobs
        from fastapi import HTTPException
        jobs=ComponentJobs(self.root)
        try:
            first=jobs.start('fixture',command=[sys.executable,'-c','import time;time.sleep(.2)'])['job_id']
            with self.assertRaises(HTTPException):jobs.start('readers')
            for _ in range(100):
                if jobs.status(first)['status']!='running':break
                time.sleep(.02)
            self.assertEqual(jobs.status(first)['status'],'completed')
            second=jobs.start('fixture',command=[sys.executable,'-c','import time;time.sleep(60)'])['job_id']
            for _ in range(100):
                if jobs.process is not None:break
                time.sleep(.02)
            child=jobs.process;self.assertIsNotNone(child)
            jobs.close();self.assertIsNotNone(child.poll())
            with self.assertRaises(HTTPException):jobs.start('readers')
        finally:jobs.close()

    def test_parent_death(self):
        pidfile=self.root/'child.pid'
        ready=self.root/'child.ready'
        code=('from chatlocal.platform_process import owned_popen\nimport sys,time,pathlib\n'
              'p=owned_popen([sys.executable,"-c","import time,pathlib,sys;pathlib.Path(sys.argv[1]).touch();time.sleep(60)",sys.argv[2]])\n'
              'pathlib.Path(sys.argv[1]).write_text(str(p.pid))\ntime.sleep(60)\n')
        parent=subprocess.Popen([sys.executable,'-c',code,str(pidfile),str(ready)],cwd=ROOT)
        try:
            for _ in range(100):
                if pidfile.exists() and ready.exists():break
                time.sleep(.02)
            self.assertTrue(ready.exists(),'Fixture child must execute before parent-death check')
            child=int(pidfile.read_text());self.assertIsNotNone(linux_identity(child));parent.kill();parent.wait()
            for _ in range(100):
                if linux_identity(child) is None:break
                time.sleep(.02)
            self.assertIsNone(linux_identity(child))
        finally:
            if parent.poll() is None:parent.kill();parent.wait()

    def test_linux_runtime_ownership_and_package(self):
        import copy
        from chatlocal.snowluma_runtime import LinuxRuntime
        from chatlocal.snowluma_local import LocalInstallation,runtime_files
        from chatlocal.snowluma_managed import load_manifest
        manifest=copy.deepcopy(load_manifest());folder=self.root/'runtime';folder.mkdir()
        for name in runtime_files(manifest):
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
        (folder/'package.json').write_text(json.dumps(dict(name='@snowluma/runtime',version=manifest['version'])))
        machine=183 if __import__('platform').machine()=='aarch64' else 62
        node=folder/'node';node.write_bytes(b'\x7fELF\x02\x01'+bytes(12)+machine.to_bytes(2,'little'));node.chmod(0o700)
        for agreement in manifest['agreements']:
            raw=agreement['id'].encode();(folder/agreement['file']).write_bytes(raw);agreement['sha256']=hashlib.sha256(raw).hexdigest()
        layer=LocalInstallation(manifest,self.root/'data/snowluma-managed')
        receipt=layer.inspect(str(folder));self.assertEqual(receipt['mode'],'local');layer.verify(receipt)
        (folder/'index.mjs').write_text('changed')
        with self.assertRaises(ManagedError):layer.verify(receipt)
        # Real pidfd signaling with a known fixture child; changing a recorded
        # birth identity must prevent termination even when PID still exists.
        script=folder/'index.mjs';script.write_text('import time;time.sleep(60)')
        proc=owned_popen([sys.executable,str(script)],env=dict(os.environ,TULPA_SNOWLUMA_INSTANCE='fixture'))
        runtime=LinuxRuntime()
        try:
            time.sleep(.1);owner=dict(linux_identity(proc.pid),instance='fixture');runtime.jobs[proc.pid]=proc
            with patch('chatlocal.snowluma_local.runtime_node',return_value=Path(sys.executable)):
                self.assertTrue(runtime.owned(owner,folder,'fixture'))
                self.assertFalse(runtime.stop(dict(owner,created='wrong'),folder,'fixture'))
                self.assertIsNone(proc.poll());self.assertTrue(runtime.stop(owner,folder,'fixture'))
                self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:proc.kill();proc.wait()

    def test_authenticated_linux_pages_and_large_qq_lock_boundary(self):
        from check_live_runtime import encrypt,sqlite_pages
        from live_vfs import ReadView,SourceBusy,configure_qq_worker
        import apsw
        for source in ('qq','wechat'):
            folder=self.root/source;folder.mkdir()
            pages=sqlite_pages(folder,48 if source=='qq' else 80)
            key=b'fixture-password' if source=='qq' else bytes(range(32))
            path=folder/'native.db';offset=1024 if source=='qq' else 0
            moved={}
            if source=='qq':
                configure_qq_worker()
                # Linux standard pending page is 262145. Page 262144 is real,
                # authenticated data; Windows' zero-placeholder rule must not run.
                moved={3:262144,4:262146,5:262147}
                struct.pack_into('>I',pages[2],4044,moved[3]);struct.pack_into('>I',pages[1],28,262147)
                for old in moved:
                    next_page=struct.unpack_from('>I',pages[old])[0];struct.pack_into('>I',pages[old],0,moved.get(next_page,0))
            with path.open('wb') as out:
                out.truncate(offset+(262147 if moved else len(pages))*4096)
                for number,page in pages.items():
                    number=moved.get(number,number);out.seek(offset+(number-1)*4096);out.write(encrypt(page,number,key,source))
            before=path.stat().st_mtime_ns
            with ReadView(path,key,source) as view:
                self.assertEqual(list(view.db.execute('SELECT value FROM fixture')),[('a'*15000,)])
                self.assertLess(view.view.bytes_read,65536)
                with self.assertRaises(apsw.ReadOnlyError):view.db.execute("INSERT INTO fixture VALUES('bad')")
                view.view.assert_fresh()
                with path.open('r+b') as out:out.seek(offset+500);out.write(b'bad')
                with self.assertRaises(SourceBusy):view.view.assert_fresh()
            with self.assertRaises(ValueError):ReadView(path,key,source)

    def test_wechat_master_key_adapter(self):
        from check_live_runtime import encrypt,sqlite_pages
        from wechatauto.db import WeChatDB
        from chatlog_keeper import wechat_db as wx
        account='wxid_fixture_abcd';folder=self.root/account/'db_storage/message';folder.mkdir(parents=True)
        pages=sqlite_pages(self.root,80);master=b'm'*32
        effective=hashlib.pbkdf2_hmac('sha512',master,b'fixture-salt-123',256000,32)
        path=folder/'message_0.db';path.write_bytes(b''.join(encrypt(page,n,effective,'wechat') for n,page in pages.items()))
        with patch('chatlocal.linux_readers.supplied_keys',return_value={'master_key':master.hex()}),\
             patch.object(wx,'load_cached_wechat_key_for_account',return_value=None),\
             patch.object(WeChatDB,'_find_weixin_pids',return_value=[]),\
             patch.object(WeChatDB,'_stable_key_file',return_value=None):
            reader=WeChatDB(db_dir=str(self.root),account=account,workdir=str(self.root/'work'),keys_file=str(self.root/'keys.json'))
            self.assertEqual(reader._keys['message/message_0.db'],effective)
            conn=reader._open('message/message_0.db')
            try:self.assertEqual(conn.execute('SELECT length(value) FROM fixture').fetchone()[0],15000)
            finally:conn.close()
        self.assertEqual((self.root/'keys.json').stat().st_mode&0o777,0o600)

    def test_service_and_source_package(self):
        import tarfile
        from chatlocal.linux_service import unit_text,source_archive
        text=unit_text(ROOT);self.assertIn('"'+str(ROOT/'tulpa')+'" serve',text)
        (self.root/'chatlocal').mkdir();(self.root/'chatlocal/a.py').write_text('print(1)')
        (self.root/'.env').write_text('SECRET=x');(self.root/'data').mkdir();(self.root/'data/secret').write_text('secret')
        output=self.root/'source.tar.gz';source_archive(output,self.root)
        with tarfile.open(output) as archive:self.assertEqual(archive.getnames(),['Tulpa/chatlocal/a.py'])


if __name__=='__main__':unittest.main()
