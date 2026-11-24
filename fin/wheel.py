"""Wheel trades: what a short put that hands you shares really made, counted to the end.

A broker books an assigned put's premium as profit, then shows the shares as a separate holding. Here the two are one
trade: it starts when a short put is assigned (or when a block of exactly 100, 200, ... shares is bought and calls are
sold on it within a week), takes in the calls and puts sold on that stock while it is open, the dividends on its
shares and what the shares did, and ends when its shares are sold or called away. Open trades count their shares at
today's price and their open contracts at today's quotes.

The broker does not always mark the stock side of an assignment (older Robinhood files, some SnapTrade rows), so a buy
or sell of 100 shares per contract at the strike within three days of an assigned contract is taken as that side.

Shares are kept apart from the rest of the stock you hold: only assigned shares and 100-share blocks go into a trade.
Sales of whole 100-share blocks and called-away shares come out of the trade first; odd-lot sales come out of your
other shares first. Calls sold on more shares than the trade holds count in proportion.
"""
from collections import defaultdict
from datetime import date, timedelta

EPS = 1e-6
DIV_SUBS = ('dividend', 'dividend_in_lieu', 'return_of_capital', 'cash_in_lieu')


def _und(s):
    return (s or '').rstrip('0123456789')        # adjusted contracts after a split (BYND1) belong to BYND


def _block(q):
    return q >= 100 - EPS and abs(q / 100 - round(q / 100)) < 1e-6


def sells_options(db, accounts, today):
    """The switch in Settings: (on, set by the owner). It starts off; accounts and today are kept for callers."""
    v = db.setting('sells_options')
    return bool(v), v is not None


def _stock_legs(trades, opt_rows):
    """Mark which stock trades are the share side of an assignment, and which contract each one belongs to."""
    assigned = [r for r in opt_rows if r.get('short') and ('assign' in r['how'] or r['how'] == 'exercised')]
    used, legs = set(), {}
    for i, t in enumerate(trades):
        if t['side'] not in ('buy', 'sell'):
            continue
        want = 'P' if t['side'] == 'buy' else 'C'
        best = None
        for j, r in enumerate(assigned):
            if j in used or r.get('right') != want or not t.get('price'):
                continue
            strike = _strike(r['key'])
            if strike is None or abs(strike - t['price']) > 0.005:
                continue
            gap = abs((date.fromisoformat(t['date']) - date.fromisoformat(r['date'])).days)
            if gap > 3 or abs((t['qty'] or 0) - 100 * r['qty']) > 1e-6:
                continue
            if best is None or gap < best[1]:
                best = (j, gap)
        if best is not None:
            used.add(best[0])
            legs[i] = assigned[best[0]]
        elif t.get('sub') == 'assignment':
            legs[i] = None                        # marked by the broker, contract not found in the rows
    return legs


def _strike(key):
    try:
        return float(key.rsplit(' ', 1)[1])
    except (ValueError, IndexError):
        return None


