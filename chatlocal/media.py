"""Local immutable image cache. No network fetches and no import-time analysis."""
import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from .config import DATA, ROOT, local_path
from .normalize import safe_reply

MAX_MEDIA_BYTES=20*1024*1024
MAX_PIXELS=30_000_000
FORMATS={'JPEG':('jpg','image/jpeg'),'PNG':('png','image/png'),
         'GIF':('gif','image/gif'),'WEBP':('webp','image/webp'),
         'MPO':('mpo','image/jpeg')}
STICKER_PLACEHOLDER='[动画表情]'
REASONS={'source_missing':'本机不存在对应图片文件','metadata_missing':'缺少图片定位信息',
         'decrypt_failed':'图片解密失败或缺少有效密钥','format_unsupported':'图片格式或尺寸暂不支持',
         'cache_corrupt':'图片缓存损坏或尚未完整写入','read_disabled':'图片读取已关闭',
         'unknown':'媒体不可用，需重新检查本机缓存',
         'source_retracted':'原消息已被撤回，不能重新补取这张图片',
         'retention_expired':'Tulpa 下载缓存已超过最近7天保留窗口'}


class UnsupportedMedia(ValueError):
    pass


def omitted_sticker(kind='sticker',**metadata):
    return dict(kind=kind,status='omitted',metadata=metadata)


def is_sticker(item):
    return (item.get('kind',item.get('type')) in ('sticker','gif')
            or item.get('animated') is True or str(item.get('format','')).lower()=='gif')


def unavailable(kind='image',reason='本机缓存中未找到媒体',reason_code='source_missing',**metadata):
    return dict(kind=kind,status='unavailable',local_path=None,reason=REASONS.get(reason_code,REASONS['unknown']),
                reason_code=reason_code if reason_code in REASONS else 'unknown',metadata=metadata)


