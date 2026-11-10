"""Isolated rolling-window, official downloader and deletion/ownership checks."""
import hashlib,io,json,sys,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from chatlocal import media,image_remote,sync
from chatlocal.store import Store
from chatlocal.qq_image_refs import clean_url,from_fields
from chatlocal.image_remote import Downloader,seed,RKeys,MARK
from chatlocal.image_index import ImageIndexer
from chatlocal.agent_tools import ChatTools
from chatlocal.retrieval import Plan
import httpx


def main():
    assert clean_url('https://multimedia.nt.qq.com.cn/download?appid=1407&rkey=SECRET&fileid=a')=='https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=a'
    for value in ('https://127.0.0.1/a','https://evil.invalid/a','https://multimedia.nt.qq.com.cn.evil.invalid/a','https://u:p@multimedia.nt.qq.com.cn/a','https://multimedia.nt.qq.com.cn:999/a','https://multimedia.nt.qq.com.cn/a\n'):
        assert clean_url(value) is None
    assert from_fields({45816:[('bytes',b'evil.invalid')],45802:[('bytes',b'/download')]})==[]
    md5 = 'ab' * 16
    prefix = '/gchatpic_new/0/0-123-' + md5
    fields = {45406:[('bytes',bytes.fromhex(md5))],
              45802:[('bytes',(prefix+'/198?term=2').encode())],
              45804:[('bytes',(prefix+'/0?is_origin=1').encode())]}
    assert from_fields(fields)[0]=='https://gchat.qpic.cn'+prefix+'/0?is_origin=1'
    fields[45406]=[('bytes',bytes(16))]
    assert from_fields(fields)==[]

    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='download-check-') as name:
        folder=Path(name);(folder/'.tmp').mkdir();data=folder/'data';data.mkdir();store=Store(data/'messages.sqlite3')
        stream=io.BytesIO();Image.new('RGB',(40,25),'blue').save(stream,'PNG');raw=stream.getvalue();md5=hashlib.md5(raw).hexdigest()
        missing=media.unavailable(md5=md5,filename='fixture.png',remote_urls=['https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=fixture&spec=0&rkey=DO_NOT_PERSIST'])
        def put(sid,age=0):
            path=folder/(sid+'.json');path.write_text(json.dumps([dict(platform='qq',conversation_id='acc:group:1',conversation_type='group',conversation='fixture',sender='test',sender_id='test',source_id=sid,timestamp=int((time.time()-age)*1000),content='original',media=[missing])]))
            store.import_file(path)
            with store.connect() as db:return db.execute('SELECT id FROM messages WHERE source_id=?',(sid,)).fetchone()[0]
        with patch.object(media,'DATA',data),patch.object(image_remote,'DATA',data),patch.object(sync,'ROOT',folder):
            first=put('first');second=put('second');old=put('old',8*86400)
            with store.connect() as db:
                a=json.loads(db.execute('SELECT media_json FROM messages WHERE id=?',(first,)).fetchone()[0]);a[0]['source_index']=7
                db.execute('UPDATE messages SET media_json=? WHERE id=?',(json.dumps(a),first))
                assert 'DO_NOT_PERSIST' not in json.dumps(a)
            seed(store);worker=Downloader(store)
            assert not worker.process_one(lambda *_: (_ for _ in ()).throw(AssertionError('disabled worker downloaded')))
            with store.connect() as db:
                db.execute("INSERT OR REPLACE INTO client_settings VALUES('image_remote',?)",(json.dumps(dict(enabled=True,days=7)),))
                assert not db.execute('SELECT 1 FROM image_download_jobs WHERE message_id=?',(old,)).fetchone()
            calls=[]
            def get(url,cancel):
                assert url.startswith('https://multimedia.nt.qq.com.cn/') and 'rkey=fixture_key' in url
                calls.append(url);return raw
            assert worker.process_one(get,keys={'20':'fixture_key'})
            assert worker.error is None,worker.error
            assert worker.process_one(lambda *_: (_ for _ in ()).throw(AssertionError('digest cache not reused')),keys={})
            assert worker.error is None,worker.error
            assert len(calls)==1
            assert store.message(first)['media'][0]['source_index']==7 and store.message(first)['content']=='original'
            assert store.message(second)['media'][0]['status']=='available'
            with store.connect() as db:
                assert dict(db.execute('SELECT status,count(*) FROM image_download_jobs GROUP BY status'))=={'ready':2}
            ocr=ImageIndexer(store)
            assert ocr.process_one(lambda _:dict(ocr='REMOTE_IMAGE_731',text_confidence=.99))
            assert ChatTools(store,Plan(platforms=['qq'])).execute('search_messages',dict(query='REMOTE_IMAGE_731'))['match_count']==1
            # Near-expiry backfill gets a turn before newly seeded background work.
            expiring = put('expiring', 6.5*86400)
            fresh = put('fresh')
            seed(store)
            assert worker.process_one(lambda *_: (_ for _ in ()).throw(AssertionError('digest cache not reused')))
            with store.connect() as db:
                assert db.execute('SELECT status FROM image_download_jobs WHERE message_id=?',(expiring,)).fetchone()[0]=='ready'
                assert db.execute('SELECT status FROM image_download_jobs WHERE message_id=?',(fresh,)).fetchone()[0]=='pending'
                db.execute('UPDATE messages SET timestamp=? WHERE id IN (?,?)',((time.time()-8*86400)*1000,expiring,fresh))
            # Hash mismatch cannot create an image entry or return wrong pixels.
            bad=put('bad');seed(store)
            with store.connect() as db:db.execute('DELETE FROM image_download_cache')
            assert worker.process_one(lambda *_:b'wrong image',keys={'20':'fixture_key'})
            assert store.message(bad)['media'][0]['status']=='unavailable'
            with store.connect() as db:assert db.execute('SELECT error_code FROM image_download_jobs WHERE message_id=?',(bad,)).fetchone()[0]=='content_hash_mismatch'
            # The first explicit read bypasses backoff once. Subsequent reads respect it.
            bad_row = store.message(bad)
            first_retry = image_remote.prioritize(store, bad_row, 0)
            assert first_retry['status']=='retry' and first_retry['next_try']==0
            assert worker.process_one(lambda *_:b'wrong image',keys={'20':'fixture_key'})
            repeated_retry = image_remote.prioritize(store, bad_row, 0)
            assert repeated_retry['next_try']>time.time()
            # Exact-message download stays in memory even with bulk work disabled.
            with store.connect() as db:
                db.execute("UPDATE client_settings SET value=? WHERE name='image_remote'",
                           (json.dumps(dict(enabled=False,on_demand=True,message_account='acc')),))
                jobs_before=[tuple(row) for row in db.execute('SELECT * FROM image_download_jobs')]
            files_before=set((data/'media').rglob('*'))
            with patch.object(image_remote._request_rkeys,'get',return_value={'20':'fixture_key'}), \
                 patch('chatlocal.mcp_chat_media.download',side_effect=lambda url,cancel,**_:get(url,cancel)) as fetch:
                assert image_remote.read_on_demand(store,bad_row,0,threading.Event())==raw
                assert image_remote.read_on_demand(store,dict(bad_row,conversation_id='other:group:1'),0,threading.Event()) is None
                assert image_remote.read_on_demand(store,bad_row,7,threading.Event()) is None
                fetch.assert_called_once()
            with store.connect() as db:
                assert [tuple(row) for row in db.execute('SELECT * FROM image_download_jobs')]==jobs_before
                db.execute("UPDATE client_settings SET value=? WHERE name='image_remote'",
                           (json.dumps(dict(enabled=True,days=7)),))
            assert files_before==set((data/'media').rglob('*'))
            # An unavailable signing service cannot block a declared legacy original.
            legacy = put('legacy')
            with store.connect() as db:
                desc = json.loads(db.execute('SELECT media_json FROM messages WHERE id=?',(legacy,)).fetchone()[0])
                desc[0]['metadata']['remote_urls'].append('https://gchat.qpic.cn/gchatpic_new/0/0-123-'+md5+'/0')
                db.execute('UPDATE messages SET media_json=? WHERE id=?',(json.dumps(desc),legacy))
            seed(store)
            def legacy_get(url, cancel):
                assert url.startswith('https://gchat.qpic.cn/') and 'rkey=' not in url
                return raw
            with patch.object(worker.rkeys, 'get', side_effect=ValueError('credential_offline')) as signing:
                assert worker.process_one(legacy_get)
                signing.assert_not_called()
            assert store.message(legacy)['media'][0]['status']=='available'
            with store.connect() as db:
                db.execute('DELETE FROM image_download_cache')
                db.execute('UPDATE messages SET timestamp=? WHERE id=?',((time.time()-8*86400)*1000,legacy))
            # Deleted during IO: no resurrection, no queued OCR for that row.
            race=put('race');seed(store)
            def deleting(*_):
                with store.connect() as db:
                    db.execute('DELETE FROM provenance WHERE message_id=?',(race,))
                    db.execute('DELETE FROM messages WHERE id=?',(race,))
                return raw
            assert worker.process_one(deleting,keys={'20':'fixture_key'})
            assert store.message(race) is None
            with store.connect() as db:assert not db.execute('SELECT 1 FROM image_ocr WHERE message_id=?',(race,)).fetchone()
            # Old remote-owned cache can expire; native/preexisting cache stays.
            digest=hashlib.sha256(raw).hexdigest();path=media.media_path(store.message(first)['media'][0]);assert path
            with store.connect() as db:
                db.execute('UPDATE messages SET timestamp=? WHERE id IN (?,?)',((time.time()-8*86400)*1000,first,second))
                db.execute('INSERT OR REPLACE INTO image_download_cache VALUES(?,?,?,?,?)',(digest,str(path.relative_to(ROOT)),1,0,md5))
            worker.cleanup();assert not path.exists()
            kept=media.cache_image(raw,recovered_by='native-cache')
            protected=put('protected')
            with store.connect() as db:
                db.execute('UPDATE messages SET media_json=?,timestamp=? WHERE id=?',(json.dumps([kept]),(time.time()-8*86400)*1000,protected))
                db.execute('INSERT OR REPLACE INTO image_download_cache VALUES(?,?,?,?,?)',(digest,kept['local_path'],1,0,md5))
            worker.cleanup();assert media.media_path(kept)
            # QQ can deliver an MPO original. Keep its bytes and expose its still frames.
            from chatlocal.vision import frames_for
            mpo = io.BytesIO()
            Image.new('RGB',(24,18),'red').save(
                mpo, format='MPO', save_all=True,
                append_images=[Image.new('RGB',(24,18),'blue')])
            mpo_item = media.cache_image(mpo.getvalue(), load_stickers=False)
            mpo_path = media.verified_media_path(mpo_item)
            assert mpo_item['format']=='mpo' and mpo_item['animated'] is False
            assert mpo_path and mpo_path.read_bytes()==mpo.getvalue()
            frames, count = frames_for(mpo_path)
            assert count==2 and len(frames)==2
            # URL metadata never crosses the public MCP media boundary.
            assert 'remote_urls' not in json.dumps(media.public_media(bad,store.message(bad)['media']))
        config=folder/'credential.json';config.write_text(json.dumps(dict(host='127.0.0.1',port=6099,token='fixture_secret')))
        actual_client=httpx.Client;seen=[]
        def handler(req):
            seen.append(req.url.path)
            if req.url.path.endswith('/login'):return httpx.Response(200,json=dict(code=0,data={'Credential':'fixture_credential'}))
            if req.url.path.endswith('/GetQQLoginInfo'):return httpx.Response(200,json=dict(code=0,data={'uin':'123','online':True}))
            assert req.url.path=='/api/Debug/call'
            assert json.loads(req.content)=={'action':'nc_get_rkey','params':{}}
            return httpx.Response(200,json=dict(code=0,data={'status':'ok','retcode':0,'data':[{'type':20,'rkey':'key'}]}))
        def client(**kwargs):return actual_client(transport=httpx.MockTransport(handler),**kwargs)
        cfg=dict(credential_config=str(config),credential_account='123')
        with patch.object(image_remote.httpx,'Client',client):
            assert RKeys().get(cfg)=={'20':'key'}
            seen.clear()
            try:RKeys().get(dict(cfg,credential_account='456'))
            except ValueError as exc:assert str(exc)=='credential_account_changed'
            else:raise AssertionError('Another credential account silently accepted')
            assert '/api/Debug/call' not in seen
    print('PASS: official URL allowlist, no credential persistence/disclosure, opt-in 7-day window, verified digest/download reuse, stable indices, OCR, wrong-hash rejection, deletion race, owned-cache retention, native-cache protection and credential-account binding. Synthetic only.')

if __name__=='__main__':main()
