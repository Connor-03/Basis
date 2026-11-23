"""Doom dashboard: where to buy the dip. Every stock you hold or watch gets a dip score from 0 to 100. Each reading is
ranked against the same reading for the other stocks here (in a crash nearly everything looks cheap against its own
past, so the useful question is which looks best right now): 100 is the best of the group, 0 the worst. Readings are
averaged inside five parts, and the parts are weighted:

- Value (30%): P/E now against the stock's own P/E over the last ~3 years (month-end prices over the trailing
  earnings known at the time), forward P/E, and PEG (forward P/E over next year's expected earnings growth, growth
  capped at 50%). A company with no positive trailing or forward earnings scores 0 here.
- Quality (25%): next year's expected earnings growth, revenue growth, profit margin.
- Pullback (15%): how far under its 52-week high.
- Analysts (15%): consensus rating, average price target against the price, ratings rising or falling over 3 months.
- Strength (15%): 3-month return against the S&P 500, and whether it is still falling (a new 20-day low under its
  50-day average).
Funds get no dip score (they have no earnings or analyst data here); their pullback and trend are shown.

Not scored, shown beside it: the P/E you paid (cost of your lots over the trailing earnings known on each buy date),
crowd mood (StockTwits bullish/bearish tags on the latest 30 posts), insider net buying, short interest, and how much
margin $1,000 of it needs.

Sources, no keys: Yahoo quoteSummary (financialData, defaultKeyStatistics, summaryDetail, earningsTrend,
recommendationTrend, upgradeDowngradeHistory, netSharePurchaseActivity) refreshed daily; Yahoo fundamentals
timeseries (split-adjusted quarterly and annual diluted EPS) weekly; StockTwits every 6 hours.
"""
import bisect
import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

MODULES = ('financialData,defaultKeyStatistics,summaryDetail,earningsTrend,recommendationTrend,'
           'upgradeDowngradeHistory,netSharePurchaseActivity,price')
WEIGHTS = {'value': 0.30, 'quality': 0.25, 'pullback': 0.15, 'analysts': 0.15, 'trend': 0.15}
LOCK = threading.Lock()


def _raw(x):
    return x.get('raw') if isinstance(x, dict) else x


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


# ---- data -----------------------------------------------------------------------------------------------------
def init(db):
    db.x('CREATE TABLE IF NOT EXISTS fundamentals(symbol TEXT NOT NULL, kind TEXT NOT NULL, data TEXT, at TEXT, '
         'PRIMARY KEY(symbol, kind)) WITHOUT ROWID')


def _cached(db, sym, kind):
    r = db.one('SELECT data, at FROM fundamentals WHERE symbol=? AND kind=?', (sym, kind))
    return (json.loads(r['data']) if r['data'] else None, r['at']) if r else (None, None)


def _put(db, sym, kind, data):
    db.x('INSERT OR REPLACE INTO fundamentals(symbol, kind, data, at) VALUES(?,?,?,?)',
         (sym, kind, json.dumps(data) if data is not None else None, datetime.now().isoformat(timespec='seconds')))


