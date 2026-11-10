"""Reusable modal dialogs: confirm, JSON detail and one-time credential."""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from .base import esc

MARGIN = 2  # Rows kept free above and below every dialog.


class Dialog(Vertical):
    """Dialog shell whose scrolling body shrinks so the action area stays on screen.

    An auto-height dialog resolves its children first, so a long body grows past
    the dialog's max-height and pushes the error line and buttons outside the
    modal (invisible and unreachable). Here the dialog height is set explicitly
    to what the screen allows, the body gets the remaining rows as `1fr`, and the
    error/button area is always inside the border, also after a resize.
    """

    _fitted = None

    def body(self):
        """The one child marked `.dialog-flex`: it scrolls and absorbs spare rows."""
        found = self.query('.dialog-flex')
        return found.first() if found else None

    def on_mount(self):
        self.fit_height()
        self.call_after_refresh(self.fit_height)

    def on_resize(self):
        self.call_after_refresh(self.fit_height)

    def fit_height(self):
        body = self.body()
        if body is None or self.screen is None:
            return
        available = max(6, self.screen.size.height - MARGIN)
        frame = max(0, self.outer_size.height - self.container_size.height)  # border + padding
        chrome = sum(w.outer_size.height + w.styles.margin.height
                     for w in self.children if w is not body)
        wanted = frame + chrome + max(3, body.virtual_size.height + body.styles.margin.height)
        # Before the first refresh the frame is not measurable yet; take all we may use.
        height = min(available, wanted) if frame else available
        if height != self._fitted:
            self._fitted = height
            self.styles.height = height


class ConfirmModal(ModalScreen):
    """Returns True only on explicit confirmation; Esc / n cancel."""

    BINDINGS = [Binding('escape,n', 'cancel', '取消'), Binding('y', 'confirm', '确认')]

    def __init__(self, title, body, confirm='确认', danger=False):
        super().__init__()
        self.title_text, self.body_text, self.confirm_text, self.danger = title, body, confirm, danger

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog' + (' -danger' if self.danger else '')):
            yield Static(esc(self.title_text), classes='dialog-title')
            with VerticalScroll(classes='dialog-flex'):
                yield Static(esc(self.body_text), classes='dialog-body')
            with Horizontal(classes='buttons'):
                yield Button(f'{self.confirm_text} (y)', id='confirm', variant='error' if self.danger else 'primary')
                yield Button('取消 (n)', id='cancel')

    def on_mount(self):
        self.query_one('#cancel').focus()  # Safe default for destructive actions.

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == 'confirm')

    def action_cancel(self):
        self.dismiss(False)

    def action_confirm(self):
        self.dismiss(True)


class TextModal(ModalScreen):
    """Read-only text such as persona content or learning JSON."""

    BINDINGS = [Binding('escape,q', 'close', '关闭')]

    def __init__(self, title, text, lead=''):
        super().__init__()
        self.title_text, self.text, self.lead = title, text, lead

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog'):
            yield Static(esc(self.title_text), classes='dialog-title')
            if self.lead:
                yield Static(self.lead, classes='hint')
            # The TextArea scrolls itself, so it is the flexible body.
            yield TextArea(self.text, read_only=True, id='text', soft_wrap=True, classes='dialog-flex')
            with Horizontal(classes='buttons'):
                yield Button('关闭 (Esc)', id='close', variant='primary')

    def on_mount(self):
        self.query_one('#text').focus()

    def on_button_pressed(self, event):
        self.dismiss(None)

    def action_close(self):
        self.dismiss(None)


class CredentialModal(ModalScreen):
    """Shows a new grant credential exactly once. Nothing here reaches logs or notifications."""

    BINDINGS = [Binding('escape', 'close', '我已保存')]

    def __init__(self, name, result):
        super().__init__()
        self.name_text = name
        self.result = result if isinstance(result, dict) else {}

    def compose(self) -> ComposeResult:
        token = str(self.result.get('token') or '')
        url = str(self.result.get('mcp_url') or '')
        config = self.result.get('client_config')
        if isinstance(config, (dict, list)):
            import json
            config = json.dumps(config, ensure_ascii=False, indent=2)
        with Dialog(classes='dialog -secret'):
            yield Static(f'连接已创建：{esc(self.name_text)}', classes='dialog-title')
            with VerticalScroll(classes='dialog-flex'):
                yield Static('[b #eab308]这是唯一一次显示凭据。[/] 关闭后无法找回，只能撤销后重新创建。'
                             '请只粘贴到你信任的外部 Agent 配置中。', classes='warn')
                yield Static('MCP 地址', classes='card-title')
                yield Static(esc(url or '—'), classes='secret-box', id='mcp-url')
                yield Static('连接凭据（Bearer）', classes='card-title')
                yield TextArea(token, read_only=True, id='token', soft_wrap=True)
                if config:
                    yield Static('客户端配置', classes='card-title')
                    yield TextArea(str(config), read_only=True, id='config', soft_wrap=True)
                yield Static('在文本框中可用鼠标或 ctrl+a / ctrl+c 复制（取决于终端）。', classes='hint')
            with Horizontal(classes='buttons'):
                yield Button('我已保存，关闭', id='close', variant='primary')

    def on_mount(self):
        self.query_one('#token').styles.height = 3
        if self.query('#config'):
            self.query_one('#config').styles.height = 8
        self.query_one('#token').focus()

    def on_button_pressed(self, event):
        self.dismiss(None)

    def action_close(self):
        self.dismiss(None)
