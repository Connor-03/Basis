"""Prints the CHANGELOG.md section for one version, followed by how to install: the text of a GitHub release.

    python tools/release_notes.py 1.0.0
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
version = sys.argv[1]
with open(os.path.join(ROOT, 'CHANGELOG.md'), encoding='utf-8') as f:
    text = f.read()
start = text.find(f'## {version}')
body = ''
if start >= 0:
    rest = text[start:].split('\n', 1)[1]
    body = rest.split('\n## ', 1)[0].strip()
sys.stdout.reconfigure(encoding='utf-8')
print(body or f'Basis {version}.')
print()
print('**Install on Windows:** download `Basis-Setup.exe` below and run it. Windows may say it does not recognize the '
      'publisher (the installer is not code-signed): choose "More info", then "Run anyway".')
print()
print('If you already have Basis, use the Update button in its top right corner instead.')
