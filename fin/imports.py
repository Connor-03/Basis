"""Uploads: Robinhood activity exports (full transaction history) and positions snapshots from any brokerage
(Fidelity, Schwab and others: a CSV with a symbol column and a quantity column).

Re-uploading overlapping files is safe: a row that is already stored is skipped, identified by its content hash and how
many identical rows precede it in the file. Each upload can be undone, which deletes only the rows it added.
"""
import csv
import hashlib
import io
import json
import os
import re
from datetime import date, datetime

from . import robinhood, snaptrade
from .util import money, num, mdy

UPLOADS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'uploads')


def sniff(data):
    text = data.decode('utf-8-sig', 'replace')
    first = next(csv.reader(io.StringIO(text, newline='')), [])
    if robinhood.detect(first):
        return 'robinhood'
    if _positions_header(text) is not None:
        return 'positions'
    return None


def import_file(db, account_id, filename, data):
    kind = sniff(data)
    if kind is None:
        raise ValueError('This file is not a format the console knows yet: a Robinhood account activity CSV, or a '
                         'positions CSV with Symbol and Quantity columns.')
    sha = hashlib.sha256(data).hexdigest()
    os.makedirs(UPLOADS, mode=0o700, exist_ok=True)
    stored = os.path.join(UPLOADS, f'{date.today().isoformat()}-{sha[:12]}.csv')
    with open(stored, 'wb') as f:
        f.write(data)
    os.chmod(stored, 0o600)
    if kind == 'robinhood':
        return _import_rh(db, account_id, filename, data, sha, stored)
    return _import_positions(db, account_id, filename, data, sha, stored)


def _import_rh(db, account_id, filename, data, sha, stored):
    rows = robinhood.read(data)
    if not rows:
        raise ValueError('No activity rows in this file.')
    dates = [mdy(r['Activity Date']) for r in rows]
    with db.tx() as c:
        cur = c.execute('INSERT INTO imports(account_id, filename, broker, rows_total, rows_added, first_date, last_date, '
                        'sha256, stored) VALUES(?,?,?,?,?,?,?,?,?)',
                        (account_id, filename, 'robinhood', len(rows), 0, min(dates), max(dates), sha, stored))
        imp = cur.lastrowid
        added = 0
        for i, r in enumerate(rows):
            payload = {k: r[k] for k in robinhood.FIELDS}
            cur = c.execute('INSERT OR IGNORE INTO raw_rows(account_id, import_id, pos, fp, occ, activity_date, data) '
                            'VALUES(?,?,?,?,?,?,?)', (account_id, imp, i, r['fp'], r['occ'], dates[i], json.dumps(payload)))
            added += cur.rowcount
        c.execute('UPDATE imports SET rows_added=? WHERE id=?', (added, imp))
    unknown = sorted({r['Trans Code'] for r in rows if robinhood.normalize(r) and
                      robinhood.normalize(r)['kind'] == 'other' and r['Trans Code']})
    db.log('import', {'import': imp, 'file': filename, 'rows': len(rows), 'added': added})
    return {'import_id': imp, 'kind': 'Robinhood activity', 'rows': len(rows), 'added': added,
            'skipped': len(rows) - added, 'first': min(dates), 'last': max(dates), 'unknown_codes': unknown}


# ---- positions snapshots -------------------------------------------------------------------------------------------
_SYM = ('symbol', 'ticker', 'symbol/cusip')
_QTY = ('quantity', 'qty', 'shares', 'qty (quantity)', 'units')
_COST_TOTAL = ('cost basis total', 'cost basis', 'total cost', 'cost', 'cost basis ($)')
_COST_AVG = ('average cost basis', 'average cost', 'avg cost', 'cost/share', 'unit cost')
_VALUE = ('current value', 'market value', 'mkt val (market value)', 'value')


def _norm(h):
    return re.sub(r'\s+', ' ', (h or '').strip().lower())


