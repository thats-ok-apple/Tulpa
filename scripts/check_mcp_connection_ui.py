"""Local management contracts; synthetic data, no QQ/Bilibili/network writes."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(sys.argv[sys.argv.index('--package')+1]).resolve() if '--package' in sys.argv else Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.store import Store
from chatlocal.mcp_access import MCPAccess,AccessDenied,connection_body
from chatlocal.mcp_routes import install_mcp_routes


class Connections(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.environment=patch.dict(os.environ,{k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR')},clear=True);self.environment.start()
        self.network=patch('socket.create_connection',side_effect=AssertionError('No external network allowed'));self.network.start()
        self.store=Store(self.root/'chats.sqlite3');self.app=FastAPI();self.service=install_mcp_routes(self.app,self.store);self.access=self.service.access
        self.client=TestClient(self.app);self.headers={'X-ChatWeave-UI':'1'}
        self.access.configure(True,18777)
        self.start=patch.object(self.service,'start');self.start.start()

    def tearDown(self):
        self.service.stop();self.client.close();self.start.stop();self.network.stop();self.environment.stop();self.temp.cleanup()

    def create(self,**changes):
        body=dict(platforms=[],history_platforms=[],bilibili_read=True,name='')|changes
        r=self.client.post('/api/mcp/connections',json=body,headers=self.headers)
        self.assertEqual(r.status_code,200,r.text);return r.json()

    def test_pause_resume_delete_restart(self):
        g=self.create();grant=self.access.authorize(g['token']);self.assertRegex(g['name'],r'^mcp-[a-f0-9]{12}$')
        cancel=threading.Event();self.service.pending[(g['id'],'fixture')]=cancel
        url='/api/mcp/connections/'+g['id']
        self.assertEqual(self.client.put(url+'/state',json={'enabled':False},headers=self.headers).status_code,200)
        self.assertTrue(cancel.is_set())
        with self.assertRaises(AccessDenied):self.access.authorize(g['token'])
        revision=self.access.list()['connections'][0]['revision']
        self.access.set_enabled(g['id'],False);self.assertEqual(self.access.list()['connections'][0]['revision'],revision)
        reloaded=MCPAccess(self.store)
        with self.assertRaises(AccessDenied):reloaded.authorize(g['token'])
        self.assertEqual(self.client.put(url+'/state',json={'enabled':True},headers=self.headers).status_code,200)
        self.assertEqual(self.access.authorize(g['token'])['id'],g['id'])
        with self.assertRaises(AccessDenied):self.access.ensure_revision(grant)
        self.assertEqual(self.client.delete(url,headers=self.headers|{'Content-Type':'application/json'}).status_code,200)
        self.assertEqual(self.access.list()['connections'],[])
        with self.assertRaises(AccessDenied):self.access.authorize(g['token'])
        self.assertEqual(self.client.put(url+'/state',json={'enabled':True},headers=self.headers).status_code,400)

    def test_disabled_modules_ignore_all_stale_fields(self):
        g=self.create(modules=dict(qq=False,wechat=False,bilibili=True),platforms=['qq','wechat'],history_platforms=['qq','wechat'],conversations=[['qq','111:group:222'],['wechat','old']],send=True,chat=True,manage=True,onebot=True,media=True,prepare=True,voice=True,chat_images=True,chat_reactions=True,bilibili_share=True)
        scope=self.access.authorize(g['token'])['scope']
        self.assertEqual(scope['platforms'],[]);self.assertEqual(scope['history_platforms'],[]);self.assertEqual(scope['conversations'],[])
        self.assertTrue(scope['bilibili_read'])
        self.assertFalse(any(scope[k] for k in ('media','prepare','voice','onebot','chat','send','manage','chat_images','chat_reactions','bilibili_share')))
        body=connection_body(dict(modules=dict(qq=True,wechat=False,bilibili=False),platforms=['qq','wechat'],history_platforms=[],conversations=[['qq','111:group:222'],['wechat','old']],send=True,bilibili_read=True))
        self.assertEqual(body['platforms'],['qq']);self.assertFalse(body['bilibili_read']);self.assertEqual(body['conversations'],[['qq','111:group:222']])

    def test_import_only_checked_history_and_no_name_collision(self):
        first=self.create();second=self.create();self.assertNotEqual(first['name'],second['name'])
        r=self.client.post('/api/mcp/connections',json=dict(platforms=['wechat'],history_platforms=['wechat'],all_conversations=True),headers=self.headers)
        self.assertEqual(r.status_code,400);self.assertIn('导入',r.json()['detail'])

    def test_concurrent_pause_idempotent(self):
        g=self.create()
        with ThreadPoolExecutor(4) as pool:rows=list(pool.map(lambda _:self.access.set_enabled(g['id'],False),range(4)))
        self.assertEqual(sum(r['changed'] for r in rows),1)
        self.assertEqual(self.access.list()['connections'][0]['revision'],1)

    def test_local_library_index_without_grants_or_online_account(self):
        learning=self.service.tools.chat.learning
        with learning.store.db() as db:
            for scope in ('qq:111:group:222','qq:111:group:333','qq:999:group:222'):
                db.execute('INSERT INTO language_libraries VALUES(?,1,0,0,0,0)',(scope,))
            db.execute("INSERT INTO expressions VALUES('a','qq:111:group:222','合成场景','合成群友表达',1,0,1,'degraded')")
            db.execute("INSERT INTO expressions VALUES('b','qq:999:group:222','另一个账号','不能混入',1,0,1,'degraded')")
        self.access.configure(False,18777)
        accounts=self.client.get('/api/mcp/learning/accounts').json()['accounts'];self.assertEqual(accounts,[dict(account='111',groups=2),dict(account='999',groups=1)])
        groups=self.client.get('/api/mcp/learning/groups?account=111').json()['groups'];self.assertEqual(len(groups),2)
        result=self.client.get('/api/mcp/learning/library?account=111&group=222').json();self.assertEqual([e['id'] for e in result['expressions']],['a'])
        r=self.client.post('/api/mcp/learning/library',json=dict(account='111',group='222',change=dict(kind='expressions',id='b',enabled=False)),headers=self.headers);self.assertEqual(r.status_code,400)
        r=self.client.post('/api/mcp/learning/library',json=dict(account='111',group='222',change=dict(kind='expressions',id='a',enabled=False)),headers=self.headers);self.assertEqual(r.status_code,200)
        self.assertFalse(r.json()['expressions'][0]['enabled'])
        with self.assertRaises(AccessDenied):learning.authorized_library(dict(id='not-authorized'),'111:group:222')
        self.assertEqual(self.client.get('/api/mcp/learning/library?account=111&group=999').status_code,400)

    def test_local_routes_enforce_origin_and_mutation_header(self):
        g=self.create();url='/api/mcp/connections/'+g['id']
        self.assertEqual(self.client.put(url+'/state',json={'enabled':False}).status_code,403)
        self.assertEqual(self.client.delete(url,headers={'Content-Type':'application/json','X-ChatWeave-UI':'1','Origin':'https://outside.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/mcp/learning/accounts',headers={'Origin':'https://outside.example'}).status_code,403)
        self.assertEqual(self.access.authorize(g['token'])['id'],g['id'])


if __name__=='__main__':unittest.main(argv=[sys.argv[0]])
