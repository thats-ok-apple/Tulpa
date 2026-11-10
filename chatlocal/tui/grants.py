"""MCP grants: list, create (group + explicit permissions), revoke, MCP enable/port."""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, SelectionList, Static

from . import model
from .base import View, esc, fill_table, items, make_table, optional_get, plain, seg, selected_key
from .modals import ConfirmModal, CredentialModal, Dialog


class GrantForm(ModalScreen):
    """Collects a grant body. Dismisses with the body dict or None; never sends anything itself."""

    BINDINGS = [Binding('escape', 'cancel', '取消'), Binding('ctrl+s', 'submit', '创建')]

    def __init__(self, groups, friends=(), extensions=(), notices=()):
        super().__init__()
        self.groups, self.friends = list(groups), list(friends)
        # [(flag, label, parent, scope)] from model.extension_permissions; empty on an old backend.
        self.extensions = list(extensions)
        self.notices = list(notices)

    @property
    def all_flags(self):
        return model.FLAGS + tuple(e[0] for e in self.extensions)

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog'):
            yield Static('新建 MCP 连接', classes='dialog-title')
            with VerticalScroll(classes='dialog-flex form-scroll'):
                for notice in self.notices:
                    yield Static(esc(notice), classes='warn grant-notice')
                with Horizontal(classes='row'):
                    yield Label('连接名称', classes='label')
                    yield Input(placeholder='例如：Codex 群聊', max_length=model.NAME_LIMIT, id='grant-name')
                yield Static('允许的 QQ 群（空格勾选）', classes='card-title')
                yield SelectionList(*[(plain(model.group_label(g)), i) for i, g in enumerate(self.groups)],
                                    id='group-list')
                if self.friends:
                    yield Static('允许的 QQ 好友私聊（空格勾选，不含临时会话）', classes='card-title')
                    yield SelectionList(*[(plain(model.group_label(f)), i) for i, f in enumerate(self.friends)],
                                        id='friend-list')
                with Horizontal(classes='row'):
                    yield Label('开始日期', classes='label')
                    yield Input(placeholder='YYYY-MM-DD，可留空', id='grant-start')
                with Horizontal(classes='row'):
                    yield Label('截止日期', classes='label')
                    yield Input(placeholder='YYYY-MM-DD，可留空（含当天）', id='grant-end')
                yield Static('权限（默认全部关闭，灰色项需先开启前置权限）', classes='card-title')
                for flag, label, parent in model.PERMISSIONS:
                    indent = '    ' if parent and parent != 'send' else '  ' if parent else ''
                    yield Checkbox(f'{indent}{label}', False, id=f'perm-{flag}')
                if self.extensions:
                    yield Static('SL 扩展权限（默认关闭；实时语音/视频/文件需先开启发送和持续聊天）', classes='card-title')
                    for flag, label, parent, scope in self.extensions:
                        if scope == 'account':
                            continue
                        yield Checkbox(f'{"    " if parent else ""}{esc(label)}', False, id=f'perm-{flag}')
                    account = [e for e in self.extensions if e[3] == 'account']
                    if account:
                        yield Static('账号级权限：作用于整个当前 QQ 账号，不局限于上面选择的群或好友，'
                                     '包括所选范围以外的联系人。只在确实需要时逐项开启。',
                                     classes='warn', id='account-scope-hint')
                        for flag, label, _parent, _scope in account:
                            yield Checkbox(f'账号级 · {esc(label)}', False, id=f'perm-{flag}')
                yield Static('外部 Agent 读到的内容可能发给它的模型服务。已有连接不会自动获得新增权限；'
                             '之后可在列表中按 e 修改权限。', classes='hint')
            yield Static('', classes='error-text', id='grant-error')
            with Horizontal(classes='buttons'):
                yield Button('创建 (ctrl+s)', id='create', variant='primary')
                yield Button('取消 (Esc)', id='cancel')

    def on_mount(self):
        self.sync_flags()
        self.query_one('#grant-name').focus()

    def flags(self):
        return {flag: self.query_one(f'#perm-{flag}', Checkbox).value for flag in self.all_flags}

    def sync_flags(self):
        """Uncheck and disable any permission whose prerequisite is off."""
        values = self.flags()
        clean = model.normalize_flags(values, [e[0] for e in self.extensions])
        for flag in self.all_flags:
            box = self.query_one(f'#perm-{flag}', Checkbox)
            if box.value != clean[flag]:
                box.value = clean[flag]
            usable = model.allowed(flag, clean)
            box.disabled = not usable
            box.set_class(not usable, '-blocked')

    def on_checkbox_changed(self, event):
        self.sync_flags()

    def on_input_submitted(self, event):
        event.stop()
        self.focus_next()

    def on_button_pressed(self, event):
        if event.button.id == 'create':
            self.action_submit()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)

    def action_submit(self):
        selected = [self.groups[i] for i in self.query_one('#group-list', SelectionList).selected]
        if self.friends:
            selected += [self.friends[i] for i in self.query_one('#friend-list', SelectionList).selected]
        try:
            body = model.grant_body(self.query_one('#grant-name', Input).value, selected, self.flags(),
                                    self.query_one('#grant-start', Input).value, self.query_one('#grant-end', Input).value,
                                    extra=[e[0] for e in self.extensions])
        except ValueError as exc:
            self.query_one('#grant-error', Static).update(esc(exc))
            return
        self.dismiss(body)