def _positions_header(text):
    for i, row in enumerate(csv.reader(io.StringIO(text, newline=''))):
        if i > 15:
            break
        names = [_norm(c) for c in row]
        if any(n in _SYM for n in names) and any(n in _QTY for n in names):
            return i, names
    return None


def _col(names, options):
    for o in options:
        if o in names:
            return names.index(o)
    return None


def parse_positions(data):
    text = data.decode('utf-8-sig', 'replace')
    found = _positions_header(text)
    if not found:
        raise ValueError('no Symbol and Quantity columns found')
    hi, names = found
    cs, cq = _col(names, _SYM), _col(names, _QTY)
    ct, ca, cv = _col(names, _COST_TOTAL), _col(names, _COST_AVG), _col(names, _VALUE)
    out, cash = [], 0.0
    for i, row in enumerate(csv.reader(io.StringIO(text, newline=''))):
        if i <= hi or len(row) <= max(cs, cq):
            continue
        sym = (row[cs] or '').strip().upper().replace('**', '')
        label = ' '.join(row).lower()
        if not sym:
            continue
        val = money(row[cv]) if cv is not None and cv < len(row) else None
        if 'cash' in sym.lower() or sym in ('SPAXX', 'FDRXX', 'FZFXX', 'CORE', 'PENDING ACTIVITY') or 'money market' in label:
            if val is not None:
                cash += val
            continue
        if sym.startswith('ACCOUNT TOTAL') or sym in ('TOTAL', 'POSITIONS TOTAL'):
            continue
        q = num(row[cq])
        if not q or not re.match(r'^[A-Z][A-Z0-9.\-/ ]{0,20}$', sym):
            continue
        cost = money(row[ct]) if ct is not None and ct < len(row) else None
        if cost is None and ca is not None and ca < len(row):
            avg = money(row[ca])
            cost = avg * q if avg is not None else None
        out.append({'symbol': sym.replace(' ', '').replace('/', '-'), 'qty': q, 'cost': cost, 'value': val})
    return out, cash


def _import_positions(db, account_id, filename, data, sha, stored):
    rows, cash = parse_positions(data)
    if not rows:
        raise ValueError('No positions found in this file.')
    today = date.today().isoformat()
    payload = json.dumps({'positions': rows, 'cash': cash})
    with db.tx() as c:
        cur = c.execute('INSERT INTO imports(account_id, filename, broker, rows_total, rows_added, first_date, last_date, '
                        'sha256, stored) VALUES(?,?,?,?,?,?,?,?,?)',
                        (account_id, filename, 'positions', len(rows), len(rows), today, today, sha, stored))
        imp = cur.lastrowid
        c.execute('INSERT OR REPLACE INTO raw_rows(account_id, import_id, pos, fp, occ, activity_date, data) '
                  'VALUES(?,?,?,?,?,?,?)', (account_id, imp, 0, 'snapshot:' + today, 1, today, payload))
    db.log('import', {'import': imp, 'file': filename, 'positions': len(rows)})
    return {'import_id': imp, 'kind': 'Positions snapshot', 'rows': len(rows), 'added': len(rows), 'skipped': 0,
            'first': today, 'last': today, 'cash': cash, 'unknown_codes': []}


def undo_import(db, import_id):
    imp = db.one('SELECT * FROM imports WHERE id=?', (import_id,))
    if not imp or imp['undone']:
        raise ValueError('nothing to undo')
    with db.tx() as c:
        n = c.execute('DELETE FROM raw_rows WHERE import_id=?', (import_id,)).rowcount
        c.execute('UPDATE imports SET undone=1 WHERE id=?', (import_id,))
    db.log('undo import', {'import': import_id, 'rows': n})
    return n


