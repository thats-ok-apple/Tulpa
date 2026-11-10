"""Reply draft review and group-admin approvals, both opened from the chat browser.

Reply flow follows chatlocal.replies exactly: create/reopen draft -> generate
(explicit, calls the model) -> edit with revision -> preview (native token) ->
separate confirmation of the exact text and destination -> send. Nothing is sent
without that last confirmation; a SENT/UNKNOWN draft is never resent automatically.
"""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from . import model
from .base import esc, seg
from .forms import by_value, ChoiceModal, Field, FormModal
from .modals import ConfirmModal, Dialog, TextModal

STATUS = dict(DRAFT='草稿', USER_APPROVED='已批准，等待发送', SENDING='发送中', SENT='已发送',
              UNKNOWN='结果未知（请到 QQ 核对，不会自动重发）', REJECTED='已放弃')
POLICY = (('ask', '逐项审批'), ('allow', '自动允许'), ('deny', '全部拒绝'))


def destination_text(dest):
    if not isinstance(dest, dict):
        return '—'
    kind = '群' if dest.get('kind') == 'group' else '私聊' if dest.get('kind') == 'direct' else str(dest.get('kind') or '')
    parts = [f'账号 {dest.get("account") or "—"}', f'{kind} {dest.get("name") or ""} ({dest.get("peer") or "—"})']
    if dest.get('quote_id') is not None:
        parts.append('引用原消息')
    return ' · '.join(parts)


