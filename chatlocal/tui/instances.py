"""实例管理: list, create, rename, start, stop and switch independent Tulpa instances.

Every request on this page goes to the original hub backend (app.hub_request), never
to the selected instance, so it keeps working while a selected child is offline. The
page itself is never rebuilt on a switch; every other page is.
"""
import re

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from .base import View, fill_table, items, make_table, seg, selected_key
from .forms import Field, FormModal, int_in, required
from .modals import ConfirmModal

DEFAULT_ID = 'default'
DEFAULT_NAME = '默认实例'
NAME_LIMIT = 60  # Backend limit for instance names.
ID_RE = re.compile(r'[a-z][a-z0-9-]{0,31}')
STATE = dict(running=('运行中', '#22c55e'), stopped=('已停止', '#7f8a9a'), error=('错误', '#ef4444'))
HINT = ('n 新建  e 改名  s 启动  x 停止  w / Enter 切换  r 刷新\n'
        '默认实例（主后台）不能在此停止。新实例不复制凭据、配置或聊天记录；'
        '其 MCP 默认关闭，需切换到该实例后自行开启并创建授权。')


def display_name(descriptor):
    descriptor = descriptor if isinstance(descriptor, dict) else {}
    iid = str(descriptor.get('id') or '')
    return str(descriptor.get('name') or (DEFAULT_NAME if iid in ('', DEFAULT_ID) else iid))


def is_default(descriptor):
    return str(descriptor.get('id')) == DEFAULT_ID or bool(descriptor.get('default'))


def optional_port(value, label):
    value = (value or '').strip()
    return None if not value else int_in(value, label, 1024, 65535)


def create_body(values):
    iid = (values.get('id') or '').strip()
    if not ID_RE.fullmatch(iid):
        raise ValueError('实例 ID 须以小写字母开头，只含小写字母、数字和 -，最多 32 位。')
    if iid == DEFAULT_ID:
        raise ValueError('default 是默认实例的保留 ID。')
    body = dict(id=iid, name=required(values.get('name'), '名称', NAME_LIMIT))
    for key, label in (('management_port', '管理端口'), ('mcp_port', 'MCP 端口')):
        port = optional_port(values.get(key), label)
        if port is not None:
            body[key] = port
    if body.get('management_port') and body.get('management_port') == body.get('mcp_port'):
        raise ValueError('管理端口与 MCP 端口不能相同。')
    return body


