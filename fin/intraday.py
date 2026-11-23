"""Intraday portfolio value for the Live, 1D and 1W charts, including pre-market (from 4:00 ET) and after-hours
(to 20:00 ET). Yahoo's spark endpoint returns up to 20 symbols per call with includePrePost; crypto trades around the
clock, mutual funds have one price a day. Robinhood's overnight 24 Hour Market has no free feed, so stock prices hold
at the after-hours close overnight.

The value at each moment uses today's share counts (trades made inside the window are not replayed) plus today's
cash and option marks, so the line shows how the current portfolio moved.
"""
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from .market import eastern_now

RANGES = {'1d': ('1d', '5m'), '5d': ('5d', '30m')}


def spark(yahoo, symbols, rng, interval):
    out = {}
    syms = [s for s in dict.fromkeys(symbols) if s and not s.startswith('^')]
    for i in range(0, len(syms), 20):
        batch = ','.join(urllib.parse.quote(s) for s in syms[i:i + 20])
        try:
            d = yahoo._get(f'https://query1.finance.yahoo.com/v7/finance/spark?symbols={batch}&range={rng}'
                           f'&interval={interval}&includePrePost=true')
        except Exception:
            continue
        for r in (d.get('spark') or {}).get('result') or []:
            resp = (r.get('response') or [{}])[0]
            ts = resp.get('timestamp') or []
            closes = (((resp.get('indicators') or {}).get('quote') or [{}])[0]).get('close') or []
            pts = [(t, c) for t, c in zip(ts, closes) if c is not None]
            if pts:
                out[r['symbol']] = {'pts': pts, 'prev': (resp.get('meta') or {}).get('chartPreviousClose')}
    return out


def _et(ts):
    """Eastern wall-clock time of a unix timestamp (same DST rule as market.eastern_now)."""
    utc = datetime.fromtimestamp(ts, timezone.utc)
    y = utc.year
    from datetime import date
    mar = date(y, 3, 8 + (6 - date(y, 3, 8).weekday()) % 7)
    nov = date(y, 11, 1 + (6 - date(y, 11, 1).weekday()) % 7)
    start = datetime(y, mar.month, mar.day, 7, tzinfo=timezone.utc)
    end = datetime(y, nov.month, nov.day, 6, tzinfo=timezone.utc)
    return (utc + timedelta(hours=-4 if start <= utc < end else -5)).replace(tzinfo=None)


def et_offset(ts):
    """Seconds to add to a unix timestamp to get Eastern wall-clock time (-14400 in summer, -18000 in winter)."""
    return int((_et(ts) - datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)).total_seconds())


def session_of(ts):
    t = _et(ts)
    m = t.hour * 60 + t.minute
    if t.weekday() >= 5:
        return 'closed'
    return 'regular' if 570 <= m < 960 else 'pre' if 240 <= m < 570 else 'post' if 960 <= m < 1200 else 'closed'


def series(model, bars, rng):
    """Portfolio value on the backbone of VOO's bars (4:00 to 20:00 ET), carrying each holding's last price."""
    voo = (bars.get('VOO') or {}).get('pts') or []
    if not voo:
        return None
    timeline = [t for t, _ in voo]
    off = et_offset(timeline[-1])
    domain = None
    if rng == '1d':
        # the latest session only: from the last 4:00 ET on the backbone's last day; the axis spans 4:00 to 20:00
        last_day = _et(timeline[-1]).date()
        timeline = [t for t in timeline if _et(t).date() == last_day]
        start = datetime(last_day.year, last_day.month, last_day.day, 4, tzinfo=timezone.utc).timestamp() - off
        domain = [start, start + 16 * 3600]
    holdings = [(p['symbol'], p['qty'], p['price']) for p in model['positions'] if p['qty']]
    fixed = model['net_worth'] - sum(q * (px or 0) for _, q, px in holdings)     # cash, options, other assets
    cursors = {}
    values = []
    for t in timeline:
        total = fixed
        for sym, q, px in holdings:
            b = bars.get(sym)
            if not b:
                total += q * (px or 0)
                continue
            pts, i = b['pts'], cursors.get(sym, 0)
            while i + 1 < len(pts) and pts[i + 1][0] <= t:
                i += 1
            cursors[sym] = i
            price = pts[i][1] if pts[i][0] <= t else (b.get('prev') or pts[0][1])
            total += q * price
        values.append(total)
    bench = {}
    for b in ('VOO', 'QQQM'):
        pts = (bars.get(b) or {}).get('pts') or []
        vals, i = [], 0
        for t in timeline:
            while i + 1 < len(pts) and pts[i + 1][0] <= t:
                i += 1
            vals.append(pts[i][1] if pts and pts[i][0] <= t else None)
        bench[b] = vals
    prev_close = None
    if rng == '1d':
        # the close before this session: today's live value less the session move and the extended move
        prev_close = model['net_worth'] - model['day_change'] - ((model.get('extended') or {}).get('move') or 0)
    bench_prev = {b: (bars.get(b) or {}).get('prev') for b in ('VOO', 'QQQM')} if rng == '1d' else {}
    return {'range': rng, 'ts': timeline, 'value': values, 'bench': bench, 'bench_prev': bench_prev, 'prev_close': prev_close,
            'domain': domain, 'et_offset': off, 'session': [session_of(t) for t in timeline],
            'labels': [_et(t).strftime('%Y-%m-%d %H:%M') for t in timeline], 'built': time.time()}


def extended_value(model, quotes):
    """Net worth at pre-market or after-hours prices, and the move since the regular close."""
    move = 0.0
    when = None
    for p in model['positions']:
        q = quotes.get(p['symbol']) or {}
        reg = q.get('regularMarketPrice')
        ext, t = None, None
        if q.get('postMarketPrice') and (q.get('postMarketTime') or 0) > (q.get('regularMarketTime') or 0):
            ext, t = q['postMarketPrice'], q.get('postMarketTime')
        if q.get('preMarketPrice') and (q.get('preMarketTime') or 0) > max(q.get('regularMarketTime') or 0, t or 0):
            ext, t = q['preMarketPrice'], q.get('preMarketTime')
        if ext and reg:
            move += p['qty'] * (ext - reg)
            when = max(when or 0, t or 0)
    if not when:
        return None
    return {'net_worth': model['net_worth'] + move, 'move': move, 'at': when, 'session': session_of(when)}
