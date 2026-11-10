"""Local-only, single-worker OCR and durable attachment retry scheduling."""
import hashlib
import json
import threading
import time

ENGINE='rapidocr-3.9.2:v1'
DAYS=30


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS image_ocr(
        id INTEGER PRIMARY KEY,message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        image_index INTEGER NOT NULL,digest TEXT NOT NULL,engine TEXT NOT NULL,status TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',confidence REAL,thumbnail INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,next_try REAL NOT NULL DEFAULT 0,error_code TEXT,
        UNIQUE(message_id,image_index));
      CREATE INDEX IF NOT EXISTS image_ocr_queue ON image_ocr(status,next_try);
      CREATE VIRTUAL TABLE IF NOT EXISTS image_ocr_fts USING fts5(terms,tokenize='unicode61');
      CREATE TRIGGER IF NOT EXISTS image_ocr_delete AFTER DELETE ON image_ocr BEGIN
        DELETE FROM image_ocr_fts WHERE rowid=old.id;
      END;
      CREATE TABLE IF NOT EXISTS image_ocr_cache(
        digest TEXT NOT NULL,engine TEXT NOT NULL,text TEXT NOT NULL,confidence REAL,
        PRIMARY KEY(digest,engine));
      CREATE TRIGGER IF NOT EXISTS image_ocr_cache_delete AFTER DELETE ON image_ocr BEGIN
        DELETE FROM image_ocr_cache WHERE digest=old.digest AND engine=old.engine
          AND NOT EXISTS(SELECT 1 FROM image_ocr WHERE digest=old.digest AND engine=old.engine);
      END;
      CREATE TABLE IF NOT EXISTS media_retry_jobs(
        message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        state TEXT NOT NULL,reason_code TEXT,attempts INTEGER NOT NULL DEFAULT 0,
        next_try REAL NOT NULL,deadline REAL NOT NULL,updated_at REAL NOT NULL);
      CREATE INDEX IF NOT EXISTS media_retry_due ON media_retry_jobs(state,next_try);
    ''')


def register(db, mid, items, timestamp, *, force=False, now=None):
    now=time.time() if now is None else now
    valid=set()
    if force or timestamp >= (now-DAYS*86400)*1000:
        for slot,item in enumerate(items):
            index=item.get('source_index',slot)
            if item.get('status')!='available' or item.get('kind')!='image' or item.get('animated'):continue
            digest=item.get('sha256')
            if not digest:continue
            valid.add(index)
            prior=db.execute('SELECT id,digest,engine FROM image_ocr WHERE message_id=? AND image_index=?',(mid,index)).fetchone()
            if prior and (prior['digest'],prior['engine'])==(digest,ENGINE):continue
            if prior:
                db.execute('DELETE FROM image_ocr WHERE id=?',(prior['id'],))
            db.execute('INSERT INTO image_ocr(message_id,image_index,digest,engine,status,thumbnail) VALUES(?,?,?,?,?,?)',
                       (mid,index,digest,ENGINE,'pending',bool(item.get('metadata',{}).get('thumbnail'))))
        # Replaced/omitted attachments must not retain searchable old OCR.
        for row in db.execute('SELECT id,image_index FROM image_ocr WHERE message_id=?',(mid,)).fetchall():
            if row['image_index'] not in valid:db.execute('DELETE FROM image_ocr WHERE id=?',(row['id'],))
    terminal=[i for i in items if i.get('status')=='unavailable' and i.get('reason_code') in ('source_retracted','retention_expired')]
    missing=[i for i in items if i.get('status')=='unavailable' and i.get('kind')=='image' and i not in terminal]
    if missing:
        reason=missing[0].get('reason_code','unknown')
        db.execute('''INSERT INTO media_retry_jobs(message_id,state,reason_code,next_try,deadline,updated_at)
          VALUES(?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET reason_code=excluded.reason_code,
          state=CASE WHEN media_retry_jobs.state='recovered' OR (media_retry_jobs.state='metadata_missing' AND excluded.reason_code!='metadata_missing') THEN 'pending' ELSE media_retry_jobs.state END,
          next_try=CASE WHEN media_retry_jobs.state='recovered' OR (media_retry_jobs.state='metadata_missing' AND excluded.reason_code!='metadata_missing') THEN 0 ELSE media_retry_jobs.next_try END''',
          (mid,'pending',reason,0,now+7*86400,now))
    elif terminal:
        db.execute("UPDATE media_retry_jobs SET state=?,reason_code=?,updated_at=? WHERE message_id=?",(terminal[0]['reason_code'],terminal[0]['reason_code'],now,mid))
    else:
        db.execute("UPDATE media_retry_jobs SET state='recovered',updated_at=? WHERE message_id=?",(now,mid))
    from .image_remote import enqueue
    enqueue(db,mid,items,timestamp)


def retry_result(db, mid, recovered, *, reason=None, now=None):
    now=time.time() if now is None else now
    row=db.execute('SELECT attempts,deadline FROM media_retry_jobs WHERE message_id=?',(mid,)).fetchone()
    if not row:return
    tries=row['attempts']+1
    state='recovered' if recovered else 'window_expired' if now>=row['deadline'] else 'metadata_missing' if reason=='metadata_missing' else 'pending'
    db.execute('UPDATE media_retry_jobs SET state=?,reason_code=coalesce(?,reason_code),attempts=?,next_try=?,updated_at=? WHERE message_id=?',
               (state,reason,tries,now+min(3600,15*2**min(tries-1,8)),now,mid))


def seed(store, *, start=None,end=None,platform=None):
    lo=(time.time()-DAYS*86400)*1000 if start is None else start
    where="timestamp>=? AND media_json!='[]'";args=[lo]
    if end is not None:where+=' AND timestamp<?';args.append(end)
    if platform:where+=' AND platform=?';args.append(platform)
    after=0;count=0
    while True:
        with store.connect() as db:
            rows=db.execute('SELECT id,media_json,timestamp FROM messages WHERE '+where+' AND id>? ORDER BY id LIMIT 100',args+[after]).fetchall()
            for row in rows:
                register(db,row['id'],json.loads(row['media_json']),row['timestamp'],force=True)
                count+=1;after=row['id']
        if len(rows)<100:break
    return count


def enabled(store):
    with store.connect() as db:
        row=db.execute("SELECT value FROM client_settings WHERE name='image_index_enabled'").fetchone()
    return not row or row[0]=='true'


def enqueue_message(store, mid):
    with store.connect() as db:
        row=db.execute('SELECT media_json,timestamp FROM messages WHERE id=?',(mid,)).fetchone()
        if not row:raise ValueError('消息不存在')
        register(db,mid,json.loads(row['media_json']),row['timestamp'],force=True)
        db.execute("UPDATE image_ocr SET status='pending',attempts=0,next_try=-1 WHERE message_id=? AND status!='ready'",(mid,))


def status(store):
    with store.connect() as db:
        ocr=dict(db.execute('SELECT status,count(*) FROM image_ocr GROUP BY status'))
        retry=dict(db.execute('SELECT state,count(*) FROM media_retry_jobs GROUP BY state'))
    from .image_remote import status as remote_status
    return dict(enabled=enabled(store),local_only=True,history_days=DAYS,engine=ENGINE,ocr=ocr,media_retries=retry,remote=remote_status(store))


def matching_clause(expression,terms):
    clause="m.id IN (SELECT e.message_id FROM image_ocr e WHERE e.status='ready' AND (e.id IN (SELECT rowid FROM image_ocr_fts WHERE image_ocr_fts MATCH ?) OR "+' OR '.join('instr(lower(e.text),?)>0' for _ in terms)+'))'
    return clause,[expression]+terms


def evidence(db,mid,terms):
    rows=db.execute("SELECT image_index,text,confidence,thumbnail FROM image_ocr WHERE message_id=? AND status='ready' ORDER BY image_index",(mid,)).fetchall()
    return [dict(index=r['image_index'],text=r['text'][:1200],confidence=r['confidence'],thumbnail=bool(r['thumbnail']),
                 evidence_kind='image_ocr',note='本地机器识别的图中文字，可能有误；请用read_image核对')
            for r in rows if any(t in r['text'].lower() for t in terms)][:3]


class ImageIndexer:
    def __init__(self,store):
        self.store=store;self.stop=threading.Event();self.thread=None;self.last_error=None

    def start(self):
        from .config import DB
        if self.store.path.resolve()!=DB.resolve():return
        self.thread=threading.Thread(target=self.run,daemon=True,name='local-image-ocr');self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=5)

    def process_one(self,recognize=None,*,message_id=None):
        from .media import media_at,media_path
        from .store import tokens
        with self.store.connect() as db:
            clause=' AND e.message_id=?' if message_id is not None else ''
            row=db.execute("SELECT e.*,m.media_json FROM image_ocr e JOIN messages m ON m.id=e.message_id WHERE e.status='pending' AND e.next_try<=?"+clause+" ORDER BY e.next_try,e.message_id DESC LIMIT 1",(time.time(),message_id) if message_id is not None else (time.time(),)).fetchone()
        if not row:return False
        item=media_at(json.loads(row['media_json']),row['image_index'])
        try:
            path=media_path(item) if item else None
            if not path:raise ValueError('source_missing')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row['digest']:raise ValueError('cache_corrupt')
            with self.store.connect() as db:
                cached=db.execute('SELECT text,confidence FROM image_ocr_cache WHERE digest=? AND engine=?',(row['digest'],ENGINE)).fetchone()
            if cached:text,confidence=cached
            else:
                if recognize:result=recognize(item)
                else:
                    from .vision import VisionProvider
                    result=VisionProvider(dict(VISION_PROVIDER='ocr')).inspect(item)
                if result.get('error'):raise ValueError('ocr_failed')
                text=result.get('ocr','')[:6000];confidence=result.get('text_confidence')
            if self.stop.is_set() or not enabled(self.store):return True
            with self.store.connect() as db:
                current=db.execute('SELECT e.digest,m.media_json FROM image_ocr e JOIN messages m ON m.id=e.message_id WHERE e.id=?',(row['id'],)).fetchone()
                if not current or current['digest']!=row['digest']:return True
                live=media_at(json.loads(current['media_json']),row['image_index'])
                if not live or live.get('sha256')!=row['digest']:return True
                db.execute('INSERT OR REPLACE INTO image_ocr_cache VALUES(?,?,?,?)',(row['digest'],ENGINE,text,confidence))
                db.execute("UPDATE image_ocr SET status='ready',text=?,confidence=?,error_code=NULL WHERE id=?",(text,confidence,row['id']))
                db.execute('DELETE FROM image_ocr_fts WHERE rowid=?',(row['id'],))
                if text:db.execute('INSERT INTO image_ocr_fts(rowid,terms) VALUES(?,?)',(row['id'],tokens(text)))
        except Exception as exc:
            code=str(exc) if isinstance(exc,ValueError) and str(exc) in ('source_missing','cache_corrupt','ocr_failed') else 'ocr_unavailable'
            with self.store.connect() as db:
                db.execute("UPDATE image_ocr SET attempts=attempts+1,error_code=?,status=CASE WHEN attempts>=2 OR ? IN ('source_missing','cache_corrupt') THEN 'failed' ELSE 'pending' END,next_try=? WHERE id=?",(code,code,time.time()+300,row['id']))
        return True

    def run(self):
        try:
            if enabled(self.store):seed(self.store)
        except Exception:self.last_error='seed_failed'
        while not self.stop.wait(1):
            try:
                if enabled(self.store):self.process_one()
            except Exception:self.last_error='worker_failed';self.stop.wait(10)


def install(app,store):
    from fastapi import Request,HTTPException
    worker=ImageIndexer(store)
    from .image_remote import install as install_remote
    install_remote(app,store)
    app.state.image_indexer=worker
    def local(request):
        if request.client and request.client.host not in ('127.0.0.1','::1','testclient'):raise HTTPException(403)
        if request.headers.get('sec-fetch-site')=='cross-site' or request.headers.get('origin') not in (None,str(request.base_url).rstrip('/')):raise HTTPException(403)
    @app.on_event('startup')
    def start():worker.start()
    @app.on_event('shutdown')
    def stop():worker.close()
    @app.get('/api/images/status')
    def get(request:Request):local(request);return dict(status(store),last_error=worker.last_error)
    @app.put('/api/images/settings')
    def configure(request:Request,body:dict):
        local(request)
        if set(body)!={'enabled'} or type(body['enabled']) is not bool:raise HTTPException(400)
        with store.connect() as db:db.execute("INSERT OR REPLACE INTO client_settings VALUES('image_index_enabled',?)",(json.dumps(body['enabled']),))
        return status(store)
    @app.post('/api/images/backfill')
    def backfill(request:Request,body:dict):
        local(request)
        from .import_scope import bounds
        try:
            lo,hi=bounds(body.get('start',''),body.get('end',''))
            if lo is None or hi is None or hi-lo>366*86400000 or body.get('platform') not in (None,'qq','wechat'):raise ValueError()
        except (TypeError,ValueError):raise HTTPException(400,'须填写一年以内的起止日期与有效平台') from None
        return dict(checked=seed(store,start=lo,end=hi,platform=body.get('platform')))
    @app.post('/api/images/{mid}/index')
    def index_message(request:Request,mid:int):
        local(request)
        try:enqueue_message(store,mid)
        except ValueError:raise HTTPException(404,'消息不存在') from None
        return dict(queued=True,message_id=mid)