def cache_image(data,kind='image',load_stickers=True,*,persist=True,**metadata):
    """Validate decoded bytes, infer actual format (QQ may name a GIF .jpg)."""
    if len(data)>MAX_MEDIA_BYTES: raise ValueError('媒体超过20MB上限')
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt=im.format
            if fmt not in FORMATS or im.width*im.height>MAX_PIXELS:
                raise UnsupportedMedia('媒体格式或尺寸暂不支持')
            size=im.size
            frames=getattr(im,'n_frames',1)
            if fmt == 'MPO':
                # An MPO is a set of still JPEGs. Check the same frames the reader returns.
                for index in sorted({0, frames // 2, frames - 1}):
                    im.seek(index)
                    if im.width * im.height > MAX_PIXELS:
                        raise UnsupportedMedia('媒体格式或尺寸暂不支持')
            im.verify()
    except SyntaxError as exc:
        # Pillow reports invalid PNG chunk checksums as SyntaxError. Normalize
        # that data error for all import/read callers' existing media fallback;
        # never cache bad bytes or abort the rest of a message batch.
        raise ValueError('图片数据损坏或尚未完整写入') from exc
    animated = frames > 1 and fmt != 'MPO'
    if not load_stickers and (kind in ('sticker','gif') or animated or fmt=='GIF'):
        return omitted_sticker(kind,**metadata)
    ext,mime=FORMATS[fmt]
    digest=hashlib.sha256(data).hexdigest()
    target=None
    if persist:
        target=DATA/'media'/digest[:2]/(digest+'.'+ext)
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            import tempfile,os
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as handle:
                temporary=Path(handle.name);handle.write(data)
            try:os.replace(temporary,target)
            finally:temporary.unlink(missing_ok=True)
    return dict(kind='gif' if frames>1 and fmt=='GIF' else kind,status='available',
                local_path=target.relative_to(ROOT).as_posix() if target else None,sha256=digest,format=fmt.lower(),
                mime=mime,width=size[0],height=size[1],frames=frames,animated=animated,metadata=metadata)


def import_media(entries,load_stickers=True):
    """An export may reference project-local files only; never arbitrary host paths."""
    if not isinstance(entries,list) or len(entries)>16: raise ValueError('每条消息最多16项媒体')
    result=[]
    for item in entries:
        if not isinstance(item,dict): raise ValueError('media 项必须是对象')
        kind=item.get('kind',item.get('type','image'))
        if kind not in ('image','sticker','gif'): continue
        metadata=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
        metadata={k:v for k,v in metadata.items() if k in ('md5','filename','thumbnail','recovered_by','original_format','caption')}
        from .qq_image_refs import clean_url
        refs=item['metadata'].get('remote_urls') if isinstance(item.get('metadata'),dict) else None
        if isinstance(refs,list):
            urls=list(dict.fromkeys(u for v in refs[:3] if (u:=clean_url(v))))
            if urls:metadata['remote_urls']=urls
        if item.get('status')=='omitted' or (not load_stickers and is_sticker(item)):
            result.append(omitted_sticker(kind,**metadata));continue
        path=item.get('local_path') or item.get('path')
        if not path:
            result.append(unavailable(kind,reason_code=item.get('reason_code','unknown'),**metadata));continue
        try:
            source=local_path(path)
            if not (source.is_relative_to(DATA/'media') or source.is_relative_to(ROOT/'imports')):
                raise ValueError('媒体路径必须位于data/media或imports')
            if source.stat().st_size>MAX_MEDIA_BYTES: raise ValueError('媒体过大')
            result.append(cache_image(source.read_bytes(),kind,load_stickers=load_stickers,**metadata))
        except FileNotFoundError:
            result.append(unavailable(kind,reason_code='source_missing',**metadata))
        except UnsupportedMedia:
            result.append(unavailable(kind,reason_code='format_unsupported',**metadata))
        except (OSError,ValueError,Image.DecompressionBombError):
            result.append(unavailable(kind,reason_code='cache_corrupt',**metadata))
    return result


def media_path(item):
    if item.get('status')!='available' or not item.get('local_path'): return None
    path=local_path(item['local_path'])
    if not path.is_relative_to(DATA/'media') or not path.is_file(): return None
    if path.suffix not in {'.'+v[0] for v in FORMATS.values()}: return None
    return path


def verified_media_path(item):
    """Existence alone cannot prove an immutable image cache is still valid."""
    try:
        path=media_path(item)
        if not path or path.stat().st_size>MAX_MEDIA_BYTES:return None
        raw=path.read_bytes()
        if item.get('sha256') and hashlib.sha256(raw).hexdigest()!=item['sha256']:return None
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>MAX_PIXELS:return None
            image.load()
        return path
    except (OSError,ValueError,Image.DecompressionBombError):return None


def media_at(items,index):
    # Filtering a sticker must not retarget an old /media/message/index URL.
    return next((item for slot,item in enumerate(items) if item.get('source_index',slot)==index),None)


def hydrate(row):
    row=dict(row)
    # Kept only for source-ID conflict checks and reversible import policy.
    row.pop('original_content',None)
    row['media']=json.loads(row.pop('media_json','[]'))
    reply=row.get('reply_to')
    row['reply_to']=safe_reply(json.loads(reply) if isinstance(reply,str) and reply else reply)
    return row


def public_media(mid,items):
    """For tool/API clients: no file paths, client resource URLs or decryption keys."""
    result=[]
    for index,item in enumerate(items):
        index=item.get('source_index',index)
        entry={k:item.get(k) for k in ('kind','format','width','height','frames','animated')}
        ready=media_path(item) is not None
        entry.update(index=index,available=ready,thumbnail=bool(item.get('metadata',{}).get('thumbnail')))
        if ready: entry['url']=f'/media/{mid}/{index}'
        else:
            code=item.get('reason_code','source_missing' if item.get('status')=='available' else 'unknown')
            entry.update(reason=REASONS.get(code,REASONS['unknown']),reason_code=code)
        result.append(entry)
    return result
