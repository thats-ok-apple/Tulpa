"""Recovery, scoped OCR, durable retries. Synthetic fixtures only."""
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from chatlocal.store import Store
from chatlocal.retrieval import Plan
from chatlocal.agent_tools import ChatTools
from chatlocal.image_index import ImageIndexer,register,retry_result
from chatlocal.live_recovery import diagnosis,quarantine,replay_issue
from chatlocal import media


def recovery(folder):
    import live_reader
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute("INSERT INTO Name2Id VALUES('self')")
    names=['broken@chatroom','healthy@chatroom']
    tables=['Msg_'+hashlib.md5(n.encode()).hexdigest() for n in names]
    for table in tables:
        db.execute(f'CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY,local_type INT,server_id INT,real_sender_id INT,create_time INT,message_content TEXT,packed_info_data BLOB,sort_seq INT)')
        db.execute(f'INSERT INTO "{table}" VALUES(1,1,11,1,100,\'fixture\',NULL,1)')
    rel='message/message_0.db'
    checkpoint=dict(account='fixture',cursors={rel+'/'+tables[0]:dict(last=5,signature='old-database')})
    assert diagnosis(db,tables[0],checkpoint['cursors'][rel+'/'+tables[0]])=='sequence_rewind'
    meta=folder/'wechat-snapshot-info.json';meta.write_text(json.dumps(dict(account='fixture',source_db=str(folder/'account/db_storage'))))
    reader=live_reader.Reader('wechat');reader.reader=SimpleNamespace(account='fixture',wxid='self',_message_dbs=lambda:[rel],_keys={rel:b'key'},_db_path=lambda _:None,
        _export_row=lambda r,_:dict(local_id=r['local_id'],type_code=1,server_id=r['server_id'],create_time=r['create_time'],content=r['message_content']))
    reader.idx={hashlib.md5(n.encode()).hexdigest():n for n in names};reader.nicks={};reader.maps_at=time.monotonic()
    reader.open=lambda *_:db
    reader.media=object();reader.media_stickers=False;reader.media_read_images=False
    reader.request=dict(checkpoint=checkpoint,wechat_read_images=False,load_stickers=False,since=0)
    with patch.object(live_reader,'DATA',folder):payload,following=reader.wechat()
    assert {m['chat'] for m in payload['messages']}==set(names)
    assert following['recovery'][rel+'/'+tables[0]]['status']=='recovered'
    assert checkpoint['cursors'][rel+'/'+tables[0]]['last']==5, 'Uncommitted replay cannot advance old state'
    # A permanent old unacknowledged row must not starve later native rows.
    db.execute(f'UPDATE "{tables[0]}" SET server_id=0 WHERE local_id=1')
    db.execute(f'INSERT INTO "{tables[0]}" VALUES(2,1,22,1,101,\'later\',NULL,2)')
    with patch.object(live_reader,'DATA',folder):payload,following=reader.wechat()
    assert following['cursors'][rel+'/'+tables[0]]['pending_ids']==[1]
    assert any(m['server_id']==22 for m in payload['messages'])
    reader.request['checkpoint']=following
    db.execute(f'UPDATE "{tables[0]}" SET server_id=33 WHERE local_id=1')
    with patch.object(live_reader,'DATA',folder):ack,following=reader.wechat()
    assert [m['server_id'] for m in ack['messages']]==[33]
    assert 'pending_ids' not in following['cursors'][rel+'/'+tables[0]]
    blocked=quarantine(checkpoint,names[0]);reader.request['checkpoint']=blocked
    with patch.object(live_reader,'DATA',folder):payload,following=reader.wechat()
    assert {m['chat'] for m in payload['messages']}=={names[1]}
    assert following['cursors'][rel+'/'+tables[0]]==checkpoint['cursors'][rel+'/'+tables[0]]
    assert replay_issue(dict(attempts=3),'sequence_rewind')['status']=='blocked'
    db.close()


