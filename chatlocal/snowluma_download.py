"""Pinned official archives, checked before extraction or execution.

This module never accepts a download URL from a browser or an MCP client.
Archives bundled by the release builder follow the same verification path.
"""
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import tarfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

from .snowluma_local import LocalInstallation, fingerprint
from .snowluma_secure import ManagedError, atomic_json, local_path, private_directory, read_json, sha256


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit
        url = urlsplit(newurl)
        if url.scheme != 'https' or url.hostname not in {
            'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
            raise ManagedError('download_redirect', '官方下载跳转到未允许的来源，已停止。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def platform_key():
    arch = {'AMD64':'x64', 'x86_64':'x64', 'aarch64':'arm64', 'ARM64':'arm64', 'arm64':'arm64'}.get(platform.machine())
    key = ('win' if os.name == 'nt' else 'linux')+'-'+str(arch)
    if platform.system() not in ('Windows', 'Linux') or key not in ('win-x64','linux-x64','linux-arm64'):
        raise ManagedError('platform_unsupported', '自动部署支持 Windows x64、Linux x64 和 ARM64。')
    return key


def archive_spec(manifest):
    spec = dict(manifest.get('archives', {}).get(platform_key(), {}))
    expected = 'https://github.com/SnowLuma/SnowLuma/releases/download/v'+manifest['version']+'/'
    if (not re.fullmatch(r'SnowLuma-v[0-9.]+-(?:win-x64\.zip|linux-(?:x64|arm64)\.tar\.gz)', spec.get('name',''))
            or spec.get('url') != expected+spec.get('name','')
            or not re.fullmatch(r'[a-f0-9]{64}', spec.get('sha256',''))
            or type(spec.get('size')) is not int or not 0 < spec['size'] < 256*1024*1024):
        raise ManagedError('manifest_invalid', '官方运行包清单缺失或不兼容，未开始下载。')
    return spec


class ArchiveInstallation:
    def __init__(self, manifest, directory, *, opener=None):
        self.manifest, self.directory = manifest, local_path(directory)
        self.local = LocalInstallation(manifest, self.directory)
        self.opener = opener or urllib.request.build_opener(OfficialRedirect())

    def planned(self):
        spec = archive_spec(self.manifest)
        folder = self.directory/'runtime'/('v'+self.manifest['version']+'-'+platform_key())
        return dict(folder=str(folder), version=self.manifest['version'], mode='local',
                    fingerprint=fingerprint(spec), archive_sha256=spec['sha256'])

    def _valid(self, path, spec, check):
        check()
        valid = path.is_file() and path.stat().st_size == spec['size'] and sha256(path) == spec['sha256']
        check()
        return valid

    def _quarantine(self, path):
        path = local_path(path)
        if path.parent != local_path(self.directory/'downloads'):
            return
        os.replace(path, path.with_name(path.name+'.rejected-'+uuid.uuid4().hex[:10]))

    def archive(self, check, progress=lambda **kw:None):
        spec = archive_spec(self.manifest)
        bundle = local_path(self.directory.parent.parent/'resources/snowluma'/spec['name'])
        cache = local_path(self.directory/'downloads'/spec['name'])
        for path, source in ((bundle, 'bundled'), (cache, 'cache')):
            if path.exists():
                progress(phase='verifying_package', source=source)
                if not self._valid(path, spec, check):
                    if source == 'cache':self._quarantine(path)
                    raise ManagedError('archive_hash_failed', 'SnowLuma 运行包校验失败，未解压或运行。缓存已隔离，可重试；随包文件损坏请恢复安装包。', retryable=source=='cache')
                return path
        private_directory(cache.parent)
        partial = local_path(cache.with_suffix(cache.suffix+'.part'))
        progress(phase='downloading', source='official', total=spec['size'], downloaded=0)
        for attempt in range(3):
            check()
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > spec['size']:
                    self._quarantine(partial)
                    raise ManagedError('archive_size_failed', '未完成的下载大小异常，已隔离；请点重试重新下载。', retryable=True)
                if offset == spec['size']:
                    break
                headers = {'User-Agent':'Tulpa-SnowLuma-Setup', 'Accept-Encoding':'identity'}
                if offset:headers['Range'] = f'bytes={offset}-'
                with self.opener.open(urllib.request.Request(spec['url'], headers=headers), timeout=10) as response:
                    if response.status == 206:
                        if response.headers.get('Content-Range') != f'bytes {offset}-{spec["size"]-1}/{spec["size"]}':
                            raise ManagedError('download_range', '官方下载分段响应不一致，未合并文件。')
                    elif response.status == 200:
                        offset = 0
                    else:
                        raise ManagedError('download_protocol', '官方下载响应异常，未执行运行包。', retryable=True)
                    with partial.open('ab' if offset else 'wb') as out:
                        started = time.monotonic()
                        while True:
                            check()
                            block = response.read(256*1024)
                            check()
                            if not block:break
                            offset += len(block)
                            if offset > spec['size']:
                                raise ManagedError('archive_size_failed', '官方下载超出固定版本大小，已停止。')
                            out.write(block)
                            progress(phase='downloading', source='official', downloaded=offset, total=spec['size'])
                            if time.monotonic()-started > 300:
                                raise TimeoutError()
                        out.flush();os.fsync(out.fileno())
                if offset != spec['size']:raise OSError('incomplete archive')
                break
            except ManagedError:raise
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise ManagedError('download_failed', '官方下载暂时失败，已保留断点。检查网络后点重试。', retryable=True) from None
                # Cancellation remains responsive during bounded backoff.
                for _ in range(5*(attempt+1)):
                    check();time.sleep(.1)
        progress(phase='verifying_package', source='official')
        if not self._valid(partial, spec, check):
            self._quarantine(partial)
            raise ManagedError('archive_hash_failed', '官方下载 SHA256 与审核清单不符，已隔离损坏包，未解压或运行。', retryable=True)
        check();os.replace(partial, cache)
        return cache

    def _target(self, stage, name, seen):
        # ZIP and TAR share a stricter portable pathname contract, including ADS,
        # Windows devices, duplicate/case-folded names and ancestor conflicts.
        rel = PurePosixPath(name)
        if (not rel.parts or '\\' in name or rel.is_absolute() or '..' in rel.parts or ':' in name
                or any(ord(c)<32 for c in name) or any(p.rstrip(' .') != p for p in rel.parts)
                or any(re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p) for p in rel.parts)
                or rel.parts[0].lower() in ('config','data','logs')):
            raise ManagedError('archive_unsafe', '运行包包含不安全路径或预置用户数据，未部署。')
        key = rel.as_posix().casefold()
        if key in seen:raise ManagedError('archive_unsafe', '运行包包含重复路径，未部署。')
        seen.add(key)
        target = local_path(stage.joinpath(*rel.parts))
        if not target.is_relative_to(stage):raise ManagedError('archive_unsafe', '运行包路径越界。')
        return target

    def extract(self, archive, stage, check):
        seen = set();total = 0
        def write(name, size, is_dir, mode, stream):
            nonlocal total
            check();total += size
            if len(seen) >= 10000 or size < 0 or total > 768*1024*1024:
                raise ManagedError('archive_too_large', '运行包解压大小超限，已停止。')
            target = self._target(stage, name, seen)
            if is_dir:target.mkdir(parents=True, exist_ok=True);return
            target.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with target.open('xb') as out:
                while block := stream.read(256*1024):
                    check();count += len(block)
                    if count > size:raise ManagedError('archive_unsafe', '解压大小与目录不符。')
                    out.write(block)
            if count != size:raise ManagedError('archive_unsafe', '运行包内容不完整。')
            if os.name != 'nt':target.chmod(0o700 if mode & 0o111 else 0o600)
        if archive.name.endswith('.zip'):
            with zipfile.ZipFile(archive) as z:
                for member in z.infolist():
                    mode = member.external_attr >> 16
                    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise ManagedError('archive_unsafe', '运行包包含链接或特殊文件，未部署。')
                    with z.open(member) as stream:write(member.orig_filename, member.file_size, member.is_dir(), mode, stream)
        else:
            with tarfile.open(archive, 'r:gz') as z:
                for member in z:
                    if member.isdir() and member.name in ('.','./'):continue
                    if not member.isdir() and not member.isfile():
                        raise ManagedError('archive_unsafe', '运行包包含链接或特殊文件，未部署。')
                    stream = z.extractfile(member) if member.isfile() else None
                    try:write(member.name, member.size, member.isdir(), member.mode, stream)
                    finally:
                        if stream:stream.close()

    def ensure(self, check, progress=lambda **kw:None):
        planned = self.planned();folder = local_path(planned['folder'])
        receipt_path = self.directory/'installed'/('v'+self.manifest['version']+'-'+platform_key()+'.json')
        if folder.exists():
            receipt = read_json(receipt_path)
            if receipt.get('archive_sha256') != planned['archive_sha256'] or receipt.get('folder') != str(folder):
                raise ManagedError('installation_unverified', '托管目录已有未知文件，未覆盖或运行；请恢复安装清单。')
            # archive_sha256 is provenance, not part of LocalInstallation's hash.
            current = self.local.inspect(str(folder))
            if current.get('fingerprint') != receipt.get('fingerprint'):
                raise ManagedError('installation_changed', '托管运行文件变化，已停止；请恢复匹配备份。')
            check();return current
        archive = self.archive(check, progress)
        check();progress(phase='deploying')
        private_directory(folder.parent)
        stage = local_path(folder.parent/('.staging-'+uuid.uuid4().hex))
        stage.mkdir()
        try:
            self.extract(archive, stage, check)
            receipt = self.local.inspect(str(stage))
            # Publish the receipt before the atomic directory move. A crash at
            # either boundary is recoverable without trusting a stray directory.
            receipt['folder'] = str(folder)
            receipt['fingerprint'] = fingerprint({k:receipt[k] for k in ('folder','version','mode','files')})
            check();atomic_json(receipt_path, dict(receipt, archive_sha256=planned['archive_sha256']))
            check();os.replace(stage, folder)
            return self.local.inspect(str(folder))
        except ManagedError:raise
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError):
            raise ManagedError('archive_invalid', '运行包无法安全部署，未启动；请检查磁盘空间或运行包。', retryable=True) from None
        finally:
            if stage.exists():
                checked = local_path(stage)
                if checked.parent == local_path(self.directory/'runtime') and checked.name.startswith('.staging-'):
                    shutil.rmtree(checked)