def _summary(yahoo, sym):
    d = yahoo._with_crumb(f'https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(sym)}?modules={MODULES}')
    r = ((d.get('quoteSummary') or {}).get('result') or [None])[0] or {}
    fd, ks, sd, pr = (r.get(k) or {} for k in ('financialData', 'defaultKeyStatistics', 'summaryDetail', 'price'))
    trend = {t.get('period'): t for t in (r.get('earningsTrend') or {}).get('trend') or []}
    rec = {t.get('period'): t for t in (r.get('recommendationTrend') or {}).get('trend') or []}
    cutoff = time.time() - 90 * 86400
    ups = downs = 0
    for u in (r.get('upgradeDowngradeHistory') or {}).get('history') or []:
        if (u.get('epochGradeDate') or 0) >= cutoff:
            ups += u.get('action') == 'up'
            downs += u.get('action') == 'down'
    ins = r.get('netSharePurchaseActivity') or {}
    nxt = trend.get('+1y') or {}
    return {
        'name': pr.get('shortName') or pr.get('longName'), 'type': pr.get('quoteType'),
        'price': _raw(fd.get('currentPrice')) or _raw(pr.get('regularMarketPrice')),
        'target': _raw(fd.get('targetMeanPrice')), 'target_low': _raw(fd.get('targetLowPrice')), 'target_high': _raw(fd.get('targetHighPrice')),
        'rec_mean': _raw(fd.get('recommendationMean')), 'rec_key': fd.get('recommendationKey'), 'analysts': _raw(fd.get('numberOfAnalystOpinions')),
        'rev_growth': _raw(fd.get('revenueGrowth')), 'earn_growth': _raw(fd.get('earningsGrowth')), 'margin': _raw(fd.get('profitMargins')),
        'fcf': _raw(fd.get('freeCashflow')), 'trailing_eps': _raw(ks.get('trailingEps')), 'forward_eps': _raw(ks.get('forwardEps')),
        'peg': _raw(ks.get('pegRatio')), 'short_float': _raw(ks.get('shortPercentOfFloat')),
        'pe': _raw(sd.get('trailingPE')), 'fpe': _raw(sd.get('forwardPE')), 'beta': _raw(sd.get('beta')),
        'high52': _raw(sd.get('fiftyTwoWeekHigh')), 'low52': _raw(sd.get('fiftyTwoWeekLow')),
        'growth_next_year': _raw(nxt.get('growth')), 'eps_next_year': _raw((nxt.get('earningsEstimate') or {}).get('avg')),
        'rec_now': rec.get('0m'), 'rec_3m': rec.get('-3m'), 'ups_90d': ups, 'downs_90d': downs,
        'insider_net': _raw(ins.get('netPercentInsiderShares')), 'insider_buys': _raw(ins.get('buyInfoCount')),
        'insider_sells': _raw(ins.get('sellInfoCount')),
    }


def _eps(yahoo, sym):
    p1 = int(time.time()) - 7 * 365 * 86400
    d = yahoo._get(f'https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{urllib.parse.quote(sym)}'
                   f'?type=quarterlyDilutedEPS,annualDilutedEPS&period1={p1}&period2={int(time.time())}')
    out = {}
    for res in (d.get('timeseries') or {}).get('result') or []:
        typ = (res.get('meta') or {}).get('type', [None])[0]
        out[typ] = [(v['asOfDate'], v['reportedValue']['raw']) for v in res.get(typ) or [] if v and v.get('reportedValue')]
    return out


def _crowd(sym):
    url = f'https://api.stocktwits.com/api/2/streams/symbol/{urllib.parse.quote(sym)}.json'
    d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20).read())
    msgs = d.get('messages') or []
    tags = [((m.get('entities') or {}).get('sentiment') or {}).get('basic') for m in msgs]
    bull, bear = tags.count('Bullish'), tags.count('Bearish')
    return {'bull': bull, 'bear': bear, 'posts': len(msgs), 'watchers': (d.get('symbol') or {}).get('watchlist_count'),
            'first': msgs[-1]['created_at'] if msgs else None, 'last': msgs[0]['created_at'] if msgs else None}


def refresh(db, yahoo, symbols, kinds=None, log=print):
    """Fetch what is stale: fundamentals daily, EPS history weekly, crowd mood every 6 hours. Runs in its own thread."""
    if not LOCK.acquire(blocking=False):
        return 0
    try:
        init(db)
        now, n = datetime.now(), 0
        ages = {'summary': timedelta(hours=20), 'eps': timedelta(days=6), 'crowd': timedelta(hours=6)}
        for sym, kind in symbols:
            for k in (('summary', 'eps', 'crowd') if kind == 'stock' else ('summary',)):
                if kinds and k not in kinds:
                    continue
                _, at = _cached(db, sym, k)
                if at and now - datetime.fromisoformat(at) < ages[k]:
                    continue
                try:
                    data = _summary(yahoo, sym) if k == 'summary' else _eps(yahoo, sym) if k == 'eps' else _crowd(sym)
                except Exception as e:
                    log(f'dip {k} {sym}: {str(e)[:120]}')
                    data = None
                _put(db, sym, k, data)
                n += 1
                if k == 'crowd':
                    time.sleep(1.0)                       # StockTwits allows about 200 calls an hour without a key
        return n
    finally:
        LOCK.release()


