"""Native voice management: transcription strategy and bounded historical discovery.

Uses the original /api/voice/settings and /api/voice/backfill routes. Discovery
only runs after an explicit form with platform, an explicit conversation scope
and a start/end date (at most one year). Switching to automatic transcription of
new voice messages is confirmed separately. Single-message transcription lives
on the chat page.
"""
from datetime import date

from . import model
from .base import esc
from .forms import ChoiceModal, Field, FormModal, date_text
from .modals import ConfirmModal

SETTINGS = '/api/voice/settings'
STRATEGIES = (('lazy', '按需转写（查看或调用时才转写）'), ('new', '自动转写新收到的语音'),
              ('manual-backfill', '仅手动（只在你发起补查时转写）'))
STATUS = dict(ready='已转写', queued='排队中', running='转写中', failed='失败', missing='音频缺失', pending='未转写')


def summary(s):
    """Plain-text status lines for the voice settings response."""
    s = s if isinstance(s, dict) else {}
    counts = s.get('counts') if isinstance(s.get('counts'), dict) else {}
    lines = [f'策略：{dict(STRATEGIES).get(s.get("strategy"), s.get("strategy") or "—")}',
             f'本地转写：{"就绪" if s.get("local_asr_ready") else "未就绪（需在「组件与服务」安装语音运行库和模型）"}',
             '语音：' + ('，'.join(f'{STATUS.get(k, k)} {v}' for k, v in counts.items()) or '尚未发现原生语音')
             + f'；队列 {s.get("queued", 0)}']
    if s.get('model'):
        lines.append(f'模型 {s["model"]} · 线程 {s.get("threads", "—")} · 单条上限 {s.get("max_seconds", "—")} 秒')
    lines.append(discovery_text(s.get('discovery')))
    return '\n'.join(l for l in lines if l)


def discovery_text(d):
    d = d if isinstance(d, dict) else {}
    if d.get('running'):
        return '历史语音发现：运行中…'
    if d.get('error'):
        return '历史语音发现失败：' + str(d['error'])
    result = d.get('result')
    if not isinstance(result, dict):
        return '历史语音发现：尚未运行'
    parts = [f'{k} {v}' for k, v in result.items() if isinstance(v, (int, float, str)) and k not in ('enqueued', 'queue_limit')]
    text = '历史语音发现完成：' + ('，'.join(parts) or '已完成')
    if 'enqueued' in result:
        text += f'；已加入转写队列 {result["enqueued"]} 条（单次上限 {result.get("queue_limit", 100)}）'
    return text


def backfill_form(platform, conversations, preselected=()):
    options = [(model.one_line(c.get('conversation') or c.get('conversation_id'), 44), c.get('conversation_id'))
               for c in conversations if c.get('conversation_id')]
    chosen = [cid for cid in preselected if cid in {v for _, v in options}]

    def validate(v):
        start = date_text(v['start'], '开始日期', allow_empty=False)
        end = date_text(v['end'], '截止日期', allow_empty=False)
        if start > end:
            raise ValueError('开始日期不能晚于截止日期。')
        if (date.fromisoformat(end) - date.fromisoformat(start)).days > 365:
            raise ValueError('一次最多补查一年以内的语音。')
        if v['all'] and v['conversations']:
            raise ValueError('请二选一：勾选具体会话，或勾选「全部已启用会话」。')
        if not v['all'] and not v['conversations']:
            raise ValueError('请勾选要补查的会话，或明确勾选「全部已启用会话」。')
        if len(v['conversations']) > 200:
            raise ValueError('一次最多选择 200 个会话。')
        body = dict(platform=platform, start=start, end=end, transcribe=v['transcribe'])
        if not v['all']:
            body['conversations'] = v['conversations']
        return body
    return FormModal(f'补查{model.platform_label(platform)}历史语音', [
        Field('conversations', '会话', 'multi', chosen, options),
        Field('all', '全部已启用会话（按本机读取范围）', 'check', False),
        Field('start', '开始日期', placeholder='YYYY-MM-DD'),
        Field('end', '截止日期（含当天）', placeholder='YYYY-MM-DD'),
        Field('transcribe', '发现后加入本地转写队列（最多 100 条，最新优先）', 'check', False,
              hint='只在本机转写，不调用模型服务；不勾选则只登记语音来源。'),
    ], validate, '开始补查', lead='从本机快照中登记该范围内的原生语音（最多 120 秒）。已有消息保留。')


