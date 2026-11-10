"""GET-only HTTPS transport with per-hop allowlists and DNS pinning.

No proxy environment, cookie jar, automatic redirects, or network input to ffmpeg.
TLS still verifies the original hostname while the socket connects to a checked IP.
"""
import http.client
import base64
import ipaddress
import json
import socket
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .errors import Failure

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'
API_PATHS = frozenset(('/x/web-interface/nav', '/x/web-interface/view',
    '/x/web-interface/wbi/search/type', '/x/web-interface/wbi/index/top/feed/rcmd',
    '/x/v2/reply/wbi/main', '/x/v2/reply/reply', '/x/player/wbi/v2', '/x/player/wbi/playurl'))
QR_PATHS = frozenset(('/x/passport-login/web/qrcode/generate', '/x/passport-login/web/qrcode/poll'))
CDNS = ('bilivideo.com', 'bilivideo.cn', 'bilivideo.net', 'hdslb.com', 'biliimg.com')
SHORT_HOSTS = frozenset(('b23.tv', 'bili2233.cn'))
WEB_HOSTS = frozenset(('www.bilibili.com', 'm.bilibili.com', 'bilibili.com'))


def validate_url(url, purpose):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url) or '\\' in url:
        raise Failure('unsafe_url', '链接格式不安全。')
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            raise ValueError()
        host = p.hostname or ''
        if not host or host.endswith('.') or host.encode('idna').decode() != host:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise Failure('unsafe_url', '只接受允许域名上的标准 HTTPS 链接。') from None
    allowed = (purpose == 'api' and host == 'api.bilibili.com' and p.path in API_PATHS or
               purpose == 'qr' and host == 'passport.bilibili.com' and p.path in QR_PATHS or
               purpose == 'media' and any(host == d or host.endswith('.' + d) for d in CDNS) or
               purpose == 'short' and (host in SHORT_HOSTS or host in WEB_HOSTS and p.path.startswith('/video/')))
    if not allowed:
        raise Failure('url_not_allowed', '此域名或接口不在 B站只读允许范围内。')
    return p


def public_addresses(host, resolver=socket.getaddrinfo):
    try:
        records = resolver(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(r[4][0] for r in records))
    except OSError:
        raise Failure('dns_failed', '无法解析 B站服务地址。', retryable=True) from None
    if not addresses or any(not ipaddress.ip_address(a).is_global or
                            getattr(ipaddress.ip_address(a), 'ipv4_mapped', None) is not None for a in addresses):
        raise Failure('unsafe_address', '地址解析到本机或非公网网络，已拒绝请求。')
    return addresses


