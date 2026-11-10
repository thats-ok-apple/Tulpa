"""The only external write: a validated native Bilibili card to an allowed QQ group."""
import hashlib
import json
import re
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

import httpx

from .content import BV
from .errors import Failure


class Uncertain(Failure):
    pass


class QQ:
    ACTIONS = frozenset(('get_login_info','get_group_list','get_version_info','get_mini_app_ark','send_group_msg'))

    def __init__(self, settings, *, config_provider=None, transport=None):
        self.settings, self.transport = settings, transport
        self.config_provider = config_provider

    def config(self):
        if self.config_provider:
            return self.config_provider()
        root = self.settings.load().get('qq_root')
        if not root:
            raise Failure('qq_not_configured', '请在 Tulpa 的 OneBot 设置中完成 QQ 连接。')
        from chatlocal.onebot import configuration
        return configuration(Path(root))

    def call(self, action, data):
        if action not in self.ACTIONS:
            raise Failure('action_denied', '未开放此 QQ 接口。')
        config = self.config()
        from chatlocal.snowluma_managed import guard
        try:
            guard(config)
        except ValueError:
            raise Failure('qq_not_ready', 'QQ 实例绑定或实时连接尚未就绪。') from None
        url = config.get('url', '').rstrip('/')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Failure('qq_not_configured', 'QQ 只允许使用已配置的本机 OneBot 服务。')
        writing = action=='send_group_msg'
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(12,connect=3), trust_env=False, follow_redirects=False) as client:
                with client.stream('POST',url+'/'+action, json=data,
                     headers={'Authorization':'Bearer '+config['token']} if config.get('token') else {}) as response:
                    if response.status_code in (401,403,404,429):
                        raise Failure('qq_rejected','QQ 接口拒绝操作，请核对授权、版本或频率。',status=response.status_code)
                    if response.status_code != 200:
                        raise ValueError()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>512*1024:
                            raise ValueError()
            result = json.loads(raw)
            if not isinstance(result,dict) or type(result.get('retcode')) is not int or result.get('status') not in ('ok','failed'):
                raise ValueError()
            if result['status']!='ok' or result['retcode']!=0:
                raise Failure('qq_rejected','QQ 明确拒绝了此次操作。',status=result['retcode'])
            return result.get('data')
        except Failure:
            raise
        except (httpx.ConnectError,httpx.ConnectTimeout):
            raise Failure('qq_unreachable','无法连接 QQ 服务，尚未发送。',retryable=True) from None
        except (httpx.HTTPError,ValueError,TypeError):
            if writing:
                raise Uncertain('send_uncertain','未取得可靠发送回执，可能已送达。请核对 QQ，不得自动重试或更换幂等键。') from None
            raise Failure('qq_protocol_error','QQ 只读响应失败或格式不兼容。',retryable=True) from None

    def identity(self):
        data = self.call('get_login_info',{})
        if not isinstance(data,dict) or not str(data.get('user_id','')).isdigit():
            raise Failure('qq_not_logged_in','QQ 尚未返回有效的登录账号。')
        return str(data['user_id'])


def unwrap_ark(value, bvid):
    """Unwrap exactly as needed; never serialize the OneBot response envelope."""
    for _ in range(4):
        if isinstance(value,str):
            try:
                value=json.loads(value)
            except ValueError:
                break
        if isinstance(value,dict) and isinstance(value.get('app'),str):
            break
        if isinstance(value,dict) and 'data' in value:
            value=value['data']
        else:
            break
    if not isinstance(value,dict) or value.get('app') not in ('com.tencent.miniapp','com.tencent.miniapp_01') or not isinstance(value.get('meta'),dict):
        raise Failure('ark_incompatible','SL 未返回原生小程序 Ark 正文，已停止分享。')
    detail = value['meta'].get('detail_1')
    if not isinstance(detail,dict) or str(detail.get('appid'))!='1109937557':
        raise Failure('ark_incompatible','卡片不是预期的 B站小程序。')
    # Verify the exact native path returned by SL, not a title/link look-alike.
    candidates=[v for v in detail.values() if isinstance(v,str) and 'pages/video/video' in v]
    from urllib.parse import unquote, parse_qs
    matched=False
    for item in candidates:
        decoded=unquote(item)
        if 'pages/video/video?' not in decoded:
            continue
        query=decoded.split('pages/video/video?',1)[1].split('#',1)[0]
        if parse_qs(query).get('bvid')==[bvid]:
            matched=True
    # Live SL 2.1.1 returns an opaque QQ-generated native link. Preserve it
    # unchanged and report that the requested BV mapping needs client testing.
    opaque = (value['app']=='com.tencent.miniapp_01' and not candidates and
              isinstance(detail.get('url'),str) and
              re.fullmatch(r'(?:https://)?m\.q\.qq\.com/a/s/[a-fA-F0-9]{32}',detail['url']) is not None)
    if not matched and not opaque:
        raise Failure('ark_target_mismatch','返回卡片无法核对到指定 BV；未发送，也未改写 SL 结果。')
    encoded=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    if len(encoded.encode())>65536:
        raise Failure('ark_incompatible','卡片正文超过安全上限。')
    return encoded, dict(native_target_verified=matched, opaque_qq_target=opaque,
                         requested_native_path=f'pages/video/video?bvid={bvid}', client_click_verified=False,
                         desktop_web_url_present=any(isinstance(v,str) and v.startswith('https://www.bilibili.com/video/'+bvid) for v in detail.values()))