def _ts(v):
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def _fund_crypto(db, account_id, evs):
    """Robinhood Crypto buys are paid from the brokerage account's cash and sales go back to it, but neither export
    shows that move. The crypto account gets the money in from the brokerage, and the brokerage account the money out."""
    acct = db.one('SELECT * FROM accounts WHERE id=?', (account_id,))
    if not acct or acct['broker'] != 'robinhood':
        return
    if acct['kind'] == 'crypto':
        pairs = [(e, 1) for e in list(evs) if e['kind'] == 'trade']
        target = evs
    elif acct['kind'] == 'taxable':
        crypto = db.one("SELECT id FROM accounts WHERE broker='robinhood' AND kind='crypto'")
        if not crypto:
            return
        rows = db.q("SELECT data FROM raw_rows WHERE account_id=? AND fp LIKE 'snap:%'", (crypto['id'],))
        pairs = [(e, -1) for e in (snaptrade.normalize(json.loads(r['data'])) for r in rows) if e and e['kind'] == 'trade']
        target = evs
    else:
        return
    for e, sign in pairs:
        amt = -(e['amount'] or 0) * sign             # a buy (negative amount) moves money into the crypto account
        target.append({'date': e['date'], 'settle': e['date'], 'code': 'CRYPTOFUND', 'symbol': '', 'qty': None, 'price': None,
                       'amount': amt, 'kind': 'flow', 'sub': 'crypto_funding', 'pos': -_ts(e.get('ts')) + 0.5, 'import_id': 0,
                       'desc': ('From brokerage cash for ' if sign > 0 else 'To Robinhood Crypto for ') + (e.get('symbol') or 'crypto')})


def _tag_assignments(evs):
    """SnapTrade does not mark the stock leg of an assignment. A buy or sell of 100 shares per contract at the strike on
    the assignment day (or the next) is that leg: tag it so the premium moves into the stock's basis, and run it first."""
    closes = [e for e in evs if e['kind'] == 'option_close' and e['sub'] in ('assigned', 'exercised') and e.get('ts')]
    for c in closes:
        o = c['option']
        d0 = date.fromisoformat(c['date'])
        for e in evs:
            if e['kind'] != 'trade' or e.get('sub') != 'trade' or not e.get('ts') or e['symbol'] != o['underlying'].rstrip('0123456789'):
                continue
            if abs((date.fromisoformat(e['date']) - d0).days) > 3 or e['price'] is None or abs(e['price'] - o['strike']) > 0.005:
                continue
            if abs((e['qty'] or 0) - 100 * (c['qty'] or 1)) > 1e-6:
                continue
            e['sub'] = 'assignment'
            e['date'] = c['date']
            e['pos'] = c['pos'] + 1                         # just before the close row
            break


def normalize_row(fp, data):
    if fp.startswith('snap:'):
        return snaptrade.normalize(data)
    return robinhood.normalize(data)


_NORM = {}       # (row id, fingerprint) -> normalized event: stored rows never change, and the model is rebuilt often


def _normalized(r):
    key = (r['id'], r['fp'])
    if key not in _NORM:
        _NORM[key] = normalize_row(r['fp'], json.loads(r['data']))
    e = _NORM[key]
    if not e:
        return None
    e = dict(e)                                           # callers change events in place
    if e.get('option'):
        e['option'] = dict(e['option'])
    return e


