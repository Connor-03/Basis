"""Robinhood account activity CSV (Account > Reports and statements > Reports).

Columns: Activity Date, Process Date, Settle Date, Instrument, Description, Trans Code, Quantity, Price, Amount.
The file has no transaction ids, so a row's identity is a hash of all nine fields plus how many identical rows came
before it in the same file (two identical 1-share buys on one day are two rows).

normalize() turns a stored row into one event the engine understands. Codes it does not know become kind 'other'
with their cash amount kept, and the import report lists them so nothing is dropped silently.
"""
import csv
import hashlib
import io
import re

from .util import money, num, mdy, parse_option

FIELDS = ['Activity Date', 'Process Date', 'Settle Date', 'Instrument', 'Description', 'Trans Code', 'Quantity',
          'Price', 'Amount']
BROKER = 'robinhood'

# code -> (kind, sub). Trades and option events are handled separately below.
CASH_CODES = {
    'CDIV': ('income', 'dividend'),
    'MDIV': ('income', 'dividend_in_lieu'),   # paid while the shares were lent out (taxed as ordinary income)
    'ROC': ('income', 'return_of_capital'),
    'INT': ('income', 'interest'),
    'ABIP': ('income', 'bonus'),              # ACAT-in bonus interest
    'SLIP': ('income', 'stock_lending'),
    'GDBP': ('income', 'bonus'),              # Gold deposit boost
    'MINT': ('expense', 'margin_interest'),
    'GOLD': ('expense', 'subscription'),
    'DTAX': ('expense', 'tax_withheld'),
    'AFEE': ('expense', 'fee'),
    'DFEE': ('expense', 'fee'),
    'ACH': ('flow', 'bank'),
    'RTP': ('flow', 'bank'),
    'ITRF': ('flow', 'internal_transfer'),
    'FUTSWP': ('flow', 'event_contracts'),
    'XENT': ('flow', 'internal_transfer'),
    'CIL': ('income', 'cash_in_lieu'),
}


def detect(header):
    h = [c.strip() for c in header]
    return h[:9] == FIELDS


def read(data):
    """bytes -> list of row dicts with 'fp' and 'occ'. Raises ValueError if this is not a Robinhood export."""
    text = data.decode('utf-8-sig', 'replace')
    rd = csv.reader(io.StringIO(text, newline=''))
    try:
        header = next(rd)
    except StopIteration:
        raise ValueError('empty file')
    if not detect(header):
        raise ValueError('not a Robinhood activity export (header: ' + ', '.join(header[:9]) + ')')
    rows, seen = [], {}
    for rec in rd:
        if len(rec) < 9 or not any(c.strip() for c in rec[:9]):
            continue                                  # blank lines and the closing disclaimer row
        if not mdy(rec[0]):
            continue
        r = dict(zip(FIELDS, (c.strip() for c in rec[:9])))
        fp = hashlib.sha1('\x1f'.join(r[f] for f in FIELDS).encode()).hexdigest()
        seen[fp] = seen.get(fp, 0) + 1
        r['fp'], r['occ'] = fp, seen[fp]
        rows.append(r)
    return rows


_DIV_RE = re.compile(r'R/D (\d{4}-\d{2}-\d{2}) P/D (\d{4}-\d{2}-\d{2}) - ([\d.]+) shares at ([\d.]+)')


def normalize(r):
    """Stored row -> event dict (or None for rows that carry nothing)."""
    code = (r.get('Trans Code') or '').strip()
    desc = r.get('Description') or ''
    first = desc.split('\n', 1)[0].strip()
    sym = (r.get('Instrument') or '').strip().upper()
    qty_raw = (r.get('Quantity') or '').strip()
    ev = {
        'date': mdy(r['Activity Date']), 'settle': mdy(r.get('Settle Date')), 'code': code,
        'symbol': sym, 'qty': num(qty_raw), 'price': money(r.get('Price')), 'amount': money(r.get('Amount')) or 0.0,
        'desc': ' / '.join(x.strip() for x in desc.split('\n') if x.strip()),
        'kind': 'other', 'sub': code.lower(),
    }
    removed = qty_raw.upper().endswith('S')       # SPR / OCA rows: "100S" = shares or contracts taken away

    if code in ('Buy', 'Sell'):
        ev['kind'] = 'trade'
        ev['side'] = 'buy' if code == 'Buy' else 'sell'
        cusip = re.search(r'CUSIP: (\w+)', desc)
        ev['cusip'] = cusip.group(1) if cusip else None
        ev['name'] = first
        low = desc.lower()
        ev['sub'] = 'drip' if 'dividend reinvestment' in low else 'assignment' if 'option assigned' in low else \
            'exercise' if 'option exercised' in low else 'trade'
        return ev

    if code in ('BTO', 'STO', 'BTC', 'STC', 'OEXP', 'OASGN', 'OEXCS', 'OCA'):
        opt = parse_option(first)
        if not opt:
            ev['note'] = 'option row without a readable contract'
            return ev
        ev['option'] = opt
        if code == 'OCA':
            ev['kind'] = 'option_ca'
            ev['removed'] = removed
            return ev
        ev['kind'] = 'option_trade' if code in ('BTO', 'STO', 'BTC', 'STC') else 'option_close'
        ev['side'] = {'BTO': 'buy', 'BTC': 'buy', 'STO': 'sell', 'STC': 'sell'}.get(code)
        ev['sub'] = {'OEXP': 'expired', 'OASGN': 'assigned', 'OEXCS': 'exercised'}.get(code, code.lower())
        return ev

    if code == 'SPR':
        ev['kind'] = 'split'
        ev['removed'] = removed
        cusip = re.search(r'CUSIP: (\w+)', desc)
        ev['cusip'] = cusip.group(1) if cusip else None
        return ev

    if code in ('ACATI', 'ACATO'):
        sign = 1 if code == 'ACATI' else -1
        if sym and ev['qty']:
            ev['kind'] = 'transfer_sec'
            ev['qty'] = sign * ev['qty']
            ev['name'] = first
            ev['sub'] = 'acat_in' if sign > 0 else 'acat_out'
        else:
            ev['kind'] = 'flow'
            ev['sub'] = 'acat_cash'
        m = re.search(r'acct_num = (\w+)', desc)
        if m:
            ev['from_account'] = m.group(1)
        return ev

    if code == 'REC':
        # shares received with no cash leg (stock rewards and similar)
        ev['kind'] = 'transfer_sec'
        ev['sub'] = 'received'
        ev['name'] = first
        return ev

    if code in CASH_CODES:
        ev['kind'], ev['sub'] = CASH_CODES[code]
        if code == 'RTP' and 'fee' in desc.lower():
            ev['kind'], ev['sub'] = 'expense', 'fee'
        m = _DIV_RE.search(desc)
        if m:
            ev['record_date'], ev['pay_date'] = m.group(1), m.group(2)
            ev['div_shares'], ev['div_rate'] = float(m.group(3)), float(m.group(4))
        return ev

    if not code and not ev['amount']:
        return None
    ev['note'] = f'unrecognized code {code!r}'
    return ev