class Shares:
    def __init__(self, settings, store, content, qq=None):
        self.settings,self.store,self.content=settings,store,content
        self.qq=qq or QQ(settings)
        self.lock=threading.RLock()

    @staticmethod
    def binding(account,group,bvid,key):
        value=json.dumps([account,group,bvid,key],separators=(',',':'))
        return hashlib.sha256(value.encode()).hexdigest(),value

    def _policy(self, account, group, generation, policy):
        self.settings.assert_generation(generation)
        if policy is None:
            raise Failure('group_not_authorized','须通过 Tulpa 统一 MCP 连接的分享权限与群范围执行。')
        policy(account, group)
        if self.qq.identity()!=account:
            raise Failure('qq_account_mismatch','QQ 当前账号与授权账号不一致，未发送。')

    def send(self,qq_account,group_id,bvid,idempotency_key,*,policy=None):
        if not BV.fullmatch(bvid):
            raise Failure('invalid_video','分享只接受标准 BV 号。')
        generation=self.settings.load()['generation']
        key,binding=self.binding(qq_account,group_id,bvid,idempotency_key)
        with self.lock:
            self._policy(qq_account,group_id,generation,policy)
            with self.store.db() as db:
                existing=db.execute('SELECT * FROM shares WHERE key=?',(key,)).fetchone()
            if existing:
                result=json.loads(existing['result']) if existing['result'] else {}
                return dict(status=existing['state'],duplicate=True,request_id=key,**result)
            info=self.content.info(bvid)
            with self.store.db() as db:
                recent=db.execute("SELECT 1 FROM shares WHERE created>? AND state IN ('succeeded','sending','uncertain') LIMIT 1",(time.time()-10,)).fetchone()
                if recent:
                    raise Failure('share_cooldown','分享间隔至少 10 秒。',retryable=True)
            cover=info.get('cover')
            if not isinstance(cover,str):
                raise Failure('cover_missing','视频没有可核验的封面地址。')
            from .net import validate_url
            if cover.startswith('http://'):
                cover='https://'+cover[7:]
            validate_url(cover,'media')
            card=self.qq.call('get_mini_app_ark',dict(type='bili',title=info['title'],
                desc=info.get('author') or '哔哩哔哩',picUrl=cover,jumpUrl=f'pages/video/video?bvid={bvid}'))
            body,diagnostic=unwrap_ark(card,bvid)
            if diagnostic['opaque_qq_target']:
                # 2026-10-24, unmodified SL 2.1.1: send receipt succeeded but
                # the user reported "mini-program removed" on opening this shape.
                # Do not repeat a known unsuccessful client flow or forge a URL.
                raise Failure('native_card_unverified', '此类 QQ 小程序卡片实测提示已下架，且无法核对原生跳转目标；已停止发送，等待上游兼容核验。', retryable=False)
            self._policy(qq_account,group_id,generation,policy)
            with self.store.db() as db:
                # Atomic insert also deduplicates an accidental second server process.
                inserted=db.execute('INSERT OR IGNORE INTO shares VALUES(?,?,?,?,?,?,?)',
                    (key,binding,generation,'sending',time.time(),time.time(),None)).rowcount
                if not inserted:
                    return dict(status='uncertain',duplicate=True,request_id=key,
                                note='另一请求已开始执行，请查询原回执，不会再次发送。')
            base=dict(qq_account=qq_account,group_id=group_id,bvid=bvid,**diagnostic)
            try:
                receipt=self.qq.call('send_group_msg',dict(group_id=int(group_id),message=[dict(type='json',data=dict(data=body))]))
                if not isinstance(receipt,dict) or type(receipt.get('message_id')) is not int:
                    raise Uncertain('receipt_uncertain','QQ 未返回可靠 message_id，请核对客户端，不自动重试。')
                state='succeeded'
                base.update(message_id=receipt['message_id'],sent_at=time.time(),
                            note='SL 接口已确认发送；手机和桌面打开行为仍需用户核验。')
            except Failure as exc:
                state='uncertain' if isinstance(exc,Uncertain) else 'failed'
                base.update(error=exc.result()['error'])
            except BaseException:
                # Persist unknown even if interrupted during the write.
                with self.store.db() as db:
                    db.execute("UPDATE shares SET state='uncertain',updated=? WHERE key=?",(time.time(),key))
                raise
            with self.store.db() as db:
                db.execute('UPDATE shares SET state=?,updated=?,result=? WHERE key=?',
                           (state,time.time(),json.dumps(base),key))
            return dict(status=state,request_id=key,duplicate=False,**base)
