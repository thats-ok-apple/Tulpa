"""Offline automatic deployment contracts; no upstream traffic or QQ actions."""
import copy
import io
import json
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_snowluma_managed import (SOURCE, ManagedSnowLuma, Runtime, API, Vault, Receiver,
    fixture_manifest, fixture_folder, wait, rejected, consent_key, _registry,
    FastAPI, TestClient, install_desktop_routes)
from chatlocal.snowluma_download import ArchiveInstallation, platform_key, OfficialRedirect
from chatlocal.snowluma_secure import ManagedError, sha256
from chatlocal.snowluma_runtime import SnowLumaAPI


class Response(io.BytesIO):
    def __init__(self, raw, offset=0):
        super().__init__(raw[offset:]);self.status=206 if offset else 200
        self.headers={'Content-Range':f'bytes {offset}-{len(raw)-1}/{len(raw)}'}


class Opener:
    def __init__(self, raw):self.raw=raw;self.calls=[];self.fail=False;self.entered=None;self.release=None
    def open(self, request, timeout):
        self.calls.append(request)
        if self.entered:self.entered.set();self.release.wait(4)
        if self.fail:raise OSError('synthetic offline')
        value=request.headers.get('Range','bytes=0-');return Response(self.raw,int(value.split('=')[1].split('-')[0]))


def fixture(root):
    manifest=fixture_manifest();folder=fixture_folder(root/'fixture',manifest)
    archive=root/('fixture.zip' if platform_key()=='win-x64' else 'fixture.tar.gz')
    if archive.suffix=='.zip':
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for p in folder.rglob('*'):
                if p.is_file():z.write(p,p.relative_to(folder).as_posix())
    else:
        with tarfile.open(archive,'w:gz') as z:
            for p in folder.rglob('*'):
                if p.is_file():z.add(p,p.relative_to(folder).as_posix())
    spec=manifest['archives'][platform_key()]
    spec.update(size=archive.stat().st_size,sha256=sha256(archive))
    return manifest,archive.read_bytes()


