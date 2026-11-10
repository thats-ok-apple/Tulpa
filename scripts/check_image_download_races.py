"""Isolated download races: no production data, native client or network access."""
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from chatlocal import image_remote as remote,media,sync
from chatlocal.store import Store


def pixels(color='blue'):
    stream=io.BytesIO();Image.new('RGB',(36,24),color).save(stream,'PNG')
    return stream.getvalue()


def lease_job(db,mid,index,md5,lease='fixture-lease'):
    db.execute("""INSERT INTO image_download_jobs(message_id,image_index,md5,status,updated_at) VALUES(?,?,?,'downloading',?)
        ON CONFLICT(message_id,image_index) DO UPDATE SET md5=excluded.md5,status='downloading',attempts=0,updated_at=excluded.updated_at""",(mid,index,md5,time.time()))
    columns={r[1] for r in db.execute('PRAGMA table_info(image_download_jobs)')}
    if 'lease' in columns:
        db.execute('UPDATE image_download_jobs SET lease=? WHERE message_id=? AND image_index=?',(lease,mid,index))
    return dict(db.execute('SELECT * FROM image_download_jobs WHERE message_id=? AND image_index=?',(mid,index)).fetchone())


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='image-race-check-') as name:
        folder=Path(name);(folder/'.tmp').mkdir();data=folder/'data';data.mkdir()
        store=Store(data/'messages.sqlite3')
        with store.connect() as db:
            db.execute("INSERT OR REPLACE INTO client_settings VALUES('image_remote',?)",(json.dumps(dict(enabled=True,days=7,message_account='fixture')),))
        with patch.object(media,'DATA',data),patch.object(remote,'DATA',data),patch.object(sync,'ROOT',folder):
            yield folder,data,store


def put(folder,store,sid,raw,age=0,available=False):
    md5=hashlib.md5(raw).hexdigest()
    meta=dict(md5=md5,filename=sid+'.png',remote_urls=['https://multimedia.nt.qq.com.cn/download?appid=1407&spec=0&fileid='+sid])
    item=media.cache_image(raw,recovered_by=remote.MARK,**meta) if available else media.unavailable(**meta)
    path=folder/(sid+'.json')
    path.write_text(json.dumps([dict(platform='qq',conversation_id='fixture:group:1',conversation_type='group',conversation='fixture',sender='fixture',sender_id='fixture',source_id=sid,timestamp=int((time.time()-age)*1000),content='unchanged',media=[item])]))
    store.import_file(path)
    with store.connect() as db:
        return media.hydrate(db.execute('SELECT * FROM messages WHERE source_id=?',(sid,)).fetchone())


def stale_completion():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY)');db.execute('INSERT INTO messages VALUES(1)');remote.initialize(db)
    class MemoryStore:
        @contextmanager
        def connect(self):
            with db:yield db
    worker=remote.Downloader(MemoryStore())
    with db:old=lease_job(db,1,0,'a'*32,'old-lease')
    with db:
        db.execute("UPDATE image_download_jobs SET md5=?,status='pending'",('b'*32,))
    worker.finish(old)
    row=db.execute('SELECT * FROM image_download_jobs').fetchone()
    assert row['md5']=='b'*32 and row['status']=='pending','stale completion marked a replacement image ready'
    assert 'lease' in row.keys(),'download claims need a persisted lease token'
    with db:
        db.execute("UPDATE image_download_jobs SET status='downloading',lease='lease-before-restart'")
    old=dict(db.execute('SELECT * FROM image_download_jobs').fetchone())
    with db:db.execute("UPDATE image_download_jobs SET lease='lease-after-restart',attempts=5")
    worker.finish(old,'remote_unavailable')
    row=db.execute('SELECT * FROM image_download_jobs').fetchone()
    assert row['status']=='downloading' and row['attempts']==5 and row['lease']=='lease-after-restart','stale worker overwrote a replacement lease'
    db.close()


