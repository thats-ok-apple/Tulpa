"""Empty-history MCP acceptance with loopback OneBot/WS fixtures only."""
import asyncio
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_mcp_reactions import ReactionBot, Events, ROOT
from check_mcp_chat import eventually
from check_fixture_support import isolated_env
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_access import AccessDenied, history_platforms
from chatlocal.mcp_tools import READ_TOOLS
from chatlocal.store import Store
from chatlocal.onebot import invalidate_availability
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def wire(service, token):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=20) as http:
        async with streamable_http_client(service.status()['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                names={t.name for t in (await client.list_tools()).tools}
                assert not names & READ_TOOLS
                assert {'list_chat_groups','start_chat_session','wait_chat_messages'}<=names
                result=(await client.call_tool('list_chat_groups',{})).structuredContent
                assert result['items'][0]['conversation_id']=='111:group:222',result


def main():
    events=Events();ReactionBot.sent=[];ReactionBot.writes=[];ReactionBot.intercept=None;ReactionBot.login=111
    server=ThreadingHTTPServer(('127.0.0.1',0),ReactionBot)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    ports={events.port,server.server_port};connect=socket.create_connection
    def restrict(address,*args,**kwargs):
        assert address[0] in ('127.0.0.1','localhost','::1') and address[1] in ports,address
        return connect(address,*args,**kwargs)
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-no-history-') as temp,patch.dict('os.environ',isolated_env(),clear=True),patch('socket.create_connection',restrict):
            root=Path(temp);store=Store(root/'chats.sqlite3')
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{server.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                service=install_mcp_routes(app:=FastAPI(),store);access=service.access;tools=service.tools
                with TestClient(app) as ui:
                    invalidate_availability()
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    ports.add(port);headers={'X-ChatWeave-UI':'1'}
                    assert ui.put('/api/mcp',json=dict(enabled=True,port=port),headers=headers).status_code==200
                    body=dict(name='no-history',platforms=['qq'],history_platforms=[],chat=True,send=True,
                              chat_images=True,chat_sticker_send=True,chat_reactions=True,
                              conversations=[['qq','111:group:222']])
                    response=ui.post('/api/mcp/connections',json=body,headers=headers)
                    assert response.status_code==200,response.text
                    credential=response.json();token=credential['token']
                    def call(name,**args):return tools.call(token,name,args,threading.Event()).structuredContent
                    with store.connect() as db:assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==0
                    for historical in (None,'qq',['wechat']):
                        assert ui.post('/api/mcp/connections',json=body|dict(history_platforms=historical),headers=headers).status_code==400
                    assert ui.post('/api/mcp/connections',json=body|dict(history_platforms=['qq']),headers=headers).status_code==400
                    assert ui.put('/api/mcp/connections/'+credential['id'],json=dict(media=True),headers=headers).status_code==400
                    with patch.object(store,'connect',side_effect=AssertionError('Live-only connection opened historical store')):
                        grant=access.authorize(token);assert history_platforms(grant['scope'])==[]
                        names={x['name'] for x in tools.schemas(grant)}
                        assert not names & (READ_TOOLS|{'read_message','read_image','prepare_file','get_group_knowledge'})
                        assert {'read_chat_image','send_chat_sticker','react_to_chat_message'}<=names
                        asyncio.run(wire(service,token))
                        result=call('start_chat_session',conversation_id='111:group:222',persona='只观察合成消息。',idempotency_key='live-empty-history')
                        assert 'session' in result,result
                        sid=result['session']['id']
                        eventually(lambda:len(events.connections)>0)
                        events.send(dict(post_type='message',message_type='group',self_id=111,group_id=222,
                                         message_id=-987,user_id=333,time=time.time(),sender=dict(nickname='Synthetic member'),
                                         message=[dict(type='text',data=dict(text='synthetic live input'))]))
                        result=call('wait_chat_messages',session_id=sid,timeout_seconds=3,quiet_seconds=0)
                        assert result['messages'][-1]['content']=='synthetic live input',result
                        assert call('get_data_status').get('error_code'), 'Hidden history tool was still callable'
                        assert call('stop_chat_session',session_id=sid)['event']=='stopped'
                    with store.connect() as db:
                        db.execute("UPDATE local_change_meta SET epoch='changed-fixture' WHERE id=1")
                    assert access.authorize(token)['scope']['history_platforms']==[]
                    ReactionBot.login=999;invalidate_availability()
                    assert call('start_chat_session',conversation_id='111:group:222',persona='观察',idempotency_key='wrong-account').get('error_code')
                    ReactionBot.login=111;invalidate_availability()
                    access.revoke(credential['id'])
                    try:access.authorize(token);raise AssertionError('Revocation ignored')
                    except AccessDenied:pass
                    # A mixed grant authorizes WeChat history, not imported QQ messages.
                    mixed=dict(scope=dict(platforms=['qq','wechat'],history_platforms=['wechat'],conversations=[],start='',end=''))
                    assert access.plan(mixed,history=True).platforms==['wechat']
                    legacy=dict(scope=dict(platforms=['qq'],conversations=[],start='',end=''))
                    assert history_platforms(legacy['scope'])==['qq']
                    assert access.plan(legacy,history=True).platforms==['qq']
                    assert not ReactionBot.sent and not ReactionBot.writes
        print('PASS: empty history -> UI grant -> real MCP discovery -> WS input -> stop; hidden history denied; scope/account/revoke/legacy boundaries; zero QQ writes')
    finally:
        events.close();server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':main()