def main():
    with tempfile.TemporaryDirectory(prefix='snow-auto-',dir=SOURCE/'.tmp') as t:
        root=Path(t);manifest,raw=fixture(root)
        opener=Opener(raw);directory=root/'app/data/snowluma-managed'
        installer=ArchiveInstallation(manifest,directory,opener=opener)
        receipt=installer.ensure(lambda:None)
        assert len(opener.calls)==1 and Path(receipt['folder']).is_dir()
        assert installer.ensure(lambda:None)==receipt and len(opener.calls)==1
        Path(receipt['folder'],'index.mjs').write_text('tampered')
        rejected('installation_changed',lambda:installer.ensure(lambda:None))
        # Resume the same pinned artifact; cached and bundled files are rehashed.
        retry=ArchiveInstallation(manifest,root/'resume/data/snowluma-managed',opener=opener)
        spec=manifest['archives'][platform_key()]
        part=retry.directory/'downloads'/(spec['name']+'.part');part.parent.mkdir(parents=True);part.write_bytes(raw[:50])
        archive=retry.archive(lambda:None);assert opener.calls[-1].headers['Range']=='bytes=50-'
        archive.write_bytes(b'corrupt')
        rejected('archive_hash_failed',lambda:retry.archive(lambda:None))
        assert not archive.exists() and list(archive.parent.glob('*.rejected-*'))
        assert retry.archive(lambda:None).read_bytes()==raw
        oversized=ArchiveInstallation(manifest,root/'oversized/data/snowluma-managed',opener=opener)
        path=oversized.directory/'downloads'/(spec['name']+'.part');path.parent.mkdir(parents=True);path.write_bytes(raw+b'bad')
        rejected('archive_size_failed',lambda:oversized.archive(lambda:None))
        assert not path.exists() and oversized.archive(lambda:None).read_bytes()==raw
        offline=Opener(raw);offline.fail=True
        failed=ArchiveInstallation(manifest,root/'offline/data/snowluma-managed',opener=offline)
        rejected('download_failed',lambda:failed.archive(lambda:None))
        assert len(offline.calls)==3 and not Path(failed.planned()['folder']).exists()
        wrong=Opener(b'x'*len(raw))
        mismatch=ArchiveInstallation(manifest,root/'mismatch/data/snowluma-managed',opener=wrong)
        rejected('archive_hash_failed',lambda:mismatch.ensure(lambda:None))
        assert not Path(mismatch.planned()['folder']).exists()
        bundle=ArchiveInstallation(manifest,root/'bundle/data/snowluma-managed',opener=opener)
        path=root/'bundle/resources/snowluma'/spec['name'];path.parent.mkdir(parents=True);path.write_bytes(raw)
        before=len(opener.calls);bundle.ensure(lambda:None);assert len(opener.calls)==before
        # No unsupported path, link, Windows device, duplicate, or preset secret.
        for i,name in enumerate(['../escape','/absolute','C:/escape','file:stream','CON','data/secret','a/../b','a\\b','a.']):
            bad=root/f'bad-{i}.zip'
            with zipfile.ZipFile(bad,'w') as z:
                info=zipfile.ZipInfo();info.filename=name;z.writestr(info,'bad')
            stage=root/f'stage-{i}';stage.mkdir()
            rejected('archive_unsafe',lambda:installer.extract(bad,stage,lambda:None))
        for kind in ('symlink','duplicate','case'):
            bad=root/(kind+'.zip')
            with zipfile.ZipFile(bad,'w') as z:
                if kind=='symlink':
                    info=zipfile.ZipInfo('link');info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,'../escape')
                else:z.writestr('file','first');z.writestr('file' if kind=='duplicate' else 'FILE','second')
            stage=root/kind;stage.mkdir();rejected('archive_unsafe',lambda:installer.extract(bad,stage,lambda:None))
        bad=root/'bad.tar.gz'
        with tarfile.open(bad,'w:gz') as z:
            member=tarfile.TarInfo('link');member.type=tarfile.SYMTYPE;member.linkname='../escape';z.addfile(member)
        stage=root/'tar';stage.mkdir();rejected('archive_unsafe',lambda:installer.extract(bad,stage,lambda:None))
        rejected('download_redirect',lambda:OfficialRedirect().redirect_request(None,None,302,'',{},'http://evil.invalid/file'))
        print('PASS archive: pinned hash, fresh download, cache/bundle recheck, resume, tampering, safe extraction, no traversal/links/ADS/preset data')
        print('PASS download faults: oversized partial quarantine/retry, bounded offline retries, SHA mismatch never executes')

        runtime=Runtime();api=API(runtime);opener=Opener(raw)
        def factory(m,d):return ArchiveInstallation(m,d,opener=opener)
        app=root/'service';service=ManagedSnowLuma(app,manifest=manifest,runtime=runtime,
            api_factory=lambda _:api,vault_factory=Vault,archive_factory=factory,
            probe=lambda *args:Receiver('12345'),poll_seconds=.03)
        body=dict(accepted=True,fingerprint=consent_key(manifest))
        rejected('consent_required',lambda:service.begin_automatic(dict(body,accepted=False)))
        assert not opener.calls and runtime.spawns==0
        _registry[str(app.resolve()).casefold()]=service;web=FastAPI();install_desktop_routes(web,app)
        with TestClient(web) as client:
            endpoint='/api/desktop/snowluma/managed/automatic';headers={'X-ChatWeave-UI':'1'}
            assert client.post(endpoint,json=body).status_code==403
            assert client.post(endpoint,json=body,headers=dict(headers,Origin='https://foreign.invalid')).status_code==403
            denied=client.post(endpoint,json=dict(body,accepted=False),headers=headers).json()
            assert denied['code']=='consent_required' and not opener.calls and runtime.spawns==0
            assert client.post(endpoint,json=body,headers=headers).json()['ok']
            assert client.post(endpoint,json=body,headers=headers).json()['ok']
            wait(service,'ready')
            assert runtime.spawns==1 and len(opener.calls)==1
            first=service.vault.read()
        service.resume();wait(service,'ready')
        assert runtime.spawns==2 and service.vault.read()==first and len(opener.calls)==1
        service.cancel(revoke=True);assert not service.connection('http')['url']
        rejected('consent_required',service.retry);service.close()
        # In-flight downloads cannot publish or execute after cancellation.
        opener=Opener(raw);opener.entered=threading.Event();opener.release=threading.Event()
        runtime=Runtime();api=API(runtime)
        service=ManagedSnowLuma(root/'cancel',manifest=manifest,runtime=runtime,
            api_factory=lambda _:api,vault_factory=Vault,archive_factory=factory)
        service.begin_automatic(body);assert opener.entered.wait(4)
        thread=threading.Thread(target=service.cancel);thread.start()
        while service.state()['enabled']:time.sleep(.01)
        opener.release.set();thread.join(5);service.close()
        assert not thread.is_alive() and runtime.spawns==0 and not Path(service.archives.planned()['folder']).exists()
        # Terms changes and multiple windows never bypass the current consent.
        rejected('consent_required',lambda:service.begin_automatic(dict(body,fingerprint='stale')))
        for version in ('2.0.3','2.1.0','2.1.1'):
            api2=SnowLumaAPI(12345);api2.version=version
            rows=[dict(pid=101,loggedIn=False,uin='0',account=dict(uin='12345'))]
            calls=[]
            def request(method,path,body=None):calls.append(path);return dict(list=rows)
            api2.request=request;assert api2.probe(101)=='12345' and api2.probe(102)==''
            rows[0].update(loggedIn=True,uin='67890');assert api2.probe(101)=='67890'
            rows.append(dict(rows[0]));assert api2.probe(101)==''
            assert set(calls)=={'/api/processes'}, (version,calls)
        print('PASS automatic lifecycle: consent-first, duplicate click, same credentials on restart, no redownload, cancel-before-execute, revoke, v2 process identity')
        print('PASS real local API route -> mock download/runtime: CSRF and consent guards, automatic start, duplicate request and lifecycle')
    print('PASS OFFLINE: upstream downloads=0, QQ writes=0; no claim of real QQ verification')


if __name__=='__main__':main()
