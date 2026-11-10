"""Opt-in rolling 7-day official QQ image download cache. No sends or UI."""
import hashlib,json,subprocess,sys,threading,time,shutil,sqlite3,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import httpx
from .config import DATA,ROOT,DB
from .media import hydrate,media_at,media_path,verified_media_path,cache_image,MAX_MEDIA_BYTES
from .qq_image_refs import clean_url
DAYS=7
MARK='qq-official-30d-download'


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS image_download_jobs(
      message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,image_index INTEGER NOT NULL,
      md5 TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_try REAL NOT NULL DEFAULT 0,
      error_code TEXT,updated_at REAL NOT NULL,PRIMARY KEY(message_id,image_index));
    CREATE INDEX IF NOT EXISTS image_download_due ON image_download_jobs(status,next_try);
    CREATE TABLE IF NOT EXISTS image_download_cache(
      digest TEXT PRIMARY KEY,path TEXT NOT NULL,owned INTEGER NOT NULL,retain_until REAL NOT NULL,md5 TEXT NOT NULL DEFAULT '');
    ''')
    if 'md5' not in {r[1] for r in db.execute('PRAGMA table_info(image_download_cache)')}:
        db.execute("ALTER TABLE image_download_cache ADD COLUMN md5 TEXT NOT NULL DEFAULT ''")
    db.execute('CREATE INDEX IF NOT EXISTS image_download_md5 ON image_download_cache(md5)')
    if 'priority' not in {r[1] for r in db.execute('PRAGMA table_info(image_download_jobs)')}:
        db.execute('ALTER TABLE image_download_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
    if 'lease' not in {r[1] for r in db.execute('PRAGMA table_info(image_download_jobs)')}:
        db.execute('ALTER TABLE image_download_jobs ADD COLUMN lease TEXT')


def settings(store):
    with store.connect() as db:
        row=db.execute("SELECT value FROM client_settings WHERE name='image_remote'").fetchone()
    try:return json.loads(row[0]) if row else dict(enabled=False,days=DAYS)
    except ValueError:return dict(enabled=False,days=DAYS)


def status(store):
    with store.connect() as db:
        counts=dict(db.execute('SELECT status,count(*) FROM image_download_jobs GROUP BY status'))
        errors=dict(db.execute("SELECT error_code,count(*) FROM image_download_jobs WHERE status='retry' GROUP BY error_code"))
    return dict(enabled=bool(settings(store).get('enabled')),days=DAYS,official_only=True,downloads=counts,errors=errors)


def enqueue(db,mid,items,timestamp):
    if timestamp<(time.time()-DAYS*86400)*1000:return
    cfg=db.execute("SELECT value FROM client_settings WHERE name='image_remote'").fetchone()
    if not cfg or not json.loads(cfg[0]).get('enabled'):return
    platform=db.execute('SELECT platform FROM messages WHERE id=?',(mid,)).fetchone()
    if not platform or platform[0]!='qq':return
    for slot,item in enumerate(items):
        md5=item.get('metadata',{}).get('md5','')
        if item.get('kind')!='image' or item.get('status')!='unavailable' or not isinstance(md5,str) or len(md5)!=32 or any(c not in '0123456789abcdefABCDEF' for c in md5):continue
        db.execute('''INSERT INTO image_download_jobs(message_id,image_index,md5,status,updated_at) VALUES(?,?,?,'pending',?)
          ON CONFLICT(message_id,image_index) DO UPDATE SET md5=excluded.md5,
          status=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready' THEN 'pending' ELSE image_download_jobs.status END,
          lease=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready' THEN NULL ELSE lease END,
          next_try=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready' THEN 0 ELSE next_try END''',
          (mid,item.get('source_index',slot),md5.lower(),time.time()))


