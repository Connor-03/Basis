"""Starting and stopping other programs the same way on Windows and Linux.

On Windows the app runs without a console (pythonw), and any console program it starts would flash a black window
unless told not to. Text is always read as UTF-8.
"""
import os
import subprocess
import sys

WIN = sys.platform == 'win32'
_NO_WINDOW = 0x08000000
_NEW_GROUP = 0x00000200


def _wrap(cmd):
    """A .cmd or .bat file (how npm installs command line tools on Windows) has to go through cmd.exe."""
    if WIN and str(cmd[0]).lower().endswith(('.cmd', '.bat')):
        return [os.environ.get('COMSPEC') or 'cmd.exe', '/d', '/c'] + list(cmd)
    return list(cmd)


def popen(cmd, group=False, **kw):
    """subprocess.Popen without a console window; group=True puts it in its own process group so kill_tree works."""
    if kw.get('text'):
        kw.setdefault('encoding', 'utf-8')
        kw.setdefault('errors', 'replace')
    if WIN:
        kw['creationflags'] = kw.get('creationflags', 0) | _NO_WINDOW | (_NEW_GROUP if group else 0)
    elif group:
        kw['start_new_session'] = True
    return subprocess.Popen(_wrap(cmd), **kw)


def run(cmd, **kw):
    if kw.get('text') or kw.get('capture_output'):
        kw.setdefault('text', True)
        kw.setdefault('encoding', 'utf-8')
        kw.setdefault('errors', 'replace')
    if WIN:
        kw['creationflags'] = kw.get('creationflags', 0) | _NO_WINDOW
    return subprocess.run(_wrap(cmd), **kw)


def kill_tree(proc):
    """Stop a program started with group=True, and anything it started."""
    if proc.poll() is not None:
        return
    try:
        if WIN:
            run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, 15)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        try:
            if WIN:
                proc.kill()
            else:
                os.killpg(proc.pid, 9)
        except OSError:
            pass
