"""MCP edition console: the same local configuration and authorized MCP transport."""
import argparse
import base64
import json
from pathlib import Path
import shlex
import sys

from .cli_runtime import Client, start, serve
from .cli_tools import credential, export_config, forget, invoke, reference, remember, tool_failed
from .config import ROOT
from .mcp_access import PERMISSION_FLAGS
from .snowluma_secure import atomic_bytes, private_directory
from .terminal_client import TerminalError
from .version import VERSION


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise TerminalError(message)


def parser():
    p = Parser(prog='Tulpa.CLI.exe', description='Tulpa MCP · 命令行配置与调用')
    p.add_argument('--version', action='version', version='Tulpa MCP '+VERSION)
    p.add_argument('--json', action='store_true', help='机器可读 JSON；错误退出码非零')
    subs = p.add_subparsers(dest='command')
    for command, help_text in [('shell','打开交互终端'),('start','启动或复用本目录的 Tulpa'),
                               ('stop','彻底停止本目录的 Tulpa'),('serve','前台运行无窗口 MCP 后台'),
                               ('status','本机服务状态'),('personas','可用人格'),('groups','当前 QQ 群目录')]:
        subs.add_parser(command, help=help_text)
    mcp = subs.add_parser('mcp', help='MCP 服务').add_subparsers(dest='action', required=True)
    mcp.add_parser('enable').add_argument('--port', type=int)
    mcp.add_parser('disable')
    conn = subs.add_parser('connections', help='连接授权与凭据引用').add_subparsers(dest='action', required=True)
    conn.add_parser('list')
    conn.add_parser('permissions')
    template = conn.add_parser('template', help='输出可编辑的最小授权 JSON')
    template.add_argument('kind', choices=['bilibili','qq-chat','qq-history','wechat-history'])
    template.add_argument('--group', default='', help='完整 QQ 会话编号 ACCOUNT:group:GROUP')
    create = conn.add_parser('create', help='按 JSON 文件创建；Token 仅保存在本机凭据库')
    create.add_argument('--file', required=True, help='JSON 文件；- 读取标准输入')
    for action in ('enable','disable','revoke','delete','export','test'):
        command = conn.add_parser(action); command.add_argument('id')
    edit = conn.add_parser('edit'); edit.add_argument('id'); edit.add_argument('--file', required=True)
    imp = conn.add_parser('import', help='显式导入已有 MCP 配置，不扫描其他程序凭据')
    imp.add_argument('id'); imp.add_argument('--file', required=True); imp.add_argument('--server')
    tools = subs.add_parser('tools', help='列出指定连接实际可用的 MCP 工具及参数')
    tools.add_argument('--connection', required=True)
    call = subs.add_parser('call', help='调用真实 MCP 工具；不自动重试')
    call.add_argument('tool'); call.add_argument('--connection', required=True)
    call.add_argument('--args-file', help='JSON 参数文件；- 读取标准输入；默认空对象')
    sl = subs.add_parser('sl', help='SnowLuma 配置').add_subparsers(dest='action', required=True)
    for action in ('status','test','retry','cancel','revoke'):
        sl.add_parser(action)
    auto = sl.add_parser('configure', help='按当前协议指纹执行自动配置')
    auto.add_argument('--accept', required=True, help='sl status 返回的 consent_fingerprint，需用户同意')
    sl.add_parser('select').add_argument('choice_id')
    cfg = sl.add_parser('settings', help='复用现有 OneBot 设置接口')
    cfg.add_argument('--file', required=True)
    bili = subs.add_parser('bilibili', help='B站账号').add_subparsers(dest='action', required=True)
    for action in ('status','poll','logout'):
        bili.add_parser(action)
    login = bili.add_parser('login', help='在本机保存扫码图片；需要用户扫码')
    login.add_argument('--consent', action='store_true', required=True)
    learn = subs.add_parser('learning', help='按 QQ 和群管理已积累内容').add_subparsers(dest='action', required=True)
    learn.add_parser('accounts')
    groups = learn.add_parser('groups'); groups.add_argument('--account', required=True)
    library = learn.add_parser('library'); library.add_argument('--account', required=True); library.add_argument('--group', required=True)
    change = learn.add_parser('edit'); change.add_argument('--file', required=True, help='现有学习管理接口的 account/group/change JSON')
    data = subs.add_parser('data', help='可选历史资料读取；实时聊天无需执行').add_subparsers(dest='action', required=True)
    for action in ('status','accounts'):
        data.add_parser(action)
    for action in ('catalog','read'):
        data.add_parser(action).add_argument('--file', required=True)
    data.add_parser('job').add_argument('id')
    data.add_parser('cancel').add_argument('id')
    api = subs.add_parser('api', help='高级：调用现有本机配置接口')
    api.add_argument('method', choices=['GET','POST','PUT','DELETE']); api.add_argument('path')
    api.add_argument('--file'); api.add_argument('--param', action='append', default=[])
    return p