class GrantEditForm(ModalScreen):
    """Edits the permission switches of one existing grant.

    Name, account, dates, conversations and the credential stay as they are. Dismisses
    with the PUT body (changed switches + expected_revision), 'unchanged', or None.
    """

    BINDINGS = [Binding('escape', 'cancel', '取消'), Binding('ctrl+s', 'submit', '保存')]

    def __init__(self, grant, extensions=(), notices=()):
        super().__init__()
        self.grant = grant
        self.scope = model.scope_dict(grant.get('scope'))
        self.revision = model.grant_revision(grant)
        # Only extensions offered by /grant-permissions are shown and may be sent.
        self.extensions = list(extensions)
        self.notices = list(notices)
        self.initial = model.grant_flags(self.scope, [e[0] for e in self.extensions])

    @property
    def all_flags(self):
        return model.FLAGS + tuple(e[0] for e in self.extensions)

    def compose(self) -> ComposeResult:
        name = self.grant.get('name') or self.grant.get('id')
        with Dialog(classes='dialog'):
            yield Static('修改 MCP 连接权限', classes='dialog-title')
            with VerticalScroll(classes='dialog-flex form-scroll'):
                yield Static(f'连接名称：{esc(model.one_line(name, 40))}\n'
                             f'授权范围：{esc(model.scope_range(self.scope))}（名称、账号、日期和群/好友范围不变）',
                             id='edit-summary')
                yield Static('保存保留原凭证；该连接已运行的会话会停止，需重新开始。', classes='warn', id='edit-keep-hint')
                for notice in self.notices:
                    yield Static(esc(notice), classes='warn grant-notice')
                yield Static('权限（灰色项需先开启前置权限）', classes='card-title')
                for flag, label, parent in model.PERMISSIONS:
                    indent = '    ' if parent and parent != 'send' else '  ' if parent else ''
                    yield Checkbox(f'{indent}{label}', self.initial[flag], id=f'perm-{flag}')
                if self.extensions:
                    yield Static('SL 扩展权限（实时语音/视频/文件需先开启发送和持续聊天）', classes='card-title')
                    for flag, label, parent, scope in self.extensions:
                        if scope == 'account':
                            continue
                        yield Checkbox(f'{"    " if parent else ""}{esc(label)}', self.initial[flag], id=f'perm-{flag}')
                    account = [e for e in self.extensions if e[3] == 'account']
                    if account:
                        yield Static('账号级权限：作用于整个当前 QQ 账号，不局限于此连接的群或好友，'
                                     '包括范围以外的联系人。只在确实需要时逐项开启。',
                                     classes='warn', id='account-scope-hint')
                        for flag, label, _parent, _scope in account:
                            yield Checkbox(f'账号级 · {esc(label)}', self.initial[flag], id=f'perm-{flag}')
                yield Static('新开启的权限可能需要 SL 在线核对同一账号；只关闭权限时 SL 离线也可保存。', classes='hint')
            yield Static('', classes='error-text', id='grant-error')
            with Horizontal(classes='buttons'):
                yield Button('保存 (ctrl+s)', id='save', variant='primary')
                yield Button('取消 (Esc)', id='cancel')

    def on_mount(self):
        self.sync_flags()
        self.query_one('#perm-media').focus()

    def flags(self):
        return {flag: self.query_one(f'#perm-{flag}', Checkbox).value for flag in self.all_flags}

    def sync_flags(self):
        """Same dependency rules as the create form: a switch without its prerequisite is cleared."""
        clean = model.normalize_flags(self.flags(), [e[0] for e in self.extensions])
        for flag in self.all_flags:
            box = self.query_one(f'#perm-{flag}', Checkbox)
            if box.value != clean[flag]:
                box.value = clean[flag]
            usable = model.allowed(flag, clean)
            box.disabled = not usable
            box.set_class(not usable, '-blocked')

    def on_checkbox_changed(self, event):
        self.sync_flags()

    def on_button_pressed(self, event):
        if event.button.id == 'save':
            self.action_submit()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)

    def action_submit(self):
        try:
            body = model.grant_update_body(self.scope, self.flags(), self.revision,
                                           extra=[e[0] for e in self.extensions])
        except ValueError as exc:
            self.query_one('#grant-error', Static).update(esc(exc))
            return
        self.dismiss(body if body else 'unchanged')


