"""Shared view plumbing: lazy loading, state lines and table helpers."""
from urllib.parse import quote

from rich.markup import escape
from rich.text import Text
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Static


def seg(value):
    """Escape one path segment from backend ids."""
    return quote(str(value), safe='')


def esc(value):
    return escape(str(value))


def plain(value):
    """Backend text as a literal cell/option value.

    DataTable and OptionList parse plain strings as markup, so a group called
    `[red]x[/]` would otherwise restyle or swallow the cell. Text carries no markup.
    """
    return Text(str('' if value is None else value), no_wrap=True, overflow='ellipsis')


class View(Vertical):
    """One page in the content switcher. Data loads on first activation, never on compose."""

    title = ''
    subtitle = ''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.loaded = False
        self.busy = False  # Not `loading`: that is a reactive on every Textual widget.
        self.after_load = None

    def compose_header(self):
        yield Static(f'[b]{esc(self.title)}[/b]  [dim]{esc(self.subtitle)}[/dim]', classes='view-title')
        yield Static('', classes='state', id=f'{self.id}-state')

    @property
    def state_line(self):
        return self.query_one(f'#{self.id}-state', Static)

    def set_state(self, kind, text=''):
        """kind: loading | error | empty | info | ok (hidden)."""
        line = self.state_line
        for name in ('loading', 'error', 'empty', 'info'):
            line.set_class(name == kind, f'-{name}')
        icons = dict(loading='… ', error='✕ ', empty='○ ', info='• ')
        line.update(icons.get(kind, '') + esc(text) if kind != 'ok' else '')
        line.display = kind != 'ok' and bool(text)

    def activate(self):
        if not self.loaded and not self.busy:
            self.refresh_data()

    def refresh_data(self):
        """Override: start a background request through self.app.request."""

    def reload_with(self, kind, text):
        """Refresh, then show an outcome message that the refresh would otherwise clear."""
        self.refresh_data()
        self.after_load = (kind, text)

    def focus_main(self):
        for widget in self.query('DataTable, Input'):
            if widget.focusable and widget.display:
                widget.focus()
                return

    def start_load(self, fn, done, text='正在加载…'):
        self.busy = True
        self.set_state('loading', text)

        def ok(result):
            self.busy = False
            self.loaded = True
            self.set_state('ok')
            done(result)
            message, self.after_load = self.after_load, None
            if message and not self.state_line.display:  # Empty-state text from done() wins.
                self.set_state(*message)

        def failed(message):
            self.busy = False
            self.loaded = False
            self.set_state('error', f'{message}  按 r 重试')

        self.app.request(fn, ok, failed)

    def act(self, fn, ok=None, text='正在处理…', error='操作失败'):
        """One explicit user action: loading line, then ok(result) or a safe error line."""
        self.set_state('loading', text)

        def done(result):
            self.set_state('ok')
            if ok:
                ok(result)

        def failed(message):
            self.set_state('error', f'{error}：{message}')
        self.app.request(fn, done, failed)

    def poll(self, key, fetch, tick, interval=1.5):
        """Repeat fetch(client) while tick(result) returns True.

        Starting a poll with the same key replaces the previous one, so a view
        never runs two status loops for one job. Timers die with the widget.
        """
        polls = self.__dict__.setdefault('_polls', {})
        token = object()
        polls[key] = token
        generation = self.app.generation  # A poll belongs to the instance it was started on.

        def alive():
            return polls.get(key) is token and self.is_mounted and self.app.generation == generation

        def step():
            if not alive():
                return

            def ok(result):
                if not alive():
                    return
                if tick(result):
                    self.set_timer(interval, step)
                else:
                    polls.pop(key, None)

            def failed(message):
                if alive():
                    polls.pop(key, None)
                    self.set_state('error', f'状态轮询中断：{message}  按 r 刷新')
            self.app.request(fetch, ok, failed)
        step()

    def stop_poll(self, key):
        self.__dict__.setdefault('_polls', {}).pop(key, None)

    def polling(self, key):
        return key in self.__dict__.get('_polls', {})


def optional_get(client, path):
    """GET for an endpoint an older backend may lack: (result, None) or (None, safe message).

    Used inside an app.request job next to a required call, so its failure only
    disables the optional part of a form instead of the whole form.
    """
    from .app import safe_message  # Lazy: app imports the views.
    try:
        return client.get(path), None
    except Exception as exc:  # noqa: BLE001 - shown as a safe message only
        return None, safe_message(exc)


def items(result, key='items'):
    if isinstance(result, dict):
        value = result.get(key)
        return value if isinstance(value, list) else []
    return result if isinstance(result, list) else []


def fill_table(table, rows, key='id'):
    """Replace rows, keeping the cursor on the same key when possible.

    Every string cell becomes a Text so backend names and message bodies are shown
    literally instead of being parsed as console markup.
    """
    current = selected_key(table)
    table.clear()
    for index, (row_key, cells) in enumerate(rows):
        cells = [plain(c) if not isinstance(c, Text) else c for c in cells]
        table.add_row(*cells, key=str(row_key if row_key not in (None, '') else f'row-{index}'))
    if current is not None:
        for index, (row_key, _) in enumerate(rows):
            if str(row_key) == current:
                table.move_cursor(row=index)
                break


def selected_key(table):
    if not table.row_count:
        return None
    row = table.cursor_row
    if row is None or not 0 <= row < table.row_count:
        return None
    try:
        return table.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value
    except Exception:
        return None


def make_table(*columns, id=None):
    table = DataTable(id=id, cursor_type='row', zebra_stripes=True)
    table.add_columns(*columns)
    return table