# ---- analysis -------------------------------------------------------------------------------------------------
def _eps_points(eps):
    """Trailing-twelve-month EPS known over time: each fiscal year's EPS at its year end, and four quarters summed at
    each quarter end once four are available. Known 40 days after the period ends."""
    pts = {d: v for d, v in (eps or {}).get('annualDilutedEPS') or []}
    q = sorted((eps or {}).get('quarterlyDilutedEPS') or [])
    for i in range(3, len(q)):
        pts[q[i][0]] = sum(v for _, v in q[i - 3:i + 1])
    return sorted(((date.fromisoformat(d) + timedelta(days=40)).isoformat(), v) for d, v in pts.items())


def _eps_at(points, d):
    i = bisect.bisect_right([p for p, _ in points], d) - 1
    return points[i][1] if i >= 0 else None


def _closes(db, sym, since):
    return [(r['d'], r['close']) for r in db.q('SELECT d, close FROM prices WHERE symbol=? AND d>=? AND close IS NOT NULL ORDER BY d', (sym, since))]


def _median(xs):
    xs = sorted(xs)
    return (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2) if xs else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def analyze(db, model, quotes, watch, spx_closes):
    """One row per symbol with every reading, the five part scores and the dip score."""
    init(db)
    today = date.today()
    since = (today - timedelta(days=4 * 366)).isoformat()
    pos = {p['symbol']: p for p in model['positions']}
    margin = (model.get('margin') or [None])[0]
    rates = {r['symbol']: r['pct'] for r in (margin or {}).get('rows', []) if r.get('pct') is not None}
    base_rate = 0.25 * ((margin or {}).get('scale') or 1)
    spx = dict(spx_closes)
    spx_d = sorted(spx)
    rows = []
    syms = [(s, 'held') for s in pos] + [(s, 'watch') for s in watch if s not in pos]
    for sym, src in syms:
        p = pos.get(sym) or {}
        cls = p.get('class') or 'Stock'
        if cls == 'Crypto' or sym.endswith('-USD'):
            continue
        summ, _ = _cached(db, sym, 'summary')
        summ = summ or {}
        q = quotes.get(sym) or {}
        price = p.get('price') or q.get('regularMarketPrice') or summ.get('price')
        if not price:
            continue
        closes = _closes(db, sym, since)
        cd = [d for d, _ in closes]
        cv = [v for _, v in closes]
        r = {'symbol': sym, 'source': src, 'name': p.get('name') or summ.get('name') or q.get('shortName') or sym, 'class': cls,
             'price': price, 'value': p.get('value'), 'weight': p.get('weight'), 'from_ath': p.get('from_ath')}
        # pullback and trend
        last252 = cv[-252:]
        hi52 = max(last252 + [price]) if last252 else summ.get('high52')
        r['from_52w'] = price / hi52 - 1 if hi52 else None
        r['ma50'] = sum(cv[-50:]) / 50 if len(cv) >= 50 else None
        r['ma200'] = sum(cv[-200:]) / 200 if len(cv) >= 200 else None
        low20 = min(cv[-20:]) if len(cv) >= 20 else None
        r['falling'] = bool(low20 and price <= low20 * 1.01 and r['ma50'] and price < r['ma50'])
        if len(cv) >= 64 and spx_d:
            d0 = cd[-64]
            j = bisect.bisect_right(spx_d, d0) - 1
            s0, s1 = (spx[spx_d[j]] if j >= 0 else None), spx[spx_d[-1]]
            r['ret_3m'] = price / cv[-64] - 1
            r['rs_3m'] = r['ret_3m'] - (s1 / s0 - 1) if s0 else None
        else:
            r['ret_3m'] = r['rs_3m'] = None
        # support: the nearest of the 200-day average and the lowest close of the last 6 months that sits under the price
        sup = [x for x in (r['ma200'], min(cv[-126:]) if len(cv) >= 126 else None) if x and x < price]
        r['support'] = max(sup) if sup else None
        # valuation
        eps_hist, _ = _cached(db, sym, 'eps')
        pts = _eps_points(eps_hist)
        ttm = summ.get('trailing_eps') or (pts[-1][1] if pts else None)
        r['pe_now'] = price / ttm if ttm and ttm > 0 else None
        r['fpe'] = summ.get('fpe') if summ.get('fpe') and summ.get('fpe') > 0 else None
        r['eps_ttm'] = ttm
        pe_hist = []
        month_end = {}
        for d, v in closes:
            month_end[d[:7]] = (d, v)
        for d, v in list(month_end.values())[-37:-1]:
            e = _eps_at(pts, d)
            if e and e > 0:
                pe_hist.append(v / e)
        r['pe_median_3y'] = _median(pe_hist) if len(pe_hist) >= 12 else None
        g = summ.get('growth_next_year')
        r['growth_next_year'] = g
        r['peg'] = (r['fpe'] / (g * 100)) if r['fpe'] and g and g > 0.02 else (summ.get('peg') if summ.get('peg') and summ.get('peg') > 0 else None)
        # the P/E you paid: your lots' cost over the trailing earnings known on each buy date
        paid_cost = paid_earn = covered = 0.0
        lots = [l for l in p.get('lots') or [] if l.get('qty', 0) > 1e-9 and l.get('cost')]
        for l in lots:
            e = _eps_at(pts, l['date'])
            if e is not None:
                paid_cost += l['cost']
                paid_earn += l['qty'] * e
                covered += l['cost']
        tot_cost = sum(l['cost'] for l in lots)
        r['pe_paid'] = paid_cost / paid_earn if paid_earn > 0 else None
        r['pe_paid_covered'] = covered / tot_cost if tot_cost else None
        r['avg_cost'] = p.get('avg_cost')
        # analysts and crowd
        for k in ('target', 'target_low', 'target_high', 'rec_mean', 'rec_key', 'analysts', 'rev_growth', 'margin', 'short_float',
                  'insider_net', 'ups_90d', 'downs_90d', 'beta'):
            r[k] = summ.get(k)
        r['target_upside'] = summ['target'] / price - 1 if summ.get('target') else None
        rn, r3 = summ.get('rec_now') or {}, summ.get('rec_3m') or {}

        def buy_share(t):
            tot = sum(t.get(k) or 0 for k in ('strongBuy', 'buy', 'hold', 'sell', 'strongSell'))
            return ((t.get('strongBuy') or 0) + (t.get('buy') or 0)) / tot if tot else None
        r['buy_share'], r['buy_share_3m'] = buy_share(rn), buy_share(r3)
        crowd, _ = _cached(db, sym, 'crowd')
        if crowd and crowd.get('bull', 0) + crowd.get('bear', 0) >= 5:
            r['crowd_bull'] = crowd['bull'] / (crowd['bull'] + crowd['bear'])
            r['crowd_n'] = crowd['bull'] + crowd['bear']
        else:
            r['crowd_bull'] = r['crowd_n'] = None
        # margin: each $1,000 bought on margin adds the stock's maintenance rate to the requirement
        r['maint_rate'] = rates.get(sym) or (1.0 if price < 3 or cls == 'Leveraged ETF' else min(1.0, base_rate))
        r['pe_vs_own'] = r['pe_now'] / r['pe_median_3y'] - 1 if r['pe_now'] and r['pe_median_3y'] else None
        g_cap = min(g, 0.5) if g is not None else None
        r['peg_capped'] = (r['fpe'] / (g_cap * 100)) if r['fpe'] and g_cap and g_cap > 0.02 else None
        r['unprofitable'] = cls == 'Stock' and not r['pe_now'] and not r['fpe']
        r['fund'] = cls != 'Stock'
        rows.append(r)
    _score(rows)
    rows.sort(key=lambda x: -(x['score'] or -1))
    return rows