def prioritize(store,row,index):
    if row['platform']!='qq' or row['timestamp']<(time.time()-DAYS*86400)*1000 or not settings(store).get('enabled'):return None
    item=media_at(row['media'],index)
    if not item or item.get('kind')!='image' or item.get('status')=='omitted':return None
    md5=item.get('metadata',{}).get('md5','')
    if not isinstance(md5,str) or len(md5)!=32 or any(c not in '0123456789abcdefABCDEF' for c in md5):return None
    with store.connect() as db:
        # The caller already scoped this message; do not recreate a deleted row.
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT media_json FROM messages WHERE id=?',(row['id'],)).fetchone()
        if not current or json.loads(current[0])!=row['media']:return None
        db.execute('''INSERT INTO image_download_jobs(message_id,image_index,md5,status,next_try,updated_at,priority)
          SELECT id,?,?,'pending',0,?,1 FROM messages WHERE id=?
          ON CONFLICT(message_id,image_index) DO UPDATE SET priority=1,
          next_try=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready'
                        OR (image_download_jobs.status='retry' AND image_download_jobs.priority=0)
                       THEN 0 ELSE next_try END,
          status=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready' THEN 'pending' ELSE status END,
          lease=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready' THEN NULL ELSE lease END,
          md5=excluded.md5''',(index,md5.lower(),time.time(),row['id']))
        job=db.execute('SELECT status,error_code,next_try FROM image_download_jobs WHERE message_id=? AND image_index=?',(row['id'],index)).fetchone()
    return dict(job) if job else None


def seed(store,now=None,after=0):
    now=time.time() if now is None else now
    while True:
        with store.connect() as db:
            rows=db.execute("SELECT id,timestamp,media_json FROM messages NOT INDEXED WHERE platform='qq' AND timestamp>=? AND timestamp<=? AND media_json!='[]' AND id>? ORDER BY id LIMIT 100",((now-DAYS*86400)*1000,(now+300)*1000,after)).fetchall()
            for row in rows:
                for slot,item in enumerate(json.loads(row['media_json'])):
                    if item.get('kind')!='image' or item.get('status')=='omitted':continue
                    md5=item.get('metadata',{}).get('md5','')
                    if not isinstance(md5,str) or len(md5)!=32 or any(c not in '0123456789abcdefABCDEF' for c in md5):continue
                    index=item.get('source_index',slot)
                    if verified_media_path(item):
                        db.execute("""UPDATE image_download_jobs SET status='ready',error_code=NULL,updated_at=?,next_try=0
                            WHERE message_id=? AND image_index=? AND md5=? AND status!='downloading'
                            AND EXISTS (SELECT 1 FROM messages WHERE id=? AND media_json=?)""",
                            (now,row['id'],index,md5.lower(),row['id'],row['media_json']))
                        continue
                    db.execute("""INSERT INTO image_download_jobs(message_id,image_index,md5,status,updated_at)
                      SELECT id,?,?,'pending',? FROM messages WHERE id=? AND media_json=?
                      ON CONFLICT(message_id,image_index) DO UPDATE SET md5=excluded.md5,
                      status=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready' THEN 'pending' ELSE image_download_jobs.status END,
                      lease=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready' THEN NULL ELSE lease END,
                      next_try=CASE WHEN image_download_jobs.md5!=excluded.md5 OR image_download_jobs.status='ready' THEN 0 ELSE next_try END""",
                      (index,md5.lower(),now,row['id'],row['media_json']))
        if rows:after=rows[-1]['id']
        if len(rows)<100:break
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Existing rows stored their deadline as the message timestamp plus 30 days.
        migrated=db.execute("INSERT OR IGNORE INTO client_settings VALUES('image_download_retention_v2','7')").rowcount
        if migrated:
            db.execute('UPDATE image_download_cache SET retain_until=retain_until-? WHERE owned=1',
                       ((30-DAYS)*86400,))
        db.execute("UPDATE image_download_jobs SET status='outside_window',error_code='outside_window',lease=NULL WHERE message_id IN (SELECT id FROM messages WHERE timestamp<?) AND status!='outside_window'",((now-DAYS*86400)*1000,))
    return after


