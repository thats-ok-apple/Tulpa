"""Private IPC: hydrate only declared image references for existing recent rows."""
import contextlib,io,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from chatlocal.config import DATA
from chatlocal.store import Store
from chatlocal.image_remote import DAYS
from chatlocal.qq_image_refs import from_fields
from live_reader import Reader
from live_vfs import configure_qq_worker
import chatlog_keeper.qq_db as q


def fetch(ids):
    configure_qq_worker();meta=json.loads((DATA/'qq-snapshot-info.json').read_text())
    reader=Reader('qq');reader.views=[];out={}
    try:
        db=reader.open(Path(meta['source'])/'nt_msg.db',q.load_cached_key_for_account(meta['account']) or q.load_cached_key())
        with Store().connect() as local:
            for mid in ids[:32]:
                row=local.execute("SELECT conversation_id,source_id,timestamp FROM messages WHERE id=? AND platform='qq'",(mid,)).fetchone()
                if not row or not (time.time()-DAYS*86400)*1000<=row['timestamp']<=(time.time()+300)*1000:continue
                parts=row['conversation_id'].split(':',2)
                if len(parts)!=3 or parts[0]!=meta['account'] or not parts[2].isdigit() or not row['source_id'].lstrip('-').isdigit():continue
                spec=next((s for s in q._QQ_MESSAGE_TABLE_SPECS if s.conversation_type==parts[1]),None)
                if not spec:continue
                rows=db.execute(f'SELECT "40001","40050","40800" FROM "{spec.table}" WHERE "40027"=? AND "40001"=? LIMIT 2',(int(parts[2]),row['source_id'])).fetchall()
                if len(rows)!=1 or str(rows[0][0])!=row['source_id'] or int(rows[0][1])*1000!=row['timestamp']:continue
                found={}
                def walk(blob,depth=0):
                    if depth>4:return
                    fs=q._proto_parse(blob)
                    md5=fs.get(45406,[(None,None)])[0][1]
                    if isinstance(md5,bytes) and len(md5)==16:
                        urls=from_fields(fs)
                        if urls:found[md5.hex()]=urls
                    # Quoted images are not attachments of the containing message.
                    for typ,child in fs.get(40800,[]):
                        if typ=='bytes':walk(child,depth+1)
                walk(rows[0][2]);out[str(mid)]=found
        for view in reader.views:view.view.assert_fresh()
        return out
    finally:
        for view in reader.views:view.close()

if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.readline())
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):result=fetch(request['ids'])
        print(json.dumps(dict(ok=True,refs=result)))
    except Exception:print(json.dumps(dict(ok=False,error='native_reference_unavailable')))
