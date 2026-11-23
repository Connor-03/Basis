"""Basis for the desktop: starts the program, opens its window, and closes it all when the window closes.

    pythonw -B -X utf8 basis.pyw            the installed shortcut runs this
    python  -B -X utf8 basis.pyw --no-window --port 8455      for tests: no window, runs until stopped

What it does:
- keeps one copy running: a second start only opens another window on the first;
- starts server.py on this computer's own address (127.0.0.1) and waits for it to answer;
- opens the page in an app window (Microsoft Edge or Chrome without the browser frame), or a browser tab if neither
  is found;
- restarts the server when it asks for one (after an update or a change made in the Customize chat), and puts the
  files back as they were if it does not come back;
- stops the server when the window is closed (the page says goodbye as it closes; if it could not, a few quiet
  minutes do it).
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ['FIN_MODE'] = 'desktop'
from fin import paths, proc  # noqa: E402

WIN = sys.platform == 'win32'
RUN = os.path.join(paths.DATA, 'run.json')
RESTART = os.path.join(paths.DATA, 'restart.json')
LOG = os.path.join(paths.LOGS, 'basis.log')
PORT = 8440


def log(*a):
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + ' '.join(str(x) for x in a) + '\n')
    except OSError:
        pass


def tell(text):
    """A message box on Windows (there is no console to print to), a printed line elsewhere."""
    log(text)
    if WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, paths.NAME, 0x10)
    else:
        print(text)


def status(port, timeout=2.5):
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/status', headers={'Host': f'127.0.0.1:{port}'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
            return d if d.get('app') == 'basis' else None
    except Exception:
        return None


def post(port, path, body):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode(), method='POST',
                                 headers={'Host': f'127.0.0.1:{port}', 'Origin': f'http://127.0.0.1:{port}',
                                          'X-Requested-With': 'finance', 'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=4).read()
    except Exception:
        pass


def free_port(want):
    """The usual port if nothing has it, else any free one."""
    for port in (want, 0):
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return want


def start_server(port):
    out = open(os.path.join(paths.LOGS, 'server.log'), 'a', encoding='utf-8')
    env = dict(os.environ, FIN_MODE='desktop')
    return proc.popen([sys.executable, '-B', '-X', 'utf8', os.path.join(HERE, 'server.py'), '--port', str(port)],
                      cwd=HERE, env=env, stdout=out, stderr=out, stdin=subprocess.DEVNULL)


def wait_up(port, server, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if server.poll() is not None:
            return False
        if status(port, 1.5):
            return True
        time.sleep(0.4)
    return False


def browsers():
    """Edge first (every Windows 10 and 11 has it), then Chrome: both can show a page as an app window."""
    if not WIN:
        return [p for p in ('/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser') if os.path.exists(p)]
    roots = [os.environ.get(k) for k in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA')]
    spots = [os.path.join(r, 'Microsoft', 'Edge', 'Application', 'msedge.exe') for r in roots if r]
    spots += [os.path.join(r, 'Google', 'Chrome', 'Application', 'chrome.exe') for r in roots if r]
    return [p for p in spots if os.path.isfile(p)]


def open_window(port):
    """Returns the window's process when it can be followed (app window with its own profile), else None."""
    url = f'http://127.0.0.1:{port}/'
    for exe in browsers():
        try:
            profile = os.path.join(paths.user_dir(), 'window')
            return proc.popen([exe, f'--app={url}', f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
                               '--window-size=1500,960', '--disable-features=Translate'], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log('window:', exe, e)
    webbrowser.open(url)
    return None


def undo(snap):
    """Put the program's files back as they were before change `snap` (fin/agent.py keeps the copies)."""
    try:
        from fin import agent
        agent.undo_cli(snap, failed=True)
        log('undone', snap)
    except Exception as e:
        log('undo failed', snap, e)


def main():
    args = sys.argv[1:]
    headless = '--no-window' in args
    want = int(args[args.index('--port') + 1]) if '--port' in args else PORT
    paths.ensure()
    # one copy at a time: if it is already running, show its window and leave
    try:
        with open(RUN, encoding='utf-8') as f:
            old = json.load(f)
        if status(old.get('port')):
            if not headless:
                open_window(old['port'])
            return 0
    except (OSError, ValueError):
        pass
    port = free_port(want)
    server = start_server(port)
    with open(RUN, 'w', encoding='utf-8') as f:
        json.dump({'port': port, 'pid': os.getpid(), 'at': time.time()}, f)
    log(f'starting {paths.NAME} {paths.version()} on port {port}')
    if not wait_up(port, server, 90):
        tell(f'{paths.NAME} could not start. The details are in:\n{os.path.join(paths.LOGS, "server.log")}')
        proc.kill_tree(server)
        return 1
    window = None if headless else open_window(port)
    followed = window is not None
    crashes, gone_at = 0, None
    try:
        while True:
            time.sleep(1)
            code = server.poll()
            if code == 0:                                    # it was told to quit
                break
            if code is not None:
                snap = ''
                if code == 75:                               # asked for: an update or a change to server code
                    try:
                        with open(RESTART, encoding='utf-8') as f:
                            snap = json.load(f).get('snap') or ''
                        os.remove(RESTART)
                    except (OSError, ValueError):
                        pass
                    log('restart asked', snap)
                else:
                    crashes += 1
                    log('server stopped with code', code)
                    if crashes > 3:
                        tell(f'{paths.NAME} keeps stopping. The details are in:\n{os.path.join(paths.LOGS, "server.log")}')
                        return 1
                server = start_server(port)
                if not wait_up(port, server, 90):
                    proc.kill_tree(server)
                    if snap:
                        log('did not come back; undoing', snap)
                        undo(snap)
                        server = start_server(port)
                        if wait_up(port, server, 90):
                            continue
                    tell(f'{paths.NAME} could not restart. The details are in:\n{os.path.join(paths.LOGS, "server.log")}')
                    return 1
                continue
            if headless:
                continue
            # when to stop: the page said goodbye and nothing has asked since (the window was closed); or the window's
            # own process is gone and it has been quiet; or, for a plain browser tab, it has been quiet for a long time
            st = status(port)
            if not st:
                continue
            idle = st.get('idle', 0)
            if followed and window.poll() is not None:
                gone_at = gone_at or time.time()
            if (st.get('closed') and idle > 8) or (gone_at and time.time() - gone_at > 5 and idle > 150) or (not followed and idle > 300):
                break
    except KeyboardInterrupt:
        pass
    finally:
        post(port, '/api/quit', {})
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill_tree(server)
        try:
            os.remove(RUN)
        except OSError:
            pass
        log('stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
