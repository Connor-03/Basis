"""SnapTrade (free Personal plan): read-only links to your brokerages through each broker's own login.

The Personal key (client id + consumer key from the SnapTrade dashboard) lives in snaptrade.json in the data folder
(owner only; on Windows the consumer key is also encrypted for the Windows account) and never goes back to the page. A Personal key needs no SnapTrade user: requests carry only clientId and timestamp. Every
request is signed: HMAC-SHA256 with the consumer key over the JSON {content, path, query} (keys sorted, no spaces;
path without the query, query exactly as sent, content null without a body), base64, in the Signature header.
Verified against SnapTrade's SDK source and docs (request-signatures, personal-vs-commercial) on 2026-11-19.
Limits: 250 requests a minute, maybe 10 a minute per account; activities refresh once a day, balances and positions
are live (poll at most every 10 minutes).

sync() pulls accounts, balances, positions, option positions and activities:
- activities become rows in raw_rows (fingerprint 'snap:<id>'), normalized by normalize() below. For an account that
  also has uploaded CSV history, the CSV wins up to its last date and SnapTrade fills in after it.
- balances become a confirmed cash balance for the day, so cash lines up with the broker on every sync.
- positions are kept as a snapshot and compared with the console's own share counts (Settings shows differences).
"""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta

from . import paths, secret

BASE = os.environ.get('SNAPTRADE_BASE', 'https://api.snaptrade.com')
PREFIX = ''
DATA = paths.DATA
CONF = os.path.join(DATA, 'snaptrade.json')


class SnapError(Exception):
    pass


def load():
    try:
        with open(CONF, encoding='utf-8') as f:
            conf = json.load(f)
        if conf.get('consumer_key'):
            conf['consumer_key'] = secret.reveal(conf['consumer_key'])
        return conf
    except (OSError, ValueError):
        return {}