def resolving_addresses(host, resolver=socket.getaddrinfo):
    """Some local VPNs synthesize 198.18/15. Resolve that case through pinned DoH.

    This is not permission to connect to the synthetic/private address. Every
    resulting address is checked again and then pinned for the actual request.
    Loopback/RFC1918 answers remain a hard failure, never trigger this fallback.
    """
    try:
        records = resolver(host,443,type=socket.SOCK_STREAM)
    except OSError:
        raise Failure('dns_failed','无法解析 B站服务地址。',retryable=True) from None
    addresses = list(dict.fromkeys(r[4][0] for r in records))
    benchmark = ipaddress.ip_network('198.18.0.0/15')
    if addresses and all(ipaddress.ip_address(a) in benchmark for a in addresses):
        import dns.message
        import dns.rdatatype
        query = dns.message.make_query(host,'A')
        encoded = base64.urlsafe_b64encode(query.to_wire()).decode().rstrip('=')
        conn = PinnedHTTPS('dns.alidns.com','223.5.5.5',5)
        try:
            conn.request('GET','/dns-query?dns='+encoded,headers={'Accept':'application/dns-message'})
            reply = conn.getresponse()
            raw = reply.read(65537)
            if reply.status!=200 or len(raw)>65536:
                raise ValueError()
            parsed = dns.message.from_wire(raw)
            if not query.is_response(parsed):
                raise ValueError()
            addresses = [str(r) for section in parsed.answer if section.rdtype==dns.rdatatype.A for r in section]
        except Exception:
            raise Failure('dns_failed','代理虚拟地址无法安全解析到公网地址，请检查网络。',retryable=True) from None
        finally:
            conn.close()
    if not addresses or any(not ipaddress.ip_address(a).is_global or getattr(ipaddress.ip_address(a),'ipv4_mapped',None) is not None for a in addresses):
        raise Failure('unsafe_address','地址解析到本机或非公网网络，已拒绝请求。')
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address, timeout):
        super().__init__(hostname, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class SafeHTTP:
    def __init__(self, *, timeout=12, resolver=socket.getaddrinfo, connector=PinnedHTTPS):
        self.timeout, self.resolver, self.connector = timeout, resolver, connector
        self.calls = 0

    def get(self, url, *, purpose, cookie='', max_bytes=4*1024*1024,
            output=None, cancel=None, deadline=None, stop_at_video=False):
        cancel = cancel or threading.Event()
        deadline = deadline or time.monotonic() + self.timeout
        current = url
        target = Path(output) if output else None
        for hop in range(4):
            p = validate_url(current, purpose)
            if stop_at_video and p.hostname in WEB_HOSTS:
                return b'', [], current
            if cancel.is_set():
                raise Failure('cancelled', '任务已取消。')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Failure('network_timeout', '只读请求超时。', retryable=True)
            addresses = resolving_addresses(p.hostname, self.resolver)
            conn = self.connector(p.hostname, addresses[0], min(self.timeout, remaining))
            try:
                headers = {'User-Agent': UA, 'Accept-Encoding': 'identity', 'Referer': 'https://www.bilibili.com/'}
                # Never send account cookies to CDNs, shortlinks, or other hosts.
                if cookie and purpose in ('api', 'qr'):
                    if '\r' in cookie or '\n' in cookie or len(cookie) > 16384:
                        raise Failure('credential_invalid', '本地凭据格式无效，请重新登录。')
                    headers['Cookie'] = cookie
                self.calls += 1
                conn.request('GET', p.path + ('?' + p.query if p.query else ''), headers=headers)
                response = conn.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location or hop == 3:
                        raise Failure('redirect_limit', '链接重定向异常或超过上限。')
                    current = urljoin(current, location)
                    validate_url(current, purpose)
                    continue
                if response.status != 200:
                    code = 'rate_limited' if response.status in (412, 429) else 'media_unavailable' if purpose == 'media' else 'http_error'
                    raise Failure(code, '平台限制了读取，请稍后重试或在设置页核对登录状态。',
                                  retryable=response.status in (412, 429, 500, 502, 503), status=response.status)
                if response.getheader('Content-Encoding', 'identity') not in ('identity', ''):
                    raise Failure('protocol_error', '上游返回了不支持的压缩响应。')
                length = response.getheader('Content-Length')
                if length and (not length.isdigit() or int(length) > max_bytes):
                    raise Failure('size_limit', '响应或媒体超过本地大小上限。')
                total, chunks = 0, []
                stream = target.open('xb') if target else None
                try:
                    while True:
                        if cancel.is_set():
                            raise Failure('cancelled', '任务已取消。')
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise Failure('network_timeout', '只读请求超时。', retryable=True)
                        if conn.sock:
                            conn.sock.settimeout(min(self.timeout, remaining))
                        block = response.read1(min(65536, max_bytes - total + 1))
                        if not block:
                            break
                        total += len(block)
                        if total > max_bytes:
                            raise Failure('size_limit', '下载超过单个媒体的大小上限。')
                        if stream:
                            stream.write(block)
                        else:
                            chunks.append(block)
                    if length and total != int(length):
                        raise Failure('incomplete_download', '下载未完整结束，临时文件已丢弃。', retryable=True)
                finally:
                    if stream:
                        stream.close()
                return b''.join(chunks), response.getheaders(), current
            except Failure:
                if target:
                    target.unlink(missing_ok=True)
                raise
            except (OSError, http.client.HTTPException):
                if target:
                    target.unlink(missing_ok=True)
                raise Failure('network_error', '网络连接中断，未取得完整只读结果。', retryable=True) from None
            finally:
                conn.close()
        raise Failure('redirect_limit', '重定向超过上限。')

    def json(self, url, **kwargs):
        raw, _, _ = self.get(url, **kwargs)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise Failure('protocol_error', '平台返回的数据结构不兼容。') from None
