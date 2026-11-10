"""Shared isolated fixtures, independent of the optional terminal UI."""
from contextlib import contextmanager
import os
import socket
import threading
import time


def isolated_env():
    """Clear user configuration while retaining Windows TLS/runtime roots."""
    return {key: value for key, value in os.environ.items()
            if key.upper() in {'SYSTEMROOT', 'WINDIR'}}


@contextmanager
def management_http(app):
    import uvicorn
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    # TestClient owns application startup/shutdown in the caller.
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+5
        while not server.started and time.monotonic() < deadline: time.sleep(.02)
        if not server.started: raise AssertionError('Management HTTP did not start')
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(5); sock.close()
        if thread.is_alive(): raise AssertionError('Management HTTP did not stop')
