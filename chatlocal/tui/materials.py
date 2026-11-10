"""File materials, evidence collections and QQ group knowledge (native artifact/investigation routes).

Every action maps to an original route: artifact actions prepare/materialize/
locate/pin/evict (with the native `confirmed` flag for the 100 MiB limit),
discovery and QQ directory reconciliation run as the shared artifact job, and
collections only store references. Nothing here deletes source chats or files.
"""
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static

from . import model
from .base import View, esc, fill_table, items, make_table, seg, selected_key
from .forms import by_value, ChoiceModal, Field, FormModal, date_text, required
from .modals import ConfirmModal, TextModal

LIMIT_BYTES = 32 * 1024 * 1024
SOURCE_TYPES = (('message', '聊天消息'), ('media', '图片 / 表情（消息ID:序号）'), ('voice', '原生语音'),
                ('artifact_source', '文件'), ('artifact_chunk', '文件片段（文件ID:片段ID）'),
                ('qq_notice', 'QQ 群公告'), ('qq_essence', 'QQ 群精华'))
AVAILABILITY = dict(CACHED='已缓存', AVAILABLE_LOCAL='本机可取得', METADATA_ONLY='仅元数据', MISSING='本机未找到')
PARSE = dict(PARSED='已解析', PARTIAL='部分解析', NO_TEXT='无文字')


def file_state(f):
    parts = [AVAILABILITY.get(f.get('availability'), f.get('availability') or '—')]
    if f.get('parse_status'):
        parts.append(PARSE.get(f['parse_status'], f['parse_status']))
    if f.get('user_pinned'):
        parts.append('长期保留')
    return ' · '.join(parts)


def conversation_options(rows):
    return [(f'{model.platform_label(r.get("platform"))} · {model.one_line(r.get("conversation") or r.get("conversation_id"), 40)}',
             model.pair(r.get('platform'), r.get('conversation_id'))) for r in rows if r.get('conversation_id')]


def save_path_form(filename):
    default = str(Path('~/Downloads').expanduser() / (filename or 'download.bin'))

    def validate(v):
        path = Path(required(v['path'], '保存路径', 1000)).expanduser()
        if not path.is_absolute():
            raise ValueError('请填写绝对路径。')
        if path.exists():
            raise ValueError('目标文件已存在；不会覆盖，请换一个新文件名。')
        if not path.parent.is_dir():
            raise ValueError('目标文件夹不存在。')
        return str(path)
    return FormModal('保存到本机', [Field('path', '保存为', value=default,
                                         hint='只会新建文件（权限 600），不会覆盖已有文件。')], validate, '保存')


def add_to_collection(view, refs, label):
    """Pick a collection (or create one) and add native {source_type, source_id} refs."""
    def opened(result):
        rows = items(result, 'collections')
        choices = [(f'{model.one_line(c.get("title"), 40)}（{c.get("item_count", 0)} 项）', c.get('id')) for c in rows]
        choices.append(('＋ 新建集合…', '__new__'))

        def picked(cid):
            if cid == '__new__':
                def created(title):
                    if title:
                        view.act(lambda c: c.api_post('/api/collections', dict(title=title)),
                                 lambda r: add(r.get('id'), title), '正在新建集合…', '新建失败')
                view.app.push_screen(FormModal('新建证据集合', [Field('title', '集合名称')],
                                               lambda v: required(v['title'], '集合名称', 100), '创建'), created)
            elif cid:
                add(cid, next((c.get('title') for c in rows if c.get('id') == cid), ''))
        view.app.push_screen(ChoiceModal(f'加入证据集合：{label}', choices,
                                         '集合只保存来源引用，不复制原文、文件或图片。'), picked)

    def add(cid, title):
        view.act(lambda c: c.api_post(f'/api/collections/{seg(cid)}/items', dict(items=refs)),
                 lambda r: view.set_state('info', f'已加入「{title}」：新增 {r.get("added", 0)} 项（重复项不会重复加入）。'),
                 '正在加入集合…', '加入失败')
    view.act(lambda c: c.api_get('/api/collections'), opened, '正在读取集合…', '读取集合失败')


