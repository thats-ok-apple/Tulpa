"""Process identities and owned children on Linux; no shell or privilege changes."""
import os
from pathlib import Path
import signal
import subprocess
import sys


def linux_identity(pid):
    if type(pid) is not int or pid <= 0:
        return None
    try:
        root = Path('/proc') / str(pid)
        if root.stat().st_uid != os.getuid():
            return None
        fields = (root/'stat').read_text().rsplit(')', 1)[1].split()
        return dict(pid=pid, created=fields[19], executable=os.readlink(root/'exe'),
                    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    except (OSError, ValueError, IndexError):
        return None


def linux_processes():
    for path in Path('/proc').iterdir():
        if path.name.isdecimal() and (identity := linux_identity(int(path.name))):
            yield identity


def owned_popen(args, *, memory_bytes=0, **kwargs):
    if os.name == 'nt':
        return subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)
    wrapper = Path(__file__).with_name('posix_child.py')
    # Isolate this stdlib-only bootstrap: its directory contains collections.py,
    # which must never shadow Python's collections during interpreter startup.
    return subprocess.Popen([sys.executable, '-I', str(wrapper), '--parent', str(os.getpid()),
                             '--memory', str(memory_bytes), '--', *map(str, args)],
                            start_new_session=True, **kwargs)


def terminate_group(process):
    """Only accepts our still-owned Popen object; never a persisted PID."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