def save(conf):
    """Owner-only file; on Windows the consumer key is also encrypted for this Windows account (fin/secret.py)."""
    out = dict(conf)
    if out.get('consumer_key'):
        out['consumer_key'] = secret.protect(out['consumer_key'])
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    fd = os.open(CONF + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(out, f)
    os.replace(CONF + '.tmp', CONF)
    os.chmod(CONF, 0o600)


def public_state():
    c = load()
    return {'has_keys': bool(c.get('client_id') and c.get('consumer_key')), 'client_id': c.get('client_id'),
            'key_tail': (c.get('consumer_key') or '')[-4:], 'registered': bool(c.get('client_id') and c.get('consumer_key')),
            'last_sync': c.get('last_sync'), 'last_error': c.get('last_error')}


class Client:
    def __init__(self, conf=None):
        self.c = conf or load()
        if not (self.c.get('client_id') and self.c.get('consumer_key')):
            raise SnapError('Add your SnapTrade client id and consumer key first.')

    def _sign(self, path, query, body):
        content = json.loads(body) if body else None
        sig = json.dumps({'content': content, 'path': path, 'query': query}, separators=(',', ':'), sort_keys=True)
        mac = hmac.new(self.c['consumer_key'].encode(), sig.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    def request(self, method, path, params=None, body=None, _retry=True):
        q = {'clientId': self.c['client_id'], 'timestamp': str(int(time.time()))}
        for k, v in (params or {}).items():
            if v is not None:
                q[k] = str(v)
        query = urllib.parse.urlencode(q)            # signed exactly as sent
        full = PREFIX + path
        data = json.dumps(body, separators=(',', ':')) if body else None
        headers = {'Signature': self._sign(full, query, data), 'Accept': 'application/json', 'User-Agent': 'basis'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(BASE + full + '?' + query, data=data.encode() if data else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 429 and _retry:
                wait = min(70, int(e.headers.get('Retry-After') or e.headers.get('X-RateLimit-Account-Reset') or 30))
                time.sleep(max(wait, 1))
                return self.request(method, path, params, body, _retry=False)
            msg = e.read(500).decode('utf-8', 'replace')
            try:
                j = json.loads(msg)
                msg = j.get('detail') or j.get('message') or msg
            except ValueError:
                pass
            raise SnapError(f'SnapTrade {e.code}: {str(msg)[:300]}')
        except urllib.error.URLError as e:
            raise SnapError(f'SnapTrade unreachable: {e.reason}')

    # ---- connections ----------------------------------------------------------------------------------------------
    def ensure_user(self):
        """A Personal key has its user built in; nothing to register."""

    def login_url(self, broker=None, reconnect=None):
        body = {'connectionType': 'read', 'darkMode': True}
        if broker:
            body['broker'] = broker
            body['immediateRedirect'] = True
        if reconnect:
            body['reconnect'] = reconnect
        res = self.request('POST', '/snapTrade/login', body=body) or {}
        url = res.get('redirectURI')
        if not url:
            raise SnapError('SnapTrade did not return a connection link.')
        return url

    def connections(self):
        return self.request('GET', '/authorizations') or []

    def delete_connection(self, connection_id):
        return self.request('DELETE', f'/connection/{urllib.parse.quote(connection_id)}')

    def brokerages(self):
        return self.request('GET', '/brokerages') or []

    # ---- account data --------------------------------------------------------------------------------------------
    def accounts(self):
        res = self.request('GET', '/accounts/all') or {}
        return [a for a in (res.get('results') if isinstance(res, dict) else res) or [] if a.get('kind', 'investment') == 'investment']

    def balances(self, acct):
        return self.request('GET', f'/accounts/{acct}/balances') or []

    def positions(self, acct):
        """Stocks, funds, options and crypto in one list (instrument.kind tells them apart)."""
        res = self.request('GET', f'/accounts/{acct}/positions/all') or {}
        return (res.get('results') if isinstance(res, dict) else res) or []

    def activities(self, acct, start, end):
        """All activities between two dates (trade_date, inclusive), following pagination."""
        out, offset = [], 0
        while True:
            res = self.request('GET', f'/accounts/{acct}/activities',
                               {'startDate': start, 'endDate': end, 'offset': offset, 'limit': 1000}) or {}
            rows = (res.get('data') if isinstance(res, dict) else res) or []
            out += rows
            pag = res.get('pagination') if isinstance(res, dict) else None
            if not pag or not rows or offset + len(rows) >= (pag.get('total') or 0):
                break
            offset += len(rows)
        return out


# ---- normalizing activities into engine events ---------------------------------------------------------------------
def _f(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _d(v):
    return (v or '')[:10] or None


CASH_FUNDS = {'SPAXX', 'FDRXX', 'FZFXX', 'SPRXX', 'FCASH', 'SWVXX', 'SNVXX', 'VMFXX', 'SNSXX', 'FTEXX'}


def is_cash_fund(sym, desc=''):
    """Fidelity's core position and other money market sweeps count as cash, not a holding."""
    d = (desc or '').upper()
    return sym in CASH_FUNDS or 'CORE ACCOUNT' in d or 'MONEY MARKET' in d or 'CASH RESERVES' in d


def _sym(a):
    """Ticker as the console prices it (crypto becomes BTC-USD), plus the name."""
    s = a.get('symbol') or a.get('instrument') or {}
    if not isinstance(s, dict):
        return (str(s) or '').upper(), None
    inner = s.get('symbol')
    if isinstance(inner, dict):
        s, inner = inner, inner.get('symbol')
    sym = (inner or s.get('raw_symbol') or '').upper()
    kind = s.get('kind') or ((s.get('type') or {}).get('code') if isinstance(s.get('type'), dict) else s.get('type')) or ''
    if sym and str(kind).lower() in ('crypto', 'crypto_currency', 'cryptocurrency') and not sym.endswith('-USD'):
        sym += '-USD'
    return sym, s.get('description')


def _opt(a):
    o = a.get('option_symbol')
    if not isinstance(o, dict) or not o:
        return None
    und = o.get('underlying_symbol') or {}
    und = (und.get('symbol') if isinstance(und, dict) else und) or ''
    right = (o.get('option_type') or '').upper()[:1]
    strike = _f(o.get('strike_price'))
    exp = _d(o.get('expiration_date'))
    if not (und and right in ('C', 'P') and strike and exp):
        return None
    return {'underlying': und.upper(), 'expiry': exp, 'right': right, 'strike': strike,
            'key': f'{und.upper()} {exp} {right} {strike:g}'}


TYPES = {
    'DIVIDEND': ('income', 'dividend'), 'STOCK_DIVIDEND': ('income', 'dividend'), 'INTEREST': ('income', 'interest'),
    'SUBSTITUTE_DIVIDEND': ('income', 'dividend_in_lieu'), 'RETURN_OF_CAPITAL': ('income', 'return_of_capital'),
    'FEE': ('expense', 'fee'), 'TAX': ('expense', 'tax_withheld'), 'CONTRIBUTION': ('flow', 'bank'),
    'DEPOSIT': ('flow', 'bank'), 'WITHDRAWAL': ('flow', 'bank'), 'TRANSFER': ('flow', 'transfer'),
}


def _trading_day(d):
    from .market import market_holidays
    return d.weekday() < 5 and d not in market_holidays(d.year)


def fill_date(a):
    """The day a trade happened. Some brokers stamp an order that waited (a limit order left open for days) with the
    day it was placed, not the day it filled. The settlement date still shows the truth: stocks and options settle one
    business day after the trade (two before May 28, 2024). When that points two or more business days past the
    stamped date, the trade is dated from its settlement. A single day's difference is left alone: holidays and
    after-hours trades explain those."""
    t, s = _d(a.get('trade_date')), _d(a.get('settlement_date'))
    if not t or not s or (a.get('type') or '').upper() not in ('BUY', 'SELL'):
        return t or s
    try:
        td, d = date.fromisoformat(t), date.fromisoformat(s)
    except ValueError:
        return t
    for _ in range(1 if s >= '2024-05-28' else 2):          # back over the settlement lag, weekends skipped
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    gap, x = 0, td
    while x < d and gap < 2:                                 # business days from the stamped date to that day
        x += timedelta(days=1)
        gap += x.weekday() < 5
    if gap < 2:
        return t
    while not _trading_day(d) and d > td:                    # the market was shut that day: the day before
        d -= timedelta(days=1)
    return d.isoformat()


def normalize(a):
    t = (a.get('type') or '').upper()
    d = fill_date(a)
    if not d:
        return None
    sym, name = _sym(a)
    desc = a.get('description') or t.title()
    units, price = _f(a.get('units')), _f(a.get('price'))
    fee = abs(_f(a.get('fee')) or 0.0)
    amount = (_f(a.get('amount')) or 0.0) - fee            # the cash that actually moved
    opt = _opt(a)
    ev = {'date': d, 'settle': _d(a.get('settlement_date')) or d, 'ts': a.get('trade_date') if _d(a.get('trade_date')) == d else d, 'code': t,
          'symbol': (opt or {}).get('underlying') or sym, 'qty': abs(units) if units is not None else None,
          'price': price, 'amount': amount, 'desc': desc, 'kind': 'other', 'sub': t.lower(), 'name': name or ''}
    if t in ('BUY', 'SELL', 'REI'):
        if opt:
            low = desc.lower()
            ot = (a.get('option_type') or '').upper()
            if 'to open' in low or ot.endswith('_TO_OPEN'):
                code = 'BTO' if t == 'BUY' else 'STO'
            elif 'to close' in low or ot.endswith('_TO_CLOSE'):
                code = 'BTC' if t == 'BUY' else 'STC'
            else:
                code = t
            ev.update(kind='option_trade', option=opt, side='buy' if t == 'BUY' else 'sell', code=code)
            return ev
        if is_cash_fund(sym, desc):
            return None                                       # sweep into or out of the core: cash either way
        ev.update(kind='trade', side='sell' if t == 'SELL' else 'buy', sub='drip' if t == 'REI' else 'trade')
        if not ev['amount'] and units and price:
            ev['amount'] = -(abs(units) * price) if ev['side'] == 'buy' else abs(units) * price
        return ev
    if t in ('OPTIONEXPIRATION', 'OPTIONASSIGNMENT', 'OPTIONEXERCISE') and opt:
        ev.update(kind='option_close', option=opt, sub={'OPTIONEXPIRATION': 'expired', 'OPTIONASSIGNMENT': 'assigned',
                                                        'OPTIONEXERCISE': 'exercised'}[t])
        return ev
    if t in ('EXTERNAL_ASSET_TRANSFER_IN', 'EXTERNAL_ASSET_TRANSFER_OUT'):
        q = abs(units or 0) * (1 if t.endswith('_IN') else -1)
        if sym and q:
            ev.update(kind='transfer_sec', qty=q, sub='acat_in' if q > 0 else 'acat_out')
        else:
            ev.update(kind='flow', sub='transfer')
        return ev
    if t in ('CONTRIBUTION', 'WITHDRAWAL') and sym and units:
        # coins or shares moved in or out (crypto deposits and withdrawals)
        ev.update(kind='transfer_sec', qty=abs(units) * (1 if t == 'CONTRIBUTION' else -1), amount=0.0, sub='transfer')
        return ev
    if t in ('TRANSFER', 'JOURNAL') and sym and units and not is_cash_fund(sym, desc):
        # shares moved between brokers (ACATS) or between accounts, reported as a plain transfer with units
        q = units
        if q > 0 and any(w in desc.lower() for w in ('deliver', 'transfer out', 'outgoing', 'sent to')):
            q = -q
        ev.update(kind='transfer_sec', qty=q, amount=0.0, sub='acat_in' if q > 0 else 'acat_out')
        return ev
    if t == 'SPLIT' and sym and units:
        ev.update(kind='split_units', qty=units, amount=0.0)  # shares added (or removed) by a split, cost unchanged
        return ev
    if t == 'ADJUSTMENT':
        if sym and units and units < 0:
            ev.update(kind='trade', side='sell', sub='reorg')  # merger or fractional payout: shares out for cash
            return ev
        if sym and units and units > 0:
            ev.update(kind='transfer_sec', qty=units, amount=0.0, sub='reorg')
            return ev
        ev.update(kind='income' if amount > 0 else 'expense', sub='cash_in_lieu' if amount > 0 else 'fee')
        return ev
    if t in TYPES:
        ev['kind'], ev['sub'] = TYPES[t]
        low = desc.lower()
        if t == 'FEE' and 'margin' in low:
            ev['sub'] = 'margin_interest'
        if t == 'INTEREST' and 'lending' in low:
            ev['sub'] = 'stock_lending'
        if t == 'DIVIDEND' and is_cash_fund(sym, desc):
            ev['sub'] = 'interest'
        return ev
    ev['note'] = f'unrecognized SnapTrade type {t!r}'
    return ev


# ---- sync ----------------------------------------------------------------------------------------------------------
def _kind(acct):
    t = ' '.join(str(x) for x in [acct.get('display_name'), acct.get('raw_type')] if x).lower()
    if 'crypto' in t or 'digitalasset' in t or acct.get('raw_type') in ('CRYP', 'DIGITALASSET'):
        return 'crypto'
    return 'roth' if 'roth' in t else 'traditional' if 'ira' in t else 'taxable'


def map_account(db, sa, inst):
    """Find or create the console account for a SnapTrade account: an existing account with the same name first
    ("Robinhood Individual"), then one of the same broker and type that is not linked yet, else a new one."""
    row = db.one('SELECT account_id FROM snap_accounts WHERE snap_id=?', (sa['id'],))
    if row and row['account_id']:
        return row['account_id']
    kind = _kind(sa)
    broker = (inst or 'brokerage').lower().split()[0]
    disp = (sa.get('display_name') or '').strip()
    full = disp if disp.lower().startswith((inst or '').lower()) else f"{inst} {disp}".strip()
    linked = {r['account_id'] for r in db.q('SELECT account_id FROM snap_accounts WHERE account_id IS NOT NULL')}
    for a in db.q('SELECT * FROM accounts'):
        if a['id'] not in linked and a['name'].lower() in (full.lower(), disp.lower()):
            return a['id']
    for a in db.q('SELECT * FROM accounts WHERE broker=? AND kind=?', (broker, kind)):
        if a['id'] not in linked:
            return a['id']
    margin = 1 if (sa.get('cash_or_margin') or '').lower() == 'margin' else 0
    return db.x('INSERT INTO accounts(name, broker, kind, margin, sort) VALUES(?,?,?,?,?)', (full[:60], broker, kind, margin, 5))


_SYNC = threading.Lock()          # one sync at a time: the first sync and the background loop can both start one
HISTORY_CHECKED = 'snap_history_checked_2'      # _2 (1.0.3): read again after SnapTrade finished loading


def sync(db, log=print, days_back=None):
    """Read every linked account. Returns {'accounts', 'activities', 'new_rows', 'errors', 'refilled', 'loading',
    'rechecked'}: refilled lists the accounts whose older history was missing and has been read again; loading names
    the accounts SnapTrade is still gathering history for; rechecked is True after a full pass over every account."""
    with _SYNC:
        return _sync(db, log, days_back)


def _sync(db, log, days_back):
    cl = Client()
    cl.ensure_user()
    conf = cl.c
    summary = {'accounts': 0, 'activities': 0, 'new_rows': 0, 'errors': [], 'refilled': [], 'loading': [], 'rechecked': False}
    today = date.today().isoformat()
    # SnapTrade lists activities newest first. Before 1.0.2 a first sync cut short could keep an account's newest rows
    # without the older ones, and every later sync only looked back a week from the newest row, so the gap stayed; and
    # before 1.0.3 a first sync could run while SnapTrade was still gathering a new connection's history. A desktop
    # copy reads every account's whole history once more to close any such gap.
    full = paths.MODE == 'desktop' and not db.setting(HISTORY_CHECKED)
    conns = {c.get('id'): c for c in cl.connections()}
    for sa in cl.accounts():
        conn = conns.get(sa.get('connection_id')) or {}
        inst = ((conn.get('brokerage') or {}).get('display_name') or (conn.get('brokerage') or {}).get('name')
                or sa.get('institution_name') or 'Brokerage')
        aid = map_account(db, sa, inst)
        num = str(sa.get('masked_account_number') or '')
        db.x('INSERT INTO snap_accounts(snap_id, account_id, institution, name, number, kind, authorization_id, meta) '
             'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(snap_id) DO UPDATE SET account_id=COALESCE(snap_accounts.account_id, '
             'excluded.account_id), institution=excluded.institution, name=excluded.name, number=excluded.number, '
             'kind=excluded.kind, authorization_id=excluded.authorization_id, meta=excluded.meta',
             (sa['id'], aid, inst, sa.get('display_name'), num[-8:], _kind(sa), sa.get('connection_id'),
              json.dumps({k: sa.get(k) for k in ('raw_type', 'cash_or_margin', 'status', 'sync_status', 'net_value')})))
        aid = db.one('SELECT account_id FROM snap_accounts WHERE snap_id=?', (sa['id'],))['account_id']
        summary['accounts'] += 1
        # a connection made minutes ago: SnapTrade lists the holdings at once but gathers the history over a while
        loading = (((sa.get('sync_status') or {}).get('transactions') or {}).get('initial_sync_completed') is False)
        if loading:
            summary['loading'].append(f"{inst} {sa.get('display_name') or sa.get('name') or ''}".strip())
        try:
            bal = cl.balances(sa['id'])
            allpos = cl.positions(sa['id'])
            pos = [p for p in allpos if ((p.get('instrument') or {}).get('kind') or '') != 'option']
            opts = [p for p in allpos if ((p.get('instrument') or {}).get('kind') or '') == 'option']
            db.x('INSERT INTO snap_snapshots(account_id, at, positions, options, balances) VALUES(?,?,?,?,?)',
                 (aid, datetime.now().isoformat(timespec='seconds'), json.dumps(pos), json.dumps(opts), json.dumps(bal)))
            cash = sum(_f(b.get('cash')) or 0 for b in bal if ((b.get('currency') or {}).get('code') or 'USD') == 'USD')
            for p in pos:                                     # Fidelity's core money market is cash too
                ps, pdesc = _sym(p)
                if is_cash_fund(ps, pdesc) or p.get('cash_equivalent'):
                    cash += (_f(p.get('units')) or 0) * (_f(p.get('price')) or 1.0)
            if bal:
                # keep only the latest broker balance: transactions arrive a day late, so an older daily balance would
                # count a trade twice once its row shows up
                db.x("DELETE FROM balances WHERE account_id=? AND note LIKE 'SnapTrade%'", (aid,))
                db.x('INSERT INTO balances(account_id, d, kind, value, note) VALUES(?,?,?,?,?)',
                     (aid, today, 'cash', cash, 'SnapTrade sync'))
            # activities: from the newest stored SnapTrade row (minus a week) once a sync of this account has finished,
            # else from the start
            had = db.one("SELECT COUNT(*) AS n, MAX(activity_date) AS d FROM raw_rows WHERE account_id=? AND fp LIKE 'snap:%'", (aid,))
            finished = (db.one('SELECT synced_at FROM snap_accounts WHERE snap_id=?', (sa['id'],)) or {}).get('synced_at')
            if had['d'] and finished and not full and not loading:
                start = (date.fromisoformat(had['d']) - timedelta(days=7)).isoformat()
            else:
                start = (date.today() - timedelta(days=days_back or 3650)).isoformat()
            acts = cl.activities(sa['id'], start, today)
            summary['activities'] += len(acts)
            imp = db.x('INSERT INTO imports(account_id, filename, broker, rows_total, rows_added, first_date, last_date) '
                       'VALUES(?,?,?,?,?,?,?)', (aid, 'SnapTrade sync', 'snaptrade', len(acts), 0, start, today))
            rows = [(aid, imp, i, 'snap:' + str(a['id']), 1, _d(a.get('trade_date')) or _d(a.get('settlement_date')) or today,
                     json.dumps({'_src': 'snaptrade', **a})) for i, a in enumerate(acts) if a.get('id')]
            with db.tx() as c:                  # all of an account's rows in one locked transaction, or none of them
                added = c.executemany('INSERT OR IGNORE INTO raw_rows(account_id, import_id, pos, fp, occ, activity_date, data) '
                                      'VALUES(?,?,?,?,?,?,?)', rows).rowcount
                c.execute('UPDATE imports SET rows_added=? WHERE id=?', (added, imp))
            summary['new_rows'] += added
            if full and had['n'] and added:
                summary['refilled'].append(aid)
            if loading:                         # not finished: the next sync reads its whole history again
                db.x('UPDATE snap_accounts SET synced_at=NULL, last_error=NULL WHERE snap_id=?', (sa['id'],))
            else:
                db.x('UPDATE snap_accounts SET synced_at=?, last_error=NULL WHERE snap_id=?', (datetime.now().isoformat(timespec='seconds'), sa['id']))
        except SnapError as e:
            summary['errors'].append(f"{sa.get('institution_name')} {sa.get('name')}: {e}")
            db.x('UPDATE snap_accounts SET last_error=? WHERE snap_id=?', (str(e)[:300], sa['id']))
    if full and not summary['errors'] and not summary['loading']:
        db.set_setting(HISTORY_CHECKED, datetime.now().isoformat(timespec='seconds'))
        summary['rechecked'] = True
    conf['last_sync'] = datetime.now().isoformat(timespec='seconds')
    conf['last_error'] = '; '.join(summary['errors'])[:300] or None
    save(conf)
    db.log('snaptrade sync', summary)
    return summary


def still_loading(db, account_id):
    """True while SnapTrade says it has not finished gathering this account's history."""
    for r in db.q('SELECT meta FROM snap_accounts WHERE account_id=?', (account_id,)):
        st = ((json.loads(r['meta'] or '{}').get('sync_status') or {}).get('transactions') or {})
        if st.get('initial_sync_completed') is False:
            return True
    return False


def broker_positions(db, account_id):
    """{symbol: {'qty', 'avg_cost'}} from the latest SnapTrade snapshot of an account, or (None, None)."""
    snap = db.one('SELECT positions, at FROM snap_snapshots WHERE account_id=? ORDER BY id DESC LIMIT 1', (account_id,))
    if not snap:
        return None, None
    out = {}
    for p in json.loads(snap['positions'] or '[]'):
        ins = p.get('instrument') or {}
        sym, desc = _sym(p)
        if ins.get('kind') in ('cash', 'option') or p.get('cash_equivalent') or is_cash_fund(sym, desc):
            continue
        u = _f(p.get('units'))
        if sym and u:
            out[sym] = {'qty': u, 'avg_cost': _f(p.get('cost_basis'))}
    return out, snap['at']