def cleanup_extension():
    with fixture() as (folder,data,store):
        raw=pixels();md5=hashlib.md5(raw).hexdigest();digest=hashlib.sha256(raw).hexdigest()
        old=put(folder,store,'expired',raw,age=8*86400,available=True)
        recent=put(folder,store,'recent',raw)
        existing=media.media_path(old['media'][0]);assert existing
        with store.connect() as db:
            db.execute('INSERT INTO image_download_cache(digest,path,owned,retain_until,md5) VALUES(?,?,?,?,?)',(digest,old['media'][0]['local_path'],1,0,md5))
            job=lease_job(db,recent['id'],0,md5)
        start=threading.Event();done=threading.Event();errors=[];selected=[]
        writer=remote.Downloader(store)
        def write_recent():
            try:
                assert start.wait(5),'cleanup did not reach expired-row selection'
                assert writer.commit_bytes(raw,recent,0,recent['media'][0],job),'recent writer was unexpectedly cancelled'
            except BaseException as exc:errors.append(exc)
            finally:done.set()
        class SelectedCursor:
            def __init__(self,cursor,db):self.cursor=cursor;self.db=db
            def fetchall(self):
                rows=self.cursor.fetchall();selected.extend(rows);start.set()
                # With BEGIN IMMEDIATE the concurrent writer is serialized and
                # must run after cleanup. The old implementation has no write
                # transaction: ensure the extension commits before stale unlink.
                if not self.db.in_transaction:
                    assert done.wait(5),'cleanup lacks a write transaction but blocked its concurrent writer'
                return rows
        class ObservedDB:
            def __init__(self,db):self.db=db
            def execute(self,sql,args=()):
                cur=self.db.execute(sql,args)
                if sql.startswith('SELECT digest FROM image_download_cache WHERE retain_until'):
                    return SelectedCursor(cur,self.db)
                return cur
            def __getattr__(self,name):return getattr(self.db,name)
        class CleanupStore:
            @contextmanager
            def connect(self):
                with store.connect() as db:yield ObservedDB(db)
        thread=threading.Thread(target=write_recent,daemon=True);thread.start()
        try:remote.Downloader(CleanupStore()).cleanup()
        finally:
            start.set();thread.join(timeout=8)
        assert selected,'fixture did not expose an expired cache row'
        assert not thread.is_alive(),'cleanup/writer deadlocked'
        assert not errors,'concurrent recent writer failed: '+','.join(type(e).__name__ for e in errors)
        saved=store.message(recent['id'])['media'][0];path=media.media_path(saved)
        assert path and hashlib.sha256(path.read_bytes()).hexdigest()==digest,'cleanup removed a newly needed image after retention extension'
        with store.connect() as db:
            cached=db.execute('SELECT retain_until FROM image_download_cache WHERE digest=?',(digest,)).fetchone()
        assert cached and cached[0]>time.time(),'cleanup discarded the renewed cache ownership record'


def corrupt_existing_cache():
    with fixture() as (folder,data,store):
        raw=pixels('red');row=put(folder,store,'corrupt',raw,available=True)
        path=media.media_path(row['media'][0]);assert path;path.write_bytes(b'corrupted fixture cache')
        md5=hashlib.md5(raw).hexdigest()
        with store.connect() as db:
            db.execute("INSERT INTO image_download_jobs(message_id,image_index,md5,status,updated_at) VALUES(?,?,?,'ready',?)",(row['id'],0,md5,time.time()))
        remote.seed(store);worker=remote.Downloader(store);calls=[]
        def download(url,cancel):calls.append(url);return raw
        assert worker.process_one(download,keys={'20':'fixture-key'}),'corrupt existing file was still treated as ready'
        assert worker.error is None,'corrupt cache recovery failed: '+str(worker.error)
        assert len(calls)==1,'corrupt cache never fetched verified replacement bytes'
        saved=store.message(row['id'])['media'][0];path=media.media_path(saved)
        assert path and path.read_bytes()==raw,'corrupt cache was not repaired'
        with store.connect() as db:
            assert db.execute('SELECT status FROM image_download_jobs WHERE message_id=?',(row['id'],)).fetchone()[0]=='ready'


