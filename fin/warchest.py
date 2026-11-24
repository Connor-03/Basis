"""War chest and liquidation line, for the Doom dashboard.

War chest: what you could put to work in the next crash. Cash in every account, plus what the margin account could
borrow while still being able to take a further fall after buying (the safety margin you pick). Margin room shrinks as
prices fall, so the same numbers are worked out for S&P 500 falls of 5% to 50% from here.

Liquidation line: how far the S&P 500 would have to fall from here before the margin account's buffer is gone (a
margin call; the broker can sell without asking) and before the loan is bigger than the holdings. Each holding moves
with the S&P 500 by its beta, measured on the last year of daily closes (no shrinking toward 1: for a margin estimate
it is safer to take the past at its word), capped at 4, and no holding falls more than 95%.
Maintenance rates are the ones the Margin page uses (yours where you entered them).

Past crashes are measured on the S&P 500 and Nasdaq-100 from their high to their lowest close, from Yahoo daily closes
(back to 1985 for the S&P 500 and the Nasdaq-100). The Doom score at a given fall comes from every trading day since
2010 that was that far under the S&P 500's 52-week high.

Everything here is an estimate from today's holdings. It is not the broker's own calculation.
"""
import bisect
import json
from datetime import date, timedelta

CRASHES = [  # key, name, search window (the lowest close inside it, measured from the highest close before that)
    ('1987', 'Black Monday', '1987-08-01', '1987-12-31'),
    ('dotcom', 'Dot-com bust', '2000-01-01', '2002-12-31'),
    ('gfc', '2008 financial crisis', '2007-07-01', '2009-03-31'),
    ('2011', 'US credit downgrade', '2011-04-01', '2011-10-31'),
    ('2015', 'China devaluation, oil crash', '2015-05-01', '2016-02-29'),
    ('2018a', 'Volmageddon', '2018-01-15', '2018-04-30'),
    ('2018', 'Fed hikes and trade war', '2018-09-01', '2018-12-31'),
    ('covid', 'COVID crash', '2020-02-01', '2020-04-30'),
    ('2022', '2022 inflation bear market', '2022-01-01', '2022-10-31'),
    ('2023', 'Bond yields hit 5%', '2023-07-15', '2023-10-31'),
    ('yen', 'Yen carry trade unwind', '2024-07-01', '2024-08-31'),
    ('tariff', 'Tariff crash', '2025-02-01', '2025-04-30'),
    ('iran', 'Iran war oil shock', '2025-10-01', '2026-04-15'),
]
FALLS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]      # S&P 500 falls from here for the scenario table
SAFETY = [0.05, 0.10, 0.15, 0.20, 0.30]                            # further falls the war chest must survive
TARGETS = [0.10, 0.20, 0.30, 0.40]                                 # "to take a fall this big" rows
MAX_DROP, DEFAULT_BETA = 0.95, 2.0


# ---- data ---------------------------------------------------------------------------------------------------------
def refresh_old(db, yahoo, log=print):
    """The S&P 500 and Nasdaq-100 before 2009 (the Doom score keeps 2009 on), fetched once."""
    for key, sym in (('spx_old', '^GSPC'), ('ndx_old', '^NDX')):
        if db.one('SELECT d FROM macro_daily WHERE series=? LIMIT 1', (key,)):
            continue
        try:
            bars = yahoo.history(sym, '1985-01-01')['bars']
            db.many('INSERT OR REPLACE INTO macro_daily(series, d, value) VALUES(?,?,?)',
                    [(key, d, c) for d, c, _ in bars if d < '2009-01-02' and c])
        except Exception as e:
            log(f'warchest {sym}: {e}')


def _long(db, key):
    rows = db.q('SELECT d, value FROM macro_daily WHERE series IN (?, ?) ORDER BY d', (key + '_old', key))
    out = {}
    for r in rows:
        out[r['d']] = r['value']
    ds = sorted(out)
    return ds, [out[d] for d in ds]