def account_events(db, account_id):
    """All stored rows of an account as engine events, plus snapshot-derived events for positions accounts."""
    evs, snaps = [], []
    # uploaded CSV history wins up to its last date; SnapTrade rows fill in after it
    win = db.one("SELECT MIN(activity_date) AS a, MAX(activity_date) AS b FROM raw_rows WHERE account_id=? AND "
                 "fp NOT LIKE 'snap:%' AND fp NOT LIKE 'snapshot:%'", (account_id,))
    csv_start, csv_end = win['a'], win['b']
    boundary = []
    for r in db.q('SELECT id, import_id, pos, fp, data, activity_date FROM raw_rows WHERE account_id=? '
                  'ORDER BY activity_date, import_id, pos', (account_id,)):
        if r['fp'].startswith('snapshot:'):
            snaps.append((r['activity_date'], json.loads(r['data'])))
            continue
        if r['fp'].startswith('snap:') and csv_end and csv_start <= r['activity_date'] <= csv_end:
            if r['activity_date'] not in (csv_start, csv_end):
                continue                                      # inside the uploaded files' dates: the CSV wins
            boundary.append(r)                                # first/last day: a file can stop partway through it
            continue
        e = _normalized(r)
        if e:
            e['pos'], e['import_id'], e['row_id'] = r['pos'], r['import_id'], r['id']
            if r['fp'].startswith('snap:'):
                e['import_id'] = 0
                e['pos'] = -_ts(e.get('ts'))                  # SnapTrade rows run in time order within a day
            evs.append(e)
    # boundary days: keep a SnapTrade row only when no CSV row on that day is the same trade or payment
    taken = set()
    for r in boundary:
        e = _normalized(r)
        if not e:
            continue
        twin = None
        for i, c in enumerate(evs):
            if i in taken or c['date'] != e['date'] or c.get('row_id') is None:
                continue
            same_kind = c['kind'] == e['kind'] and c.get('side') == e.get('side') and \
                (c.get('symbol') or '') == (e.get('symbol') or '')
            if same_kind and abs((c.get('qty') or 0) - (e.get('qty') or 0)) < 1e-6 and \
                    (abs((c['amount'] or 0) - (e['amount'] or 0)) <= 0.10 or
                     (c.get('price') and e.get('price') and abs(c['price'] - e['price']) <= 0.02)):
                twin = i
                break
        if twin is None:
            e['pos'], e['import_id'], e['row_id'] = -_ts(e.get('ts')), 0, r['id']
            evs.append(e)
        else:
            taken.add(twin)
    _fund_crypto(db, account_id, evs)
    # renamed tickers (CEP became XXI): the old symbol's rows count as the new one, whose price history covers both
    aliases = db.setting('symbol_aliases', {}) or {}
    if aliases:
        for e in evs:
            if e.get('symbol') in aliases:
                e['symbol'] = aliases[e['symbol']]
            if e.get('option') and e['option']['underlying'] in aliases:
                e['option'] = dict(e['option'], underlying=aliases[e['option']['underlying']])
    _tag_assignments(evs)
    # share corrections (matching a brokerage's own count): moved in or out on that day at market value
    for a in db.q('SELECT * FROM adjustments WHERE account_id=?', (account_id,)):
        evs.append({'date': a['d'], 'settle': a['d'], 'code': 'ADJ', 'symbol': a['symbol'], 'qty': a['qty'], 'price': None,
                    'amount': 0.0, 'desc': a['note'] or 'share count correction', 'kind': 'transfer_sec', 'sub': 'adjust',
                    'name': '', 'pos': 0, 'import_id': 10 ** 9})
    basis = {}
    prev, prev_cash = {}, 0.0
    for d, snap in sorted(snaps, key=lambda s: s[0]):
        cur = {p['symbol']: p for p in snap['positions']}
        for sym in set(prev) | set(cur):
            dq = (cur.get(sym, {}).get('qty') or 0) - (prev.get(sym, {}).get('qty') or 0)
            if abs(dq) > 1e-9:
                evs.append({'date': d, 'settle': d, 'code': 'SNAP', 'symbol': sym, 'qty': dq, 'price': None,
                            'amount': 0.0, 'desc': 'positions snapshot', 'kind': 'transfer_sec', 'sub': 'snapshot',
                            'name': '', 'pos': 0, 'import_id': 0})
                c_new, c_old = cur.get(sym, {}).get('cost'), prev.get(sym, {}).get('cost') or 0
                if dq > 0 and c_new is not None:
                    basis[(sym, d)] = c_new - c_old if prev.get(sym) else c_new
        dc = (snap.get('cash') or 0) - prev_cash
        if abs(dc) > 0.005:
            evs.append({'date': d, 'settle': d, 'code': 'SNAP', 'symbol': '', 'qty': None, 'price': None, 'amount': dc,
                        'desc': 'cash in positions snapshot', 'kind': 'flow', 'sub': 'snapshot', 'pos': 0, 'import_id': 0})
        prev, prev_cash = cur, snap.get('cash') or 0
    return evs, basis
