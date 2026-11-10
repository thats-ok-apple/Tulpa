"""Independent operator settings; never accepted as MCP tool parameters."""
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from chatlocal.snowluma_secure import SecretVault, atomic_json, local_path, private_directory, read_json
from .errors import Failure


@dataclass(frozen=True)
class Limits:
    max_video_seconds: int = 1800
    max_download_bytes: int = 256 * 1024 * 1024
    cache_bytes: int = 1024 * 1024 * 1024
    cache_ttl_seconds: int = 86400
    max_frames: int = 8
    initial_frames: int = 4
    max_jobs: int = 2
    job_timeout_seconds: int = 600
    frame_width: int = 960
    asr_backend: str = 'whispercpp'
    asr_model_path: str = ''


class Settings:
    def __init__(self, root):
        self.root = local_path(Path(root))
        private_directory(self.root)
        self.path = self.root / 'settings.json'
        self.vault = SecretVault(self.root / 'private')
        self.limits = Limits(**self.load().get('limits', {}))
        l = self.limits
        if not (1 <= l.max_jobs <= 4 and 1 <= l.max_frames <= 16 and 1 <= l.initial_frames <= l.max_frames
                and 1 <= l.max_video_seconds <= 7200 and 10 <= l.job_timeout_seconds <= 1800
                and 1024**2 <= l.max_download_bytes <= 1024**3 and l.max_download_bytes <= l.cache_bytes <= 8*1024**3
                and 60 <= l.cache_ttl_seconds <= 7*86400 and 160 <= l.frame_width <= 1920
                and l.asr_backend in ('whispercpp','faster_whisper','disabled')):
            raise Failure('invalid_config', 'B站资源限制超出支持范围。')

    def load(self):
        return read_json(self.path, dict(schema=1, enabled=True, qq_root='', qq_account='', qq_groups=[], generation=0))

    def save(self, changes):
        from chatlocal.snowluma_secure import file_lock
        with file_lock(self.root / 'settings.lock'):
            data = self.load()
            data.update(changes)
            data['generation'] = data.get('generation', 0) + 1
            atomic_json(self.path, data)
        return data

    def cookie(self, purpose='content'):
        # Deliberately separate: QR login does not authorize media credentials.
        return self.vault.read().get(purpose + '_cookie', '')

    def set_cookie(self, cookie, purpose='content'):
        from chatlocal.snowluma_secure import file_lock
        with file_lock(self.root / 'vault.lock'):
            data = self.vault.read()
            data[purpose + '_cookie'] = cookie
            self.vault.write(data)
        self.save({})  # Invalidate in-flight work when credentials are replaced/revoked.

    def clear_login(self):
        from chatlocal.snowluma_secure import file_lock
        with file_lock(self.root / 'vault.lock'):
            saved = self.vault.read()
            saved.pop('content_cookie', None)
            saved.pop('media_cookie', None)
            self.vault.write(saved)
        self.save({})

    def assert_generation(self, generation):
        data = self.load()
        if not data.get('enabled') or data.get('generation', 0) != generation:
            raise Failure('authorization_changed', '配置或授权已经改变，本次旧任务失效。')

    def public_limits(self):
        values = asdict(self.limits)
        configured = values.pop('asr_model_path')
        if self.limits.asr_backend == 'whispercpp':
            from .media import whispercpp_paths
            executable, model = whispercpp_paths()
            values['asr_model_available'] = executable.is_file() and model.is_file()
        else:
            values['asr_model_available'] = bool(configured)
        return values