def images(folder):
    from PIL import Image
    local=folder/'media-cache';store=Store(folder/'messages.sqlite3')
    data=io.BytesIO();Image.new('RGB',(60,30),'white').save(data,'PNG')
    with patch.object(media,'DATA',local):
        item=media.cache_image(data.getvalue())
        source=folder/'source.json'
        records=[dict(platform='qq',conversation_id=cid,conversation=cid,conversation_type='direct',sender='fixture',sender_id='fixture',source_id=cid,timestamp=int(time.time()*1000),content='ordinary message',media=[item]) for cid in ('allowed','forbidden')]
        source.write_text(json.dumps(records));store.import_file(source)
        worker=ImageIndexer(store)
        recognize=lambda _:dict(ocr='测试识别暗号 QX731',text_confidence=.99)
        assert worker.process_one(recognize) and worker.process_one(recognize)
        plan=Plan(platforms=['qq'],conversations=[json.dumps(['qq','allowed'])])
        result=ChatTools(store,plan).execute('search_messages',dict(query='QX731'))
        assert result['match_count']==1 and len(result['hit_ids'])==1,result
        hit=next(m for m in result['messages'] if m['id'] in result['hit_ids'])
        assert hit['image_text'][0]['text'].endswith('QX731') and hit['content']=='ordinary message'
        assert ChatTools(store,plan).execute('search_messages',dict(query='QX731',search_source='text'))['match_count']==0
        denied=ChatTools(store,plan);denied.allow_image_text=False
        assert denied.execute('search_messages',dict(query='QX731'))['match_count']==0
        assert ChatTools(store,Plan(platforms=['wechat'])).execute('search_messages',dict(query='QX731'))['match_count']==0
        assert ChatTools(store,Plan(start=int(time.time()*1000)+10000)).execute('search_messages',dict(query='QX731'))['match_count']==0
        assert ChatTools(store,Plan(snapshot_max_id=0)).execute('search_messages',dict(query='QX731'))['match_count']==0
        # Persisted retry times survive construction of another worker.
        with store.connect() as db:
            register(db,1,[media.unavailable(reason_code='source_missing')],int(time.time()*1000),now=100)
            retry_result(db,1,False,now=100)
        with store.connect() as db:
            row=db.execute('SELECT * FROM media_retry_jobs WHERE message_id=1').fetchone()
            assert row['attempts']==1 and row['next_try']==115
            retry_result(db,1,False,now=100+8*86400)
            assert db.execute('SELECT state FROM media_retry_jobs WHERE message_id=1').fetchone()[0]=='window_expired'
        # Requeue OCR, delete the message during recognition, and reject commit.
        with store.connect() as db:db.execute("UPDATE image_ocr SET status='pending' WHERE message_id=2");db.execute('DELETE FROM image_ocr_cache')
        def deleted(_):
            with store.connect() as db:
                db.execute('DELETE FROM provenance WHERE message_id=2');db.execute('DELETE FROM messages WHERE id=2')
            return dict(ocr='MUST_NOT_REAPPEAR',text_confidence=1)
        assert worker.process_one(deleted)
        with store.connect() as db:
            assert not db.execute('SELECT 1 FROM image_ocr WHERE message_id=2').fetchone()
            assert not db.execute("SELECT 1 FROM image_ocr_fts WHERE image_ocr_fts MATCH 'MUST_NOT_REAPPEAR'").fetchone()
        # Re-import valid bytes repairs an existing corrupt immutable cache.
        path=media.local_path(item['local_path']);path.write_bytes(b'bad')
        again=media.cache_image(data.getvalue())
        assert hashlib.sha256(path.read_bytes()).hexdigest()==again['sha256']


def coverage_cache(folder):
    store=Store(folder/'coverage.sqlite3')
    cold=folder/'coverage.json';cold.write_text(json.dumps(dict(coverage={'exported_chats':2})))
    stamp='2026-11-08'
    with store.connect() as db:
        db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?,?)',[(f'live-{n}','wechat.json','imports/DO_NOT_OPEN_LIVE.json','wechatauto',stamp,0,0) for n in range(1000)])
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?)',('cold','wechat-real.json',str(cold.relative_to(ROOT)),'wechatauto',stamp,0,0))
    original=Path.read_text
    def read(path,*a,**kw):
        assert path.name!='DO_NOT_OPEN_LIVE.json','Status must not scan realtime archives'
        return original(path,*a,**kw)
    with patch.object(Path,'read_text',read):assert store.latest_wechat_coverage()=={'exported_chats':2}
    cold.unlink()
    assert store.latest_wechat_coverage()=={'exported_chats':2},'Cached metadata survives archive absence'


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='stability-check-') as name:
        folder=Path(name);recovery(folder);images(folder);coverage_cache(folder)
    print('PASS: whole-reader sequence rewind/replay, healthy-conversation isolation, bounded quarantine; local OCR/search source, scope/date/snapshot/media permission, deletion during OCR, durable retry expiry and corrupt-cache repair. Synthetic only.')


if __name__=='__main__':main()
