"""Web push for the Home Screen app: alerts for the Monday options menu, a thin margin cushion and big daily moves.

The VAPID signing and aes128gcm encryption are the same code as ~/calendar/push.py (RFC 8292 / RFC 8291 on the
cryptography package), with this app's own key in data/vapid.json (600) and subscriptions in the push_subs table.
"""
import base64
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from . import paths

try:                                              # phone alerts are for a copy served over https from a home server;
    from cryptography.hazmat.primitives import hashes, serialization           # the desktop program ships without
    from cryptography.hazmat.primitives.asymmetric import ec                    # this package and simply has none
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    AVAILABLE = paths.MODE == 'server'
except ImportError:
    AVAILABLE = False

DATA = paths.DATA
KEY_PATH = os.path.join(DATA, 'vapid.json')
SUBJECT = 'mailto:basis@localhost'
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "notify.windows.com", "push.services.mozilla.com")

_lock = threading.Lock()
_key = None
_jwts = {}


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    s = str(s).strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _point(pub):
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ----------------------------------------------------------------------------- keys and signing

def vapid_key():
    global _key
    with _lock:
        if _key is None:
            if os.path.exists(KEY_PATH):
                with open(KEY_PATH) as f:
                    _key = serialization.load_pem_private_key(json.load(f)["private_pem"].encode(), None)
            else:
                key = ec.generate_private_key(ec.SECP256R1())
                pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
                fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump({"private_pem": pem, "public": b64u(_point(key.public_key()))}, f)
                _key = key
        return _key


def public_key():
    return b64u(_point(vapid_key().public_key()))


def vapid_header(endpoint):
    u = urllib.parse.urlsplit(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    token, exp = _jwts.get(aud, (None, 0))
    if exp - time.time() < 3600:
        exp = int(time.time()) + 12 * 3600
        enc = lambda d: b64u(json.dumps(d, separators=(",", ":")).encode())
        signing = enc({"typ": "JWT", "alg": "ES256"}) + "." + enc(
            {"aud": aud, "exp": exp, "sub": SUBJECT})
        r, s = decode_dss_signature(vapid_key().sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        token = signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        _jwts[aud] = (token, exp)
    return f"vapid t={token}, k={public_key()}"


def encrypt(plaintext, p256dh, auth):
    """RFC 8291 aes128gcm body for one browser subscription (a single record)."""
    ua_pub = unb64u(p256dh)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    eph = ec.generate_private_key(ec.SECP256R1())
    as_pub = _point(eph.public_key())
    ikm = HKDF(hashes.SHA256(), 32, unb64u(auth), b"WebPush: info\x00" + ua_pub + as_pub).derive(
        eph.exchange(ec.ECDH(), ua_key))
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    ct = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_pub)) + as_pub + ct


# ----------------------------------------------------------------------------- subscriptions

def check_endpoint(endpoint):
    u = urllib.parse.urlsplit(endpoint or "")
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS):
        raise ValueError("not a known browser push service")



SCHEMA = """CREATE TABLE IF NOT EXISTS push_subs(id INTEGER PRIMARY KEY, endpoint TEXT UNIQUE NOT NULL, p256dh TEXT NOT NULL,
  auth TEXT NOT NULL, label TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP, last_ok TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS push_sent(key TEXT PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP);"""


def init(db):
    with db.lock:
        db.c.executescript(SCHEMA)


def subscribe(db, sub, label):
    endpoint = (sub or {}).get('endpoint') or ''
    keys = (sub or {}).get('keys') or {}
    check_endpoint(endpoint)
    try:
        if len(unb64u(keys.get('p256dh', ''))) != 65 or len(unb64u(keys.get('auth', ''))) != 16:
            raise ValueError
    except ValueError:
        raise ValueError('subscription keys are missing or malformed')
    label = (label or 'Device').strip()[:60] or 'Device'
    db.x('INSERT INTO push_subs(endpoint, p256dh, auth, label) VALUES(?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET '
         'p256dh=excluded.p256dh, auth=excluded.auth, label=excluded.label, last_error=NULL',
         (endpoint, keys['p256dh'], keys['auth'], label))
    return db.one('SELECT id FROM push_subs WHERE endpoint=?', (endpoint,))['id']


def public_subs(db):
    return [{'id': s['id'], 'label': s['label'], 'created': s['created'], 'last_ok': s['last_ok'],
             'last_error': s['last_error']} for s in db.q('SELECT * FROM push_subs ORDER BY id')]


def send_one(sub, payload, ttl=4 * 3600):
    body = encrypt(json.dumps(payload).encode(), sub['p256dh'], sub['auth'])
    req = urllib.request.Request(sub['endpoint'], data=body, method='POST', headers={
        'Authorization': vapid_header(sub['endpoint']), 'TTL': str(ttl), 'Urgency': 'high',
        'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, ''
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode('utf-8', 'replace').strip()
    except (urllib.error.URLError, OSError) as e:
        return 0, str(getattr(e, 'reason', e))


def deliver(db, payload, only=None, log=print):
    ok = 0
    for s in db.q('SELECT * FROM push_subs ORDER BY id'):
        if only is not None and s['id'] != only:
            continue
        status, err = send_one(s, payload)
        now = datetime.now().isoformat(timespec='seconds')
        if 200 <= status < 300:
            ok += 1
            db.x('UPDATE push_subs SET last_ok=?, last_error=NULL WHERE id=?', (now, s['id']))
        elif status in (404, 410):
            db.x('DELETE FROM push_subs WHERE id=?', (s['id'],))
            log(f"push: {s['label']} subscription expired ({status}); removed")
        else:
            db.x('UPDATE push_subs SET last_error=? WHERE id=?', (f"{status or 'network'} {err}"[:200], s['id']))
    return ok


def once(db, key, payload, log=print):
    """Send a notification only the first time this key is seen (e.g. 'menu:2026-11-23')."""
    if db.one('SELECT key FROM push_sent WHERE key=?', (key,)):
        return 0
    if not db.one('SELECT id FROM push_subs LIMIT 1'):
        return 0
    db.x('INSERT INTO push_sent(key) VALUES(?)', (key,))
    return deliver(db, payload, log=log)