class RKeys:
    def __init__(self):
        self.keys={}
        self.until=0
        self.binding=None
        self.failure=None
        self.lock=threading.Lock()

    def get(self,cfg):
        binding=(cfg['credential_config'],cfg['credential_account'])
        with self.lock:
            if self.binding==binding and self.until>time.monotonic():
                if self.failure:raise ValueError(self.failure)
                return self.keys
            self.binding=binding
            self.failure=None
            self.keys={}
            try:
                self.keys=self.fetch(cfg)
            except (ValueError,OSError,KeyError,TypeError,httpx.HTTPError) as exc:
                expected={'credential_unavailable','credential_not_loopback',
                          'credential_account_changed','credential_offline','credential_backend_unavailable'}
                self.failure=str(exc) if str(exc) in expected else 'credential_connection_error' if isinstance(exc,httpx.HTTPError) else 'credential_unavailable'
                self.until=time.monotonic()+60
                raise ValueError(self.failure) from None
            self.until=time.monotonic()+300
            return self.keys

    @staticmethod
    def fetch(cfg):
        path=Path(cfg['credential_config']).expanduser()
        local=json.loads(path.read_text())
        if local.get('host') not in ('127.0.0.1','localhost','::1') or type(local.get('port')) is not int:raise ValueError('credential_not_loopback')
        def unwrap(response):
            if response.status_code!=200 or len(response.content)>1024*1024:raise ValueError('credential_unavailable')
            value=response.json()
            if value.get('code')!=0:raise ValueError('credential_unavailable')
            return value.get('data')
        with httpx.Client(base_url='http://127.0.0.1:'+str(local['port']),timeout=15,trust_env=False,follow_redirects=False) as client:
            auth=unwrap(client.post('/api/auth/login',json={'hash':hashlib.sha256((local['token']+'.napcat').encode()).hexdigest()}))
            if not isinstance(auth,dict) or not auth.get('Credential'):raise ValueError('credential_unavailable')
            client.headers['Authorization']='Bearer '+auth['Credential']
            account=unwrap(client.post('/api/QQLogin/GetQQLoginInfo',json={}))
            if str(account.get('uin'))!=str(cfg['credential_account']):raise ValueError('credential_account_changed')
            if not account.get('online'):raise ValueError('credential_offline')
            receipt=unwrap(client.post('/api/Debug/call',json={'action':'nc_get_rkey','params':{}}))
            if receipt.get('status')!='ok' or receipt.get('retcode')!=0 or not isinstance(receipt.get('data'),list):
                detail=str(receipt.get('message') or receipt.get('wording') or '').lower()
                if 'packet' in detail and any(term in detail for term in ('不可用','不支持','unavailable','unsupported')):
                    raise ValueError('credential_backend_unavailable')
                raise ValueError('credential_unavailable')
            keys={str(k['type']):str(k['rkey']).removeprefix('&rkey=') for k in receipt['data'] if k.get('rkey') and str(k.get('type')) in ('10','20')}
        if not keys:raise ValueError('credential_unavailable')
        return keys

def native_refs(store,ids):
    result=subprocess.run([sys.executable,str(ROOT/'scripts/image_native_refs.py')],input=json.dumps({'ids':ids[:32]}),text=True,capture_output=True,timeout=45,cwd=ROOT)
    if result.returncode or len(result.stdout)>1024*1024:raise ValueError('native_reference_unavailable')
    data=json.loads(result.stdout)
    if not data.get('ok'):raise ValueError('native_reference_unavailable')
    with store.connect() as db:
        for mid,refs in data['refs'].items():
            row=db.execute('SELECT media_json FROM messages WHERE id=?',(int(mid),)).fetchone()
            if not row:continue
            items=json.loads(row[0]);changed=False
            for item in items:
                md5=(item.get('metadata',{}).get('md5') or '').lower();urls=refs.get(md5)
                if urls:
                    item['metadata']=dict(item.get('metadata',{}),remote_urls=urls);changed=True
            if changed:db.execute('UPDATE messages SET media_json=? WHERE id=? AND media_json=?',(json.dumps(items,ensure_ascii=False),int(mid),row[0]))


