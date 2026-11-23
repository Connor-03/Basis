"""Builds what a release ships, into dist/:

    basis-<version>-app.zip    the program's files with MANIFEST.json (what the in-app updater downloads)
    SHA256SUMS.txt             checksums of everything in dist/
    stage/app/                 the same files laid out for the installer (packaging/windows/basis.iss)
    stage/python/              with --python: the Windows embeddable Python the installer bundles

    python tools/build_release.py [--python 3.12.10]

Only files that are tracked here go in: the program itself, never data, backups, logs or a local config.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, 'dist')
TOP = ['server.py', 'basis.pyw', 'VERSION', 'README.md', 'LICENSE', 'CHANGELOG.md']
FOLDERS = {'fin': ('.py',), 'agent': ('.md',), 'docs': ('.md',),
           'web': ('.js', '.css', '.html', '.svg', '.png', '.ico', '.webmanifest', '.woff2', '.txt')}


def files():
    out = [f for f in TOP if os.path.isfile(os.path.join(ROOT, f))]
    for folder, exts in FOLDERS.items():
        for base, dirs, names in os.walk(os.path.join(ROOT, folder)):
            dirs[:] = sorted(d for d in dirs if d != '__pycache__' and d != 'img')
            for n in sorted(names):
                if n.endswith(exts):
                    out.append(os.path.relpath(os.path.join(base, n), ROOT).replace('\\', '/'))
    return out


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    with open(os.path.join(ROOT, 'VERSION'), encoding='utf-8') as f:
        version = f.read().strip()
    shutil.rmtree(DIST, ignore_errors=True)
    stage = os.path.join(DIST, 'stage', 'app')
    os.makedirs(stage)
    manifest = {'version': version, 'files': {}}
    blobs = {}
    for rel in files():
        with open(os.path.join(ROOT, rel), 'rb') as f:
            data = f.read()
        if rel.endswith(('.py', '.pyw', '.js', '.css', '.html', '.md', '.svg', '.webmanifest', '.txt')) or rel in ('VERSION', 'LICENSE'):
            data = data.replace(b'\r\n', b'\n')              # the same bytes whichever system built it
        blobs[rel] = data
        manifest['files'][rel] = sha(data)
    blobs['MANIFEST.json'] = json.dumps(manifest, indent=1, sort_keys=True).encode()
    name = f'basis-{version}-app.zip'
    with zipfile.ZipFile(os.path.join(DIST, name), 'w', zipfile.ZIP_DEFLATED) as z:
        for rel, data in sorted(blobs.items()):
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
            dst = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as f:
                f.write(data)
    if '--python' in sys.argv:
        pv = sys.argv[sys.argv.index('--python') + 1]
        url = f'https://www.python.org/ftp/python/{pv}/python-{pv}-embed-amd64.zip'
        print('fetching', url)
        raw = urllib.request.urlopen(url, timeout=120).read()
        pdir = os.path.join(DIST, 'stage', 'python')
        zipfile.ZipFile(io.BytesIO(raw)).extractall(pdir)
        print('python', pv, len(os.listdir(pdir)), 'files, sha256', sha(raw))
    print(name, len(manifest['files']), 'files')
    return version


def sums():
    lines = []
    for n in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, n)
        if os.path.isfile(p) and n != 'SHA256SUMS.txt':
            with open(p, 'rb') as f:
                lines.append(f'{sha(f.read())}  {n}')
    with open(os.path.join(DIST, 'SHA256SUMS.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    if '--sums' in sys.argv:
        sums()
    else:
        main()
        sums()
