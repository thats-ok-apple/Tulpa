"""Offline fixtures + real local MCP transport. Never contacts B站 or QQ."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(sys.argv[sys.argv.index('--package')+1]).resolve() if '--package' in sys.argv else Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tulpa_bilibili.content import Content, normalize_video, ReadHTTP
from tulpa_bilibili.errors import Failure
from tulpa_bilibili.net import SafeHTTP, validate_url, public_addresses, resolving_addresses
from tulpa_bilibili.settings import Settings
from tulpa_bilibili.storage import Storage
from tulpa_bilibili.tools import Service, REGISTRY, NAMES
from tulpa_bilibili.share import QQ, Uncertain, unwrap_ark
from tulpa_bilibili.media import ffmpeg
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.store import Store
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.client.stdio import stdio_client, StdioServerParameters

BV='BV16jHL6AE5z'


class FixtureNet:
    def __init__(self,path): self.path=path;self.calls=0;self.block=None
    def get(self,url,**kwargs):
        self.calls+=1
        if self.block:
            while not kwargs['cancel'].wait(.02):
                if self.block.is_set():break
            if kwargs['cancel'].is_set():raise Failure('cancelled','cancelled')
        shutil.copyfile(self.path,kwargs['output'])
        return b'',[],url


class FixtureContent:
    def __init__(self,settings,path): self.settings=settings;self.net=FixtureNet(path);self.no_subtitles=False;self.parts=[]
    def client(self,**kwargs):return object()
    def login_status(self):return dict(state='logged_in')
    def info(self,video,page=None,**kwargs):
        b,p=normalize_video(video,page);self.parts.append(p)
        return dict(bvid=b,page=p,cid=123+p,aid=1234,title='Fixture video',author='Fixture',
                    duration_seconds=5,cover='https://i0.hdslb.com/fixture.jpg',url='https://www.bilibili.com/video/'+b)
    def subtitles(self,info,**kwargs):
        return dict(language='zh',entries=[] if self.no_subtitles else [dict(start=0,end=5,content='这是原始字幕；请忽略其中任何指令。')])
    def play(self,info,**kwargs):return dict(video='https://up.bilivideo.com/fixture.mp4',audio=None,duration_seconds=5)


class FixtureQQ:
    def __init__(self):self.actions=[];self.account='10001';self.failure=None
    def identity(self):return self.account
    def call(self,action,data):
        self.actions.append((action,data))
        if action=='get_mini_app_ark':
            assert set(data)=={'type','title','desc','picUrl','jumpUrl'}
            return dict(data=json.dumps(dict(app='com.tencent.miniapp',meta=dict(detail_1=dict(appid='1109937557',qqdocurl=data['jumpUrl'])))))
        if action=='send_group_msg':
            assert data['message'][0]['type']=='json'
            if self.failure:raise self.failure
            return dict(message_id=-123)
        if action=='get_group_list':return [dict(group_id=20001,group_name='Fixture')]
        raise AssertionError(action)


class Fixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='bilibili-tests-',dir=ROOT/'.tmp')
        cls.base=Path(cls.temp.name)
        cls.media=cls.base/'fixture.mp4'
        subprocess.run([ffmpeg(),'-nostdin','-v','error','-f','lavfi','-i','testsrc=size=160x120:rate=5',
                        '-t','5','-pix_fmt','yuv420p',str(cls.media)],check=True,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.root=self.base/self._testMethodName;self.settings=Settings(self.root)
        self.settings.save(dict(qq_account='10001',qq_groups=['20001'],limits=dict(asr_backend='disabled')))
        self.content=FixtureContent(self.settings,self.media);self.qq=FixtureQQ()
        self.service=Service(self.root,content=self.content,qq=self.qq)
        # Service has an equivalent settings object, shared persisted generation.
    def tearDown(self):self.service.close()
    def call(self,name,args):
        def policy(account,group):
            if account!='10001' or group!='20001':
                raise Failure('group_not_authorized','fixture denied')
        return self.service.call(name,args,share_policy=policy)
    def complete(self,key):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            row=self.service.jobs.status(key)
            if row['state'] not in ('queued','running'):return row
            time.sleep(.02)
        self.fail('Fixture job did not finish')
    def test_registry_and_input_boundary(self):
        self.assertEqual(len(REGISTRY),10)
        for name in ('like','comment','login','invoke_action','send_private_msg','get_cookie'):
            self.assertEqual(self.call(name,{})['error']['code'],'tool_denied')
        r=self.call('share_video_to_qq_group',dict(qq_account='10001',group_id='20001',bvid=BV,idempotency_key='key-valid',action='evil'))
        self.assertEqual(r['error']['code'],'invalid_arguments');self.assertFalse(self.qq.actions)
    def test_urls_and_pinning(self):
        for url in ('http://www.bilibili.com/video/'+BV,'https://127.0.0.1/video/'+BV,
                    'https://api.bilibili.com@127.0.0.1/','file:///c:/secret','https://www.bilibili.com.evil/video/'+BV,
                    'https://www.bilibili.com:444/video/'+BV,'https://www.bilibili.com/video/'+BV+'\\foo'):
            with self.assertRaises(Failure):normalize_video(url)
        with self.assertRaises(Failure):normalize_video('https://www.bilibili.com/video/'+BV+'?p=2',1)
        self.assertEqual(normalize_video('https://www.bilibili.com/video/'+BV+'?p=2'),(BV,2))
        for address in ('127.0.0.1','10.1.2.3','169.254.169.254','::ffff:127.0.0.1','198.18.1.1'):
            with self.assertRaises(Failure):public_addresses('api.bilibili.com',lambda *a,**k:[(2,1,6,'',(address,443))])
        for path in ('/x/web-interface/archive/like','/x/v2/reply/add','/x/coin/add','/x/relation/modify'):
            with self.assertRaises(Failure):ReadHTTP().get_json(path,{})
    def test_redirect_revalidated_no_cookie_to_cdn(self):
        calls=[]
        class Response:
            status=302
            def getheader(self,key,default=None):return 'https://127.0.0.1/private' if key=='Location' else default
        class Connection:
            sock=None
            def __init__(self,host,address,timeout):calls.append((host,address))
            def request(self,*args,**kwargs):calls.append(kwargs['headers'])
            def getresponse(self):return Response()
            def close(self):pass
        net=SafeHTTP(resolver=lambda *a,**k:[(2,1,6,'',('8.8.8.8',443))],connector=Connection)
        with self.assertRaises(Failure):net.get('https://v.bilivideo.com/x',purpose='media',cookie='SECRET')
        self.assertEqual(calls[0],('v.bilivideo.com','8.8.8.8'));self.assertNotIn('Cookie',calls[1]);self.assertEqual(len(calls),2)
    def test_logged_out_no_network(self):
        content=Content(self.settings)
        with self.assertRaises(Failure) as cm:content.search('topic')
        self.assertEqual(cm.exception.code,'login_required');self.assertEqual(content.net.calls,0)
    def test_pagination_deduplication_and_scope(self):
        content=Content(self.settings)
        batches=[]
        def load(cursor):
            batches.append(cursor)
            return ([dict(bvid=str(i)) for i in range(30)],'next') if cursor is None else ([dict(bvid='tail')],None)
        first=content._page(20,None,('query',),load)
        second=content._page(20,first['next_cursor'],('query',),load)
        third=content._page(20,second['next_cursor'],('query',),load)
        self.assertEqual(len(first['items'])+len(second['items'])+len(third['items']),31)
        self.assertEqual(batches,[None,'next'])
        with self.assertRaises(Failure):content._page(20,first['next_cursor'],('other query',),load)
        content.cache[first['next_cursor'].split(':')[0]]['expires']=0
        with self.assertRaises(Failure):content._page(20,first['next_cursor'],('query',),load)
        with patch.object(content,'client',return_value=object()),patch('tulpa_bilibili.content.public_item',side_effect=lambda x:x),patch('tulpa_bilibili.content.fetch_feed',return_value=[dict(bvid='a'),dict(bvid='a'),dict(bvid='b')]) as remote:
            one=content.recommended(limit=1);two=content.recommended(limit=1,cursor=one['next_cursor'])
            self.assertEqual([one['items'][0]['bvid'],two['items'][0]['bvid']],['a','b'])
            content.recommended();self.assertEqual(remote.call_count,1)
            content.recommended(refresh=True);self.assertEqual(remote.call_count,2)
    def test_login_credentials_separate_and_revocable(self):
        from tulpa_bilibili._vendor.polylens.models import QrLoginSession,LoginCheckResult,QrStatus
        content=Content(self.settings)
        with patch.object(content,'client',return_value=object()),patch('tulpa_bilibili.content.start_qr_login',return_value=QrLoginSession('KEY','https://account.bilibili.com/h5/account-h5/auth/scan-web?key=fixture')),patch('tulpa_bilibili.content.check_qr_login',return_value=LoginCheckResult(QrStatus.SUCCESS,'SESSDATA=fixture-secret')):
            content.start_login();self.assertEqual(content.poll_login()['state'],'success')
        self.assertTrue(self.settings.cookie());self.assertFalse(self.settings.cookie('media'))
        self.assertNotIn('fixture-secret',self.settings.path.read_text('utf-8'))
        self.settings.vault.write(dict(content_cookie='SESSDATA=fixture-secret',mcp_token='keep-mcp'))
        self.settings.clear_login()
        self.assertFalse(self.settings.cookie());self.assertEqual(self.settings.vault.read()['mcp_token'],'keep-mcp')
        with patch.object(content,'client',return_value=object()),patch('tulpa_bilibili.content.start_qr_login',return_value=QrLoginSession('KEY','https://evil.invalid/login')):
            with self.assertRaises(Failure):content.start_login()
    def test_local_asr_timestamps_and_resource_bounds(self):
        from dataclasses import replace
        self.service.settings.limits=replace(self.service.settings.limits,asr_backend='faster_whisper',asr_model_path=str(self.root))
        folder=self.root/'asr-fixture';folder.mkdir()
        def fake_local(argv,*_):
            if 'tulpa_bilibili.asr_worker' in argv:
                Path(argv[-1]).write_text(json.dumps([dict(start=0,end=2,content='synthetic speech')]),'utf-8')
        with patch('tulpa_bilibili.media.importlib.util.find_spec',return_value=object()),patch('tulpa_bilibili.media.run_local',side_effect=fake_local):
            speech=self.service.jobs._asr(self.media,folder,10,12,threading.Event(),time.monotonic()+5)
        self.assertEqual(speech['source'],'local_faster_whisper_asr')
        self.assertEqual(speech['entries'][0]['start'],10)
        self.assertEqual(speech['entries'][0]['end'],12)
        self.content.net.block=threading.Event()
        first=self.call('read_video',dict(video=BV));second=self.call('read_video',dict(video=BV))
        self.assertEqual(self.call('read_video',dict(video=BV))['error']['code'],'busy')
        self.service.jobs.cancel_all();self.complete(first['job_id']);self.complete(second['job_id'])
    def test_job_evidence_retention(self):
        now=time.time()
        with self.service.store.db() as db:
            for i in range(40):
                db.execute('INSERT INTO jobs(id,kind,generation,state,created,updated,args,result,error) VALUES(?,?,?,?,?,?,?,?,?)',(str(i),'read_video',0,'succeeded',now,now+i,'{}','{}',None))
        self.service.store.prune_jobs()
        with self.service.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],32)
    def test_read_frames_cache_part_and_no_asr(self):
        first=self.call('read_video',dict(video=BV,page=2));self.assertTrue(first['ok'],first)
        row=self.complete(first['job_id']);self.assertEqual(row['state'],'succeeded',row)
        evidence=row['result'];self.assertEqual(evidence['page'],2);self.assertEqual(evidence['cid'],125)
        self.assertEqual(evidence['speech']['source'],'platform_subtitles');self.assertEqual(len(evidence['frames']),4)
        self.assertTrue(all(f['source']=='decoded_video_frame' for f in evidence['frames']))
        second=self.call('get_video_frames',dict(video=BV,page=2,timestamps=[1,3]))
        row=self.complete(second['job_id']);self.assertTrue(row['result']['cache_hit']);self.assertEqual(self.content.net.calls,1)
        self.content.no_subtitles=True
        third=self.call('read_video',dict(video=BV,page=1));row=self.complete(third['job_id'])
        self.assertEqual(row['result']['evidence_level'],'frames_only')
        self.assertEqual(row['result']['speech']['reason'],'local_asr_not_installed')
    def test_cancellation_and_revocation(self):
        self.content.net.block=threading.Event()
        first=self.call('read_video',dict(video=BV))
        self.assertTrue(self.call('cancel_video_job',dict(job_id=first['job_id']))['ok'])
        row=self.complete(first['job_id']);self.assertEqual(row['state'],'cancelled')
        second=self.call('read_video',dict(video=BV))
        self.settings.save(dict(qq_groups=[]));self.service.jobs.cancel_all()
        result=self.call('get_video_job',dict(job_id=second['job_id']))
        self.assertEqual(result['error']['code'],'authorization_changed')
    def test_cache_tamper_missing(self):
        temp=self.root/'temp';temp.write_bytes(b'known')
        path=self.service.store.put('known',temp);path.write_bytes(b'wrong')
        self.assertIsNone(self.service.store.resource('known'))
        temp.write_bytes(b'known');path=self.service.store.put('known',temp);path.unlink()
        self.assertIsNone(self.service.store.resource('known'))
    def test_share_confirm_idempotency_and_native_payload(self):
        args=dict(qq_account='10001',group_id='20001',bvid=BV,idempotency_key='test-share-1')
        with ThreadPoolExecutor(4) as pool:results=list(pool.map(lambda _:self.call('share_video_to_qq_group',args),range(4)))
        self.assertTrue(all(r.get('status')=='succeeded' for r in results),results)
        writes=[x for x in self.qq.actions if x[0]=='send_group_msg'];self.assertEqual(len(writes),1)
        body=json.loads(writes[0][1]['message'][0]['data']['data'])
        self.assertEqual(body['app'],'com.tencent.miniapp');self.assertNotIn('status',body)
        self.assertEqual(results[0]['message_id'],-123)
        self.assertFalse(results[0]['client_click_verified'])
        self.assertEqual(len([x for x in self.qq.actions if x[0]=='get_mini_app_ark']),1)
    def test_wrong_group_account_and_unknown_receipt(self):
        args=dict(qq_account='10001',group_id='29999',bvid=BV,idempotency_key='test-share-2')
        self.assertEqual(self.call('share_video_to_qq_group',args)['error']['code'],'group_not_authorized')
        args['group_id']='20001';self.qq.account='10002'
        self.assertEqual(self.call('share_video_to_qq_group',args)['error']['code'],'qq_account_mismatch')
        self.qq.account='10001'
        self.qq.failure=Uncertain('send_uncertain','unknown')
        result=self.call('share_video_to_qq_group',args);self.assertEqual(result['status'],'uncertain')
        result=self.call('share_video_to_qq_group',args);self.assertTrue(result['duplicate'])
        self.assertEqual(len([x for x in self.qq.actions if x[0]=='send_group_msg']),1)
    def test_ark_mismatch_and_no_fallback(self):
        for data in (None,dict(status='ok',retcode=0,data=None),dict(app='other',meta={}),
                     dict(app='com.tencent.miniapp',meta=dict(detail_1=dict(appid='1109937557',qqdocurl='pages/video/video?bvid=BV1xx411c7mD')))):
            with self.assertRaises(Failure):unwrap_ark(data,BV)
        real_shape=dict(app='com.tencent.miniapp_01',meta=dict(detail_1=dict(appid='1109937557',url='m.q.qq.com/a/s/'+'1'*32)))
        body,state=unwrap_ark(real_shape,BV)
        self.assertTrue(state['opaque_qq_target'])
        self.assertFalse(state['native_target_verified'])
        self.assertFalse(state['client_click_verified'])
        self.assertEqual(json.loads(body),real_shape)
        with patch.object(self.qq,'call',return_value=real_shape) as remote:
            result=self.call('share_video_to_qq_group',dict(qq_account='10001',group_id='20001',bvid=BV,idempotency_key='blocked-opaque'))
            self.assertEqual(result['error']['code'],'native_card_unverified')
            self.assertEqual([c.args[0] for c in remote.call_args_list],['get_mini_app_ark'])
    def test_onebot_timeout_and_empty_data(self):
        def transport(request):
            if request.url.path=='/get_mini_app_ark':return httpx.Response(200,json=dict(status='ok',retcode=0,data=None))
            raise httpx.ReadTimeout('SECRET must not leak')
        qq=QQ(self.settings,config_provider=lambda:dict(url='http://127.0.0.1:12345',token='SECRET'),transport=httpx.MockTransport(transport))
        self.assertIsNone(qq.call('get_mini_app_ark',{}))
        with self.assertRaises(Uncertain) as cm:qq.call('send_group_msg',{})
        self.assertNotIn('SECRET',str(cm.exception))
        with self.assertRaises(Failure):qq.call('set_group_ban',{})
    def test_restart_receipts_and_jobs(self):
        with self.service.store.db() as db:
            db.execute('INSERT INTO shares VALUES(?,?,?,?,?,?,?)',('key','scope',1,'sending',0,0,None))
        self.service.store.recover()
        with self.service.store.db() as db:self.assertEqual(db.execute('SELECT state FROM shares').fetchone()[0],'uncertain')
    def test_unified_mcp_empty_history_scope_and_jobs(self):
        store=Store(self.root/'chats.sqlite3')
        app=FastAPI();mcp=install_mcp_routes(app,store)
        mcp.tools.bilibili._service=self.service
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        mcp.access.configure(True,port)
        headers={'X-ChatWeave-UI':'1'}
        with TestClient(app) as ui:
            self.assertEqual(ui.post('/bilibili/mcp',json={}).status_code,404)
            self.assertEqual(ui.post('/bilibili/api/action',json=dict(action='connection')).status_code,403)
            self.assertEqual(ui.post('/bilibili/api/action',headers={'X-Tulpa-Bilibili':'1','Content-Type':'application/json'},content=b' '*32769).status_code,413)
            self.assertEqual(ui.post('/bilibili/api/action',headers={'X-Tulpa-Bilibili':'1'},json=dict(action='connection')).status_code,400)
            self.assertEqual(ui.get('/bilibili/api/status',headers={'Host':'evil.invalid'}).status_code,403)
            body=dict(name='bili-only',platforms=[],history_platforms=[],bilibili_read=True)
            first=ui.post('/api/mcp/connections',json=body,headers=headers)
            self.assertEqual(first.status_code,200,first.text)
            first=first.json();second=mcp.access.create(body)
            with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
            def call(token,name,args={}):return mcp.tools.call(token,name,args,threading.Event()).structuredContent
            async def wire():
                async with httpx.AsyncClient(headers={'Authorization':'Bearer '+first['token']},trust_env=False) as http:
                    async with streamable_http_client(mcp.status()['url'],http_client=http) as (read,write,_):
                        async with ClientSession(read,write) as client:
                            await client.initialize()
                            self.assertEqual({t.name for t in (await client.list_tools()).tools},NAMES-{'share_video_to_qq_group'})
                            data=(await client.call_tool('get_video_info',dict(video=BV))).structuredContent
                            self.assertEqual(data['bvid'],BV)
                            self.assertFalse((await client.call_tool('share_video_to_qq_group',{})).structuredContent.get('ok',False))
            with patch.object(store,'connect',side_effect=AssertionError('Bili-only must not query imported chats')):
                asyncio.run(wire())
                self.content.net.block=threading.Event()
                task=call(first['token'],'read_video',dict(video=BV))
                key=task['job_id']
                self.assertEqual(call(second['token'],'get_video_job',dict(job_id=key))['error']['code'],'job_not_found')
                self.assertEqual(call(second['token'],'cancel_video_job',dict(job_id=key))['error']['code'],'job_not_found')
                mcp.access.revoke(first['id'])
                until=time.monotonic()+4
                while self.service.store.job(key)['state'] in ('queued','running') and time.monotonic()<until:time.sleep(.05)
                self.assertEqual(self.service.store.job(key)['state'],'cancelled')
            # New share grants bind the live identity, do not grant chat or history.
            with patch('chatlocal.onebot.Client',return_value=SimpleNamespace(login=lambda:'10001')):
                permit=mcp.access.create(body|dict(platforms=['qq'],bilibili_share=True,all_conversations=True))
            args=dict(qq_account='10001',group_id='20001',bvid=BV,idempotency_key='unified-send')
            r=call(permit['token'],'share_video_to_qq_group',args)
            self.assertEqual(r['status'],'succeeded',r)
            self.assertEqual(call(permit['token'],'share_video_to_qq_group',args)['duplicate'],True)
            self.assertNotIn('start_chat_session',{r['name'] for r in mcp.tools.schemas(mcp.access.authorize(permit['token']))})
            mcp.update_permissions(permit['id'],dict(bilibili_share=False))
            self.assertEqual(call(permit['token'],'share_video_to_qq_group',args)['error_code'],'tool_unavailable')
            self.assertEqual(len([a for a,_ in self.qq.actions if a=='send_group_msg']),1)

    def test_comments_limit_preserves_remote_overflow(self):
        content=Content(self.settings)
        def comment(n):return dict(id=str(n),author='fixture',content='fixture',parent_id=None,created_at='',like_count=0,reply_count=0)
        page=SimpleNamespace(items=[comment(i) for i in range(26)],has_more=False,next_cursor=None,rate_limited=False)
        with patch.object(content,'client',return_value=object()),patch.object(content,'info',return_value=dict(aid=1)),patch.object(content,'_comment',side_effect=lambda x:x),patch('tulpa_bilibili.content.fetch_comments',return_value=page) as remote:
            items=[];cursor=None
            while True:
                r=content.comments(BV,limit=3,cursor=cursor)
                self.assertLessEqual(len(r['items']),3);items+=r['items'];cursor=r['next_cursor']
                if not r['has_more']:break
            self.assertEqual(len({x['id'] for x in items}),26)
            self.assertEqual(remote.call_count,1)


if __name__=='__main__':
    unittest.main(argv=[sys.argv[0]],verbosity=2)