def read_json(path):
    if path == '-':
        raw = sys.stdin.read(512001)
    else:
        with Path(path).open(encoding='utf-8-sig') as stream:
            raw = stream.read(512001)
    if len(raw.encode('utf-8')) > 512000:
        raise TerminalError('JSON 文件超过 500 KB。')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TerminalError('JSON 须为对象。')
    return value


def template(kind, group=''):
    qq = kind.startswith('qq-'); wx = kind == 'wechat-history'
    body = dict(name='', modules=dict(qq=qq, wechat=wx, bilibili=kind=='bilibili'),
                platforms=['qq'] if qq else ['wechat'] if wx else [], history_platforms=[], conversations=[])
    if kind == 'bilibili':
        body['bilibili_read'] = True
    elif kind == 'qq-chat':
        body.update(send=True, chat=True)
        if not group:
            raise TerminalError('先用 groups 查询完整编号，再传 --group ACCOUNT:group:GROUP。')
        body['conversations'] = [['qq', group]]
    else:
        body['history_platforms'] = body['platforms'][:]
        body['all_conversations'] = True
    return body


def sanitized(value):
    secret_keys = {'token','http_token','ws_token','sender_token','events_token','api_key','cookie','cookies',
                   'authorization','password','admin_password','client_config','codex_config','qr_image'}
    if isinstance(value, dict):
        return {k: sanitized(v) for k,v in value.items() if k.lower() not in secret_keys}
    if isinstance(value, list):
        return [sanitized(v) for v in value]
    return value


