"""Polylens read API adaptation. QR and media cookies have separate owners."""
import base64
from dataclasses import asdict
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

from .errors import Failure
from .net import SafeHTTP, SHORT_HOSTS, WEB_HOSTS, validate_url
from ._vendor.polylens.api._http import HttpClient, BilibiliHttpError
from ._vendor.polylens.api._signing import fetch_nav, sign_params
from ._vendor.polylens.api._video import fetch_view, build_video_info
from ._vendor.polylens.api._search import fetch_search
from ._vendor.polylens.api._feed import fetch_feed
from ._vendor.polylens.api._comments import fetch_comments, fetch_replies
from ._vendor.polylens.api._subtitles import fetch_subtitles
from ._vendor.polylens.api._login import start_qr_login, check_qr_login
from ._vendor.polylens.models import QrStatus

BV = re.compile(r'BV[1-9A-HJ-NP-Za-km-z]{10}\Z')
logging.getLogger('polylens_bilibili').disabled = True


def normalize_video(value, page=None, http=None):
    if not isinstance(value, str) or len(value) > 2048:
        raise Failure('invalid_video', '请输入标准 BV 号或 B站视频链接。')
    value = value.strip()
    inferred = None
    if BV.fullmatch(value):
        bvid = value
    else:
        p = validate_url(value, 'short')
        if p.hostname in SHORT_HOSTS:
            if not re.fullmatch(r'/[A-Za-z0-9]+/?', p.path):
                raise Failure('invalid_video', '短链格式无效。')
            _, _, value = (http or SafeHTTP()).get(value, purpose='short', max_bytes=65536, stop_at_video=True)
            p = validate_url(value, 'short')
        match = re.fullmatch(r'/video/(BV[1-9A-HJ-NP-Za-km-z]{10})/?', p.path)
        if p.hostname not in WEB_HOSTS or not match:
            raise Failure('invalid_video', '短链未指向支持的 B站视频页面。')
        bvid = match.group(1)
        supplied = parse_qs(p.query).get('p')
        if supplied:
            if len(supplied) != 1 or not supplied[0].isdigit():
                raise Failure('invalid_part', '分 P 必须是单个正整数。')
            inferred = int(supplied[0])
    if page is not None and inferred is not None and page != inferred:
        raise Failure('part_conflict', '链接中的分 P 与参数不一致，请明确选择同一分 P。')
    selected = page if page is not None else inferred or 1
    if type(selected) is not int or not 1 <= selected <= 1000:
        raise Failure('invalid_part', '分 P 必须是 1–1000 内的整数。')
    return bvid, selected


