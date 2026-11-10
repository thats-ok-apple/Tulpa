"""Textual interaction checks for Docker consent, login, logs and stop/retry."""
import asyncio
import copy
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from check_terminal_tui import FullClient, settle, visible_text
from chatlocal.tui.app import TulpaApp
from chatlocal.tui.forms import FormModal
from chatlocal.tui.managed import LoginModal, M
from chatlocal.tui.modals import ConfirmModal, TextModal
from chatlocal.snowluma_docker import SCOPE
from chatlocal.snowluma_managed import load_manifest
from textual.widgets import Button, Checkbox, OptionList, TextArea

PASSWORD='fixture-VNC-password'


class Client(FullClient):
    def __init__(self):
        super().__init__()
        self.managed=dict(backend='local',supported_backends=['local','docker'],enabled=False,phase='idle',
            phase_label='请选择托管方式',version='1.14.22',available=True,consent_current=False,qq_ready=False,
            login_available=False)
        self.fail_inspect=False
    def api_get(self,path,**params):
        if path==M:
            self.record('GET',path); return copy.deepcopy(self.managed)
        return super().api_get(path,**params)
    def api_post(self,path,body=None):
        if not path.startswith(M+'/'): return super().api_post(path,body)
        self.record('POST',path,body)
        action=path.rsplit('/',1)[-1]
        if action=='docker-inspect':
            if self.fail_inspect:return dict(ok=False,code='docker_missing',message='请先安装 Docker')
            return dict(ok=True,mode='docker',version='1.14.22',image='motricseven7/snowluma:v1.14.22',
                platform='linux/arm64',selection_id='test-selection',consent_fingerprint='test-fingerprint',
                agreements=load_manifest()['agreements'],scope=SCOPE,message='测试扫码托管')
        if action=='docker-start':
            assert body==dict(accepted=True,selection_id='test-selection',fingerprint='test-fingerprint'),body
            self.managed.update(backend='docker',enabled=True,phase='waiting_login',phase_label='等待扫码',consent_current=True,
                container_name='fixture-container',container_id='a'*64,image='motricseven7/snowluma:v1.14.22',
                novnc_url='http://127.0.0.1:26081/?resize=scale',webui_url='http://127.0.0.1:25099/',login_available=True)
        elif action=='docker-login':
            return dict(ok=True,password=PASSWORD,novnc_url='http://127.0.0.1:26081/?resize=scale&token=SHOULD_BE_REMOVED',
                        webui_url='http://127.0.0.1:25099/')
        elif action=='docker-logs':return dict(ok=True,container_name='fixture-container',lines=['QQ 启动中','[含凭据的日志已隐藏]'])
        elif action in ('cancel','revoke'):
            self.managed.update(enabled=False,qq_ready=False,login_available=False,phase='cancelled' if action=='cancel' else 'revoked')
            if action=='revoke':self.managed['consent_current']=False
        elif action=='retry':self.managed.update(enabled=True,phase='ready',phase_label='QQ 已就绪',qq_ready=True,account='12345',login_available=True)
        else:raise AssertionError(action)
        return dict(ok=True,**self.managed)


