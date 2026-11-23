"""Small helpers shared by the importers and the engine."""
import re
from datetime import date, datetime

_MONEY = re.compile(r'^\(?-?\$?[\d,]*\.?\d*\)?$')


def money(s):
    """'$1,998.75' -> 1998.75, '($4.92)' -> -4.92, '' -> None."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    neg = s.startswith('(') and s.endswith(')') or s.startswith('-')
    v = re.sub(r'[^\d.]', '', s)
    if not v or v == '.':
        return None
    v = float(v)
    return -v if neg else v


def num(s):
    """Quantity: '3.3333' -> 3.3333, '100S' -> 100.0 (the S flag is read separately)."""
    if s is None:
        return None
    s = re.sub(r'[^\d.\-]', '', str(s))
    return float(s) if s not in ('', '.', '-') else None


def mdy(s):
    """'11/13/2026' -> '2026-11-13'."""
    s = (s or '').strip()
    if not s:
        return None
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def iso(d):
    return d.isoformat() if isinstance(d, (date, datetime)) else d


OPT_RE = re.compile(r'^(?:Option Expiration for )?([A-Z][A-Z0-9.]*) (\d{1,2}/\d{1,2}/\d{4}) (Call|Put) \$([\d,]+(?:\.\d+)?)')


def parse_option(desc):
    """'MSTR 11/13/2026 Call $160.00' -> dict(underlying, expiry, right, strike, key)."""
    m = OPT_RE.match((desc or '').strip())
    if not m:
        return None
    und, exp, right, strike = m.group(1), mdy(m.group(2)), m.group(3)[0], float(m.group(4).replace(',', ''))
    return {'underlying': und, 'expiry': exp, 'right': right, 'strike': strike,
            'key': f'{und} {exp} {right} {strike:g}'}


def occ_symbol(opt):
    """Yahoo/OCC contract symbol, e.g. MSTR261120C00160000. Adjusted contracts (BYND1) keep their root."""
    y, m, d = opt['expiry'].split('-')
    return f"{opt['underlying']}{y[2:]}{m}{d}{opt['right']}{int(round(opt['strike'] * 1000)):08d}"
