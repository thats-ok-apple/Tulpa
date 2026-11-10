"""Follow native background jobs (data reads, watch checks, component installs) on a view."""
from . import model
from .base import esc, seg

DONE = ('completed', 'error', 'failed', 'cancelled', 'interrupted', 'done', 'ok')


def job_lines(job):
    if not isinstance(job, dict):
        return '任务状态未知。'
    status = str(job.get('status') or '—')
    lines = [f'状态：{status}']
    progress = job.get('progress') if isinstance(job.get('progress'), dict) else {}
    if progress.get('text'):
        lines.append(str(progress['text']))
    events = job.get('events') if isinstance(job.get('events'), list) else []
    lines += [model.event_line(e) for e in events[-8:]]
    if job.get('detail'):
        lines.append(str(job['detail']))
    result = job.get('result')
    if isinstance(result, dict):
        if result.get('summary'):
            lines.append(str(result['summary']))
        elif result.get('title') and status == 'completed':
            lines.append('已完成：' + str(result['title']))
    elif isinstance(result, list):
        for r in result:
            if isinstance(r, dict):
                lines.append(f'{model.platform_label(r.get("platform"))}：{r.get("status", "")} 新增 {r.get("added", 0)} '
                             + str(r.get('detail') or ''))
    if job.get('error'):
        lines.append('错误：' + str(job['error']))
    return '\n'.join(l for l in lines if l)


def follow(view, job_id, *, kind='data', box=None, done=None, label='任务'):
    """Poll a job until it finishes, rendering its progress into `box` (a Static).

    kind='data' uses the native /api/watch-jobs/{id}; kind='component' uses the
    terminal /jobs/{id}. `done(job)` runs once on completion.
    """
    if not job_id:
        view.set_state('error', f'{label}未返回任务编号。')
        return
    view.current_job = (kind, job_id)
    if kind == 'data':
        fetch = lambda c: c.api_get(f'/api/watch-jobs/{seg(job_id)}')  # noqa: E731
    else:
        fetch = lambda c: c.get(f'/jobs/{seg(job_id)}')  # noqa: E731

    def tick(job):
        job = job if isinstance(job, dict) else {}
        if box is not None:
            box.update(f'[b #a78bfa]{esc(label)}[/]\n' + esc(job_lines(job)))
            box.display = True
        status = str(job.get('status') or '')
        if status in ('running', 'queued', 'pending', 'downloading', 'cancelling', ''):
            view.set_state('loading', f'{label}运行中…（x 停止）')
            return True
        view.current_job = None
        if status in ('completed', 'done', 'ok'):
            view.set_state('info', f'{label}已完成。')
        else:
            view.set_state('error', f'{label}未完成：{job.get("error") or job.get("detail") or status}')
        if done:
            done(job)
        return False
    view.poll('job', fetch, tick)


def stop_job(view):
    current = getattr(view, 'current_job', None)
    if not current:
        view.set_state('info', '没有正在跟踪的任务。')
        return
    kind, job_id = current
    if kind != 'data':
        view.set_state('info', '组件安装任务不能从界面中途停止；可等待完成或失败。')
        return
    view.act(lambda c: c.api_post(f'/api/watch-jobs/{seg(job_id)}/stop', {}),
             lambda r: view.set_state('info', '已请求停止；已提交的批次和进度保留。'), '正在请求停止…', '停止失败')