class InstancesView(View):
    title = '实例管理'
    subtitle = '一个界面管理多个独立的 QQ / SL / Tulpa 实例（F2）'
    BINDINGS = [Binding('n', 'create', '新建'), Binding('e', 'rename', '改名'), Binding('s', 'start', '启动'),
                Binding('x', 'stop', '停止'), Binding('w', 'switch', '切换')]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.descriptors = {}

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield make_table('当前', '名称', 'ID', '状态', '管理端口', 'MCP 端口', '说明', id='inst-table')
        yield Static(HINT, classes='hint', id='inst-hint')

    # ---- loading (hub only) ---------------------------------------------

    def refresh_data(self):
        self.busy = True
        self.set_state('loading', '正在读取实例列表…')
        self.app.hub_request(lambda c: c.get('/instances'), self.show, self.load_failed)

    def load_failed(self, message):
        self.busy = False
        self.loaded = False
        if self.is_mounted:
            self.set_state('error', f'主后台未响应：{message}  按 r 重试')

    def show(self, result):
        self.busy = False
        self.loaded = True
        if not self.is_mounted:
            return
        listed = [d for d in items(result) if isinstance(d, dict) and d.get('id')]
        self.descriptors = {str(d['id']): d for d in listed}
        self.app.instances_listed(self.descriptors)
        self.render_rows()
        self.set_state('ok') if listed else self.set_state('empty', '主后台未返回任何实例。')
        message, self.after_load = self.after_load, None
        if message:
            self.set_state(*message)

    def merge(self, descriptor):
        """Apply one returned descriptor without waiting for a full reload."""
        if isinstance(descriptor, dict) and descriptor.get('id'):
            self.descriptors[str(descriptor['id'])] = descriptor
            self.app.instances_listed({str(descriptor['id']): descriptor})
            self.render_rows()

    def render_rows(self):
        rows = []
        for iid, d in self.descriptors.items():
            current = iid == self.app.instance_id
            state = d.get('state') or ('running' if d.get('running') else 'stopped')
            label, color = STATE.get(state, (str(state), '#7f8a9a'))
            mcp = d.get('mcp') if isinstance(d.get('mcp'), dict) else {}
            mcp_text = f'{d.get("mcp_port") or "—"}' + ('（运行中）' if mcp.get('running') else '')
            note = d.get('error') or ('主后台 · 不可在此停止' if is_default(d) else '')
            rows.append((iid, [Text('▶ 当前' if current else '', style='bold #fbbf24'),
                               Text(display_name(d), style='bold #fbbf24' if current else ''), iid,
                               Text(label, style=color), str(d.get('management_port') or '—'), mcp_text, note]))
        fill_table(self.query_one('#inst-table'), rows)

    def selected(self):
        key = selected_key(self.query_one('#inst-table'))
        return self.descriptors.get(key) if key is not None else None

    def hub_act(self, fn, ok, text, error, slow=False):
        self.set_state('loading', text)

        def done(result):
            if self.is_mounted:
                self.set_state('ok')
                ok(result)

        def failed(message):
            if self.is_mounted:
                self.set_state('error', f'{error}：{message}')
        self.app.hub_request(fn, done, failed, slow=slow)

    # ---- actions --------------------------------------------------------

    def action_create(self):
        fields = [Field('id', '实例 ID', placeholder='例如 deepseek', hint='小写字母开头，可含数字和 -；创建后不可修改。'),
                  Field('name', '名称', placeholder='例如 大肥鱼'),
                  Field('management_port', '管理端口', placeholder='留空自动分配（7862 起）'),
                  Field('mcp_port', 'MCP 端口', placeholder='留空自动分配（18778 起）'),
                  Field('note', '新实例为干净的停止状态，不复制凭据、配置或历史；MCP 默认关闭。', kind='note')]

        def submit(body):
            if body:
                self.hub_act(lambda c: c.post('/instances', body),
                             lambda d: self.reload_with('info', f'已创建实例「{display_name(d)}」（已停止），按 s 启动。'),
                             '正在创建实例…', '创建失败')
        self.app.push_screen(FormModal('新建实例', fields, create_body, submit='创建'), submit)

    def action_rename(self):
        d = self.selected()
        if d is None:
            return
        iid = str(d['id'])

        def submit(body):
            if body:
                self.hub_act(lambda c: c.put(f'/instances/{seg(iid)}', body),
                             lambda r: (self.merge(r), self.set_state('info', f'已改名为「{display_name(r)}」。')),
                             '正在改名…', '改名失败')
        self.app.push_screen(FormModal(f'实例改名：{iid}', [Field('name', '名称', value=display_name(d))],
                                       lambda v: dict(name=required(v.get('name'), '名称', NAME_LIMIT))), submit)

    def start(self, d, then=None):
        iid, name = str(d['id']), display_name(d)

        def ok(result):
            self.merge(result)
            if then:
                then(result)
            else:
                self.set_state('info', f'实例「{name}」已启动，按 w 切换到该实例。')
        self.hub_act(lambda c: c.post(f'/instances/{seg(iid)}/start', {}), ok,
                     f'正在启动实例「{name}」（最长约 60 秒）…', '启动失败', slow=True)

    def action_start(self):
        d = self.selected()
        if d is None:
            return
        if d.get('running'):
            self.set_state('info', f'实例「{display_name(d)}」已在运行。')
            return
        self.start(d)

    def action_stop(self):
        d = self.selected()
        if d is None:
            return
        name, iid = display_name(d), str(d['id'])
        if is_default(d):
            self.set_state('error', '默认实例（主后台）不能在此停止。')
            return
        if not d.get('running'):
            self.set_state('info', f'实例「{name}」未在运行。')
            return
        body = (f'将停止实例「{name}」（{iid}）的后台服务，以及它托管的 SnowLuma（该 QQ 连接会断开）。'
                '其他实例不受影响。')
        if iid == self.app.instance_id:
            body += '\n\n这是当前选中的实例：停止后其余页面会显示离线，可在此页切回默认实例或重新启动。'

        def confirmed(yes):
            if yes:
                self.hub_act(lambda c: c.post(f'/instances/{seg(iid)}/stop', {}),
                             lambda r: (self.merge(r), self.set_state('info', f'实例「{name}」已停止。')),
                             f'正在停止实例「{name}」…', '停止失败')
        self.app.push_screen(ConfirmModal(f'停止实例「{name}」', body, '停止', danger=True), confirmed)

    def action_switch(self):
        d = self.selected()
        if d is None:
            return
        name = display_name(d)
        if str(d['id']) == self.app.instance_id:
            self.set_state('info', f'当前已是实例「{name}」。')
            return
        if d.get('running') or is_default(d):
            self.switch(d)
            return

        def confirmed(yes):
            if yes:
                self.start(d, then=self.switch_after_start)
        self.app.push_screen(ConfirmModal(f'切换到「{name}」', f'实例「{name}」尚未运行。先启动它（最长约 60 秒），'
                                          '成功后切换到该实例？', '启动并切换'), confirmed)

    def switch_after_start(self, descriptor):
        """Only switch automatically while the user is still here and no dialog is open."""
        name = display_name(descriptor)
        if not descriptor.get('running'):
            self.set_state('error', f'实例「{name}」未能启动：{descriptor.get("error") or "状态未知"}')
        elif self.app.current != 'instances' or len(self.app.screen_stack) > 1:
            self.app.notify(f'实例「{name}」已启动；在实例管理页按 w 切换。', title='实例管理')
        else:
            self.switch(descriptor)

    def switch(self, descriptor):
        if self.app.switch_instance(descriptor):
            self.render_rows()
            self.set_state('info', f'已切换到实例「{display_name(descriptor)}」。其余页面已按新实例重新加载。')

    def on_data_table_row_selected(self, event):
        if event.data_table.id == 'inst-table':
            event.stop()
            self.action_switch()
