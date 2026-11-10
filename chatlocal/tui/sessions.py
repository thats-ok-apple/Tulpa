"""Persistent group-chat sessions: start (grant/group/persona), stop, language learning."""
import uuid

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, Static

from . import model
from .base import View, esc, fill_table, items, make_table, optional_get, plain, seg, selected_key
from .modals import ConfirmModal, Dialog, TextModal

AGENT_NOTE = ('Tulpa 管理人设、学习与会话；群聊或私聊需要外部 MCP Agent 由你启动并连接此授权后才会开始。'
              '状态显示“等待 Agent”表示尚未有 Agent 接手。')


class StartForm(ModalScreen):
    """Pick grant, target (group or friend) and persona. The idempotency key is fixed per form so retries do not duplicate."""

    BINDINGS = [Binding('escape', 'cancel', '取消'), Binding('ctrl+s', 'submit', '开始')]

    def __init__(self, grants, groups, personas, friends=(), notice=''):
        super().__init__()
        self.grants, self.groups, self.personas = grants, groups, personas
        self.friends = list(friends)
        self.notice = notice
        self.request_key = uuid.uuid4().hex

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog'):
            yield Static('开始持续聊天会话（群聊 / 私聊）', classes='dialog-title')
            with VerticalScroll(classes='dialog-flex'):
                with Horizontal(classes='row'):
                    yield Label('MCP 连接', classes='label')
                    yield Select([(plain(model.one_line(g.get('name') or g['id'], 40)), str(g['id']))
                                  for g in self.grants], prompt='选择连接', id='start-grant')
                with Horizontal(classes='row'):
                    yield Label('群聊 / 私聊', classes='label')
                    yield Select([], prompt='先选择连接', id='start-group')
                with Horizontal(classes='row'):
                    yield Label('人设', classes='label')
                    yield Select([(plain(model.one_line(p.get('name') or p['id'], 40)), str(p['id']))
                                  for p in self.personas], prompt='选择人设', id='start-persona')
                # Below the selects so a small screen still shows every control first.
                if self.notice:
                    yield Static(esc(self.notice), classes='warn', id='start-notice')
                yield Static(esc(AGENT_NOTE), classes='hint')
            yield Static('', classes='error-text', id='start-error')
            with Horizontal(classes='buttons'):
                yield Button('准备会话 (ctrl+s)', id='start', variant='primary')
                yield Button('取消 (Esc)', id='cancel')

    def on_mount(self):
        if len(self.grants) == 1:
            self.query_one('#start-grant', Select).value = str(self.grants[0]['id'])
        if len(self.personas) == 1:
            self.query_one('#start-persona', Select).value = str(self.personas[0]['id'])
        self.query_one('#start-grant').focus()

    def groups_for(self, grant_id):
        """Verified targets covered by this grant, including account-bound all-conversation grants."""
        grant = next((g for g in self.grants if str(g['id']) == grant_id), None)
        if grant is None:
            return []
        scope = model.scope_dict(grant.get('scope'))
        allowed = model.scope_conversations(scope)
        selections = scope.get('conversations')
        if isinstance(selections, list) and selections and allowed is None:
            return []  # A malformed/non-QQ selection is not an all-conversation grant.
        account = scope.get('chat_account') or model.scope_dict(grant.get('accounts')).get('qq')

        def same_account(target):
            return not account or (str(target.get('account')) == str(account)
                                   and model.conversation_id(target).split(':')[0] == str(account))

        groups = [g for g in self.groups if same_account(g) and (allowed is None or model.conversation_id(g) in allowed)]
        # MCPAccess persists an explicit all_conversations grant as conversations=[].
        # Missing scope data still never implies authorization for arbitrary friends.
        all_qq = selections == [] and 'qq' in scope.get('platforms', []) and bool(account)
        friends = [f for f in self.friends if same_account(f) and
                   (all_qq or allowed is not None and model.conversation_id(f) in allowed)]
        return groups + friends

    def on_select_changed(self, event):
        if event.select.id != 'start-grant':
            return
        box = self.query_one('#start-group', Select)
        value = event.value
        if not isinstance(value, str):
            box.set_options([])
            return
        groups = self.groups_for(value)
        box.set_options([(plain(model.group_label(g, 30)), model.conversation_id(g)) for g in groups])
        if len(groups) == 1:
            box.value = model.conversation_id(groups[0])
        self.query_one('#start-error', Static).update(
            '' if groups else '此连接授权的群或好友不在当前 SL 账号的群 / 好友列表中。')

    def on_button_pressed(self, event):
        if event.button.id == 'start':
            self.action_submit()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)

    def action_submit(self):
        values = [self.query_one(f'#start-{k}', Select).value for k in ('grant', 'group', 'persona')]
        if not all(isinstance(v, str) and v for v in values):
            self.query_one('#start-error', Static).update('请选择连接、群聊或私聊，以及人设。')
            return
        grant, group, persona = values
        if group not in {model.conversation_id(g) for g in self.groups_for(grant)}:
            self.query_one('#start-error', Static).update('所选群聊或私聊不在此连接的授权范围内。')
            return
        self.dismiss(dict(connection_id=grant, conversation_id=group, persona_preset=persona, idempotency_key=self.request_key))