async def check(size):
    client=Client(); app=TulpaApp(client=client)
    async with app.run_test(size=size) as pilot:
        await settle(app,pilot)
        app.action_goto('managed'); await settle(app,pilot)
        view=app.page('managed')
        client.fail_inspect=True
        await pilot.press('d'); await settle(app,pilot)
        assert not isinstance(app.screen,FormModal)
        assert '安装 Docker' in str(view.state_line.render())
        client.fail_inspect=False
        await pilot.press('d'); await settle(app,pilot)
        assert isinstance(app.screen,FormModal)
        checkbox=app.screen.query_one('#f-accepted',Checkbox)
        assert checkbox.value is False
        submit=app.screen.query_one('#submit')
        assert submit.region.bottom <= size[1],submit.region
        await pilot.press('ctrl+s'); await settle(app,pilot)
        assert isinstance(app.screen,FormModal)
        assert not any(p==M+'/docker-start' for m,p,b in client.calls)
        checkbox.value=True
        await pilot.press('ctrl+s'); await settle(app,pilot)
        assert not isinstance(app.screen,FormModal)
        assert view.status['phase']=='waiting_login'
        assert view.polling('managed')
        await pilot.press('b'); await settle(app,pilot)
        assert isinstance(app.screen,LoginModal)
        modal=app.screen; password_box=modal.query_one('#login-password',TextArea)
        assert password_box.text==PASSWORD
        assert 'SHOULD_BE_REMOVED' not in str(modal.query_one('#login-url').render())
        assert 'resize=scale' in str(modal.query_one('#login-url').render())
        assert '-L 26081:127.0.0.1:26081' in str(modal.query_one('#login-ssh').render())
        assert modal.query_one('#close').region.bottom<=size[1]
        assert PASSWORD not in visible_text(app)
        assert PASSWORD not in str(view.status)
        await pilot.press('escape'); await settle(app,pilot)
        assert modal._password=='' and password_box.text==''
        await pilot.press('l'); await settle(app,pilot)
        assert isinstance(app.screen,TextModal)
        await pilot.press('escape'); await settle(app,pilot)
        await pilot.press('x'); await settle(app,pilot)
        assert isinstance(app.screen,ConfirmModal)
        assert 'QQ 和 SnowLuma 都会关闭' in app.screen.body_text
        assert '登录数据' in app.screen.body_text
        await pilot.press('y'); await settle(app,pilot)
        assert not view.status['enabled']
        await pilot.press('t'); await settle(app,pilot)
        assert view.status['phase']=='ready' and view.polling('managed')
        assert view.poll_interval==10
        # A restored account becoming offline must be displayed without navigation.
        client.managed.update(phase='reconnecting',phase_label='正在恢复',qq_ready=False)
        view.tick(dict(client.managed))
        await pilot.pause()
        assert view.poll_interval==3
        await pilot.press('R'); await settle(app,pilot)
        assert isinstance(app.screen,ConfirmModal)
        assert '按 d' in app.screen.body_text and '保留' in app.screen.body_text
        await pilot.press('escape'); await settle(app,pilot)
        view.stop_poll('managed')
        if size==(100,32):
            folder=ROOT/'.tmp'; folder.mkdir(exist_ok=True)
            app.save_screenshot(str(folder/'terminal-docker-managed.svg'))


def named_button(view, label):
    matches=[button for button in view.query(Button) if label in str(button.label)]
    assert len(matches)==1,(label,[str(button.label) for button in view.query(Button)])
    return matches[0]