def build(db, model, today=None, prices=None):
    from .analytics import Prices                  # analytics imports this module's sells_options
    today = today or model.get('today') or date.today().isoformat()
    accounts = model['accounts']
    live = {p['symbol']: p['price'] for p in model['positions'] if p.get('price')}
    syms = {_und(r['underlying']) for a in accounts for r in a.get('opt_realized') or () if r.get('short')}
    prices = prices or Prices(db, syms)
    trades_all = []
    for a in accounts:
        rows = [r for r in a.get('opt_realized') or () if r.get('short')]
        if not rows and not any(o['qty'] < 0 for o in a.get('options') or ()):
            continue
        by_sym = defaultdict(list)
        for r in rows:
            by_sym[_und(r['underlying'])].append(r)
        open_by = defaultdict(list)
        for o in a.get('options') or ():
            if o['qty'] < 0 and o.get('status', 'open') == 'open':
                open_by[_und(o['underlying'])].append(o)
        tr_by = defaultdict(list)
        for t in a.get('trades') or ():
            tr_by[t['symbol']].append(t)
        div_by = defaultdict(list)
        for r in a.get('income_rows') or ():
            if r.get('symbol') and r['sub'] in DIV_SUBS:
                div_by[r['symbol']].append(r)
        for sym in sorted(set(by_sym) | set(open_by)):
            trades_all += _symbol(a, sym, tr_by.get(sym, []), by_sym.get(sym, []), open_by.get(sym, []),
                                  div_by.get(sym, []), prices, live.get(sym), today)
    opens = sorted((w for w in trades_all if w['end'] is None), key=lambda w: w['start'], reverse=True)
    closed = sorted((w for w in trades_all if w['end'] is not None), key=lambda w: w['end'], reverse=True)

    def tot(ws, k):
        return round(sum(w[k] for w in ws), 2)
    return {'trades': opens + closed, 'as_of': today,
            'closed': {'n': len(closed), 'won': sum(1 for w in closed if w['net'] > 0), 'net': tot(closed, 'net'),
                       'options': tot(closed, 'options'), 'shares': tot(closed, 'shares'), 'dividends': tot(closed, 'dividends')},
            'open': {'n': len(opens), 'net': tot(opens, 'net'), 'options': tot(opens, 'options'), 'shares': tot(opens, 'shares'),
                     'dividends': tot(opens, 'dividends'), 'value': round(sum(w['value'] for w in opens), 2)},
            'under': tot(trades_all, 'under')}


