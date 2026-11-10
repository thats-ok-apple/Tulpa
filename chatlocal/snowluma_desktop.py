"""Configure the private Docker login desktop; executable inside the SL image.

Only Python's standard library and the image's X11 library are used. No QQ
process, login credentials or application data are touched.
"""
import ctypes
import json
import os
from pathlib import Path
import tempfile


def replace_text(path, text, mode=0o644):
    """Publish a complete config without exposing a partial browser response."""
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            name = stream.name
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def configure_files(root=Path('/')):
    # A URL default is insufficient: existing phone bookmarks/settings may
    # select remote resize or clipping, neither usable with this fixed Xvfb.
    mandatory = root/'opt/noVNC/mandatory.json'
    settings = json.loads(mandatory.read_text('utf-8')) if mandatory.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError('noVNC mandatory settings must be an object')
    settings['resize'] = 'scale'
    replace_text(mandatory, json.dumps(settings, ensure_ascii=False, indent=2)+'\n')

    # Fluxbox binds wheel events on the desktop to workspace changes. Mobile
    # scrolling can therefore hide QQ on an empty desktop without any failure
    # in Xvfb/VNC. This dedicated login desktop needs only one workspace.
    config = root/'root/.fluxbox/init'
    lines = config.read_text('utf-8').splitlines()
    lines = [line for line in lines if not line.lstrip().startswith('session.screen0.workspaces:')]
    lines.append('session.screen0.workspaces: 1')
    replace_text(config, '\n'.join(lines)+'\n')

    # x11vnc also reads this at its next start, independently of the host
    # process. Preserve authentication options; only replace sharing flags.
    vnc = root/'root/.x11vncrc'
    lines = vnc.read_text('utf-8').splitlines() if vnc.exists() else []
    lines = [line for line in lines if line.strip().lstrip('-') not in ('shared', 'alwaysshared', 'nevershared')]
    lines.append('shared')
    replace_text(vnc, '\n'.join(lines)+'\n', mode=0o600)


def restore_workspace(display=b':1'):
    """Use standard window-manager requests; do not restart X, VNC or QQ."""
    x = ctypes.CDLL('libX11.so.6')
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x.XInternAtom.restype = ctypes.c_ulong

    class Message(ctypes.Structure):
        _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                    ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                    ('format', ctypes.c_int), ('data', ctypes.c_long*5)]

    class Event(ctypes.Union):
        _fields_ = [('message', Message), ('pad', ctypes.c_long*24)]

    x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.POINTER(Event)]
    x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    connection = x.XOpenDisplay(display)
    if not connection:
        raise RuntimeError('Login desktop is unavailable')
    try:
        root = x.XDefaultRootWindow(connection)
        for atom, value in ((b'_NET_CURRENT_DESKTOP', 0), (b'_NET_NUMBER_OF_DESKTOPS', 1)):
            event = Event()
            event.message.type = 33  # ClientMessage
            event.message.display = connection
            event.message.window = root
            event.message.message_type = x.XInternAtom(connection, atom, 0)
            event.message.format = 32
            event.message.data[0] = value
            if not x.XSendEvent(connection, root, 0, (1 << 20) | (1 << 19), ctypes.byref(event)):
                raise RuntimeError('Could not restore the login workspace')
        x.XSync(connection, 0)
    finally:
        x.XCloseDisplay(connection)


if __name__ == '__main__':
    configure_files()
    restore_workspace()
