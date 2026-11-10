"""CLI/gateway/MCP loopback integration with isolated data and no QQ/Bili writes."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv[sys.argv.index('--package')+1]).resolve() if '--package' in sys.argv else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(Path(__file__).resolve().parent))
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from chatlocal import mcp_cli, cli_runtime, cli_tools
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.store import Store
from chatlocal.terminal_client import TerminalError
from check_fixture_support import isolated_env, management_http

SECRET = 'fixture-'+'s'*48


class CLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tulpa-cli-')
        self.root = Path(self.temp.name)
        (self.root/'.tmp').mkdir()
        environment=isolated_env()
        if os.name=='nt':
            system=Path(os.environ['WINDIR'])/'System32'
            environment['PATH']=str(system)+os.pathsep+str(system/'WindowsPowerShell/v1.0')
        self.env = patch.dict(os.environ, environment, clear=True); self.env.start()
        self.app = FastAPI()
        self.store = Store(self.root/'data/chats.sqlite3')
        self.service = install_mcp_routes(self.app, self.store)
        cli_runtime.install(self.app, root=self.root, token=SECRET)
        self.ui = TestClient(self.app)
        self.headers = {'Authorization':'Bearer '+SECRET,'X-ChatWeave-UI':'1','X-Tulpa-Bilibili':'1'}
        self.service.access.configure(True, 18777)
        self.start_mock = patch.object(self.service, 'start'); self.start_mock.start()
        self.fake = type('ManagedClient', (), dict(root=self.root,url='http://testserver',request=self.request))()

    def tearDown(self):
        self.start_mock.stop(); self.service.stop(); self.service.tools.bilibili.close()
        self.ui.close(); self.env.stop(); self.temp.cleanup()

    def request(self, method, path, body=None, *, params=None, internal=False):
        response = self.ui.request(method, ('' if internal else cli_runtime.PREFIX)+path,
                                   json=body, params=params, headers=self.headers)
        if response.is_error:
            raise TerminalError(str(response.json()), status_code=response.status_code)
        return response.json()

    def command(self, text):
        return mcp_cli.execute(mcp_cli.parser().parse_args(text), self.fake)

    def create(self):
        path = self.root/'new config.json'
        path.write_text(json.dumps(mcp_cli.template('bilibili')),encoding='utf-8')
        return self.command(['connections','create','--file',str(path)])

    def test_gateway_authentication_origin_and_routes(self):
        self.assertEqual(self.ui.get('/api/cli/identity').status_code, 401)
        self.assertEqual(self.ui.get('/api/cli/identity',headers=self.headers|{'Origin':'http://evil.test'}).status_code,403)
        self.assertEqual(self.ui.get('/api/cli/identity',headers=self.headers|{'Host':'evil.test'}).status_code,403)
        self.assertEqual(self.ui.get('/api/cli/identity',headers=self.headers).json()['root'],str(self.root))
        for path in ('/api/desktop/quit','/api/terminal/shutdown','/invoke_action','/api/cli/stop'):
            self.assertEqual(self.ui.post(cli_runtime.PREFIX+path,json={},headers=self.headers).status_code,404)
        response = self.ui.post(cli_runtime.PREFIX+'/api/mcp/connections',content='x'*512001,headers=self.headers|{'Content-Type':'application/json'})
        self.assertEqual(response.status_code,413)

    def test_create_no_history_and_protected_secret(self):
        result = self.create()
        self.assertNotIn('token',result); self.assertTrue(result['credential_saved'])
        self.assertRegex(result['name'],r'^mcp-[a-f0-9]{12}$')
        token = cli_tools.credential(result['id'], self.root)
        protected = cli_tools.connection_vault(self.root).path.read_bytes()
        self.assertNotIn(token.encode(),protected)
        scope = self.service.access.authorize(token,source=False)['scope']
        self.assertEqual(scope['history_platforms'],[]); self.assertFalse(scope['send'])
        self.assertEqual(self.command(['status'])['connections'],1)

    def test_pause_edit_delete_and_reopen(self):
        result = self.create(); gid=result['id']
        self.command(['connections','disable',gid])
        with self.assertRaises(TerminalError): cli_tools.current_connection(self.fake,gid)
        self.command(['connections','enable',gid])
        self.assertEqual(self.command(['connections','list'])['connections'][0]['id'],gid)
        self.command(['connections','delete',gid])
        self.assertEqual(self.command(['connections','list'])['connections'],[])
        with self.assertRaises(TerminalError): cli_tools.credential(gid,self.root)

    def test_binding_rejects_other_grant_and_stale_permissions(self):
        one=self.create(); two=self.create()
        token=cli_tools.credential(one['id'],self.root)
        with self.assertRaises(TerminalError): self.request('POST','/api/cli/bind',dict(id=two['id'],token=token),internal=True)
        self.assertEqual(self.request('POST','/api/cli/bind',dict(id=one['id'],token=token),internal=True)['connection_id'],one['id'])
        self.command(['connections','revoke',one['id']])
        with self.assertRaises(TerminalError): self.request('POST','/api/cli/bind',dict(id=one['id'],token=token),internal=True)

    def test_module_off_history_gate_and_original_personas(self):
        path=self.root/'history.json';path.write_text(json.dumps(mcp_cli.template('wechat-history')),encoding='utf-8')
        with self.assertRaises(TerminalError): self.command(['connections','create','--file',str(path)])
        self.assertEqual(self.command(['connections','list'])['connections'],[])
        body=mcp_cli.template('bilibili')|dict(send=True,chat=True,bilibili_share=True,platforms=['qq'],conversations=[['qq','123:group:456']])
        path.write_text(json.dumps(body),encoding='utf-8')
        result=self.command(['connections','create','--file',str(path)])
        scope=self.service.access.authorize(cli_tools.credential(result['id'],self.root),source=False)['scope']
        self.assertFalse(scope['send']);self.assertFalse(scope['bilibili_share']);self.assertEqual(scope['conversations'],[])
        personas=self.command(['personas'])
        self.assertTrue(any(p['id']=='little_whale_v2' for p in personas['personas']))

    def test_real_http_mcp_list_call_denial_and_audit(self):
        result=self.create();gid=result['id']
        self.start_mock.stop()
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        self.service.access.configure(True,port);self.service.start()
        try:
            tools=self.command(['tools','--connection',gid])
            self.assertIn('get_bilibili_status',[t['name'] for t in tools['tools']])
            self.assertNotIn('send_chat_message',[t['name'] for t in tools['tools']])
            # This is local status only, no Bili login, media, or QQ sender.
            status=self.command(['call','get_bilibili_status','--connection',gid])
            self.assertFalse(cli_tools.tool_failed(status),status)
            denied=self.command(['call','send_chat_message','--connection',gid])
            self.assertTrue(cli_tools.tool_failed(denied))
            with self.service.access.connect() as db:
                rows=db.execute('SELECT tool FROM calls WHERE grant_id=?',(gid,)).fetchall()
            self.assertIn('get_bilibili_status',[r[0] for r in rows])
            self.command(['connections','disable',gid])
            with self.assertRaises(TerminalError):self.command(['tools','--connection',gid])
        finally:
            self.service.stop();self.start_mock.start()

    def test_rpc_timeout_and_protocol_do_not_retry(self):
        seen=[]
        def handler(request):
            data=json.loads(request.content);seen.append(data['method'])
            if data['method']=='initialize':return httpx.Response(200,json=dict(result=dict(protocolVersion='2025-03-26')))
            raise httpx.ReadTimeout('private request not echoed')
        with self.assertRaisesRegex(TerminalError,'未知'):
            cli_tools.rpc('http://127.0.0.1:9999/mcp',SECRET,'tools/call',dict(name='mock_write'),transport=httpx.MockTransport(handler))
        self.assertEqual(seen,['initialize','tools/call'])
        seen.clear()
        with self.assertRaises(TerminalError):
            cli_tools.rpc('https://evil.test/mcp',SECRET,'tools/list',{},transport=httpx.MockTransport(handler))
        self.assertEqual(seen,[])

    def test_discovery_bound_to_install_and_restart(self):
        with management_http(self.app) as url:
            cli_runtime.publish(url,self.root)
            with patch('chatlocal.cli_runtime.management_token',return_value=SECRET):
                client=cli_runtime.Client(self.root)
                self.assertEqual(client.root,self.root)
                self.assertEqual(client.request('GET','/api/mcp')['connections'],[])
                other=self.root/'other';(other/'.tmp').mkdir(parents=True)
                cli_runtime.publish(url,other)
                with self.assertRaisesRegex(TerminalError,'其他 Tulpa'):cli_runtime.Client(other)

    def test_parser_json_stdin_and_failure_exit(self):
        stdout=io.StringIO();stderr=io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            code=mcp_cli.main(['connections','template','bilibili','--json'])
        self.assertEqual(code,0);self.assertTrue(json.loads(stdout.getvalue())['bilibili_read'])
        with patch('sys.stdin',io.StringIO('{"关键词":"测试"}')):
            self.assertEqual(mcp_cli.read_json('-'),{'关键词':'测试'})
        stdout=io.StringIO();stderr=io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            code=mcp_cli.main(['--json','missing'])
        self.assertEqual(code,1);self.assertFalse(stdout.getvalue());self.assertFalse(json.loads(stderr.getvalue())['ok'])
        self.assertEqual(mcp_cli.sanitized(dict(token=SECRET,items=[dict(cookie=SECRET,ok=True)])),dict(items=[dict(ok=True)]))

    def test_configuration_commands_keep_original_contracts(self):
        with patch.object(self.fake,'request',return_value={'ok':True}) as request:
            self.command(['groups']);request.assert_called_with('GET','/api/mcp/live-groups')
            self.command(['sl','configure','--accept','reviewed-fingerprint'])
            request.assert_called_with('POST','/api/desktop/snowluma/managed/automatic',dict(accepted=True,fingerprint='reviewed-fingerprint'))
            self.command(['learning','library','--account','100','--group','200'])
            request.assert_called_with('GET','/api/mcp/learning/library',params=dict(account='100',group='200'))
            self.command(['data','accounts']);request.assert_called_with('GET','/api/data/accounts')
            self.command(['data','cancel','job123']);request.assert_called_with('POST','/api/watch-jobs/job123/stop',{})
        with self.assertRaises(TerminalError):self.command(['api','POST','/api/mcp/connections'])
        self.assertTrue(cli_tools.tool_failed({'content':[{'type':'text','text':'{"status":"UNKNOWN"}'}]}))


if __name__=='__main__':
    unittest.main(argv=[sys.argv[0]],verbosity=2)
