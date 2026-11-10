"""Build the Linux source release from a clean Git commit, with no local data.

Uses the same allowlist as `tulpa package`, but reads committed bytes and modes.
Dependencies are installed on Linux by setup-linux.sh, never copied from Windows.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatlocal.linux_distribution import included, release_path
from chatlocal.version import VERSION
from check_publication import allowed_path, check_text, linux_archive_check, source_check


def build(output):
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
        raise RuntimeError('Commit source changes before building a release')
    source_check()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    timestamp = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT))
    snapshot = subprocess.check_output(['git', 'archive', '--format=tar', 'HEAD'], cwd=ROOT)
    payload = {}
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        for member in archive:
            # Git emits directory entries too. The Bilibili vendor allowlist
            # permits extensionless license files, so test the archive type
            # before applying the file policy; directories have no payload.
            if member.isdir():
                continue
            if not included(member.name) or member.name == 'README.md':
                continue
            if not member.isfile():
                raise RuntimeError('Non-file in public source: '+member.name)
            name = release_path(member.name).as_posix()
            raw = archive.extractfile(member).read()
            if name in payload or not allowed_path(name, archive=True) or check_text(name, raw):
                raise RuntimeError('Unsafe or duplicate public member: '+name)
            payload[name] = (raw, 0o755 if member.mode & 0o111 else 0o644)
    manifest = dict(product='Tulpa', version=VERSION, checkpoint=commit, platform='linux',
                    edition='source', architectures=['x86_64', 'aarch64'],
                    files=sorted(payload),
                    file_sha256={name: hashlib.sha256(raw).hexdigest() for name, (raw, _) in sorted(payload.items())})
    payload['build-manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2).encode()+b'\n', 0o644)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting an existing release or user file.
    with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, (raw, mode) in sorted(payload.items()):
            member = tarfile.TarInfo('Tulpa/'+name)
            member.size = len(raw); member.mode = mode; member.mtime = timestamp
            archive.addfile(member, io.BytesIO(raw))
    return linux_archive_check(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'release'/f'Tulpa-MCP-{VERSION}-linux.tar.gz')
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False, indent=2))