def _symbol(acct, sym, trades, opt_rows, open_opts, divs, prices, live_px, today):
    splits = [(d, r) for d, r in prices.split_list.get(sym, ()) if r and r != 1]
    legs_of = _stock_legs(trades, opt_rows)
    # one timeline: stock trades and splits, in date order (a day's trades keep their order)
    tl = [(t['date'], 0, i, 'trade', t) for i, t in enumerate(trades)]
    tl += [(d, -1, 0, 'split', r) for d, r in splits]
    tl.sort(key=lambda x: (x[0], x[1], x[2]))
    calls_opened = sorted(r['opened'] for r in opt_rows if r.get('right') == 'C' and r.get('opened'))
    calls_opened += sorted(o['opened'] for o in open_opts if o['right'] == 'C' and o.get('opened'))

    out, cur = [], None
    other = 0.0                               # shares of this stock held outside any wheel trade
    pool_at = []                              # (date, shares in the trade at the end of that day), for calls and dividends

    def start(d):
        return {'symbol': sym, 'account': acct['name'], 'account_id': acct['id'], 'start': d, 'end': None,
                'lots': [], 'legs': [], 'bound': set(), 'shares_in': 0.0, 'cost_in': 0.0, 'realized': 0.0,
                'under': 0.0, 'left': 0.0, 'max_qty': 0.0}

    def pool(w):
        return sum(l['qty'] for l in w['lots'])

    def take(w, q, proceeds, d, how, px=None, close=None):
        lots, left, cost = w['lots'], q, 0.0
        while left > EPS and lots:
            l = lots[0]
            n = min(left, l['qty'])
            part = l['cost'] * n / l['qty']
            l['qty'] -= n
            l['cost'] -= part
            cost += part
            left -= n
            if l['qty'] <= EPS:
                lots.pop(0)
        gain = proceeds - cost
        w['realized'] += gain
        leg = {'d': d, 'kind': how, 'qty': q, 'price': px, 'amount': round(proceeds, 2), 'gain': round(gain, 2)}
        if close is not None and px is not None and how == 'called':
            leg['close'] = round(close, 4)
            w['left'] += max(close - px, 0) * q
            leg['left'] = round(max(close - px, 0) * q, 2)
        w['legs'].append(leg)

    for d, _, i, kind, x in tl:
        if kind == 'split':
            other *= x
            if cur:
                for l in cur['lots']:
                    l['qty'] *= x
                cur['legs'].append({'d': d, 'kind': 'split', 'ratio': x})
            continue
        t, q = x, x['qty'] or 0
        if q <= EPS:
            continue
        opt = legs_of.get(i, False)          # False: an ordinary trade; None: broker-marked, contract unknown
        px = t.get('price') or (-(t['amount'] or 0) / q if q else None)
        if t['side'] in ('buy', 'in'):
            put_in = opt is not False and t['side'] == 'buy' and (opt is None or opt.get('right') == 'P')
            block = t['side'] == 'buy' and _block(q)
            starts_calls = block and any(t['date'] <= c <= (date.fromisoformat(t['date']) + timedelta(days=7)).isoformat()
                                         for c in calls_opened)
            if put_in or (block and (cur or starts_calls)):
                if cur is None:
                    cur = start(d)
                cost = -(t['amount'] or 0) if t['side'] == 'buy' and t['amount'] else (px or 0) * q
                cur['lots'].append({'qty': q, 'cost': cost})
                cur['shares_in'] += q
                cur['cost_in'] += cost
                leg = {'d': d, 'kind': 'assigned' if put_in else 'bought', 'qty': q, 'price': px, 'amount': round(-cost, 2)}
                if put_in:
                    close = prices.px(sym, d)
                    if close is not None and px is not None:
                        leg['close'] = round(close, 4)
                        leg['under'] = round((px - close) * q, 2)
                        cur['under'] += (px - close) * q
                    if opt:
                        leg['premium'] = round(opt['pnl'], 2)
                        leg['contract'] = opt['key']
                        cur['bound'].add(id(opt))
                cur['legs'].append(leg)
                cur['max_qty'] = max(cur['max_qty'], pool(cur))
            else:
                other += q
        else:                                  # sell / out
            called = opt is not False and t['side'] == 'sell' and (opt is None or opt.get('right') == 'C')
            proceeds = t['amount'] or 0
            if t['side'] == 'out':
                proceeds = (prices.px(sym, d) or px or 0) * q
            have = pool(cur) if cur else 0.0
            if have > EPS and (called or _block(q)):
                n = min(q, have)
            elif have > EPS:
                n = max(0.0, min(q - other, have))   # odd lots: your other shares go first
            else:
                n = 0.0
            if n > EPS:
                close = prices.px(sym, d) if called else None
                take(cur, n, proceeds * n / q, d, 'called' if called else ('moved' if t['side'] == 'out' else 'sold'), px, close)
                if called and opt:
                    cur['legs'][-1]['premium'] = round(opt['pnl'], 2)
                    cur['legs'][-1]['contract'] = opt['key']
                    cur['bound'].add(id(opt))
                if pool(cur) <= EPS:
                    cur['end'] = d
                    out.append(cur)
                    cur = None
            other = max(0.0, other - (q - n))
        pool_at.append((d, pool(cur) if cur else 0.0, other))
    if cur:
        out.append(cur)

    def pool_on(d):
        """(shares in a trade, other shares) at the end of day d."""
        p = (0.0, 0.0)
        for pd, pq, po in pool_at:
            if pd > d:
                break
            p = (pq, po)
        return p

    # contracts: each closed one goes to the trade that was open when it was sold (or, failing that, when it closed)
    for w in out:
        w['opt_real'] = 0.0
        w['opt_open'] = 0.0
        w['divs'] = 0.0
    bound = {b for w in out for b in w['bound']}

    def home(d_open):
        """The trade that was open when a contract was sold (a put sold on the day shares were called away starts
        the next round, not this one)."""
        for w in out:
            if w['start'] <= d_open and (w['end'] is None or d_open < w['end']):
                return w
        return None

    for r in opt_rows:
        w = None
        if id(r) in bound:
            w = next(x for x in out if id(r) in x['bound'])
            w['opt_real'] += r['pnl']
            continue
        w = home(r.get('opened') or r['date'])
        if not w:
            continue
        frac = 1.0
        if r.get('right') == 'C':
            have = pool_on(r.get('opened') or r['date'])[0]
            frac = min(1.0, have / (100 * r['qty'])) if r['qty'] else 0.0
            if frac <= EPS:
                continue
        w['opt_real'] += r['pnl'] * frac
        w['legs'].append({'d': r['date'], 'kind': 'call' if r.get('right') == 'C' else 'put', 'contract': r['key'],
                          'qty': r['qty'], 'how': r['how'], 'opened': r.get('opened'), 'amount': round(r['pnl'] * frac, 2),
                          'part': round(frac, 3) if frac < 1 - 1e-6 else None})
    for o in open_opts:
        w = next((x for x in out if x['end'] is None), None)
        if not w:
            continue                               # a plain covered call or cash-secured put: no trade open
        n = abs(o['qty'])
        frac = min(1.0, (pool(w) if o['right'] == 'C' else 100 * n) / (100 * n)) if n else 0.0
        if frac <= EPS:
            continue
        pnl = (o.get('pnl') if o.get('pnl') is not None else -(o['cost'] or 0)) * frac
        w['opt_open'] += pnl
        w['legs'].append({'d': today, 'kind': 'open', 'contract': o['key'], 'qty': n, 'opened': o.get('opened'),
                          'premium': round(-(o['cost'] or 0) * frac, 2), 'amount': round(pnl, 2),
                          'quoted': bool(o.get('quoted')), 'part': round(frac, 3) if frac < 1 - 1e-6 else None})
    for r in divs:
        for w in out:
            if w['start'] <= r['date'] <= (w['end'] or today):
                inside, outside = pool_on(r['date'])
                frac = inside / (inside + outside) if inside + outside > EPS else 0.0
                if frac > EPS:
                    w['divs'] += r['amount'] * frac
                    w['legs'].append({'d': r['date'], 'kind': 'dividend', 'amount': round(r['amount'] * frac, 2)})
                break

    res = []
    for w in out:
        qty = pool(w)
        cost_left = sum(l['cost'] for l in w['lots'])
        px_now = live_px or prices.px(sym, today)
        value = qty * px_now if qty > EPS and px_now else 0.0
        unreal = value - cost_left if qty > EPS else 0.0
        shares = w['realized'] + unreal
        options = w['opt_real'] + w['opt_open']
        net = options + shares + w['divs']
        legs = sorted(w['legs'], key=lambda l: (l['d'], {'assigned': 0, 'bought': 0, 'put': 1, 'call': 2, 'called': 3,
                                                         'sold': 3, 'moved': 3, 'dividend': 4, 'split': 5, 'open': 6}[l['kind']]))
        end = w['end']
        days = (date.fromisoformat(end or today) - date.fromisoformat(w['start'])).days
        res.append({'symbol': sym, 'account': w['account'], 'account_id': w['account_id'], 'start': w['start'], 'end': end,
                    'days': days, 'shares_in': w['shares_in'], 'avg_in': w['cost_in'] / w['shares_in'] if w['shares_in'] else None,
                    'max_qty': w['max_qty'], 'qty': qty, 'price': px_now if qty > EPS else None, 'value': round(value, 2),
                    'options': round(options, 2), 'options_open': round(w['opt_open'], 2),
                    'shares': round(shares, 2), 'shares_open': round(unreal, 2), 'dividends': round(w['divs'], 2),
                    'net': round(net, 2), 'under': round(w['under'], 2), 'left': round(w['left'], 2),
                    'puts_assigned': sum(1 for l in legs if l['kind'] == 'assigned'),
                    'calls': sum(1 for l in legs if l['kind'] in ('call', 'open')),
                    'start_kind': legs[0]['kind'] if legs else None, 'legs': legs})
    return res
