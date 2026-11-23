"""SQLite storage. Raw import rows are kept exactly as exported; everything else is rebuilt from them."""
import json
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, broker TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'taxable',
  margin INTEGER NOT NULL DEFAULT 0, cash_known REAL, cash_known_date TEXT, sort INTEGER DEFAULT 0,
  note TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS imports(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, filename TEXT, broker TEXT, rows_total INTEGER,
  rows_added INTEGER, first_date TEXT, last_date TEXT, sha256 TEXT, stored TEXT, undone INTEGER DEFAULT 0,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS raw_rows(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, import_id INTEGER NOT NULL, pos INTEGER NOT NULL,
  fp TEXT NOT NULL, occ INTEGER NOT NULL, activity_date TEXT, data TEXT NOT NULL,
  UNIQUE(account_id, fp, occ));
CREATE INDEX IF NOT EXISTS raw_rows_acct ON raw_rows(account_id, activity_date);
CREATE TABLE IF NOT EXISTS opening(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL, cost REAL,
  as_of TEXT, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS basis(
  account_id INTEGER NOT NULL, symbol TEXT NOT NULL, d TEXT NOT NULL, cost REAL NOT NULL,
  PRIMARY KEY(account_id, symbol, d));
CREATE TABLE IF NOT EXISTS manual(
  id INTEGER PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'other', symbol TEXT, qty REAL,
  value REAL, cost REAL, liability INTEGER DEFAULT 0, note TEXT DEFAULT '', updated TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS prices(symbol TEXT NOT NULL, d TEXT NOT NULL, close REAL, adj REAL,
  PRIMARY KEY(symbol, d)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS splits(symbol TEXT NOT NULL, d TEXT NOT NULL, ratio REAL NOT NULL, PRIMARY KEY(symbol, d));
CREATE TABLE IF NOT EXISTS divs(symbol TEXT NOT NULL, ex TEXT NOT NULL, amount REAL NOT NULL, pay TEXT,
  source TEXT NOT NULL, PRIMARY KEY(symbol, ex, source));
CREATE TABLE IF NOT EXISTS symbols(symbol TEXT PRIMARY KEY, name TEXT, type TEXT, profile TEXT, profile_at TEXT,
  history_at TEXT, history_from TEXT, divs_at TEXT, failed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS quotes(symbol TEXT PRIMARY KEY, data TEXT, at REAL);
CREATE TABLE IF NOT EXISTS ath(symbol TEXT PRIMARY KEY, high REAL, d TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS macro_daily(series TEXT NOT NULL, d TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY(series, d)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS fundamentals(symbol TEXT NOT NULL, kind TEXT NOT NULL, data TEXT, at TEXT, PRIMARY KEY(symbol, kind)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS moves(
  id INTEGER PRIMARY KEY, d TEXT NOT NULL, title TEXT NOT NULL, note TEXT DEFAULT '', kind TEXT NOT NULL DEFAULT 'note',
  data TEXT DEFAULT '{}', snap TEXT DEFAULT '{}', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS balances(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, d TEXT NOT NULL, kind TEXT NOT NULL, value REAL NOT NULL,
  note TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS snap_accounts(
  snap_id TEXT PRIMARY KEY, account_id INTEGER, institution TEXT, name TEXT, number TEXT, kind TEXT,
  authorization_id TEXT, meta TEXT, synced_at TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS adjustments(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, d TEXT NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL,
  note TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS snap_snapshots(
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, at TEXT NOT NULL, positions TEXT, options TEXT, balances TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP, what TEXT, detail TEXT);
"""


class DB:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        self.path = path
        self.lock = threading.RLock()
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA journal_mode=WAL')
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.executescript(SCHEMA)
        cols = {r[1] for r in self.c.execute('PRAGMA table_info(accounts)')}
        for col, decl in (('cash_note', "TEXT DEFAULT ''"), ('maint_scale', 'REAL'), ('maint_note', "TEXT DEFAULT ''")):
            if col not in cols:
                self.c.execute(f'ALTER TABLE accounts ADD COLUMN {col} {decl}')
        if new:
            os.chmod(path, 0o600)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def tuples(self, sql, args=()):
        """Rows as plain tuples: for big reads where building a dict per row costs more than the query."""
        with self.lock:
            cur = self.c.cursor()
            cur.row_factory = None
            try:
                return cur.execute(sql, args).fetchall()
            finally:
                cur.close()

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args)
            return cur.lastrowid

    def many(self, sql, rows):
        with self.lock:
            self.c.execute('BEGIN')
            try:
                self.c.executemany(sql, rows)
                self.c.execute('COMMIT')
            except Exception:
                self.c.execute('ROLLBACK')
                raise

    def tx(self):
        return _Tx(self)

    def setting(self, key, default=None):
        r = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(r['value']) if r else default

    def set_setting(self, key, value):
        self.x('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (key, json.dumps(value)))

    def log(self, what, detail=''):
        self.x('INSERT INTO log(what, detail) VALUES(?,?)', (what, detail if isinstance(detail, str) else json.dumps(detail)))


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.c.execute('BEGIN')
        return self.db.c

    def __exit__(self, et, ev, tb):
        try:
            self.db.c.execute('ROLLBACK' if et else 'COMMIT')
        finally:
            self.db.lock.release()