class MCPSettings(ModalScreen):
    BINDINGS = [Binding('escape', 'cancel', '取消'), Binding('ctrl+s', 'submit', '保存')]

    def __init__(self, enabled, port):
        super().__init__()
        self.enabled, self.port = bool(enabled), port

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog'):
            yield Static('MCP 服务设置', classes='dialog-title')
            with VerticalScroll(classes='dialog-flex'):
                yield Checkbox('启用 MCP 服务（供外部 Agent 连接）', self.enabled, id='mcp-enabled')
                with Horizontal(classes='row'):
                    yield Label('端口', classes='label')
                    yield Input(str(self.port or 18777), id='mcp-port', restrict=r'[0-9]*', max_length=5)
                yield Static('只修改服务开关和端口，不会创建任何连接授权。', classes='hint')
            yield Static('', classes='error-text', id='mcp-error')
            with Horizontal(classes='buttons'):
                yield Button('保存 (ctrl+s)', id='save', variant='primary')
                yield Button('取消 (Esc)', id='cancel')

    def on_mount(self):
        self.query_one('#mcp-enabled').focus()

    def on_input_submitted(self, event):
        event.stop()
        self.action_submit()

    def on_button_pressed(self, event):
        if event.button.id == 'save':
            self.action_submit()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)

    def action_submit(self):
        try:
            port = model.parse_port(self.query_one('#mcp-port', Input).value)
        except ValueError as exc:
            self.query_one('#mcp-error', Static).update(esc(exc))
            return
        self.dismiss(dict(enabled=self.query_one('#mcp-enabled', Checkbox).value, port=port))