class ReadHTTP(HttpClient):
    """Only the fixed GET endpoints survive the upstream adapter boundary."""
    _pace_lock = threading.Lock()
    _last_comment = 0.0

    def __init__(self, cookie='', *, net=None, deadline=None, cancel=None):
        # Do not initialize upstream's proxy or automatic cookie/redirect handlers.
        self.cookie = cookie
        self.account = hashlib.sha256(cookie.encode()).hexdigest()[:16]
        self.api_base = 'https://api.bilibili.com'
        self.net = net or SafeHTTP()
        self.deadline = deadline or time.monotonic() + 30
        self.cancel = cancel or threading.Event()

    def get_bytes(self, url, referer='', *, proxied=False):
        p = urlsplit(url)
        purpose = 'api' if p.hostname == 'api.bilibili.com' else 'qr' if p.hostname == 'passport.bilibili.com' else 'media'
        raw, _, _ = self.net.get(url, purpose=purpose, cookie=self.cookie,
                                deadline=self.deadline, cancel=self.cancel)
        return raw

    def get_json(self, path, params=None, *, referer='', base_url=None, allow_codes=None, proxied=False):
        url = (base_url or self.api_base) + path + ('?' + urlencode(params) if params else '')
        validate_url(url, 'qr' if base_url == 'https://passport.bilibili.com' else 'api')
        if path.startswith('/x/v2/reply/'):
            with self._pace_lock:
                now = time.monotonic()
                if now - type(self)._last_comment < 2:
                    raise Failure('rate_limited', '评论读取间隔至少 2 秒，请稍后续取。', retryable=True, status=429)
                type(self)._last_comment = now
        data = json.loads(self.get_bytes(url))
        if not isinstance(data, dict) or type(data.get('code')) is not int:
            raise Failure('protocol_error', 'B站响应格式不兼容。')
        code = data['code']
        if code in (-352, -509):
            raise Failure('rate_limited', 'B站暂时限制了读取。', retryable=True, status=code)
        if code != 0 and code not in (allow_codes or set()):
            if code in (-101, -111):
                raise Failure('login_required', 'B站登录已失效，请在本地设置页重新扫码。', status=code)
            raise Failure('upstream_rejected', 'B站未提供所请求的内容。', status=code)
        if isinstance(data.get('data'), dict) and 'v_voucher' in data['data']:
            raise Failure('rate_limited', 'B站要求额外验证；请稍后重试。', retryable=True, status=code)
        return data.get('data')

    def get_json_url(self, url, *, proxied=False):
        validate_url(url, 'media')
        return json.loads(self.get_bytes(url))

    def get_bytes_and_set_cookies(self, url):
        raw, headers, _ = self.net.get(url, purpose='qr', cookie='', deadline=self.deadline)
        return raw, [v for k, v in headers if k.lower() == 'set-cookie']

    def get_api_bytes(self, *args, **kwargs):
        raise Failure('action_denied', '未注册的 B站读取或写入接口不可使用。')


def public_item(item):
    data = asdict(item)
    bvid, _ = normalize_video(data['url'])
    return dict(bvid=bvid, title=str(data['title'])[:500], author=data.get('author'),
                duration_seconds=data.get('duration_sec'), description=str(data.get('summary') or '')[:3000],
                url=f'https://www.bilibili.com/video/{bvid}', published_at=data.get('published_at'),
                evidence_level='metadata_only')


