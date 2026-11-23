"""Keeps the price cache current: daily history (with splits and dividends), live quotes, sector profiles and
declared dividends. Called by the server's background thread; safe to run by hand: python3 -m fin.sync"""
import json
import time
from datetime import date, datetime, timedelta

from . import market

YH = market.Yahoo()


def _today():
    return date.today().isoformat()


def history(db, symbols, start, force=False, log=print):
    """Fetch or extend daily bars. Re-reads the last 10 days each time so late corrections land."""
    done = 0
    for s in symbols:
        row = db.one('SELECT history_at, history_from, failed FROM symbols WHERE symbol=?', (s,))
        if row and not force:
            if row['history_at'] and row['history_at'][:10] == _today() and (row['history_from'] or '9') <= start:
                continue
            if row['failed'] and row['failed'] >= 3 and row['history_at'] and row['history_at'][:10] >= \
                    (date.today() - timedelta(days=7)).isoformat():
                continue
        last = db.one('SELECT MAX(d) AS d, MIN(d) AS f FROM prices WHERE symbol=?', (s,))
        frm = start
        if last and last['d'] and last['f'] and last['f'] <= start:
            frm = (date.fromisoformat(last['d']) - timedelta(days=10)).isoformat()
        try:
            h = YH.history(s, frm)
        except Exception as e:
            db.x('INSERT INTO symbols(symbol, failed, history_at) VALUES(?,1,?) ON CONFLICT(symbol) DO UPDATE SET '
                 'failed=COALESCE(failed,0)+1, history_at=excluded.history_at', (s, datetime.now().isoformat()))
            log(f'history {s}: {e}')
            continue
        db.many('INSERT OR REPLACE INTO prices(symbol, d, close, adj) VALUES(?,?,?,?)',
                [(s, d, c, a) for d, c, a in h['bars']])
        if h['splits']:
            db.many('INSERT OR REPLACE INTO splits(symbol, d, ratio) VALUES(?,?,?)', [(s, d, r) for d, r in h['splits']])
        if h['divs']:
            db.many("INSERT OR REPLACE INTO divs(symbol, ex, amount, pay, source) VALUES(?,?,?,NULL,'yahoo')",
                    [(s, d, a) for d, a in h['divs']])
        hf = min(start, (row or {}).get('history_from') or start) if h['bars'] else (row or {}).get('history_from')
        db.x('INSERT INTO symbols(symbol, name, type, history_at, history_from, failed) VALUES(?,?,?,?,?,0) '
             'ON CONFLICT(symbol) DO UPDATE SET name=COALESCE(excluded.name, name), type=COALESCE(excluded.type, type), '
             'history_at=excluded.history_at, history_from=excluded.history_from, failed=0',
             (s, h.get('name'), h.get('type'), datetime.now().isoformat(), hf))
        done += 1
    return done


def profiles(db, symbols, max_age_days=7, log=print):
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    n = 0
    for s in symbols:
        if s.startswith('^') or s.endswith('-USD'):
            continue
        row = db.one('SELECT profile_at, profile FROM symbols WHERE symbol=?', (s,))
        # fresh enough, unless it predates the description field (used to spot Bitcoin and crypto treasury companies)
        if row and row['profile_at'] and row['profile_at'] > cutoff and '"summary"' in (row['profile'] or ''):
            continue
        try:
            p = YH.profile(s)
        except Exception as e:
            log(f'profile {s}: {e}')
            p = {'symbol': s, 'error': str(e)[:100]}
        db.x('INSERT INTO symbols(symbol, profile, profile_at) VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET '
             'profile=excluded.profile, profile_at=excluded.profile_at', (s, json.dumps(p), datetime.now().isoformat()))
        n += 1
    return n


def ath(db, symbols, max_age_hours=20, log=print):
    """All-time highs for held symbols, refreshed about once a day (a new high made today comes from the live quote)."""
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    n = 0
    for s in symbols:
        if not s or s.startswith('^') or len(s) > 12:     # indexes and option contracts
            continue
        row = db.one('SELECT at FROM ath WHERE symbol=?', (s,))
        if row and row['at'] and row['at'] > cutoff:
            continue
        try:
            a = YH.ath(s) or {}
        except Exception as e:
            log(f'ath {s}: {e}')
            a = {}
        db.x('INSERT OR REPLACE INTO ath(symbol, high, d, at) VALUES(?,?,?,?)',
             (s, a.get('high'), a.get('d'), datetime.now().isoformat()))
        n += 1
    return n


def dividends(db, symbols, max_age_hours=20, log=print):
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    n = 0
    for s in symbols:
        if s.startswith('^') or s.endswith('-USD'):
            continue
        row = db.one('SELECT divs_at, type FROM symbols WHERE symbol=?', (s,))
        if row and row['divs_at'] and row['divs_at'] > cutoff:
            continue
        rows = market.nasdaq_dividends(s, etf=(row or {}).get('type') == 'ETF')
        if rows:
            db.many("INSERT OR REPLACE INTO divs(symbol, ex, amount, pay, source) VALUES(?,?,?,?,'nasdaq')",
                    [(s, r['ex'], r['amount'], r['pay']) for r in rows if r['type'] in (None, 'Cash')])
        db.x('INSERT INTO symbols(symbol, divs_at) VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET divs_at=excluded.divs_at',
             (s, datetime.now().isoformat()))
        n += 1
        time.sleep(0.3)
    return n


def quotes(db, symbols):
    q = YH.quotes(symbols)
    now = time.time()
    if q:
        db.many('INSERT OR REPLACE INTO quotes(symbol, data, at) VALUES(?,?,?)',
                [(s, json.dumps(v), now) for s, v in q.items()])
    return q


def cached_quotes(db):
    return {r['symbol']: json.loads(r['data']) for r in db.q('SELECT symbol, data FROM quotes')}
