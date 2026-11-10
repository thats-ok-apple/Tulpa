"""Pure helpers for the terminal UI: permission rules, request bodies, formatting.

Nothing here imports Textual or performs I/O, so it is testable on its own.
"""
import json
import re
import time
from datetime import date, datetime

# (flag, label, required parent flag). Order is the on-screen order.
PERMISSIONS = (
    ('media', '向外部 Agent 提供图片（最多 60 次/小时）', None),
    ('prepare', '下载原文件 / 按需提取文字（合计最多 12 次/小时）', None),
    ('voice', '本地转写语音（最多 12 次/小时）', None),
    ('onebot', '读取群公告、精华、当前成员和文件目录', None),
    ('send', '直接发送 QQ 消息（无需逐次审批）', None),
    ('chat', '持续聊天：群聊 / 私聊（需发送权限，可随时停止）', 'send'),
    ('chat_reactions', '对已读群消息添加 / 撤销小表情回应', 'chat'),
    ('chat_images', '持续聊天看图，读取此账号收藏表情', 'chat'),
    ('chat_sticker_send', '在持续聊天中直接发送表情', 'chat_images'),
    ('chat_sticker_collect', '把看过的图片直接收藏到 QQ', 'chat_images'),
    ('manage', '直接执行 QQ 群管理（无需逐次审批）', None),
)
FLAGS = tuple(p[0] for p in PERMISSIONS)

# SL extension permissions (doc/TERMINAL_API.md, GET /grant-permissions). Static
# fallback labels and the local dependency rules; the backend list decides which are
# offered. Attachments need chat (and thus send). Old grants never gain these.
QQ_PERMISSIONS = (
    ('chat_voice', '实时语音读取、转写和发送（含 QQ AI 语音）', 'chat'),
    ('chat_video', '实时视频读取和发送', 'chat'),
    ('chat_files', '实时文件读取、发送及授权群的文件管理', 'chat'),
    ('qq_messages', '授权会话的撤回、转发、卡片及已读操作', None),
    ('qq_interactions', '授权会话的戳一戳、资料点赞、群签到', None),
    ('qq_albums', '授权群的相册浏览、上传、评论、点赞及删除', None),
    ('qq_friends', '当前账号的好友目录、申请、备注、分组及删除', None),
    ('qq_profile', '当前账号的个人资料、头像、签名及在线状态', None),
    ('qq_qzone', '当前账号的 QQ 空间读取、发表、删除、点赞及评论', None),
)
QQ_FLAGS = tuple(p[0] for p in QQ_PERMISSIONS)
ACCOUNT_FLAGS = ('qq_friends', 'qq_profile', 'qq_qzone')
PARENT = {flag: parent for flag, _, parent in PERMISSIONS + QQ_PERMISSIONS}
SHORT = dict(media='图片', prepare='文件', voice='语音', onebot='群资料', send='发送', chat='持续聊天',
             chat_reactions='回应', chat_images='看图', chat_sticker_send='发表情',
             chat_sticker_collect='收藏表情', manage='群管理',
             chat_voice='实时语音', chat_video='实时视频', chat_files='实时文件', qq_messages='消息操作',
             qq_interactions='互动', qq_albums='群相册', qq_friends='好友(账号级)',
             qq_profile='资料(账号级)', qq_qzone='空间(账号级)')
NAME_LIMIT = 60


def extension_permissions(result):
    """Known extension flags offered by GET /grant-permissions, in on-screen order.

    Returns [(flag, label, parent, scope)]. Unknown ids are skipped (no local
    dependency rule); the three account-wide flags are always marked `account`.
    """
    rows = result.get('items') if isinstance(result, dict) else result
    offered = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get('id') in QQ_FLAGS:
            offered[row['id']] = row
    result = []
    for flag, label, parent in QQ_PERMISSIONS:
        if flag in offered:
            row = offered[flag]
            scope = 'account' if flag in ACCOUNT_FLAGS or row.get('scope') == 'account' else 'conversation'
            result.append((flag, one_line(row.get('label') or label, 60), parent, scope))
    return result


def allowed(flag, values):
    """A flag is selectable only while its whole parent chain is selected."""
    parent = PARENT.get(flag)
    while parent:
        if not values.get(parent):
            return False
        parent = PARENT.get(parent)
    return True


def normalize_flags(values, extra=()):
    """Clear any flag whose prerequisite is off; returns a new dict of every flag.

    `extra` lists the extension flags the backend offers; others are left out of
    the result so an older backend never receives fields it does not know.
    """
    names = FLAGS + tuple(f for f in extra if f in QQ_FLAGS and f not in FLAGS)
    result = {flag: bool(values.get(flag)) for flag in names}
    for flag in names:
        if result[flag] and not allowed(flag, result):
            result[flag] = False
    return result


