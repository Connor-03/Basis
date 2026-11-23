"""Moves: decisions you log (a sale for margin room, a transfer between accounts, a swap), each with the picture at the
time and how it has turned out since.

A move made today is stamped with the live numbers when it is saved (net worth, margin buffer and loan, doom score,
S&P 500, Bitcoin and the prices involved). One dated in the past is filled from the daily history instead, where it
has the number (the buffer is only known for moves stamped live). Quantities and prices left blank are filled from
your trades in the account on that day or the next three, so a move can be logged before the trade settles.
"""
import json
from bisect import bisect_right
from datetime import date, datetime, timedelta

KINDS = ('note', 'sell', 'buy', 'swap', 'transfer')


def _px(quotes, prices_db, sym):
    q = quotes.get(sym) or {}
    for k in ('postMarketPrice', 'preMarketPrice'):
        if q.get(k) and (q.get(k.replace('Price', 'Time')) or 0) > (q.get('regularMarketTime') or 0):
            return q[k]
    if q.get('regularMarketPrice'):
        return q['regularMarketPrice']
    return prices_db(sym, date.today().isoformat())


def _close_on(db):
    cache = {}

    def f(sym, d):
        if sym not in cache:
            rows = db.q('SELECT d, close FROM prices WHERE symbol=? ORDER BY d', (sym,))
            cache[sym] = ([r['d'] for r in rows], [r['close'] for r in rows])
        ds, cs = cache[sym]
        i = bisect_right(ds, d) - 1
        return cs[i] if i >= 0 else None
    return f


def snapshot(m, doom, quotes, db, symbols=()):
    """The numbers worth comparing later."""
    close = _close_on(db)
    mg = (m.get('margin') or [None])[0] or {}
    now = (doom or {}).get('now') or {}
    cur = ((doom or {}).get('cycle') or {}).get('current') or {}
    spx = ((doom or {}).get('history') or {}).get('spx') or []
    return {'at': datetime.now().isoformat(timespec='seconds'), 'live': True, 'net_worth': m['net_worth'],
            'buffer': mg.get('excess'), 'loan': mg.get('debt'), 'doom': now.get('score'), 'doom_label': now.get('label'),
            'phase': cur.get('phase'), 'cycle_day': cur.get('length'), 'cycle_start': cur.get('start'),
            'spx': _px(quotes, close, '^GSPC') or (spx[-1] if spx else None), 'btc': _px(quotes, close, 'BTC-USD'),
            'prices': {s: _px(quotes, close, s) for s in symbols if s}}


def _from_history(m, doom, db, d, symbols=()):
    """The same picture for a past date, from the stored history (no buffer)."""
    close = _close_on(db)
    h = m.get('history') or {}
    i = bisect_right(h.get('dates') or [], d) - 1
    dh = (doom or {}).get('history') or {}
    j = bisect_right(dh.get('dates') or [], d) - 1
    score = dh['score'][j] if j >= 0 else None
    bands = (doom or {}).get('bands') or []
    label = next((b['name'] for b in sorted(bands, key=lambda b: -b['from']) if score is not None and score >= b['from']), None)
    return {'at': d, 'live': False, 'net_worth': h['value'][i] if i >= 0 else None, 'buffer': None, 'loan': None,
            'doom': score, 'doom_label': label, 'spx': dh['spx'][j] if j >= 0 and dh.get('spx') else None,
            'btc': close('BTC-USD', d), 'prices': {s: close(s, d) for s in symbols if s}}


def _trades(m, d, sym, sides, accounts=None):
    """(qty, average price) of your trades in a symbol from d through three days after."""
    end = (date.fromisoformat(d) + timedelta(days=3)).isoformat()
    q = cost = 0.0
    for a in m['accounts']:
        if accounts and a['id'] not in accounts:
            continue
        for t in a.get('trades') or []:
            if t['symbol'] != sym or not (d <= t['date'] <= end):
                continue
            if t['side'] in sides or (t['side'] in ('in', 'out') and t.get('sub') == 'pending' and
                                      (t['side'] == 'in') == ('buy' in sides)):
                q += t['qty'] or 0
                cost += (t['qty'] or 0) * (t['price'] or 0)
    return (q, cost / q if q else None) if q else (None, None)


def _leg(m, d, leg, sides, accounts):
    """Fill a leg's qty and price from your trades when they were left blank."""
    leg = dict(leg or {})
    if not leg.get('symbol'):
        return leg
    if not leg.get('qty') or not leg.get('price'):
        q, p = _trades(m, d, leg['symbol'], sides, accounts)
        if not leg.get('qty') and q:
            leg['qty'], leg['filled'] = q, True
        if not leg.get('price') and p:
            leg['price'], leg['filled'] = p, True
    return leg