class ArtifactsView(View):
    title = '文件材料'
    subtitle = '聊天中的文件来源；元数据不会触发下载、解析或模型'
    BINDINGS = [Binding('f', 'filters', '筛选'), Binding('g', 'materialize', '取得原文件'), Binding('p', 'prepare', '解析'),
                Binding('l', 'locate', '定位'), Binding('k', 'pin', '长期保留'), Binding('e', 'evict', '释放缓存'),
                Binding('s', 'save', '保存到本机'), Binding('c', 'collect', '加入集合'), Binding('v', 'chunks', '查看正文'),
                Binding('D', 'discover', '发现历史附件'), Binding('i', 'inventory', 'QQ 群目录'),
                Binding('right_square_bracket', 'next_page', '下一页'), Binding('left_square_bracket', 'prev_page', '上一页')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static('', classes='hint', id='art-status')
        with Horizontal(classes='search-bar'):
            yield Input(placeholder='按文件名搜索（勾选正文搜索时搜索已解析文本），Enter 执行', id='art-query', max_length=600)
            yield Button('搜索', id='art-go', variant='primary')
        yield make_table('文件', '会话', '发送者', '大小', '时间', '状态', id='art-table')
        yield Static('', classes='detail', id='art-detail')
        yield Static('f 筛选 · v 正文 · g 取得 · p 解析 · l 定位 · k 保留 · e 释放 · s 保存 · c 加入集合 · D 发现 · i 群目录 · [ ] 翻页',
                     classes='hint')

    def on_mount(self):
        self.files, self.status = {}, {}
        self.filters = dict(platform='', conversation_id='', extension='', sender='', start='', end='', body=False)
        self.page_offset, self.next_offset = 0, None
        self.query_one('#art-detail').display = False

    def refresh_data(self):
        query = self.query_one('#art-query', Input).value.strip()
        params = dict(self.filters, query=query, offset=self.page_offset)
        params['body'] = 'true' if params['body'] else 'false'

        def fetch(c):
            return c.api_get('/api/artifacts/status'), c.api_get('/api/artifacts', **params)
        self.start_load(fetch, self.show, '正在读取文件材料…')

    def show(self, result):
        status, found = result
        self.show_status(status)
        rows = items(found, 'files')
        # Body search returns one row per matching chunk; keep the chunk id in the key.
        self.files = {}
        table_rows = []
        for f in rows:
            key = f'{f.get("id")}:{f.get("chunk_id")}' if f.get('chunk_id') else str(f.get('id'))
            self.files[key] = f
            table_rows.append((key, (model.one_line(f.get('filename'), 28), model.one_line(f.get('conversation'), 16),
                                     model.one_line(f.get('sender'), 10), model.size_text(f.get('size')),
                                     model.fmt_time(f.get('timestamp')), file_state(f))))
        fill_table(self.query_one('#art-table'), table_rows)
        found = found if isinstance(found, dict) else {}
        self.next_offset = found.get('next_offset')
        if not rows:
            self.set_state('empty', '没有符合条件的文件。历史附件可按 D 发现一次。')
        else:
            self.set_state('info', f'共 {found.get("match_count", len(rows))} 项，第 {self.page_offset + 1} 起。'
                                   + esc(model.one_line(found.get('note') or '', 80)))
        self.show_detail()

    def show_status(self, status):
        self.status = status if isinstance(status, dict) else {}
        s = self.status
        counts = s.get('counts') or {}
        text = (f'已登记 QQ {counts.get("qq", 0)} · 微信 {counts.get("wechat", 0)} · 唯一文件 {s.get("unique_artifacts", 0)} · '
                f'缓存 {model.size_text(s.get("cache_bytes"))} · OneBot {"已配置" if s.get("onebot_configured") else "未配置"}')
        if s.get('running'):
            text += ' · [#eab308]文件任务运行中[/]'
        elif s.get('error'):
            text += f' · [#ef4444]上次任务：{esc(model.one_line(s["error"], 60))}[/]'
        self.query_one('#art-status', Static).update(text)

    def selected(self):
        return self.files.get(selected_key(self.query_one('#art-table')) or '')

    def on_data_table_row_highlighted(self, event):
        self.show_detail()

    def on_data_table_row_selected(self, event):
        self.action_chunks()

    def show_detail(self):
        box = self.query_one('#art-detail', Static)
        f = self.selected()
        box.display = bool(f)
        if not f:
            return
        lines = [f'[b #a78bfa]{esc(f.get("filename") or "—")}[/]  {esc(f.get("citation_id") or "")}',
                 f'{esc(model.platform_label(f.get("platform")))} · {esc(f.get("conversation") or "")} · {esc(f.get("sender") or "")} · '
                 f'{esc(f.get("time") or "")} · {esc(model.size_text(f.get("size")))}',
                 f'状态 {esc(file_state(f))}' + (f' · 解析器 {esc(f["parser"])}' if f.get('parser') else '')]
        if f.get('parse_note'):
            lines.append(f'[#7f8a9a]{esc(model.one_line(f["parse_note"], 160))}[/]')
        if f.get('text'):
            lines.append(esc(model.one_line(f['text'], 300)))
        box.update('\n'.join(lines))

    def on_input_submitted(self, event):
        event.stop()
        self.page_offset = 0
        self.refresh_data()

    def on_button_pressed(self, event):
        if event.button.id == 'art-go':
            self.page_offset = 0
            self.refresh_data()

    def action_next_page(self):
        if self.next_offset is None:
            self.set_state('info', '已经是最后一页。')
            return
        self.page_offset = self.next_offset
        self.refresh_data()

    def action_prev_page(self):
        if not self.page_offset:
            return
        self.page_offset = max(0, self.page_offset - 20)
        self.refresh_data()

    def action_filters(self):
        convs = [('全部会话', '')] + [(label, value) for label, value in conversation_options(self.status.get('conversations') or [])]
        current = model.pair(self.filters['platform'], self.filters['conversation_id']) if self.filters['conversation_id'] else ''

        def validate(v):
            platform, cid = (model.unpair(v['conversation']) if v['conversation'] else (v['platform'] or '', ''))
            start, end = date_text(v['start'], '开始日期'), date_text(v['end'], '截止日期')
            return dict(platform=platform or '', conversation_id=cid or '', extension=v['extension'].strip().lstrip('.').lower()[:20],
                        sender=v['sender'].strip()[:200], start=start, end=end, body=v['body'])

        def done(body):
            if body is not None:
                self.filters, self.page_offset = body, 0
                self.refresh_data()
        self.app.push_screen(FormModal('筛选文件', [
            Field('platform', '平台', 'select', self.filters['platform'], (('全部平台', ''), ('QQ', 'qq'), ('微信', 'wechat'))),
            Field('conversation', '会话', 'select', current, convs),
            Field('extension', '扩展名', value=self.filters['extension'], placeholder='如 pdf、docx'),
            Field('sender', '发送者', value=self.filters['sender']),
            Field('start', '开始日期', value=self.filters['start'], placeholder='YYYY-MM-DD'),
            Field('end', '截止日期', value=self.filters['end'], placeholder='YYYY-MM-DD'),
            Field('body', '搜索已解析正文（而不是文件名）', 'check', self.filters['body']),
        ], validate, '应用'), done)

    # ---- artifact actions (shared native job) ---------------------------

    def run_action(self, action, f, confirmed=False, label=''):
        sid = f.get('id')

        def started(result):
            self.app.show_status(f'{label}已开始：{model.one_line(f.get("filename"), 30)}')
            self.poll('artifact', lambda c: c.api_get('/api/artifacts/status'), self.job_tick)
        self.act(lambda c: c.api_post(f'/api/artifacts/{seg(sid)}/action', dict(action=action, confirmed=confirmed)),
                 started, f'正在{label}…', f'{label}失败')

    def job_tick(self, status):
        self.show_status(status)
        status = status if isinstance(status, dict) else {}
        if status.get('running'):
            self.set_state('loading', '文件任务运行中…')
            return True
        if status.get('error'):
            self.reload_with('error', '文件任务未完成：' + str(status['error']))
        else:
            result = status.get('result')
            note = result.get('note') if isinstance(result, dict) else ''
            self.reload_with('info', '文件任务已完成。' + (str(note) if note else ''))
        return False

    def require(self):
        f = self.selected()
        if not f:
            self.set_state('empty', '请先选择一个文件。')
            return None
        if self.status.get('running'):
            self.set_state('info', '已有文件任务在运行，请等待完成。')
            return None
        return f

    def with_limit(self, f, action, label):
        size = f.get('size')
        if isinstance(size, int) and size > LIMIT_BYTES:
            def confirmed(yes):
                if yes:
                    self.run_action(action, f, True, label)
            self.app.push_screen(ConfirmModal('文件超过默认大小限制',
                                              f'「{f.get("filename")}」约 {model.size_text(size)}，超过 32 MiB 默认限制。\n'
                                              '确认后允许最多 100 MiB 的本机复制或下载。', '确认扩大限制'), confirmed)
        else:
            self.run_action(action, f, False, label)

    def action_materialize(self):
        if f := self.require():
            self.with_limit(f, 'materialize', '取得原文件')

    def action_prepare(self):
        if f := self.require():
            self.with_limit(f, 'prepare', '解析文件')

    def action_locate(self):
        if f := self.require():
            self.run_action('locate', f, False, '定位本机文件')

    def action_pin(self):
        f = self.require()
        if not f:
            return

        def confirmed(yes):
            if yes:
                self.with_limit(f, 'pin', '长期保留')
        self.app.push_screen(ConfirmModal('长期保留原文件', f'把「{f.get("filename")}」的原文件保存在本项目缓存中并长期保留？\n'
                                          '长期保留的文件不会被释放缓存清理。', '保留'), confirmed)

    def action_evict(self):
        f = self.require()
        if not f:
            return
        if f.get('user_pinned') or f.get('referenced_by_answer'):
            self.set_state('info', '此文件已长期保留或被正式回答引用，不能释放缓存。')
            return

        def confirmed(yes):
            if yes:
                self.run_action('evict', f, False, '释放缓存')
        self.app.push_screen(ConfirmModal('释放原文件缓存', f'释放「{f.get("filename")}」在本项目中的原文件缓存？\n'
                                          '来源记录、解析文本和检索索引保留；客户端中的原文件不受影响。', '释放', danger=True), confirmed)

    def action_discover(self):
        if self.status.get('running'):
            self.set_state('info', '已有文件任务在运行，请等待完成。')
            return

        def confirmed(yes):
            if yes:
                self.act(lambda c: c.api_post('/api/artifacts-discover', {}),
                         lambda r: self.poll('artifact', lambda c: c.api_get('/api/artifacts/status'), self.job_tick),
                         '正在启动发现…', '发现失败')
        self.app.push_screen(ConfirmModal('发现历史附件', '扫描已导入聊天中的文件消息并登记元数据（最多 180 秒）。\n'
                                          '不会下载、解析或调用模型；已登记的来源保留。', '开始'), confirmed)

    def action_inventory(self):
        groups = [r for r in self.status.get('conversations') or [] if r.get('platform') == 'qq' and ':group:' in str(r.get('conversation_id'))]
        if not self.status.get('onebot_configured'):
            self.set_state('info', '未配置 OneBot：QQ 群文件目录无法对账，当前只包含本地文件消息。')
            return
        if not groups:
            self.set_state('empty', '没有已导入的 QQ 群。')
            return
        enabled = {r.get('conversation_id'): r for r in self.status.get('inventories') or []}
        choices = [(f'{model.one_line(g.get("conversation"), 36)}' + ('（已启用对账）' if g.get('conversation_id') in enabled else ''),
                    g.get('conversation_id')) for g in groups]

        def picked(cid):
            if cid:
                self.act(lambda c: c.api_post('/api/artifacts-inventory', dict(conversation_id=cid)),
                         lambda r: self.poll('artifact', lambda c: c.api_get('/api/artifacts/status'), self.job_tick),
                         '正在读取群文件目录…', '对账失败')
        self.app.push_screen(ChoiceModal('读取并启用 QQ 群文件目录对账', choices,
                                         '只读取文件目录元数据；启用后启动时及每小时对账。不下载文件。'), picked)

    def action_save(self):
        f = self.selected()
        if not f:
            self.set_state('empty', '请先选择一个文件。')
            return
        if f.get('availability') != 'CACHED':
            self.set_state('info', '原文件尚未缓存：先按 g 取得原文件，再保存到本机。')
            return

        def chosen(path):
            if path:
                self.act(lambda c: c.download(f'/api/artifacts/{seg(f.get("id"))}/content', path),
                         lambda r: self.set_state('info', f'已保存到 {r}'), '正在保存…', '保存失败')
        self.app.push_screen(save_path_form(f.get('filename')), chosen)

    def action_collect(self):
        f = self.selected()
        if not f:
            self.set_state('empty', '请先选择一个文件。')
            return
        if f.get('chunk_id'):
            refs = [dict(source_type='artifact_chunk', source_id=f'{f.get("id")}:{f.get("chunk_id")}')]
        else:
            refs = [dict(source_type='artifact_source', source_id=str(f.get('id')))]
        add_to_collection(self, refs, f.get('filename') or '')

    def action_chunks(self):
        f = self.selected()
        if not f:
            return
        sid = f.get('id')

        def fetch(c):
            pages, offset = [], 0
            for _ in range(5):  # At most 40 chunks per view; the rest stays in the index.
                page = c.api_get(f'/api/artifacts/{seg(sid)}/chunks', offset=offset)
                pages.append(page)
                offset = page.get('next_offset') if isinstance(page, dict) else None
                if offset is None:
                    break
            return pages

        def show(pages):
            chunks = [x for p in pages for x in items(p, 'files')]
            note = (pages[0].get('note') if pages and isinstance(pages[0], dict) else '') or ''
            if not chunks:
                self.app.push_screen(TextModal(f'正文：{f.get("filename")}', note or '尚未解析；按 p 解析后可查看正文。'))
                return
            more = isinstance(pages[-1], dict) and pages[-1].get('has_more')
            text = '\n\n'.join(f'[{c.get("citation_id")}] {c.get("text") or ""}' for c in chunks)
            if more:
                text += f'\n\n（仅显示前 {len(chunks)} 个片段，共 {pages[-1].get("match_count")} 个。）'
            self.app.push_screen(TextModal(f'正文：{f.get("filename")}', text, esc(model.one_line(note, 120))))
        self.act(fetch, show, '正在读取正文片段…', '读取正文失败')


def ref_form():
    def validate(v):
        kind = v['source_type']
        if not kind:
            raise ValueError('请选择来源类型。')
        sid = required(v['source_id'], '来源编号', 40).upper().lstrip('MFK')
        sid = sid.replace(':C', ':')
        parts = sid.split(':')
        if not all(p.isdigit() for p in parts) or len(parts) != (2 if kind in ('media', 'artifact_chunk') else 1):
            raise ValueError('编号格式：消息/文件/资料为数字；图片为 消息ID:序号；文件片段为 文件ID:片段ID。')
        return [dict(source_type=kind, source_id=sid)]
    return FormModal('添加来源引用', [
        Field('source_type', '来源类型', 'select', 'message', by_value(SOURCE_TYPES)),
        Field('source_id', '来源编号', placeholder='如 1234、F12、F12:C34、K7'),
        Field('note', '编号可在聊天浏览、文件材料和群资料页的详情中看到（M/F/K 前缀可省略）。服务端会重新核对来源是否存在。', 'note'),
    ], validate, '添加')


class CollectionsView(View):
    title = '证据集合'
    subtitle = '命名的来源引用集；只保存引用，不复制原文或文件'
    BINDINGS = [Binding('n', 'create', '新建'), Binding('e', 'rename', '重命名'), Binding('d', 'delete', '删除集合'),
                Binding('a', 'add', '添加引用'), Binding('x', 'remove', '移除引用'), Binding('w', 'workspace', '用于新工作区'),
                Binding('right_square_bracket', 'next_page', '更多条目')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes='split'):
            yield make_table('集合', '条目', '更新', id='coll-table')
            yield make_table('来源', '类型', '状态', id='coll-items')
        yield Static('', classes='detail', id='coll-detail')
        yield Static('n 新建 · e 重命名 · d 删除集合 · a 添加引用 · x 移除引用 · w 以此集合新建工作区（调查分析）· ] 更多条目',
                     classes='hint')

    def on_mount(self):
        self.collections, self.detail, self.item_rows = {}, {}, {}
        self.query_one('#coll-detail').display = False

    def refresh_data(self):
        self.start_load(lambda c: c.api_get('/api/collections'), self.show, '正在读取集合…')

    def show(self, result):
        rows = items(result, 'collections')
        self.collections = {str(c.get('id')): c for c in rows}
        fill_table(self.query_one('#coll-table'), [
            (c.get('id'), (model.one_line(c.get('title'), 26), str(c.get('item_count', 0)), model.fmt_time(c.get('updated_at'))))
            for c in rows])
        if not rows:
            self.set_state('empty', '还没有证据集合。按 n 新建，或在文件/聊天页按 c 加入集合。')
            fill_table(self.query_one('#coll-items'), [])
        else:
            self.load_items()

    def selected(self):
        return selected_key(self.query_one('#coll-table'))

    def on_data_table_row_highlighted(self, event):
        if event.data_table.id == 'coll-table':
            self.load_items()
        else:
            self.show_item()

    def load_items(self, offset=0):
        cid = self.selected()
        if not cid:
            return

        def done(result):
            if self.selected() != cid:
                return
            result = result if isinstance(result, dict) else {}
            previous = self.detail.get(cid, {}).get('items', []) if offset else []
            result['items'] = previous + items(result, 'items')
            self.detail[cid] = result
            self.item_rows = {str(i.get('id')): i for i in result['items']}
            fill_table(self.query_one('#coll-items'), [
                (i.get('id'), (model.one_line((i.get('source') or {}).get('title') or i.get('source_id'), 30),
                               dict(SOURCE_TYPES).get(i.get('source_type'), i.get('source_type')).split('（')[0],
                               '可用' if i.get('status') == 'available' else '不可用')) for i in result['items']])
            self.show_item()
        self.app.request(lambda c: c.api_get(f'/api/collections/{seg(cid)}', offset=offset), done,
                         lambda m: self.set_state('error', f'读取集合失败：{m}'))

    def action_next_page(self):
        cid = self.selected()
        nxt = (self.detail.get(cid) or {}).get('next_offset')
        if nxt is None:
            self.set_state('info', '已显示全部条目。')
        else:
            self.load_items(nxt)

    def show_item(self):
        box = self.query_one('#coll-detail', Static)
        item = self.item_rows.get(selected_key(self.query_one('#coll-items')) or '')
        box.display = bool(item)
        if not item:
            return
        src = item.get('source') or {}
        lines = [f'[b #a78bfa]{esc(model.one_line(src.get("title") or item.get("source_id"), 80))}[/]',
                 f'{esc(item.get("source_type"))} {esc(item.get("source_id"))} · 加入于 {esc(model.fmt_time(item.get("added_at")))}']
        if item.get('status') == 'available':
            lines.append(f'{esc(model.platform_label(src.get("platform")))} · {esc(src.get("conversation") or "")} · '
                         f'{esc(src.get("sender") or src.get("publisher") or "")} · {esc(model.fmt_time(src.get("timestamp")))}')
            if src.get('excerpt'):
                lines.append(esc(model.one_line(src['excerpt'], 300)))
        else:
            lines.append(f'[#ef4444]{esc(item.get("reason") or "来源不可用")}[/]')
        box.update('\n'.join(lines))

    def action_create(self):
        def done(title):
            if title:
                self.act(lambda c: c.api_post('/api/collections', dict(title=title)),
                         lambda r: self.reload_with('info', f'已新建集合「{title}」。'), '正在新建…', '新建失败')
        self.app.push_screen(FormModal('新建证据集合', [Field('title', '集合名称')],
                                       lambda v: required(v['title'], '集合名称', 100), '创建'), done)

    def action_rename(self):
        cid = self.selected()
        if not cid:
            self.set_state('empty', '请先选择一个集合。')
            return
        old = (self.collections.get(cid) or {}).get('title') or ''

        def done(title):
            if title:
                self.act(lambda c: c.api_patch(f'/api/collections/{seg(cid)}', dict(title=title)),
                         lambda r: self.reload_with('info', f'已重命名为「{title}」。'), '正在保存…', '重命名失败')
        self.app.push_screen(FormModal('重命名集合', [Field('title', '集合名称', value=old)],
                                       lambda v: required(v['title'], '集合名称', 100), '保存'), done)

    def action_delete(self):
        cid = self.selected()
        if not cid:
            self.set_state('empty', '请先选择一个集合。')
            return
        c = self.collections.get(cid) or {}

        def confirmed(yes):
            if yes:
                self.act(lambda cl: cl.api_delete(f'/api/collections/{seg(cid)}'),
                         lambda r: self.reload_with('info', f'已删除集合「{c.get("title")}」；原始资料保留。'), '正在删除…', '删除失败')
        self.app.push_screen(ConfirmModal('删除证据集合', f'确定删除集合「{c.get("title")}」及其 {c.get("item_count", 0)} 个引用吗？\n'
                                          '只删除集合与引用，原始聊天、文件、图片和语音保留。', '删除', danger=True), confirmed)

    def action_add(self):
        cid = self.selected()
        if not cid:
            self.set_state('empty', '请先选择一个集合。')
            return

        def done(refs):
            if refs:
                self.act(lambda c: c.api_post(f'/api/collections/{seg(cid)}/items', dict(items=refs)),
                         lambda r: self.reload_with('info', f'新增 {r.get("added", 0)} 项。'), '正在核对来源…', '添加失败')
        self.app.push_screen(ref_form(), done)

    def action_remove(self):
        cid = self.selected()
        item = self.item_rows.get(selected_key(self.query_one('#coll-items')) or '')
        if not cid or not item:
            self.set_state('empty', '请先在右侧选择一个引用。')
            return
        label = model.one_line((item.get('source') or {}).get('title') or item.get('source_id'), 40)

        def confirmed(yes):
            if yes:
                self.act(lambda c: c.api_delete(f'/api/collections/{seg(cid)}/items/{seg(item.get("id"))}'),
                         lambda r: self.reload_with('info', '已移除引用；原始资料保留。'), '正在移除…', '移除失败')
        self.app.push_screen(ConfirmModal('移除引用', f'从集合中移除「{label}」？只移除引用，原始资料保留。', '移除', danger=True),
                             confirmed)

    def action_workspace(self):
        cid = self.selected()
        if not cid:
            self.set_state('empty', '请先选择一个集合。')
            return
        from .workspaces import create_workspace
        create_workspace(self, collection_id=cid, title=(self.collections.get(cid) or {}).get('title') or '')


KIND = dict(notice='群公告', essence='群精华')


class KnowledgeView(View):
    title = 'QQ 群资料'
    subtitle = '可选的群公告/精华同步；不发送消息、不下载公告图片'
    BINDINGS = [Binding('g', 'group', '选择群'), Binding('s', 'sync', '同步一次'), Binding('t', 'schedule', '定期同步开关'),
                Binding('c', 'collect', '加入集合'), Binding('right_square_bracket', 'next_page', '更多')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static('', classes='hint', id='kn-status')
        yield make_table('类型', '发布者', '时间', '内容', id='kn-table')
        yield Static('', classes='detail', id='kn-detail')
        yield Static('g 选择群 · s 同步一次 · t 启用/关闭每 6 小时同步 · c 加入集合 · ] 更多', classes='hint')

    def on_mount(self):
        self.status, self.sources, self.group, self.next_offset = {}, {}, '', None
        self.query_one('#kn-detail').display = False

    def refresh_data(self):
        group = self.group

        def fetch(c):
            return c.api_get('/api/knowledge/status'), c.api_get('/api/knowledge', conversation_id=group, offset=0)
        self.start_load(fetch, self.show, '正在读取群资料…')

    def show(self, result, append=False):
        status, page = result
        if status is not None:
            self.show_status(status)
        rows = items(page, 'sources')
        if not append:
            self.sources = {}
        for r in rows:
            self.sources[str(r.get('id'))] = r
        values = list(self.sources.values())
        fill_table(self.query_one('#kn-table'), [
            (r.get('id'), (KIND.get(r.get('kind'), r.get('kind') or '—'), model.one_line(r.get('publisher'), 12),
                           model.one_line(r.get('time'), 18), model.one_line(r.get('content'), 60))) for r in values])
        self.next_offset = page.get('next_offset') if isinstance(page, dict) else None
        if not values:
            self.set_state('empty', '没有已同步的群资料。按 g 选择群后按 s 同步。')
        self.show_detail()

    def show_status(self, status):
        self.status = status if isinstance(status, dict) else {}
        s = self.status
        names = {g.get('conversation_id'): g.get('conversation') for g in s.get('groups') or []}
        periodic = {r.get('conversation_id') for r in s.get('sync') or [] if r.get('enabled')}
        text = [f'OneBot {"已配置" if s.get("configured") else "未配置（无法同步）"}',
                f'当前：{esc(names.get(self.group) or "全部群")}' + (' · 定期同步已启用' if self.group in periodic else ''),
                f'{len(periodic)} 个群启用定期同步']
        if s.get('running'):
            text.append('[#eab308]同步运行中[/]')
        elif s.get('error'):
            text.append(f'[#ef4444]{esc(model.one_line(s["error"], 60))}[/]')
        self.query_one('#kn-status', Static).update(' · '.join(text) + f'\n[#7f8a9a]{esc(model.one_line(s.get("note"), 140))}[/]')

    def selected(self):
        return self.sources.get(selected_key(self.query_one('#kn-table')) or '')

    def on_data_table_row_highlighted(self, event):
        self.show_detail()

    def on_data_table_row_selected(self, event):
        r = self.selected()
        if r:
            self.app.push_screen(TextModal(f'{KIND.get(r.get("kind"), "")}：{r.get("conversation") or ""}',
                                           str(r.get('content') or ''), esc(f'{r.get("publisher") or ""} · {r.get("time") or ""}')))

    def show_detail(self):
        box = self.query_one('#kn-detail', Static)
        r = self.selected()
        box.display = bool(r)
        if r:
            extra = f' · {r["image_count"]} 张图片未下载' if r.get('image_count') else ''
            box.update(f'[b #a78bfa]{esc(KIND.get(r.get("kind"), ""))}[/] {esc(r.get("citation_id") or "")} · '
                       f'{esc(r.get("conversation") or "")} · {esc(r.get("provenance") or "")}{esc(extra)}\n'
                       f'{esc(model.one_line(r.get("content"), 400))}')

    def action_next_page(self):
        if self.next_offset is None:
            self.set_state('info', '已显示全部。')
            return
        offset, group = self.next_offset, self.group
        self.act(lambda c: c.api_get('/api/knowledge', conversation_id=group, offset=offset),
                 lambda page: self.show((None, page), append=True), '正在读取更多…', '读取失败')

    def action_group(self):
        groups = self.status.get('groups') or []
        choices = [('全部群', '')] + [(model.one_line(g.get('conversation') or g.get('conversation_id'), 40),
                                      g.get('conversation_id')) for g in groups]

        def picked(cid):
            if cid is not None:
                self.group = cid
                self.refresh_data()
        self.app.push_screen(ChoiceModal('选择 QQ 群', choices), picked)

    def require_group(self):
        if not self.group:
            self.set_state('empty', '请先按 g 选择一个具体的群。')
            return None
        if not self.status.get('configured'):
            self.set_state('info', '未配置 OneBot，无法读取群公告和精华。')
            return None
        return self.group

    def periodic(self, cid):
        return any(r.get('conversation_id') == cid and r.get('enabled') for r in self.status.get('sync') or [])

    def action_sync(self):
        cid = self.require_group()
        if not cid:
            return
        keep = self.periodic(cid)

        def started(result):
            def tick(status):
                self.show_status(status)
                if (status or {}).get('running'):
                    self.set_state('loading', '群资料同步运行中…')
                    return True
                self.reload_with('error' if status.get('error') else 'info',
                                 status.get('error') or '群资料同步完成；未返回的旧条目保留，不代表仍有效。')
                return False
            self.poll('knowledge', lambda c: c.api_get('/api/knowledge/status'), tick)
        self.act(lambda c: c.api_post('/api/knowledge/refresh', dict(conversation_id=cid, periodic=keep)),
                 started, '正在启动同步…', '同步失败')

    def action_schedule(self):
        cid = self.require_group()
        if not cid:
            return
        target = not self.periodic(cid)

        def confirmed(yes):
            if yes:
                self.act(lambda c: c.api_post('/api/knowledge/schedule', dict(conversation_id=cid, enabled=target)),
                         lambda r: self.reload_with('info', '已启用定期同步。' if target else '已关闭定期同步。'),
                         '正在保存…', '保存失败')
        self.app.push_screen(ConfirmModal('定期同步群资料', ('启用后，服务启动时及每 6 小时读取此群的公告与精华。'
                                                              if target else '关闭此群的定期同步？已同步的资料保留。'),
                                          '启用' if target else '关闭'), confirmed)

    def action_collect(self):
        r = self.selected()
        if not r:
            self.set_state('empty', '请先选择一条资料。')
            return
        add_to_collection(self, [dict(source_type='qq_' + str(r.get('kind')), source_id=str(r.get('id')))],
                          model.one_line(r.get('content'), 30))
