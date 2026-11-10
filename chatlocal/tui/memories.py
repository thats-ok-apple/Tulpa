"""Tulpa local memory: global switch, per-conversation scopes, versioned manual edits, retry.

No model tool can edit memory; every write here is an explicit operator action and
manual edits carry the scope revision so a newer version is never overwritten.
"""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static

from . import model
from .base import View, esc, fill_table, make_table, seg, selected_key
from .forms import ChoiceModal, Field, FormModal
from .modals import ConfirmModal, TextModal

STATUS = dict(collecting='收集中', updating='整理中', ready='已整理', error='整理失败', waiting='等待')
KIND = dict(group='群聊', direct='私聊')


class MemoriesView(View):
    title = '记忆'
    subtitle = '只从你本人参与的对话中学习；可关闭、查看、人工修订'
    BINDINGS = [Binding('t', 'toggle', '开关记忆'), Binding('e', 'edit', '人工修订'), Binding('y', 'retry', '重新整理'),
                Binding('a', 'add', '添加会话'), Binding('w', 'view', '查看全文')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static('', id='mem-control')
        with Horizontal(classes='search-bar'):
            yield Input(placeholder='按会话名筛选，Enter 确认', id='mem-filter', max_length=100)
            yield Button('筛选', id='mem-go')
        yield make_table('会话', '类型', '状态', '片段', '记忆', '更新', id='mem-table')
        yield Static('', classes='detail', id='mem-detail')
        yield Static('t 开关记忆 · e 人工修订 · y 重新整理 · a 添加会话 · w 查看全文', classes='hint')

    def on_mount(self):
        self.scopes, self.directory, self.control, self.details = {}, [], {}, {}
        self.query_one('#mem-detail').display = False

    def refresh_data(self):
        query = self.query_one('#mem-filter', Input).value.strip()[:100]
        self.start_load(lambda c: c.api_get('/api/tulpa', q=query), self.show, '正在读取记忆…')

    def show(self, result):
        result = result if isinstance(result, dict) else {}
        self.control = result.get('control') if isinstance(result.get('control'), dict) else {}
        self.directory = result.get('directory') if isinstance(result.get('directory'), list) else []
        on = bool(self.control.get('enabled'))
        self.query_one('#mem-control', Static).update(
            f'记忆：{"[#22c55e]开启[/]" if on else "[#ef4444]关闭[/]"} · 自 {esc(model.fmt_time(self.control.get("since")))} 起收集'
            + ('' if on else ' · 关闭期间不收集新行为，也不会把记忆用于回复草稿。'))
        rows = [s for s in result.get('scopes') or [] if isinstance(s, dict)]
        self.scopes = {str(s.get('id')): s for s in rows}
        fill_table(self.query_one('#mem-table'), [
            (s.get('id'), (model.one_line(s.get('name'), 24), KIND.get(s.get('kind'), s.get('kind') or ''),
                           STATUS.get(s.get('status'), s.get('status') or '—') + ('' if s.get('valid', 1) else ' · 来源已变'),
                           str(s.get('episodes', 0)),
                           '·'.join(x for x, y in (('自动', s.get('has_memory')), ('人工', s.get('has_manual'))) if y) or '—',
                           model.fmt_time(s.get('updated_at')))) for s in rows])
        if not rows:
            self.set_state('empty', '还没有记忆会话。记忆开启后，你在群里引用回复或私聊往来时会自动收集；也可按 a 添加。')
        self.show_detail()

    def focus_main(self):
        self.query_one('#mem-table').focus()  # Page keys (t/e/y/a) act on the list, not the filter box.

    def on_input_submitted(self, event):
        event.stop()
        self.refresh_data()

    def on_button_pressed(self, event):
        if event.button.id == 'mem-go':
            self.refresh_data()

    def selected(self):
        return self.scopes.get(selected_key(self.query_one('#mem-table')) or '')

    def on_data_table_row_highlighted(self, event):
        self.show_detail()

    def on_data_table_row_selected(self, event):
        self.action_view()

    def show_detail(self, fetch=True):
        box = self.query_one('#mem-detail', Static)
        scope = self.selected()
        box.display = bool(scope)
        if not scope:
            return
        sid = str(scope.get('id'))
        detail = self.details.get(sid)
        lines = [f'[b #a78bfa]{esc(scope.get("name") or sid)}[/] · {esc(model.platform_label(scope.get("platform")))} · '
                 f'已处理 {esc(scope.get("processed", 0))}/{esc(scope.get("total", 0))} · 待整理批次 {esc(scope.get("pending_batches", 0))}']
        if scope.get('failures'):
            lines.append(f'[#eab308]整理失败 {esc(scope["failures"])} 次；下次重试 {esc(model.fmt_time(scope.get("retry_at")))}[/]')
        if detail is None:
            lines.append('[#7f8a9a]正在读取详情…[/]')
            if fetch:
                self.load_detail(sid)
        else:
            lines.append('[#7f8a9a]自动记忆[/] ' + esc(model.one_line(detail.get('automatic') or '（尚无）', 200)))
            lines.append('[#7f8a9a]人工修订[/] ' + esc(model.one_line(detail.get('manual') or '（无）', 200)))
            lines.append(f'[#7f8a9a]版本[/] {esc(detail.get("revision", 0))} · 片段 {len(detail.get("episodes") or [])} · '
                         f'来源消息 {len(detail.get("sources") or [])}')
        box.update('\n'.join(lines))

    def load_detail(self, sid, then=None):
        def ok(result):
            if isinstance(result, dict):
                self.details[sid] = result
            if (self.selected() or {}).get('id') is not None and str(self.selected()['id']) == sid:
                self.show_detail(fetch=False)
            if then:
                then(self.details.get(sid) or {})

        def failed(message):
            self.details.pop(sid, None)
            self.set_state('error', f'读取记忆详情失败：{message}')
        self.app.request(lambda c: c.api_get(f'/api/tulpa/scopes/{seg(sid)}'), ok, failed)

    def need(self):
        scope = self.selected()
        if not scope:
            self.set_state('empty', '请先选择一个会话。')
        return scope

    # ---- actions ------------------------------------------------------------

    def action_toggle(self):
        target = not bool(self.control.get('enabled'))
        text = ('开启记忆后，将从现在开始收集你本人参与的新对话行为（不回溯历史）。' if target else
                '关闭记忆会立即停止收集，并取消正在进行、可能用到记忆的回复草稿生成。已有记忆保留。')

        def confirmed(yes):
            if yes:
                self.act(lambda c: c.api_post('/api/tulpa/control', dict(enabled=target)),
                         lambda r: self.reload_with('info', f'记忆已{"开启" if target else "关闭"}。'),
                         '正在切换…', '切换失败')
        self.app.push_screen(ConfirmModal('开启记忆' if target else '关闭记忆', text, '开启' if target else '关闭',
                                          danger=not target), confirmed)

    def action_view(self):
        scope = self.need()
        if not scope:
            return
        sid = str(scope['id'])

        def open_modal(detail):
            episodes = detail.get('episodes') or []
            history = detail.get('history') or []
            text = '\n'.join([
                '【自动整理的记忆】', detail.get('automatic') or '（尚无）', '',
                f'【人工修订】（版本 {detail.get("revision", 0)}）', detail.get('manual') or '（无）', '',
                f'【最近片段】共 {len(episodes)} 条（只引用原消息编号，不复制聊天）'] +
                [f'- {model.fmt_time(e.get("timestamp"))} 对方 {e.get("person_id")}：输入 {e.get("incoming")} → 回复 {e.get("outgoing")}'
                 + ('' if e.get('valid', 1) else '（来源已变化，已失效）') for e in episodes] +
                ['', '【修订历史】'] + [f'- {model.fmt_time(h.get("created_at"))} {h.get("kind")}：{model.one_line(h.get("body"), 120)}'
                                        for h in history])
            self.app.push_screen(TextModal(f'记忆：{scope.get("name") or sid}', text))
        self.load_detail(sid, open_modal)

    def action_edit(self):
        scope = self.need()
        if not scope:
            return
        sid = str(scope['id'])

        def open_form(detail):
            revision = detail.get('revision')
            if not isinstance(revision, int):
                self.set_state('error', '未读取到记忆版本，不能修订。')
                return

            def validate(v):
                if len(v['manual']) > 6000:
                    raise ValueError('人工记忆最多 6000 字。')
                return dict(manual=v['manual'], revision=revision)

            def done(body):
                if not body:
                    return

                def ok(result):
                    if isinstance(result, dict):
                        self.details[sid] = result
                    self.reload_with('info', '人工修订已保存。')
                self.act(lambda c: c.api_patch(f'/api/tulpa/scopes/{seg(sid)}', body), ok, '正在保存…', '保存失败')
            self.app.push_screen(FormModal(f'人工修订：{scope.get("name") or sid}', [
                Field('manual', '人工记忆（留空表示清除人工部分）', 'area', detail.get('manual') or '', height=10),
                Field('note', f'基于版本 {revision}；若期间已被更新，保存会被拒绝，不会覆盖。', 'note'),
            ], validate, '保存', lead='自动记忆：' + model.one_line(detail.get('automatic') or '（尚无）', 300)), done)
        self.load_detail(sid, open_form)

    def action_retry(self):
        scope = self.need()
        if not scope:
            return
        sid = str(scope['id'])

        def ok(result):
            if isinstance(result, dict):
                self.details[sid] = result
            self.reload_with('info', '已安排重新整理；整理在后台按批进行，可稍后刷新。')
        self.act(lambda c: c.api_post(f'/api/tulpa/scopes/{seg(sid)}/retry', {}), ok, '正在安排…', '安排失败')

    def action_add(self):
        if not self.query_one('#mem-filter', Input).value.strip():
            self.set_state('empty', '先在筛选框输入会话名并按 Enter，再按 a 从结果中添加尚未收集的会话。')
            self.query_one('#mem-filter').focus()
            return
        if not self.directory:
            self.set_state('empty', '没有匹配且尚未收集的群聊 / 私聊。')
            return

        def chosen(row):
            if not row:
                return
            self.act(lambda c: c.api_post('/api/tulpa/conversation',
                                          dict(platform=row.get('platform'), conversation_id=row.get('conversation_id'))),
                     lambda r: self.reload_with('info', f'已添加「{row.get("name")}」；这不会回溯分析历史消息。'),
                     '正在添加…', '添加失败')
        self.app.push_screen(ChoiceModal('添加会话（只建立记忆目录，不分析历史）', [
            (f'{model.platform_label(r.get("platform"))} · {KIND.get(r.get("kind"), "")} · {model.one_line(r.get("name"), 40)}', r)
            for r in self.directory]), chosen)
