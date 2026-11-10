"""Model settings and local Agent dialogue: sessions, ask, stop, polled history.

Asking is the only action here that invokes the configured model, and only on an
explicit submit. The API key is a password input and is never shown or logged.
"""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Static

from . import model
from .base import View, esc, fill_table, items, make_table, seg, selected_key
from .forms import by_value, Field, FormModal, date_text, required
from .modals import ConfirmModal

AGENT = '/api/terminal/agent/sessions'
RUNNING = ('running', 'queued')


def model_form(current):
    current = current if isinstance(current, dict) else {}
    key_state = '已保存（留空保留）' if current.get('has_api_key') else '未设置'

    def validate(v):
        body = dict(api_base=required(v['api_base'], 'API 地址', 500), model=required(v['model'], '模型名称', 200),
                    reasoning_effort=v['reasoning_effort'] or 'high')
        if v['clear_api_key']:
            if v['api_key']:
                raise ValueError('不能同时填写新密钥和清除密钥。')
            body['clear_api_key'] = True
        elif v['api_key'].strip():
            body['api_key'] = v['api_key'].strip()
        return body
    return FormModal('模型设置', [
        Field('api_base', 'API 地址', value=current.get('api_base') or '', placeholder='https://api.example.com/v1'),
        Field('model', '模型名称', value=current.get('model') or ''),
        Field('api_key', 'API Key', 'password', placeholder=key_state,
              hint='密钥只写入本机配置，不会回显、记录日志或出现在状态栏。'),
        Field('clear_api_key', '清除已保存的 API Key', 'check', False),
        Field('reasoning_effort', '推理强度', 'select', current.get('reasoning_effort') or 'high', by_value(model.REASONING)),
    ], validate, '保存')


def options_form(options, conversations):
    def validate(v):
        platforms = [p for p in ('qq', 'wechat') if v[p]]
        if not platforms:
            raise ValueError('请至少选择一个平台。')
        start, end = date_text(v['start'], '开始日期'), date_text(v['end'], '截止日期')
        if start and end and start > end:
            raise ValueError('开始日期不能晚于截止日期。')
        chosen = [c for c in v['conversations'] if model.unpair(c)[0] in platforms]
        return dict(platforms=platforms, conversations=chosen, start=start, end=end,
                    profile=v['profile'] or 'standard', vision=v['vision'] or 'ocr',
                    include_reasoning=v['include_reasoning'])
    convs = [(f'{model.platform_label(c.get("platform"))} · {model.one_line(c.get("conversation") or c.get("conversation_id"), 40)}',
              model.pair(c.get('platform'), c.get('conversation_id'))) for c in conversations]
    return FormModal('提问范围与分析强度', [
        Field('qq', '包含 QQ', 'check', 'qq' in options['platforms']),
        Field('wechat', '包含微信', 'check', 'wechat' in options['platforms']),
        Field('conversations', '限定会话（不选表示所选平台全部已导入会话）', 'multi', options['conversations'], convs),
        Field('start', '开始日期', value=options['start'], placeholder='YYYY-MM-DD，可留空'),
        Field('end', '截止日期', value=options['end'], placeholder='YYYY-MM-DD，可留空'),
        Field('profile', '分析强度', 'select', options['profile'], by_value(model.PROFILES)),
        Field('vision', '图片识别', 'select', options['vision'], by_value(model.VISION)),
        Field('include_reasoning', '在记录中保留模型推理摘要', 'check', options['include_reasoning']),
    ], validate, '应用', lead='这些选项只影响之后的提问；提交问题时才会调用模型。')


