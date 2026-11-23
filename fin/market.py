"""Free market data: Yahoo Finance (quotes, daily history, splits, dividends, sector and ETF sector weights) and
Nasdaq's public dividend API (declared payments with pay dates). No keys.

Yahoo answers 429 to long browser user agents coming from a non-browser client, so the plain 'Mozilla/5.0' agent is
used on purpose. Quotes and quoteSummary need a cookie + crumb, fetched once and refreshed on 401.
"""
import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

UA = {'User-Agent': 'Mozilla/5.0'}
NASDAQ = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json, text/plain, */*',
          'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/'}


class Yahoo:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.crumb = None
        self.lock = threading.Lock()
        self.last = 0.0

    def _get(self, url, headers=UA, timeout=20):
        with self.lock:                                  # at most ~4 requests a second, politely
            wait = 0.25 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
        r = self.op.open(urllib.request.Request(url, headers=headers), timeout=timeout)
        return json.loads(r.read())

    def _crumb(self, force=False):
        if self.crumb and not force:
            return self.crumb
        try:
            self.op.open(urllib.request.Request('https://fc.yahoo.com', headers=UA), timeout=15)
        except Exception:
            pass                                          # 404 is normal; it still sets the cookie
        r = self.op.open(urllib.request.Request('https://query2.finance.yahoo.com/v1/test/getcrumb', headers=UA),
                         timeout=15)
        c = r.read().decode().strip()
        if not c or ' ' in c or '<' in c:
            raise RuntimeError('no crumb: ' + c[:40])
        self.crumb = c
        return c

    def _with_crumb(self, url):
        for attempt in range(2):
            c = self._crumb(force=attempt > 0)
            try:
                return self._get(url + ('&' if '?' in url else '?') + 'crumb=' + urllib.parse.quote(c))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 0:
                    continue
                raise

    def quotes(self, symbols):
        """symbols -> {sym: quote dict}. Works for stocks, ETFs, OCC option symbols, BTC-USD, ^IRX."""
        out = {}
        syms = [s for s in dict.fromkeys(symbols) if s]
        for i in range(0, len(syms), 40):
            batch = ','.join(urllib.parse.quote(s) for s in syms[i:i + 40])
            data = self._with_crumb('https://query1.finance.yahoo.com/v7/finance/quote?symbols=' + batch)
            for q in (data.get('quoteResponse') or {}).get('result') or []:
                out[q['symbol']] = {k: q.get(k) for k in (
                    'symbol', 'quoteType', 'shortName', 'longName', 'regularMarketPrice', 'regularMarketChange',
                    'regularMarketChangePercent', 'regularMarketPreviousClose', 'regularMarketTime', 'marketState',
                    'preMarketPrice', 'preMarketChangePercent', 'preMarketTime', 'postMarketPrice', 'postMarketChangePercent',
                    'postMarketTime',
                    'trailingAnnualDividendRate', 'dividendRate', 'dividendYield', 'exDividendDate', 'dividendDate',
                    'fiftyTwoWeekHigh', 'fiftyTwoWeekLow', 'regularMarketDayHigh', 'marketCap', 'bid', 'ask', 'currency',
                    'exchangeDataDelayedBy', 'quoteSourceName', 'earningsTimestamp')}
        return out

    def history(self, symbol, start):
        """Daily bars from start (iso) to now: {'bars': [(d, close, adj)], 'splits': [(d, ratio)],
        'divs': [(ex_date, amount)], 'type': instrumentType}. close is split-adjusted, adj is also dividend-adjusted."""
        p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
        p2 = int(time.time()) + 86400
        url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}'
               f'?period1={p1}&period2={p2}&interval=1d&events=div%7Csplit&includeAdjustedClose=true')
        data = self._get(url)
        res = ((data.get('chart') or {}).get('result') or [None])[0]
        if not res:
            return {'bars': [], 'splits': [], 'divs': [], 'type': None}
        meta = res.get('meta') or {}
        tz = meta.get('gmtoffset') or 0
        ts = res.get('timestamp') or []
        ind = res.get('indicators') or {}
        close = ((ind.get('quote') or [{}])[0]).get('close') or []
        adj = ((ind.get('adjclose') or [{}])[0]).get('adjclose') or close
        bars = []
        for i, t in enumerate(ts):
            c = close[i] if i < len(close) else None
            if c is None:
                continue
            d = datetime.fromtimestamp(t + tz, timezone.utc).date().isoformat()
            a = adj[i] if i < len(adj) and adj[i] is not None else c
            if bars and bars[-1][0] == d:
                bars[-1] = (d, c, a)
            else:
                bars.append((d, c, a))
        ev = res.get('events') or {}
        splits = sorted((datetime.fromtimestamp(int(k) + tz, timezone.utc).date().isoformat(),
                         (v.get('numerator') or 1) / (v.get('denominator') or 1))
                        for k, v in (ev.get('splits') or {}).items())
        divs = sorted((datetime.fromtimestamp(int(k) + tz, timezone.utc).date().isoformat(), v.get('amount') or 0)
                      for k, v in (ev.get('dividends') or {}).items())
        return {'bars': bars, 'splits': splits, 'divs': divs, 'type': meta.get('instrumentType'),
                'name': meta.get('longName') or meta.get('shortName')}

    def ath(self, symbol):
        """All-time high (intraday, split-adjusted like the rest of Yahoo's bars) and the day it was set: the highest
        bar over the whole history (Yahoo answers range=max with monthly or, for long histories, 3-month bars), then
        that bar's daily bars for the date. None if Yahoo has no bars."""
        base = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}'
        res = ((self._get(base + '?range=max&interval=1mo').get('chart') or {}).get('result') or [None])[0]
        if not res:
            return None
        ts = res.get('timestamp') or []
        highs = ((res.get('indicators') or {}).get('quote') or [{}])[0].get('high') or []
        best = max(((hi, i) for i, hi in enumerate(highs[:len(ts)]) if hi), default=None)
        if not best:
            return None
        tz = (res.get('meta') or {}).get('gmtoffset') or 0
        high, i = best
        start, end = ts[i], ts[i + 1] if i + 1 < len(ts) else int(time.time())
        when = start + 6 * 3600                           # bars start at midnight Eastern; keep the date safe from DST
        try:
            r2 = ((self._get(f'{base}?period1={start - 3 * 86400}&period2={end + 3 * 86400}&interval=1d')
                   .get('chart') or {}).get('result') or [None])[0] or {}
            dh = ((r2.get('indicators') or {}).get('quote') or [{}])[0].get('high') or []
            day = max(((hi, t) for hi, t in zip(dh, r2.get('timestamp') or []) if hi), default=None)
            if day and abs(day[0] - high) <= 0.02 * high:
                high, when = max(high, day[0]), day[1]
        except Exception:
            pass                                          # keep the bar's first day
        return {'high': high, 'd': datetime.fromtimestamp(when + tz, timezone.utc).date().isoformat()}

    def options(self, symbol, expiry_epoch=None):
        """Option chain: {'expirations': [epoch], 'calls': [...], 'puts': [...], 'quote': {...}} for one expiry."""
        url = f'https://query2.finance.yahoo.com/v7/finance/options/{urllib.parse.quote(symbol)}'
        if expiry_epoch:
            url += f'?date={int(expiry_epoch)}'
        data = self._with_crumb(url)
        res = ((data.get('optionChain') or {}).get('result') or [None])[0] or {}
        ch = (res.get('options') or [{}])[0] if res.get('options') else {}
        keep = ('contractSymbol', 'strike', 'lastPrice', 'bid', 'ask', 'impliedVolatility', 'openInterest', 'volume',
                'inTheMoney', 'expiration', 'lastTradeDate')
        return {'expirations': res.get('expirationDates') or [], 'quote': res.get('quote') or {},
                'expiration': ch.get('expirationDate'),
                'calls': [{k: c.get(k) for k in keep} for c in ch.get('calls') or []],
                'puts': [{k: c.get(k) for k in keep} for c in ch.get('puts') or []]}

    def profile(self, symbol):
        """Sector / industry for stocks, sector weights and category for funds."""
        mods = 'assetProfile,topHoldings,fundProfile,price,summaryDetail'
        data = self._with_crumb(f'https://query2.finance.yahoo.com/v10/finance/quoteSummary/'
                                f'{urllib.parse.quote(symbol)}?modules={mods}')
        res = ((data.get('quoteSummary') or {}).get('result') or [None])[0] or {}
        ap, th, fp, pr, sd = (res.get(k) or {} for k in ('assetProfile', 'topHoldings', 'fundProfile', 'price',
                                                          'summaryDetail'))
        weights = {}
        for w in th.get('sectorWeightings') or []:
            for k, v in w.items():
                raw = v.get('raw') if isinstance(v, dict) else v
                if raw:
                    weights[k] = raw
        raw = lambda d, k: (d.get(k) or {}).get('raw') if isinstance(d.get(k), dict) else d.get(k)
        return {'symbol': symbol, 'type': pr.get('quoteType'), 'name': pr.get('longName') or pr.get('shortName'),
                'sector': ap.get('sectorKey') or ap.get('sector'), 'sector_name': ap.get('sectorDisp') or ap.get('sector'),
                'industry': ap.get('industryDisp') or ap.get('industry'), 'country': ap.get('country'),
                'category': fp.get('categoryName'), 'family': fp.get('family'), 'sector_weights': weights,
                'beta': raw(sd, 'beta'), 'expense_ratio': raw(fp.get('feesExpensesInvestment') or {}, 'annualReportExpenseRatio'),
                'stock_position': raw(th, 'stockPosition'), 'bond_position': raw(th, 'bondPosition'),
                'cash_position': raw(th, 'cashPosition'),
                'summary': (ap.get('longBusinessSummary') or '')[:1500]}


def nasdaq_dividends(symbol, etf=False):
    """Declared and past cash dividends with pay dates: [{'ex', 'pay', 'record', 'declared', 'amount'}]."""
    sym = symbol.replace('-', '.')
    out = []
    for ac in (['etf', 'stocks'] if etf else ['stocks', 'etf']):
        url = f'https://api.nasdaq.com/api/quote/{urllib.parse.quote(sym)}/dividends?assetclass={ac}'
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=NASDAQ), timeout=20)
            data = (json.loads(r.read()) or {}).get('data') or {}
        except Exception:
            continue
        rows = ((data.get('dividends') or {}).get('rows')) or []
        for row in rows:
            amt = row.get('amount') or ''
            try:
                a = float(amt.replace('$', '').replace(',', ''))
            except ValueError:
                continue
            out.append({'ex': _mdy(row.get('exOrEffDate')), 'pay': _mdy(row.get('paymentDate')),
                        'record': _mdy(row.get('recordDate')), 'declared': _mdy(row.get('declarationDate')),
                        'amount': a, 'type': row.get('type')})
        if out:
            break
    return [x for x in out if x['ex']]


def _mdy(s):
    try:
        return datetime.strptime(s, '%m/%d/%Y').date().isoformat()
    except (TypeError, ValueError):
        return None


# ---- US market clock -----------------------------------------------------------------------------------------------
def eastern_now():
    """Current New York time without zoneinfo data files: DST second Sunday of March to first Sunday of November."""
    utc = datetime.now(timezone.utc)
    y = utc.year
    mar = date(y, 3, 8 + (6 - date(y, 3, 8).weekday()) % 7)
    nov = date(y, 11, 1 + (6 - date(y, 11, 1).weekday()) % 7)
    start = datetime(y, mar.month, mar.day, 7, tzinfo=timezone.utc)
    end = datetime(y, nov.month, nov.day, 6, tzinfo=timezone.utc)
    off = -4 if start <= utc < end else -5
    return (utc + timedelta(hours=off)).replace(tzinfo=None)


def session_now():
    """'regular', 'pre', 'post' or 'closed' by the clock (holidays come from Yahoo's marketState when known)."""
    n = eastern_now()
    if n.weekday() >= 5:
        return 'closed'
    m = n.hour * 60 + n.minute
    if 570 <= m < 960:
        return 'regular'
    if 240 <= m < 570:
        return 'pre'
    if 960 <= m < 1200:
        return 'post'
    return 'closed'


def market_holidays(year):
    """Days the New York Stock Exchange is closed in a year (the regular ones; one-off closures are not known here)."""
    from datetime import date as _date, timedelta as _td

    def nth(month, weekday, n):                      # the n-th given weekday of a month (n=-1: the last)
        if n > 0:
            d = _date(year, month, 1)
            d += _td(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
        else:
            d = _date(year + (month == 12), month % 12 + 1, 1) - _td(days=1)
            d -= _td(days=(d.weekday() - weekday) % 7)
        return d

    def observed(d):                                 # Saturday holidays are kept on Friday, Sunday ones on Monday
        return d - _td(days=1) if d.weekday() == 5 else d + _td(days=1) if d.weekday() == 6 else d

    a, b, c = year % 19, year // 100, year % 100     # Easter (Gregorian), for Good Friday
    h = (19 * a + b - b // 4 - (b - (b + 8) // 25 + 1) // 3 + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * (b % 4) + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    easter = _date(year, (h + el - 7 * m + 114) // 31, (h + el - 7 * m + 114) % 31 + 1)
    out = {nth(1, 0, 3), nth(2, 0, 3), easter - _td(days=2), nth(5, 0, -1), observed(_date(year, 7, 4)), nth(9, 0, 1),
           nth(11, 3, 4), observed(_date(year, 12, 25))}
    if _date(year, 1, 1).weekday() != 5:             # a Saturday New Year's Day is not made up on the Friday before
        out.add(observed(_date(year, 1, 1)))
    if year >= 2022:
        out.add(observed(_date(year, 6, 19)))
    return out
