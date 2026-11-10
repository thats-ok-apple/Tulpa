"""Investigation workspaces: scope, tasks, materials, proposals, outputs and history.

All writes go through the native workspace routes. Agent tasks only create
proposals; formal outputs change only after the user previews a proposal and
confirms `apply` (confirm:true). Scope edits carry the workspace revision, and
restores/edits carry base_version, so a stale screen can never overwrite.
"""
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static

from . import model
from .base import View, esc, fill_table, items, make_table, seg, selected_key
from .forms import by_value, ChoiceModal, Field, FormModal, date_text, int_in, required
from .materials import save_path_form
from .modals import ConfirmModal, TextModal

W = '/api/workspaces'
MODES = (('auto', '自动判断（含糊时只回答）'), ('ask', '只提问，不整理'), ('task', '整理 / 修改成果'),
         ('fact', '补充事实（未经核实的用户陈述）'), ('changes', '处理新变化'))
PANES = dict(t='任务', c='资料', p='提案', o='成果')
COLUMNS = dict(t=('问题', '模式', '状态', '时间'), c=('资料', '类型', '状态', '原因'),
               p=('名称', '改动', '基于版本', '摘要'), o=('名称', '类型', '版本', '摘要'))
STATE = dict(accepted='已收录', pending='待定', excluded='已排除', filtered='已筛除')
ORIGIN = dict(agent='Agent', user='用户', restore='恢复')
SCHEDULES = ((0, '关闭'), (60, '每小时'), (360, '每 6 小时'), (1440, '每天'), (10080, '每周'))


def ms_date(value, end=False):
    if not isinstance(value, (int, float)):
        return ''
    try:
        return datetime.fromtimestamp((value - (1 if end else 0)) / 1000).strftime('%Y-%m-%d')
    except (OverflowError, OSError, ValueError):
        return ''


def scope_text(scope):
    scope = scope if isinstance(scope, dict) else {}
    convs = scope.get('conversations') or []
    return ' · '.join(['、'.join(model.platform_label(p) for p in scope.get('platforms') or []) or '—',
                       f'{len(convs)} 个会话' if convs else '全部会话',
                       f'{ms_date(scope.get("start")) or "不限"} 至 {ms_date(scope.get("end"), True) or "不限"}'])


def scope_fields(scope, conversations):
    scope = scope if isinstance(scope, dict) else {}
    platforms = scope.get('platforms') or ['qq', 'wechat']
    options = [(f'{model.platform_label(c.get("platform"))} · {model.one_line(c.get("conversation") or c.get("conversation_id"), 40)}',
                model.pair(c.get('platform'), c.get('conversation_id'))) for c in conversations]
    known = {v for _, v in options}
    for value in scope.get('conversations') or []:  # Keep selections outside the first page visible.
        if value not in known:
            options.append((f'{model.platform_label(model.unpair(value)[0])} · {model.unpair(value)[1]}', value))
    return [Field('qq', '包含 QQ', 'check', 'qq' in platforms), Field('wechat', '包含微信', 'check', 'wechat' in platforms),
            Field('conversations', '限定会话（不选表示所选平台全部会话）', 'multi', scope.get('conversations') or [], options),
            Field('start', '开始日期', value=ms_date(scope.get('start')), placeholder='YYYY-MM-DD，可留空'),
            Field('end', '截止日期（含当天）', value=ms_date(scope.get('end'), True), placeholder='YYYY-MM-DD，可留空')]


def scope_body(v, old=None):
    """Native scope; unchanged dates keep their stored bounds so no accidental epoch change."""
    old = old if isinstance(old, dict) else {}
    platforms = [p for p in ('qq', 'wechat') if v['qq' if p == 'qq' else 'wechat']]
    if not platforms:
        raise ValueError('请至少选择一个平台。')
    start, end = date_text(v['start'], '开始日期'), date_text(v['end'], '截止日期')
    if start and end and start > end:
        raise ValueError('开始日期不能晚于截止日期。')
    conversations = [c for c in v['conversations'] if model.unpair(c)[0] in platforms]
    return dict(platforms=platforms, conversations=conversations,
                start=old.get('start') if start and start == ms_date(old.get('start')) else (start or None),
                end=old.get('end') if end and end == ms_date(old.get('end'), True) else (end or None))


def output_body(v):
    if len(v['body']) > 100000:
        raise ValueError('正文超出 10 万字符。')
    return v['body']