class AgentView(View):
    title = 'Agent 对话'
    subtitle = '基于本机已导入记录提问；提交时调用已配置的模型'
    BINDINGS = [Binding('n', 'new', '新对话'), Binding('d', 'delete', '删除'), Binding('o', 'options', '提问选项'),
                Binding('m', 'model', '模型设置'), Binding('x', 'stop', '停止本轮')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static('', classes='hint', id='agent-model')
        with Horizontal(classes='split'):
            yield make_table('对话', '更新', id='agent-table')
            with VerticalScroll(classes='pane', id='agent-log-box'):
                yield Static('', id='agent-log')
        yield Static('', classes='hint', id='agent-options')
        with Horizontal(classes='search-bar'):
            yield Input(placeholder='输入问题后按 Enter 提交（会调用模型）', id='agent-input', max_length=2000)
            yield Button('提问', id='agent-ask', variant='primary')
        yield Static('n 新对话 · d 删除 · o 范围/强度 · m 模型设置 · x 停止本轮', classes='hint')

    def on_mount(self):
        self.sessions, self.history, self.model_cfg, self.conversations = {}, {}, {}, []
        self.options = dict(platforms=['qq', 'wechat'], conversations=[], start='', end='',
                            profile='standard', vision='ocr', include_reasoning=False)
        self.show_options()

    def refresh_data(self):
        def fetch(c):
            return c.get('/model'), c.get('/agent/sessions')
        self.start_load(fetch, self.show, '正在读取模型设置与对话…')

    def show(self, result):
        cfg, sessions = result
        self.model_cfg = cfg if isinstance(cfg, dict) else {}
        self.show_model()
        rows = items(sessions)
        self.sessions = {str(s.get('id')): s for s in rows}
        fill_table(self.query_one('#agent-table'), [
            (s.get('id'), (model.one_line(s.get('title') or '新对话', 22), model.fmt_time(s.get('updated')))) for s in rows])
        if not rows:
            self.set_state('empty', '还没有对话。按 n 新建，然后输入问题。')
        self.load_history()

    def show_model(self):
        cfg = self.model_cfg
        key = '已保存' if cfg.get('has_api_key') else '[#ef4444]未设置[/]'
        self.query_one('#agent-model', Static).update(
            f'模型 {esc(cfg.get("model") or "—")} · {esc(cfg.get("api_base") or "—")} · API Key {key} · '
            f'推理 {esc(dict(model.REASONING).get(cfg.get("reasoning_effort"), cfg.get("reasoning_effort") or "—"))}')

    def show_options(self):
        o = self.options
        parts = ['、'.join(model.platform_label(p) for p in o['platforms']),
                 f'{len(o["conversations"])} 个会话' if o['conversations'] else '全部会话',
                 f'{o["start"] or "不限"} 至 {o["end"] or "不限"}', dict(model.PROFILES).get(o['profile'], o['profile']),
                 dict(model.VISION).get(o['vision'], o['vision'])]
        self.query_one('#agent-options', Static).update('范围：' + esc(' · '.join(parts)))

    def selected(self):
        return selected_key(self.query_one('#agent-table'))

    def on_data_table_row_highlighted(self, event):
        self.load_history()

    def load_history(self):
        sid = self.selected()
        if not sid:
            self.render_history(None)
            return
        self.poll('history', lambda c: c.get(f'/agent/sessions/{seg(sid)}'), lambda r: self.got_history(sid, r))

    def got_history(self, sid, result):
        self.history[sid] = result if isinstance(result, dict) else {}
        if self.selected() != sid:
            return False
        self.render_history(self.history[sid])
        return any(t.get('status') in RUNNING for t in self.turns(sid))

    def turns(self, sid):
        value = (self.history.get(sid) or {}).get('turns')
        return value if isinstance(value, list) else []

    def render_history(self, data):
        box = self.query_one('#agent-log', Static)
        if not data:
            box.update('[#7f8a9a]选择或新建一个对话。[/]')
            return
        blocks = []
        for turn in data.get('turns') or []:
            record = turn.get('record') if isinstance(turn.get('record'), dict) else {}
            status = str(turn.get('status') or '')
            blocks.append(f'[b #22d3ee]问[/] {esc(turn.get("question") or "")}  [#7f8a9a]{esc(status)}[/]')
            if status in RUNNING:
                lines = [model.event_line(e) for e in (record.get('events') or [])][-6:]
                blocks.append('[#eab308]正在回答…[/]\n' + esc('\n'.join(l for l in lines if l)))
            else:
                text = model.claims_text(record.get('result')) or '（无回答内容）'
                blocks.append(esc(text))
                stats = [f'{record["seconds"]} 秒' if record.get('seconds') else '',
                         f'{record["requests"]} 次模型请求' if record.get('requests') else '']
                if any(stats):
                    blocks.append(f'[#7f8a9a]{esc(" · ".join(s for s in stats if s))}[/]')
        box.update('\n\n'.join(blocks) or '[#7f8a9a]此对话还没有提问。[/]')
        self.query_one('#agent-log-box').scroll_end(animate=False)

    def on_input_submitted(self, event):
        event.stop()
        if event.input.id == 'agent-input':
            self.ask()

    def on_button_pressed(self, event):
        if event.button.id == 'agent-ask':
            self.ask()

    def ask(self):
        box = self.query_one('#agent-input', Input)
        question = box.value.strip()
        sid = self.selected()
        if not question:
            self.set_state('empty', '请输入问题。')
            return
        if not sid:
            self.set_state('empty', '请先按 n 新建对话。')
            return
        if not self.model_cfg.get('has_api_key'):
            self.set_state('error', '尚未设置 API Key：按 m 打开模型设置。')
            return
        if any(t.get('status') in RUNNING for t in self.turns(sid)):
            self.set_state('info', '本对话正在回答，请等待或按 x 停止。')
            return
        body = dict(self.options, question=question)

        def ok(result):
            box.value = ''
            self.app.show_status('问题已提交，正在等待模型回答')
            self.load_history()
        self.act(lambda c: c.post(f'/agent/sessions/{seg(sid)}/ask', body), ok, '正在提交问题…', '提问失败')

    def action_new(self):
        def ok(result):
            sid = (result or {}).get('id')
            self.app.show_status('已新建对话')

            def select(_):
                table = self.query_one('#agent-table')
                for index, key in enumerate(table.rows):
                    if key.value == sid:
                        table.move_cursor(row=index)
                self.query_one('#agent-input').focus()
            self.start_load(lambda c: (c.get('/model'), c.get('/agent/sessions')),
                            lambda r: (self.show(r), select(r)))
        self.act(lambda c: c.post('/agent/sessions', {}), ok, '正在新建对话…', '新建失败')

    def action_delete(self):
        sid = self.selected()
        if not sid:
            self.set_state('empty', '请先选择一个对话。')
            return
        title = (self.sessions.get(sid) or {}).get('title') or '新对话'
        if any(t.get('status') in RUNNING for t in self.turns(sid)):
            self.set_state('info', '本轮仍在运行，请先按 x 停止并等待退出。')
            return

        def confirmed(yes):
            if yes:
                self.stop_poll('history')
                self.act(lambda c: c.api_delete(f'{AGENT}/{seg(sid)}', {}),
                         lambda r: self.reload_with('info', f'已删除对话「{title}」。'), '正在删除…', '删除失败')
        self.app.push_screen(ConfirmModal('删除对话', f'确定删除对话「{title}」及其全部问答记录吗？此操作不可恢复。',
                                          '删除', danger=True), confirmed)

    def action_stop(self):
        sid = self.selected()
        running = [t for t in self.turns(sid or '') if t.get('status') in RUNNING]
        if not running:
            self.set_state('info', '当前对话没有正在运行的回答。')
            return
        tid = running[-1].get('id')
        self.act(lambda c: c.post(f'/agent/turns/{seg(tid)}/stop', {}),
                 lambda r: (self.set_state('info', '已请求停止；已完成的检索记录保留。'), self.load_history()),
                 '正在停止…', '停止失败')

    def action_options(self):
        def opened(conversations):
            def done(body):
                if body:
                    self.options = body
                    self.show_options()
            self.conversations = items(conversations, 'conversations')
            self.app.push_screen(options_form(self.options, self.conversations), done)
        self.act(lambda c: c.api_get('/api/chat-conversations', limit=200), opened, '正在读取会话列表…', '读取会话失败')

    def action_model(self):
        def done(body):
            if not body:
                return

            def ok(result):
                self.model_cfg = result if isinstance(result, dict) else self.model_cfg
                self.show_model()
                self.set_state('info', '模型设置已保存。')
                self.app.show_status('模型设置已保存')  # Never the key itself.
            self.act(lambda c: c.put('/model', body), ok, '正在保存模型设置…', '保存失败')
        self.app.push_screen(model_form(self.model_cfg), done)