class Content:
    def __init__(self, settings, *, net=None):
        self.settings, self.net = settings, net or SafeHTTP()
        self.lock = threading.RLock()
        self.cache = {}
        self.qr = None

    def client(self, *, login=True, purpose='content', cancel=None, deadline=None):
        cookie = self.settings.cookie(purpose)
        if login and not cookie:
            raise Failure('login_required', '请在 Tulpa 的 B站设置页完成只读扫码登录。')
        client = ReadHTTP(cookie, net=self.net, cancel=cancel, deadline=deadline)
        if login and not fetch_nav(client).is_login:
            raise Failure('login_required', 'B站登录已过期，请在设置页重新扫码。')
        return client

    def login_status(self):
        if not self.settings.cookie():
            return dict(state='logged_out')
        try:
            self.client()
            return dict(state='logged_in', scope='搜索、推荐、详情、字幕、评论；无社交写入')
        except Failure as exc:
            return dict(state='expired' if exc.code == 'login_required' else 'unknown', error=exc.result()['error'])

    def start_login(self):
        # Operator-only: never registered as an MCP tool.
        session = start_qr_login(self.client(login=False))
        p = urlsplit(session.url)
        if (p.scheme != 'https' or p.username or p.password or p.port not in (None,443) or
                p.hostname not in ('passport.bilibili.com', 'www.bilibili.com', 'account.bilibili.com') or
                p.hostname=='account.bilibili.com' and p.path!='/h5/account-h5/auth/scan-web'):
            raise Failure('protocol_error', '登录二维码来源不兼容。')
        with self.lock:
            self.qr = dict(key=session.key, url=session.url, expires=time.time()+180, last_poll=0,
                           generation=self.settings.load()['generation'])
        return session.url

    def poll_login(self):
        with self.lock:
            qr = self.qr
            if not qr or time.time() >= qr['expires']:
                self.qr = None
                return dict(state='expired')
            self.settings.assert_generation(qr['generation'])
            if time.time()-qr['last_poll'] < 3:
                return dict(state='waiting')
            qr['last_poll'] = time.time()
            result = check_qr_login(self.client(login=False), qr['key'])
            if result.status == QrStatus.SUCCESS:
                if not result.cookie or 'SESSDATA=' not in result.cookie:
                    raise Failure('protocol_error', '登录未返回有效凭据，请重新扫码。')
                self.settings.set_cookie(result.cookie)
                self.qr = None
            return dict(state=result.status.value)

    def info(self, video, page=None, *, client=None):
        bvid, selected = normalize_video(video, page, self.net)
        client = client or self.client()
        raw = fetch_view(client, dict(bvid=bvid))
        if raw.get('bvid') != bvid:
            raise Failure('video_mismatch', '返回视频与请求不一致。')
        parts = raw.get('pages') or []
        found = next((p for p in parts if p.get('page') == selected), None)
        if not found or type(found.get('cid')) is not int or found['cid'] <= 0:
            raise Failure('part_unavailable', '这个分 P 不存在或无法确认 cid。')
        info, aid, cid = build_video_info(raw, selected)
        data = public_item(info)
        data.update(page=selected, cid=cid, aid=aid, cover=info.cover_url,
                    parts=[dict(page=p['page'], cid=p['cid'], title=str(p.get('part', ''))[:300],
                                duration_seconds=p.get('duration')) for p in parts[:1000]],
                    part_count=len(parts), fetched_at=time.time(), source='bilibili_view', content_trust='untrusted_data')
        return data

    def _remember(self, items, next_remote, binding):
        with self.lock:
            now = time.time()
            self.cache = {k:v for k,v in self.cache.items() if v['expires'] > now}
            if len(self.cache) >= 32:
                self.cache.pop(next(iter(self.cache)))
            key = uuid.uuid4().hex
            self.cache[key] = dict(items=items, next_remote=next_remote, binding=binding, expires=now+300)
            return key

    def _page(self, limit, cursor, binding, load):
        if not 1 <= limit <= 20:
            raise Failure('invalid_limit', '每次列表最多 20 条。')
        if cursor:
            try:
                key, offset_text = cursor.split(':')
                offset = int(offset_text)
                with self.lock:
                    batch = self.cache[key]
                if batch['expires'] <= time.time() or batch['binding'] != binding or not 0 <= offset <= len(batch['items']):
                    raise ValueError()
            except (ValueError, KeyError):
                raise Failure('cursor_expired', '分页游标已失效或属于另一查询，请从第一页重试。') from None
            if offset == len(batch['items']) and batch['next_remote'] is not None:
                items, remote = load(batch['next_remote'])
                key = self._remember(items, remote, binding)
                batch, offset = self.cache[key], 0
        else:
            items, remote = load(None)
            key = self._remember(items, remote, binding)
            batch, offset = self.cache[key], 0
        items = batch['items'][offset:offset+limit]
        end = offset+len(items)
        more = end < len(batch['items']) or batch['next_remote'] is not None
        return dict(items=items, has_more=more, next_cursor=f'{key}:{end}' if more else None,
                    fetched_at=time.time(), content_trust='untrusted_data')

    def search(self, query, order='relevance', limit=10, cursor=None):
        client = self.client()
        binding = (self.settings.load()['generation'], 'search', query, order)
        def load(remote):
            result = fetch_search(client, query, order=order, cursor=remote)
            return [public_item(x) for x in result.items], result.next_cursor if result.has_more else None
        return self._page(limit, cursor, binding, load)

    def recommended(self, limit=10, cursor=None, refresh=False):
        client = self.client()
        if cursor and refresh:
            raise Failure('invalid_arguments', '续页和刷新不能同时使用。')
        binding = (self.settings.load()['generation'], 'feed')
        def load(_):
            with self.lock:
                prior = self.cache.get('feed')
                if not refresh and prior and prior['expires'] > time.time() and prior['binding'] == binding:
                    return prior['items'], None
            items = list({x['bvid']:x for x in map(public_item, fetch_feed(client))}.values())
            with self.lock:
                self.cache['feed'] = dict(items=items, next_remote=None, binding=binding, expires=time.time()+60)
            return items, None
        result = self._page(limit, cursor, binding, load)
        result['pagination_note'] = '游标只遍历本次推荐批；refresh 获取新批，平台不保证可重放。批内去重。'
        return result

    def comments(self, video, sort='hot', limit=10, cursor=None, reply_to=None, reply_page=1):
        bvid, _ = normalize_video(video, http=self.net)
        client = self.client()
        info = self.info(bvid, client=client)
        binding=(self.settings.load()['generation'],'comments',bvid,sort,reply_to,reply_page)
        def load(remote):
            if reply_to:
                pn=int(remote) if remote else reply_page
                batch=fetch_replies(client,info['aid'],[reply_to],start_page=pn,pages=1)
                thread=next((t for t in batch.threads if t.comment_id==reply_to),None)
                if thread is None or thread.error:
                    raise Failure('comments_unavailable','此评论的回复暂时不可读取。',retryable=True)
                return [self._comment(x) for x in thread.page.items], str(pn+1) if thread.page.has_more else None
            page=fetch_comments(client,info['aid'],count=20,cursor=remote,sort=sort)
            if page.rate_limited and not page.items:
                raise Failure('rate_limited','评论读取被限频，请稍后继续。',retryable=True)
            return [self._comment(x) for x in page.items], page.next_cursor if page.has_more else None
        # Upstream returns whole pages (and pinned comments), sometimes > count.
        # Retain overflow in bounded batches; never silently drop it via [:limit].
        result=self._page(limit,cursor,binding,load)
        result['content_trust']='untrusted_opinions_not_video_facts'
        return dict(**result, bvid=bvid, reply_to=reply_to, source='audience_comments',
                    completeness='平台当前可见评论；删除、折叠及未返回内容不在此范围。')

    @staticmethod
    def _comment(item):
        d = asdict(item)
        return {k:d[k] for k in ('id', 'author', 'content', 'parent_id', 'created_at', 'like_count', 'reply_count')}

    def subtitles(self, info, *, cancel=None, deadline=None):
        client = self.client(cancel=cancel, deadline=deadline)
        track = fetch_subtitles(client, info['aid'], info['cid'])
        return dict(language=track.lang, entries=[asdict(x) for x in track.entries], available_languages=track.available_langs)

    def play(self, info, *, cancel=None, deadline=None):
        # Separate media credential, anonymous unless explicitly configured in UI.
        client = self.client(login=False, purpose='media', cancel=cancel, deadline=deadline)
        nav = fetch_nav(client)
        params = sign_params(dict(bvid=info['bvid'], cid=info['cid'], qn=32, fnval=16, fourk=0), nav.img_key, nav.sub_key)
        data = client.get_json('/x/player/wbi/playurl', params)
        if not isinstance(data, dict):
            raise Failure('media_unavailable', '未取得可读取的视频资源。')
        if data.get('is_preview'):
            raise Failure('preview_only', '平台只提供试看，未作为完整视频下载。')
        dash = data.get('dash') or {}
        videos = [v for v in dash.get('video', []) if v.get('codecs', '').startswith('avc') and v.get('height', 9999) <= 720]
        audios = dash.get('audio') or []
        if videos:
            video = min(videos, key=lambda v:v.get('bandwidth', 10**12))
            audio = min(audios, key=lambda v:v.get('bandwidth', 10**12)) if audios else None
            return dict(video=video.get('baseUrl') or video.get('base_url'),
                        audio=(audio.get('baseUrl') or audio.get('base_url')) if audio else None,
                        duration_seconds=dash.get('duration'), has_audio=bool(audio))
        segments = data.get('durl') or []
        if len(segments) == 1:
            return dict(video=segments[0].get('url'), audio=None, has_audio=True,
                        duration_seconds=data.get('timelength', 0)/1000)
        raise Failure('media_unavailable', '没有兼容的有界媒体流；不绕过登录、付费或地区限制。')
