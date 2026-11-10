"""Headless checks for 实例管理: hub/child client separation and safe switching. Fake clients only."""
import asyncio
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from check_terminal_tui import FakeClient, SECRET, screen_text, settle  # noqa: E402
from chatlocal.tui.app import TerminalError, TulpaApp  # noqa: E402
from textual.widgets import Input  # noqa: E402

SIZE = (110, 32)


class ChildClient(FakeClient):
    def __init__(self, descriptor):
        super().__init__()
        self.descriptor = descriptor
        self.base_url = descriptor['url']
        self.closed = False
        self.grants = []  # A clean instance: nothing copied from the hub.

    def get(self, path, **params):
        result = super().get(path, **params)
        if path == '/overview':
            result.update(version='child-test', instance=dict(id=self.descriptor['id'], name=self.descriptor['name']))
        return result

    def close(self):
        self.closed = True


class HubClient(FakeClient):
    """The original backend: its own data plus the /instances management endpoints."""

    def __init__(self):
        super().__init__()
        self.instances = {'default': self.descriptor('default', '', 7861, 18777, running=True, default=True)}
        self.gate = None  # threading.Event: blocks the next /poll call until set.

    @staticmethod
    def descriptor(iid, name, port, mcp_port, running=False, default=False):
        return dict(id=iid, name=name, root=f'/srv/tulpa/{iid}', management_port=port, mcp_port=mcp_port,
                    url=f'http://127.0.0.1:{port}', token_file=f'/srv/tulpa/{iid}/terminal-admin.token',
                    default=default, running=running, state='running' if running else 'stopped', error='',
                    mcp=dict(enabled=False, running=False, port=mcp_port))

    def get(self, path, **params):
        if path == '/instances':
            self.record('GET', path)
            return dict(items=list(self.instances.values()), current_id='default')
        if path == '/poll':
            self.record('GET', path)
            return dict(ok=True)
        return super().get(path, **params)

    def post(self, path, body=None):
        if path == '/instances':
            self.record('POST', path, body)
            if body['id'] in self.instances:
                raise TerminalError('实例 ID 已存在。', status_code=409)
            port = body.get('management_port') or 7862 + len(self.instances) - 1
            mcp_port = body.get('mcp_port') or 18778 + len(self.instances) - 1
            self.instances[body['id']] = self.descriptor(body['id'], body['name'], port, mcp_port)
            return self.instances[body['id']]
        parts = path.split('/')
        if len(parts) == 4 and parts[1] == 'instances' and parts[3] in ('start', 'stop'):
            self.record('POST', path, body)
            d = self.instances[parts[2]]
            if parts[3] == 'stop' and d['default']:
                raise TerminalError('默认实例不能停止。', status_code=400)
            d.update(running=parts[3] == 'start', state='running' if parts[3] == 'start' else 'stopped')
            return d
        return super().post(path, body)

    def put(self, path, body):
        if path.startswith('/instances/'):
            self.record('PUT', path, body)
            d = self.instances[path.split('/')[2]]
            d['name'] = body['name']
            return d
        return super().put(path, body)


def make_app(initial=None, hub=None):
    hub = hub or HubClient()
    children = []

    def factory(descriptor):
        assert 'token' not in descriptor, 'descriptors never carry token contents'
        child = ChildClient(descriptor)
        children.append(child)
        return child
    return hub, children, TulpaApp(client=hub, instance_client_factory=factory, initial_instance=initial)


def header(app):
    return str(app.query_one('#header').render())


def select_row(app, iid):
    table = app.query_one('#inst-table')
    table.focus()
    keys = list(app.page('instances').descriptors)
    table.move_cursor(row=keys.index(iid))


async def open_instances(app, pilot):
    await pilot.press('escape', 'f2')
    await settle(app, pilot)
    assert app.current == 'instances', app.current


async def create_and_start(app, pilot, iid='deepseek', name='大肥鱼'):
    await pilot.press('n')
    await pilot.pause()
    assert type(app.screen).__name__ == 'FormModal'
    app.screen.query_one('#f-id', Input).value = iid
    app.screen.query_one('#f-name', Input).value = name
    await pilot.press('ctrl+s')
    await settle(app, pilot)
    select_row(app, iid)
    await pilot.press('s')
    await settle(app, pilot)


