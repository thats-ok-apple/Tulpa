"""Generic field-spec form dialog and small choice dialogs shared by the feature pages.

A form never sends anything itself: it dismisses with the validated body (or None),
and the calling view performs the request. Secrets only use password inputs.
"""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList, Select, SelectionList, Static, TextArea
from textual.widgets.option_list import Option

from .base import esc, plain
from .modals import Dialog


def by_value(pairs):
    """Turn `(value, label)` constant tables into the `(label, value)` options a Field takes."""
    return [(label, value) for value, label in pairs]


class Field:
    """kind: text | password | area | select | check | multi | note."""

    def __init__(self, key, label, kind='text', value=None, options=(), placeholder='', hint='', height=6):
        self.key, self.label, self.kind = key, label, kind
        self.value, self.options = value, list(options)
        self.placeholder, self.hint, self.height = placeholder, hint, height

    @property
    def wid(self):
        return 'f-' + self.key.replace('.', '-').replace('_', '-')


class FormModal(ModalScreen):
    """Collect values; `validate(values)` returns the request body or raises ValueError."""

    BINDINGS = [Binding('escape', 'cancel', '取消'), Binding('ctrl+s', 'submit', '提交')]

    def __init__(self, title, fields, validate=None, submit='保存', lead='', danger=False):
        super().__init__()
        self.title_text, self.fields, self.validate = title, fields, validate or (lambda v: v)
        self.submit_text, self.lead, self.danger = submit, lead, danger

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog' + (' -danger' if self.danger else '')):
            yield Static(esc(self.title_text), classes='dialog-title')
            with VerticalScroll(classes='dialog-flex form-scroll'):
                if self.lead:
                    yield Static(esc(self.lead), classes='hint')
                for f in self.fields:
                    yield from self.compose_field(f)
            yield Static('', classes='error-text', id='form-error')
            with Horizontal(classes='buttons'):
                yield Button(f'{self.submit_text} (ctrl+s)', id='submit', variant='error' if self.danger else 'primary')
                yield Button('取消 (Esc)', id='cancel')

    def compose_field(self, f):
        if f.kind == 'note':
            yield Static(esc(f.label), classes='hint')
            return
        if f.kind == 'check':
            yield Checkbox(f.label, bool(f.value), id=f.wid)
        elif f.kind in ('area', 'multi'):
            yield Static(esc(f.label), classes='card-title')
            if f.kind == 'area':
                area = TextArea(str(f.value or ''), id=f.wid, soft_wrap=True, classes='form-area')
                area.styles.height = f.height
                yield area
            else:
                chosen = set(f.value or ())
                yield SelectionList(*[(plain(label), value, value in chosen) for label, value in f.options],
                                    id=f.wid, classes='form-multi')
        else:
            with Horizontal(classes='row'):
                yield Label(f.label, classes='label')
                if f.kind == 'select':
                    options = [(plain(label), value) for label, value in f.options]
                    values = [v for _, v in f.options]
                    kwargs = dict(value=f.value) if f.value in values else {}
                    yield Select(options, prompt=f.placeholder or '请选择', id=f.wid, **kwargs)
                else:
                    yield Input('' if f.kind == 'password' else str(f.value if f.value is not None else ''),
                                placeholder=f.placeholder, password=f.kind == 'password', id=f.wid)
        if f.hint:
            yield Static(esc(f.hint), classes='hint')

    def on_mount(self):
        for widget in self.query('Input, TextArea, Select, SelectionList, Checkbox'):
            widget.focus()
            break

    def values(self):
        result = {}
        for f in self.fields:
            if f.kind == 'note':
                continue
            widget = self.query_one(f'#{f.wid}')
            if f.kind == 'area':
                result[f.key] = widget.text
            elif f.kind == 'multi':
                result[f.key] = list(widget.selected)
            elif f.kind == 'select':
                value = widget.value
                result[f.key] = None if value is Select.NULL else value
            elif f.kind == 'check':
                result[f.key] = bool(widget.value)
            else:
                result[f.key] = widget.value
        return result

    def on_input_submitted(self, event):
        event.stop()
        self.focus_next()

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'submit':
            self.action_submit()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)

    def action_submit(self):
        try:
            body = self.validate(self.values())
        except ValueError as exc:
            self.query_one('#form-error', Static).update(esc(exc))
            return
        self.dismiss(body)


class ChoiceModal(ModalScreen):
    """Pick one of several labelled actions or items; dismisses with its value or None."""

    BINDINGS = [Binding('escape', 'cancel', '取消')]

    def __init__(self, title, choices, lead=''):
        super().__init__()
        self.title_text, self.choices, self.lead = title, list(choices), lead

    def compose(self) -> ComposeResult:
        with Dialog(classes='dialog'):
            yield Static(esc(self.title_text), classes='dialog-title')
            if self.lead:
                yield Static(esc(self.lead), classes='hint')
            yield OptionList(*[Option(plain(label), id=str(i)) for i, (label, _) in enumerate(self.choices)],
                             id='choice-list', classes='dialog-flex')
            with Horizontal(classes='buttons'):
                yield Button('取消 (Esc)', id='cancel')

    def on_mount(self):
        self.query_one('#choice-list').focus()

    def on_option_list_option_selected(self, event):
        event.stop()
        self.dismiss(self.choices[int(event.option.id)][1])

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


def required(value, label, limit=None):
    value = (value or '').strip()
    if not value:
        raise ValueError(f'请填写{label}。')
    if limit and len(value) > limit:
        raise ValueError(f'{label}最多 {limit} 字。')
    return value


def int_in(value, label, low, high):
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f'{label}应为整数。') from None
    if not low <= number <= high:
        raise ValueError(f'{label}范围 {low}–{high}。')
    return number


def date_text(value, label='日期', allow_empty=True):
    from . import model
    value = (value or '').strip()
    if not value and allow_empty:
        return ''
    if not value or not model.valid_date(value):
        raise ValueError(f'{label}应为 YYYY-MM-DD' + ('，或留空。' if allow_empty else '。'))
    return value
