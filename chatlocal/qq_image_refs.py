"""Private QQ image references. Never expose capabilities in MCP output."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def clean_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        return None
    try:
        p = urlsplit(value)
        host = (p.hostname or '').lower()
        if p.scheme not in ('https', 'http') or p.username or p.password or p.fragment or p.port not in (None, 443, 80):
            return None
        if host not in ('multimedia.nt.qq.com.cn', 'gchat.qpic.cn', 'c2cpicdw.qpic.cn'):
            return None
        # A fresh rkey is obtained from the existing signed-in local service.
        # Never persist this short-lived credential in message archives.
        query = urlencode([(k, v) for k, v in parse_qsl(p.query) if k.lower() != 'rkey'])
        return urlunsplit(('https', host, p.path, query, ''))
    except ValueError:
        return None


def from_fields(fields):
    def text(number):
        value = (fields.get(number) or [(None, b'')])[0][1]
        return value.decode('utf-8', 'strict') if isinstance(value, bytes) else ''
    try:
        host = text(45816)
        result = []
        digest = (fields.get(45406) or [(None, None)])[0][1]
        legacy = not host and isinstance(digest, bytes) and len(digest) == 16
        for number in (45802, 45803, 45804):
            path = text(number)
            if legacy and path.startswith('/') and not path.startswith('//'):
                parsed = urlsplit(path)
                pattern = rf'/gchatpic_new/[0-9]+/[0-9]+-[0-9]+-{digest.hex()}/(?:0|198|720)'
                if re.fullmatch(pattern, parsed.path, re.IGNORECASE):
                    url = clean_url('https://gchat.qpic.cn' + path)
                    if url and url not in result:
                        if parsed.path.endswith('/0'):
                            result.insert(0, url)
                        else:
                            result.append(url)
                continue
            url = clean_url('https://' + host + path if path.startswith('/') and not path.startswith('//') else path)
            if url and url not in result:result.append(url)
        return result
    except (UnicodeError, ValueError):
        return []