class Downloader:
    def __init__(self,store):
        self.store=store;self.stop=threading.Event();self.thread=None;self.rkeys=RKeys();self.error=None;self.last_seed=0;self.seed_after=0
    def start(self):
        if self.store.path.resolve()!=DB.resolve():return
        self.thread=threading.Thread(target=self.run,daemon=True,name='official-image-cache');self.thread.start()
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=5)
    def finish(self,job,code=None):
        now=time.time()
        attempt=job['attempts']+(code!='commit_busy')
        delay=min(3600,30*2**min(max(attempt-1,0),7))
        if code=='commit_busy':
            # Local lock contention says nothing about the CDN or image.
            delay=10
        with self.store.connect() as db:
            db.execute("UPDATE image_download_jobs SET status=?,attempts=?,next_try=?,error_code=?,updated_at=?,lease=NULL WHERE message_id=? AND image_index=? AND md5=? AND lease=? AND status='downloading'",
                ('ready' if not code else 'retry',attempt,now+delay if code else 0,code,now,job['message_id'],job['image_index'],job['md5'],job.get('lease')))
    def commit_bytes(self,raw,row,index,item,job):
        if hashlib.md5(raw).hexdigest()!=job['md5']:raise ValueError('content_hash_mismatch')
        if self.stop.is_set() or not settings(self.store).get('enabled'):return False
        if row['timestamp']<(time.time()-DAYS*86400)*1000:raise ValueError('message_changed')
        if shutil.disk_usage(DATA).free<len(raw)+512*1024*1024:raise ValueError('disk_space_low')
        from .sync import message_commit_lock
        from .image_recheck import commit_image
        digest=hashlib.sha256(raw).hexdigest()
        with message_commit_lock(wait=2, cancel=self.stop), self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            claim=db.execute('SELECT md5,lease,status FROM image_download_jobs WHERE message_id=? AND image_index=?',(row['id'],index)).fetchone()
            if not claim or claim['md5']!=job['md5'] or claim['lease']!=job.get('lease') or claim['status']!='downloading':return False
            existing=list((DATA/'media'/digest[:2]).glob(digest+'.*'))
            restored=cache_image(raw,'image',load_stickers=False,**dict(item.get('metadata',{}),thumbnail=False,recovered_by=MARK))
            if not verified_media_path(restored):raise ValueError('excluded_animation')
            db.execute('''INSERT INTO image_download_cache(digest,path,owned,retain_until,md5) VALUES(?,?,?,?,?)
              ON CONFLICT(digest) DO UPDATE SET retain_until=max(retain_until,excluded.retain_until),md5=excluded.md5''',
                (digest,restored['local_path'],not bool(existing),row['timestamp']/1000+DAYS*86400,job['md5']))
            committed=commit_image(self.store,row,index,restored,self.stop,db=db)
        if not committed:raise ValueError('message_changed')
        self.finish(job);self.error=None;return True
    def fetch_image(self,urls,md5,cfg,cancel,download=None,keys=None):
        if not urls:raise ValueError('native_reference_unavailable')
        if download is None:
            from .mcp_chat_media import download as secure_download
            download=lambda url,cancel:secure_download(url,cancel,max_bytes=MAX_MEDIA_BYTES,
                allowed_target=lambda target:target.scheme=='https' and clean_url(target.geturl()) is not None)
        rkeys = keys
        credential_error = None
        candidates=[]
        for original in urls:
            url=clean_url(original)
            if not url:continue
            parts=urlsplit(url)
            pairs=parse_qsl(parts.query)
            query=dict(pairs)
            appid=query.get('appid')
            candidates.append((appid in ('1406','1407'), query.get('spec') not in (None,'0'),parts,pairs,appid))
        last='remote_unavailable'
        attempted = False
        for _,_,parts,pairs,appid in sorted(candidates,key=lambda candidate:candidate[:2])[:3]:
            if appid in ('1406', '1407'):
                if rkeys is None and credential_error is None:
                    try:
                        rkeys = self.rkeys.get(cfg)
                    except (ValueError, OSError, KeyError, TypeError, httpx.HTTPError) as exc:
                        credential_error = exc
                if rkeys is None:
                    continue
                key = rkeys.get('20' if appid == '1407' else '10')
                if not key:
                    credential_error = ValueError('credential_unavailable')
                    continue
                pairs.append(('rkey', key))
            if cancel.is_set():raise ValueError('paused')
            attempted = True
            try:raw=download(urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(pairs),'')),cancel)
            except ValueError:last='remote_unavailable';continue
            if len(raw)>MAX_MEDIA_BYTES:raise ValueError('download_too_large')
            if hashlib.md5(raw).hexdigest()!=md5:last='content_hash_mismatch';continue
            if cancel.is_set():raise ValueError('paused')
            return raw
        if not attempted and credential_error is not None:
            raise credential_error
        raise ValueError(last)

    def process_one(self,download=None,keys=None):
        cfg=settings(self.store)
        if not cfg.get('enabled') or self.stop.is_set():return False
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time()
            job = db.execute("""SELECT j.* FROM image_download_jobs j
                JOIN messages m ON m.id=j.message_id
                WHERE j.status IN ('pending','retry') AND j.next_try<=? AND m.timestamp>=?
                ORDER BY j.priority DESC,
                         CASE WHEN m.timestamp<? THEN m.timestamp END ASC NULLS LAST,
                         CASE WHEN j.status='retry' THEN j.next_try END ASC NULLS LAST,
                         m.id DESC LIMIT 1""",
                (now, (now-DAYS*86400)*1000, (now-(DAYS-1)*86400)*1000)).fetchone()
            if not job:return False
            lease=uuid.uuid4().hex
            db.execute("UPDATE image_download_jobs SET status='downloading',updated_at=?,lease=? WHERE message_id=? AND image_index=?",(time.time(),lease,job['message_id'],job['image_index']))
            row=hydrate(db.execute('SELECT * FROM messages WHERE id=?',(job['message_id'],)).fetchone())
        job=dict(job,lease=lease);index=job['image_index'];item=media_at(row['media'],index)
        if cfg.get('message_account') and not row['conversation_id'].startswith(cfg['message_account']+':'):
            self.finish(job,'message_account_changed');return True
        if not item or (item.get('metadata',{}).get('md5') or '').lower()!=job['md5']:
            self.finish(job,'identity_changed');return True
        if item.get('kind')!='image' or item.get('status')=='omitted':
            self.finish(job,'excluded_animation');return True
        if verified_media_path(item):self.finish(job);return True
        try:
            with self.store.connect() as db:
                cached=db.execute('SELECT path FROM image_download_cache WHERE md5=? AND retain_until>?',(job['md5'],time.time())).fetchone()
            if cached:
                path=(ROOT/cached['path']).resolve()
                if path.is_relative_to((DATA/'media').resolve()) and path.is_file() and path.stat().st_size<=MAX_MEDIA_BYTES:
                    raw=path.read_bytes()
                    if hashlib.md5(raw).hexdigest()==job['md5']:
                        self.commit_bytes(raw,row,index,item,job);return True
            if not item.get('metadata',{}).get('remote_urls'):
                with self.store.connect() as db:
                    now = time.time()
                    nearby = db.execute("""SELECT j.message_id FROM image_download_jobs j
                        JOIN messages m ON m.id=j.message_id
                        WHERE j.status IN ('pending','retry') AND j.next_try<=? AND m.timestamp>=?
                        GROUP BY j.message_id
                        ORDER BY max(j.priority) DESC,
                                 CASE WHEN m.timestamp<? THEN m.timestamp END ASC NULLS LAST,
                                 j.message_id DESC LIMIT 31""",
                        (now, (now-DAYS*86400)*1000, (now-(DAYS-1)*86400)*1000))
                    ids = [row['id']] + [entry[0] for entry in nearby]
                native_refs(self.store,ids)
                with self.store.connect() as db:
                    current=db.execute('SELECT * FROM messages WHERE id=?',(row['id'],)).fetchone()
                if not current:return True
                row=hydrate(current);item=media_at(row['media'],index)
            if not item:raise ValueError('identity_changed')
            raw=self.fetch_image(item.get('metadata',{}).get('remote_urls',[]),job['md5'],cfg,self.stop,download,keys)
            self.commit_bytes(raw,row,index,item,job);return True
        except Exception as exc:
            allowed={'paused','identity_changed','credential_not_loopback','credential_unavailable','credential_account_changed','credential_offline','credential_connection_error','credential_backend_unavailable','native_reference_unavailable','download_too_large','content_hash_mismatch','excluded_animation','message_changed','remote_unavailable','disk_space_low'}
            code=str(exc) if str(exc) in allowed else 'commit_busy' if isinstance(exc,ValueError) and '正在读取或导入' in str(exc) else 'credential_connection_error' if isinstance(exc,httpx.HTTPError) else 'storage_error' if isinstance(exc,sqlite3.Error) else 'download_failed'
            self.finish(job,code);self.error=code
        return True
    def cleanup(self,now=None):
        now=time.time() if now is None else now
        from .sync import message_commit_lock
        with self.store.connect() as db:
            rows=db.execute('SELECT digest FROM image_download_cache WHERE retain_until<? ORDER BY retain_until LIMIT 4',(now,)).fetchall()
        for row in rows:
            with message_commit_lock(),self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT * FROM image_download_cache WHERE digest=? AND retain_until<?',(row['digest'],now)).fetchone()
                if not current:continue
                row=current
                recent=db.execute("SELECT max(m.timestamp) FROM messages m,json_each(m.media_json) i WHERE instr(m.media_json,?)>0 AND json_extract(i.value,'$.sha256')=? AND m.timestamp>=?",(row['digest'],row['digest'],(now-DAYS*86400)*1000)).fetchone()[0]
                if recent:
                    db.execute('UPDATE image_download_cache SET retain_until=? WHERE digest=?',(recent/1000+DAYS*86400,row['digest']));continue
                protected=db.execute("SELECT 1 FROM messages m,json_each(m.media_json) i WHERE instr(m.media_json,?)>0 AND json_extract(i.value,'$.sha256')=? AND coalesce(json_extract(i.value,'$.metadata.recovered_by'),'')!=? LIMIT 1",(row['digest'],row['digest'],MARK)).fetchone()
                path=(ROOT/row['path']).resolve()
                if row['owned'] and not protected and path.is_relative_to((DATA/'media').resolve()) and path.stem==row['digest']:path.unlink(missing_ok=True)
                db.execute('DELETE FROM image_download_cache WHERE digest=?',(row['digest'],))
    def run(self):
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='qq-image-fetch') as pool:
            pending=set()
            while not self.stop.wait(.1):
                try:
                    for future in list(pending):
                        if future.done():pending.remove(future);future.result()
                    if not settings(self.store).get('enabled'):continue
                    if time.time()-self.last_seed>60:
                        self.seed_after=seed(self.store,after=self.seed_after);self.cleanup();self.last_seed=time.time()
                        with self.store.connect() as db:
                            db.execute("UPDATE image_download_jobs SET status='retry',error_code='worker_restarted',next_try=0,lease=NULL WHERE status='downloading' AND updated_at<?",(time.time()-300,))
                    while len(pending)<2:pending.add(pool.submit(self.process_one))
                except Exception:self.error='worker_unavailable';self.stop.wait(10)


