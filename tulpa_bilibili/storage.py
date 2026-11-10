"""Private job, cache and send receipts. No cookies or signed media URLs."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from chatlocal.snowluma_secure import local_path
from .errors import Failure


class Storage:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.root / 'state.sqlite3'
        self.cache = local_path(settings.root / 'cache')
        self.cache.mkdir(exist_ok=True)
        self.cache_lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, generation INTEGER,
                    state TEXT, created REAL, updated REAL, args TEXT, result TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS resources(key TEXT PRIMARY KEY, filename TEXT UNIQUE,
                    size INTEGER, digest TEXT, used REAL, expires REAL);
                CREATE TABLE IF NOT EXISTS shares(key TEXT PRIMARY KEY, binding TEXT, generation INTEGER,
                    state TEXT, created REAL, updated REAL, result TEXT);
                CREATE TABLE IF NOT EXISTS approvals(key TEXT PRIMARY KEY, binding TEXT, generation INTEGER,
                    expires REAL, approved INTEGER DEFAULT 0);
            ''')
            if 'title' not in {r[1] for r in db.execute('PRAGMA table_info(approvals)')}:
                db.execute("ALTER TABLE approvals ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            if 'owner' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN owner TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE jobs SET state='interrupted', error=?, updated=? WHERE state IN ('queued','running')",
                       (json.dumps(dict(code='process_restarted', message='程序重启，未完成任务已终止，可重新创建只读任务。', retryable=True)), time.time()))
            # An interrupted send may already have reached QQ. Never turn it into a retry.
            db.execute("UPDATE shares SET state='uncertain',updated=? WHERE state='sending'", (time.time(),))
        self.prune_jobs()

    def prune_jobs(self):
        # Inline image evidence is bounded independently of the media LRU.
        # Keep at most 32 completed tasks / 64 MiB / the configured cache TTL.
        with self.db() as db:
            rows=db.execute("SELECT id,updated,length(CAST(COALESCE(result,'') AS BLOB)) AS bytes FROM jobs WHERE state NOT IN ('queued','running') ORDER BY updated DESC").fetchall()
            total=0
            for index,row in enumerate(rows):
                total+=row['bytes']
                if index>=32 or total>64*1024*1024 or row['updated']<time.time()-self.settings.limits.cache_ttl_seconds:
                    db.execute('DELETE FROM jobs WHERE id=?',(row['id'],))
            db.execute('DELETE FROM approvals WHERE expires<?',(time.time(),))

    def resource(self, key):
        with self.cache_lock, self.db() as db:
            row = db.execute('SELECT * FROM resources WHERE key=?', (key,)).fetchone()
            if not row:
                return None
            path = local_path(self.cache / row['filename'])
            valid = row['expires'] > time.time() and path.is_file() and path.stat().st_size == row['size']
            if valid:
                with path.open('rb') as f:
                    valid = hashlib.file_digest(f, 'sha256').hexdigest() == row['digest']
            if not valid:
                db.execute('DELETE FROM resources WHERE key=?', (key,))
                path.unlink(missing_ok=True)
                return None
            db.execute('UPDATE resources SET used=? WHERE key=?', (time.time(), key))
            return path

    def cleanup(self, reserve=0):
        # Caller holds cache_lock for the entire use/download, so a live resource
        # cannot be evicted by another worker while ffmpeg is reading it.
        with self.cache_lock, self.db() as db:
            rows = db.execute('SELECT * FROM resources ORDER BY used').fetchall()
            total = sum(r['size'] for r in rows)
            for row in rows:
                if row['expires'] <= time.time() or total + reserve > self.settings.limits.cache_bytes:
                    local_path(self.cache / row['filename']).unlink(missing_ok=True)
                    db.execute('DELETE FROM resources WHERE key=?', (row['key'],))
                    total -= row['size']
            if total + reserve > self.settings.limits.cache_bytes:
                raise Failure('cache_full', '媒体缓存容量不足，请降低单文件上限或清理缓存。')

    def put(self, key, temp):
        with self.cache_lock:
            size = temp.stat().st_size
            self.cleanup(reserve=size)
            name = hashlib.sha256(key.encode()).hexdigest() + '.bin'
            path = local_path(self.cache / name)
            with temp.open('rb') as f:
                digest = hashlib.file_digest(f, 'sha256').hexdigest()
            temp.replace(path)
            now = time.time()
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO resources VALUES(?,?,?,?,?,?)',
                           (key, name, size, digest, now, now+self.settings.limits.cache_ttl_seconds))
            return path

    def clear_cache(self):
        with self.cache_lock, self.db() as db:
            for row in db.execute('SELECT filename FROM resources'):
                local_path(self.cache / row['filename']).unlink(missing_ok=True)
            db.execute('DELETE FROM resources')

    def job(self, key):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone()
        if not row:
            raise Failure('job_missing', '任务不存在。')
        result = dict(row)
        for k in ('args', 'result', 'error'):
            result[k] = json.loads(result[k]) if result[k] else None
        return result
