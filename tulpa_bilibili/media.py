"""Bounded local evidence processing, inspired by Yueying's subtitle/frame flow.

Subprocesses consume local files only. No shell, browser-cookie extraction,
remote ASR service, implicit model download, or generated video summary.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from chatlocal.snowluma_secure import local_path
from .errors import Failure, public_error
from .net import validate_url


def run_local(argv, cancel, deadline):
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        while process.poll() is None:
            if cancel.wait(.1):
                raise Failure('cancelled', '任务已取消。')
            if time.monotonic() >= deadline:
                raise Failure('processing_timeout', '本地媒体处理超时。', retryable=True)
        if process.returncode:
            raise Failure('processing_failed', '本地媒体处理失败；未把封面或元数据当作视频内容。')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def whispercpp_paths():
    root = Path(__file__).resolve().parents[1]
    executable = root / ('tools/whispercpp/Release/whisper-cli.exe' if os.name=='nt' else 'tools/whispercpp/bin/whisper-cli')
    return executable, root/'.cache/voice-models/ggml-small-q5_1.bin'


class GuardedCancel:
    """Cooperative revocation checks at IO/processing boundaries, never a new model."""
    def __init__(self, guard):
        self.event, self.guard, self.checked = threading.Event(), guard, 0

    def set(self):
        self.event.set()

    def is_set(self):
        if not self.event.is_set() and time.monotonic()-self.checked >= .25:
            try:
                self.guard()
            except Exception:
                self.event.set()
            self.checked = time.monotonic()
        return self.event.is_set()

    def wait(self, seconds):
        return self.event.wait(seconds) or self.is_set()


class Jobs:
    def __init__(self, settings, store, content):
        self.settings, self.store, self.content = settings, store, content
        self.pool = ThreadPoolExecutor(settings.limits.max_jobs, thread_name_prefix='bilibili-read')
        self.lock = threading.RLock()
        self.cancels = {}
        self.future = {}
        self.work = local_path(settings.root / 'work')
        self.work.mkdir(exist_ok=True)

    def recover(self):
        # Only the process holding service.lock may invalidate old work.
        self.store.recover()
        self.store.cleanup()
        for folder in self.work.iterdir():
            if len(folder.name)!=32 or any(c not in '0123456789abcdef' for c in folder.name):
                continue
            folder=local_path(folder)
            if not folder.is_dir():
                continue
            for child in folder.iterdir():
                child=local_path(child)
                if child.is_file():
                    child.unlink()
            if not any(folder.iterdir()):
                folder.rmdir()

    def start(self, kind, args, *, owner='', guard=lambda:None):
        guard()
        self.content.client()  # No download jobs before the content login gate.
        generation = self.settings.load()['generation']
        self.settings.assert_generation(generation)
        with self.lock:
            finished=[key for key,future in self.future.items() if future.done()]
            for key in finished:
                self.future.pop(key,None);self.cancels.pop(key,None)
            self.store.prune_jobs()
            active = sum(not x.done() for x in self.future.values())
            if active >= self.settings.limits.max_jobs:
                raise Failure('busy', '媒体任务已达到并发上限，请等待或取消现有任务。', retryable=True)
            key = uuid.uuid4().hex
            now = time.time()
            with self.store.db() as db:
                db.execute('INSERT INTO jobs(id,kind,generation,state,created,updated,args,result,error,owner) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (key, kind, generation, 'queued', now, now, json.dumps(args), None, None, owner))
            cancel = GuardedCancel(guard)
            self.cancels[key] = cancel
            self.future[key] = self.pool.submit(self._run, key, cancel)
            return dict(job_id=key, state='queued', poll_after_seconds=2, next_tool='get_video_job')

    def owned(self, key, owner):
        row = self.store.job(key)
        if row['owner'] != owner:
            raise Failure('job_not_found', '本连接没有这个任务；权限修改后请重新创建只读任务。')
        return row

    def cancel(self, key, *, owner=''):
        row = self.owned(key, owner)
        self.settings.assert_generation(row['generation'])
        with self.lock:
            if key in self.cancels:
                self.cancels[key].set()
        return dict(job_id=key, state=row['state'], cancellation_requested=row['state'] in ('queued','running'))

    def cancel_all(self, grant_id=None):
        with self.lock:
            for key, event in self.cancels.items():
                with self.store.db() as db:
                    row=db.execute('SELECT owner FROM jobs WHERE id=?',(key,)).fetchone()
                if grant_id is None or row and row['owner'].startswith(grant_id+':'):
                    event.set()

    def close(self):
        self.cancel_all()
        self.pool.shutdown(wait=True, cancel_futures=True)

    def status(self, key, *, owner=''):
        row = self.owned(key, owner)
        self.settings.assert_generation(row['generation'])
        # Also revalidate the platform session before returning private evidence.
        self.content.client()
        result = dict(job_id=key, state=row['state'], created_at=row['created'], updated_at=row['updated'])
        if row['result']:
            result['result'] = row['result']
        if row['error']:
            result['error'] = row['error']
        result['poll_after_seconds'] = 2 if row['state'] in ('queued', 'running') else None
        return result

    def _run(self, key, cancel):
        row = self.store.job(key)
        folder = local_path(self.work / key)
        folder.mkdir()
        deadline = time.monotonic()+self.settings.limits.job_timeout_seconds
        try:
            if cancel.is_set():
                raise Failure('cancelled', '任务权限已失效或任务已取消。')
            with self.store.db() as db:
                db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (time.time(), key))
            self.settings.assert_generation(row['generation'])
            result = self._evidence(row, folder, cancel, deadline)
            self.settings.assert_generation(row['generation'])
            if cancel.is_set():
                raise Failure('cancelled', '任务已取消。')
            with self.store.db() as db:
                db.execute("UPDATE jobs SET state='succeeded',result=?,updated=? WHERE id=?",
                           (json.dumps(result), time.time(), key))
        except Exception as exc:
            error = public_error(exc)['error']
            state = 'cancelled' if error['code'] in ('cancelled', 'authorization_changed') else 'failed'
            with self.store.db() as db:
                db.execute('UPDATE jobs SET state=?,error=?,updated=? WHERE id=?',
                           (state, json.dumps(error), time.time(), key))
        finally:
            # Only files created inside this UUID directory; no recursive deletion.
            for child in folder.iterdir():
                local_path(child).unlink()
            folder.rmdir()
            self.store.prune_jobs()

    def _download(self, info, folder, cancel, deadline, generation):
        identity = f'{generation}:{info["bvid"]}:{info["page"]}:{info["cid"]}'
        key = identity + ':video'
        cached = self.store.resource(key)
        audio = self.store.resource(identity + ':audio')
        if cached:
            return cached, audio, True
        streams = self.content.play(info, cancel=cancel, deadline=deadline)
        duration = streams.get('duration_seconds')
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration > self.settings.limits.max_video_seconds:
            raise Failure('duration_limit', '媒体流时长未知或超过限制。')
        total = 0
        paths = {}
        for kind in ('video', 'audio'):
            url = streams.get(kind)
            if not url:
                paths[kind] = None
                continue
            validate_url(url, 'media')
            self.settings.assert_generation(generation)
            temp = folder / (kind + '.download')
            self.store.cleanup(reserve=self.settings.limits.max_download_bytes)
            self.content.net.get(url, purpose='media', output=temp,
                max_bytes=self.settings.limits.max_download_bytes-total, cancel=cancel, deadline=deadline)
            total += temp.stat().st_size
            # Commit both streams only after every required download succeeded.
            paths[kind] = temp
        video = self.store.put(key, paths['video'])
        audio = self.store.put(identity+':audio', paths['audio']) if paths['audio'] else None
        return video, audio, False

    def _evidence(self, row, folder, cancel, deadline):
        args = row['args']
        if cancel.is_set():
            raise Failure('cancelled', '任务已取消。')
        info = self.content.info(args['video'], args.get('page'))
        duration = info['duration_seconds']
        if not isinstance(duration, (int,float)) or not math.isfinite(duration) or duration <= 0 or duration > self.settings.limits.max_video_seconds:
            raise Failure('duration_limit', '当前视频分 P 时长未知或超过限制，请换用更短的视频。')
        start, end = args.get('start_seconds', 0), args.get('end_seconds', duration)
        if not 0 <= start < end <= duration:
            raise Failure('invalid_range', '读取区间必须位于所选分 P 的有效时间轴内。')
        result = dict(bvid=info['bvid'], page=info['page'], cid=info['cid'], fetched_at=time.time(),
                      requested_range=[start,end], frames=[], speech=dict(state='not_requested'), warnings=[],
                      content_trust='untrusted_data', evidence_level='metadata_only')
        if row['kind'] == 'read_video':
            try:
                sub = self.content.subtitles(info, cancel=cancel, deadline=deadline)
                entries = [e for e in sub['entries'] if e['end'] > start and e['start'] < end]
                if entries:
                    # Explicit truncation preserves context size; caller can request narrower range.
                    selected, chars = [], 0
                    for e in entries:
                        if len(selected) >= 600 or chars+len(e['content']) > 24000:
                            break
                        selected.append(e); chars += len(e['content'])
                    result['speech'] = dict(state='available', source='platform_subtitles', language=sub['language'],
                                            entries=selected, truncated=len(selected)<len(entries),
                                            coverage=[selected[0]['start'], selected[-1]['end']])
            except Failure as exc:
                result['warnings'].append(dict(component='subtitles', **exc.result()['error']))
        times = args.get('timestamps')
        if times is None:
            count = self.settings.limits.initial_frames
            times = [round(start+(end-start)*(i+.5)/count, 3) for i in range(count)]
        if not times or len(times)>self.settings.limits.max_frames or any(not 0<=t<duration for t in times):
            raise Failure('invalid_timestamps', '抽帧时间超出分 P 或帧数上限。')
        # Serialize cache users (two short jobs may queue); prevents eviction races
        # and bounds temporary download bytes to one media job at a time.
        while not self.store.cache_lock.acquire(timeout=.2):
            if cancel.is_set() or time.monotonic()>=deadline:
                raise Failure('cancelled' if cancel.is_set() else 'processing_timeout', '媒体任务等待已结束。')
        try:
            try:
                video, audio, hit = self._download(info, folder, cancel, deadline, row['generation'])
                result['cache_hit'] = hit
                for index, stamp in enumerate(times):
                    self.settings.assert_generation(row['generation'])
                    output = folder/f'frame-{index}.jpg'
                    run_local([ffmpeg(), '-nostdin','-v','error','-protocol_whitelist','file,pipe',
                               '-ss',str(stamp),'-i',str(video),'-frames:v','1','-vf',
                               f'scale={self.settings.limits.frame_width}:-2','-q:v','3',str(output)],
                              cancel, min(deadline, time.monotonic()+30))
                    if not output.exists() or output.stat().st_size > 512*1024:
                        raise Failure('frame_failed', '该时间点没有可用画面。')
                    result['frames'].append(dict(timestamp_seconds=stamp, mime_type='image/jpeg',
                        source='decoded_video_frame', data=base64.b64encode(output.read_bytes()).decode()))
                if row['kind']=='read_video' and result['speech']['state']!='available':
                    result['speech'] = self._asr(audio or video, folder, start, end, cancel, deadline)
            except Failure as exc:
                if exc.code in ('cancelled','authorization_changed'):
                    raise
                result['warnings'].append(dict(component='media', **exc.result()['error']))
                if result['speech']['state']=='not_requested' and row['kind']=='read_video':
                    result['speech'] = dict(state='unavailable', reason=exc.code)
        finally:
            self.store.cache_lock.release()
        has_speech = result['speech']['state']=='available'
        has_frames = bool(result['frames'])
        result['evidence_level'] = 'subtitles_and_frames' if has_speech and has_frames else 'speech_only' if has_speech else 'frames_only' if has_frames else 'metadata_only'
        result['coverage_note'] = '仅这些时间点是真实画面；字幕/ASR区间和缺失项分别列出，不代表已完整理解音画。'
        return result

    def _asr(self, audio, folder, start, end, cancel, deadline):
        if self.settings.limits.asr_backend=='whispercpp':
            executable, model = whispercpp_paths()
            if not executable.is_file() or not model.is_file():
                return dict(state='unavailable',reason='local_asr_not_installed',
                            next_step='请在 Tulpa 首页的可选组件安装本地语音模型，再重新读取。不会调用外部模型。')
            from chatlocal.optional_components import VOICE
            with model.open('rb') as handle:
                if hashlib.file_digest(handle,'sha256').hexdigest()!=VOICE['sha256']:
                    raise Failure('asr_model_invalid','本地语音模型校验失败，请重新安装可选组件。')
            wav, result = folder/'audio.wav', folder/'asr'
            run_local([ffmpeg(),'-nostdin','-v','error','-protocol_whitelist','file,pipe','-ss',str(start),
                       '-i',str(audio),'-t',str(end-start),'-vn','-ac','1','-ar','16000',str(wav)],cancel,deadline)
            run_local([str(executable),'-m',str(model),'-f',str(wav),'-l','auto','-t','4','-oj','-of',str(result),'-np'],cancel,deadline)
            raw=json.loads(result.with_suffix('.json').read_text(encoding='utf-8'))
            entries=[];chars=0
            for item in raw.get('transcription',[]):
                text=str(item.get('text','')).strip();offsets=item.get('offsets',{})
                if not text or type(offsets.get('from')) not in (int,float) or type(offsets.get('to')) not in (int,float):continue
                if len(entries)>=600 or chars+len(text)>24000:break
                entries.append(dict(start=start+offsets['from']/1000,end=start+offsets['to']/1000,content=text));chars+=len(text)
            if not entries:return dict(state='unavailable',reason='no_recognizable_speech')
            return dict(state='available',source='local_whispercpp_asr',entries=entries,
                        coverage=[start,end],truncated=len(entries)<len(raw.get('transcription',[])),
                        note='复用 Tulpa 本地 Whisper 组件；机器转写可能误听，不是平台字幕。')
        model = self.settings.limits.asr_model_path
        if self.settings.limits.asr_backend!='faster_whisper' or not model or not Path(model).is_dir() or not importlib.util.find_spec('faster_whisper'):
            return dict(state='unavailable', reason='local_asr_not_installed',
                        next_step='此视频无可用字幕；可在高级设置配置本地 faster-whisper 模型，不会自动下载或调用外部模型。')
        wav, result = folder/'audio.wav', folder/'asr.json'
        run_local([ffmpeg(),'-nostdin','-v','error','-protocol_whitelist','file,pipe','-ss',str(start),
                   '-i',str(audio),'-t',str(end-start),'-vn','-ac','1','-ar','16000',str(wav)],cancel,deadline)
        run_local([sys.executable,'-m','tulpa_bilibili.asr_worker',str(Path(model).resolve()),str(wav),str(result)],cancel,deadline)
        entries = json.loads(result.read_text(encoding='utf-8'))
        if not entries:return dict(state='unavailable',reason='no_recognizable_speech')
        selected=[];chars=0
        for e in entries:
            e['start'] += start
            e['end'] += start
            if len(selected)>=600 or chars+len(e['content'])>24000:break
            selected.append(e);chars+=len(e['content'])
        return dict(state='available',source='local_faster_whisper_asr', entries=selected,
                    truncated=len(selected)<len(entries), coverage=[start,end],
                    note='本地机器转写，可能误听；没有平台字幕时才使用。')