# The request path uses the same credential cooldown as a batch worker, without
# creating jobs or keeping downloaded bytes.
_request_rkeys=RKeys()


def read_on_demand(store,row,index,cancel):
    cfg=settings(store)
    if not cfg.get('on_demand') or row['platform']!='qq' or cancel.is_set():
        return None
    if not cfg.get('message_account') or not row['conversation_id'].startswith(cfg['message_account']+':'):
        return None
    if row['timestamp']<(time.time()-DAYS*86400)*1000:
        return None
    item=media_at(row['media'],index)
    if not item or item.get('kind')!='image' or item.get('status')=='omitted' or item.get('reason_code')=='source_retracted':
        return None
    metadata=item.get('metadata',{})
    md5=metadata.get('md5','')
    if not isinstance(md5,str) or len(md5)!=32 or any(c not in '0123456789abcdefABCDEF' for c in md5):
        return None
    urls=metadata.get('remote_urls')
    if not urls:
        native_refs(store,[row['id']])
        with store.connect() as db:
            current=db.execute('SELECT media_json,source_id,timestamp FROM messages WHERE id=?',(row['id'],)).fetchone()
        if not current or (current['source_id'],current['timestamp'])!=(row['source_id'],row['timestamp']):
            return None
        match=media_at(json.loads(current['media_json']),index)
        if not match or match.get('metadata',{}).get('md5')!=md5:
            return None
        urls=match['metadata'].get('remote_urls')
    reader=Downloader(store)
    reader.rkeys=_request_rkeys
    return reader.fetch_image(urls or [],md5.lower(),cfg,cancel)


def install(app,store):
    from fastapi import Request,HTTPException
    worker=Downloader(store);app.state.image_downloader=worker
    @app.on_event('startup')
    def start():worker.start()
    @app.on_event('shutdown')
    def stop():worker.close()
    @app.put('/api/images/remote')
    def configure(request:Request,body:dict):
        if request.client and request.client.host not in ('127.0.0.1','::1','testclient'):raise HTTPException(403)
        if request.headers.get('sec-fetch-site')=='cross-site' or request.headers.get('origin') not in (None,str(request.base_url).rstrip('/')):raise HTTPException(403)
        if set(body)!={'enabled'} or type(body['enabled']) is not bool:raise HTTPException(400)
        cfg=settings(store)
        if body['enabled'] and not cfg.get('credential_config'):raise HTTPException(400,'尚未配置本机图片凭证来源')
        cfg['enabled']=body['enabled']
        with store.connect() as db:db.execute("INSERT OR REPLACE INTO client_settings VALUES('image_remote',?)",(json.dumps(cfg),))
        return status(store)