class ReplyModal(ModalScreen):
    """One reply draft. All requests go through the app's worker; results replace the draft."""

    BINDINGS = [Binding('escape', 'close', '关闭'), Binding('ctrl+g', 'generate', '生成'),
                Binding('ctrl+s', 'save', '保存'), Binding('ctrl+p', 'preview', '预览发送')]

    def __init__(self, draft):
        super().__init__()
        self.draft = draft if isinstance(draft, dict) else {}
        self.polling = False

    @property
    def sid(self):
        return self.draft.get('id')

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog'):
            yield Static('回复草稿', classes='dialog-title')
            with VerticalScroll(classes='dialog-flex'):
                yield Static('', id='reply-target')
                yield Static('', id='reply-status')
                yield Static('最终回复（可编辑，保存后才能预览）', classes='card-title')
                yield TextArea('', id='reply-text', soft_wrap=True)
                yield Static('', classes='hint', id='reply-runs')
            yield Static('', classes='error-text', id='reply-error')
            with Horizontal(classes='buttons'):
                yield Button('生成 (ctrl+g)', id='generate')
                yield Button('停止', id='stop')
                yield Button('保存 (ctrl+s)', id='save')
                yield Button('预览发送 (ctrl+p)', id='preview', variant='primary')
                yield Button('放弃', id='reject', variant='error')
                yield Button('关闭 (Esc)', id='close')

    def on_mount(self):
        self.query_one('#reply-text').styles.height = 7
        self.render_draft(reset_text=True)
        self.query_one('#reply-text').focus()

    # ---- rendering ---------------------------------------------------------

    def render_draft(self, reset_text=False):
        d = self.draft
        target = d.get('target') if isinstance(d.get('target'), dict) else {}
        sender = d.get('sender') if isinstance(d.get('sender'), dict) else {}
        self.query_one('#reply-target', Static).update(
            f'[#7f8a9a]回复给[/] {esc(target.get("sender") or "—")} · {esc(target.get("conversation") or "")}'
            f' · {esc(model.fmt_time(target.get("timestamp")))}\n{esc(model.one_line(target.get("content"), 300))}')
        status = str(d.get('status') or '')
        lines = [f'[#7f8a9a]状态[/] {esc(STATUS.get(status, status))} · 版本 {esc(d.get("revision", "—"))}'
                 + (' · [#eab308]生成中…[/]' if d.get('active_run') else '')]
        if not sender.get('available'):
            lines.append(f'[#eab308]{esc(sender.get("note") or "当前不能直接发送，可编辑后复制。")}[/]')
        if d.get('error'):
            lines.append(f'[#ef4444]{esc(d["error"])}[/]')
        receipt = d.get('receipt') if isinstance(d.get('receipt'), dict) else {}
        if status == 'SENT' and receipt:
            lines.append('[#22c55e]发送回执[/] ' + esc(model.one_line(', '.join(f'{k}={v}' for k, v in receipt.items()), 120)))
        self.query_one('#reply-status', Static).update('\n'.join(lines))
        area = self.query_one('#reply-text', TextArea)
        if reset_text or not self.dirty():
            area.load_text(str(d.get('final_text') or ''))
        editable = status == 'DRAFT' and not d.get('active_run')
        area.read_only = not editable
        runs = d.get('generations') if isinstance(d.get('generations'), list) else []
        if runs:
            run = runs[0]
            events = [model.event_line(e) for e in run.get('events') or []][-4:]
            text = f'最近生成：{run.get("status")}' + (f' · 要求：{model.one_line(run.get("instruction"), 60)}' if run.get('instruction') else '')
            if events:
                text += '\n' + '\n'.join(e for e in events if e)
            self.query_one('#reply-runs', Static).update(esc(text))
        self.query_one('#generate').disabled = not editable
        self.query_one('#stop').disabled = not d.get('active_run')
        self.query_one('#save').disabled = not editable
        self.query_one('#preview').disabled = not (editable and d.get('ai_text') and sender.get('available'))
        self.query_one('#reject').disabled = status != 'DRAFT'

    def dirty(self):
        try:
            return self.query_one('#reply-text', TextArea).text != str(self.draft.get('final_text') or '')
        except Exception:  # noqa: BLE001 - before compose
            return False

    def error(self, text=''):
        self.query_one('#reply-error', Static).update(esc(text))

    # ---- requests ------------------------------------------------------------

    def call(self, fn, ok=None, reset_text=False):
        self.error('')

        def done(result):
            if isinstance(result, dict) and result.get('id') == self.sid:
                self.draft = result
                self.render_draft(reset_text)
            if ok:
                ok(result)
            self.watch_run()

        self.app.request(fn, done, lambda message: self.error(message))

    def watch_run(self):
        if not self.draft.get('active_run') or self.polling or not self.is_mounted:
            return
        self.polling = True
        generation = self.app.generation

        def again():
            self.polling = False
            if self.is_mounted and self.app.generation == generation:
                self.call(lambda c: c.api_get(f'/api/replies/{seg(self.sid)}'))
        self.set_timer(1.5, again)

    def on_button_pressed(self, event):
        event.stop()
        action = event.button.id
        getattr(self, f'action_{action}')()

    def action_close(self):
        self.dismiss(self.draft)

    def action_generate(self):
        if self.dirty():
            self.error('编辑尚未保存；先保存（ctrl+s），生成会以已保存草稿为基础。')
            return

        def done(body):
            if body:
                self.call(lambda c: c.api_post(f'/api/replies/{seg(self.sid)}/generate', body))
        self.app.push_screen(FormModal('生成回复草稿', [
            Field('instruction', '额外要求', 'area', '', height=4, hint='可留空；最多 1000 字。'),
            Field('profile', '分析强度', 'select', 'standard', by_value(model.PROFILES)),
            Field('vision', '图片识别', 'select', 'ocr', by_value(model.VISION)),
        ], self.generate_body, '生成', lead='提交后会调用已配置的模型读取上下文并生成草稿；不会发送。'), done)

    @staticmethod
    def generate_body(v):
        if len(v['instruction']) > 1000:
            raise ValueError('额外要求最多 1000 字。')
        return dict(instruction=v['instruction'].strip(), profile=v['profile'] or 'standard', vision=v['vision'] or 'ocr')

    def action_stop(self):
        self.app.request(lambda c: c.api_post(f'/api/replies/{seg(self.sid)}/stop', {}),
                         lambda r: self.error('' if (r or {}).get('stopping') else '没有正在运行的生成。'),
                         lambda m: self.error(m))

    def action_save(self):
        text = self.query_one('#reply-text', TextArea).text
        if len(text) > 2000:
            self.error('回复最多 2000 字。')
            return
        revision = self.draft.get('revision')
        self.call(lambda c: c.api_patch(f'/api/replies/{seg(self.sid)}', dict(text=text, revision=revision)),
                  lambda r: self.app.show_status('草稿已保存'), reset_text=True)

    def action_reject(self):
        revision = self.draft.get('revision')

        def confirmed(yes):
            if yes:
                self.call(lambda c: c.api_post(f'/api/replies/{seg(self.sid)}/reject', dict(revision=revision)))
        self.app.push_screen(ConfirmModal('放弃草稿', '放弃这条回复草稿？正在进行的生成会被取消，之后可重新新建。',
                                          '放弃', danger=True), confirmed)

    def action_preview(self):
        if self.dirty():
            self.error('编辑尚未保存；预览只针对已保存的版本。')
            return
        if not self.draft.get('ai_text'):
            self.error('请先生成草稿，再编辑并预览。')
            return
        revision = self.draft.get('revision')

        def choose(quote):
            if quote is None:
                return
            self.app.request(lambda c: c.api_post(f'/api/replies/{seg(self.sid)}/preview',
                                                  dict(revision=revision, quote=quote)),
                             self.confirm_send, lambda m: self.error(m))
        self.app.push_screen(ChoiceModal('发送方式', [('普通发送', False), ('引用原消息发送', True)]), choose)

    def confirm_send(self, preview):
        preview = preview if isinstance(preview, dict) else {}
        token = preview.get('token')
        if not token or preview.get('revision') != self.draft.get('revision'):
            self.error('预览结果与当前版本不一致，未发送；请重新预览。')
            return
        body = (f'目标：{destination_text(preview.get("destination"))}\n'
                f'有效期：{preview.get("expires_in", 300)} 秒\n\n将发送以下准确文字：\n\n{preview.get("text", "")}')

        def confirmed(yes):
            if not yes:
                self.error('已取消发送；预览令牌未使用。')
                return
            self.call(lambda c: c.api_post(f'/api/replies/{seg(self.sid)}/send', dict(token=token, confirm=True)),
                      lambda r: self.app.show_status('发送请求已处理，请核对状态'), reset_text=True)
        self.app.push_screen(ConfirmModal('确认发送', body, '发送这段文字', danger=True), confirmed)


