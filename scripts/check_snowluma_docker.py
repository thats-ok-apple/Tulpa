"""Isolated Docker management regression tests. No Docker/QQ/model is contacted."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from chatlocal.snowluma_docker import DockerRuntime, DockerSnowLumaAPI, PORTS, MOUNTS, release
from chatlocal.snowluma_managed import ManagedSnowLuma, load_manifest, consent_key, _registry
from chatlocal.snowluma_secure import ManagedError


class Runtime(DockerRuntime):
    def __init__(self, root):
        super().__init__(root)
        self.owner=None; self.running=False; self.created=0; self.starts=0; self.stops=0; self.pulls=0
        self.engine='fixture-engine'; self.changed=False; self.package_changed=False; self.started_at='boot-1'
        self.after_create=lambda check:None
        self.display_preparations=0; self.screen_blank=True
    def environment(self, manifest): return dict(release(manifest), engine_id=self.engine, engine_version='fixture')
    def ensure_image(self, selection, check): check(); self.pulls+=1; return 'sha256:'+'b'*64
    def volumes(self, *args, **kw): return {}
    def recover_created(self, selection, instance, ports, image_id): return copy.deepcopy(self.owner)
    def create(self, selection, instance, image_id, ports, credentials, check):
        check(); self.created+=1; self.owner=dict(id='a'*64, image_id=image_id)
        self.after_create(check); check()
        return copy.deepcopy(self.owner)
    def validate(self, owner, selection, instance, ports, *, running=False):
        if self.changed: raise ManagedError('docker_owner_changed','fixture owner changed')
        if self.owner != owner: raise ManagedError('docker_container_missing','fixture container missing')
        if running and not self.running: raise ManagedError('docker_stopped','fixture stopped',retryable=True)
        return {'State':{'Running':self.running,'StartedAt':self.started_at}}
    def verify_package(self, owner, manifest):
        if self.package_changed: raise ManagedError('terms_changed','fixture package changed')
    def verify_credentials(self,*args,**kw): pass
    def seed_config(self,*args,**kw): pass
    def start(self, owner, selection, instance, ports, check):
        self.validate(owner,selection,instance,ports); check()
        if not self.running: self.starts+=1; self.running=True
    def stop(self, owner, selection, instance, ports):
        self.validate(owner,selection,instance,ports)
        if self.running: self.stops+=1; self.running=False
    def prepare_display(self, owner, selection, instance, ports, check):
        check(); self.validate(owner,selection,instance,ports,running=True)
        self.display_preparations+=1; self.screen_blank=False
    def logs(self,*args): return ['fixture log, no credentials']


class API:
    def __init__(self):
        self.values=['12345']; self.initializations=0; self.configurations=0; self.expired=False; self.bad_password=False
    def initialize(self,password,manifest,check):
        check(); self.initializations+=1
        if self.bad_password: raise ManagedError('admin_auth_failed','fixture password changed')
        self.expired=False
    def accounts(self):
        if self.expired: raise ManagedError('admin_auth_failed','fixture session expired')
        return list(self.values)
    def configure(self,account,ports,credentials): self.configurations+=1
    def request(self,method,path,body=None): return {'mustChangePassword':False}


class Receiver:
    def __init__(self,account): self.account=account; self.phase='connected'; self.stopped=False
    def status(self): return dict(state=self.phase,account=self.account)
    def stop(self): self.stopped=True


def wait(m, phase, timeout=7):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        status=m.status()
        if status['phase']==phase: return status
        if status['phase']=='error': raise AssertionError(status)
        time.sleep(.02)
    raise AssertionError(m.status())


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tulpa-docker-check-')
        self.root=Path(self.temp.name); self.runtime=Runtime(self.root); self.api=API(); self.invalidated=[]; self.receivers=[]
        self.m=self.new_manager()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.m.close)
    def new_manager(self):
        def probe(http,ws,account,check):
            check(); r=Receiver(account); self.receivers.append(r); return r
        return ManagedSnowLuma(self.root,docker_runtime=self.runtime,docker_api_factory=lambda port:self.api,
            probe=probe,poll_seconds=.02,event_timeout=.2,on_invalidated=lambda:self.invalidated.append(True))
    def accepted(self):
        preview=self.m.docker.inspect()
        return dict(accepted=True,selection_id=preview['selection_id'],fingerprint=preview['consent_fingerprint'])
    def start(self):
        self.m.docker.begin(self.accepted()); return wait(self.m,'ready')
    def rejected(self,code,fn):
        with self.assertRaises(ManagedError) as ctx: fn()
        self.assertEqual(ctx.exception.code,code)
    def test_inspection_is_read_only_and_consent_is_exact(self):
        preview=self.m.docker.inspect()
        self.assertEqual(preview['mode'],'docker'); self.assertIn('persist_qq_data',preview['scope'])
        self.assertFalse(self.m.directory.exists()); self.assertEqual(self.runtime.created,0)
        body=dict(accepted=False,selection_id=preview['selection_id'],fingerprint=preview['consent_fingerprint'])
        self.rejected('consent_required',lambda:self.m.docker.begin(body))
        body.update(accepted=True,fingerprint='wrong')
        self.rejected('consent_required',lambda:self.m.docker.begin(body))
    def test_engine_change_between_inspection_and_start(self):
        body=self.accepted(); self.runtime.engine='different'
        self.rejected('docker_environment_changed',lambda:self.m.docker.begin(body))
        self.assertEqual(self.runtime.created,0)
    def test_login_and_ready_are_distinct_and_secrets_stay_private(self):
        self.api.values=[]; self.m.docker.begin(self.accepted()); state=wait(self.m,'waiting_login')
        self.assertFalse(state['qq_ready']); self.assertEqual(self.m.connection('http')['url'],'')
        login=self.m.docker.login(); self.assertTrue(login['novnc_url'].startswith('http://127.0.0.1:'))
        self.assertEqual(parse_qs(urlsplit(login['novnc_url']).query).get('resize'),['scale'])
        self.assertEqual(state['novnc_url'],login['novnc_url'])
        self.assertNotIn(login['password'],json.dumps(state)); self.assertNotIn('password=',login['novnc_url'])
        self.api.values=['12345']; wait(self.m,'ready')
        connection=self.m.connection('http'); self.assertTrue(connection['url']); self.m.guard(connection['_managed'])
        for secret in self.m.vault.read().values():
            self.assertNotIn(secret,json.dumps(self.m.status()))
            self.assertNotIn(secret,self.m.state_path.read_text())
        self.assertEqual(self.m.vault.path.stat().st_mode & 0o777,0o600)
    def test_stop_retry_keeps_container_ports_and_credentials(self):
        first=self.start(); credentials=self.m.vault.read(); cid=first['container_id']; ports=first['ports']
        self.m.cancel(); self.assertFalse(self.runtime.running)
        self.assertEqual(self.m.connection('http')['url'],'')
        self.m.retry(); after=wait(self.m,'ready')
        self.assertEqual(after['container_id'],cid); self.assertEqual(after['ports'],ports)
        self.assertEqual(self.runtime.created,1); self.assertEqual(self.m.vault.read(),credentials)
    def test_desktop_wakes_before_login_and_after_retry(self):
        self.api.values=[]; self.m.docker.begin(self.accepted()); first=wait(self.m,'waiting_login')
        self.assertFalse(self.runtime.screen_blank); self.assertEqual(self.runtime.display_preparations,1)
        self.runtime.screen_blank=True
        self.m.docker.login()
        self.assertFalse(self.runtime.screen_blank); self.assertEqual(self.runtime.display_preparations,2)
        self.m.cancel(); self.runtime.screen_blank=True; self.m.retry()
        after=wait(self.m,'waiting_login')
        self.assertFalse(self.runtime.screen_blank); self.assertEqual(self.runtime.display_preparations,3)
        self.assertEqual(first['container_id'],after['container_id'])
        self.m.cancel(revoke=True)
        self.rejected('cancelled',self.m.docker.login)
        self.assertEqual(self.runtime.display_preparations,3)
    def test_restart_while_waiting_for_scan_restores_desktop_and_session(self):
        self.api.values=[]; self.m.docker.begin(self.accepted()); first=wait(self.m,'waiting_login')
        self.runtime.screen_blank=True; self.runtime.started_at='boot-2'
        wait(self.m,'reconnecting'); self.api.expired=True
        after=wait(self.m,'waiting_login')
        self.assertEqual(first['container_id'],after['container_id'])
        self.assertEqual(self.runtime.created,1)
        self.assertEqual(self.runtime.display_preparations,2)
        self.assertFalse(self.runtime.screen_blank); self.assertFalse(self.api.expired)
    def test_explicit_retry_after_external_mode_restores_managed_connection(self):
        first=self.start(); self.m.cancel(); self.m.use_external()
        self.assertEqual(self.m.state()['mode'],'external')
        self.m.retry(); after=wait(self.m,'ready')
        self.assertEqual(self.m.state()['mode'],'managed')
        self.assertEqual(after['container_id'],first['container_id'])
        self.assertTrue(self.m.connection('http')['url'])
        self.m.guard(self.m.connection('http')['_managed'])
    def test_revoke_requires_new_consent_and_preserves_data_identity(self):
        state=self.start(); self.m.cancel(revoke=True)
        self.rejected('consent_required',self.m.retry)
        self.rejected('cancelled',self.m.docker.login)
        self.assertEqual(self.m.connection('http')['url'],'')
        self.m.docker.begin(self.accepted()); after=wait(self.m,'ready')
        self.assertEqual(state['container_id'],after['container_id']); self.assertEqual(self.runtime.created,1)
    def test_backend_restart_resumes_owned_container(self):
        state=self.start(); self.m.close()
        m=self.new_manager(); self.addCleanup(m.close); m.resume(); after=wait(m,'ready')
        self.assertEqual(after['container_id'],state['container_id']); self.assertEqual(self.runtime.created,1)
    def test_changed_account_is_blocked_before_send(self):
        self.start(); connection=self.m.connection('http'); self.api.values=['54321']
        self.rejected('account_mismatch',lambda:self.m.guard(connection['_managed']))
        status=wait(self.m,'error'); self.assertEqual(status['code'],'account_mismatch')
        self.assertEqual(self.m.connection('http')['url'],''); self.assertTrue(self.invalidated)
    def test_multiple_accounts_never_publish_connection(self):
        self.api.values=['12345','54321']; self.m.docker.begin(self.accepted())
        status=wait(self.m,'error'); self.assertEqual(status['code'],'unexpected_account')
        self.assertEqual(self.m.connection('http')['url'],'')
    def test_wrong_event_account_never_becomes_ready(self):
        self.m.probe=lambda *a:Receiver('54321'); self.m.docker.begin(self.accepted())
        status=wait(self.m,'error'); self.assertEqual(status['code'],'event_identity_failed')
    def test_restarted_container_and_expired_session_recover_same_identity(self):
        self.start(); self.runtime.started_at='boot-2'
        wait(self.m,'reconnecting'); wait(self.m,'ready')
        first=self.api.initializations; self.api.expired=True
        wait(self.m,'reconnecting'); wait(self.m,'ready')
        self.assertGreater(self.api.initializations,first); self.assertEqual(self.runtime.created,1)
    def test_event_auth_failure_stops_access(self):
        self.start(); self.receivers[-1].phase='auth_error'
        status=wait(self.m,'error'); self.assertEqual(status['code'],'event_identity_failed')
    def test_changed_terms_prevent_container_start(self):
        self.runtime.package_changed=True; self.m.docker.begin(self.accepted())
        status=wait(self.m,'error'); self.assertEqual(status['code'],'terms_changed'); self.assertEqual(self.runtime.starts,0)
    def test_cancellation_after_create_recovers_owned_id(self):
        self.runtime.after_create=lambda check:self.m.cancel()
        self.m.docker.begin(self.accepted()); wait(self.m,'cancelled')
        end=time.monotonic()+3
        while self.m.thread.is_alive() and time.monotonic()<end: time.sleep(.01)
        self.assertEqual(self.m.state()['owner'],self.runtime.owner)
        self.assertEqual(self.runtime.starts,0); self.assertFalse(self.runtime.running)
    def test_lost_credentials_do_not_reset_existing_container(self):
        self.start(); self.m.cancel(); self.m.vault.path.unlink()
        self.m.retry(); status=wait(self.m,'error')
        self.assertEqual(status['code'],'credential_unavailable'); self.assertEqual(self.runtime.created,1)
    def test_owner_change_never_stops_another_container(self):
        self.start(); self.runtime.changed=True; before=self.runtime.stops
        status=wait(self.m,'error'); self.assertEqual(status['code'],'docker_owner_changed')
        self.assertEqual(self.runtime.stops,before); self.assertEqual(self.m.connection('http')['url'],'')
        self.runtime.changed=False
    def test_terms_change_invalidates_resume(self):
        self.start(); self.m.close(); self.m.manifest['docker']['tag']='future'
        self.assertFalse(self.m.status()['consent_current'])
        self.assertEqual(self.m.connection('http')['url'],'')
    def test_loopback_api_auth_and_body_validation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from chatlocal.desktop_routes import install_desktop_routes
        app=FastAPI(); key=str(self.root.resolve()).casefold()
        with patch.dict(_registry,{key:self.m}):
            install_desktop_routes(app,self.root)
            with TestClient(app) as client:
                route='/api/desktop/snowluma/managed/'
                self.assertEqual(client.post(route+'docker-inspect',json={}).status_code,403)
                headers={'x-chatweave-ui':'1'}
                result=client.post(route+'docker-inspect',json={},headers=headers).json()
                self.assertTrue(result['ok']); self.assertEqual(result['mode'],'docker')
                self.assertEqual(client.post(route+'docker-inspect',json={'image':'untrusted'},headers=headers).status_code,400)
                self.assertEqual(client.post(route+'docker-start',json={'accepted':False},headers=headers).json()['code'],'consent_required')
                self.assertEqual(client.post(route+'docker-login',json={},headers={**headers,'origin':'https://foreign'}).status_code,403)


class RuntimeTests(unittest.TestCase):
    def test_display_commands_require_owned_container_and_honor_cancellation(self):
        r=DockerRuntime(ROOT); owner={'id':'fixture-owned-container'}
        with patch.object(r,'validate') as validate, patch.object(r,'call') as call:
            r.prepare_display(owner,{},'instance',{},lambda:None)
            validate.assert_called_once_with(owner,{},'instance',{},running=True)
            self.assertEqual([c.args[0] for c in call.call_args_list],
                [['exec','-i',owner['id'],'python3','-']]+
                [['exec',owner['id'],'env','DISPLAY=:1','xset','s',s] for s in ('off','noblank','reset')]+
                [['exec',owner['id'],'env','DISPLAY=:1','X11VNC_SYNC_TIMEOUT=8','x11vnc','-sync','-R',s] for s in ('shared','refresh')])
            self.assertEqual(call.call_args_list[0].kwargs['data'],(ROOT/'chatlocal/snowluma_desktop.py').read_bytes())
        with patch.object(r,'validate',side_effect=ManagedError('docker_owner_changed','changed')), patch.object(r,'call') as call:
            with self.assertRaises(ManagedError):r.prepare_display(owner,{},'instance',{},lambda:None)
            call.assert_not_called()
        def cancelled():raise ManagedError('cancelled','fixture cancelled')
        with patch.object(r,'validate'), patch('subprocess.Popen') as process:
            with self.assertRaises(ManagedError):r.prepare_display(owner,{},'instance',{},cancelled)
            process.assert_not_called()
    def test_display_config_overrides_resize_and_preserves_other_settings(self):
        from chatlocal.snowluma_desktop import configure_files
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);novnc=root/'opt/noVNC';fluxbox=root/'root/.fluxbox'
            novnc.mkdir(parents=True);fluxbox.mkdir(parents=True)
            config=fluxbox/'init';mandatory=novnc/'mandatory.json';vnc=root/'root/.x11vncrc'
            config.write_text('session.screen0.workspaces: 4\nsession.screen0.strftimeFormat: %H:%M\n')
            mandatory.write_text('{"resize":"remote","view_only":false}')
            vnc.write_text('nevershared\nrfbauth /root/.vnc/passwd\n')
            configure_files(root)
            self.assertEqual(json.loads(mandatory.read_text()),dict(resize='scale',view_only=False))
            self.assertIn('session.screen0.workspaces: 1',config.read_text())
            self.assertIn('session.screen0.strftimeFormat: %H:%M',config.read_text())
            self.assertEqual(vnc.read_text(),'rfbauth /root/.vnc/passwd\nshared\n')
            self.assertEqual(vnc.stat().st_mode & 0o777,0o600)
            before=(config.read_bytes(),mandatory.read_bytes(),vnc.read_bytes());configure_files(root)
            self.assertEqual((config.read_bytes(),mandatory.read_bytes(),vnc.read_bytes()),before)
            mandatory.unlink();configure_files(root)
            self.assertEqual(json.loads(mandatory.read_text()),dict(resize='scale'))
            mandatory.write_text('[]')
            with self.assertRaises(ValueError):configure_files(root)
            self.assertEqual(mandatory.read_text(),'[]')
    def test_pinned_image_supports_native_arch_only(self):
        manifest=load_manifest()
        for machine,arch in [('aarch64','arm64'),('x86_64','amd64')]:
            with patch('platform.machine',return_value=machine):
                item=release(manifest); self.assertEqual(item['platform'],'linux/'+arch)
                self.assertIn('@sha256:',item['image_ref']); self.assertNotIn('latest',item['image'])
        with patch('platform.machine',return_value='riscv64'):
            with self.assertRaises(ManagedError): release(manifest)
    def test_remote_docker_context_is_rejected(self):
        r=DockerRuntime(ROOT,executable='/usr/bin/docker',endpoint='tcp://remote:2375')
        with self.assertRaises(ManagedError) as ctx:r._command()
        self.assertEqual(ctx.exception.code,'docker_remote')
    def test_container_identity_ports_and_mounts_are_checked(self):
        r=DockerRuntime(ROOT); instance='c'*32; selected=release(load_manifest()); owner={'id':'a'*64,'image_id':'sha256:'+'b'*64}
        ports=dict(admin=23000,http=23001,ws=23002,novnc=23003)
        row=dict(Id=owner['id'],Name='/'+r.name(instance),Image=owner['image_id'],
                 Config=dict(Labels=r.labels(instance),Image=selected['image_ref']),
                 HostConfig=dict(NetworkMode='bridge',PortBindings={str(PORTS[k])+'/tcp':[dict(HostIp='127.0.0.1',HostPort=str(v))] for k,v in ports.items()}),
                 Mounts=[dict(Type='volume',Name=r.name(instance)+'-'+k,Destination=v) for k,v in MOUNTS.items()],State=dict(Running=True))
        with patch.object(r,'inspect',return_value=row):self.assertEqual(r.validate(owner,selected,instance,ports),row)
        for changed in ('label','image','port','mount','privileged'):
            other=copy.deepcopy(row)
            if changed=='label':other['Config']['Labels']={}
            if changed=='image':other['Image']='sha256:'+'d'*64
            if changed=='port':other['HostConfig']['PortBindings']['5099/tcp'][0]['HostIp']='0.0.0.0'
            if changed=='mount':other['Mounts'][0]['Type']='bind'
            if changed=='privileged':other['HostConfig']['Privileged']=True
            with patch.object(r,'inspect',return_value=other):
                with self.assertRaises(ManagedError) as ctx:r.validate(owner,selected,instance,ports)
                self.assertEqual(ctx.exception.code,'docker_owner_changed')
    def test_container_onebot_binds_inside_namespace_only(self):
        calls=[]; api=DockerSnowLumaAPI(12345)
        api.request=lambda method,path,body:(calls.append(body) or dict(saved=True,applied=True,online=True))
        api.configure('12345',dict(http=50001,ws=50002),dict(http_token='test-http',ws_token='test-ws'))
        config=calls[0]['networks']
        self.assertEqual(config['httpServers'][0]['host'],'0.0.0.0'); self.assertEqual(config['httpServers'][0]['port'],3000)
        self.assertEqual(config['wsServers'][0]['port'],3001); self.assertEqual(config['wsServers'][0]['role'],'Event')
        self.assertEqual(config['httpClients'],[]); self.assertEqual(config['wsClients'],[])
    def test_auth_file_is_verified_without_reset(self):
        r=DockerRuntime(ROOT); password='fixture-secret-password'; salt=b'x'*16
        value=dict(passwordSalt=salt.hex(),passwordHash=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=64).hex(),mustChangePassword=False)
        with patch.object(r,'read_file',return_value=json.dumps(value).encode()):r.verify_credentials({'id':'fixture'},{'admin_password':password},required=True)
        for raw in (None,b'{}',json.dumps(dict(value,mustChangePassword=True)).encode()):
            with patch.object(r,'read_file',return_value=raw):
                with self.assertRaises(ManagedError) as ctx:r.verify_credentials({'id':'fixture'},{'admin_password':password},required=True)
                self.assertEqual(ctx.exception.code,'credential_state_invalid')
    def test_docker_subprocess_cancellation_errors_and_no_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/'docker-fixture'
            script.write_text('#!/usr/bin/env python3\nimport json,sys,time\nargs=sys.argv[3:]\n'
                              'if args[0]=="sleep": time.sleep(30)\n'
                              'if args[0]=="permission":\n sys.stderr.write("permission denied connecting to docker.sock token=SECRET"); sys.exit(1)\n'
                              'print(json.dumps(args))\n')
            script.chmod(0o700); r=DockerRuntime(ROOT,executable=str(script),endpoint='unix:///fixture.sock')
            malicious='image; touch '+str(Path(tmp)/'unwanted')
            self.assertEqual(r.json(['echo',malicious]),['echo',malicious]); self.assertFalse((Path(tmp)/'unwanted').exists())
            with self.assertRaises(ManagedError) as ctx:r.call(['permission'])
            self.assertEqual(ctx.exception.code,'docker_permission'); self.assertNotIn('SECRET',str(ctx.exception))
            start=time.monotonic()
            def cancelled():
                if time.monotonic()-start>.1:raise ManagedError('cancelled','test cancel')
            with self.assertRaises(ManagedError) as ctx:r.call(['sleep'],check=cancelled)
            self.assertEqual(ctx.exception.code,'cancelled'); self.assertLess(time.monotonic()-start,3)


if __name__=='__main__': unittest.main(verbosity=2)
