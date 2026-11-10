"""Overview, SL settings, personas, local search and tool-call event views."""
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, Static

from . import model
from .base import View, esc, fill_table, items, make_table, selected_key
from .modals import TextModal


def kv(rows):
    return '\n'.join(f'[#7f8a9a]{esc(k)}[/]  {v}' for k, v in rows)


def flag(value, yes='是', no='否'):
    return f'[#22c55e]{yes}[/]' if value else f'[#7f8a9a]{no}[/]'


class OverviewView(View):
    title = '概览'
    subtitle = '后台服务、SL 传输、MCP 与本地数据状态'

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes='cards'):
            yield Static('', classes='card', id='ov-service')
            yield Static('', classes='card', id='ov-mcp')
        with Horizontal(classes='cards'):
            yield Static('', classes='card', id='ov-sl')
            yield Static('', classes='card', id='ov-data')
        yield Static('', classes='hint', id='ov-hint')

    def on_mount(self):
        for card in self.query('.card'):
            card.display = False

    def refresh_data(self):
        self.start_load(lambda c: c.get('/overview'), self.show, '正在读取后台状态…')

    def show(self, data):
        data = data if isinstance(data, dict) else {}
        self.app.update_overview(data)
        mcp, sl = data.get('mcp') or {}, data.get('sl') or {}
        counts, caps = data.get('counts') or {}, data.get('capabilities') or {}
        receiver = data.get('receiver') or {}
        storage = data.get('storage')
        if isinstance(storage, dict):
            storage = model.one_line(', '.join(f'{k}={v}' for k, v in storage.items()), 60)
        self.query_one('#ov-service', Static).update('[b #a78bfa]服务[/]\n' + kv([
            ('版本', esc(data.get('version') or '—')), ('平台', esc(data.get('platform') or '—')),
            ('事件接收', esc(model.receiver_state(receiver))),
            ('说明', esc(model.one_line(receiver.get('note') or '—', 50))),
            ('存储', esc(storage or '—'))]))
        mcp_rows = [('启用', flag(mcp.get('enabled'))), ('运行', flag(mcp.get('running'), '运行中', '未运行')),
                    ('端口', esc(mcp.get('port') or '—')), ('地址', esc(mcp.get('url') or '—'))]
        if mcp.get('error'):
            mcp_rows.append(('错误', f'[#ef4444]{esc(model.one_line(mcp["error"], 60))}[/]'))
        self.query_one('#ov-mcp', Static).update('[b #a78bfa]MCP 服务[/]\n' + kv(mcp_rows))
        self.query_one('#ov-sl', Static).update('[b #a78bfa]SnowLuma 传输[/]\n' + kv([
            ('已配置', flag(sl.get('configured'))), ('HTTP', esc(sl.get('http_url') or '—')),
            ('WebSocket', esc(sl.get('ws_url') or '—')),
            ('凭据', f'HTTP {flag(sl.get("has_http_token"), "已保存", "未保存")} · WS {flag(sl.get("has_ws_token"), "已保存", "未保存")}')]))
        self.query_one('#ov-data', Static).update('[b #a78bfa]数据[/]\n' + kv([
            ('本地消息', esc(counts.get('messages', '—'))), ('MCP 连接', esc(counts.get('connections', '—'))),
            ('活跃会话', esc(counts.get('active_sessions', '—'))),
            # native_import only covers reading QQ/WeChat's own databases.
            # Importing an exported JSON/JSONL file works regardless.
            ('原生数据库读取', flag(caps.get('native_import'), '已适配', '未适配')
             + ('  [#7f8a9a]（0 本机数据）[/]' if caps.get('native_import') else '')),
            ('JSON 文件导入', '[#22c55e]可用[/]'),
            ('托管 SnowLuma', flag(caps.get('managed_snowluma'), '可用', '不可用'))]))
        for card in self.query('.card'):
            card.display = True
        hint = []
        if not sl.get('configured'):
            hint.append('SL 尚未配置：按 2 进入「SL 连接」填写地址后手动测试。')
        if not mcp.get('running'):
            hint.append('MCP 未运行：在「MCP 授权」中按 m 启用并设置端口。')
        if not counts.get('messages'):
            hint.append('本地还没有消息：按 0 进入「本机数据」读取 QQ / 微信，或导入 imports/ 下的 JSON。')
        hint.append('持续群聊需要外部 MCP Agent 由你启动并连接，否则会话保持“等待 Agent”。'
                    'Agent 对话（8）、回复草稿、关注卡与工作区任务只在你提交时调用模型；发送回复须预览并单独确认。')
        hint.append('更多页面在左侧菜单（↑↓ 选择）：聊天记录 9、本机数据 0、记忆、关注卡、文件材料、证据集合、'
                    'QQ 群资料、调查工作区、SL 托管、组件与服务。')
        self.query_one('#ov-hint', Static).update('\n'.join(esc(h) for h in hint))