async def check_create_start_switch():
    hub, children, app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert '默认实例' in header(app), header(app)
        await open_instances(app, pilot)
        assert ('GET', '/instances', None) in hub.calls
        assert '7861' in screen_text(app) and '18777' in screen_text(app)
        # Validation: bad id and the reserved default id never reach the hub.
        await pilot.press('n')
        await pilot.pause()
        app.screen.query_one('#f-id', Input).value = 'default'
        app.screen.query_one('#f-name', Input).value = 'x'
        await pilot.press('ctrl+s')
        await pilot.pause()
        assert type(app.screen).__name__ == 'FormModal' and '保留' in str(app.screen.query_one('#form-error').render())
        await pilot.press('escape')
        await pilot.pause()
        await create_and_start(app, pilot)
        posted = [b for m, p, b in hub.calls if (m, p) == ('POST', '/instances')]
        assert posted == [dict(id='deepseek', name='大肥鱼')], posted  # Empty ports are left to the hub.
        assert ('POST', '/instances/deepseek/start', {}) in hub.calls
        assert hub.instances['deepseek']['running'] and not children, 'starting must not switch silently'
        assert app.instance_id == 'default'
        # Explicit switch.
        await pilot.press('w')
        await settle(app, pilot, 8)
        assert len(children) == 1 and app.instance_id == 'deepseek'
        child = children[0]
        assert child.base_url == 'http://127.0.0.1:7862'
        assert '大肥鱼' in header(app) and app.conn_state == 'online', header(app)
        assert '▶ 当前' in screen_text(app)
        # Normal pages now use the child; the hub is not asked for them.
        hub_before = list(hub.paths())
        await pilot.press('escape', '3')
        await settle(app, pilot)
        assert '/grants' in child.paths('GET') and hub.paths() == hub_before
        assert app.query_one('#grant-table').row_count == 0, 'grants are instance-local'
        # Management stays on the hub even with the child selected.
        await open_instances(app, pilot)
        await pilot.press('r')
        await settle(app, pilot)
        assert hub.paths().count('/instances') >= 3 and '/instances' not in child.paths()
        select_row(app, 'deepseek')
        await pilot.press('e')
        await pilot.pause()
        app.screen.query_one('#f-name', Input).value = '小肥鱼'
        await pilot.press('ctrl+s')
        await settle(app, pilot)
        assert ('PUT', '/instances/deepseek', dict(name='小肥鱼')) in hub.calls
        assert '小肥鱼' in header(app), header(app)
        # Switching back reuses the original hub client and closes the retired child.
        select_row(app, 'default')
        await pilot.press('w')
        await settle(app, pilot, 8)
        assert app.instance_id == 'default' and app.client() is hub and child.closed
        assert '默认实例' in header(app)
        assert SECRET not in screen_text(app)


async def check_default_stop_and_child_stop():
    hub, children, app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await open_instances(app, pilot)
        select_row(app, 'default')
        await pilot.press('x')
        await settle(app, pilot)
        assert type(app.screen).__name__ != 'ConfirmModal'
        assert '不能' in str(app.query_one('#instances-state').render())
        assert not [p for p in hub.paths('POST') if p.endswith('/stop')]
        await create_and_start(app, pilot)
        select_row(app, 'deepseek')
        await pilot.press('x')
        await pilot.pause()
        assert type(app.screen).__name__ == 'ConfirmModal'
        body = str(app.screen.query_one('.dialog-body').render())
        assert '大肥鱼' in body and 'SnowLuma' in body, body
        await pilot.press('n')  # Cancel: nothing sent.
        await settle(app, pilot)
        assert not [p for p in hub.paths('POST') if p.endswith('/stop')]
        await pilot.press('x')
        await pilot.pause()
        await pilot.press('y')
        await settle(app, pilot)
        assert ('POST', '/instances/deepseek/stop', {}) in hub.calls
        assert not hub.instances['deepseek']['running'] and app.conn_state == 'online'


async def check_stale_results_and_polls():
    hub, children, app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await open_instances(app, pilot)
        await create_and_start(app, pilot)
        # A poll on an old-instance page and a request still waiting for the hub.
        overview = app.page('overview')
        overview.poll('stale', lambda c: c.get('/poll'), lambda r: True, interval=0.05)
        gate, used, delivered = threading.Event(), [], []

        def slow(client):
            gate.wait(5)
            used.append(client)
            return client.get('/overview')
        app.request(slow, ok=delivered.append, failed=delivered.append)
        await pilot.pause()
        select_row(app, 'deepseek')
        app.page('instances').action_switch()
        gate.set()
        await settle(app, pilot, 8)
        child = children[0]
        assert used == [hub], 'a request is bound to the client chosen at submission'
        assert delivered == [], 'results of the old instance are discarded after a switch'
        assert app.page('overview') is not overview and overview.parent is None, 'pages are recreated'
        polls = hub.paths().count('/poll')
        await asyncio.sleep(0.3)
        await settle(app, pilot)
        assert hub.paths().count('/poll') == polls, 'old-instance polls stop with their page'
        assert '/poll' not in child.paths()
        assert not child.closed and app.client() is child