def create_workspace(view, collection_id=None, title=''):
    """Shared by the collections page: scope form, then POST /api/workspaces."""
    def opened(result):
        convs = items(result, 'conversations')

        def validate(v):
            body = dict(goal=required(v['goal'], '调查目标', 2000), title=(v['title'] or '').strip()[:100],
                        scope=scope_body(v))
            if collection_id:
                body['collection_id'] = collection_id
            return body

        def done(body):
            if body:
                view.act(lambda c: c.api_post(W, body),
                         lambda r: (view.app.show_status(f'已新建工作区：{r.get("title")}'),
                                    view.reload_with('info', f'已新建工作区「{r.get("title")}」。')),
                         '正在新建工作区…', '新建失败')
        lead = ('将复制此集合中当前范围内可用的引用（最多 100 项）作为起始资料。' if collection_id else '') + \
            '新建不会调用模型；之后由你提交任务。'
        view.app.push_screen(FormModal('新建调查工作区', [
            Field('title', '名称', value=title, placeholder='留空则取目标前 32 字'),
            Field('goal', '调查目标', 'area', height=4), *scope_fields({}, convs)], validate, '创建', lead=lead), done)
    view.act(lambda c: c.api_get('/api/chat-conversations', limit=200), opened, '正在读取会话…', '读取会话失败')