def betas(db, symbols, since):
    """Beta to the S&P 500 for each symbol from daily closes since `since`: {sym: (beta, days, raw beta)}."""
    spx = {r['d']: r['value'] for r in db.q("SELECT d, value FROM macro_daily WHERE series='spx' AND d>=? ORDER BY d", (since,))}
    days = sorted(spx)
    out = {}
    for sym in symbols:
        px = {r['d']: r['p'] for r in db.q('SELECT d, COALESCE(adj, close) AS p FROM prices WHERE symbol=? AND d>=?', (sym, since)) if r['p']}
        ks = [d for d in days if d in px]
        xs, ys = [], []
        for a, b in zip(ks, ks[1:]):
            xs.append(spx[b] / spx[a] - 1)
            ys.append(px[b] / px[a] - 1)
        if len(xs) < 120:
            out[sym] = (None, len(xs), None)
            continue
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        var = sum((x - mx) ** 2 for x in xs)
        raw = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var if var else None
        out[sym] = (max(0.0, min(4.0, raw)) if raw is not None else None, len(xs), raw)
    return out


# ---- Doom score at a given fall -----------------------------------------------------------------------------------
def doom_at(c, under_high):
    """What the Doom score read on past days when the S&P 500 was `under_high` (0.15 = 15%) under its 52-week high."""
    pts = [(-c['raw']['spx_dd'][i], c['total'][i]) for i in range(c['first'], len(c['days']))
           if c['raw']['spx_dd'][i] is not None and c['total'][i] is not None]
    if not pts:
        return None
    deepest = max(p[0] for p in pts)
    if under_high > deepest + 0.01:
        top = max(pts, key=lambda p: p[1])
        return {'beyond': True, 'deepest': deepest, 'max': top[1]}
    for width in (0.025, 0.05, 0.08):
        xs = sorted(t for d, t in pts if abs(d - under_high) <= width)
        if len(xs) >= 8:
            return {'mid': xs[len(xs) // 2], 'lo': xs[len(xs) // 4], 'hi': xs[(3 * len(xs)) // 4], 'n': len(xs)}
    return None


# ---- past crashes -------------------------------------------------------------------------------------------------
def crashes(db, c, personal=None):
    sd, sv = _long(db, 'spx')
    nd, nv = _long(db, 'ndx')
    n_at = {d: v for d, v in zip(nd, nv)}
    doom_by = dict(zip(c['days'], c['total']))
    pidx = dict(zip(personal['dates'], personal['index'])) if personal and personal.get('dates') else {}
    pdates = sorted(pidx)
    out = []
    for key, name, lo, hi in CRASHES:
        a, b = bisect.bisect_left(sd, lo), bisect.bisect_right(sd, hi)
        if b - a < 5:
            continue
        low = min(range(a, b), key=lambda j: sv[j])
        top = max(range(a, low + 1), key=lambda j: sv[j])
        row = {'key': key, 'name': name, 'top': sd[top], 'low': sd[low], 'spx': sv[low] / sv[top] - 1, 'days': low - top}
        ns = [(d, n_at[d]) for d in sd[top:low + 6] if n_at.get(d)]
        if ns:
            nt = max(ns[:max(1, low - top + 1)], key=lambda x: x[1])
            nl = min((x for x in ns if x[0] >= nt[0]), key=lambda x: x[1])
            row['ndx'] = nl[1] / nt[1] - 1
        if sd[top] >= c['days'][c['first']]:
            win = [(doom_by.get(d), d) for d in sd[top:low + 6] if doom_by.get(d) is not None]
            if win:
                row['doom'], row['doom_date'] = max(win)
        if pdates and pdates[0] <= sd[top]:
            span = [pidx[d] for d in pdates[bisect.bisect_left(pdates, sd[top]):bisect.bisect_right(pdates, sd[min(low + 5, len(sd) - 1)])] if pidx[d]]
            if span:
                k = max(range(len(span)), key=lambda q: span[q])
                row['you'] = min(span[k:]) / span[k] - 1
        out.append(row)
    return sorted(out, key=lambda r: r['spx'], reverse=True)


def this_selloff(c):
    """The S&P 500 against its 52-week high now, and the lowest close since that high."""
    days, spx = c['days'], c['spx']
    i = len(days) - 1
    top = max(range(max(0, i - 251), i + 1), key=lambda j: spx[j])
    low = min(range(top, i + 1), key=lambda j: spx[j])
    return {'top': days[top], 'low': days[low], 'low_fall': spx[low] / spx[top] - 1, 'now': spx[i] / spx[top] - 1, 'date': days[i]}


# ---- one margin account under a fall ------------------------------------------------------------------------------
class Book:
    """A margin account's holdings with their maintenance rates and betas. s is an S&P 500 fall from here (0.2 = 20%)."""

    def __init__(self, mg, beta_of):
        self.name, self.debt, self.excess0, self.equity0 = mg['account'], mg['debt'], mg['excess'], mg['equity']
        self.rows = []
        for r in mg['rows']:
            if r['pct'] is None or r['why'] in ('long option', 'short put held as cash') or not r['value'] or r['value'] <= 0:
                continue
            b, how = beta_of(r['symbol'])
            self.rows.append({'symbol': r['symbol'], 'v': r['value'], 'm': r['pct'], 'b': b, 'how': how})
        self.value = sum(r['v'] for r in self.rows)
        self.beta = sum(r['v'] * r['b'] for r in self.rows) / self.value if self.value else 1.0
        self.maint = sum(r['v'] * r['m'] for r in self.rows) / self.value if self.value else 0.25

    @staticmethod
    def drop(b, s):
        return min(MAX_DROP, max(0.0, b * s))

    def lost(self, s):
        return sum(r['v'] * self.drop(r['b'], s) for r in self.rows)

    def excess(self, s):
        return self.excess0 - sum(r['v'] * self.drop(r['b'], s) * (1 - r['m']) for r in self.rows)

    def equity(self, s):
        return self.equity0 - self.lost(s)

    def solve(self, f):
        """The smallest S&P 500 fall where f(s) reaches zero, or None if it never does."""
        if f(0.0) <= 0:
            return 0.0
        if f(1.0) > 0:
            return None
        lo, hi = 0.0, 1.0
        for _ in range(50):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
        return hi

    def safe_buy(self, s, f):
        """Most you could buy on margin after a fall of s and still have a buffer left after a further fall of f.
        New buys are taken to be like what the account holds now (its average beta and maintenance)."""
        num = self.excess(s) - sum(r['v'] * (1 - self.drop(r['b'], s)) * self.drop(r['b'], f) * (1 - r['m']) for r in self.rows)
        dn = self.drop(self.beta, f)
        return max(0.0, num / (self.maint + dn * (1 - self.maint))) if num > 0 else 0.0

    def to_take(self, t):
        """To keep a buffer through an S&P 500 fall of t from here: the deposit needed, or the stock to sell (spread over
        everything held, the proceeds paying down the loan)."""
        bl = sum(r['v'] * self.drop(r['b'], t) * (1 - r['m']) for r in self.rows)
        need = bl - self.excess0
        if need <= 0:
            return {'fall': t, 'deposit': 0.0, 'sell': 0.0, 'ok': True}
        k = need / (sum(r['v'] * r['m'] for r in self.rows) + bl)
        return {'fall': t, 'deposit': need, 'sell': k * self.value if k < 1 else None, 'ok': False}

    def weights(self, s, k=6):
        """The holdings that take the most of the buffer in a fall of s."""
        rows = sorted(self.rows, key=lambda r: -r['v'] * self.drop(r['b'], s) * (1 - r['m']))
        total = sum(r['v'] * self.drop(r['b'], s) * (1 - r['m']) for r in self.rows) or 1
        return [{'symbol': r['symbol'], 'value': r['v'], 'beta': r['b'], 'how': r['how'], 'maint': r['m'],
                 'fall': self.drop(r['b'], s), 'share': r['v'] * self.drop(r['b'], s) * (1 - r['m']) / total} for r in rows[:k]]


def _buying_power(db, account_id):
    r = db.one('SELECT balances FROM snap_snapshots WHERE account_id=? ORDER BY id DESC LIMIT 1', (account_id,))
    try:
        bal = json.loads((r or {}).get('balances') or '[]')
        bp = sum(float(x.get('buying_power') or 0) for x in bal if (x.get('currency') or {}).get('code', 'USD') == 'USD')
        return bp if bal else None
    except (ValueError, TypeError):
        return None


def summary(db, m, c, personal=None):
    if not c:
        return None
    now = this_selloff(c)
    under = -now['now']                                   # how far under its 52-week high the S&P 500 is now

    def total_under(s):                                   # a further fall of s, measured from the 52-week high
        return 1 - (1 - under) * (1 - s)

    since = (date.fromisoformat(c['days'][-1]) - timedelta(days=366)).isoformat()
    syms = {p['symbol'] for p in m['positions']} | {r['symbol'] for mg in m.get('margin') or [] for r in mg['rows']}
    bt = betas(db, sorted(syms), since)
    cls = {p['symbol']: p.get('class') for p in m['positions']}

    def beta_of(sym):
        b = bt.get(sym, (None, 0, None))[0]
        if b is not None:
            return b, 'last year'
        return DEFAULT_BETA, 'assumed, short history'

    ids = {a['name']: a['id'] for a in m['accounts']}
    books = [Book(mg, beta_of) for mg in m.get('margin') or [] if mg.get('rows')]
    cash = [{'account': a['name'], 'kind': a['kind'], 'cash': a['cash']} for a in m['accounts']
            if a['cash'] > 1 and a['kind'] != 'crypto' and not a.get('value_note') and not a.get('closed')]
    cash_total = sum(x['cash'] for x in cash)
    held = [(p['symbol'], p['value'] or 0, beta_of(p['symbol'])[0]) for p in m['positions'] if (p['value'] or 0) > 0]

    def scenario(s):
        row = {'fall': s, 'under_high': total_under(s), 'doom': doom_at(c, total_under(s)),
               'net_worth': m['net_worth'] - sum(v * Book.drop(b, s) for _, v, b in held), 'cash': cash_total, 'accounts': []}
        safe = {str(f): cash_total for f in SAFETY}
        for bk in books:
            ex, eq = bk.excess(s), bk.equity(s)
            st = 'ok' if ex > 0 else 'call' if eq > 0 else 'wiped'
            sb = {str(f): bk.safe_buy(s, f) for f in SAFETY}
            for f in SAFETY:
                safe[str(f)] += sb[str(f)]
            row['accounts'].append({'account': bk.name, 'holdings': bk.value - bk.lost(s), 'equity': eq, 'buffer': ex,
                                    'room': max(ex, 0) / bk.maint if bk.maint else None, 'status': st, 'safe': sb})
        row['chest'] = safe
        row['status'] = 'wiped' if any(a['status'] == 'wiped' for a in row['accounts']) else \
            'call' if any(a['status'] == 'call' for a in row['accounts']) else 'ok'
        return row

    scen = [scenario(s) for s in FALLS]
    if c['total'][-1] is not None:                         # today's row: the score itself, not the past days like it
        scen[0]['doom'] = {'mid': c['total'][-1], 'lo': c['total'][-1], 'hi': c['total'][-1], 'n': 1, 'now': True}
    lines = []
    for bk in books:
        call, wipe = bk.solve(bk.excess), bk.solve(bk.equity)
        lines.append({'account': bk.name, 'debt': bk.debt, 'holdings': bk.value, 'equity': bk.equity0, 'buffer': bk.excess0,
                      'beta': bk.beta, 'maint': bk.maint, 'buying_power': _buying_power(db, ids.get(bk.name)),
                      'call': call, 'call_under_high': total_under(call) if call is not None else None,
                      'call_doom': doom_at(c, total_under(call)) if call is not None else None,
                      'call_portfolio': bk.lost(call) / bk.value if call is not None and bk.value else None,
                      'wipe': wipe, 'wipe_under_high': total_under(wipe) if wipe is not None else None,
                      'wipe_doom': doom_at(c, total_under(wipe)) if wipe is not None else None,
                      'to_take': [bk.to_take(t) for t in TARGETS],
                      'weights': bk.weights(call if call else 0.10)})
    past = crashes(db, c, personal)
    worst = min(lines, key=lambda x: x['call'] if x['call'] is not None else 9) if lines else None
    for r in past:             # how much further this selloff would have to fall to be as deep as each past crash
        depth = -r['spx']
        fall = 1 - (1 - depth) / (1 - under) if depth > under else None
        sc = scenario(fall) if fall is not None else None
        r.update({'further': fall, 'status': sc['status'] if sc else 'past', 'net_worth': sc['net_worth'] if sc else None})
    transit = [{'value': t['value'], 'to': t.get('to_name'), 'from': t.get('from_name')} for t in m.get('transit') or []
               if t.get('status') == 'in transit' and (t.get('value') or 0) > 1]
    return {'now': now, 'cash': cash, 'cash_total': cash_total, 'lines': lines, 'line': worst,
            'scenarios': scen, 'safety': SAFETY, 'crashes': past, 'transit': transit,
            'betas': {s: {'beta': b[0], 'days': b[1], 'raw': b[2]} for s, b in bt.items()},
            'held_beta': sum(v * b for _, v, b in held) / sum(v for _, v, _ in held) if held else None}