def execute(args, client=None):
    if args.command == 'start':
        return start()
    if args.command == 'connections' and args.action == 'template':
        return template(args.kind, args.group)
    if args.command == 'connections' and args.action == 'permissions':
        return dict(permissions=list(PERMISSION_FLAGS), modules=['qq','wechat','bilibili'])
    client = client or Client()
    request = client.request
    if args.command == 'stop':
        return request('POST', '/api/cli/stop', {}, internal=True)
    if args.command == 'status':
        state = request('GET', '/api/mcp')
        return dict(version=VERSION, root=str(client.root), url=client.url, mcp={k:state[k] for k in ('enabled','running','port','url','error')},
                    connections=len(state['connections']), data_platforms=state['data_platforms'])
    if args.command in ('personas','groups'):
        return request('GET', '/api/mcp/'+('chat-personas' if args.command=='personas' else 'live-groups'))
    if args.command == 'mcp':
        state = request('GET', '/api/mcp')
        return request('PUT', '/api/mcp', dict(enabled=args.action=='enable', port=getattr(args,'port',None) or state['port']))
    if args.command == 'connections':
        if args.action == 'list':
            return dict(connections=request('GET', '/api/mcp')['connections'])
        if args.action == 'create':
            body = read_json(args.file)
            # Verify protected storage before asking the server to create a one-time credential.
            from .cli_tools import connection_vault
            store = connection_vault(client.root)
            private_directory(store.directory)
            result = request('POST', '/api/mcp/connections', body)
            try:
                remember(result['id'], result['token'], client.root)
            except Exception:
                # No external message was sent; prevent an inaccessible active grant.
                request('POST', '/api/mcp/connections/'+result['id']+'/revoke', {})
                raise TerminalError('凭据未能安全保存；新连接已撤销，请检查目录权限。') from None
            return sanitized(result)|dict(credential_saved=True)
        gid = reference(args.id); path = '/api/mcp/connections/'+gid
        if args.action in ('enable','disable'):
            return request('PUT', path+'/state', dict(enabled=args.action=='enable'))
        if args.action == 'revoke':
            return request('POST', path+'/revoke', {})
        if args.action == 'delete':
            result = request('DELETE', path, {})
            forget(gid, client.root)
            return result
        if args.action == 'edit':
            body = read_json(args.file)
            if 'expected_revision' not in body:
                row = next((r for r in request('GET','/api/mcp')['connections'] if r['id']==gid), None)
                if not row:
                    raise TerminalError('连接不存在。')
                body['expected_revision'] = row['revision']
            return request('PUT', path, body)
        if args.action == 'export':
            return export_config(client, gid)
        if args.action == 'test':
            return request('POST', '/api/mcp/test', dict(token=credential(gid, client.root)))
        if args.action == 'import':
            config = read_json(args.file).get('mcpServers', {})
            if args.server:
                selected = config.get(args.server)
            elif len(config) == 1:
                selected = next(iter(config.values()))
            else:
                raise TerminalError('配置包含多个连接，请用 --server 指定。')
            if not isinstance(selected, dict):
                raise TerminalError('未找到指定 MCP 配置。')
            status = request('GET', '/api/mcp')
            if selected.get('url') != status['url']:
                raise TerminalError('配置不是当前安装的 MCP 地址，未导入。')
            token = selected.get('headers', {}).get('Authorization', '').removeprefix('Bearer ')
            # Test against the declared grant, without recovering hashes or changing permissions.
            result = request('POST', '/api/mcp/test', dict(token=token))
            if not result.get('ok', True):
                raise TerminalError('连接检测未通过。')
            # A token can be valid yet belong to another grant; server binds it explicitly.
            binding = request('POST', '/api/cli/bind', dict(id=gid, token=token), internal=True)
            remember(gid, token, client.root)
            return binding
    if args.command in ('tools','call'):
        return invoke(client, args.connection, 'tools/list' if args.command=='tools' else 'tools/call',
                      {} if args.command=='tools' else dict(name=args.tool, arguments=read_json(args.args_file) if args.args_file else {}))
    if args.command == 'sl':
        path = '/api/desktop/snowluma/managed'
        if args.action == 'status':
            return request('GET', path)
        if args.action == 'test':
            return request('POST', '/api/desktop/onebot/test', {})
        if args.action == 'settings':
            return request('PUT', '/api/desktop/settings', read_json(args.file))
        if args.action == 'configure':
            return request('POST', path+'/automatic', dict(accepted=True, fingerprint=args.accept))
        return request('POST', path+'/'+args.action, dict(choice_id=args.choice_id) if args.action=='select' else {})
    if args.command == 'bilibili':
        if args.action == 'status':
            return request('GET', '/bilibili/api/status')
        action = dict(login='start_login',poll='poll_login',logout='logout')[args.action]
        result = request('POST', '/bilibili/api/action', dict(action=action, **({'consent':True} if args.action=='login' else {})))
        if args.action == 'login' and result.get('qr_image'):
            directory = client.root/'data/cli-login'; private_directory(directory)
            path = directory/'bilibili-qr.png'
            atomic_bytes(path, base64.b64decode(result['qr_image'].split(',',1)[1], validate=True))
            return dict(qr_file=str(path), next_command='bilibili poll')
        if args.action in ('poll','logout'):
            state = result.get('state', '')
            if args.action=='logout' or state in ('logged_in','expired','success'):
                (client.root/'data/cli-login/bilibili-qr.png').unlink(missing_ok=True)
        return result
    if args.command == 'learning':
        if args.action == 'edit':
            return request('POST','/api/mcp/learning/library',read_json(args.file))
        params = {key:getattr(args,key) for key in ('account','group') if hasattr(args,key)}
        return request('GET','/api/mcp/learning/'+args.action,params=params)
    if args.command == 'data':
        if args.action in ('status','accounts'):
            return request('GET','/api/data/'+('settings' if args.action=='status' else 'accounts'))
        if args.action in ('job','cancel'):
            path='/api/watch-jobs/'+reference(args.id)
            return request('GET',path) if args.action=='job' else request('POST',path+'/stop',{})
        return request('POST','/api/data/'+args.action,read_json(args.file))
    if args.command == 'api':
        params = {}
        for value in args.param:
            if '=' not in value:
                raise TerminalError('--param 格式为 key=value。')
            key, value = value.split('=',1); params[key] = value
        if args.method=='POST' and args.path=='/api/mcp/connections':
            raise TerminalError('请用 connections create，以安全保存新连接凭据。')
        result = request(args.method, args.path, read_json(args.file) if args.file else {} if args.method!='GET' else None, params=params)
        return sanitized(result)
    raise TerminalError('未知命令，请查看 --help。')


