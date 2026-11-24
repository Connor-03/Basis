"""End-to-end check of the desktop program, on Windows or Linux, with no real accounts.

It builds the release files, runs the launcher from a copy of them against a stand-in SnapTrade (tools/mock_snaptrade.py)
and a stand-in for GitHub's release page, and walks through what a new user does: save a key, link a brokerage, first
sync. Then it installs an update, and an update that is broken on purpose to see the old version come back.

    python tools/smoke.py [--python PATH_TO_PYTHON] [--stage BUILT_APP_FOLDER] [--keep]
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PY = os.path.abspath(sys.argv[sys.argv.index('--python') + 1]) if '--python' in sys.argv else sys.executable
WIN = sys.platform == 'win32'
MOCK, GH, APP = 8461, 8462, 8463
RELEASE = {}                                      # what the stand-in GitHub serves
FAILS = []


def ok(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        FAILS.append(what)


def call(path, body=None, host=None, plain=False, timeout=30):
    """(status, json) from the program. plain=True leaves out the header every write needs."""
    headers = {'Host': host or f'127.0.0.1:{APP}'}
    if not plain:
        headers.update({'X-Requested-With': 'finance', 'Origin': f'http://127.0.0.1:{APP}'})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'http://127.0.0.1:{APP}{path}', data=data, headers=headers, method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b'{', b'[') else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw[:1] == b'{' else raw)
    except Exception as e:
        return 0, {'error': str(e)}


def wait(fn, seconds, what):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.5)
    ok(False, f'waited {seconds}s for: {what}')
    return None


class GitHub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = RELEASE.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header('Content-Length', str(len(body or b'')))
        self.end_headers()
        self.wfile.write(body or b'')


def publish(app, version, change):
    """Put a made-up release on the stand-in GitHub: the installed files with `change` applied."""
    with open(os.path.join(app, 'MANIFEST.json'), encoding='utf-8') as f:
        names = [n for n in json.load(f)['files'] if n != 'MANIFEST.json']
    files = {}
    for rel in names:
        with open(os.path.join(app, rel), 'rb') as f:
            files[rel] = f.read()
    files['VERSION'] = (version + '\n').encode()
    change(files)
    manifest = {'version': version, 'files': {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
        z.writestr('MANIFEST.json', json.dumps(manifest))
    blob, name = buf.getvalue(), f'basis-{version}-app.zip'
    base = f'http://127.0.0.1:{GH}'
    RELEASE.clear()
    RELEASE.update({'/' + name: blob, '/SHA256SUMS.txt': f'{hashlib.sha256(blob).hexdigest()}  {name}\n'.encode(),
                    '/latest': json.dumps({'tag_name': 'v' + version, 'body': f'Test release {version}.', 'html_url': base,
                                           'assets': [{'name': name, 'browser_download_url': f'{base}/{name}'},
                                                      {'name': 'SHA256SUMS.txt', 'browser_download_url': f'{base}/SHA256SUMS.txt'}]}).encode()})


def main():
    stage = os.path.abspath(sys.argv[sys.argv.index('--stage') + 1]) if '--stage' in sys.argv else None
    if not stage:                                       # no built files handed over: build them now
        from tools import build_release
        build_release.main()
        stage = os.path.join(ROOT, 'dist', 'stage', 'app')
    tmp = tempfile.mkdtemp(prefix='basis-smoke-')
    app = os.path.join(tmp, 'app')
    shutil.copytree(stage, app)
    env = dict(os.environ, FIN_MODE='desktop', FIN_DATA=os.path.join(tmp, 'data'), FIN_BACKUPS=os.path.join(tmp, 'backups'),
               FIN_LOGS=os.path.join(tmp, 'logs'), SNAPTRADE_BASE=f'http://127.0.0.1:{MOCK}', BASIS_TEST='1',
               BASIS_UPDATE_API=f'http://127.0.0.1:{GH}/latest')
    env.pop('FIN_CONFIG', None)
    print('python:', PY, '| folder:', tmp, flush=True)
    mock = subprocess.Popen([PY, '-B', '-X', 'utf8', os.path.join(ROOT, 'tools', 'mock_snaptrade.py'), '--port', str(MOCK)], stdout=subprocess.DEVNULL)
    gh = ThreadingHTTPServer(('127.0.0.1', GH), GitHub)
    threading.Thread(target=gh.serve_forever, daemon=True).start()
    launcher = subprocess.Popen([PY, '-B', '-X', 'utf8', os.path.join(app, 'basis.pyw'), '--no-window', '--port', str(APP)], env=env, cwd=tmp)
    try:
        st = wait(lambda: call('/api/status')[1].get('app') == 'basis' and call('/api/status')[1], 120, 'the program to start')
        ok(bool(st), f"started, version {st and st.get('version')}")
        if not st:
            return
        # what any page on the internet could try
        ok(call('/api/model', host='evil.example')[0] == 421, 'a request under another host name is refused')
        ok(call('/api/snaptrade/keys', {'client_id': 'x', 'consumer_key': 'y'}, plain=True)[0] == 403, 'a write without the app header is refused')
        code, page = call('/')
        ok(code == 200 and b'Basis' in page, 'the page loads')
        for p in ('/static/app.js', '/static/app.css', '/static/icon.svg', '/recover'):
            ok(call(p)[0] == 200, f'{p} loads')
        code, fr = call('/api/first-run')
        ok(code == 200 and fr['mode'] == 'desktop' and not fr['ready'] and not fr['has_keys'], 'a new install starts at the welcome page')
        code, m = call('/api/model')
        ok(code == 200 and m['app']['ready'] is False and m['positions'] == [], 'the model builds with no accounts')
        # the key
        secret_key = 'smoke-consumer-key-9876'
        ok(call('/api/snaptrade/keys', {'client_id': 'SMOKE', 'consumer_key': secret_key})[0] == 200, 'the key is saved')
        with open(os.path.join(tmp, 'data', 'snaptrade.json'), encoding='utf-8') as f:
            stored = f.read()
        if WIN:
            ok(secret_key not in stored and 'dpapi:' in stored, 'the key on disk is encrypted for this Windows account')
        else:
            ok(secret_key in stored, 'the key file is written (owner only)')
        ok(secret_key not in json.dumps(call('/api/first-run')[1]) and secret_key not in json.dumps(call('/api/snaptrade')[1]), 'the key never goes back to the page')
        # link a brokerage and sync
        code, link = call('/api/snaptrade/connect', {})
        ok(code == 200 and '/portal' in link.get('url', ''), 'a connection link is made')
        urllib.request.urlopen(f'http://127.0.0.1:{MOCK}/portal/done', timeout=10).read()
        fr = wait(lambda: (call('/api/first-run')[1].get('connections') or None) and call('/api/first-run')[1], 40, 'the link to be noticed')
        ok(bool(fr) and fr['connections'][0]['broker'] == 'Demo Brokerage', 'the linked brokerage is noticed')
        ok(call('/api/first-run', {'action': 'sync'})[0] == 200, 'the first sync starts')
        fr = wait(lambda: (lambda d: d if not d['job']['running'] and (d['job']['done'] or d['job']['error']) else None)(call('/api/first-run')[1]), 420, 'the first sync to finish')
        ok(bool(fr) and fr['job']['done'] and fr['ready'], f"the first sync finishes ({fr and fr['job'].get('error')})")
        code, m = call('/api/model', timeout=90)
        ok(code == 200 and len(m['accounts']) == 3 and len(m['positions']) >= 14, f"3 accounts and {len(m.get('positions', []))} holdings are read")
        ok(m['net_worth'] > 40000 and m['app']['ready'], f"net worth is computed ({round(m['net_worth'])})")
        mg = (m.get('margin') or [{}])[0]
        ok(12000 < mg.get('debt', 0) < 16000, f"the margin loan is read ({round(mg.get('debt', 0))})")
        ok(all(not a['gaps']['shares'] for a in m['accounts']), 'share counts match the broker')
        types = {p.get('type') for p in m['positions']}
        ok('Bitcoin & Equivalents' in types and 'Crypto & Equivalents' in types, 'Bitcoin and crypto are grouped on their own')
        for p in ('/api/setup', '/api/snaptrade', '/api/agent', '/api/update', '/api/doom?brief=1', '/api/activity'):
            ok(call(p, timeout=120)[0] == 200, f'{p} answers')
        # an account switched off is left out of everything, and comes back whole when switched on
        cr = next(a for a in m['accounts'] if 'Crypto' in a['name'])
        ok(call('/api/account/hide', {'id': cr['id'], 'hidden': True})[0] == 200, 'an account is switched off')
        code, m2 = call('/api/model', timeout=90)
        held = {x['id'] for p in m2.get('positions', []) for x in p['accounts']}
        ok(code == 200 and len(m2['accounts']) == 2 and [a['id'] for a in m2['hidden_accounts']] == [cr['id']] and cr['id'] not in held,
           'it is left out of the accounts and holdings')
        acts = call('/api/activity?limit=3000')[1]['rows']
        ok(bool(acts) and all(r['account'] != cr['name'] for r in acts), 'and out of the activity list')
        ok(not call('/api/snaptrade')[1]['diffs'], 'the broker check shows no differences for it')
        ok(call('/api/account/hide', {'id': cr['id'], 'hidden': False})[0] == 200, 'it is switched back on')
        code, m3 = call('/api/model', timeout=90)
        ok(code == 200 and len(m3['accounts']) == 3 and len(m3['positions']) == len(m['positions']) and not m3['hidden_accounts'],
           'every account and holding is back')
        # an update, with a file the user changed
        with open(os.path.join(app, 'README.md'), 'a', encoding='utf-8') as f:
            f.write('\nmy own note\n')
        publish(app, '9.9.9', lambda files: files.update({'web/app.css': files['web/app.css'] + b'\n/* 9.9.9 */\n'}))
        code, u = call('/api/update', {'action': 'check'})
        ok(code == 200 and u['available'] and u['latest'] == '9.9.9', 'a newer version is found')
        ok(u['customized'] == ['README.md'], 'the file the user changed is listed')
        before = call('/api/status')[1].get('started')
        ok(call('/api/update', {'action': 'apply'})[0] == 200, 'the update starts')
        # a new process on 9.9.9: the old one reads the new VERSION file in the moment before it exits
        st = wait(lambda: (lambda d: d if d.get('version') == '9.9.9' and d.get('started') != before else None)(call('/api/status')[1]), 180, 'the program to come back on 9.9.9')
        ok(bool(st), 'the update is installed and the program restarted')
        code, u = call('/api/update')
        last = u.get('last') or {}
        ok(last.get('to') == '9.9.9' and last.get('replaced') == ['README.md'], 'the update is recorded')
        ok(bool(last.get('kept')) and os.path.exists(os.path.join(app, last['kept'], 'README.md')), 'the changed file was kept aside')
        with open(os.path.join(app, 'web', 'app.css'), 'rb') as f:
            ok(b'/* 9.9.9 */' in f.read(), 'the new files are in place')
        code, m = call('/api/model', timeout=90)
        ok(code == 200 and len(m['positions']) >= 14, 'the data is untouched by the update')
        # an update that cannot start: the old version must come back
        publish(app, '9.9.10', lambda files: files.update({'server.py': b'raise SystemExit(3)\n'}))
        call('/api/update', {'action': 'check'})
        before = call('/api/status')[1].get('started')
        ok(call('/api/update', {'action': 'apply'})[0] == 200, 'a broken update starts')
        time.sleep(4)
        st = wait(lambda: (lambda d: d if d.get('app') == 'basis' and d.get('started') != before else None)(call('/api/status')[1]), 240, 'the program to come back after the broken update')
        ok(bool(st) and st.get('version') == '9.9.9', f"the broken update is undone (running {st and st.get('version')})")
        with open(os.path.join(app, 'server.py'), 'rb') as f:
            ok(len(f.read()) > 1000, 'server.py is the working one again')
        log_path = os.path.join(tmp, 'logs', 'server.log')
        with open(log_path, encoding='utf-8', errors='replace') as f:
            log_text = f.read()
        ok('Traceback' not in log_text.replace('SystemExit', ''), 'no errors in the server log')
        if 'Traceback' in log_text:
            print(log_text[-3000:])
        call('/api/quit', {})
        ok(wait(lambda: launcher.poll() is not None, 40, 'the launcher to stop') is not None, 'quitting stops everything')
    finally:
        for p in (launcher, mock):
            if p.poll() is None:
                p.kill()
        gh.shutdown()
        if '--keep' not in sys.argv and not FAILS:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print('kept', tmp)
    print(f'\n{len(FAILS)} failed' if FAILS else '\nall passed')
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