class GrantsView(View):
    title = 'MCP 授权'
    subtitle = '外部 Agent 的连接凭据与权限；凭据只在创建时显示一次'
    BINDINGS = [Binding('n', 'create', '新建'), Binding('e', 'edit', '修改权限'), Binding('d', 'revoke', '撤销'),
                Binding('m', 'mcp', 'MCP 设置')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static('', classes='hint', id='mcp-line')
        yield make_table('名称', '状态', '范围与权限', '创建时间', '调用', id='grant-table')
        yield Static('n 新建连接 · e 修改所选权限（保留凭证） · d 撤销所选 · m 启用/端口 · '
                     '创建后由你在外部 Agent 中配置并运行', classes='hint')

    def on_mount(self):
        self.grants = {}
        self.mcp = {}

    def refresh_data(self):
        def fetch(client):
            return client.get('/grants'), client.get('/overview')
        self.start_load(fetch, self.show, '正在读取连接…')

    def show(self, result):
        grants, overview = result
        if isinstance(overview, dict):
            self.app.update_overview(overview)
            self.mcp = overview.get('mcp') or {}
        self.show_mcp()
        rows = items(grants)
        self.grants = {str(g.get('id')): g for g in rows}
        fill_table(self.query_one('#grant-table'), [
            (g.get('id'), (model.one_line(g.get('name'), 20), model.grant_status(g),
                           model.one_line(model.scope_summary(g.get('scope')), 48),
                           model.fmt_time(g.get('created_at')), str(g.get('calls', '—')))) for g in rows])
        if not rows:
            self.set_state('empty', '还没有 MCP 连接。按 n 选择群和权限后创建。')

    def show_mcp(self):
        mcp = self.mcp
        state = '[#22c55e]运行中[/]' if mcp.get('running') else '[#7f8a9a]未运行[/]'
        enabled = '已启用' if mcp.get('enabled') else '未启用'
        text = f'MCP 服务：{state} · {enabled} · 端口 {esc(mcp.get("port") or "—")} · {esc(mcp.get("url") or "")}'
        if mcp.get('error'):
            text += f'\n[#ef4444]{esc(model.one_line(mcp["error"], 80))}[/]'
        self.query_one('#mcp-line', Static).update(text)

    def selected(self):
        return self.grants.get(selected_key(self.query_one('#grant-table')) or '')

    def action_create(self):
        self.set_state('loading', '正在从当前 SL 账号读取群、好友和可用权限…')

        def fetch(client):
            # /groups stays required; the newer endpoints are optional so an older
            # backend still offers the original group-only form.
            return client.get('/groups'), optional_get(client, '/friends'), optional_get(client, '/grant-permissions')

        def ok(result):
            groups_result, (friends_result, friends_error), (perms_result, perms_error) = result
            groups = model.valid_targets(items(groups_result), 'group')
            friends = model.valid_targets(items(friends_result), 'direct')
            if not groups and not friends:
                self.set_state('error', '当前 SL 账号没有可选的群或好友，无法创建连接。请检查 SL 连接后重试。')
                return
            notices = []
            if friends_error:
                notices.append(f'未能读取好友列表，本次只能选择群：{friends_error}')
            extensions = model.extension_permissions(perms_result) if not perms_error else []
            if perms_error:
                notices.append(f'未能读取 SL 扩展权限，本次只提供原有权限：{perms_error}')
            self.set_state('ok')
            self.app.push_screen(GrantForm(groups, friends, extensions, notices), self.submit)

        def failed(message):
            self.set_state('error', f'无法读取群列表：{message}')
        self.app.request(fetch, ok, failed)

    def submit(self, body):
        if not body:
            return
        name = body['name']
        self.set_state('loading', f'正在创建连接「{name}」…')

        def ok(result):
            self.set_state('ok')
            self.app.show_status(f'已创建连接：{name}')  # Never include the credential here.
            self.app.push_screen(CredentialModal(name, result))
            self.refresh_data()

        def failed(message):
            self.set_state('error', f'创建失败：{message}')
        self.app.request(lambda c: c.post('/grants', body), ok, failed)

    def action_edit(self):
        grant = self.selected()
        if not grant:
            self.set_state('empty', '请先选择一个连接。')
            return
        if grant.get('revoked'):
            self.set_state('info', '该连接已撤销，不能修改权限。')
            return
        self.set_state('loading', '正在读取可用权限…')

        def ok(result):
            perms_result, perms_error = result
            notices = []
            extensions = model.extension_permissions(perms_result) if not perms_error else []
            if perms_error:
                notices.append(f'未能读取 SL 扩展权限，本次只修改原有权限，已有扩展权限保持不变：{perms_error}')
            self.set_state('ok')
            self.app.push_screen(GrantEditForm(grant, extensions, notices), lambda body: self.submit_edit(grant, body))
        # The conversation range does not change, so /groups and /friends are not needed.
        self.app.request(lambda c: optional_get(c, '/grant-permissions'), ok,
                         lambda message: self.set_state('error', f'无法读取权限：{message}'))

    def submit_edit(self, grant, body):
        if body is None:
            return
        name = grant.get('name') or grant.get('id')
        if body == 'unchanged':
            self.set_state('info', f'「{name}」的权限没有改动，未保存。')
            return
        gid = grant.get('id')
        self.set_state('loading', f'正在保存「{name}」的权限…')

        def put(client):
            try:
                return client.put(f'/grants/{seg(gid)}', body)
            except Exception as exc:  # noqa: BLE001 - only the status decides; text stays safe
                status = getattr(exc, 'status_code', None)
                if status in (404, 405, 409):
                    return dict(_status=status)
                raise

        def ok(result):
            status = result.get('_status') if isinstance(result, dict) else None
            if status == 409:
                self.reload_with('error', f'「{name}」已在别处被修改，本次未保存。列表已刷新，请重新按 e 修改。')
                return
            if status in (404, 405):
                self.set_state('error', '当前 Tulpa 后台不支持修改连接权限（或该连接已不存在），本次未保存。'
                                        '请升级 Tulpa 后刷新重试；不要为此撤销或新建连接。')
                return
            if not isinstance(result, dict) or not result.get('ok'):
                self.set_state('error', '保存失败：后台未确认修改。请刷新后查看。')
                return
            if not result.get('changed'):
                text = f'「{name}」的权限没有变化。'
            else:
                stopped = result.get('stopped_sessions') or 0
                text = f'已修改「{name}」的权限，原凭证继续有效' + (f'；已停止 {stopped} 个会话，需重新开始。' if stopped else '。')
            note = model.one_line(result.get('note'), 80)
            self.app.show_status(f'已修改连接权限：{name}')  # Never include the credential here.
            self.app.ping()
            self.reload_with('info', text + (f' {note}' if note else ''))

        def failed(message):
            self.set_state('error', f'保存失败：{message}')
        self.app.request(put, ok, failed)

    def action_revoke(self):
        grant = self.selected()
        if not grant:
            self.set_state('empty', '请先选择一个连接。')
            return
        if grant.get('revoked'):
            self.set_state('info', '该连接已撤销。')
            return
        name = grant.get('name') or grant.get('id')

        def confirmed(yes):
            if not yes:
                return
            self.set_state('loading', f'正在撤销「{name}」…')

            def ok(result):
                self.app.show_status(f'已撤销连接：{name}')
                self.reload_with('info', f'已撤销「{name}」。')

            def failed(message):
                self.set_state('error', f'撤销失败：{message}')
            gid = grant.get('id')
            self.app.request(lambda c: c.post(f'/grants/{seg(gid)}/revoke', {}), ok, failed)
        self.app.push_screen(ConfirmModal('撤销 MCP 连接', f'确定撤销连接「{name}」吗？\n使用此凭据的外部 Agent 将立即失去访问权限，此操作不可恢复。',
                                          '撤销', danger=True), confirmed)

    def action_mcp(self):
        def done(body):
            if not body:
                return
            self.set_state('loading', '正在保存 MCP 设置…')

            def ok(result):
                if isinstance(result, dict):
                    self.mcp = result
                self.show_mcp()
                self.set_state('info', 'MCP 设置已保存。')
                self.app.show_status('MCP 设置已保存')
                self.app.ping()

            def failed(message):
                self.set_state('error', f'保存失败：{message}')
            self.app.request(lambda c: c.put('/mcp', body), ok, failed)
        self.app.push_screen(MCPSettings(self.mcp.get('enabled'), self.mcp.get('port')), done)
