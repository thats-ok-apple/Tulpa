"""Sanitized native metadata/local-file diagnosis; never UI, network or key output."""
import contextlib,hashlib,io,json,re,sys,time
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from chatlocal.store import Store
from chatlocal.config import DATA
from live_reader import Reader
from live_wechat import LiveWeChatDB
from wechatauto.db import MSG_TYPE_NAMES
from reader_media import xml_message


def diagnose():
    meta=json.loads((DATA/'wechat-snapshot-info.json').read_text());runner=Reader('wechat');runner.views=[]
    reader=LiveWeChatDB(db_dir=str(Path(meta['source_db']).parent.parent),account=meta['account'],workdir=str(DATA/'live/wechat'),keys_file=str(DATA/'wechat-reader/keys.json'))
    root=Path(meta['source_db']).parent
    names=set()
    for base in (root/'msg/attach',root/'cache'):
        for path in base.rglob('*.dat'):names.add(path.stem.split('_')[0].lower())
    opened={};out=[]
    try:
        with Store().connect() as local:
            rows=local.execute('''SELECT DISTINCT m.* FROM messages m,json_each(media_json) i WHERE platform='wechat' AND timestamp>=? AND json_extract(i.value,'$.kind')='image' AND json_extract(i.value,'$.status')='unavailable' ''',((time.time()-30*86400)*1000,)).fetchall()
            for row in rows:
                result=dict(message_id=row['id'],native_verified=False)
                for loc, in local.execute('SELECT locator FROM message_native_ids WHERE message_id=?',(row['id'],)):
                    wxid,rel,table,rid=json.loads(loc)
                    if wxid!=reader.wxid or not re.fullmatch(r'message/message_\d+\.db',rel) or table!='Msg_'+hashlib.md5(row['conversation_id'].encode()).hexdigest():continue
                    if rel not in opened:opened[rel]=runner.open(reader._db_path(rel),reader._keys[rel])
                    native=opened[rel].execute(f'SELECT local_id,local_type,server_id,real_sender_id,create_time,message_content,packed_info_data,sort_seq FROM "{table}" WHERE local_id=?',(rid,)).fetchone()
                    if not native or str(native['server_id'])!=row['source_id'] or native['create_time']*1000!=row['timestamp']:continue
                    message=reader._export_row(native,MSG_TYPE_NAMES);content=message.get('content','');xml=xml_message(content)
                    img=xml if xml is not None and xml.tag=='img' else xml.find('.//img') if xml is not None else None
                    md5s={i.get('metadata',{}).get('md5') for i in json.loads(row['media_json'])}
                    if img is not None:md5s.update(img.get(k) for k in ('md5','originsourcemd5'))
                    md5s={h.lower() for h in md5s if isinstance(h,str) and re.fullmatch('[a-fA-F0-9]{32}',h)}
                    result.update(native_verified=True,native_type=native['local_type']&0xffffffff,content_chars=len(content),xml_root=xml.tag if xml is not None else None,is_retraction=bool(xml is not None and xml.tag=='sysmsg' and xml.get('type')=='revokemsg'),has_image_element=img is not None,declared_hashes=len(md5s),local_hash_matches=len(md5s&names),has_http_reference=bool(img is not None and any(v.startswith(('https://','http://')) for v in img.attrib.values())))
                    if xml is None:
                        try:ET.fromstring(content[content.index('<'):])
                        except (ValueError,ET.ParseError) as exc:result['xml_parse_error']=type(exc).__name__
                    break
                out.append(result)
        for view in runner.views:view.view.assert_fresh()
        return out
    finally:
        for view in runner.views:view.close()

if __name__=='__main__':
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):data=diagnose()
    print(json.dumps(data))