class SLView(View):
    title = 'SL 连接'
    subtitle = 'SnowLuma OneBot 地址与凭据；保存不会启动 SnowLuma'
    BINDINGS = [Binding('ctrl+s', 'save', '保存'), Binding('ctrl+t', 'test', '只读测试')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        # Entry to the managed page; it only navigates, nothing starts or is agreed to from here.
        with Horizontal(id='sl-docker-bar'):
            yield Button('SL Docker 托管', id='sl-docker', variant='primary')
            yield Static('由 Tulpa 启动并托管 SnowLuma 运行环境，扫码登录 QQ', classes='hint')
        # The fields scroll; the buttons stay below so they are reachable on a short terminal.
        with VerticalScroll(classes='page-flex'):
            with Horizontal(classes='row'):
                yield Label('HTTP 地址', classes='label')
                yield Input(placeholder='http://127.0.0.1:3000', id='sl-http')
            with Horizontal(classes='row'):
                yield Label('WebSocket 地址', classes='label')
                yield Input(placeholder='ws://127.0.0.1:3001', id='sl-ws')
            with Horizontal(classes='row'):
                yield Label('HTTP Token', classes='label')
                yield Input(password=True, id='sl-http-token')
            with Horizontal(classes='row'):
                yield Label('WS Token', classes='label')
                yield Input(password=True, id='sl-ws-token')
            yield Static('修改连接设置会停止现有群聊会话。Token 留空表示保留已保存的值。Token 不会显示在日志或状态栏中。',
                         classes='hint')
            yield Static('', classes='detail', id='sl-result')
        with Horizontal(classes='buttons'):
            yield Button('保存 (ctrl+s)', id='sl-save', variant='primary')
            yield Button('只读测试 (ctrl+t)', id='sl-test')

    def on_mount(self):
        self.query_one('#sl-result').display = False

    def refresh_data(self):
        self.start_load(lambda c: c.get('/sl'), self.fill, '正在读取 SL 配置…')

    def fill(self, data):
        data = data if isinstance(data, dict) else {}
        self.query_one('#sl-http', Input).value = str(data.get('http_url') or '')
        self.query_one('#sl-ws', Input).value = str(data.get('ws_url') or '')
        for key, field in (('has_http_token', '#sl-http-token'), ('has_ws_token', '#sl-ws-token')):
            box = self.query_one(field, Input)
            box.value = ''
            box.placeholder = '已保存（留空保留）' if data.get(key) else '未设置'
        self.set_state('info', '已配置。测试需要手动触发。' if data.get('configured') else '尚未配置 SL 连接。')

    def on_button_pressed(self, event):
        if event.button.id == 'sl-docker':
            self.app.action_goto('managed')
        elif event.button.id == 'sl-save':
            self.action_save()
        elif event.button.id == 'sl-test':
            self.action_test()

    def on_input_submitted(self, event):
        event.stop()
        self.screen.focus_next()  # Enter moves on; saving is always explicit.

    def action_save(self):
        try:
            body = model.sl_body(*(self.query_one(f, Input).value for f in
                                   ('#sl-http', '#sl-ws', '#sl-http-token', '#sl-ws-token')))
        except ValueError as exc:
            self.set_state('error', str(exc))
            return
        self.set_state('loading', '正在保存…')
        self.query_one('#sl-save', Button).disabled = True

        def ok(result):
            self.query_one('#sl-save', Button).disabled = False
            self.fill(result)
            self.set_state('info', '已保存。SnowLuma 不会因此启动；可按 ctrl+t 测试。')
            self.app.show_status('SL 配置已保存')

        def failed(message):
            self.query_one('#sl-save', Button).disabled = False
            self.set_state('error', f'保存失败：{message}')
        self.app.request(lambda c: c.put('/sl', body), ok, failed)

    def action_test(self):
        self.set_state('loading', '正在进行只读测试（身份与事件连接）…')
        self.query_one('#sl-test', Button).disabled = True

        def ok(result):
            self.query_one('#sl-test', Button).disabled = False
            result = result if isinstance(result, dict) else {}
            rows = [('结果', flag(result.get('ok'), '通过', '失败')), ('账号', esc(result.get('account') or '—')),
                    ('说明', esc(result.get('message') or '—'))]
            for key, label in (('http', 'HTTP'), ('ws', 'WebSocket')):
                if key in result:
                    value = result[key]
                    if isinstance(value, dict):
                        value = ' · '.join(f'{k}: {v}' for k, v in value.items())
                    rows.append((label, esc(model.one_line(value, 70))))
            box = self.query_one('#sl-result', Static)
            box.update(kv(rows))
            box.display = True
            self.set_state('info' if result.get('ok') else 'error', '测试通过。' if result.get('ok') else '测试未通过，详见下方。')

        def failed(message):
            self.query_one('#sl-test', Button).disabled = False
            self.set_state('error', f'测试失败：{message}')
        self.app.request(lambda c: c.post('/sl/test', {}), ok, failed)


class PersonasView(View):
    title = '人设'
    subtitle = 'Tulpa 人设预设（只读）；Enter 查看全文'

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield make_table('名称', 'ID', '简介', id='persona-table')
        yield Static('', classes='detail', id='persona-detail')

    def on_mount(self):
        self.personas = {}
        self.query_one('#persona-detail').display = False

    def refresh_data(self):
        self.start_load(lambda c: c.get('/personas'), self.show, '正在读取人设…')

    def show(self, result):
        rows = items(result)
        self.personas = {str(p.get('id')): p for p in rows}
        fill_table(self.query_one('#persona-table'), [
            (p.get('id'), (model.one_line(p.get('name'), 20), model.one_line(p.get('id'), 18),
                           model.one_line(p.get('description'), 60))) for p in rows])
        if not rows:
            self.set_state('empty', '没有可用的人设预设。')
        self.show_detail()

    def on_data_table_row_highlighted(self, event):
        self.show_detail()

    def on_data_table_row_selected(self, event):
        persona = self.current_persona()
        if persona:
            self.app.push_screen(TextModal(f'人设：{persona.get("name") or persona.get("id")}',
                                           str(persona.get('content') or ''), esc(persona.get('description') or '')))

    def current_persona(self):
        return self.personas.get(selected_key(self.query_one('#persona-table')) or '')

    def show_detail(self):
        box = self.query_one('#persona-detail', Static)
        persona = self.current_persona()
        box.display = bool(persona)
        if persona:
            box.update(f'[b #a78bfa]{esc(persona.get("name") or "—")}[/]\n'
                       f'{esc(model.one_line(persona.get("content"), 400))}')


class SearchView(View):
    title = '本地搜索'
    subtitle = '搜索本机已导入的消息索引；并非所有 QQ 历史都会自动导入'

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes='search-bar'):
            yield Input(placeholder='输入关键词后按 Enter', id='search-input')
            yield Button('搜索', id='search-go', variant='primary')
        yield make_table('时间', '会话', '发送者', '内容', id='search-table')
        yield Static('', classes='detail', id='search-detail')

    def on_mount(self):
        self.messages = {}
        self.query_one('#search-detail').display = False

    def activate(self):
        self.loaded = True  # Nothing to load until the user searches.
        if not self.messages and not self.busy:
            self.set_state('empty', '输入关键词开始搜索（最多显示 20 条）。')

    def refresh_data(self):
        self.search()

    def focus_main(self):
        self.query_one('#search-input').focus()

    def on_input_submitted(self, event):
        event.stop()
        self.search()

    def on_button_pressed(self, event):
        if event.button.id == 'search-go':
            self.search()

    def search(self):
        query = self.query_one('#search-input', Input).value.strip()
        if not query:
            self.set_state('empty', '请输入关键词。')
            return
        self.start_load(lambda c: c.get('/search', query=query, limit=20), self.show, f'正在搜索「{query}」…')

    def show(self, result):
        rows = items(result, 'messages')
        self.messages = {str(m.get('id')): m for m in rows}
        fill_table(self.query_one('#search-table'), [
            (m.get('id'), (model.fmt_time(m.get('timestamp')),
                           model.one_line(m.get('conversation') or m.get('conversation_id'), 18),
                           model.one_line(m.get('sender'), 12), model.one_line(m.get('content'), 70))) for m in rows])
        if not rows:
            self.set_state('empty', '没有匹配的本地消息。可能尚未导入相关聊天记录。')
        else:
            self.set_state('info', f'找到 {len(rows)} 条。')
            self.query_one('#search-table').focus()
        self.show_detail()

    def on_data_table_row_highlighted(self, event):
        self.show_detail()

    def show_detail(self):
        box = self.query_one('#search-detail', Static)
        message = self.messages.get(selected_key(self.query_one('#search-table')) or '')
        box.display = bool(message)
        if message:
            box.update(f'[#a78bfa]{esc(message.get("sender") or "—")}[/] · {esc(message.get("conversation") or "")} · '
                       f'{esc(model.fmt_time(message.get("timestamp")))}\n{esc(message.get("content") or "")}')


class EventsView(View):
    title = '工具调用'
    subtitle = '外部 Agent 最近的 MCP 工具调用'

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield make_table('时间', '连接', '工具', '状态', '耗时', id='events-table')

    def refresh_data(self):
        self.start_load(lambda c: c.get('/events'), self.show, '正在读取调用记录…')

    def show(self, result):
        rows = items(result)
        fill_table(self.query_one('#events-table'), [
            (e.get('id'), (model.fmt_time(e.get('at')), model.one_line(e.get('name'), 18),
                           model.one_line(e.get('tool'), 26), status_cell(e.get('status')),
                           model.fmt_seconds(e.get('seconds')))) for e in rows])
        if not rows:
            self.set_state('empty', '暂无工具调用。外部 Agent 连接并调用工具后会显示在这里。')


def status_cell(status):
    status = str(status or '—')
    low = status.lower()
    style = '#22c55e' if low in ('ok', 'success', 'done') else '#ef4444' if low in ('error', 'failed', 'denied') else ''
    return Text(status, style=style)
