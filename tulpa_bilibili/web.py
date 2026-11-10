"""Local account settings only. Tools and grants live in Tulpa's unified MCP."""
from contextlib import asynccontextmanager
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

import anyio
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from chatlocal.snowluma_secure import file_lock
from .errors import Failure, public_error


async def bounded_body(request, limit=32768):
    try:
        length=int(request.headers.get('content-length','0'))
    except ValueError:
        raise HTTPException(400) from None
    if length<0 or length>limit:
        raise HTTPException(413)
    raw=bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk)>limit:
            raise HTTPException(413)
        raw.extend(chunk)
    return bytes(raw)


def install(app, adapter):
    # Lazy initialization keeps chat-only startup independent of Bilibili files.
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            try:
                yield
            finally:
                await anyio.to_thread.run_sync(adapter.close)
                if getattr(adapter, '_profile_lock', None):
                    adapter._profile_lock.__exit__(None,None,None)
                    adapter._profile_lock=None
    app.router.lifespan_context=lifespan

    def local(request,write=False):
        base=str(request.base_url).rstrip('/')
        if (request.client and request.client.host not in ('127.0.0.1','::1','testclient') or
            urlsplit(base).hostname not in ('127.0.0.1','localhost','testserver') or
            request.headers.get('origin') not in (None,base) or request.headers.get('sec-fetch-site')=='cross-site'):
            raise HTTPException(403)
        if write and (request.headers.get('x-tulpa-bilibili')!='1' or request.headers.get('content-type','').split(';')[0]!='application/json'):
            raise HTTPException(403)

    @app.get('/bilibili')
    async def page(request:Request):
        local(request)
        return HTMLResponse((Path(__file__).parent/'ui.html').read_text(encoding='utf-8'),headers={
            'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"})

    @app.get('/bilibili/ui.js')
    async def script(request:Request):
        local(request)
        return FileResponse(Path(__file__).parent/'ui.js',headers={'Cache-Control':'no-store'})

    @app.get('/bilibili/api/status')
    async def status(request:Request):
        local(request)
        return await anyio.to_thread.run_sync(lambda:adapter.service.status())

    @app.post('/bilibili/api/action')
    async def action(request:Request):
        local(request,True)
        try:
            body=json.loads(await bounded_body(request))
        except ValueError:
            raise HTTPException(400) from None
        if not isinstance(body,dict):
            raise HTTPException(400)
        act=body.get('action')
        try:
            service=adapter.service
            if act=='start_login' and body.get('consent') is True:
                url=await anyio.to_thread.run_sync(service.content.start_login)
                import segno
                import base64
                stream=io.BytesIO();segno.make(url).save(stream,kind='png',scale=6,border=3)
                return dict(qr_image='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())
            if act=='poll_login':
                return await anyio.to_thread.run_sync(service.content.poll_login)
            if act=='logout':
                service.jobs.cancel_all();service.content.qr=None
                await anyio.to_thread.run_sync(service.settings.clear_login)
                await anyio.to_thread.run_sync(service.store.clear_cache)
                return dict(state='logged_out')
            if act=='media_cookie' and body.get('consent') is True:
                value=body.get('cookie')
                if not isinstance(value,str) or len(value)>16384 or any(c in value for c in ('\r','\n')):
                    raise Failure('credential_invalid','媒体凭据格式无效。')
                service.jobs.cancel_all()
                await anyio.to_thread.run_sync(service.settings.set_cookie,value,'media')
                return dict(saved=True)
            if act=='clear_cache':
                service.jobs.cancel_all()
                await anyio.to_thread.run_sync(service.store.clear_cache)
                return dict(cleared=True)
            raise Failure('action_denied','此页仅管理 B站登录和媒体缓存；连接与权限请在统一授权页设置。')
        except Exception as exc:
            return JSONResponse(public_error(exc),status_code=400)
