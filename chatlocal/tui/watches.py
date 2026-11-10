"""Watch cards: list/detail, create, edit, check, follow-up questions, pause, delete.

Creating a card, checking it and asking follow-ups call the configured model, so
each is an explicit action whose background job is followed until it finishes.
"""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from . import model
from .base import View, esc, fill_table, items, make_table, seg, selected_key
from .forms import by_value, Field, FormModal, int_in, required
from .jobs import follow, stop_job
from .modals import ConfirmModal, Dialog

SCHEDULE = ((0, '仅手动'), (30, '30 分钟'), (60, '1 小时'), (180, '3 小时'), (360, '6 小时'), (720, '12 小时'),
            (1440, '1 天'), (4320, '3 天'), (10080, '7 天'))
STATUS = dict(active='关注中', paused='已暂停')
KIND = dict(setup='创建', settings='设置', check='检查', question='追问')


def claim_lines(claims, limit=8):
    lines = []
    for claim in (claims or [])[:limit]:
        if isinstance(claim, dict) and claim.get('text'):
            ids = claim.get('evidence_ids') or []
            lines.append('• ' + str(claim['text']) + (f'（{len(ids)} 条原文）' if ids else ''))
    return lines


def entry_text(e):
    head = (f'── {model.fmt_time(e.get("created_at"))} · {KIND.get(e.get("kind"), e.get("kind") or "")} · '
            f'{e.get("status") or ""}')
    if e.get('kind') == 'question' and e.get('question'):
        head += f'\n问：{e["question"]}'
    return head + '\n' + (model.strip_html(e.get('html')) or '（无内容）')


class TimelineModal(ModalScreen):
    """Watch timeline, newest page first; `older` from the API is passed back as `before`."""

    BINDINGS = [Binding('escape', 'close', '关闭'), Binding('ctrl+o', 'older', '更早')]

    def __init__(self, card):
        super().__init__()
        self.card = card
        self.entries = []  # Ascending by id, as the API returns each page.
        self.older = None
        self.page_loading = False

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog'):
            yield Static(f'时间线：{esc(self.card.get("title") or "")}', classes='dialog-title')
            yield Static('', classes='hint', id='timeline-lead')
            yield TextArea('', read_only=True, id='text', soft_wrap=True, classes='dialog-flex')
            yield Static('', classes='error-text', id='timeline-error')
            with Horizontal(classes='buttons'):
                yield Button('更早一页 (ctrl+o)', id='older')
                yield Button('关闭 (Esc)', id='close', variant='primary')

    def on_mount(self):
        self.query_one('#older').disabled = True
        self.load(None)

    def load(self, before):
        if self.page_loading:
            return
        self.page_loading = True
        cid = self.card['id']
        params = dict(before=before) if before else {}

        def ok(result):
            self.page_loading = False
            result = result if isinstance(result, dict) else {}
            page = [e for e in result.get('entries') or [] if isinstance(e, dict)]
            self.entries = page + self.entries
            older = result.get('older')
            self.older = older if isinstance(older, int) and older > 0 else None
            area = self.query_one('#text', TextArea)
            area.load_text('\n\n'.join(entry_text(e) for e in self.entries) or '暂无记录。')
            if before is None:
                area.scroll_end(animate=False)
            self.query_one('#older').disabled = self.older is None
            self.query_one('#timeline-lead', Static).update(
                f'已显示 {len(self.entries)} 条' + ('，还有更早记录。' if self.older else '，已到最早。'))
            self.query_one('#timeline-error', Static).update('')

        def failed(message):
            self.page_loading = False
            self.query_one('#timeline-error', Static).update(esc(message))
        self.app.request(lambda c: c.api_get(f'/api/watch-cards/{seg(cid)}/thread', **params), ok, failed)

    def action_older(self):
        if self.older:
            self.load(self.older)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'older':
            self.action_older()
        else:
            self.dismiss(None)

    def action_close(self):
        self.dismiss(None)