def follow_discovery(view, box=None):
    """Poll /api/voice/settings until discovery finishes; render into `box` if given."""
    def tick(s):
        s = s if isinstance(s, dict) else {}
        d = s.get('discovery') if isinstance(s.get('discovery'), dict) else {}
        if box is not None:
            box.update('[b #a78bfa]语音[/]\n' + esc(summary(s)))
            box.display = True
        if d.get('running'):
            view.set_state('loading', '历史语音发现运行中…')
            return True
        if d.get('error'):
            view.set_state('error', discovery_text(d))
        else:
            view.set_state('info', discovery_text(d))
        return False
    view.poll('voice', lambda c: c.api_get(SETTINGS), tick, interval=2)


def change_strategy(view, current, box=None):
    def picked(strategy):
        if not strategy or strategy == current:
            return

        def saved(s):
            if box is not None:
                box.update('[b #a78bfa]语音[/]\n' + esc(summary(s)))
                box.display = True
            view.set_state('info', '转写策略已保存：' + dict(STRATEGIES)[strategy])

        def save(yes=True):
            if yes:
                view.act(lambda c: c.api_put(SETTINGS, dict(strategy=strategy)), saved, '正在保存转写策略…', '保存失败')
        if strategy == 'new':
            view.app.push_screen(ConfirmModal('自动转写新语音', '之后收到的原生语音会自动加入本机转写队列。\n'
                                              '转写在本机进行，会占用 CPU 与磁盘；不会调用模型服务。', '开启'), save)
        else:
            save()
    view.app.push_screen(ChoiceModal('转写策略', [(('● ' if k == current else '  ') + label, k) for k, label in STRATEGIES]), picked)


def voice_menu(view, platforms, conversations_for, preselected=None, box=None):
    """Entry point for a page.

    platforms: platform ids offered for discovery; conversations_for(platform) returns
    native conversation dicts; preselected: (platform, conversation_id) of the row the
    user highlighted, only pre-ticked in the form, never submitted automatically.
    """
    def opened(s):
        s = s if isinstance(s, dict) else {}
        if box is not None:
            box.update('[b #a78bfa]语音[/]\n' + esc(summary(s)))
            box.display = True
        running = (s.get('discovery') or {}).get('running')

        def picked(choice):
            if choice == 'strategy':
                change_strategy(view, s.get('strategy'), box)
            elif choice == 'backfill':
                if running:
                    view.set_state('info', '历史语音发现正在运行，请等待完成。')
                    follow_discovery(view, box)
                    return
                view.app.push_screen(ChoiceModal('选择平台', [(model.platform_label(p), p) for p in platforms]), start)
            elif choice == 'follow':
                follow_discovery(view, box)
        choices = [('修改转写策略…', 'strategy'), ('补查历史语音（指定会话与日期）…', 'backfill'), ('跟踪发现进度', 'follow')]
        view.app.push_screen(ChoiceModal('语音管理', choices, summary(s)), picked)

    def start(platform):
        if not platform:
            return
        pre = [preselected[1]] if preselected and preselected[0] == platform else []

        def done(body):
            if body:
                view.act(lambda c: c.api_post('/api/voice/backfill', body),
                         lambda r: follow_discovery(view, box), '正在启动语音发现…', '语音补查未开始')
        view.app.push_screen(backfill_form(platform, conversations_for(platform), pre), done)
    view.act(lambda c: c.api_get(SETTINGS), opened, '正在读取语音状态…', '读取语音状态失败')