def _pct_ranks(rows, key, higher_better):
    vals = sorted(r[key] for r in rows if r.get(key) is not None)
    n = len(vals)
    out = {}
    for r in rows:
        v = r.get(key)
        if v is None or n < 3:
            continue
        lo, hi = bisect.bisect_left(vals, v), bisect.bisect_right(vals, v)
        pr = ((lo + hi - 1) / 2) / (n - 1) * 100          # ties share the middle rank
        out[r['symbol']] = pr if higher_better else 100 - pr
    return out


def _score(rows):
    stocks = [r for r in rows if not r['fund']]
    rk = {k: _pct_ranks(stocks, k, hb) for k, hb in (
        ('pe_vs_own', False), ('fpe', False), ('peg_capped', False), ('growth_next_year', True), ('rev_growth', True),
        ('margin', True), ('from_52w', False), ('rec_mean', False), ('target_upside', True), ('rec_drift', True), ('rs_3m', True))}
    for r in rows:
        r['rec_drift'] = (r['buy_share'] - r['buy_share_3m']) if r.get('buy_share') is not None and r.get('buy_share_3m') is not None else None
    rk['rec_drift'] = _pct_ranks(stocks, 'rec_drift', True)
    parts_of = {'value': ('pe_vs_own', 'fpe', 'peg_capped'), 'quality': ('growth_next_year', 'rev_growth', 'margin'),
                'pullback': ('from_52w',), 'analysts': ('rec_mean', 'target_upside', 'rec_drift'), 'trend': ('rs_3m',)}
    for r in rows:
        if r['fund']:
            r['parts'], r['score'], r['coverage'] = {}, None, 0
        else:
            sym = r['symbol']
            few = (r.get('analysts') or 0) < 3
            sc = {}
            for part, keys in parts_of.items():
                xs = [rk[k][sym] for k in keys if sym in rk[k] and not (part == 'analysts' and few)]
                if part == 'trend' and r.get('ma50'):
                    xs.append(15.0 if r['falling'] else 70.0)
                sc[part] = sum(xs) / len(xs) if xs else None
            if r['unprofitable']:
                sc['value'] = 0.0
            have = {k: v for k, v in sc.items() if v is not None}
            wsum = sum(WEIGHTS[k] for k in have)
            r['parts'] = sc
            r['score'] = sum(WEIGHTS[k] * v for k, v in have.items()) / wsum if wsum else None
            r['coverage'] = wsum
        r['flags'] = _flags(r)
        r['reasons'] = _reasons(r)


