"""M2 money supply, the yardstick for returns here: beating M2's growth is good, anything under it loses to new money
(bonds and Treasuries, mostly), and borrowing at a rate under it is good.

Monthly, seasonally adjusted, from the Federal Reserve's own H.6 data download (FRED times out from this server),
refreshed daily. Each month's average sits on the 15th and the days between are filled geometrically. Days after the
last published month carry on at the last 12 months' pace until the Fed publishes the next one (about four weeks late).
"""
import csv
import io
import math
import re
import urllib.request
from bisect import bisect_right
from datetime import date

URL = ('https://www.federalreserve.gov/datadownload/Output.aspx?rel=H6&series=798e2796917702a5f8423426ba7e6b42'
       '&lastobs=&from=&to=&filetype=csv&label=include&layout=seriescolumn')
SERIES = 'H6/H6_M2/M2.M'          # M2, seasonally adjusted, billions of dollars


def fetch():
    raw = urllib.request.urlopen(urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'}), timeout=40).read()
    rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig', 'replace'))))
    ident = next(r for r in rows if r and r[0].strip().startswith('Unique Identifier'))
    col = [c.strip() for c in ident].index(SERIES)
    start = next(i for i, r in enumerate(rows) if r and r[0].strip() == 'Time Period') + 1
    out = {}
    for r in rows[start:]:
        if len(r) > col and re.match(r'\d{4}-\d{2}$', r[0].strip()):
            try:
                out[r[0].strip()] = float(r[col])
            except ValueError:
                pass
    if len(out) < 120:
        raise ValueError('M2: the download had no data')
    return out


def ensure(db):
    db.x('CREATE TABLE IF NOT EXISTS m2(month TEXT PRIMARY KEY, value REAL NOT NULL)')


def refresh(db):
    ensure(db)
    data = fetch()
    with db.lock:
        db.c.executemany('INSERT INTO m2(month, value) VALUES(?,?) ON CONFLICT(month) DO UPDATE SET value=excluded.value',
                         sorted(data.items()))
    return len(data)


class M2:
    def __init__(self, db):
        try:
            rows = db.q('SELECT month, value FROM m2 ORDER BY month')
        except Exception:
            rows = []
        self.months = [r['month'] for r in rows]
        self.values = [r['value'] for r in rows]
        self.t = [date(int(m[:4]), int(m[5:7]), 15).toordinal() for m in self.months]
        self.lv = [math.log(v) for v in self.values]
        self.ready = len(rows) >= 13
        if self.ready:
            self.yoy = self.values[-1] / self.values[-13] - 1
            self.rate = math.log(1 + self.yoy) / 365.25          # per day, for days after the last month

    def level(self, d):
        """M2 on a day (billions), interpolated between monthly averages, estimated past the last month."""
        t = (date.fromisoformat(d) if isinstance(d, str) else d).toordinal()
        if t >= self.t[-1]:
            return math.exp(self.lv[-1] + self.rate * (t - self.t[-1]))
        if t <= self.t[0]:
            return self.values[0]
        i = bisect_right(self.t, t) - 1
        f = (t - self.t[i]) / (self.t[i + 1] - self.t[i])
        return math.exp(self.lv[i] + f * (self.lv[i + 1] - self.lv[i]))

    def growth(self, d0, d1):
        return self.level(d1) / self.level(d0) - 1

    def annual(self, d0, d1):
        """M2's growth from d0 to d1 as a yearly rate."""
        days = (date.fromisoformat(d1) - date.fromisoformat(d0)).days
        return (1 + self.growth(d0, d1)) ** (365.25 / days) - 1 if days > 0 else None

    def info(self):
        if not self.ready:
            return None
        n = len(self.values)
        cagr = lambda years: (self.values[-1] / self.values[-1 - 12 * years]) ** (1 / years) - 1 if n > 12 * years else None
        three = (self.values[-1] / self.values[-4]) ** 4 - 1 if n > 4 else None
        return {'month': self.months[-1], 'value': self.values[-1] * 1e9, 'yoy': self.yoy, 'three_month': three,
                'cagr5': cagr(5), 'cagr10': cagr(10), 'cagr20': cagr(20),
                'history': {'months': self.months[-121:], 'values': self.values[-121:]},
                'note': f'M2 (seasonally adjusted) from the Federal Reserve\'s H.6 release, through {self.months[-1]}. '
                        f'Days since then are estimated at the last 12 months\' pace ({self.yoy * 100:.2f}% a year).'}