class WorkspacesView(View):
    title = '调查工作区'
    subtitle = '任务只生成提案；成果在你预览并确认应用后才改变'
    BINDINGS = [Binding('n', 'create', '新建'), Binding('e', 'edit', '编辑范围'), Binding('D', 'delete', '删除'),
                Binding('t', 'pane("t")', '任务'), Binding('c', 'pane("c")', '资料'), Binding('p', 'pane("p")', '提案'),
                Binding('o', 'pane("o")', '成果'), Binding('h', 'home', '概况'), Binding('x', 'stop', '停止任务'),
                Binding('a', 'accept', '收录/应用'), Binding('j', 'reject', '排除/忽略'), Binding('A', 'apply_all', '全部应用'),
                Binding('u', 'release', '取消排除'), Binding('b', 'backfill', '日期补查'), Binding('R', 'reconnect', '重建接续点'),
                Binding('s', 'save', '保存成果'), Binding('E', 'edit_output', '编辑成果'), Binding('H', 'history', '版本历史'),
                Binding('S', 'schedule', '自动维护')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes='split'):
            yield make_table('工作区', '待定', '更新', id='ws-table')
            with Vertical(classes='pane'):
                yield Static('', classes='hint', id='ws-head')
                yield make_table(*COLUMNS['t'], id='ws-items')
        yield Static('', classes='detail', id='ws-detail')
        with Horizontal(classes='search-bar'):
            yield Input(placeholder='输入任务后按 Enter（会调用模型；模式按 ctrl+o 切换）', id='ws-input', max_length=2000)
            yield Button('提交任务', id='ws-ask', variant='primary')
        yield Static('', classes='hint', id='ws-keys')

    def on_mount(self):
        self.workspaces, self.data, self.rows = {}, {}, {}
        self.pane_key, self.mode, self.profile = 't', 'auto', 'deep'
        self.query_one('#ws-detail').display = False
        self.show_keys()

    def show_keys(self):
        common = 'n 新建 · e 范围 · D 删除 · t/c/p/o 切换 · h 概况 · S 自动维护'
        extra = dict(t='Enter 提交 · ctrl+o 模式 · x 停止', c='a 收录 · j 排除 · u 取消排除 · b 补查 · R 接续点',
                     p='Enter 预览 · a 应用 · A 全部应用 · j 忽略', o='Enter 阅读 · s 保存 · E 编辑 · H 历史/恢复')[self.pane_key]
        self.query_one('#ws-keys', Static).update(f'{common}\n{PANES[self.pane_key]}：{extra}')

    def refresh_data(self):
        self.start_load(lambda c: c.api_get(W), self.show, '正在读取工作区…')

    def show(self, result):
        rows = items(result, 'workspaces')
        self.workspaces = {str(w.get('id')): w for w in rows}
        fill_table(self.query_one('#ws-table'), [
            (w.get('id'), (model.one_line(w.get('title'), 20) + (' ⚠' if w.get('gap') else ''), str(w.get('pending', 0)),
                           model.fmt_time(w.get('updated_at')))) for w in rows])
        if not rows:
            self.set_state('empty', '还没有工作区。按 n 新建，或在「证据集合」页按 w 以集合为起点。')
            self.render_pane()
        else:
            self.load_workspace()

    def wid(self):
        return selected_key(self.query_one('#ws-table'))

    def on_data_table_row_highlighted(self, event):
        if event.data_table.id == 'ws-table':
            self.load_workspace()
        else:
            self.show_detail()

    def on_data_table_row_selected(self, event):
        if event.data_table.id == 'ws-items':
            self.open_item()

    def load_workspace(self):
        wid = self.wid()
        if not wid:
            return
        pane = self.pane_key

        def fetch(c):
            data = dict(overview=c.api_get(f'{W}/{seg(wid)}'))
            if pane == 't':
                data['thread'] = c.api_get(f'{W}/{seg(wid)}/thread')
            elif pane == 'c':
                data['materials'] = c.api_get(f'{W}/{seg(wid)}/materials')
            elif pane == 'p':
                data['proposals'] = c.api_get(f'{W}/{seg(wid)}/proposals')
            return data

        def tick(data):
            if self.wid() != wid or self.pane_key != pane:
                return False
            self.data = data if isinstance(data, dict) else {}
            self.render_pane()
            tasks = (self.data.get('overview') or {}).get('tasks') or []
            return any(t.get('state') == 'running' for t in tasks)
        self.poll('workspace', fetch, tick, interval=2)

    def workspace(self):
        return (self.data.get('overview') or {}).get('workspace') or {}

    def running_task(self):
        tasks = (self.data.get('overview') or {}).get('tasks') or []
        return next((t for t in tasks if t.get('state') == 'running'), None)

    def render_pane(self):
        table = self.query_one('#ws-items')
        table.clear(columns=True)
        table.add_columns(*COLUMNS[self.pane_key])
        overview = self.data.get('overview') or {}
        w = overview.get('workspace') or {}
        head = self.query_one('#ws-head', Static)
        if not w:
            head.update('')
            fill_table(table, [])
            self.show_detail()
            return
        running = self.running_task()
        head.update(f'[b #a78bfa]{esc(w.get("title"))}[/] · {esc(scope_text(w.get("scope")))} · 待定候选 {overview.get("pending", 0)}'
                    + (' · [#ef4444]变化流有缺口，需日期补查[/]' if w.get('gap') else '')
                    + (' · [#eab308]任务运行中[/]' if running else '')
                    + f'\n[#7f8a9a]{PANES[self.pane_key]} · 模式：{esc(dict(MODES)[self.mode])}[/]')
        self.rows = {}
        rows = []
        if self.pane_key == 't':
            for t in (self.data.get('thread') or {}).get('turns') or []:
                self.rows[str(t.get('id'))] = t
                rows.append((t.get('id'), (model.one_line(t.get('question'), 30), t.get('mode') or '', t.get('state') or '',
                                           model.fmt_time(t.get('created_at')))))
            rows.reverse()
        elif self.pane_key == 'c':
            mats = self.data.get('materials') or {}
            for c in mats.get('candidates') or []:
                key = f'cand:{c.get("id")}'
                self.rows[key] = dict(c, _kind='candidate')
                src = c.get('source') or {}
                rows.append((key, (model.one_line(src.get('title') or c.get('source_id'), 30), c.get('source_type') or '',
                                   '候选', model.one_line(c.get('reason'), 16))))
            for m in mats.get('materials') or []:
                key = f'mat:{m.get("source_type")}:{m.get("source_id")}'
                self.rows[key] = dict(m, _kind='material')
                label = m.get('title') or m.get('label') or m.get('source_id')
                state = STATE.get(m.get('state'), m.get('state') or '') + ('' if m.get('available', True) else ' · 不可用') + \
                    (' · 已变化' if m.get('changed') else '')
                rows.append((key, (model.one_line(label, 30), m.get('source_type') or '', state, model.one_line(m.get('reason'), 16))))
        elif self.pane_key == 'p':
            for p in (self.data.get('proposals') or {}).get('proposals') or []:
                self.rows[str(p.get('id'))] = p
                change = '收录资料' if p.get('action') == 'material' else '删除' if p.get('action') == 'delete' else \
                    ('新建' if not p.get('base_version') else '修订')
                rows.append((p.get('id'), (model.one_line(p.get('name'), 24), change, str(p.get('base_version') or '—'),
                                           model.one_line(p.get('summary'), 36))))
        else:
            for o in overview.get('outputs') or []:
                if o.get('deleted'):
                    continue
                self.rows[str(o.get('id'))] = o
                kind = dict(state='当前状态', note='笔记').get(o.get('resource_kind'), '资料索引' if o.get('format') == 'csv' else '报告')
                rows.append((o.get('id'), (model.one_line(o.get('name'), 24), kind, str(o.get('head') or '—'),
                                           model.one_line(o.get('summary'), 36))))
        fill_table(table, rows)
        if not rows:
            self.set_state('empty', dict(t='还没有任务。在下方输入任务后按 Enter。', c='没有资料或待定候选。可按 b 指定日期补查。',
                                         p='没有待处理的提案。任务完成后生成的草稿会出现在这里。',
                                         o='还没有已应用的成果。')[self.pane_key])
        else:
            self.set_state('ok')
        self.show_detail()

    def item(self):
        return self.rows.get(selected_key(self.query_one('#ws-items')) or '')

    def show_detail(self):
        box = self.query_one('#ws-detail', Static)
        item = self.item()
        box.display = bool(item)
        if not item:
            return
        if self.pane_key == 't':
            record = item.get('record') if isinstance(item.get('record'), dict) else {}
            if item.get('state') == 'running':
                text = '正在执行…\n' + '\n'.join(model.event_line(e) for e in (item.get('events') or [])[-5:])
            else:
                text = model.claims_text(record.get('result')) or record.get('note') or record.get('error') or '（无回答内容）'
                drafts = record.get('drafts_saved') or []
                if drafts:
                    text += f'\n已生成 {len(drafts)} 份草稿提案，按 p 查看并确认。'
            box.update(f'[b #a78bfa]{esc(model.one_line(item.get("question"), 80))}[/]\n{esc(model.one_line(text, 600))}')
        elif self.pane_key == 'c':
            src = item.get('source') or item
            box.update(f'[b #a78bfa]{esc(model.one_line(src.get("title") or src.get("label") or item.get("source_id"), 80))}[/]  '
                       f'{esc(item.get("source_type"))} {esc(item.get("source_id"))}\n'
                       f'{esc(model.platform_label(src.get("platform")))} · {esc(src.get("conversation") or "")} · '
                       f'{esc(src.get("sender") or "")}' + (f'\n[#7f8a9a]{esc(src.get("notice") or src.get("note"))}[/]'
                                                          if src.get('notice') or src.get('note') else ''))
        elif self.pane_key == 'p':
            refs = item.get('refs') or []
            bad = sum(1 for r in refs if not r.get('available') or r.get('changed'))
            box.update(f'[b #a78bfa]{esc(item.get("name"))}[/] · {esc(item.get("format"))} · 基于版本 {esc(item.get("base_version") or 0)}\n'
                       f'{esc(model.one_line(item.get("summary"), 200))}\n引用 {len(refs)} 项'
                       + (f' · [#ef4444]{bad} 项已变化或不可用，应用会被拒绝[/]' if bad else '') + ' · Enter 预览差异')
        else:
            box.update(f'[b #a78bfa]{esc(item.get("name"))}[/] · 版本 {esc(item.get("head"))} · {esc(item.get("format"))}\n'
                       f'{esc(model.one_line(item.get("summary"), 300))}')

    def action_pane(self, key):
        self.pane_key = key
        self.show_keys()
        self.load_workspace()

    def require(self):
        wid = self.wid()
        if not wid:
            self.set_state('empty', '请先选择或新建一个工作区。')
        return wid

    def on_key(self, event):
        if event.key == 'ctrl+o':
            event.stop()
            self.pick_mode()

    def reload_pane(self, kind='info', text=''):
        if text:
            self.set_state(kind, text)
        self.load_workspace()

    # ---- workspace CRUD ------------------------------------------------

    def action_create(self):
        create_workspace(self)

    def action_edit(self):
        wid = self.require()
        if not wid:
            return
        w = self.workspace()
        if self.running_task():
            self.set_state('info', '请先停止当前任务再修改范围。')
            return

        def opened(result):
            def validate(v):
                return dict(revision=w.get('revision'), title=required(v['title'], '名称', 100),
                            goal=required(v['goal'], '调查目标', 2000), scope=scope_body(v, w.get('scope')))

            def done(body):
                if body:
                    self.act(lambda c: c.api_patch(f'{W}/{seg(wid)}', body),
                             lambda r: self.reload_with('info', '已保存。若范围或目标改变，旧成果与任务正文已隔离，需在新范围重新调查。'),
                             '正在保存…', '保存失败')
            self.app.push_screen(FormModal('编辑工作区', [
                Field('title', '名称', value=w.get('title') or ''), Field('goal', '调查目标', 'area', w.get('goal') or '', height=4),
                *scope_fields(w.get('scope'), items(result, 'conversations'))], validate, '保存',
                lead='修改范围或目标会开始新的范围周期：旧成果正文隔离，待处理提案失效，需要日期补查。版本号用于防止覆盖他处修改。'), done)
        self.act(lambda c: c.api_get('/api/chat-conversations', limit=200), opened, '正在读取会话…', '读取会话失败')

    def action_delete(self):
        wid = self.require()
        if not wid:
            return
        title = (self.workspaces.get(wid) or {}).get('title') or wid

        def confirmed(yes):
            if yes:
                self.stop_poll('workspace')
                self.act(lambda c: c.api_delete(f'{W}/{seg(wid)}', dict(confirm=True)),
                         lambda r: self.reload_with('info', f'已删除工作区「{title}」。原始聊天和文件保留。'), '正在删除…', '删除失败')
        self.app.push_screen(ConfirmModal('删除工作区', f'确定删除工作区「{title}」吗？\n其任务、提案、成果版本与专属资料集合将一并删除，'
                                          '原始聊天、文件和其他集合保留。此操作不可恢复。', '删除', danger=True), confirmed)

    def action_home(self):
        wid = self.require()
        if not wid:
            return

        def show(h):
            h = h if isinstance(h, dict) else {}
            lines = []
            state = h.get('current_state')
            if state:
                lines.append(f'当前状态文档：{state.get("name")}（版本 {state.get("version")}）')
            lines.append(f'\n已确认的理解（{h.get("understanding_total", 0)} 条，显示前 8 条）：')
            lines += [f'· {r.get("text")}' for r in h.get('understanding') or []] or ['· 暂无']
            if h.get('user_supplements'):
                lines.append('\n用户补充（未独立核实）：')
                lines += [f'· {r.get("text")}' + ('（来源已变化）' if r.get('stale') else '') for r in h['user_supplements']]
            lines.append(f'\n待确认问题（{h.get("question_total", 0)}）：')
            lines += [f'· {r.get("text")}' + ('（来源已变化）' if r.get('stale') else '') for r in h.get('questions') or []] or ['· 暂无']
            recent = h.get('recent') or {}
            lines.append('\n上次应用后的新变化' + ('（变化流有缺口）' if recent.get('gap') else '') + '：')
            lines += [f'· {r.get("label")} {r.get("count")} 项' for r in recent.get('items') or []] or ['· 暂无']
            states = h.get('material_states') or {}
            lines.append(f'\n资料：已收录 {h.get("confirmed_materials", 0)}；' +
                         '，'.join(f'{STATE.get(k, k)} {v}' for k, v in states.items()) + f'；待处理提案 {h.get("proposals", 0)}')
            note = (self.data.get('overview') or {}).get('note') or ''
            self.app.push_screen(TextModal(f'工作区概况：{self.workspace().get("title") or ""}', '\n'.join(lines), esc(note)))
        self.act(lambda c: c.api_get(f'{W}/{seg(wid)}/home'), show, '正在读取概况…', '读取概况失败')

    # ---- tasks ---------------------------------------------------------

    def pick_mode(self):
        def picked(value):
            if value:
                self.mode = value
                self.render_pane()
        self.app.push_screen(ChoiceModal('任务模式', MODES, '任何模式都只生成提案；正式成果须你确认应用。'), picked)

    def on_input_submitted(self, event):
        event.stop()
        if event.input.id == 'ws-input':
            self.submit_task()

    def on_button_pressed(self, event):
        if event.button.id == 'ws-ask':
            self.submit_task()

    def submit_task(self):
        wid = self.require()
        box = self.query_one('#ws-input', Input)
        question = box.value.strip()
        if not wid:
            return
        if not question:
            self.set_state('empty', '请输入任务或问题。')
            return
        if self.running_task():
            self.set_state('info', '该工作区已有任务执行中，请等待或按 x 停止。')
            return
        body = dict(question=question, mode=self.mode, profile=self.profile, vision='ocr')

        def ok(result):
            box.value = ''
            if result.get('api_called') is False:
                self.set_state('info', '没有新的待整理候选，未调用模型。')
            self.app.show_status('任务已提交')
            self.action_pane('t')
        self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/tasks', body), ok, '正在提交任务…', '提交失败')

    def action_stop(self):
        wid = self.require()
        task = self.running_task() if wid else None
        if not task:
            self.set_state('info', '没有正在运行的任务。')
            return
        self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/tasks/{seg(task.get("id"))}/stop', {}),
                 lambda r: self.reload_pane('info', '已请求停止；草稿和候选保留，可继续。'), '正在停止…', '停止失败')

    # ---- materials / candidates ------------------------------------------

    def material(self, state, reason):
        wid, item = self.require(), self.item()
        if not wid or self.pane_key != 'c' or not item:
            self.set_state('empty', '请先在「资料」中选择一项（按 c 切换）。')
            return
        body = dict(source_type=item.get('source_type'), source_id=str(item.get('source_id')), state=state, reason=reason)
        label = '收录' if state == 'accepted' else '排除'
        self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/materials', body),
                 lambda r: self.reload_pane('info', f'已{label}。'), f'正在{label}…', f'{label}失败')

    def action_accept(self):
        if self.pane_key == 'p':
            self.open_item()
        else:
            self.material('accepted', '用户选择')

    def action_reject(self):
        if self.pane_key == 'p':
            self.reject_proposals()
            return
        item = self.item()
        if self.pane_key != 'c' or not item:
            self.set_state('empty', '请先在「资料」或「提案」中选择一项。')
            return

        def confirmed(yes):
            if yes:
                self.material('excluded', '用户排除')
        self.app.push_screen(ConfirmModal('排除资料', '排除后此来源（及其图片/语音或文件片段）不再进入本工作区的任务和成果。\n'
                                          '原始资料保留，可按 u 取消排除。', '排除', danger=True), confirmed)

    def action_release(self):
        wid, item = self.require(), self.item()
        if not wid or self.pane_key != 'c' or not item or item.get('state') != 'excluded':
            self.set_state('empty', '请先选择一项「已排除」的资料。')
            return
        body = dict(release=True, source_type=item.get('source_type'), source_id=str(item.get('source_id')))
        self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/materials', body),
                 lambda r: self.reload_pane('info', '已取消排除，此项回到待定。'), '正在处理…', '取消排除失败')

    def action_backfill(self):
        wid = self.require()
        if not wid:
            return

        def validate(v):
            start, end = date_text(v['start'], '开始日期', False), date_text(v['end'], '截止日期', False)
            if start > end:
                raise ValueError('开始日期不能晚于截止日期。')
            return dict(start=start, end=end, limit=int_in(v['limit'], '每批条数', 1, 500),
                        offset=int_in(v['offset'] or 0, '起始位置', 0, 100000), new_scope_only=v['new_scope_only'])

        def done(body):
            if body:
                self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/backfill', body),
                         lambda r: self.reload_pane('info', f'已加入 {r.get("count", 0)} 项候选' +
                                                    (f'；还有更多，下一批起始位置 {r.get("next_offset")}。' if r.get('has_more') else '。')),
                         '正在补查…', '补查失败')
        self.app.push_screen(FormModal('指定日期补查', [
            Field('start', '开始日期', placeholder='YYYY-MM-DD'), Field('end', '截止日期', placeholder='YYYY-MM-DD'),
            Field('limit', '每批条数', value='200'), Field('offset', '起始位置', value='0'),
            Field('new_scope_only', '只补查最近一次范围变化新增的部分', 'check', False),
        ], validate, '补查', lead='把范围内该日期区间的消息、文件和群资料加入待定候选；不调用模型。'), done)

    def action_reconnect(self):
        wid = self.require()
        if not wid:
            return
        if not self.workspace().get('gap'):
            self.set_state('info', '变化流没有缺口，无需重建接续点。')
            return

        def confirmed(yes):
            if yes:
                self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/reconnect', dict(confirm=True)),
                         lambda r: self.reload_pane('info', '已重建接续点。'), '正在处理…', '重建失败')
        self.app.push_screen(ConfirmModal('重建变化接续点', '变化流出现缺口时，需先按 b 对缺口日期执行有界补查。\n'
                                          '确认后从当前位置继续接收变化；这不代表已恢复完整历史。', '确认重建'), confirmed)

    # ---- proposals -------------------------------------------------------

    def open_item(self):
        item = self.item()
        if not item:
            return
        if self.pane_key == 'p':
            self.preview_proposal(item)
        elif self.pane_key == 'o':
            self.read_output(item)
        elif self.pane_key == 't':
            self.show_detail()
            record = item.get('record') if isinstance(item.get('record'), dict) else {}
            text = model.claims_text(record.get('result')) or record.get('note') or record.get('error') or ''
            trace = '\n'.join(model.event_line(e) for e in item.get('events') or [])
            self.app.push_screen(TextModal(f'任务：{model.one_line(item.get("question"), 40)}',
                                           text + ('\n\n— 查询过程 —\n' + trace if trace else '')))

    def preview_proposal(self, p):
        refs = p.get('refs') or []
        bad = [r for r in refs if not r.get('available') or r.get('changed')]
        body = (p.get('diff') or p.get('body') or '') if p.get('action') != 'material' else \
            '\n'.join(f'收录：{r.get("label") or r.get("source_type")} {r.get("source_id")}' for r in refs)
        lines = [f'名称：{p.get("name")}  格式：{p.get("format")}  基于版本：{p.get("base_version") or 0}',
                 f'摘要：{p.get("summary") or ""}', f'引用 {len(refs)} 项' + (f'，其中 {len(bad)} 项已变化或不可用' if bad else ''),
                 '', body]
        self.app.push_screen(TextModal(f'提案预览：{p.get("name")}', '\n'.join(lines),
                                       '下一步可确认应用到正式成果；此预览不会修改任何内容。'),
                             lambda _: self.confirm_apply([p]))

    def confirm_apply(self, proposals):
        wid = self.wid()
        if not proposals or not wid:
            return
        names = '\n'.join(f'· {p.get("name")}（基于版本 {p.get("base_version") or 0}）' for p in proposals[:20])

        def confirmed(yes):
            if yes:
                ids = [p.get('id') for p in proposals[:20]]
                self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/apply', dict(ids=ids, confirm=True)),
                         lambda r: self.reload_pane('info', f'已应用 {r.get("applied", 0)} 项。'), '正在应用…', '应用失败')
        self.app.push_screen(ConfirmModal('应用提案到正式成果', f'确认应用以下 {min(20, len(proposals))} 项改动？\n{names}\n\n'
                                          '服务端会再次核对版本与引用来源；若成果已被编辑或来源已变化，将拒绝应用。', '应用'), confirmed)

    def action_apply_all(self):
        if self.pane_key != 'p':
            self.set_state('info', '请先按 p 打开提案列表。')
            return
        proposals = list(self.rows.values())
        if not proposals:
            self.set_state('empty', '没有待处理的提案。')
            return
        if len(proposals) > 20:
            self.set_state('info', '一次最多应用 20 项，将确认前 20 项。')
        self.confirm_apply(proposals)

    def reject_proposals(self):
        wid, p = self.wid(), self.item()
        if not wid or not p:
            self.set_state('empty', '请先选择一项提案。')
            return

        def confirmed(yes):
            if yes:
                self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/reject', dict(ids=[p.get('id')])),
                         lambda r: self.reload_pane('info', '已忽略该提案。'), '正在处理…', '忽略失败')
        self.app.push_screen(ConfirmModal('忽略提案', f'忽略提案「{p.get("name")}」？正式成果不变。', '忽略', danger=True), confirmed)

    # ---- outputs ---------------------------------------------------------

    def output_item(self):
        item = self.item()
        if self.pane_key != 'o' or not item:
            self.set_state('empty', '请先在「成果」中选择一项（按 o 切换）。')
            return None
        return item

    def read_output(self, o, version=None):
        wid = self.wid()
        params = dict(version=version) if version else {}

        def show(v):
            refs = v.get('refs') or []
            issues = [r for r in refs if not r.get('available') or r.get('changed')]
            lead = f'版本 {v.get("version")} · {ORIGIN.get(v.get("origin"), v.get("origin") or "")} · {model.fmt_time(v.get("created_at"))}'
            if issues:
                lead += f' · {len(issues)} 项来源已变化或不可用'
            self.app.push_screen(TextModal(f'成果：{v.get("name")}', str(v.get('body') or ''), esc(lead)))
        self.act(lambda c: c.api_get(f'{W}/{seg(wid)}/outputs/{seg(o.get("id"))}', **params), show, '正在读取成果…', '读取失败')

    def action_save(self):
        o = self.output_item()
        if not o:
            return
        wid = self.wid()
        suffix = '.' + str(o.get('format') or 'md')
        name = str(o.get('name') or 'output')
        name = name if name.endswith(suffix) else name + suffix

        def chosen(path):
            if path:
                self.act(lambda c: c.download(f'{W}/{seg(wid)}/outputs/{seg(o.get("id"))}/download', path),
                         lambda r: self.set_state('info', f'已保存到 {r}'), '正在保存…', '保存失败')
        self.app.push_screen(save_path_form(Path(name).name), chosen)

    def action_edit_output(self):
        o = self.output_item()
        if not o:
            return
        wid = self.wid()

        def opened(v):
            base = v.get('version')

            def done(body):
                if body is None:
                    return
                self.act(lambda c: c.api_patch(f'{W}/{seg(wid)}/outputs/{seg(o.get("id"))}', dict(body=body, base_version=base)),
                         lambda r: (self.set_state('info', '已生成手动编辑提案；在「提案」中预览并确认应用后才生效。'),
                                    self.action_pane('p')), '正在保存草稿…', '保存失败')
            self.app.push_screen(FormModal(f'编辑成果：{v.get("name")}（基于版本 {base}）',
                                           [Field('body', '正文', 'area', v.get('body') or '', height=16)],
                                           output_body, '保存为提案',
                                           lead='编辑会成为一份待确认的提案；若成果在此期间被修改，保存会因版本冲突被拒绝。'), done)
        self.act(lambda c: c.api_get(f'{W}/{seg(wid)}/outputs/{seg(o.get("id"))}'), opened, '正在读取成果…', '读取失败')

    def action_history(self):
        wid = self.require()
        if not wid:
            return
        current = self.item() if self.pane_key == 'o' else None

        def show(h):
            versions = [v for v in (h or {}).get('versions') or [] if not current or v.get('output_id') == current.get('id')]
            if not versions:
                self.set_state('empty', '没有版本记录。')
                return
            choices = [(f'{v.get("name")} v{v.get("version")} · {ORIGIN.get(v.get("origin"), v.get("origin") or "")} · '
                        f'{model.fmt_time(v.get("created_at"))} · {model.one_line(v.get("summary"), 30)}', v) for v in versions[:60]]
            self.app.push_screen(ChoiceModal('版本历史（选择一项查看差异，可恢复）', choices), self.version_chosen)
        self.act(lambda c: c.api_get(f'{W}/{seg(wid)}/history'), show, '正在读取历史…', '读取历史失败')

    def version_chosen(self, v):
        if not v:
            return
        head = next((o.get('head') for o in (self.data.get('overview') or {}).get('outputs') or [] if o.get('id') == v.get('output_id')), None)

        def after(_):
            if head is None or v.get('version') == head or v.get('summary') == '旧范围版本':
                return
            self.confirm_restore(v, head)
        self.app.push_screen(TextModal(f'{v.get("name")} v{v.get("version")} 的差异', str(v.get('diff') or '（无差异记录）'),
                                       esc(f'当前版本 {head}；关闭后可选择恢复此版本。' if head and v.get('version') != head else '')), after)

    def confirm_restore(self, v, head):
        wid = self.wid()

        def confirmed(yes):
            if yes:
                body = dict(version=v.get('version'), base_version=head, confirm=True)
                self.act(lambda c: c.api_post(f'{W}/{seg(wid)}/outputs/{seg(v.get("output_id"))}/restore', body),
                         lambda r: self.reload_pane('info', f'已恢复为版本 {v.get("version")} 的内容（作为新版本 {r.get("version")}）。'),
                         '正在恢复…', '恢复失败')
        self.app.push_screen(ConfirmModal('恢复成果版本', f'把「{v.get("name")}」恢复为版本 {v.get("version")} 的内容？\n'
                                          f'将基于当前版本 {head} 生成新版本；来源状态以当前核对为准。', '恢复'), confirmed)

    def action_schedule(self):
        wid = self.require()
        if not wid:
            return

        def opened(s):
            s = s if isinstance(s, dict) else {}
            minutes = s.get('minutes', 0)
            options = list(SCHEDULES) if minutes in dict(SCHEDULES) else [*SCHEDULES, (minutes, f'{minutes} 分钟')]

            def validate(v):
                return dict(minutes=int(v['minutes'] or 0), profile=v['profile'] or 'standard', vision=v['vision'] or 'ocr')

            def done(body):
                if body:
                    self.act(lambda c: c.api_put(f'{W}/{seg(wid)}/schedule', body),
                             lambda r: self.set_state('info', '自动维护已关闭。' if not body['minutes'] else
                                                      f'自动维护已设置；下次 {model.fmt_time(r.get("next_at"))}。'),
                             '正在保存…', '保存失败')
            lead = '自动维护会按间隔处理新变化并调用模型，只生成提案，不会自动应用。'
            if s.get('error'):
                lead += f'\n上次错误：{s["error"]}'
            self.app.push_screen(FormModal('自动维护', [
                Field('minutes', '间隔', 'select', minutes, [(label, value) for value, label in options]),
                Field('profile', '分析强度', 'select', s.get('profile') or 'standard', by_value(model.PROFILES)),
                Field('vision', '图片识别', 'select', s.get('vision') or 'ocr', by_value(model.VISION)),
            ], validate, '保存', lead=lead), done)
        self.act(lambda c: c.api_get(f'{W}/{seg(wid)}/schedule'), opened, '正在读取设置…', '读取失败')