class GroupAdminModal(ModalScreen):
    """Approve or deny group-management operations proposed in one Agent dialogue."""

    BINDINGS = [Binding('escape', 'close', '关闭'), Binding('y', 'allow', '允许'), Binding('n', 'deny', '拒绝'),
                Binding('p', 'policy', '审批模式'), Binding('r', 'reload', '刷新')]

    def __init__(self, session_id, title=''):
        super().__init__()
        self.session_id, self.title_text = session_id, title
        self.data, self.index = {}, 0

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog'):
            yield Static(f'群管理审批 · {esc(self.title_text or self.session_id)}', classes='dialog-title')
            with VerticalScroll(classes='dialog-flex'):
                yield Static('', id='ga-policy')
                yield Static('', id='ga-ops')
            yield Static('', classes='error-text', id='ga-error')
            with Horizontal(classes='buttons'):
                yield Button('上一项', id='prev')
                yield Button('下一项', id='next')
                yield Button('允许 (y)', id='allow', variant='primary')
                yield Button('拒绝 (n)', id='deny', variant='error')
                yield Button('审批模式 (p)', id='policy')
                yield Button('关闭 (Esc)', id='close')

    def on_mount(self):
        self.action_reload()

    def action_reload(self):
        self.app.request(lambda c: c.api_get('/api/group-admin', session_id=self.session_id), self.show,
                         lambda m: self.error(m))

    def error(self, text=''):
        self.query_one('#ga-error', Static).update(esc(text))

    def ops(self):
        value = self.data.get('operations')
        return value if isinstance(value, list) else []

    def show(self, data):
        self.data = data if isinstance(data, dict) else {}
        policy = self.data.get('policy') if isinstance(self.data.get('policy'), dict) else {}
        self.query_one('#ga-policy', Static).update(
            f'QQ 群管理接口：{"可用" if self.data.get("available") else "[#eab308]不可用[/]"} · '
            f'审批模式：{esc(dict(POLICY).get(policy.get("mode"), policy.get("mode") or "—"))}')
        ops = self.ops()
        self.index = min(self.index, max(0, len(ops) - 1))
        if not ops:
            self.query_one('#ga-ops', Static).update('[#7f8a9a]此对话没有群管理操作。[/]')
        else:
            lines = []
            for i, op in enumerate(ops):
                mark = '▶ ' if i == self.index else '  '
                lines.append(f'{mark}[b]{esc(op.get("action") or "")}[/] {esc(op.get("status") or "")} · '
                             f'{esc(model.one_line(op.get("summary"), 80))}')
            op = ops[self.index]
            target = op.get('target') if isinstance(op.get('target'), dict) else {}
            lines.append('\n[#a78bfa]所选操作[/]\n' + esc(op.get('summary') or '') + '\n' +
                         esc('目标：' + model.one_line(', '.join(f'{k}={v}' for k, v in target.items()), 200)) +
                         f'\n到期：{esc(model.fmt_time(op.get("expires")))}' +
                         (f'\n结果：{esc(op.get("detail"))}' if op.get('detail') else ''))
            self.query_one('#ga-ops', Static).update('\n'.join(lines))
        pending = bool(ops) and ops[self.index].get('status') == 'PENDING'
        self.query_one('#allow').disabled = self.query_one('#deny').disabled = not pending

    def on_button_pressed(self, event):
        event.stop()
        key = event.button.id
        if key in ('prev', 'next'):
            self.index = max(0, min(len(self.ops()) - 1, self.index + (1 if key == 'next' else -1)))
            self.show(self.data)
        else:
            getattr(self, f'action_{key}')()

    def action_close(self):
        self.dismiss(None)

    def decide(self, allow):
        ops = self.ops()
        if not ops or ops[self.index].get('status') != 'PENDING':
            self.error('所选操作不是待审批状态。')
            return
        op = ops[self.index]
        oid, token = op.get('id'), op.get('approval_token')

        def confirmed(yes):
            if yes:
                self.app.request(lambda c: c.api_post(f'/api/group-admin/{seg(oid)}/decision',
                                                      dict(session_id=self.session_id, allow=allow, token=token)),
                                 lambda r: self.action_reload(), lambda m: self.error(m))
        verb = '允许执行' if allow else '拒绝'
        self.app.push_screen(ConfirmModal(f'{verb}群管理操作', f'{verb}以下操作？\n\n{op.get("summary") or op.get("action")}',
                                          verb, danger=allow), confirmed)

    def action_allow(self):
        self.decide(True)

    def action_deny(self):
        self.decide(False)

    def action_policy(self):
        def chosen(mode):
            if not mode:
                return

            def confirmed(yes):
                if yes:
                    self.app.request(lambda c: c.api_put('/api/group-admin/policy',
                                                         dict(session_id=self.session_id, mode=mode)),
                                     lambda r: self.action_reload(), lambda m: self.error(m))
            if mode == 'allow':
                self.app.push_screen(ConfirmModal('自动允许群管理', '此对话之后提出的群管理操作将不再逐项询问而直接执行。确定吗？',
                                                  '自动允许', danger=True), confirmed)
            else:
                confirmed(True)
        self.app.push_screen(ChoiceModal('此对话的群管理审批模式', list((label, key) for key, label in POLICY)), chosen)


def show_receipts(app, result):
    rows = (result or {}).get('receipts') if isinstance(result, dict) else []
    text = '\n\n'.join(f'{model.fmt_time(r.get("timestamp"))}\n{r.get("content") or ""}' for r in rows or [])
    app.push_screen(TextModal('本工具发送回执', text or '此会话还没有通过本工具发送的回复。',
                              esc((result or {}).get('note') or '')))
