"""Where things live, and which of the two ways the program is running.

- desktop: the installed program on someone's own computer, started by basis.pyw. It listens on this computer only and
  keeps its data in the user's own data folder, never inside the program folder, so an update or the Customize chat
  cannot reach it.
- server: a copy kept running on a home server (cron starts server.py), with a config.json beside server.py that says
  which addresses to listen on. Data, backups and logs sit in the program folder.
"""
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get('FIN_CONFIG') or os.path.join(APP, 'config.json')
MODE = os.environ.get('FIN_MODE') or ('server' if os.path.exists(CONFIG_PATH) else 'desktop')
NAME = 'Basis'
REPO = 'Connor-03/Basis'                         # where releases are published (fin/update.py)


def user_dir():
    """The per-user folder for this program's data on a desktop."""
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~\\AppData\\Local'), NAME)
    if sys.platform == 'darwin':
        return os.path.expanduser(f'~/Library/Application Support/{NAME}')
    return os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), NAME.lower())


_home = APP if MODE == 'server' else user_dir()
DATA = os.environ.get('FIN_DATA') or os.path.join(_home, 'data')
BACKUPS = os.environ.get('FIN_BACKUPS') or os.path.join(_home, 'backups')
LOGS = os.environ.get('FIN_LOGS') or os.path.join(_home, 'logs')
MANIFEST = os.path.join(APP, 'MANIFEST.json')    # written when a release is built: every shipped file and its hash


def version():
    try:
        with open(os.path.join(APP, 'VERSION'), encoding='utf-8') as f:
            return f.read().strip() or '0'
    except OSError:
        return '0'


def ensure():
    for d in (DATA, BACKUPS, LOGS):
        os.makedirs(d, mode=0o700, exist_ok=True)