async def check_visible_controls(size):
    """Reproduce finding Docker from SL connection, without knowing any Docker hotkey."""
    client=Client(); app=TulpaApp(client=client)
    async with app.run_test(size=size) as pilot:
        await settle(app,pilot)
        sidebar=app.query_one('#sidebar',OptionList)
        pages=[sidebar.get_option_at_index(i).id for i in range(sidebar.option_count)]
        assert pages.index('managed')==pages.index('sl')+1,pages
        await pilot.press('2'); await settle(app,pilot)
        assert app.current=='sl'

        async def click(view, label):
            button=named_button(view,label)
            assert not button.disabled,label
            assert button.region.width>0 and button.region.height>0,label
            assert button.region.x>=0 and button.region.right<=size[0],(label,button.region)
            assert button.region.y>=0 and button.region.bottom<size[1],(label,button.region)
            assert await pilot.click(f'#{button.id}'),(label,button.region)
            await settle(app,pilot)
            return button

        await click(app.page('sl'),'SL Docker 托管')
        assert app.current=='managed'
        view=app.page('managed')
        assert app.focused is named_button(view,'启用 Docker 托管')
        assert named_button(view,'扫码登录').disabled
        assert named_button(view,'查看日志').disabled
        assert not any(method=='POST' for method,_,_ in client.calls)
        folder=ROOT/'.tmp'; folder.mkdir(exist_ok=True)
        app.save_screenshot(str(folder/f'terminal-docker-controls-{size[0]}x{size[1]}.svg'))

        await click(view,'启用 Docker 托管')
        assert isinstance(app.screen,FormModal)
        assert app.screen.query_one('#f-accepted',Checkbox).value is False
        await pilot.press('escape'); await settle(app,pilot)
        assert not any(path==M+'/docker-start' for _,path,_ in client.calls)
        # Keyboard activation uses the same visible primary control.
        named_button(view,'启用 Docker 托管').focus()
        await pilot.press('tab','shift+tab','enter'); await settle(app,pilot)
        assert isinstance(app.screen,FormModal)
        await pilot.press('ctrl+s'); await settle(app,pilot)
        assert isinstance(app.screen,FormModal)
        assert not any(path==M+'/docker-start' for _,path,_ in client.calls)
        app.screen.query_one('#f-accepted',Checkbox).value=True
        await pilot.press('ctrl+s'); await settle(app,pilot)
        assert view.status['phase']=='waiting_login' and not view.status['qq_ready']
        assert not named_button(view,'停止 Docker 托管').disabled

        await click(view,'扫码登录')
        assert isinstance(app.screen,LoginModal)
        await pilot.press('escape'); await settle(app,pilot)
        await click(view,'查看日志')
        assert isinstance(app.screen,TextModal)
        await pilot.press('escape'); await settle(app,pilot)
        await click(view,'停止 Docker 托管')
        assert isinstance(app.screen,ConfirmModal)
        await pilot.press('escape'); await settle(app,pilot)
        assert view.status['enabled']
        assert not any(path==M+'/cancel' for _,path,_ in client.calls)
        await click(view,'停止 Docker 托管')
        await pilot.press('y'); await settle(app,pilot)
        assert not view.status['enabled'] and named_button(view,'扫码登录').disabled
        inspect_count=sum(path==M+'/docker-inspect' for _,path,_ in client.calls)
        await click(view,'启用 Docker 托管')
        assert not isinstance(app.screen,FormModal)
        assert view.status['phase']=='ready'
        assert sum(path==M+'/retry' for _,path,_ in client.calls)==1
        assert sum(path==M+'/docker-inspect' for _,path,_ in client.calls)==inspect_count

        await pilot.press('R'); await settle(app,pilot)
        assert isinstance(app.screen,ConfirmModal)
        await pilot.press('y'); await settle(app,pilot)
        assert not view.status['consent_current']
        await click(view,'启用 Docker 托管')
        assert isinstance(app.screen,FormModal)
        assert app.screen.query_one('#f-accepted',Checkbox).value is False
        await pilot.press('escape'); await settle(app,pilot)
        view.stop_poll('managed')

        before=len(client.calls)
        # Server-driven refreshes update the controls without issuing mutations.
        client.managed.update(enabled=True,phase='pulling',phase_label='准备镜像',container_id='',login_available=False)
        view.show(copy.deepcopy(client.managed))
        await pilot.pause()
        assert named_button(view,'扫码登录').disabled
        assert named_button(view,'查看日志').disabled
        view.stop_poll('managed')
        view.show(dict(backend='local',supported_backends=['local','docker'],enabled=True,
                       phase='ready',folder='/fixture/SnowLuma',consent_current=True))
        assert named_button(view,'启用 Docker 托管').disabled
        view.stop_poll('managed')
        view.show(dict(backend='local',supported_backends=['local'],enabled=False,phase='idle'))
        assert named_button(view,'启用 Docker 托管').disabled
        assert not any(method=='POST' for method,_,_ in client.calls[before:])


if __name__=='__main__':
    for size in ((80,24),(100,32)):
        asyncio.run(check(size)); print('PASS Docker TUI',size)
        asyncio.run(check_visible_controls(size)); print('PASS visible Docker controls',size)