def card_form(conversations, card=None):
    card = card or {}
    scope = card.get('platform_scope') or ['qq', 'wechat']
    schedule_values = [v for v, _ in SCHEDULE]
    schedule = card.get('schedule_minutes', 0)
    options = SCHEDULE if schedule in schedule_values else SCHEDULE + ((schedule, f'{schedule} 分钟'),)

    def validate(v):
        platforms = [p for p in ('qq', 'wechat') if v[p]]
        if not platforms:
            raise ValueError('请至少选择一个平台。')
        convs = [c for c in v['conversations'] if model.unpair(c)[0] in platforms]
        return dict(title=required(v['title'], '标题', 100), watch_query=required(v['watch_query'], '关注内容', 1600),
                    platforms=platforms, conversations=convs,
                    schedule_minutes=v['schedule_minutes'] if isinstance(v['schedule_minutes'], int) else 0,
                    profile=v['profile'] or 'deep', load_stickers=v['load_stickers'],
                    message_threshold=int_in(v['message_threshold'] or '0', '新消息触发条数', 0, 100000))
    convs = [(f'{model.platform_label(c.get("platform"))} · {model.one_line(c.get("conversation") or c.get("conversation_id"), 40)}',
              model.pair(c.get('platform'), c.get('conversation_id'))) for c in conversations]
    return FormModal('修改关注卡' if card else '新建关注卡', [
        Field('title', '标题', value=card.get('title') or ''),
        Field('watch_query', '关注内容', 'area', card.get('watch_query') or '', height=4,
              hint='描述要持续跟踪的事情，例如“项目答辩的时间和材料要求”。'),
        Field('qq', '包含 QQ', 'check', 'qq' in scope),
        Field('wechat', '包含微信', 'check', 'wechat' in scope),
        Field('conversations', '限定会话（不选表示所选平台全部会话）', 'multi', card.get('conversation_scope') or [], convs),
        Field('schedule_minutes', '自动检查间隔', 'select', schedule, by_value(options)),
        Field('message_threshold', '新消息触发条数', value=str(card.get('message_threshold', 0)),
              hint='范围内新增消息达到此数时自动检查；0 表示关闭。'),
        Field('profile', '分析强度', 'select', card.get('profile') or 'deep', by_value(model.PROFILES)),
        Field('load_stickers', '检查前同步时载入表情包 / 图片', 'check', bool(card.get('load_stickers'))),
    ], validate, '保存' if card else '创建并检查',
        lead=('修改关注内容或范围会重建基线。' if card else '创建后会立即调用模型整理当前已知情况。'))


