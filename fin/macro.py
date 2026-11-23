"""Market-moving dates the options menu waits on. FOMC meetings come from the Federal Reserve's own calendar page
(refreshed weekly). BLS refuses automated requests, so CPI and jobs dates are added by hand in Settings."""
import re
import urllib.request
from datetime import date

FED = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
MONTHS = {m: i for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                                       'September', 'October', 'November', 'December'], 1)}


def fomc_dates():
    """[{'d': 'YYYY-MM-DD', 'label': 'FOMC decision'} ...] for both meeting days; decision is the last day."""
    html = urllib.request.urlopen(urllib.request.Request(FED, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30).read().decode('utf-8', 'replace')
    parts = re.split(r'(\d{4}) FOMC Meetings', html)
    out = []
    for i in range(1, len(parts) - 1, 2):
        year, body = int(parts[i]), parts[i + 1]
        for mon, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', body, re.S):
            names = [m for m in re.split(r'[/\-]', mon.strip()) if m in MONTHS]
            nums = [int(x) for x in re.findall(r'\d+', days)]
            if not names or not nums:
                continue
            m1, m2 = MONTHS[names[0]], MONTHS[names[-1]]
            first, last = nums[0], nums[-1]
            try:
                d1 = date(year, m1, first)
                d2 = date(year, m2 if last < first else m1, last)
            except ValueError:
                continue
            if d2 != d1:
                out.append({'d': d1.isoformat(), 'label': 'FOMC meeting'})
            out.append({'d': d2.isoformat(), 'label': 'FOMC decision'})
    return sorted({x['d']: x for x in out}.values(), key=lambda x: x['d'])
