"""Imported chat browser: conversations, message context paging, media/voice, reply drafts.

Read-only except for explicit actions: creating a reply draft, queueing a local voice
transcription, saving media to a new local file, and group-admin decisions.
"""
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static

from . import model
from .a_reply import GroupAdminModal, ReplyModal, show_receipts
from .base import View, esc, fill_table, items, make_table, seg, selected_key
from .forms import ChoiceModal, Field, FormModal, required
from .modals import TextModal

PAGE = 30


def message_text(m):
    content = str(m.get('content') or '')
    extras = []
    if m.get('media'):
        extras.append(f'[{len(m["media"])} 个图片/表情]')
    if m.get('voice'):
        voice = m['voice'] if isinstance(m['voice'], dict) else {}
        extras.append('[语音' + (f'：{voice.get("transcript")}' if voice.get('transcript') else '') + ']')
    if m.get('files'):
        extras.append('[文件 ' + '、'.join(str(f.get('filename')) for f in m['files'][:3]) + ']')
    return ' '.join([content] + extras).strip()


class ChatsView(View):
    title = '聊天记录'
    subtitle = '浏览已导入的本地聊天；回复需生成、编辑、预览并单独确认后才发送'
    BINDINGS = [Binding('left_square_bracket', 'earlier', '更早'), Binding('right_square_bracket', 'later', '更新'),
                Binding('f', 'follow', '跳到最新'), Binding('R', 'reply', '回复'), Binding('v', 'voice', '语音'),
                Binding('o', 'media', '保存媒体'), Binding('h', 'receipts', '发送回执'), Binding('g', 'admin', '群管理审批'),
                Binding('w', 'detail', '消息全文'), Binding('slash', 'filter', '筛选', show=False)]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes='search-bar'):
            yield Input(placeholder='按会话名筛选后按 Enter', id='chat-filter')
            yield Button('筛选', id='chat-go')
        with Horizontal(classes='split'):
            yield make_table('会话', '条数', '最近', id='chat-conv-table')
            yield make_table('时间', '发送者', '内容', id='chat-msg-table')
        yield Static('', classes='detail', id='chat-detail')
        yield Static('Enter 打开 · [ 更早 · ] 更新 · f 最新 · R 回复 · v 语音转写 · o 保存媒体 · w 全文 · h 回执 · g 群管理审批',
                     classes='hint')

    def on_mount(self):
        self.convs, self.messages, self.window, self.current = {}, {}, {}, None
        self.query_one('#chat-detail').display = False

    def refresh_data(self):
        query = self.query_one('#chat-filter', Input).value.strip()
        self.start_load(lambda c: c.api_get('/api/chat-conversations', query=query, limit=200), self.show_convs,
                        '正在读取会话…')

    def show_convs(self, result):
        rows = items(result, 'conversations')
        self.convs = {model.pair(c.get('platform'), c.get('conversation_id')): c for c in rows}
        fill_table(self.query_one('#chat-conv-table'), [
            (key, (f'{model.platform_label(c.get("platform"))} · {model.one_line(c.get("conversation"), 22)}',
                   str(c.get('count', '')), c.get('time') or model.fmt_time(c.get('timestamp'))))
            for key, c in self.convs.items()])
        if not rows:
            self.set_state('empty', '没有已导入的会话。可在「本机数据」中读取并导入聊天记录。')
        if self.current in self.convs:
            self.load(dict(platform=self.convs[self.current]['platform'],
                           conversation_id=self.convs[self.current]['conversation_id'], before=PAGE, after=0))

    def on_input_submitted(self, event):
        event.stop()
        self.refresh_data()

    def on_button_pressed(self, event):
        if event.button.id == 'chat-go':
            self.refresh_data()

    def focus_main(self):
        self.query_one('#chat-conv-table').focus()

    def on_data_table_row_selected(self, event):
        if event.data_table.id == 'chat-conv-table':
            self.action_open()

    def on_data_table_row_highlighted(self, event):
        if event.data_table.id == 'chat-msg-table':
            self.show_detail()

    def action_filter(self):
        self.query_one('#chat-filter').focus()

    # ---- message window ----------------------------------------------------

    def action_open(self):
        key = selected_key(self.query_one('#chat-conv-table'))
        conv = self.convs.get(key or '')
        if not conv:
            return
        self.current = key
        self.load(dict(platform=conv['platform'], conversation_id=conv['conversation_id'], before=PAGE, after=0),
                  focus=True)

    def load(self, params, focus=False, anchor_last=True):
        key = self.current

        def ok(result):
            if self.current != key:
                return  # The user opened another conversation meanwhile.
            result = result if isinstance(result, dict) else {}
            self.window = result
            rows = result.get('messages') or []
            self.messages = {str(m.get('id')): m for m in rows}
            table = self.query_one('#chat-msg-table')
            fill_table(table, [(m.get('id'), (m.get('time') or model.fmt_time(m.get('timestamp')),
                                              model.one_line('我' if m.get('is_self') == 1 else m.get('sender'), 12),
                                              model.one_line(message_text(m), 80))) for m in rows])
            anchor = str(result.get('anchor_message_id') or '')
            for index, m in enumerate(rows):
                if str(m.get('id')) == anchor:
                    table.move_cursor(row=index)
            if focus:
                table.focus()
            flags = ['可向前翻页' if result.get('has_before') else '已到最早', '可向后翻页' if result.get('has_after') else '已是最新']
            self.set_state('info', f'{result.get("conversation") or ""} · {len(rows)} 条 · ' + ' · '.join(flags))
            self.show_detail()
        self.act(lambda c: c.api_get('/api/chat-context', **params), ok, '正在读取消息…', '读取消息失败')

    def edge(self, first):
        rows = self.window.get('messages') or []
        return rows[0 if first else -1] if rows else None

    def action_earlier(self):
        m = self.edge(True)
        if not m or not self.window.get('has_before'):
            self.set_state('info', '已经是最早的已导入消息。')
            return
        self.load(dict(anchor_message_id=m['id'], platform=m['platform'], conversation_id=m['conversation_id'],
                       before=PAGE, after=0))

    def action_later(self):
        m = self.edge(False)
        if not m or not self.window.get('has_after'):
            self.set_state('info', '已经是最新的已导入消息。')
            return
        self.load(dict(anchor_message_id=m['id'], platform=m['platform'], conversation_id=m['conversation_id'],
                       before=0, after=PAGE))

    def action_follow(self):
        conv = self.convs.get(self.current or '')
        if conv:
            self.load(dict(platform=conv['platform'], conversation_id=conv['conversation_id'], before=PAGE, after=0))

    def selected(self):
        return self.messages.get(selected_key(self.query_one('#chat-msg-table')) or '')

    def show_detail(self):
        box = self.query_one('#chat-detail', Static)
        m = self.selected()
        box.display = bool(m)
        if not m:
            return
        lines = [f'[#a78bfa]{esc("我" if m.get("is_self") == 1 else m.get("sender") or "—")}[/] · '
                 f'{esc(m.get("time") or model.fmt_time(m.get("timestamp")))} · M{esc(m.get("id"))}',
                 esc(model.one_line(m.get('content'), 400))]
        ref = m.get('reply_to') if isinstance(m.get('reply_to'), dict) else None
        if ref:
            lines.append(f'[#7f8a9a]引用[/] {esc(model.one_line(ref.get("content") or ref.get("source_id"), 80))}')
        voice = m.get('voice') if isinstance(m.get('voice'), dict) else None
        if voice:
            lines.append(f'[#7f8a9a]语音[/] {esc(voice.get("status") or "")} '
                         f'{esc(model.one_line(voice.get("transcript") or voice.get("error") or "", 120))}')
        for f in m.get('files') or []:
            lines.append(f'[#7f8a9a]文件[/] {esc(f.get("filename"))} · {esc(f.get("availability") or "")} · F{esc(f.get("id"))}')
        box.update('\n'.join(lines))

    def action_detail(self):
        m = self.selected()
        if m:
            self.app.push_screen(TextModal(f'消息 M{m.get("id")}', message_text(m)))

    # ---- actions on a message ---------------------------------------------

    def need_message(self):
        m = self.selected()
        if not m:
            self.set_state('empty', '请先打开会话并选择一条消息。')
        return m

    def action_reply(self):
        m = self.need_message()
        if not m:
            return

        def open_draft(draft, fresh=False):
            draft = draft if isinstance(draft, dict) else {}
            if draft.get('status') in ('SENT', 'REJECTED') and not fresh:
                def chosen(value):
                    if value == 'new':
                        self.act(lambda c: c.api_post('/api/replies', dict(message_id=m['id'], fresh=True)),
                                 lambda d: open_draft(d, True), '正在新建草稿…', '新建草稿失败')
                    elif value == 'view':
                        self.app.push_screen(ReplyModal(draft))
                self.app.push_screen(ChoiceModal('这条消息已有处理过的草稿',
                                                 [('查看原草稿', 'view'), ('新建一份草稿', 'new')]), chosen)
                return
            self.app.push_screen(ReplyModal(draft))
        self.act(lambda c: c.api_post('/api/replies', dict(message_id=m['id'], fresh=False)), open_draft,
                 '正在打开回复草稿…', '打开草稿失败')

    def action_voice(self):
        m = self.need_message()
        if not m or not m.get('voice'):
            if m:
                self.set_state('info', '这条消息没有原生语音。')
            return
        mid = m['id']
        retry = (m['voice'] or {}).get('status') in ('ready', 'error', 'failed')

        def queued(result):
            self.set_state('info', '已加入本地转写队列，稍后按 r 刷新查看。')
            self.poll('voice', lambda c: c.api_get(f'/api/voice/{seg(mid)}'), lambda r: self.voice_tick(mid, r), 3)
        self.act(lambda c: c.api_post(f'/api/voice/{seg(mid)}', dict(action='transcribe', retry=retry)), queued,
                 '正在加入转写队列…', '转写未开始')

    def voice_tick(self, mid, result):
        message = self.messages.get(str(mid))
        if message is not None and isinstance(result, dict):
            message['voice'] = result
            self.show_detail()
        return isinstance(result, dict) and result.get('status') in ('queued', 'running')

    def action_media(self):
        m = self.need_message()
        if not m or not m.get('media'):
            if m:
                self.set_state('info', '这条消息没有可保存的图片或表情。')
            return
        # Only entries the server marked available carry a same-origin URL (/media/{id}/{index}).
        choices = [(f'{x.get("index")}. {x.get("kind") or "媒体"} {x.get("format") or ""}', x)
                   for x in m['media'] if isinstance(x, dict) and x.get('available') and x.get('url')]
        if not choices:
            self.set_state('info', '这条消息的媒体本机未缓存或暂不支持解密。')
            return

        def chosen(item):
            if item is None:
                return
            url, index = item['url'], item.get('index')
            suffix = f'.{item["format"]}' if item.get('format') else ''
            default = str(Path.home() / 'Downloads' / f'tulpa-M{m["id"]}-{index}{suffix}')

            def done(body):
                if body:
                    path = body['path']
                    self.act(lambda c: c.download(url, path),
                             lambda r: self.set_state('info', f'已保存到 {path}（不会覆盖已有文件）。'), '正在保存…', '保存失败')
            self.app.push_screen(FormModal('保存媒体到本机', [
                Field('path', '保存为', value=default, hint='新建文件，权限 600；同名文件存在时拒绝覆盖。')],
                lambda v: dict(path=str(Path(required(v['path'], '路径', 2048)).expanduser())), '保存'), done)
        self.app.push_screen(ChoiceModal('选择媒体', choices), chosen)

    def action_receipts(self):
        conv = self.convs.get(self.current or '')
        if not conv:
            self.set_state('empty', '请先打开一个会话。')
            return
        self.act(lambda c: c.api_get('/api/reply-receipts', platform=conv['platform'],
                                     conversation_id=conv['conversation_id']),
                 lambda r: show_receipts(self.app, r), '正在读取回执…', '读取回执失败')

    def action_admin(self):
        def opened(result):
            rows = items(result)
            if not rows:
                self.set_state('empty', '还没有 Agent 对话；群管理操作只会由 Agent 对话提出。')
                return
            self.app.push_screen(ChoiceModal('选择提出操作的 Agent 对话',
                                             [(f'{model.one_line(s.get("title") or "新对话", 40)} · {model.fmt_time(s.get("updated"))}',
                                               (s.get('id'), s.get('title'))) for s in rows]),
                                 lambda v: v and self.app.push_screen(GroupAdminModal(*v)))
        self.act(lambda c: c.get('/agent/sessions'), opened, '正在读取 Agent 对话…', '读取对话失败')