def conversation_id(group):
    """`GET /groups` / `GET /friends` already return the full conversation id; keep it verbatim."""
    return str(group['id'])


def conversation_kind(value):
    """'group' / 'direct' from a full `ACCOUNT:group:N` / `ACCOUNT:direct:N` id, else ''."""
    parts = str(value or '').split(':')
    return parts[1] if len(parts) == 3 and parts[1] in ('group', 'direct') else ''


KIND_LABEL = dict(group='群', direct='好友私聊')


def group_label(group, limit=28):
    """Plain-text option label. Never passed through markup parsing."""
    name = one_line(group.get('name') or group['id'], limit)
    account = group.get('account')
    kind = KIND_LABEL.get(conversation_kind(conversation_id(group)))
    return (f'[{kind}] ' if kind else '') + f'{name}  [{conversation_id(group)}]' + (f' · {account}' if account else '')


def valid_targets(rows, kind):
    """Entries from GET /groups or /friends that carry a full id of the expected kind and an account.

    Group rows keep the original lenient check (old backends), friend rows must be
    real `ACCOUNT:direct:FRIEND` ids: a friend is never guessed from a bare number.
    """
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get('id') in (None, '') or not row.get('account'):
            continue
        actual = conversation_kind(row['id'])
        if kind == 'direct' and actual != 'direct' or kind == 'group' and actual == 'direct':
            continue
        result.append(row)
    return result


def valid_date(value):
    if not value:
        return True
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def grant_body(name, groups, flags, start='', end='', extra=()):
    """Build the native POST /grants body or raise ValueError with a Chinese message.

    `groups` holds the selected groups and friends (full ids from the backend).
    """
    name = (name or '').strip()
    if not name or len(name) > NAME_LIMIT or any(ord(c) < 32 for c in name):
        raise ValueError(f'请填写不超过 {NAME_LIMIT} 字的连接名称。')
    if not groups:
        raise ValueError('请至少选择一个群或好友；没有群或好友就不会创建连接。')
    start, end = (start or '').strip(), (end or '').strip()
    if not valid_date(start) or not valid_date(end):
        raise ValueError('日期应为 YYYY-MM-DD，或留空表示不限制。')
    if start and end and start > end:
        raise ValueError('开始日期不能晚于截止日期。')
    clean = normalize_flags(flags, extra)
    if any(bool(flags.get(f)) != clean[f] for f in clean):
        raise ValueError('部分权限缺少前置权限，请检查勾选。')
    if any(flags.get(f) for f in QQ_FLAGS if f not in clean):
        raise ValueError('后台未提供部分新增权限，请刷新后重试。')
    conversations = []
    for group in groups:
        pair = ['qq', conversation_id(group)]
        if pair not in conversations:
            conversations.append(pair)
    return dict(name=name, platforms=['qq'], conversations=conversations, start=start, end=end, **clean)


def sl_body(http_url, ws_url, http_token='', ws_token=''):
    """PUT /sl body. Empty tokens are omitted so the backend keeps saved values."""
    http_url, ws_url = (http_url or '').strip(), (ws_url or '').strip()
    if not re.match(r'^https?://\S+$', http_url):
        raise ValueError('HTTP 地址应以 http:// 或 https:// 开头。')
    if not re.match(r'^wss?://\S+$', ws_url):
        raise ValueError('WebSocket 地址应以 ws:// 或 wss:// 开头。')
    body = dict(http_url=http_url, ws_url=ws_url)
    if http_token:
        body['http_token'] = http_token
    if ws_token:
        body['ws_token'] = ws_token
    return body


def parse_port(text):
    try:
        port = int((text or '').strip())
    except ValueError:
        raise ValueError('端口应为数字。') from None
    if not 1024 <= port <= 65535:
        raise ValueError('端口范围 1024–65535。')
    return port


def scope_dict(scope):
    if isinstance(scope, str):
        try:
            scope = json.loads(scope)
        except ValueError:
            return {}
    return scope if isinstance(scope, dict) else {}


def scope_conversations(scope):
    """Conversation ids a grant covers, or None when the scope does not say."""
    scope = scope_dict(scope)
    items = scope.get('conversations')
    if not isinstance(items, list) or not items:
        return None
    result = set()
    for item in items:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except ValueError:
                continue
        if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == 'qq':
            result.add(str(item[1]))
    return result or None


def scope_range(scope):
    """Group / friend range of a grant scope, e.g. `2 个群、1 个好友`; never the ids themselves."""
    data = scope_dict(scope)
    convs = scope_conversations(data)
    if convs:
        friends = sum(conversation_kind(c) == 'direct' for c in convs)
        groups = len(convs) - friends
        return '、'.join(t for t in (f'{groups} 个群' if groups else '', f'{friends} 个好友' if friends else '') if t)
    return '全部会话' if data.get('all_conversations') or \
        data.get('conversations') == [] and data.get('platforms') else '—'