def summary(db, m, doom, quotes):
    close = _close_on(db)
    px = lambda s: _px(quotes, close, s)
    rows = db.q('SELECT * FROM moves ORDER BY d DESC, id DESC')
    now = snapshot(m, doom, quotes, db)
    transit = {t['move_id']: t for t in m.get('transit') or []}
    names = {a['id']: a['name'] for a in m['accounts']}
    out = []
    for r in rows:
        data = json.loads(r['data'] or '{}')
        snap = json.loads(r['snap'] or '{}')
        accounts = data.get('accounts')
        res = {}
        if r['kind'] in ('sell', 'buy', 'swap'):
            sold = _leg(m, r['d'], data.get('sold'), ('sell',), accounts)
            bought = _leg(m, r['d'], data.get('bought'), ('buy',), accounts)
            res['sold'], res['bought'] = sold, bought
            if sold.get('qty') and sold.get('price'):
                res['proceeds'] = sold['qty'] * sold['price']
                p = px(sold['symbol'])
                res['sold_now'] = sold['qty'] * p if p else None     # what the shares or coins sold would be worth now
            if bought.get('qty') and bought.get('price'):
                res['cost'] = bought['qty'] * bought['price']
                p = px(bought['symbol'])
                res['bought_now'] = bought['qty'] * p if p else None
            if r['kind'] == 'sell' and res.get('sold_now') is not None:
                res['delta'] = res['proceeds'] - res['sold_now']     # positive: selling has paid off so far
            if r['kind'] == 'buy' and res.get('bought_now') is not None:
                res['delta'] = res['bought_now'] - res['cost']
            if r['kind'] == 'swap' and res.get('sold_now') is not None and res.get('bought_now') is not None:
                res['cash_kept'] = data['cash'] if data.get('cash') is not None else res['proceeds'] - res['cost']
                res['swap_now'] = res['bought_now'] + res['cash_kept']
                res['delta'] = res['swap_now'] - res['sold_now']      # positive: the swap is ahead of holding on
        elif r['kind'] == 'transfer':
            t = transit.get(r['id']) or {}
            res = {'from': data.get('from'), 'to': data.get('to'), 'expected': data.get('expected'), 'from_name': names.get(data.get('from')),
                   'to_name': names.get(data.get('to')), **{k: t.get(k) for k in
                   ('status', 'value', 'left_value', 'symbols_left', 'symbols_waiting', 'cash_waiting', 'arrived_on')}}
            fa = next((a for a in m['accounts'] if a['id'] == data.get('from')), None)
            res['from_closed'] = bool(fa and fa.get('closed'))
            res['from_value'] = fa['equity'] if fa else None
        syms = [x for x in ((data.get('sold') or {}).get('symbol'), (data.get('bought') or {}).get('symbol')) if x]
        then = snap or _from_history(m, doom, db, r['d'], syms)
        out.append({'id': r['id'], 'd': r['d'], 'title': r['title'], 'note': r['note'], 'kind': r['kind'],
                    'data': data, 'then': then, 'result': res,
                    'prices_now': {s: px(s) for s in syms}})
    return {'moves': out, 'now': now, 'accounts': [{'id': a['id'], 'name': a['name'], 'closed': bool(a.get('closed'))}
                                                    for a in m['accounts']]}


def save(db, m, doom, quotes, b):
    """Add or edit a move. b: {id?, d, title, note, kind, data, remove?}."""
    if b.get('remove') and b.get('id'):
        db.x('DELETE FROM moves WHERE id=?', (int(b['id']),))
        return {'ok': True}
    title = (b.get('title') or '').strip()[:140]
    if not title:
        raise ValueError('Give the move a short title.')
    d = (b.get('d') or date.today().isoformat())[:10]
    date.fromisoformat(d)
    kind = b.get('kind') if b.get('kind') in KINDS else 'note'
    data = b.get('data') if isinstance(b.get('data'), dict) else {}
    clean = {}
    for leg in ('sold', 'bought'):
        x = data.get(leg) or {}
        sym = (x.get('symbol') or '').strip().upper()[:12]
        if sym:
            clean[leg] = {'symbol': sym, 'qty': _num(x.get('qty')), 'price': _num(x.get('price'))}
    for k in ('from', 'to'):
        if data.get(k):
            clean[k] = int(data[k])
    if data.get('expected'):
        clean['expected'] = date.fromisoformat(str(data['expected'])[:10]).isoformat()
    if data.get('cash') not in (None, ''):
        clean['cash'] = _num(data['cash'])
    if data.get('accounts'):
        clean['accounts'] = [int(x) for x in data['accounts']][:10]
    if kind == 'transfer' and not (clean.get('from') and clean.get('to') and clean['from'] != clean['to']):
        raise ValueError('A transfer needs two different accounts.')
    note = (b.get('note') or '').strip()[:2000]
    syms = [clean[k]['symbol'] for k in ('sold', 'bought') if k in clean]
    if b.get('id'):
        db.x('UPDATE moves SET d=?, title=?, note=?, kind=?, data=? WHERE id=?',
             (d, title, note, kind, json.dumps(clean), int(b['id'])))
        return {'id': int(b['id'])}
    snap = snapshot(m, doom, quotes, db, syms) if d == date.today().isoformat() else {}
    i = db.x('INSERT INTO moves(d, title, note, kind, data, snap) VALUES(?,?,?,?,?,?)',
             (d, title, note, kind, json.dumps(clean), json.dumps(snap)))
    db.log('move logged', {'id': i, 'title': title})
    return {'id': i}


def _num(v):
    if v in (None, ''):
        return None
    v = float(str(v).replace(',', '').replace('$', ''))
    if v != v or abs(v) > 1e12:
        raise ValueError('number')
    return v