class WatchesView(View):
    title = '关注卡'
    subtitle = '持续跟踪一件事的进展；检查与追问会调用模型'
    BINDINGS = [Binding('n', 'create', '新建'), Binding('e', 'edit', '修改'), Binding('c', 'check', '检查'),
                Binding('a', 'ask', '追问'), Binding('p', 'pause', '暂停/恢复'), Binding('delete', 'delete', '删除'),
                Binding('w', 'timeline', '时间线'), Binding('x', 'stop', '停止任务')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes='split'):
            yield make_table('标题', '状态', '待处理', '上次检查', id='watch-table')
            with VerticalScroll(classes='pane'):
                yield Static('', id='watch-detail')
        yield Static('', classes='detail', id='watch-job')
        yield Static('n 新建 · e 修改 · c 检查 · a 追问 · p 暂停/恢复 · Del 删除 · w 时间线 · x 停止任务', classes='hint')

    def on_mount(self):
        self.cards, self.current_job = {}, None
        self.query_one('#watch-job').display = False

    def refresh_data(self):
        def fetch(c):
            return c.api_get('/api/watch-cards'), c.api_get('/api/refresh-status')
        self.start_load(fetch, self.show, '正在读取关注卡…')

    def show(self, result):
        cards, status = result
        rows = [c for c in items(cards, 'cards') if isinstance(c, dict)]
        self.cards = {str(c.get('id')): c for c in rows}
        fill_table(self.query_one('#watch-table'), [
            (c.get('id'), (model.one_line(c.get('title'), 24), STATUS.get(c.get('status'), c.get('status') or ''),
                           str(c.get('pending_messages', 0)), model.fmt_time(c.get('last_checked_at')))) for c in rows])
        if not rows:
            self.set_state('empty', '还没有关注卡。按 n 新建：描述要跟踪的事情和范围。')
        self.show_detail()
        running = [j for j in (status or {}).get('running_jobs') or [] if j.get('card_id')]
        if running and not self.polling('job'):
            self.follow(running[0].get('id'), '关注卡任务')

    def selected(self):
        return self.cards.get(selected_key(self.query_one('#watch-table')) or '')

    def on_data_table_row_highlighted(self, event):
        self.show_detail()

    def on_data_table_row_selected(self, event):
        self.action_timeline()

    def show_detail(self):
        card = self.selected()
        box = self.query_one('#watch-detail', Static)
        if not card:
            box.update('[#7f8a9a]选择一张关注卡。[/]')
            return
        schedule = dict(SCHEDULE).get(card.get('schedule_minutes'), f'{card.get("schedule_minutes")} 分钟')
        scope = '、'.join(model.platform_label(p) for p in card.get('platform_scope') or [])
        convs = card.get('conversation_scope') or []
        lines = [f'[b #a78bfa]{esc(card.get("title"))}[/] · {esc(STATUS.get(card.get("status"), card.get("status") or ""))}',
                 f'[#7f8a9a]关注[/] {esc(card.get("watch_query") or "")}',
                 f'[#7f8a9a]范围[/] {esc(scope)} · {len(convs) or "全部"} 个会话 · 自动检查 {esc(schedule)}'
                 + (f' · 新消息 ≥{esc(card["message_threshold"])} 条' if card.get('message_threshold') else ''),
                 f'[#7f8a9a]下次检查[/] {esc(model.fmt_time(card.get("next_run_at")))} · 待处理新消息 {esc(card.get("pending_messages", 0))}']
        if card.get('error'):
            lines.append(f'[#ef4444]{esc(card["error"])}[/]')
        known = claim_lines(card.get('baseline'))
        lines += ['', '[b]当前已知[/]'] + ([esc(l) for l in known] or ['[#7f8a9a]尚未形成基线。[/]'])
        uncertain = claim_lines(card.get('uncertainties'), 5)
        if uncertain:
            lines += ['', '[b]仍需确认[/]'] + [esc(l) for l in uncertain]
        last = card.get('last_result') if isinstance(card.get('last_result'), dict) else {}
        changes = claim_lines(last.get('changes'), 5)
        if changes:
            lines += ['', '[b]上次检查的新进展[/]'] + [esc(l) for l in changes]
        box.update('\n'.join(lines))

    def need(self):
        card = self.selected()
        if not card:
            self.set_state('empty', '请先选择一张关注卡。')
        return card

    def follow(self, job_id, label):
        follow(self, job_id, kind='data', box=self.query_one('#watch-job', Static), label=label,
               done=lambda job: self.reload_with('info' if job.get('status') == 'completed' else 'error',
                                                 f'{label}已结束。' if job.get('status') == 'completed'
                                                 else f'{label}未完成：{job.get("error") or ""}'))

    def started(self, label):
        return lambda r: self.follow((r or {}).get('job_id'), label)

    # ---- actions ------------------------------------------------------------

    def with_conversations(self, then):
        self.act(lambda c: c.api_get('/api/chat-conversations', limit=200),
                 lambda r: then(items(r, 'conversations')), '正在读取会话…', '读取会话失败')

    def action_create(self):
        def opened(conversations):
            def done(body):
                if body:
                    self.act(lambda c: c.api_post('/api/watch-cards', dict(body, seed_ids=[])),
                             self.started('创建并整理关注卡'), '正在创建…', '创建失败')
            self.app.push_screen(card_form(conversations), done)
        self.with_conversations(opened)

    def action_edit(self):
        card = self.need()
        if not card:
            return
        cid = card['id']

        def opened(conversations):
            def done(body):
                if body:
                    self.act(lambda c: c.api_put(f'/api/watch-cards/{seg(cid)}', body),
                             lambda r: self.reload_with('info', '关注卡已保存。若修改了范围或内容，下次检查会重建基线。'),
                             '正在保存…', '保存失败')
            self.app.push_screen(card_form(conversations, card), done)
        self.with_conversations(opened)

    def action_check(self):
        card = self.need()
        if not card:
            return
        if card.get('status') == 'paused':
            self.set_state('info', '此关注卡已暂停，请先按 p 恢复。')
            return
        cid = card['id']

        def done(body):
            if body:
                self.act(lambda c: c.api_post(f'/api/watch-cards/{seg(cid)}/check', body),
                         self.started(f'检查「{model.one_line(card.get("title"), 20)}」'), '正在开始检查…', '检查未开始')
        self.app.push_screen(FormModal('立即检查', [
            Field('profile', '分析强度', 'select', card.get('profile') or 'deep', by_value(model.PROFILES)),
            Field('load_stickers', '同步时载入表情包 / 图片', 'check', bool(card.get('load_stickers'))),
        ], lambda v: dict(profile=v['profile'] or 'deep', load_stickers=v['load_stickers']), '开始检查',
            lead='会先同步所选范围的新消息，再调用模型核对进展。'), done)

    def action_ask(self):
        card = self.need()
        if not card:
            return
        cid = card['id']

        def validate(v):
            question = required(v['question'], '问题', 6000)
            body = dict(question=question, profile=v['profile'] or card.get('profile') or 'deep')
            if v['vision_mode']:
                body['vision_mode'] = v['vision_mode']
            return body

        def done(body):
            if body:
                self.act(lambda c: c.api_post(f'/api/watch-cards/{seg(cid)}/ask', body),
                         self.started('追问'), '正在提交追问…', '追问失败')
        self.app.push_screen(FormModal(f'追问：{card.get("title")}', [
            Field('question', '问题', 'area', '', height=4),
            Field('profile', '分析强度', 'select', card.get('profile') or 'deep', by_value(model.PROFILES)),
            Field('vision_mode', '图片识别', 'select', None, by_value(model.VISION), placeholder='默认'),
        ], validate, '提问', lead='追问只读，不改变关注卡的基线；会调用模型。'), done)

    def action_pause(self):
        card = self.need()
        if not card:
            return
        paused = card.get('status') != 'paused'
        cid = card['id']
        self.act(lambda c: c.api_post(f'/api/watch-cards/{seg(cid)}/pause', dict(paused=paused)),
                 lambda r: self.reload_with('info', '已暂停自动检查，仍可追问。' if paused else '已恢复关注。'),
                 '正在保存…', '操作失败')

    def action_delete(self):
        card = self.need()
        if not card:
            return
        cid, title = card['id'], card.get('title') or card['id']

        def confirmed(yes):
            if yes:
                self.act(lambda c: c.api_delete(f'/api/watch-cards/{seg(cid)}'),
                         lambda r: self.reload_with('info', f'已删除关注卡「{title}」。'), '正在删除…', '删除失败')
        self.app.push_screen(ConfirmModal('删除关注卡', f'删除关注卡「{title}」及其检查和追问记录？\n'
                                          '聊天原文不受影响。此操作不可恢复。', '删除', danger=True), confirmed)

    def action_timeline(self):
        card = self.need()
        if card:
            self.app.push_screen(TimelineModal(card))

    def action_stop(self):
        stop_job(self)