def grant_flags(scope, extra=()):
    """Current switch values of a grant for the edit form (old FLAGS plus offered extensions)."""
    data = scope_dict(scope)
    return {flag: bool(data.get(flag)) for flag in FLAGS + tuple(f for f in extra if f in QQ_FLAGS and f not in FLAGS)}


def grant_revision(grant):
    """`revision` from GET /grants; grants from before revisions count as 0."""
    value = grant.get('revision') if isinstance(grant, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def grant_update_body(scope, values, revision=0, extra=()):
    """PUT /grants/{id} body holding only the switches that differ from `scope`, or None.

    `extra` lists the extension flags shown in the form. Extensions that are not shown
    are never sent; if one is on and would lose its prerequisite, saving is refused
    instead of letting the change silently clear it. Raises ValueError (Chinese text).
    """
    data = scope_dict(scope)
    clean = normalize_flags(values, extra)
    if any(bool(values.get(f)) != clean[f] for f in clean):
        raise ValueError('部分权限缺少前置权限，请检查勾选。')
    changes = {flag: value for flag, value in clean.items() if value != bool(data.get(flag))}
    if not changes:
        return None
    merged = {flag: bool(data.get(flag)) for flag in FLAGS + QQ_FLAGS}
    merged.update(changes)
    hidden = [f for f in QQ_FLAGS if f not in clean and merged[f] and not allowed(f, merged)]
    if hidden:
        raise ValueError(f'该连接已开启的{"、".join(SHORT[f] for f in hidden)}依赖要关闭的权限；'
                         '当前未能读取 SL 扩展权限，无法一并处理。请恢复扩展权限列表后重新打开修改。')
    return dict(changes, expected_revision=revision)


def scope_summary(scope):
    if isinstance(scope, str) and not scope_dict(scope):
        return scope
    data = scope_dict(scope)
    if not data:
        return '—'
    parts = [scope_range(data)]
    flags = [SHORT[f] for f in FLAGS + QQ_FLAGS if data.get(f)]
    parts.append('、'.join(flags) if flags else '只读')
    return ' · '.join(parts)


def grant_status(grant):
    if grant.get('revoked'):
        return '已撤销'
    if grant.get('valid') is False:
        return '已失效'
    return '有效'


def grant_usable(grant):
    return not grant.get('revoked') and grant.get('valid') is not False


def grant_chat_capable(grant):
    """Continuous group chat needs both send and chat in the grant's scope."""
    if not grant_usable(grant):
        return False
    scope = scope_dict(grant.get('scope'))
    return bool(scope.get('send')) and bool(scope.get('chat'))


def fmt_time(value):
    if value in (None, ''):
        return '—'
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            stamp = datetime.fromtimestamp(seconds)
        except (OverflowError, OSError, ValueError):
            return str(value)
        fmt = '%H:%M:%S' if stamp.date() == datetime.now().date() else '%Y-%m-%d %H:%M'
        return stamp.strftime(fmt)
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return text[:19]


def fmt_seconds(value):
    if not isinstance(value, (int, float)):
        return '—'
    return f'{value * 1000:.0f} ms' if value < 1 else f'{value:.1f} s'


def one_line(text, limit=80):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def yes_no(value, yes='是', no='否'):
    return yes if value else no


# Native chat session states (chatlocal.mcp_chat.public) -> operator-facing label.
# A prepared session only waits for an external Agent; never claim a model is running.
SESSION_STATES = {
    'waiting_agent': '等待 Agent',
    'waiting_messages': 'Agent 已接入 · 等待新消息',
    'agent_connected': 'Agent 已接入',
    'service_offline': 'MCP 服务未就绪',
    'stopped': '已停止',
    '': '等待 Agent',
}


def session_state(session):
    """Human label for a native session state; unknown values are shown verbatim."""
    state = str(session.get('state') or '').strip().lower()
    if not session.get('active'):
        return SESSION_STATES.get(state) if state in ('stopped', 'service_offline') else '已停止'
    return SESSION_STATES.get(state, str(session.get('state') or '').strip() or '等待 Agent')


# Native receiver states (chatlocal.onebot_events) -> operator-facing label.
RECEIVER_STATES = {
    'connected': '已连接', 'connecting': '连接中…', 'verifying': '核对账号中…',
    'disconnected': '已断开，正在重连', 'stopped': '已停止', 'unconfigured': '未配置',
    'unverified': '未确认可用', 'upstream_offline': 'QQ 链路不健康',
    'account_mismatch': '账号不一致', 'auth_error': '凭据被拒绝', 'handshake_error': '握手失败',
}


def receiver_state(receiver):
    if not isinstance(receiver, dict):
        return '—'
    state = str(receiver.get('state') or '').strip()
    return RECEIVER_STATES.get(state.lower(), state or '—')


LEARNING_KEYS = (
    ('enabled', '学习开关'), ('library', '学习库'), ('expression_count', '表达总数'),
    ('usable_expressions', '可用表达'), ('jargon_count', '黑话总数'), ('usable_jargon', '可用黑话'),
    ('independence', '独立性'), ('configuration_error', '配置错误'),
)


def learning_summary(status):
    """Known fields first as (label, value) rows; the full JSON stays available separately."""
    if not isinstance(status, dict):
        return [('状态', str(status))]
    rows = []
    for key, label in LEARNING_KEYS:
        if key in status:
            value = status[key]
            if isinstance(value, bool):
                value = '开启' if value else '关闭'
            elif isinstance(value, (dict, list)):
                value = one_line(json.dumps(value, ensure_ascii=False), 60)
            rows.append((label, str(value)))
    return rows


def learning_enabled(status):
    return bool(status.get('enabled')) if isinstance(status, dict) else None


def pretty_json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def now():
    return time.time()


# ---- full-port helpers ---------------------------------------------------

PLATFORM_LABEL = dict(qq='QQ', wechat='微信')
PROFILES = (('quick', '快速'), ('standard', '标准'), ('deep', '深入'), ('thorough', '充分'))
VISION = (('ocr', '本地 OCR（默认）'), ('native', '模型原生识图'))
REASONING = (('none', '不推理'), ('low', '低'), ('high', '高'), ('max', '最高'))


def platform_label(value):
    return PLATFORM_LABEL.get(str(value), str(value or '—'))


def pair(platform, conversation_id):
    """Native scope encoding used by watch cards, workspaces and Agent plans."""
    return json.dumps([platform, conversation_id], ensure_ascii=False)


def unpair(value):
    try:
        p, c = json.loads(value)
        return p, c
    except (TypeError, ValueError):
        return None, str(value)


def claims_text(result):
    """Readable answer text from a native Agent result; unverified claims are labelled."""
    if not isinstance(result, dict):
        return ''
    claims = result.get('display_claims') or result.get('claims') or []
    lines = []
    for index, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            continue
        text = str(claim.get('text') or '').strip()
        refs = [f'M{i}' for i in claim.get('evidence_ids') or []] + list(claim.get('artifact_evidence_ids') or [])
        mark = '（未通过证据检查）' if claim.get('_unverified_reason') else ''
        lines.append(f'{index}. {text}{mark}' + (f'\n   来源：{"、".join(map(str, refs[:12]))}' if refs else ''))
    if not lines:
        if result.get('error'):
            lines.append('未完成：' + str(result['error']))
        elif result.get('insufficient'):
            lines.append('证据不足：本地已导入记录中没有找到可以支撑回答的内容。')
    elif result.get('error'):
        lines.append('提示：' + str(result['error']))
    return '\n'.join(lines)


def event_line(event):
    """One progress line from a native Agent event (dict or text)."""
    if isinstance(event, str):
        return event
    if not isinstance(event, dict):
        return ''
    kind = event.get('type')
    if kind == 'tool_start':
        return '调用工具：' + str(event.get('name') or '')
    if kind == 'model_start':
        return f'请求模型（第 {event.get("request", "?")} 次）'
    if kind == 'tool_end':
        result = event.get('result') if isinstance(event.get('result'), dict) else {}
        return '工具完成' + ('：' + str(result['error']) if result.get('error') else '')
    if event.get('text'):
        return str(event['text'])
    # Unknown event: a readable summary from safe scalar fields only, never raw JSON
    # (payloads may carry tool arguments, prompts, headers or credentials).
    label = EVENT_LABELS.get(kind) or (f'事件：{one_line(kind, 24)}' if kind else '进度更新')
    parts = []
    for key, name in (('name', '名称'), ('request', '请求'), ('request_limit', '上限'), ('characters', '字数')):
        value = event.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) or isinstance(value, str) and value and len(value) <= 40 \
                and not _SECRETISH.search(value):
            parts.append(f'{name} {value}')
    return label + ('（' + '，'.join(parts) + '）' if parts else '')


EVENT_LABELS = dict(started='已启动', settled='已结束', validation='证据检查', reply_context='已读取回复上下文',
                    model_reasoning='模型推理中', model_end='模型请求完成', import_progress='导入进度')
_SECRETISH = re.compile(r'(?i)(bearer|token|secret|key|sk-|authorization|password)')


def size_text(value):
    if not isinstance(value, (int, float)):
        return '—'
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if value < 1024 or unit == 'GiB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def strip_html(text):
    """Server-rendered HTML fields are shown as plain text, never interpreted."""
    import html
    text = re.sub(r'<(br|/p|/div|/li|/summary|/pre)[^>]*>', '\n', str(text or ''), flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\n{3,}', '\n\n', html.unescape(text)).strip()
