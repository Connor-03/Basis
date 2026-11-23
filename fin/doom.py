"""Doom score: how hard fear is pressing on prices, 0 to 100, scored the same way for every trading day since 2010 so
today can be lined up against past bottoms (April 8, 2025 and others).

Thirteen readings in four groups. Each becomes 0-100, where 100 is the most fear: readings already on a 0-100
fear/greed scale are flipped (100 - score); the rest are ranked against every trading day of their own history since
2010 (a VIX higher than on 95% of those days scores 95).
- Fear (40%): CNN Fear & Greed; VIX; VIX against the 3-month VIX (above 1 means protection for the next month costs
  more than for the next three, which happens in panics); put/call ratio and junk bond spread (both from CNN's feed).
- Price damage (30%): S&P 500 and Nasdaq-100 drop from their 52-week highs; S&P 500 against its 200-day average;
  S&P 500 one-month return.
- Valuation (15%): S&P 500 and Nasdaq-100 P/E against their own 10-year median (the daily P/E is the latest monthly
  reading moved with the index since that reading, so it is an estimate between readings).
- Crypto (15%): Crypto Fear & Greed (alternative.me) and Bitcoin's drop from its 52-week high.
A group with nothing on a day (CNN's feed starts in 2021) is left out that day and the others are reweighted.

Sources, no keys: Yahoo daily bars (^VIX, ^VIX3M, ^GSPC, ^NDX, BTC-USD since 2009), CNN's Fear & Greed data feed
(since January 2021; it answers 418 to a bare user agent), alternative.me (since 2018), and the valuation table
(multpl.com S&P 500 P/E, worldperatio.com Nasdaq-100 P/E, both monthly).
"""
import bisect
import json
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import date, datetime, timedelta, timezone

