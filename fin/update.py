"""Updates for the installed desktop program, from the project's GitHub releases.

Checking is a setting the user can turn off (Data and settings, About); with it off nothing here touches the network.
A release carries two files this module uses: basis-<version>-app.zip (the program's files, with a MANIFEST.json
listing each file and its SHA-256) and SHA256SUMS.txt.

Applying an update:
1. download the zip and check it against SHA256SUMS.txt, then check every file inside against the manifest;
2. copy the current program files aside (the same snapshot the Customize chat uses, so the update can be undone);
3. any file the user changed since it was installed is also copied to kept/<old version>/ inside the program folder,
   where they (and the Customize chat) can read it and bring the change back;
4. put the new files in place and restart. If the program does not come back, the launcher restores the snapshot.

Only the program's own files are replaced. The data folder is never touched.
"""
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime

from . import paths

TESTING = bool(os.environ.get('BASIS_TEST'))                 # tests point this at a local stand-in for GitHub
API = (TESTING and os.environ.get('BASIS_UPDATE_API')) or f'https://api.github.com/repos/{paths.REPO}/releases/latest'
PAGE = f'https://github.com/{paths.REPO}/releases'
ALLOWED_HOSTS = ('github.com', 'api.github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com')
TOP = ('fin/', 'web/', 'agent/', 'docs/')                 # folders an update may write into
TOP_FILES = ('server.py', 'basis.pyw', 'VERSION', 'README.md', 'LICENSE', 'CHANGELOG.md', 'MANIFEST.json')
MAX_BYTES = 60_000_000
EVERY = 12 * 3600

_lock = threading.Lock()
_state = {'latest': None, 'notes': '', 'url': PAGE, 'asset': None, 'sums': None, 'checked': 0, 'error': None,
          'busy': False, 'step': ''}


def supported():
    """Only an installed desktop copy updates itself; a source checkout or a server copy is updated by hand."""
    return paths.MODE == 'desktop' and os.path.exists(paths.MANIFEST)


def _ver(v):
    return tuple(int(x) for x in re.findall(r'\d+', str(v or ''))[:3]) or (0,)