def concurrent_claims():
    with fixture() as (folder,data,store):
        samples={name:pixels(color) for name,color in (('claim-red','red'),('claim-green','green'))}
        rows={name:put(folder,store,name,raw) for name,raw in samples.items()};remote.seed(store)
        worker=remote.Downloader(store);barrier=threading.Barrier(2);seen=[];snapshots=[];lock=threading.Lock()
        def download(url,cancel):
            sid=parse_qs(urlsplit(url).query)['fileid'][0]
            with lock:
                seen.append(sid);initial=len(seen)<=2
            if initial:
                barrier.wait(timeout=5)
                with store.connect() as db:
                    snapshots.append({r[0] for r in db.execute("SELECT message_id FROM image_download_jobs WHERE status='downloading'")})
                barrier.wait(timeout=5)
            return samples[sid]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:worker.process_one(download,keys={'20':'fixture-key'}),range(2)))
        assert all(results) and set(seen[:2])==set(samples),'concurrent workers claimed the same image'
        assert snapshots and all(s=={r['id'] for r in rows.values()} for s in snapshots),'claims were not persisted before network IO'
        assert len(seen)==2,'workers downloaded the same bytes again'
        with store.connect() as db:
            ready=db.execute("SELECT count(*) FROM image_download_jobs WHERE status='ready'").fetchone()[0]
        assert ready==2,'brief commit contention sent a completed download back to retry'
        for sid,row in rows.items():
            path=media.media_path(store.message(row['id'])['media'][0])
            assert path and path.read_bytes()==samples[sid],'concurrent claim lost or mixed image bytes'


def stale_seed_snapshot():
    with fixture() as (folder, data, store):
        raw = pixels()
        row = put(folder, store, 'seed-race', raw)
        ready = media.cache_image(raw, **row['media'][0]['metadata'])
        def finish_during_scan(item):
            with store.connect() as db:
                db.execute('UPDATE messages SET media_json=? WHERE id=?', (json.dumps([ready]),row['id']))
                db.execute("UPDATE image_download_jobs SET status='ready',next_try=0 WHERE message_id=?", (row['id'],))
            return None
        with patch.object(remote,'verified_media_path',side_effect=finish_during_scan):
            remote.seed(store)
        with store.connect() as db:
            assert db.execute('SELECT status FROM image_download_jobs WHERE message_id=?',(row['id'],)).fetchone()[0]=='ready'


def retry_fairness():
    with fixture() as (folder, data, store):
        older = put(folder, store, 'older', pixels(), age=86400)
        newer = put(folder, store, 'newer', pixels('red'))
        worker = remote.Downloader(store)
        with store.connect() as db:
            job = lease_job(db, older['id'], 0, hashlib.md5(pixels()).hexdigest())
            db.execute('UPDATE image_download_jobs SET attempts=7 WHERE message_id=?', (older['id'],))
            job['attempts'] = 7
        worker.finish(job, 'commit_busy')
        with store.connect() as db:
            attempts, next_try = db.execute('SELECT attempts,next_try FROM image_download_jobs WHERE message_id=?',
                                            (older['id'],)).fetchone()
            assert attempts == 7 and 0 < next_try-time.time() <= 10
            db.execute('UPDATE image_download_jobs SET next_try=0 WHERE message_id=?', (older['id'],))
        assert worker.process_one(lambda *_: pixels(), keys={'20':'fixture_key'})
        assert store.message(older['id'])['media'][0]['status'] == 'available'
        assert store.message(newer['id'])['media'][0]['status'] == 'unavailable'


if __name__=='__main__':
    failures=[]
    for check in (stale_completion,cleanup_extension,corrupt_existing_cache,concurrent_claims,stale_seed_snapshot,retry_fairness):
        try:check();print('PASS:',check.__name__)
        except BaseException as exc:
            failures.append(check.__name__);print('FAIL:',check.__name__,type(exc).__name__,str(exc))
    if failures:raise SystemExit(1)
    print('PASS: isolated lease, retention-extension, corrupt-cache and concurrent-claim regressions; no production data or network.')