class SessionsView(View):
    title = '持续会话'
    subtitle = '群聊 / 私聊持续聊天、人设与语言学习'
    BINDINGS = [Binding('n', 'start', '开始'), Binding('s', 'stop', '停止'),
                Binding('l', 'learning', '学习状态'), Binding('t', 'toggle_learning', '切换学习'),
                Binding('j', 'learning_json', '学习详情')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield make_table('名称', '群聊 / 私聊', '人设', '状态', '参与度', '更新', id='session-table')
        yield Static('', classes='detail', id='session-detail')
        yield Static('n 开始 · s 停止 · l 读取学习状态 · t 开关学习 · j 学习详情 JSON', classes='hint')

    def on_mount(self):
        self.sessions = {}
        self.learning = {}  # session id -> last learning status
        self.query_one('#session-detail').display = False

    def refresh_data(self):
        self.start_load(lambda c: c.get('/sessions'), self.show, '正在读取会话…')

    def show(self, result):
        rows = items(result)
        self.sessions = {str(s.get('id')): s for s in rows}
        fill_table(self.query_one('#session-table'), [
            (s.get('id'), (model.one_line(s.get('name'), 16), model.one_line(s.get('conversation_id'), 22),
                           model.one_line(s.get('persona_name') or s.get('persona_preset'), 12),
                           model.session_state(s), str(s.get('participation') or '—'),
                           model.fmt_time(s.get('updated')))) for s in rows])
        if not rows:
            self.set_state('empty', '还没有持续会话。按 n 选择连接、群聊或私聊和人设来准备会话。')
        self.show_detail()

    def selected(self):
        return self.sessions.get(selected_key(self.query_one('#session-table')) or '')

    def on_data_table_row_highlighted(self, event):
        self.show_detail()

    def show_detail(self):
        box = self.query_one('#session-detail', Static)
        session = self.selected()
        box.display = bool(session)
        if not session:
            return
        lines = [f'[b #a78bfa]{esc(session.get("name") or session.get("id"))}[/]  {esc(model.session_state(session))}',
                 f'[#7f8a9a]会话[/] {esc(session.get("id"))}  [#7f8a9a]连接[/] {esc(session.get("grant_id") or "—")}  '
                 f'[#7f8a9a]人设[/] {esc(session.get("persona_name") or "—")} ({esc(session.get("persona_preset") or "—")})']
        if session.get('note'):
            lines.append(f'[#7f8a9a]说明[/] {esc(model.one_line(session["note"], 160))}')
        if session.get('stop_reason'):
            lines.append(f'[#7f8a9a]停止原因[/] {esc(model.one_line(session["stop_reason"], 160))}')
        if session.get('active'):
            lines.append(f'[#eab308]{esc(AGENT_NOTE)}[/]')
        status = self.learning.get(str(session.get('id')))
        if status is None:
            lines.append('[#7f8a9a]语言学习：按 l 读取[/]')
        else:
            summary = model.learning_summary(status)
            lines.append('[#7f8a9a]语言学习[/] ' + (' · '.join(f'{esc(k)} {esc(v)}' for k, v in summary) or '无摘要，按 j 查看详情'))
        box.update('\n'.join(lines))

    # ---- start / stop ---------------------------------------------------

    def action_start(self):
        self.set_state('loading', '正在读取连接、群、好友和人设…')

        def fetch(client):
            # /friends is optional: an older backend keeps the group-only form.
            return (client.get('/grants'), client.get('/groups'), client.get('/personas'),
                    optional_get(client, '/friends'))

        def ok(result):
            grants, groups, personas = (items(r) for r in result[:3])
            friends_result, friends_error = result[3]
            usable = [g for g in grants if isinstance(g, dict) and g.get('id') and model.grant_usable(g)]
            # Continuous chat needs send+chat; a read-only grant cannot host a session.
            grants = [g for g in usable if model.grant_chat_capable(g)]
            groups = model.valid_targets(groups, 'group')
            friends = model.valid_targets(items(friends_result), 'direct')
            personas = [p for p in personas if isinstance(p, dict) and p.get('id')]
            if usable and not grants:
                self.set_state('empty', '现有连接都没有「发送 + 持续聊天」权限，无法开始会话。'
                                        '请在「MCP 授权」中新建一个勾选这两项的连接。')
                return
            missing = [label for label, rows in (('可持续聊天的 MCP 连接', grants),
                                                 ('当前 SL 账号的群或好友', groups + friends),
                                                 ('人设', personas)) if not rows]
            if missing:
                self.set_state('error', '无法开始：缺少' + '、'.join(missing) + '。')
                return
            notice = f'未能读取好友列表，本次只能选择群：{friends_error}' if friends_error else ''
            self.set_state('ok')
            self.app.push_screen(StartForm(grants, groups, personas, friends, notice), self.submit)

        def failed(message):
            self.set_state('error', f'无法准备表单：{message}')
        self.app.request(fetch, ok, failed)

    def submit(self, body):
        if not body:
            return
        self.set_state('loading', '正在准备会话…')

        def ok(result):
            result = result if isinstance(result, dict) else {}
            note = result.get('note') or ''
            self.app.show_status('会话已准备，等待外部 Agent 连接')
            self.reload_with('info', note.strip() or '会话已准备，等待 Agent。')

        def failed(message):
            self.set_state('error', f'开始失败：{message}')
        self.app.request(lambda c: c.post('/sessions/start', body), ok, failed)

    def action_stop(self):
        session = self.selected()
        if not session:
            self.set_state('empty', '请先选择一个会话。')
            return
        if not session.get('active'):
            self.set_state('info', '该会话已停止。')
            return
        name = session.get('name') or session.get('id')
        sid = session.get('id')

        def confirmed(yes):
            if not yes:
                return
            self.set_state('loading', f'正在停止「{name}」…')

            def ok(result):
                self.app.show_status(f'已停止会话：{name}')
                self.reload_with('info', f'已停止「{name}」。')

            def failed(message):
                self.set_state('error', f'停止失败：{message}')
            self.app.request(lambda c: c.post(f'/sessions/{seg(sid)}/stop', {}), ok, failed)
        self.app.push_screen(ConfirmModal('停止持续会话', f'确定停止「{name}」吗？\n此会话的持续群聊 / 私聊与表情操作将停止。该 MCP 连接的其他权限不受影响，如需关闭请撤销连接。',
                                          '停止', danger=True), confirmed)

    # ---- learning -------------------------------------------------------

    def remember_learning(self, sid, status):
        self.learning[str(sid)] = status
        self.show_detail()

    def action_learning(self, then=None, sid=None):
        if sid is None:
            session = self.selected()
            if not session:
                self.set_state('empty', '请先选择一个会话。')
                return
            sid = session.get('id')
        self.set_state('loading', '正在读取语言学习状态…')

        def ok(result):
            self.set_state('ok')
            self.remember_learning(sid, result)
            if then:
                then(result)

        def failed(message):
            self.set_state('error', f'读取学习状态失败：{message}')
        self.app.request(lambda c: c.get(f'/sessions/{seg(sid)}/learning'), ok, failed)

    def action_toggle_learning(self, sid=None):
        # Carry the originally chosen session id through the async read, so a cursor
        # move while the status request is in flight cannot toggle a different session.
        if sid is None:
            session = self.selected()
            if not session:
                self.set_state('empty', '请先选择一个会话。')
                return
            sid = session.get('id')
        status = self.learning.get(str(sid))
        if status is None:
            self.action_learning(then=lambda _: self.action_toggle_learning(sid=sid), sid=sid)
            return
        current = model.learning_enabled(status)
        if current is None:
            self.set_state('error', '学习状态格式未知，无法切换；按 j 查看详情。')
            return
        name = (self.sessions.get(str(sid)) or {}).get('name') or sid
        target = not current
        action = '开启' if target else '关闭'
        self.set_state('loading', f'正在{action}「{model.one_line(name, 20)}」的语言学习…')

        def ok(result):
            self.set_state('info', f'「{model.one_line(name, 20)}」的语言学习已{action}。')
            self.remember_learning(sid, result)

        def failed(message):
            self.set_state('error', f'切换失败：{message}')
        self.app.request(lambda c: c.put(f'/sessions/{seg(sid)}/learning', {'enabled': target}), ok, failed)

    def action_learning_json(self):
        session = self.selected()
        if not session:
            self.set_state('empty', '请先选择一个会话。')
            return
        sid = str(session.get('id'))

        def open_modal(status):
            self.app.push_screen(TextModal(f'语言学习：{session.get("name") or sid}', model.pretty_json(status)))
        if sid in self.learning:
            open_modal(self.learning[sid])
        else:
            self.action_learning(then=open_modal)