YAHOO = {'vix': '^VIX', 'vix3m': '^VIX3M', 'spx': '^GSPC', 'ndx': '^NDX', 'btc': 'BTC-USD'}
CNN = 'https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{}'
CNN_UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/124.0 Safari/537.36', 'Accept': '*/*', 'Referer': 'https://edition.cnn.com/',
          'Origin': 'https://edition.cnn.com'}
CRYPTO_FG = 'https://api.alternative.me/fng/?limit={}&format=json'
SCORE_FROM = '2010-01-01'

GROUPS = [('fear', 'Fear', 0.40), ('damage', 'Price damage', 0.30), ('value', 'Valuation', 0.15), ('crypto', 'Crypto', 0.15)]
# key, group, name, how it scores ('flip' = 100 - value, 'high' = higher is more fear, 'low' = lower is more fear), unit
COMPONENTS = [
    ('fg', 'fear', 'CNN Fear & Greed', 'flip', 'index'),
    ('vix', 'fear', 'VIX (expected S&P 500 swings)', 'high', 'level'),
    ('vix_ratio', 'fear', 'VIX vs 3-month VIX', 'high', 'ratio'),
    ('pc', 'fear', 'Put/call ratio (5-day)', 'high', 'ratio'),
    ('junk', 'fear', 'Junk bond spread', 'high', 'pctpt'),
    ('spx_dd', 'damage', 'S&P 500 from 52-week high', 'low', 'pct'),
    ('ndx_dd', 'damage', 'Nasdaq-100 from 52-week high', 'low', 'pct'),
    ('spx_200', 'damage', 'S&P 500 vs 200-day average', 'low', 'pct'),
    ('spx_1m', 'damage', 'S&P 500 one-month return', 'low', 'pct'),
    ('spx_pe', 'value', 'S&P 500 P/E vs its 10-year median', 'low', 'pe'),
    ('ndx_pe', 'value', 'Nasdaq-100 P/E vs its 10-year median', 'low', 'pe'),
    ('cfg', 'crypto', 'Crypto Fear & Greed', 'flip', 'index'),
    ('btc_dd', 'crypto', 'Bitcoin from 52-week high', 'low', 'pct'),
]
# recent bottoms to compare against (dates checked against the price data and the news of each day)
MARKS = [
    {'key': 'tariff', 'label': 'Tariff bottom', 'from': '2025-04-08', 'to': '2025-04-08',
     'note': 'Lowest close after the April 2 "Liberation Day" tariffs, 19% under the S&P 500\'s high; the 90-day pause came the next day.'},
    {'key': 'btc', 'label': 'Bitcoin crash low', 'from': '2026-02-05', 'to': '2026-02-05',
     'note': 'Bitcoin closed at $62,700, down 30% in a week from $89,200 on January 28.'},
    {'key': 'iran', 'label': 'Iran war oil shock', 'from': '2026-03-30', 'to': '2026-03-30',
     'note': 'The war began with US and Israeli strikes on February 28; on March 30 it widened, US crude closed above $100 for '
             'the first time since, and the S&P 500 and Nasdaq-100 made their lowest closes of 2026.'},
]
# names for the fearful stretches since 2010, matched by the date the score peaked
EVENTS = [
    ('2010-05-01', '2010-07-10', 'Flash Crash, Greek debt crisis'),
    ('2010-11-05', '2010-11-30', 'Irish bailout'),
    ('2011-03-01', '2011-03-25', 'Japan earthquake'),
    ('2011-07-25', '2011-10-10', 'US credit downgrade'),
    ('2012-05-01', '2012-06-30', 'Euro debt crisis'),
    ('2012-11-01', '2012-12-31', 'Fiscal cliff'),
    ('2013-05-20', '2013-07-10', 'Taper tantrum'),
    ('2013-09-25', '2013-10-17', 'Government shutdown, debt ceiling'),
    ('2014-01-20', '2014-02-10', 'Emerging markets selloff'),
    ('2014-10-01', '2014-10-31', 'Growth scare, Ebola fears'),
    ('2014-12-01', '2014-12-20', 'Oil crash, ruble crisis'),
    ('2015-08-10', '2015-09-30', 'China devaluation crash'),
    ('2016-01-01', '2016-02-29', 'Oil crash, China slowdown'),
    ('2016-06-23', '2016-07-08', 'Brexit vote'),
    ('2018-02-01', '2018-02-28', 'Volmageddon (VIX spike)'),
    ('2018-10-01', '2018-12-31', 'Fed hikes and trade war (Christmas Eve low)'),
    ('2019-05-01', '2019-06-10', 'Trade war escalation'),
    ('2019-08-01', '2019-08-31', 'Trade war, yield curve inversion'),
    ('2020-02-20', '2020-04-30', 'COVID crash'),
    ('2022-01-01', '2022-03-31', 'Fed hikes begin, Russia invades Ukraine'),
    ('2022-04-01', '2022-10-31', '2022 inflation bear market'),
    ('2023-03-08', '2023-03-31', 'Regional bank failures (SVB)'),
    ('2023-09-20', '2023-10-31', 'Bond yields hit 5%'),
    ('2024-07-25', '2024-08-15', 'Yen carry trade unwind'),
    ('2025-02-20', '2025-04-30', 'Tariff crash'),
    ('2026-01-26', '2026-02-27', 'Bitcoin crash'),
    ('2026-02-28', '2026-04-30', 'Iran war oil shock'),
]


def event_name(d):
    return next((name for lo, hi, name in EVENTS if lo <= d <= hi), None) if d else None


BANDS = [(80, 'Panic'), (60, 'Very fearful'), (40, 'Fearful'), (20, 'Uneasy'), (0, 'Calm')]


def label(score):
    return next(name for lo, name in BANDS if score >= lo) if score is not None else None


# ---- data ---------------------------------------------------------------------------------------------------------
def _store(db, series, rows):
    db.many('INSERT OR REPLACE INTO macro_daily(series, d, value) VALUES(?,?,?)',
            [(series, d, v) for d, v in rows if v is not None])


def _last(db, series):
    r = db.one('SELECT MAX(d) AS d FROM macro_daily WHERE series=?', (series,))
    return r['d'] if r else None


def refresh(db, yahoo, log=print):
    """Bring every series up to date (only the last few weeks once the history is stored)."""
    db.x('CREATE TABLE IF NOT EXISTS macro_daily(series TEXT NOT NULL, d TEXT NOT NULL, value REAL NOT NULL, '
         'PRIMARY KEY(series, d)) WITHOUT ROWID')
    n = 0
    for key, sym in YAHOO.items():
        last = _last(db, key)
        start = (date.fromisoformat(last) - timedelta(days=14)).isoformat() if last else '2009-01-01'
        try:
            bars = yahoo.history(sym, start)['bars']
            _store(db, key, [(d, c) for d, c, _ in bars])
            n += len(bars)
        except Exception as e:
            log(f'doom {sym}: {e}')
    last = _last(db, 'fg')
    start = (date.fromisoformat(last) - timedelta(days=21)).isoformat() if last else '2021-01-04'
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(CNN.format(start), headers=CNN_UA), timeout=30).read())
        for series, key in (('fg', 'fear_and_greed_historical'), ('pc', 'put_call_options'), ('junk', 'junk_bond_demand')):
            pts = (d.get(key) or {}).get('data') or []
            _store(db, series, [(datetime.fromtimestamp(p['x'] / 1000, timezone.utc).date().isoformat(), p.get('y')) for p in pts])
            n += len(pts)
        now = d.get('fear_and_greed') or {}
        if now.get('score') is not None and now.get('timestamp'):
            _store(db, 'fg', [(now['timestamp'][:10], now['score'])])
    except Exception as e:
        log(f'doom cnn: {e}')
    try:
        limit = 0 if not _last(db, 'cfg') else 30
        d = json.loads(urllib.request.urlopen(urllib.request.Request(CRYPTO_FG.format(limit), headers={'User-Agent': 'Mozilla/5.0'}),
                                              timeout=30).read())
        pts = d.get('data') or []
        _store(db, 'cfg', [(datetime.fromtimestamp(int(p['timestamp']), timezone.utc).date().isoformat(), float(p['value'])) for p in pts])
        n += len(pts)
    except Exception as e:
        log(f'doom crypto fear: {e}')
    return n


# ---- scoring ------------------------------------------------------------------------------------------------------
def _rolling_max(vals, window):
    out, dq = [], deque()
    for i, v in enumerate(vals):
        while dq and vals[dq[-1]] <= v:
            dq.pop()
        dq.append(i)
        while dq[0] <= i - window:
            dq.popleft()
        out.append(vals[dq[0]])
    return out


def _asof(series_dict):
    """Look up the latest value on or before a date."""
    ds = sorted(series_dict)
    vs = [series_dict[d] for d in ds]

    def f(d, max_age=7):
        i = bisect.bisect_right(ds, d) - 1
        if i < 0 or (date.fromisoformat(d) - date.fromisoformat(ds[i])).days > max_age:
            return None
        return vs[i]
    return f


def _pe_ratio(days, idx, monthly):
    """Daily P/E estimate against the median of the monthly readings in the 10 years before. Returns (pe, ratio)."""
    if not monthly:
        return [None] * len(days), [None] * len(days)
    rd = [d for d, _ in monthly]
    rv = [v for _, v in monthly]
    px_at = _asof(dict(zip(days, idx)))
    pe, ratio = [], []
    for d, p in zip(days, idx):
        i = bisect.bisect_right(rd, d) - 1
        if i < 0 or not p:
            pe.append(None)
            ratio.append(None)
            continue
        base = px_at(rd[i], max_age=10)
        est = rv[i] * p / base if base else rv[i]
        lo = bisect.bisect_left(rd, f'{int(d[:4]) - 10}{d[4:]}')
        window = sorted(rv[lo:i + 1])
        med = window[len(window) // 2] if len(window) >= 24 else None
        pe.append(est)
        ratio.append(est / med if med else None)
    return pe, ratio


def compute(db):
    """Every series aligned on S&P 500 trading days, each component's 0-100 score, the groups and the total."""
    by = {}
    for r in db.q('SELECT series, d, value FROM macro_daily ORDER BY series, d'):
        by.setdefault(r['series'], {})[r['d']] = r['value']
    spx_d = by.get('spx') or {}
    days = sorted(spx_d)
    if len(days) < 300:
        return None
    spx = [spx_d[d] for d in days]
    ndx_at, vix_at, v3_at = _asof(by.get('ndx', {})), _asof(by.get('vix', {})), _asof(by.get('vix3m', {}))
    ndx = [ndx_at(d) for d in days]
    raw = {k: [None] * len(days) for k, *_ in COMPONENTS}
    hi = _rolling_max(spx, 252)
    ndx_f = [v if v is not None else 0 for v in ndx]
    nhi = _rolling_max(ndx_f, 252)
    run = 0.0
    for i, d in enumerate(days):
        run += spx[i]
        if i >= 200:
            run -= spx[i - 200]
        if i >= 251:
            raw['spx_dd'][i] = spx[i] / hi[i] - 1
            if ndx[i]:
                raw['ndx_dd'][i] = ndx[i] / nhi[i] - 1
        if i >= 199:
            raw['spx_200'][i] = spx[i] / (run / 200) - 1
        if i >= 21:
            raw['spx_1m'][i] = spx[i] / spx[i - 21] - 1
        v, v3 = vix_at(d), v3_at(d)
        raw['vix'][i] = v
        raw['vix_ratio'][i] = v / v3 if v and v3 else None
    for k in ('fg', 'pc', 'junk', 'cfg'):
        f = _asof(by.get(k, {}))
        raw[k] = [f(d, max_age=5) for d in days]
    # Bitcoin trades every day: its 52-week high comes from its own calendar
    btc = by.get('btc') or {}
    bd = sorted(btc)
    bhi = dict(zip(bd, _rolling_max([btc[d] for d in bd], 365)))
    bat = _asof({d: btc[d] / bhi[d] - 1 for i, d in enumerate(bd) if i >= 364})
    raw['btc_dd'] = [bat(d, max_age=4) for d in days]
    try:
        val = {}
        for r in db.q("SELECT series, d, value FROM valuation WHERE series IN ('sp500_pe','ndx_pe') ORDER BY d"):
            val.setdefault(r['series'], []).append((r['d'], r['value']))
    except Exception:
        val = {}
    pe = {}
    pe['spx'], raw['spx_pe'] = _pe_ratio(days, spx, val.get('sp500_pe'))
    pe['ndx'], raw['ndx_pe'] = _pe_ratio(days, [x or 0 for x in ndx], val.get('ndx_pe'))

    first = bisect.bisect_left(days, SCORE_FROM)
    scores, refs = {}, {}
    for key, grp, name, how, unit in COMPONENTS:
        vals = raw[key]
        if how == 'flip':
            scores[key] = [100 - v if v is not None else None for v in vals]
            continue
        ref = sorted(v for v in vals[first:] if v is not None)
        if len(ref) < 100:
            scores[key] = [None] * len(days)
            continue
        refs[key] = ref
        n = len(ref)
        if how == 'high':
            scores[key] = [100.0 * bisect.bisect_left(ref, v) / n if v is not None else None for v in vals]
        else:
            scores[key] = [100.0 * (n - bisect.bisect_right(ref, v)) / n if v is not None else None for v in vals]
    group_scores = {g: [] for g, _, _ in GROUPS}
    total = []
    for i in range(len(days)):
        acc = wsum = 0.0
        for g, _, w in GROUPS:
            xs = [scores[k][i] for k, kg, *_ in COMPONENTS if kg == g and scores[k][i] is not None]
            s = sum(xs) / len(xs) if xs else None
            group_scores[g].append(s)
            if s is not None:
                acc += w * s
                wsum += w
        total.append(acc / wsum if wsum else None)
    return {'days': days, 'spx': spx, 'ndx': ndx, 'raw': raw, 'scores': scores, 'groups': group_scores, 'total': total,
            'pe': pe, 'first': first, 'ref': refs, 'btc': (bd, [btc[d] for d in bd])}


def _snapshot(c, i):
    return {'date': c['days'][i], 'score': c['total'][i], 'label': label(c['total'][i]),
            'groups': {g: c['groups'][g][i] for g, _, _ in GROUPS},
            'components': {k: {'raw': c['raw'][k][i], 'score': c['scores'][k][i]} for k, *_ in COMPONENTS},
            'pe': {'spx': c['pe']['spx'][i], 'ndx': c['pe']['ndx'][i]}}


# ---- highest and lowest reading inside each day -------------------------------------------------------------------
# The score above is one reading a day, from closing prices. Inside a day it moves with the S&P 500, the Nasdaq-100,
# the VIX, the 3-month VIX and Bitcoin, so each day also keeps its highest and lowest reading, rebuilt from 5-minute
# prices of those five (regular session; Yahoo keeps about a month of them). CNN's readings and the crypto fear index
# have no intraday history, so they count at the day's own value. A reading the app itself made live also counts, and
# so does one entered by hand (`seen`, for a reading the owner saw on the page before these were kept). src says which:
# 'prices' (rebuilt from 5-minute prices), 'live' (the app's own reading at a refresh) or 'seen'.
def _total(c, r):
    """The 0-100 total for one set of raw component values, scored exactly as compute() scores a day."""
    acc = wsum = 0.0
    for g, _, w in GROUPS:
        xs = []
        for key, kg, _, how, _ in COMPONENTS:
            v = r.get(key)
            if kg != g or v is None:
                continue
            if how == 'flip':
                xs.append(100 - v)
            elif key in c['ref']:
                ref = c['ref'][key]
                xs.append(100.0 * (bisect.bisect_left(ref, v) if how == 'high' else len(ref) - bisect.bisect_right(ref, v)) / len(ref))
        if xs:
            acc += w * sum(xs) / len(xs)
            wsum += w
    return acc / wsum if wsum else None


def _scorer(c, i):
    """A function px -> score for trading day i with live prices swapped in: px has any of spx, ndx, vix, vix3m, btc."""
    days, spx, ndx, raw = c['days'], c['spx'], c['ndx'], c['raw']
    base = {k: raw[k][i] for k, *_ in COMPONENTS}
    hi = max(spx[i - 251:i]) if i >= 251 else None
    run = sum(spx[i - 199:i]) if i >= 199 else None
    nprev = [v for v in ndx[max(0, i - 251):i] if v]
    v3_close = raw['vix'][i] / raw['vix_ratio'][i] if raw['vix'][i] and raw['vix_ratio'][i] else None
    bd, bv = c['btc']
    d0 = (date.fromisoformat(days[i]) - timedelta(days=364)).isoformat()
    lo_i, hi_i = bisect.bisect_left(bd, d0), bisect.bisect_left(bd, days[i])
    bhi = max(bv[lo_i:hi_i]) if hi_i - lo_i >= 300 else None

    def f(px):
        r = dict(base)
        s = px.get('spx')
        if s:
            if hi is not None:
                r['spx_dd'] = s / max(hi, s) - 1
            if run is not None:
                r['spx_200'] = s / ((run + s) / 200) - 1
            if i >= 21:
                r['spx_1m'] = s / spx[i - 21] - 1
            if base['spx_pe'] is not None and spx[i]:
                r['spx_pe'] = base['spx_pe'] * s / spx[i]
        n = px.get('ndx')
        if n and ndx[i]:
            if len(nprev) >= 200:
                r['ndx_dd'] = n / max(max(nprev), n) - 1
            if base['ndx_pe'] is not None:
                r['ndx_pe'] = base['ndx_pe'] * n / ndx[i]
        v, v3 = px.get('vix'), px.get('vix3m') or v3_close
        if v:
            r['vix'] = v
            if v3:
                r['vix_ratio'] = v / v3
        b = px.get('btc')
        if b and bhi:
            r['btc_dd'] = b / max(bhi, b) - 1
        return _total(c, r)
    return f


def _bars(yahoo, sym):
    """A month of 5-minute closes: [(unix time, close)]."""
    d = yahoo._get(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=1mo&interval=5m&includePrePost=false')
    r = ((d.get('chart') or {}).get('result') or [None])[0] or {}
    cl = (((r.get('indicators') or {}).get('quote') or [{}])[0]).get('close') or []
    return [(t, v) for t, v in zip(r.get('timestamp') or [], cl) if v is not None]


def refresh_hilo(db, yahoo, c=None, log=print):
    """Store each day's highest and lowest reading for the days Yahoo still has 5-minute prices for. Days already
    stored and over are left alone; today is redone each time, and never lowered below what was already seen."""
    from .intraday import _et
    _hilo_table(db)
    c = c or compute(db)
    if not c:
        return 0
    days = c['days']
    have = _hilo(db)
    today = days[-1]
    try:
        bars = {k: _bars(yahoo, sym) for k, sym in YAHOO.items()}
    except Exception as e:
        log(f'doom intraday: {e}')
        return 0
    if not bars.get('spx'):
        return 0
    others = {k: ([t for t, _ in v], [x for _, x in v]) for k, v in bars.items() if k != 'spx'}
    by_day = {}
    for t, v in bars['spx']:
        by_day.setdefault(_et(t).date().isoformat(), []).append((t, v))
    n = 0
    for d, pts in sorted(by_day.items()):
        if d not in days or (d in have and d != today):
            continue
        i = days.index(d)
        f = _scorer(c, i)
        path = []
        for t, v in pts:
            px = {'spx': v}
            for k, (ts, vs) in others.items():
                j = bisect.bisect_right(ts, t) - 1
                if j >= 0 and t - ts[j] <= 3600:               # the latest price within the hour before
                    px[k] = vs[j]
            s = f(px)
            if s is not None:
                path.append((s, t, 'prices'))
        if c['total'][i] is not None:                           # the day's own reading (the close, or right now)
            path.append((c['total'][i], int(time.time()) if d == today else pts[-1][0], 'live' if d == today else 'prices'))
        if not path:
            continue
        hi, lo = max(path), min(path)
        old = have.get(d)
        if old and old['hi'] >= hi[0]:
            hi = (old['hi'], old['hi_at'], old['src'])
        if old and old['lo'] <= lo[0]:
            lo = (old['lo'], old['lo_at'])
        db.x('INSERT OR REPLACE INTO doom_hilo(d, hi, hi_at, lo, lo_at, src) VALUES(?,?,?,?,?,?)', (d, hi[0], hi[1], lo[0], lo[1], hi[2]))
        n += 1
    return n


def _hilo_table(db):
    db.x('CREATE TABLE IF NOT EXISTS doom_hilo(d TEXT PRIMARY KEY, hi REAL NOT NULL, hi_at INTEGER, lo REAL NOT NULL, '
         "lo_at INTEGER, src TEXT NOT NULL DEFAULT 'prices')")


def _hilo(db):
    try:
        return {r['d']: dict(r) for r in db.q('SELECT d, hi, hi_at, lo, lo_at, src FROM doom_hilo')}
    except Exception:
        return {}


def seen(db, d, score):
    """Record a reading the owner saw on the page on day d (kept only if it is the highest for that day)."""
    _hilo_table(db)
    old = _hilo(db).get(d)
    if old and old['hi'] >= score:
        return False
    lo = old or {'lo': score, 'lo_at': None}
    db.x('INSERT OR REPLACE INTO doom_hilo(d, hi, hi_at, lo, lo_at, src) VALUES(?,?,?,?,?,?)', (d, float(score), None, lo['lo'], lo['lo_at'], 'seen'))
    return True


def _episodes(c, level, gap=40, horizon=126):
    """Each time the score first reached `level` after at least `gap` trading days below it: how much further the
    S&P 500 fell before its low in the next six months, how high the score went, and the S&P 500 return after."""
    days, tot, spx = c['days'], c['total'], c['spx']
    out, below = [], gap
    for i in range(c['first'], len(days)):
        s = tot[i]
        if s is None:
            continue
        if s >= level and below >= gap:
            end = min(len(days) - 1, i + horizon)
            low_i = min(range(i, end + 1), key=lambda j: spx[j])
            peak_i = max(range(i, end + 1), key=lambda j: tot[j] or 0)
            fwd = {n: (spx[i + n] / spx[i] - 1) if i + n < len(days) else None for n in (21, 63, 252)}
            out.append({'date': days[i], 'score': s, 'further_drop': spx[low_i] / spx[i] - 1, 'low_date': days[low_i],
                        'days_to_low': low_i - i, 'peak_score': tot[peak_i], 'peak_date': days[peak_i],
                        'fwd': {str(k): v for k, v in fwd.items()}, 'complete': i + horizon < len(days),
                        'name': event_name(days[peak_i]) or event_name(days[i])})
        below = 0 if s >= level else below + 1
    return out


def _peaks(c, min_score=70, sep=120):
    """The most fearful days since 2010: local highs of the score at least `sep` trading days apart."""
    tot, days = c['total'], c['days']
    idx = sorted((i for i in range(c['first'], len(days)) if tot[i] is not None and tot[i] >= min_score), key=lambda i: -tot[i])
    keep = []
    for i in idx:
        if all(abs(i - j) >= sep for j in keep):
            keep.append(i)
    keep = [i for i in keep if i < len(days) - 20]          # the last four weeks cannot be called a peak yet
    out = []
    for i in sorted(keep):
        fwd = {str(n): (c['spx'][i + n] / c['spx'][i] - 1) if i + n < len(days) else None for n in (63, 252)}
        out.append({'date': days[i], 'score': tot[i], 'label': label(tot[i]), 'fwd': fwd, 'name': event_name(days[i])})
    return out


# doom cycles: one starts when the score reaches CYCLE_START, its peak is the highest score until it ends, and it ends
# after CALM_DAYS trading days in a row under CALM. A cycle that peaked at ERA or higher is an era of doom.
CYCLE_START, CALM, CALM_DAYS, ERA = 65, 45, 10, 80


def _cycles(c):
    days, tot, spx = c['days'], c['total'], c['spx']
    n = len(days)
    raw, cur, calm = [], None, 0
    for i in range(c['first'], n):
        s = tot[i]
        if s is None:
            continue
        if cur is None:
            if s >= CYCLE_START:
                cur, calm = {'start': i, 'peak': i}, 0
            continue
        if s > tot[cur['peak']]:
            cur['peak'] = i
        calm = calm + 1 if s < CALM else 0
        if calm >= CALM_DAYS:
            cur['end'] = i
            raw.append(cur)
            cur = None
    if cur:
        cur['calm'] = calm
        raw.append(cur)
    out = []
    for x in raw:
        st, pk, end = x['start'], x['peak'], x.get('end')
        last = end if end is not None else n - 1
        low = min(range(st, last + 1), key=lambda j: spx[j])
        fwd = {str(k): (spx[pk + k] / spx[pk] - 1) if pk + k < n else None for k in (63, 252)}
        cy = {'start': days[st], 'peak_date': days[pk], 'peak': tot[pk], 'end': days[end] if end is not None else None,
              'name': event_name(days[pk]), 'era': tot[pk] >= ERA, 'days_to_peak': pk - st, 'length': last - st,
              'spx_low_date': days[low], 'spx_drop': spx[low] / spx[st] - 1, 'low_vs_peak_days': low - pk,
              'spx_from_peak': fwd, 'path': [round(tot[j], 1) if tot[j] is not None else None for j in range(st, min(last, st + 160) + 1)]}
        if end is None:
            now, since = tot[n - 1], n - 1 - pk
            if since <= 2:
                phase = 'Rising'
            elif now < CALM:
                phase = 'Calming'
            elif tot[pk] - now >= 10:
                phase = 'Easing'
            else:
                phase = 'Peaking'
            cy.update({'open': True, 'phase': phase, 'now': now, 'days_since_peak': since, 'calm_days': x.get('calm', 0),
                       'spx_from_start': spx[n - 1] / spx[st] - 1})
        out.append(cy)
    return out


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    return (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2) if xs else None


def summary(db, personal=None):
    """Everything the page shows. personal: {'dates': [...], 'index': [...]} (the time-weighted index) for the
    'your portfolio from its high' row, which is context only and not part of the score."""
    c = compute(db)
    if not c:
        return None
    n = len(c['days'])
    now_i = max(i for i in range(n) if c['total'][i] is not None)
    now = _snapshot(c, now_i)
    marks = []
    for mk in MARKS:
        rng = [i for i in range(n) if mk['from'] <= c['days'][i] <= mk['to'] and c['total'][i] is not None]
        if not rng:
            continue
        i = max(rng, key=lambda j: c['total'][j])
        marks.append({**_snapshot(c, i), 'key': mk['key'], 'name': mk['label'], 'note': mk.get('note')})
    # your time-weighted portfolio against its own running high, on each of those days
    if personal and personal.get('dates'):
        pd_, pi = personal['dates'], personal['index']
        run, dd = None, {}
        for d, v in zip(pd_, pi):
            if v is None:
                continue
            run = v if run is None else max(run, v)
            dd[d] = v / run - 1
        at = _asof(dd)
        for snap in [now] + marks:
            snap['you_dd'] = at(snap['date'], max_age=5)
    level = int(round(now['score']))                     # the whole number the page shows
    eps = _episodes(c, level)
    done = [e for e in eps if e['complete']]
    base = None
    if done:
        f12 = [e['fwd']['252'] for e in done if e['fwd']['252'] is not None]
        base = {'level': level, 'count': len(done), 'median_further_drop': _median([e['further_drop'] for e in done]),
                'worst_further_drop': min(e['further_drop'] for e in done),
                'median_days_to_low': _median([e['days_to_low'] for e in done]),
                'median_peak': _median([e['peak_score'] for e in done]),
                'up_12m': sum(1 for x in f12 if x > 0), 'n_12m': len(f12), 'median_12m': _median(f12),
                'median_3m': _median([e['fwd']['63'] for e in done])}
    start = max(c['first'], 0)
    cyc = _cycles(c)
    cur = cyc[-1] if cyc and cyc[-1].get('open') else None
    # the highest reading inside a day (see refresh_hilo); cycles and eras are still judged on closing readings
    hl = _hilo(db)
    t = hl.get(now['date'])
    if t:
        now.update({'high': max(t['hi'], now['score']), 'high_at': t['hi_at'], 'low': min(t['lo'], now['score']), 'low_at': t['lo_at']})
    for x in cyc[-6:]:
        in_cycle = [(v['hi'], d, v['hi_at'] or 0, v['src']) for d, v in hl.items() if x['start'] <= d <= (x['end'] or '9999')]
        top = max(in_cycle) if in_cycle else None
        if top and top[0] > x['peak'] + 1e-9:
            x.update({'high': top[0], 'high_date': top[1], 'high_at': top[2] or None, 'high_src': top[3]})
    eras = [x for x in cyc if x['era'] and not x.get('open')]
    cycle = {'rules': {'start': CYCLE_START, 'calm': CALM, 'calm_days': CALM_DAYS, 'era': ERA}, 'current': cur,
             'eras': eras, 'scares': [x for x in cyc if not x['era'] and not x.get('open')],
             'era_stats': {'median_peak': _median([x['peak'] for x in eras]), 'median_days_to_peak': _median([x['days_to_peak'] for x in eras]),
                           'median_low_vs_peak': _median([x['low_vs_peak_days'] for x in eras]),
                           'median_drop': _median([x['spx_drop'] for x in eras]), 'count': len(eras)} if eras else None}
    return {'now': now, 'marks': marks, 'episodes': eps, 'base': base, 'peaks': _peaks(c, min_score=80), 'cycle': cycle,
            'history': {'dates': c['days'][start:], 'score': [round(x, 1) if x is not None else None for x in c['total'][start:]],
                        'spx': [round(x, 2) for x in c['spx'][start:]]},
            'groups': [{'key': g, 'name': nm, 'weight': w} for g, nm, w in GROUPS],
            'components': [{'key': k, 'group': g, 'name': nm, 'how': how, 'unit': u} for k, g, nm, how, u in COMPONENTS],
            'bands': [{'from': lo, 'name': nm} for lo, nm in BANDS]}