async def check_selected_instance_stop_and_restart():
    hub, children, app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await open_instances(app, pilot)
        await create_and_start(app, pilot)
        await pilot.press('w')
        await settle(app, pilot, 8)
        child = children[0]
        app.action_goto('grants')
        await settle(app, pilot)
        old_page = app.page('grants')
        # A response still in flight when the stop receipt arrives cannot restore online.
        gate, delivered = threading.Event(), []
        app.request(lambda c: (gate.wait(5), {'ok': True})[1], ok=delivered.append)
        await pilot.pause()
        child.offline = True
        stopped = hub.post('/instances/deepseek/stop', {})
        app.instances_listed({'deepseek': stopped})
        gate.set()
        await settle(app, pilot, 8)
        assert app.conn_state == 'offline' and '离线' in header(app)
        assert app.query_one('#offline').display and not app.query_one('#content').display
        assert app.page('grants') is not old_page and not delivered
        await open_instances(app, pilot)
        assert app.query_one('#content').display
        assert app.conn_state == 'offline'
        # Name-only responses and another instance's state cannot affect this client's health.
        app.instances_listed({'deepseek': {'id': 'deepseek', 'name': '改名'}, 'other': {'running': True}})
        assert app.conn_state == 'offline'
        child.offline = False
        started = hub.post('/instances/deepseek/start', {})
        app.instances_listed({'deepseek': started})
        await settle(app, pilot, 8)
        assert app.conn_state == 'online' and app.client() is child
        app.action_goto('grants')
        await settle(app, pilot)
        assert app.query_one('#content').display and app.page('grants').loaded


async def check_child_offline_recovery():
    hub, children, app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await open_instances(app, pilot)
        await create_and_start(app, pilot)
        await pilot.press('w')
        await settle(app, pilot, 8)
        child = children[0]
        child.offline = True
        await pilot.press('escape', '1', 'r')
        await settle(app, pilot)
        assert app.conn_state == 'offline' and app.query_one('#offline').display
        assert '大肥鱼' in header(app) and 'F2' in str(app.query_one('#offline').render())
        # 实例管理 stays usable through the hub while the child is down.
        await pilot.press('f2')
        await settle(app, pilot)
        assert app.current == 'instances' and app.query_one('#content').display
        assert not app.query_one('#offline').display
        assert app.query_one('#inst-table').row_count == 2
        # A hub failure is reported on the page without touching the instance state.
        hub.offline = True
        await pilot.press('r')
        await settle(app, pilot)
        assert '主后台' in str(app.query_one('#instances-state').render())
        assert app.instance_id == 'deepseek'
        hub.offline = False
        await pilot.press('r')
        await settle(app, pilot)
        select_row(app, 'default')
        await pilot.press('w')
        await settle(app, pilot, 8)
        assert app.instance_id == 'default' and app.conn_state == 'online' and child.closed
        await pilot.press('escape', '3')
        await settle(app, pilot)
        assert app.query_one('#content').display and app.query_one('#grant-table').row_count == 1


async def check_legacy_overview_and_keys():
    """An older fake without /instances or overview.instance still works; existing keys are intact."""
    client = FakeClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert '默认实例' in header(app) and app.conn_state == 'online'
        await pilot.press('escape', 'f2')
        await settle(app, pilot)
        assert app.current == 'instances' and app.conn_state == 'online'
        await pilot.press('escape', '2')
        await settle(app, pilot)
        assert app.current == 'sl'
        # A dialog blocks global navigation, F2 included.
        from chatlocal.tui.modals import ConfirmModal
        app.push_screen(ConfirmModal('t', 'b'))
        await pilot.pause()
        await pilot.press('f2')
        await settle(app, pilot)
        assert app.current == 'sl' and type(app.screen).__name__ == 'ConfirmModal'
        await pilot.press('escape')
        await settle(app, pilot)
        # Listing on an older backend fails safely on the page only.
        await pilot.press('escape', 'f2')
        await settle(app, pilot)
        assert '主后台' in str(app.query_one('#instances-state').render()) and app.conn_state == 'online'

