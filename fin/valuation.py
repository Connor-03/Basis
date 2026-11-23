"""Is the market expensive? Price/earnings of the S&P 500 and the Nasdaq-100 over time, judged against their own
history, plus the Shiller P/E and the earnings yield against the 10-year Treasury.

Sources (refreshed daily, no keys):
- S&P 500 P/E (trailing twelve-month reported earnings, monthly since 1871) and Shiller P/E (CAPE, ten-year average
  real earnings) from multpl.com, whose tables come from S&P and Robert Shiller's data.
- Nasdaq-100 P/E (from the QQQ fund, monthly since 1990) from worldperatio.com.
- 10-year Treasury yield (^TNX) from Yahoo.

Verdict, the same bands worldperatio uses: within 1 standard deviation of the typical level = fair; 1 to 2 above =
overvalued; more than 2 above = strongly overvalued; and the same below for undervalued. The typical level is the
median and the spread is 1.4826 x the median absolute deviation (equal to the mean and standard deviation for normal
data), because trailing P/E jumps when earnings collapse (S&P 500: 123 in May 2009) and one such stretch would drag
a plain average around for twenty years.
"""
import math
import re
import urllib.request
from datetime import date, datetime

UA = {'User-Agent': 'Mozilla/5.0'}
SERIES = {
    'sp500_pe': {'name': 'S&P 500 P/E', 'short': 'S&P 500', 'source': 'multpl.com (S&P, Shiller data)',
                 'url': 'https://www.multpl.com/s-p-500-pe-ratio/table/by-month'},
    'ndx_pe': {'name': 'Nasdaq-100 P/E', 'short': 'Nasdaq-100', 'source': 'worldperatio.com (QQQ)',
               'url': 'https://worldperatio.com/index/nasdaq-100/'},
    'cape': {'name': 'Shiller P/E (CAPE)', 'short': 'Shiller CAPE', 'source': 'multpl.com (Robert Shiller)',
             'url': 'https://www.multpl.com/shiller-pe/table/by-month'},
}
WINDOWS = (10, 20, 0)          # years; 0 = all history


def _get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode('utf-8', 'replace')


def _multpl(url):
    html = _get(url)
    out = []
    for d, v in re.findall(r'<td>([A-Z][a-z]{2} \d{1,2}, \d{4})</td>\s*<td>\s*(?:&#x2002;|<abbr[^>]*>[^<]*</abbr>)?\s*([\d.]+)', html):
        out.append((datetime.strptime(d, '%b %d, %Y').date().isoformat(), float(v)))
    return sorted(out)


def _worldperatio(url):
    html = _get(url)
    m = re.search(r'detailPE_data\s*=\s*\[(.*?)\];', html, re.S)
    if not m:
        return []
    out = []
    for y, mo, d, v in re.findall(r'Date\.UTC\((\d{4}),\s*(\d{1,2}),\s*(\d{1,2})\)\s*,\s*([\d.]+)', m.group(1)):
        out.append((date(int(y), int(mo) + 1, int(d)).isoformat(), float(v)))   # JavaScript months start at 0
    cur = re.search(r'Current P/E \(\s*([\d.]+)\s*\)', re.sub(r'<[^>]+>', ' ', html))
    if cur and out:
        out.append((date.today().isoformat(), float(cur.group(1))))
    return sorted(dict(out).items())


def refresh(db, log=print):
    db.x('CREATE TABLE IF NOT EXISTS valuation(series TEXT NOT NULL, d TEXT NOT NULL, value REAL NOT NULL, '
         'PRIMARY KEY(series, d)) WITHOUT ROWID')
    n = 0
    for key, meta in SERIES.items():
        try:
            rows = _worldperatio(meta['url']) if 'worldperatio' in meta['url'] else _multpl(meta['url'])
        except Exception as e:
            log(f'valuation {key}: {e}')
            continue
        if len(rows) < 24:
            log(f'valuation {key}: only {len(rows)} rows, kept the old data')
            continue
        # one point per month for history, plus the newest reading
        db.many('INSERT OR REPLACE INTO valuation(series, d, value) VALUES(?,?,?)', [(key, d, v) for d, v in rows])
        n += len(rows)
    return n


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2) if n else None


def _stats(vals):
    """Robust center and spread: median and 1.4826 x median absolute deviation."""
    if len(vals) < 12:
        return None, None
    med = _median(vals)
    mad = _median([abs(v - med) for v in vals])
    return med, 1.4826 * mad if mad else None


def verdict(z):
    if z is None:
        return 'Not enough history'
    if z > 2:
        return 'Strongly overvalued'
    if z > 1:
        return 'Overvalued'
    if z >= -1:
        return 'Fair value'
    if z >= -2:
        return 'Undervalued'
    return 'Strongly undervalued'


def summary(db, quotes=None):
    try:
        rows = db.q('SELECT series, d, value FROM valuation ORDER BY series, d')
    except Exception:
        rows = []
    by = {}
    for r in rows:
        by.setdefault(r['series'], []).append((r['d'], r['value']))
    out = {'series': {}, 'sources': {k: v['source'] for k, v in SERIES.items()}}
    today = date.today()
    for key, pts in by.items():
        meta = SERIES.get(key, {'name': key, 'short': key})
        monthly = {}
        for d, v in pts:
            monthly[d[:7]] = (d, v)                   # latest reading in each month
        hist = [monthly[k] for k in sorted(monthly)]
        cur_d, cur = pts[-1]
        windows = {}
        for yrs in WINDOWS:
            start = f'{today.year - yrs}-{today.month:02d}' if yrs else '0000'
            vals = [v for d, v in hist if d[:7] >= start and d != cur_d]
            mean, sd = _stats(vals)
            z = (cur - mean) / sd if sd else None
            pct = sum(1 for v in vals if v < cur) / len(vals) if vals else None
            windows[str(yrs or 'all')] = {'years': yrs, 'mean': mean, 'sd': sd, 'z': z, 'percentile': pct,
                                          'verdict': verdict(z), 'from': next((d for d, _ in hist if d[:7] >= start), None),
                                          'months': len(vals)}
        out['series'][key] = {'name': meta['name'], 'short': meta['short'], 'current': cur, 'date': cur_d,
                              'history': hist, 'windows': windows, 'first': hist[0][0] if hist else None}
    # earnings yield vs the 10-year Treasury
    tnx = ((quotes or {}).get('^TNX') or {}).get('regularMarketPrice')
    sp = out['series'].get('sp500_pe')
    if sp and tnx:
        ey = 100.0 / sp['current']
        out['yield_gap'] = {'earnings_yield': ey, 'treasury': tnx, 'gap': ey - tnx}
    # the headline: average of all three readings against their own last 20 years
    zs = [out['series'][k]['windows']['20']['z'] for k in ('sp500_pe', 'ndx_pe', 'cape')
          if k in out['series'] and out['series'][k]['windows']['20']['z'] is not None]
    z = sum(zs) / len(zs) if zs else None
    lean = None
    if z is not None and -1 <= z <= 1:
        lean = 'leaning expensive' if z > 0.5 else 'leaning cheap' if z < -0.5 else None
    out['overall'] = {'z': z, 'verdict': verdict(z) if zs else None, 'lean': lean, 'count': len(zs)}
    return out