def _flags(r):
    f = []
    if r['falling']:
        f.append('still falling (new 20-day low, under its 50-day average)')
    if r.get('unprofitable'):
        f.append('no positive earnings (no P/E)')
    if (r.get('weight') or 0) > 0.10:
        f.append(f"already {r['weight'] * 100:.0f}% of your holdings")
    if r['maint_rate'] >= 0.5:
        f.append(f"needs {r['maint_rate'] * 100:.0f}% margin")
    if (r.get('downs_90d') or 0) > (r.get('ups_90d') or 0) + 1:
        f.append(f"{r['downs_90d']} analyst downgrades in 90 days")
    if (r.get('short_float') or 0) > 0.15:
        f.append(f"{r['short_float'] * 100:.0f}% of shares sold short")
    if not r['fund'] and r['coverage'] < 0.6:
        f.append('thin data')
    return f


def _reasons(r):
    out = []
    if r.get('pe_now') and r.get('pe_median_3y'):
        out.append(f"P/E {r['pe_now']:.0f} vs its usual {r['pe_median_3y']:.0f}")
    elif r.get('pe_now'):
        out.append(f"P/E {r['pe_now']:.0f}")
    if r.get('fpe'):
        out.append(f"forward P/E {r['fpe']:.0f}")
    if r.get('growth_next_year') is not None:
        out.append(f"earnings {r['growth_next_year'] * 100:+.0f}% next year (est.)")
    if r.get('from_52w') is not None:
        out.append(f"{-r['from_52w'] * 100:.0f}% under its 52-week high")
    if r.get('target_upside') is not None and (r.get('analysts') or 0) >= 3:
        out.append(f"analysts' target {r['target_upside'] * 100:+.0f}%")
    if r.get('pe_paid') and r.get('pe_now'):
        out.append(f"you paid {r['pe_paid']:.0f}x earnings")
    return out