def running_child(hub, iid='deepseek', name='大肥鱼', port=7862, mcp_port=18778):
    hub.instances[iid] = hub.descriptor(iid, name, port, mcp_port, running=True)
    return hub.instances[iid]


async def check_poll_gap_during_rebuild():
    """Between a switch and the page rebuild, old timers must not reach the new client."""
    hub = HubClient()
    child_d = running_child(hub)
    hub, children, app = make_app(hub=hub)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        overview = app.page('overview')
        overview.poll('stale', lambda c: c.get('/poll'), lambda r: True, interval=0.05)
        await settle(app, pilot)
        assert hub.paths().count('/poll') >= 1
        await app._rebuild_lock.acquire()  # Hold the rebuild so the old pages stay mounted.
        try:
            assert app.switch_instance(child_d) and app._rebuilding
            hub_polls = hub.paths().count('/poll')
            for _ in range(6):  # Several poll intervals: the old timer fires while mounted.
                await asyncio.sleep(0.06)
                await pilot.pause()
            assert overview.parent is not None, 'old page is still mounted during the gap'
            overview.refresh_data()  # A stale control acting directly is dropped too.
            await pilot.pause()
            child = children[0]
            assert '/poll' not in child.paths(), child.paths()
            assert child.paths().count('/overview') <= 1, child.paths()  # Only the app's own ping.
            assert hub.paths().count('/poll') == hub_polls, 'old poll stopped instead of retargeting'
        finally:
            app._rebuild_lock.release()
        await settle(app, pilot, 8)
        assert not app._rebuilding and app.page('overview') is not overview
        assert app.conn_state == 'online' and '大肥鱼' in header(app)
        await asyncio.sleep(0.2)
        await settle(app, pilot)
        assert '/poll' not in child.paths()


async def check_rapid_switches():
    hub = HubClient()
    child_d = running_child(hub)
    hub, children, app = make_app(hub=hub)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        default_d = hub.instances['default']
        for d in (child_d, default_d, child_d, default_d, child_d):
            assert app.switch_instance(d)
        await settle(app, pilot, 10)
        assert len(children) == 3 and app.client() is children[-1]
        assert all(c.closed for c in children[:-1]) and not children[-1].closed
        assert not app._rebuilding and app.conn_state == 'online' and '大肥鱼' in header(app)
        await pilot.press('escape', '3')
        await settle(app, pilot)
        assert '/grants' in children[-1].paths() and '/grants' not in hub.paths()
        assert app.query_one('#grant-table').row_count == 0
        assert len(app.query('#content > #overview')) == 1, 'exactly one set of pages after rebuilds'


async def check_initial_child_instance():
    """Opened from a child directory: the child is selected from the start; the hub manages."""
    hub = HubClient()
    child_d = running_child(hub)
    hub, children, app = make_app(initial=child_d, hub=hub)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert len(children) == 1 and app.client() is children[0] and app.hub_client() is hub
        assert '/overview' in children[0].paths() and '/overview' not in hub.paths(), hub.paths()
        assert '大肥鱼' in header(app) and '默认实例' not in header(app), header(app)
        assert '7862' in header(app)
        await open_instances(app, pilot)
        assert '/instances' in hub.paths() and '/instances' not in children[0].paths()
        assert '▶ 当前' in screen_text(app)
        select_row(app, 'default')
        await pilot.press('w')
        await settle(app, pilot, 8)
        assert app.client() is hub and children[0].closed and '默认实例' in header(app)
        await pilot.press('escape', '3')
        await settle(app, pilot)
        assert app.query_one('#grant-table').row_count == 1


def check_name_limit():
    from chatlocal.tui.instances import create_body
    assert create_body(dict(id='a1', name='名' * 60))['name'] == '名' * 60
    try:
        create_body(dict(id='a1', name='名' * 61))
    except ValueError as exc:
        assert '60' in str(exc)
    else:
        raise AssertionError('names over 60 characters are rejected')


def main():
    check_name_limit()
    print('ok check_name_limit')
    for check in (check_create_start_switch, check_default_stop_and_child_stop, check_stale_results_and_polls,
                  check_selected_instance_stop_and_restart,
                  check_child_offline_recovery, check_legacy_overview_and_keys, check_poll_gap_during_rebuild,
                  check_rapid_switches, check_initial_child_instance):
        asyncio.run(check())
        print('ok', check.__name__)
    print('tui instance checks passed')


if __name__ == '__main__':
    main()