def shell():
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    console = Console()
    console.print(Panel('[bold cyan]Tulpa MCP[/bold cyan]  '+VERSION+'\n本机配置 · QQ · 微信 · 哔哩哔哩', border_style='cyan'))
    menu = Table(border_style='grey35', header_style='magenta')
    menu.add_column('命令'); menu.add_column('用途')
    for command, text in [('start / status','启动后台 / 查看状态'),('connections list','MCP 连接'),('sl status / sl test','QQ 连接'),
                          ('bilibili status','B站账号'),('learning accounts','学习内容'),('personas','人物卡'),
                          ('tools --connection ID','发现工具'),('help / exit','帮助 / 退出终端')]:
        menu.add_row(command,text)
    console.print(menu)
    while True:
        try:
            line = console.input('[bold cyan]tulpa › [/]').strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if line in ('exit','quit','q'):
            return 0
        if not line:
            continue
        if line=='help':
            line='--help'
        try:
            # Windows paths retain backslashes; quoted paths may contain spaces.
            parts = [p[1:-1] if len(p)>1 and p[0]==p[-1] and p[0] in ('"',"'") else p for p in shlex.split(line,posix=False)]
            if parts[0] in ('shell','serve'):
                raise TerminalError('请在独立终端运行此命令。')
            main(parts)
        except (SystemExit, TerminalError) as exc:
            if isinstance(exc, TerminalError):
                console.print(str(exc), style='red', markup=False)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    machine = '--json' in argv
    try:
        # Accept --json before or after subcommands for shell/agent convenience.
        args = parser().parse_args((['--json'] if machine else [])+[a for a in argv if a!='--json'])
        if args.command in (None, 'shell'):
            if machine or not sys.stdin.isatty():
                if machine:
                    print(json.dumps(dict(commands=['start','status','connections','sl','bilibili','learning','tools','call']),ensure_ascii=False))
                else:
                    parser().print_help()
                return 0
            return shell()
        if args.command == 'serve':
            serve(); return 0
        result = execute(args)
        if args.command not in ('tools','call'):
            result = sanitized(result)
        if machine:
            print(json.dumps(result,ensure_ascii=False))
        else:
            from rich.console import Console
            Console().print_json(json.dumps(result,ensure_ascii=False))
        return 2 if tool_failed(result) else 0
    except (TerminalError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, TerminalError) else '参数或本机文件不可用，请检查 JSON、路径和权限。'
        print(json.dumps(dict(ok=False,error=message),ensure_ascii=False) if machine else 'Tulpa: '+message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
