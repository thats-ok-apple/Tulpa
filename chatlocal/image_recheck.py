"""Recheck authorized images and atomically replace derived media only."""
import json
from .config import DATA
from .media import media_at,media_path


def _commit_image(db,row,index,item,cancel):
    from .image_index import register
    current=db.execute('SELECT media_json,timestamp,source_id FROM messages WHERE id=?',(row['id'],)).fetchone()
    if not current or current['source_id']!=row['source_id'] or current['timestamp']!=row['timestamp'] or json.loads(current['media_json'])!=row['media'] or cancel.is_set():return None
    items=json.loads(current['media_json']);slot=next((n for n,i in enumerate(items) if i.get('source_index',n)==index),None)
    if slot is None:return None
    items[slot]=dict(item,source_index=index)
    db.execute('UPDATE messages SET media_json=? WHERE id=?',(json.dumps(items,ensure_ascii=False),row['id']))
    register(db,row['id'],items,current['timestamp'],force=True)
    return items[slot]


def commit_image(store,row,index,item,cancel,*,db=None):
    from .sync import message_commit_lock
    if cancel.is_set() or not media_path(item):return None
    if db is not None:return _commit_image(db,row,index,item,cancel)
    with message_commit_lock(),store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        return _commit_image(connection,row,index,item,cancel)


def _wechat_local(store,row,metadata,cancel,image_writer=None):
    from .read_preferences import wechat_images_enabled
    if not metadata.get('md5') or not wechat_images_enabled(store):
        return None
    with store.connect() as db:
        locators=db.execute("SELECT locator FROM message_native_ids WHERE message_id=? AND platform='wechat'",
                            (row['id'],)).fetchall()
    if not locators or cancel.is_set():
        return None
    from scripts.refresh_media import parser_for
    parser,_=parser_for('wechat',False,live=True)
    if not any(json.loads(loc[0])[0]==parser.wxid for loc in locators) or cancel.is_set():
        return None
    if image_writer is not None:parser.image_writer=image_writer
    _,items,_=parser.parse(dict(chat=row['conversation_id'],create_time=row['timestamp']//1000,
                                type_code=3,md5=metadata['md5'],content=''))
    return items[0] if items else None


def recheck(store,row,index,cancel):
    old=media_at(row.get('media',[]),index)
    if not old or old.get('kind')!='image' or old.get('reason_code')=='source_retracted' or cancel.is_set():return old
    metadata=old.get('metadata',{})
    if not metadata.get('md5') and not metadata.get('filename'):return old
    try:
        if row['platform']=='wechat':
            item=_wechat_local(store,row,metadata,cancel)
            if not item or cancel.is_set():return old
            if not media_path(item):return dict(old,reason_code=item.get('reason_code','unknown'))
            return commit_image(store,row,index,item,cancel) or old
        if row['platform']!='qq':return old
        meta=json.loads((DATA/'qq-snapshot-info.json').read_text())
        if not row['conversation_id'].startswith(str(meta['account'])+':'):return old
        from scripts.reader_media import QQMedia
        item=QQMedia(None,meta['source'],load_stickers=False).resolve('image',metadata.get('md5'),metadata.get('filename',''),row['timestamp'])
        if not media_path(item) or cancel.is_set():return old
        if metadata.get('remote_urls'):item=dict(item,metadata=dict(item.get('metadata',{}),remote_urls=metadata['remote_urls']))
        return commit_image(store,row,index,item,cancel)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):return old



def read_local_once(store,row,index,cancel):
    """Read just the selected source image, with no persistent cache write."""
    from .media import cache_image
    old=media_at(row.get('media',[]),index)
    if not old or old.get('kind')!='image' or old.get('status')=='omitted' or old.get('reason_code')=='source_retracted' or cancel.is_set():
        return None,old
    metadata=old.get('metadata',{})
    raw=None

    def collect(data,*args,**kwargs):
        nonlocal raw
        item=cache_image(data,*args,persist=False,**kwargs)
        if item['status']=='available':
            raw=data
        return item

    try:
        if row['platform']=='wechat':
            item=_wechat_local(store,row,metadata,cancel,collect)
        elif row['platform']=='qq':
            meta=json.loads((DATA/'qq-snapshot-info.json').read_text())
            if not row['conversation_id'].startswith(str(meta['account'])+':'):
                return None,old
            from scripts.reader_media import QQMedia
            reader=QQMedia(None,meta['source'],load_stickers=False,scan=False,image_writer=collect)
            item=reader.resolve('image',metadata.get('md5'),metadata.get('filename',''),row['timestamp'])
        else:
            return None,old
        return raw,item or old
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):
        return None,old