def _get(url, limit=MAX_BYTES):
    req = urllib.request.Request(url, headers={'User-Agent': f'Basis/{paths.version()}', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=45) as r:
        end = urllib.parse.urlparse(r.geturl())
        host = end.hostname or ''
        if not (TESTING and host == '127.0.0.1') and (end.scheme != 'https' or host not in ALLOWED_HOSTS):
            raise ValueError(f'The download was sent to an address this program does not trust ({host}).')
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError('The download is larger than an update should be.')
    return data


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    with open(path, 'rb') as f:
        return _sha(f.read())


def installed():
    """The manifest of what was installed: {'version', 'files': {path: sha256}}."""
    try:
        with open(paths.MANIFEST, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'version': paths.version(), 'files': {}}


def customized():
    """Shipped files the user (or the Customize chat) has changed since they were installed."""
    out = []
    for rel, sha in sorted(installed().get('files', {}).items()):
        p = os.path.join(paths.APP, rel)
        if rel != 'MANIFEST.json' and os.path.exists(p) and _file_sha(p) != sha:
            out.append(rel)
    return out


def check(settings, force=False):
    """Ask GitHub for the newest release (at most every 12 hours unless forced). Does nothing when checking is off."""
    if not supported() or not (settings.get('check', True) or force):
        return
    with _lock:
        if _state['busy'] or (not force and time.time() - _state['checked'] < EVERY):
            return
        _state['checked'] = time.time()
    try:
        rel = json.loads(_get(API, 2_000_000))
        tag = str(rel.get('tag_name') or '').lstrip('v')
        assets = {a.get('name'): a.get('browser_download_url') for a in rel.get('assets') or []}
        with _lock:
            _state.update({'latest': tag or None, 'notes': str(rel.get('body') or '')[:4000], 'url': rel.get('html_url') or PAGE,
                           'asset': assets.get(f'basis-{tag}-app.zip'), 'sums': assets.get('SHA256SUMS.txt'), 'error': None})
    except Exception as e:                                     # offline, rate limited, no release yet
        with _lock:
            _state['error'] = str(e)[:200]


def state(settings):
    cur = paths.version()
    with _lock:
        s = dict(_state)
    newer = bool(s['latest']) and _ver(s['latest']) > _ver(cur)
    last = _last()
    return {'supported': supported(), 'mode': paths.MODE, 'current': cur, 'latest': s['latest'], 'available': newer and bool(s['asset']),
            'installer_only': newer and not s['asset'], 'notes': s['notes'] if newer else '', 'url': s['url'],
            'check': bool(settings.get('check', True)), 'checked': s['checked'], 'error': s['error'], 'busy': s['busy'],
            'step': s['step'], 'customized': customized() if supported() and newer else [], 'last': last}


def _last_path():
    return os.path.join(paths.DATA, 'update.json')


def _last():
    """The last update applied here, for the note the page shows afterwards."""
    try:
        with open(_last_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def dismiss():
    last = _last()
    if last:
        last['seen'] = True
        with open(_last_path(), 'w', encoding='utf-8') as f:
            json.dump(last, f)
    return {'ok': True}


def _safe(name):
    """A path inside the zip is taken only if it stays inside the program folder and in a place updates own."""
    if not name or name.startswith(('/', '\\')) or '..' in name.replace('\\', '/').split('/') or ':' in name or '\\' in name:
        return False
    return name in TOP_FILES or name.startswith(TOP)


def apply(snapshot, restart):
    """Download, check and install the newest release. snapshot() copies the current files aside and returns its id;
    restart(id) restarts the program, restoring that snapshot if it does not come back. Runs in its own thread."""
    with _lock:
        if _state['busy']:
            raise ValueError('An update is already running.')
        if not supported():
            raise ValueError('This copy does not update itself.')
        asset, sums, latest = _state['asset'], _state['sums'], _state['latest']
        if not asset or not sums or _ver(latest) <= _ver(paths.version()):
            raise ValueError('There is no newer version to install.')
        _state.update({'busy': True, 'step': 'Downloading', 'error': None})

    def work():
        try:
            blob = _get(asset)
            listed = {}
            for line in _get(sums, 200_000).decode('utf-8', 'replace').splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    listed[parts[-1].lstrip('*')] = parts[0].lower()
            want = listed.get(f'basis-{latest}-app.zip')
            if not want or _sha(blob) != want:
                raise ValueError('The download does not match its published checksum, so it was not installed.')
            _state['step'] = 'Checking the files'
            z = zipfile.ZipFile(io.BytesIO(blob))
            new = json.loads(z.read('MANIFEST.json').decode('utf-8'))
            if _ver(new.get('version')) != _ver(latest):
                raise ValueError('The download is not the version it says it is.')
            files = {}
            for rel, sha in new['files'].items():
                if not _safe(rel):
                    raise ValueError(f'The update names a file outside the program ({rel}), so it was not installed.')
                data = z.read(rel)
                if _sha(data) != sha:
                    raise ValueError(f'{rel} in the download does not match its checksum, so nothing was installed.')
                files[rel] = data
            old = installed()
            mine = customized()
            _state['step'] = 'Saving the current version'
            snap = snapshot()
            kept = ''
            if mine:
                kept = os.path.join('kept', f"{old.get('version') or paths.version()}-{datetime.now():%Y%m%d-%H%M}")
                for rel in mine:
                    dst = os.path.join(paths.APP, kept, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(os.path.join(paths.APP, rel), dst)
            _state['step'] = 'Installing'
            for rel, data in files.items():
                if rel == 'MANIFEST.json':
                    continue
                dst = os.path.join(paths.APP, rel)
                os.makedirs(os.path.dirname(dst) or paths.APP, exist_ok=True)
                with open(dst + '.new', 'wb') as f:
                    f.write(data)
                os.replace(dst + '.new', dst)
            for rel in old.get('files', {}):                  # files the new version no longer has
                if rel not in files and _safe(rel) and rel not in mine:
                    try:
                        os.remove(os.path.join(paths.APP, rel))
                    except OSError:
                        pass
            with open(paths.MANIFEST, 'wb') as f:
                f.write(z.read('MANIFEST.json'))
            with open(_last_path(), 'w', encoding='utf-8') as f:
                json.dump({'from': old.get('version') or paths.version(), 'to': new.get('version'), 'snap': snap, 'kept': kept.replace('\\', '/'),
                           'replaced': mine, 'at': time.time(), 'seen': False}, f)
            _state['step'] = 'Restarting'
            restart(snap)
        except Exception as e:
            _state['error'] = str(e)[:300]
        finally:
            _state['busy'] = False

    threading.Thread(target=work, daemon=True).start()
    return {'ok': True}
