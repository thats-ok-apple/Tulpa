"""Narrow adapter into Tulpa's existing grant, MCP registry and audit boundary."""
import copy
import json
import threading
import time

from mcp import types
from .errors import Failure, public_error
from .tools import NAMES, REGISTRY, Service


class Adapter:
    def __init__(self, access, qq_scope):
        self.access, self.qq_scope = access, qq_scope
        self.lock = threading.Lock()
        self._service = None
        self._profile_lock = None

    @property
    def service(self):
        with self.lock:
            if self._service is None:
                from chatlocal.onebot import configuration
                from .share import QQ
                from chatlocal.snowluma_secure import private_directory, file_lock
                root=self.access.path.parent/'bilibili'
                private_directory(root)
                lock=file_lock(root/'service.lock',blocking=False)
                lock.__enter__()
                try:
                    instance=Service(root)
                    instance.shares.qq = QQ(instance.settings, config_provider=configuration)
                    instance.jobs.recover()
                    self._service=instance
                    self._profile_lock=lock
                except BaseException:
                    lock.__exit__(None,None,None)
                    raise
            return self._service

    def cancel(self, gid=None):
        if self._service is not None:
            self._service.jobs.cancel_all(gid)

    def close(self):
        if self._service is not None:
            self._service.close()
        if self._profile_lock is not None:
            self._profile_lock.__exit__(None,None,None)
            self._profile_lock=None

    @staticmethod
    def schemas(scope):
        if not scope.get('bilibili_read'):
            return []
        return [dict(name=r['name'], description=r['description'], parameters=copy.deepcopy(r['inputSchema']))
                for r in REGISTRY if r['name']!='share_video_to_qq_group' or scope.get('bilibili_share')]

    def call(self, grant, name, args, cancel):
        started, call_id = time.monotonic(), None
        status = 'error'
        def guard():
            from chatlocal.mcp_access import AccessDenied
            try:
                current = self.access.ensure_revision(grant)
            except AccessDenied:
                raise Failure('authorization_changed','此连接已停用、撤销或修改权限；旧任务不可继续。') from None
            if not current['scope'].get('bilibili_read'):
                raise Failure('not_authorized', '此连接未授权 B站只读能力。')
            return current
        def share_policy(account, group):
            if cancel.is_set():
                raise Failure('cancelled', '调用已取消，未继续发送。')
            current = guard()
            from chatlocal.mcp_access import AccessDenied
            try:
                _, bound = self.qq_scope.identity(current, 'bilibili_share')
                if account != bound:
                    raise Failure('qq_account_mismatch', '请求 QQ 与此连接绑定账号不符。')
                self.qq_scope.target(current, f'{account}:group:{group}')
            except AccessDenied:
                raise Failure('group_not_authorized','此 QQ 群、当前日期或视频分享不在连接授权中。') from None
        try:
            guard()
            if cancel.is_set():
                raise Failure('cancelled', '调用已取消。')
            call_id = self.access.begin_call(grant['id'], name)
            owner = f'{grant["id"]}:{grant.get("revision",0)}'
            data = self.service.call(name, args, owner=owner, guard=guard, share_policy=share_policy)
            if name=='get_bilibili_status' and data.get('ok'):
                scope = grant['scope']
                data['tools'] = [r['name'] for r in self.schemas(scope)]
                data['qq_share'] = dict(authorized=bool(scope.get('bilibili_share')),
                    account=scope.get('chat_account') if scope.get('bilibili_share') else None,
                    conversations=[json.loads(p)[1] for p in scope['conversations'] if json.loads(p)[0]=='qq'] if scope.get('bilibili_share') else [],
                    all_groups=bool(scope.get('bilibili_share') and not scope['conversations']))
            # A completed write must retain its receipt even if the caller disconnects.
            if name!='share_video_to_qq_group' and cancel.is_set():
                raise Failure('cancelled', '调用已取消。')
            status = data.get('status', 'ok' if data.get('ok') else 'error')
        except Exception as exc:
            data = public_error(exc)
        finally:
            if call_id is not None:
                self.access.finish_call(call_id, status, time.monotonic()-started)
        data = copy.deepcopy(data)
        images = []
        for frame in data.get('result', {}).get('frames', []):
            images.append(types.ImageContent(type='image',data=frame.pop('data'),mimeType=frame['mime_type']))
        return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(data,ensure_ascii=False)),*images],
            structuredContent=data, isError=not data.get('ok',False) or data.get('status') in ('failed','uncertain'))
