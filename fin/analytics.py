"""Builds the whole portfolio picture from stored rows, cached prices and live quotes.

Returns are time-weighted (deposits and withdrawals do not count as gains). The VOO / QQQM comparison puts every
deposit and withdrawal into the benchmark on the same day, dividends reinvested, so both sides had the same money at
the same times. All the formulas the grades use are listed in GRADE_RULES and shown in the app.
"""
import json
import math
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import snaptrade
from .m2 import M2
from .engine import Replay
from .imports import account_events

BENCH = ['VOO', 'QQQM']
EXTRA = ['^IRX', 'BTC-USD']
TRADING_DAYS = 252

GRADE_RULES = {
    'performance': 'Yearly return minus VOO over the same dates: A at +5 points or better, B 0 to +5, C -5 to 0, '
                   'D -10 to -5, F below -10.',
    'm2': 'Yearly return minus the yearly growth of the M2 money supply over the same dates: A at +10 points or better, '
          'B +5 to +10, C 0 to +5, D -5 to 0, F below -5. Under M2 loses to new money even when the number is positive.',
    'risk_adjusted': 'Sharpe ratio (yearly return above T-bills divided by yearly volatility): A 1.5+, B 1.0 to 1.5, '
                     'C 0.5 to 1.0, D 0 to 0.5, F below 0.',
    'diversification': 'Effective number of holdings (1 / sum of squared weights) and the largest sector: A 20+ '
                       'holdings and no sector over 35%, B 12+ and 50%, C 8+ and 60%, D 4+, F fewer.',
    'margin': 'How far the whole portfolio can fall before a margin call: A no margin or 50%+, B 35 to 50%, '
              'C 25 to 35%, D 15 to 25%, F under 15%.',
    'costs': 'Margin interest and fees over the last 12 months as a share of account value: A under 0.5%, '
             'B 0.5 to 1.5%, C 1.5 to 3%, D 3 to 5%, F over 5%.',
}

LEV_RE = re.compile(r'\b([23])x\b|\bultra(pro)?\b|leverag', re.I)
INCOME_RE = re.compile(r'income|weeklypay|yieldmax|premium|option income|covered call|high income|buywrite', re.I)
PREF_RE = re.compile(r'preferred|perpetual', re.I)


def _f(x):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else x


class Prices:
    """Daily closes from the cache. px() gives the price actually traded that day (split factors undone)."""

    def __init__(self, db, symbols):
        self.bars, self.splits = {}, defaultdict(list)
        self.load(db, symbols)
        for r in db.q('SELECT symbol, d, ratio FROM splits ORDER BY d'):
            self.splits[r['symbol']].append((r['d'], r['ratio']))
        self.trade_px = defaultdict(list)
        self.split_list = {k: list(v) for k, v in self.splits.items()}
        self.live, self.live_day = {}, None

    def load(self, db, symbols):
        """Read the daily bars of symbols not loaded yet (one query per hundred symbols)."""
        syms = sorted(set(s for s in symbols if s and s not in self.bars))
        for i in range(0, len(syms), 100):
            part = syms[i:i + 100]
            got = defaultdict(lambda: ([], [], []))
            for sym, d, close, adj in db.tuples(f"SELECT symbol, d, close, adj FROM prices WHERE symbol IN ({','.join('?' * len(part))}) ORDER BY symbol, d", part):
                b = got[sym]
                b[0].append(d)
                b[1].append(close)
                b[2].append(adj)
            self.bars.update(got)

    def set_live(self, prices, day):
        """Today's moves (shares from the broker's counts before the rows arrive) are valued at live prices."""
        self.live, self.live_day = {k: v for k, v in prices.items() if v}, day

    def add_trade_prices(self, events):
        for e in events:
            if e.get('kind') == 'trade' and e.get('price'):
                self.trade_px[e['symbol']].append((e['date'], e['price']))
        for s in self.trade_px:
            self.trade_px[s].sort()

    def _factor(self, s, d):
        f = 1.0
        for sd, r in self.splits.get(s, ()):
            if sd > d and r:
                f *= r
        return f

    def px(self, s, d):
        if self.live_day and d >= self.live_day and s in self.live:
            return self.live[s]
        b = self.bars.get(s)
        if b:
            i = bisect_right(b[0], d) - 1
            if i >= 0:
                return b[1][i] * self._factor(s, d)
        t = self.trade_px.get(s)
        if t:
            i = bisect_right([x[0] for x in t], d) - 1
            return t[i][1] if i >= 0 else t[0][1]
        if b and b[0]:
            return b[1][0] * self._factor(s, d)
        return None

    def on(self, s, d):
        """The price on d, or None when the symbol had no price yet by then (listed later)."""
        b = self.bars.get(s)
        if b and b[0]:
            return self.px(s, d) if b[0][0] <= d else None
        t = self.trade_px.get(s)
        return self.px(s, d) if t and t[0][0] <= d else None

    def adj(self, s, d):
        b = self.bars.get(s)
        if not b:
            return None
        i = bisect_right(b[0], d) - 1
        return b[2][i] if i >= 0 else None

    def dates(self, s, start, end):
        b = self.bars.get(s)
        if not b:
            return []
        i = bisect_right(b[0], start) - 1
        return [d for d in b[0][max(i, 0):] if start <= d <= end]

    def returns(self, s, start):
        b = self.bars.get(s)
        if not b:
            return {}
        out, prev = {}, None
        for d, a in zip(b[0], b[2]):
            if d >= start and prev:
                out[d] = a / prev - 1
            prev = a
        return out


# ---- helpers -------------------------------------------------------------------------------------------------------
def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _grade(v, cuts, reverse=False):
    if v is None:
        return None
    letters = 'ABCDF'
    for i, c in enumerate(cuts):
        if (v <= c) if reverse else (v >= c):
            return letters[i]
    return 'F'


def xirr(flows):
    """flows: [(iso_date, amount)] from the investor's side (deposits negative, final value positive)."""
    if not flows or not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    t0 = date.fromisoformat(min(d for d, _ in flows))
    pts = [((date.fromisoformat(d) - t0).days / 365.0, a) for d, a in flows]
    f = lambda r: sum(a / (1 + r) ** t for t, a in pts)
    lo, hi = -0.99, 10.0
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-7:
            break
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return mid


def classify(sym, q, prof, name):
    """Asset class from the quote type and the fund's own name (no hand-made lists)."""
    t = (q or {}).get('quoteType') or (prof or {}).get('type') or ''
    nm = name or (q or {}).get('longName') or (q or {}).get('shortName') or ''
    if t == 'CRYPTOCURRENCY':
        return 'Crypto'
    if PREF_RE.search(nm):
        return 'Preferred stock'
    if t == 'ETF' or t == 'MUTUALFUND':
        if LEV_RE.search(nm):
            return 'Leveraged ETF'
        if INCOME_RE.search(nm):
            return 'Option income ETF'
        return 'ETF'
    return 'Stock'


# The "By type" groups. Coins split into Bitcoin and the rest. Stocks and funds join them when they are built around
# holding the coins: funds named for a coin, companies whose own description says they hold it as a treasury, and the
# well-known ones listed here (so they are right before a description has been fetched). Any symbol can be moved or kept
# out by hand in Settings (setting type_groups: {SYMBOL: group, or 'none' to keep it out}).
BITCOIN, CRYPTO = 'Bitcoin & Equivalents', 'Crypto & Equivalents'
BTC_KNOWN = {'MSTR', 'STRK', 'STRF', 'STRD', 'STRC', 'ASST', 'SATA', 'XXI', 'CEP', 'SMLR', 'NAKA', 'BRR', 'MTPLF', 'KULR',
             'IBIT', 'FBTC', 'GBTC', 'BITB', 'ARKB', 'BTC', 'HODL', 'BRRR', 'EZBC', 'BTCO', 'BTCW', 'BITO'}
CRYPTO_KNOWN = {'BMNR', 'SBET', 'BTBT', 'ETHZ', 'ETHM', 'ETHA', 'FETH', 'ETHE', 'ETH', 'ETHV', 'ETHW', 'UPXI', 'DFDV',
                'FWDI', 'BSOL', 'GSOL'}
BTC_FUND_RE = re.compile(r'\bbitcoin\b', re.I)
CRYPTO_FUND_RE = re.compile(r'\bether(eum)?\b|\bsolana\b|\bxrp\b|\bcrypto', re.I)
# a company's own description: holding the coin is the business, not a mention in passing (miners stay stocks)
TREASURY_RE = re.compile(r'(bitcoin|btc|ether(?:eum)?|eth|solana|sol|digital asset|crypto(?:currency)?)[ -]treasury|'
                         r'treasury (?:company|strategy|reserve)[^.]{0,80}(bitcoin|ether(?:eum)?|solana|digital asset)|'
                         r'(?:acquir|accumulat|hold)\w*[^.]{0,40}(bitcoin|ether(?:eum)?|solana)[^.]{0,40}'
                         r'(?:treasury|reserve|balance sheet)|'
                         r'accumulating (bitcoin|ether(?:eum)?|solana)|'
                         r'acquiring,? (?:and )?holding[^.;]{0,30}(bitcoin|ether(?:eum)?|solana|digital assets)', re.I)
TYPE_GROUPS_DEFAULT = {}


def type_groups(db):
    g = db.setting('type_groups', None)
    return TYPE_GROUPS_DEFAULT if g is None else g


def auto_group(sym, cls, name, prof=None):
    """(group or None, why) without the hand-made overrides."""
    if cls == 'Crypto':
        return (BITCOIN if sym.split('-')[0] == 'BTC' else CRYPTO), 'coin'
    if sym in BTC_KNOWN:
        return BITCOIN, 'known'
    if sym in CRYPTO_KNOWN:
        return CRYPTO, 'known'
    if cls.endswith('ETF'):
        if BTC_FUND_RE.search(name or ''):
            return BITCOIN, 'fund name'
        if CRYPTO_FUND_RE.search(name or ''):
            return CRYPTO, 'fund name'
    m = TREASURY_RE.search((prof or {}).get('summary') or '')
    if m:
        coin = next((g for g in m.groups() if g), '').lower()
        return (BITCOIN if coin in ('bitcoin', 'btc') else CRYPTO), 'its description'
    return None, None


def type_group(sym, cls, name, groups, prof=None):
    if sym in groups:
        return cls if groups[sym] in ('none', '', None) else groups[sym]
    return auto_group(sym, cls, name, prof)[0] or cls


def leverage(name):
    m = re.search(r'\b([23])x\b', name or '', re.I)
    return int(m.group(1)) if m else (2 if LEV_RE.search(name or '') else 1)


# ---- main ----------------------------------------------------------------------------------------------------------
def build(db, quotes, today=None, pending=True):
    """The whole model. pending=False leaves out the share counts the broker shows that the activity rows do not
    explain yet (_pending): the first matching with a broker compares against the rows alone."""
    today = today or date.today().isoformat()
    accounts = db.q('SELECT * FROM accounts ORDER BY sort, id')
    for a in accounts:
        a['_balances'] = db.q('SELECT d, kind, value, note FROM balances WHERE account_id=? ORDER BY d', (a['id'],))
        sa = db.q('SELECT meta FROM snap_accounts WHERE account_id=?', (a['id'],))
        a['_linked'] = bool(sa)
        a['_broker_value'] = sum(((json.loads(x['meta'] or '{}').get('net_value') or {}).get('amount') or 0) for x in sa) or None
    profiles = {r['symbol']: json.loads(r['profile']) for r in db.q('SELECT symbol, profile FROM symbols WHERE profile IS NOT NULL')}
    maint_over = db.setting('maintenance', {}) or {}
    groups = type_groups(db)
    margin_rates = db.setting('margin_rates', {}) or {}

    per = []
    all_syms = set(BENCH + EXTRA)
    for a in accounts:
        evs, basis_snap = account_events(db, a['id'])
        all_syms |= {e['symbol'] for e in evs if e.get('symbol') and e['kind'] in ('trade', 'transfer_sec', 'split', 'split_units')}
        all_syms |= {e['option']['underlying'] for e in evs if e.get('option')}
        per.append((a, evs, basis_snap))
    manual = db.q('SELECT * FROM manual ORDER BY id')
    all_syms |= {m['symbol'] for m in manual if m.get('symbol')}
    prices = Prices(db, all_syms)
    for _, evs, _ in per:
        prices.add_trade_prices(evs)
    prices.set_live({sym: live_price(q)['live'] for sym, q in quotes.items()}, today)

    # pass 1: replay each account, then add the share counts the broker shows now that the activity rows lack
    ctx = []
    for a, evs, basis_snap in per:
        basis = dict(basis_snap)
        for b in db.q('SELECT symbol, d, cost FROM basis WHERE account_id=?', (a['id'],)):
            basis[(b['symbol'], b['d'])] = b['cost']
        opening = db.q('SELECT symbol, qty, cost, as_of AS date FROM opening WHERE account_id=?', (a['id'],))
        rp = _replay_reconciled(db, a, evs, opening, basis, prices, quotes, today)
        pend, pbasis = _pending(db, a, rp, prices, quotes, today) if pending else ([], {})
        if pend:
            prices.load(db, {e['symbol'] for e in pend})       # their price history too, for charts and correlations
            evs = evs + pend
            basis.update(pbasis)
            all_syms |= {e['symbol'] for e in pend}
            rp = _replay_reconciled(db, a, evs, opening, basis, prices, quotes, today)
        ctx.append({'a': a, 'evs': evs, 'basis': basis, 'opening': opening, 'rp': rp, 'pending': pend})
    # pass 2: transfers between your own accounts (logged under Moves): carry lots across, count what is in transit
    transit, transit_hist = _transfers(db, ctx, prices, quotes, today)
    for c in ctx:
        if c.pop('redo', False):
            c['rp'] = _replay_reconciled(db, c['a'], c['evs'], c['opening'], c['basis'], prices, quotes, today)

    out_accounts, hist_by_acct = [], []
    combined = {}
    first_date = None
    for c in ctx:
        a, evs, opening, rp = c['a'], c['evs'], c['opening'], c['rp']
        acct = _account_now(a, rp, evs, prices, quotes, profiles, maint_over, today, opening, margin_rates)
        acct['pending'] = [{'symbol': e['symbol'], 'qty': e['qty']} for e in c['pending']]
        out_accounts.append(acct)
        h = _history(a, rp, evs, prices, opening, today, acct)
        hist_by_acct.append(h)
        # an account that reads zero after having money in it (moved out or closed) drops off the dashboard
        if h['value'] and abs(acct['equity']) < 1 and not acct['positions'] and not acct['options'] and \
                not acct.get('value_note') and max(abs(v) for v in h['value']) >= 1:
            last = max((i for i, v in enumerate(h['value']) if abs(v) >= 1), default=None)
            acct['closed'] = True
            acct['closed_on'] = h['dates'][last + 1] if last is not None and last + 1 < len(h['dates']) else today
        if h['dates']:
            first_date = min(first_date or h['dates'][0], h['dates'][0])
        for sym, pos in acct['positions'].items():
            c = combined.setdefault(sym, {'symbol': sym, 'qty': 0.0, 'value': 0.0, 'cost': 0.0, 'cost_complete': True,
                                          'day_change': 0.0, 'accounts': [], 'name': pos['name'], 'class': pos['class'],
                                          'type': type_group(sym, pos['class'], pos['name'], groups, profiles.get(sym)),
                                          'price': pos['price'], 'change_pct': pos['change_pct'], 'sector': pos['sector'],
                                          'income': 0.0, 'income_ttm': 0.0, 'first': pos['first'], 'lots': [],
                                          'earnings': pos.get('earnings')})
            c['qty'] += pos['qty']
            c['value'] += pos['value'] or 0
            c['day_change'] += pos['day_change'] or 0
            if pos['cost'] is None:
                c['cost_complete'] = False
            c['cost'] += pos['cost_known'] or 0
            c['accounts'].append({'account': a['name'], 'id': a['id'], 'qty': pos['qty'], 'value': pos['value']})
            c['lots'].extend({**l, 'account': a['name']} for l in pos['lots'])
            c['first'] = min(filter(None, [c['first'], pos['first']]), default=None)

    # manual assets (crypto held elsewhere, cash, anything else)
    manual_rows, manual_total = [], 0.0
    for m in manual:
        v = m['value']
        q = quotes.get(m['symbol']) if m.get('symbol') else None
        if m.get('symbol') and m.get('qty') is not None:
            p = (q or {}).get('regularMarketPrice') or prices.px(m['symbol'], today)
            v = (p or 0) * m['qty']
        v = v or 0
        sign = -1 if m['liability'] else 1
        manual_total += sign * v
        manual_rows.append({**m, 'value_now': v, 'price': (q or {}).get('regularMarketPrice'),
                            'change_pct': (q or {}).get('regularMarketChangePercent')})

    if transit_hist:
        hist_by_acct.append(transit_hist)
    hist = _combine(hist_by_acct, prices)
    glory = _glory(hist, hist_by_acct, prices)
    if glory:
        hist['glory'], hist['glory_effect'] = glory['line'], glory['effect']
    m2 = M2(db)
    perf = _performance(hist, prices, today, m2)
    positions = sorted(combined.values(), key=lambda p: -(p['value'] or 0))
    invested = sum(p['value'] for p in positions)
    for p in positions:
        p['weight'] = p['value'] / invested if invested else 0
        p['gain'] = p['value'] - p['cost'] if p['cost_complete'] else None
        p['gain_pct'] = p['gain'] / p['cost'] if p['gain'] is not None and p['cost'] else None
        p['avg_cost'] = p['cost'] / p['qty'] if p['cost_complete'] and p['qty'] else None
    # distance from the all-time high, at the live price; a high made today (or after hours) counts at once
    try:
        highs = {r['symbol']: r for r in db.q('SELECT symbol, high, d FROM ath WHERE high IS NOT NULL')}
    except Exception:
        highs = {}
    for p in positions:
        a = highs.get(p['symbol'])
        if not a or not p['price']:
            continue
        high, d = a['high'], a['d']
        for cand in ((quotes.get(p['symbol']) or {}).get('regularMarketDayHigh'), p['price']):
            if cand and cand > high:
                high, d = cand, today
        p['ath'], p['ath_date'], p['from_ath'] = high, d, p['price'] / high - 1
    income = _income(db, out_accounts, positions, today)
    for p in positions:
        inc = income['by_symbol'].get(p['symbol']) or {}
        p['income'] = inc.get('annual') or 0
        p['income_ttm'] = inc.get('ttm') or 0
        p['yield'] = p['income'] / p['value'] if p['value'] else None
    if m2.ready:
        # each holding against M2 since it was bought: what its cost would be if it had only kept up with the money supply
        lv_now = m2.level(today)
        for p in positions:
            lots = [l for l in p['lots'] if l.get('cost') and l.get('date')]
            if p['cost_complete'] and lots and p['value']:
                kept = sum(l['cost'] * lv_now / m2.level(l['date']) for l in lots)
                p['m2_cost'] = kept
                p['vs_m2'] = p['value'] / kept - 1 if kept > 0 else None
    divers = _diversification(positions, profiles, prices, today)
    for acct in out_accounts:
        mg = acct.get('margin')
        if not mg:
            continue
        # a broker's crypto account carries no margin, so it is shown, not counted:
        # selling it pays down the loan, which adds its full value to the buffer
        crypto = [x for x, a in zip(out_accounts, accounts) if a['broker'] == acct['broker'] and a['kind'] == 'crypto']
        if crypto:
            mg['crypto'] = sum(x['equity'] for x in crypto)
            mg['crypto_label'] = ', '.join(x['name'] for x in crypto)
    margin = [acct['margin'] for acct in out_accounts if acct.get('margin')]
    for mg in margin:
        if m2.ready and mg.get('rate'):
            mg['m2_yoy'] = m2.yoy
            mg['vs_m2'] = mg['rate'] - m2.yoy            # negative: the loan costs less than money is being created
    # leverage: what the margin accounts hold for each dollar of your own money in them. Retirement accounts are left
    # out (they are not collateral and cannot be borrowed against); a second figure covers every non-retirement account
    lev = None
    m_eq = sum(mg['equity'] for mg in margin)
    m_debt = sum(mg['debt'] for mg in margin)
    if margin and m_eq > 0:
        tax = [(x, a) for x, a in zip(out_accounts, accounts) if a['kind'] not in ('roth', 'ira', 'retirement', '401k')]
        t_eq = sum(x['equity'] for x, _ in tax)
        t_debt = sum(max(-x['cash'], 0.0) for x, _ in tax)
        lev = {'margin': (m_eq + m_debt) / m_eq, 'equity': m_eq, 'debt': m_debt, 'held': m_eq + m_debt,
               'accounts': [mg['account'] for mg in margin],
               'taxable': (t_eq + t_debt) / t_eq if t_eq > 0 else None, 'taxable_equity': t_eq}

    transit_value = sum(t['value'] for t in transit)
    total_accounts = sum(acct['equity'] for acct in out_accounts) + transit_value
    net_worth = total_accounts + manual_total
    loan_now = sum(max(-acct['cash'], 0.0) for acct in out_accounts)
    day_change = sum(acct['day_change'] for acct in out_accounts) + sum(
        (m['value_now'] * (m['change_pct'] or 0) / (100 + (m['change_pct'] or 0))) for m in manual_rows
        if m.get('change_pct') is not None and not m['liability'])
    costs = _costs(out_accounts, today)
    ext_move = sum(acct.get('ext_change') or 0 for acct in out_accounts)
    ext_at = max((acct.get('ext_at') or 0 for acct in out_accounts), default=0)
    ext_kind = next((acct['ext_kind'] for acct in sorted(out_accounts, key=lambda x: -(x.get('ext_at') or 0)) if acct.get('ext_kind')), None)
    extended = {'move': ext_move, 'session': ext_kind, 'at': ext_at, 'net_worth': net_worth} if ext_kind and abs(ext_move) >= 0.5 else None
    grades = _grades(perf, divers, margin, costs, total_accounts)
    gaps = [{'account': acct['name'], 'account_id': acct['id'], **acct['gaps']} for acct in out_accounts]
    options = _options_summary(out_accounts, today)
    shares = [{'symbol': p['symbol'], 'qty': p['qty'], 'name': p['name']} for p in positions]

    return {
        'as_of': datetime.now().isoformat(timespec='seconds'), 'today': today,
        'net_worth': net_worth, 'day_change': day_change, 'accounts_total': total_accounts,
        'assets': net_worth + loan_now, 'loan': loan_now,
        'glory': _glory_summary(glory, positions, prices, hist, today),
        'm2': m2.info(), 'leverage': lev,
        'type_auto': sorted(({'symbol': p['symbol'], 'group': g, 'why': w} for p in positions
                             for g, w in [auto_group(p['symbol'], p['class'], p['name'], profiles.get(p['symbol']))]
                             if g and w != 'coin'), key=lambda r: r['symbol']),
        'manual_total': manual_total, 'invested': invested,
        'accounts': [{k: v for k, v in acct.items() if k not in ('positions', 'replay')} for acct in out_accounts],
        'positions': positions, 'manual': manual_rows, 'history': hist, 'performance': perf, 'income': income,
        'diversification': divers, 'margin': margin, 'grades': grades, 'grade_rules': GRADE_RULES,
        'gaps': gaps, 'options': options, 'costs': costs, 'share_counts': shares, 'extended': extended, 'transit': transit,
        'symbols_needed': sorted(s for s in all_syms if s),
        'opening_count': sum(len(db.q('SELECT id FROM opening WHERE account_id=?', (a['id'],))) for a in accounts),
    }


def _replay_reconciled(db, a, evs, opening, basis, prices, quotes, today):
    """Replay, then line the account up with balances you confirmed. Robinhood's export leaves out Robinhood Crypto
    and Spending, so money can leave without a row; each confirmed balance adds the difference as an outside transfer
    on that date (it is a flow, so it never counts as a gain or loss)."""
    extra = []
    for b in db.q('SELECT * FROM balances WHERE account_id=? ORDER BY d, id', (a['id'],)):
        rp = Replay(a['name'], opening, 0.0, basis, prices.px).run(evs + extra, prices.split_list)
        snap = None
        for s in rp.snapshots:
            if s[0] <= b['d']:
                snap = s
        if snap is None:
            hold, cash, oc = {o['symbol']: o['qty'] for o in opening}, 0.0, 0.0
        else:
            hold, cash, oc = snap[1], snap[2], snap[3]
        if b['kind'] == 'cash':
            diff = b['value'] - cash
        else:
            if b['d'] >= today:
                lmv = sum(q * ((quotes.get(s) or {}).get('regularMarketPrice') or prices.px(s, today) or 0)
                          for s, q in hold.items())
            else:
                lmv = sum(q * (prices.px(s, b['d']) or 0) for s, q in hold.items())
            diff = b['value'] - (lmv + cash + oc)
        if abs(diff) >= 0.01:
            extra.append({'date': b['d'], 'settle': b['d'], 'code': 'BAL', 'symbol': '', 'qty': None, 'price': None,
                          'amount': diff, 'kind': 'flow', 'sub': 'reconcile', 'pos': 0, 'import_id': 10 ** 9,
                          'desc': f"Balance check: {b['kind']} confirmed at ${b['value']:,.2f}. "
                                  f"Difference moved in or out outside the export (crypto, Spending). {b['note'] or ''}".strip()})
    return Replay(a['name'], opening, 0.0, basis, prices.px).run(evs + extra, prices.split_list)


def _pending(db, a, rp, prices, quotes, today):
    """Share counts the broker shows now that the activity rows (a day late through SnapTrade) do not have yet: today's
    trades, transfers and crypto deposits. Added as shares moved in or out today, so values, the margin estimate and
    the holdings are right the same hour (the broker's cash is already current); the rows replace them once they land.
    Left out: a split the broker has not applied yet, dust under $1, and a broker list that looks incomplete."""
    if not a.get('_linked'):
        return [], {}
    snap = db.one('SELECT at FROM snap_snapshots WHERE account_id=? ORDER BY id DESC LIMIT 1', (a['id'],))
    if not snap or (datetime.now() - datetime.fromisoformat(snap['at'])).total_seconds() > 12 * 3600:
        return [], {}
    theirs, _ = snaptrade.broker_positions(db, a['id'])
    if theirs is None:
        return [], {}
    held = rp.holdings()
    ours = {s: h['qty'] for s, h in held.items()}
    cutoff = (date.fromisoformat(today) - timedelta(days=14)).isoformat()
    rows = []
    for sym in sorted(set(ours) | set(theirs)):
        oq, tq = ours.get(sym, 0.0), (theirs.get(sym) or {}).get('qty') or 0.0
        diff = tq - oq
        if abs(diff) <= 1e-6:
            continue
        # a recent split the broker has not applied yet (a broker can list the old count for a day or two)
        if any(d >= cutoff and r and tq and abs(tq * r - oq) <= 0.01 * max(oq, 1e-9)
               for d, r in prices.split_list.get(sym, ())):
            continue
        px = live_price(quotes.get(sym) or {})['live'] or prices.px(sym, today)
        if px is not None and abs(diff) * px < 1.0:
            continue
        rows.append((sym, diff, oq, (theirs.get(sym) or {}).get('avg_cost')))
    gone = [r for r in rows if r[1] < 0]
    if len(gone) > max(5, 0.3 * len(ours)):
        # many holdings missing at once: trust it only when the broker says the account is empty (moved out or closed)
        meta = db.one('SELECT meta FROM snap_accounts WHERE account_id=?', (a['id'],))
        net = ((json.loads((meta or {}).get('meta') or '{}').get('net_value') or {}).get('amount'))
        long_value = sum(q * (prices.px(s, today) or 0) for s, q in ours.items())
        if theirs or (net is not None and net > max(50.0, 0.2 * long_value)):
            return [], {}
    evs, basis = [], {}
    for sym, diff, oq, avg in rows:
        evs.append({'date': today, 'settle': today, 'code': 'PENDING', 'symbol': sym, 'qty': diff, 'price': None,
                    'amount': 0.0, 'kind': 'transfer_sec', 'sub': 'pending', 'name': '', 'pos': 0, 'import_id': 10 ** 9 + 1,
                    'desc': "From the broker's positions; the activity row arrives about a day later"})
        if diff > 0 and oq <= 1e-9 and avg:
            basis[(sym, today)] = avg * diff
    return evs, basis


def _transfers(db, ctx, prices, quotes, today):
    """Transfers between your own accounts, logged under Moves (from, to, start date). Shares and cash that left the
    first account count as in transit until they show up in the second, so net worth does not dip while the transfer
    is under way, and shares that arrive keep the buy dates and cost they had. Anything still missing 20 days after it
    left is treated as gone. Returns (summaries, a history series for the in-transit amount)."""
    try:
        moves = db.q("SELECT * FROM moves WHERE kind='transfer' ORDER BY d, id")
    except Exception:
        return [], None
    by_id = {c['a']['id']: c for c in ctx}
    out, series = [], defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # date -> [value, flow, shares held, day-before shares]
    tr_hold, tr_cash, tr_flows = defaultdict(lambda: defaultdict(float)), defaultdict(float), []   # for Glory Days Worth

    def px_now(sym):
        return live_price(quotes.get(sym) or {})['live'] or prices.px(sym, today)

    for m in moves:
        t = json.loads(m['data'] or '{}')
        A, B = by_id.get(t.get('from')), by_id.get(t.get('to'))
        if not A or not B:
            continue
        start = m['d']
        early = (date.fromisoformat(start) - timedelta(days=3)).isoformat()
        deps = [x for x in A['rp'].moved_out if x['date'] >= start and x['sub'] in ('acat_out', 'transfer', 'pending', 'snapshot')]
        cash_out = sorted(((f['date'], -f['amount']) for f in A['rp'].flows
                           if f['date'] >= start and not f.get('symbol') and f['amount'] < -0.5
                           and f['sub'] in ('bank', 'transfer', 'reconcile', 'snapshot')))
        # shares that arrived in the second account, matched to what left (the leftover fraction may come as cash)
        pool = defaultdict(list)
        left_q = defaultdict(float)
        for x in deps:
            pool[x['symbol']].extend(dict(l) for l in x['lots'])
            left_q[x['symbol']] += x['qty']
        arrivals = []
        for tr in sorted(B['rp'].trades, key=lambda r: r['date']):
            sym = tr['symbol']
            if tr['side'] != 'in' or tr['sub'] not in ('acat_in', 'transfer', 'pending', 'snapshot') or tr['date'] < early \
                    or left_q.get(sym, 0) <= 1e-9:
                continue
            take = min(tr['qty'], left_q[sym])
            left_q[sym] -= take
            arrivals.append((tr['date'], sym, take, tr['sub']))
            lots, need = [], take
            while need > 1e-9 and pool[sym]:
                lot = pool[sym][0]
                q = min(need, lot['qty'])
                part = None if lot['cost'] is None else lot['cost'] * q / lot['qty']
                lots.append({'date': lot['date'], 'qty': q, 'cost': part})
                lot['qty'] -= q
                if lot['cost'] is not None:
                    lot['cost'] -= part
                need -= q
                if lot['qty'] <= 1e-9:
                    pool[sym].pop(0)
            extra = tr['qty'] - take
            if extra > 1e-9:
                p = prices.px(sym, tr['date'])
                lots.append({'date': tr['date'], 'qty': extra, 'cost': p * extra if p else None})
            key = (sym, tr['date'])
            prev = B['basis'].get(key)
            B['basis'][key] = (prev if isinstance(prev, list) else []) + lots
            B['redo'] = True
        cash_in, waiting = [], list(cash_out)
        for f in sorted(B['rp'].flows, key=lambda r: r['date']):
            if f['date'] < start or f.get('symbol') or f['amount'] <= 0.5 or f['sub'] == 'crypto_funding':
                continue
            for i, (d, amt) in enumerate(waiting):
                if abs(f['amount'] - amt) <= max(1.0, 0.01 * amt) and f['date'] >= d:
                    cash_in.append((f['date'], f['amount']))
                    waiting.pop(i)
                    break
        # in transit, day by day: (left so far - arrived so far) at that day's price, plus cash on the way; a move on
        # a weekend counts on the next trading day
        days = prices.dates('VOO', start, today)
        if not days or days[-1] != today:
            days.append(today)
        slot = lambda d: days[min(bisect_left(days, d), len(days) - 1)]
        moves_on = defaultdict(list)                              # day -> [(symbol or '', qty or amount, sign)]
        for x in deps:
            moves_on[slot(x['date'])].append((x['symbol'], x['qty'], 1, x['date'], x['sub']))
        for ad, sym, q, sub in arrivals:
            moves_on[slot(ad)].append((sym, q, -1, ad, sub))
        for cd, amt in cash_out:
            moves_on[slot(cd)].append(('', amt, 1, cd, ''))
        for cd, amt in cash_in:
            moves_on[slot(cd)].append(('', amt, -1, cd, ''))
        give_up = (date.fromisoformat(min((x['date'] for x in deps), default=start)) + timedelta(days=20)).isoformat()
        waiting_q, cash_waiting, value_now, last_arrival = defaultdict(float), 0.0, 0.0, None
        prev_waiting = {}
        for d in days:
            flow, sent = 0.0, 0.0
            for sym, q, sign, when, sub in moves_on.get(d, ()):
                if sym:
                    waiting_q[sym] += sign * q
                    flow += sign * q * (prices.px(sym, when) or 0)
                    if sub != 'pending':        # mirrors the accounts: a pending leg stands in for a trade
                        sent += sign * q * (prices.px(sym, d) or 0)
                    tr_flows.append((d, sym, sign * q, sign * q * (prices.px(sym, when) or 0), sub))
                else:
                    cash_waiting += sign * q
                    flow += sign * q
                    tr_flows.append((d, None, None, sign * q, 'transfer'))
                if sign < 0:
                    last_arrival = when
            live = d == today
            val = sum(max(q, 0) * ((px_now(sym) if live else prices.px(sym, d)) or 0) for sym, q in waiting_q.items())
            val += max(cash_waiting, 0)
            if d >= give_up and val > 0.005:
                flow -= val                                       # still missing after 20 days: treated as gone
                tr_flows.append((d, None, None, -val, 'gone'))
                val, cash_waiting = 0.0, 0.0
                waiting_q.clear()
            series[d][0] += val
            series[d][1] += flow
            pxd = lambda sym: (px_now(sym) if live else prices.px(sym, d)) or 0
            series[d][2] += sum(max(q, 0) * pxd(sym) for sym, q in waiting_q.items())
            series[d][3] += sum(max(q, 0) * pxd(sym) for sym, q in prev_waiting.items()) + sent
            prev_waiting = dict(waiting_q)
            for sym, q in waiting_q.items():
                if q > 1e-9:
                    tr_hold[d][sym] += q
            tr_cash[d] += max(cash_waiting, 0)
            if live:
                value_now = val
        names = {c['a']['id']: c['a']['name'] for c in ctx}
        left_value = sum(x['qty'] * (px_now(x['symbol']) or 0) for x in deps) + sum(a2 for _, a2 in cash_out)
        status = 'waiting' if not deps and not cash_out else 'in transit' if value_now > 0.5 else \
            'gone' if today >= give_up and not arrivals and not cash_in else 'arrived'
        cash_wait = max(cash_waiting, 0)
        out.append({'move_id': m['id'], 'from': t['from'], 'to': t['to'], 'from_name': names.get(t['from']),
                    'to_name': names.get(t['to']), 'start': start, 'expected': t.get('expected'), 'value': value_now, 'status': status,
                    'left_value': left_value, 'symbols_left': len({x['symbol'] for x in deps}),
                    'symbols_waiting': sum(1 for q in waiting_q.values() if q > 1e-9),
                    'cash_waiting': cash_wait, 'arrived_on': last_arrival if status == 'arrived' else None})
    if not series:
        return out, None
    ds = sorted(series)
    return out, {'dates': ds, 'value': [series[d][0] for d in ds], 'flow': [series[d][1] for d in ds],
                 'loan': [0.0] * len(ds), 'held': [series[d][2] for d in ds], 'held_prev': [series[d][3] for d in ds],
                 'account': 'In transit', '_glory': [(dict(tr_hold[d]), tr_cash[d], 0.0) for d in ds], '_gflows': tr_flows}


def _account_now(a, rp, evs, prices, quotes, profiles, maint_over, today, opening, margin_rates=None):
    rp_hold = rp.holdings()
    positions = {}
    day_change, lmv, ext_change, ext_at, ext_kind = 0.0, 0.0, 0.0, 0, None
    for sym, h in rp_hold.items():
        q = quotes.get(sym) or {}
        lp = live_price(q)
        regular = lp['regular'] or prices.px(sym, today)
        price = lp['live'] or regular
        prev = lp['prev']
        bought_today = sum(t['qty'] for t in rp.trades if t['symbol'] == sym and t['date'] == today and
                           (t['side'] == 'buy' or (t['side'] == 'in' and t['sub'] == 'pending')))
        held = max(h['qty'] - bought_today, 0)
        # "today" is the regular session against the close before it, unless a new day's pre-market has begun
        chg = (regular - prev) if (regular is not None and prev and not lp['new_day']) else 0.0 if lp['new_day'] else None
        dc = chg * held if chg is not None else 0
        ext = (price - regular) * h['qty'] if (lp['kind'] and regular) else 0.0
        val = (price or 0) * h['qty']
        prof = profiles.get(sym) or {}
        name = h['name'] or q.get('longName') or q.get('shortName') or prof.get('name') or ''
        cls = classify(sym, q, prof, name)
        ets = q.get('earningsTimestamp')
        positions[sym] = {'symbol': sym, 'qty': h['qty'], 'price': price, 'regular_price': regular, 'prev_close': prev,
                          'earnings': datetime.utcfromtimestamp(ets).date().isoformat() if ets else None,
                          'change_pct': q.get('regularMarketChangePercent') if not lp['new_day'] else 0.0,
                          'ext_change': ext, 'ext_pct': ((price / regular - 1) * 100) if (lp['kind'] and regular) else None,
                          'value': val, 'day_change': dc,
                          'cost': h['cost'], 'cost_known': h['cost_known'], 'avg_cost': h['avg_cost'],
                          'first': h['first'], 'name': name, 'class': cls,
                          'sector': prof.get('sector_name') or (cls if cls in ('Crypto',) else None),
                          'lots': h['lots'], 'stale': not q}
        day_change += dc
        ext_change += ext
        lmv += val
        if lp['kind'] and lp['at'] and lp['at'] > ext_at:
            ext_at, ext_kind = lp['at'], lp['kind']
    # open options: expire the ones past their date that the files have not closed yet
    opts, opt_value, opt_day = [], 0.0, 0.0
    for o in rp.open_options():
        occ = _occ(o)
        q = quotes.get(occ) or {}
        mark = q.get('regularMarketPrice')
        expired = o['expiry'] < today
        und_px = (quotes.get(o['underlying']) or {}).get('regularMarketPrice') or prices.px(o['underlying'], today)
        status = 'open'
        if expired:
            at_exp = prices.px(o['underlying'], o['expiry'])
            itm = at_exp is not None and ((o['right'] == 'C' and at_exp > o['strike']) or (o['right'] == 'P' and at_exp < o['strike']))
            status = 'probably assigned' if itm else 'expired'
            mark = 0.0
        value = (mark if mark is not None else (o['cost'] / (o['qty'] * 100) if o['qty'] else 0)) * o['qty'] * 100
        if mark is not None and not expired and q.get('regularMarketChange') is not None:
            opt_day += q['regularMarketChange'] * o['qty'] * 100
        opt_value += value
        opts.append({**o, 'occ': occ, 'mark': mark, 'value': value, 'status': status, 'underlying_price': und_px,
                     'pnl': value - o['cost'], 'quoted': bool(q)})
    cash = rp.cash
    equity = lmv + opt_value + cash
    acct = {
        'id': a['id'], 'name': a['name'], 'broker': a['broker'], 'kind': a['kind'], 'is_margin': bool(a['margin']),
        'cash': cash, 'cash_confirmed': bool(db_balances(a)), 'balances': db_balances(a),
        'long_value': lmv, 'options_value': opt_value, 'equity': equity, 'day_change': day_change + opt_day,
        'ext_change': ext_change, 'ext_at': ext_at or None, 'ext_kind': ext_kind,
        'positions': positions, 'options': opts, 'gaps': _priced_gaps(rp.gap_list(), prices, today), 'replay': rp,
        'linked': bool(a.get('_broker_value') is not None or a.get('_linked')),
        'income_rows': rp.income, 'expense_rows': rp.expenses, 'flow_rows': rp.flows, 'realized': rp.realized,
        'opt_realized': rp.opt_realized, 'trades': rp.trades,
        'first_date': min((e['date'] for e in evs), default=None), 'last_date': max((e['date'] for e in evs), default=None),
        'rows': len(evs), 'has_opening': bool(opening),
    }
    acct['margin'] = _margin(acct, maint_over, a.get('maint_scale'), a.get('maint_note')) if a['margin'] else None
    if acct['margin']:
        mg = acct['margin']
        tiers = (margin_rates or {}).get(a['broker'])
        mg['free'] = (tiers or {}).get('free', 0)
        mg['rate_implied'] = _margin_rate(rp, mg['free'])
        if tiers:
            # the broker's own schedule: one rate on the whole loan, set by the loan's size; the first part is free
            debt = mg['debt']
            rate = next((r for above, r in sorted(tiers['tiers'], reverse=True) if debt > above), tiers['tiers'][-1][1])
            mg['rate'], mg['rate_source'] = rate, tiers.get('note', 'broker schedule')
            mg['interest_year'] = -rate * max(debt - tiers.get('free', 0), 0)
            nxt = [(above, r) for above, r in tiers['tiers'] if above and debt > above]
            mg['rate_change_at'] = max(a2 for a2, _ in nxt) if nxt else None
            if mg['rate_change_at']:
                below = [r for above, r in sorted(tiers['tiers'], reverse=True) if above < mg['rate_change_at']]
                mg['rate_below'] = below[0] if below else None
        else:
            r = mg['rate_implied']
            mg['rate'], mg['rate_source'] = r, 'implied by the last interest charge'
            mg['interest_year'] = -(r * mg['debt']) if r else None
    if not evs and a.get('_broker_value'):
        # linked, but the broker shares only a total (Fidelity Crypto)
        acct['equity'] = acct['cash'] = a['_broker_value']
        acct['value_note'] = 'Total from SnapTrade; the broker does not share the holdings.'
    return acct


def live_price(q):
    """The newest trade price: after-hours or pre-market when it is newer than the regular session's."""
    reg, rt = q.get('regularMarketPrice'), q.get('regularMarketTime') or 0
    live, kind, at = reg, None, rt
    if q.get('postMarketPrice') and (q.get('postMarketTime') or 0) > at:
        live, kind, at = q['postMarketPrice'], 'post', q.get('postMarketTime')
    if q.get('preMarketPrice') and (q.get('preMarketTime') or 0) > at:
        live, kind, at = q['preMarketPrice'], 'pre', q.get('preMarketTime')
    # a pre-market trade on a later day than the last session starts a new "today"
    new_day = bool(kind == 'pre' and rt and datetime.utcfromtimestamp(at - 5 * 3600).date() > datetime.utcfromtimestamp(rt - 5 * 3600).date())
    return {'live': live, 'regular': reg, 'prev': q.get('regularMarketPreviousClose'), 'kind': kind, 'at': at, 'new_day': new_day}


def _priced_gaps(g, prices, today):
    """Shares the history cannot explain, with today's value; under $5 is rounding dust (crypto, fractions)."""
    for x in g['shares']:
        x['value'] = (prices.px(x['symbol'], today) or 0) * x['qty']
    g['shares'] = [x for x in g['shares'] if x['value'] >= 5]
    return g


def db_balances(a):
    return a.get('_balances') or []


def _occ(o):
    y, m, d = o['expiry'].split('-')
    return f"{o['underlying']}{y[2:]}{m}{d}{o['right']}{int(round(o['strike'] * 1000)):08d}"


def _maint_pct(p, over, scale=None):
    if p['symbol'] in over:
        return float(over[p['symbol']]) / 100.0, 'your rate'
    if (p['price'] or 0) < 3:
        return 1.0, 'under $3'
    lev = leverage(p['name']) if p['class'] == 'Leveraged ETF' else 1
    base, why = (min(1.0, 0.25 * lev), f'{lev}x fund') if lev > 1 else (0.25, 'base 25%')
    if scale:
        return min(1.0, base * scale), why + f' x{scale:.2f}'
    return base, why


def _margin(acct, over, scale=None, scale_note=''):
    rows, req, puts = [], 0.0, 0.0
    for p in acct['positions'].values():
        m, why = _maint_pct(p, over, scale)
        rows.append({'symbol': p['symbol'], 'value': p['value'], 'pct': m, 'why': why, 'req': m * p['value']})
        req += m * p['value']
    for o in acct['options']:
        if o['status'] != 'open':
            continue
        if o['qty'] > 0:
            rows.append({'symbol': o['key'], 'value': o['value'], 'pct': 1.0, 'why': 'long option', 'req': o['value']})
            req += o['value']
        elif o['right'] == 'P':
            hold = o['strike'] * 100 * -o['qty']
            puts += hold
            rows.append({'symbol': o['key'], 'value': o['value'], 'pct': None, 'why': 'short put held as cash',
                         'req': hold})
    lmv = acct['long_value'] + max(acct['options_value'], 0)
    debt = max(-acct['cash'], 0.0)
    equity = acct['equity']
    excess = equity - req - puts
    avg_m = (req / lmv) if lmv else 0
    drop = 1 - (debt + puts) / (lmv * (1 - avg_m)) if lmv and avg_m < 1 else None
    drop = max(drop, 0.0) if drop is not None else None
    for r in rows:
        v, m = r['value'] or 0, r['pct']
        r['solo_drop'] = (excess / (v * (1 - m))) if v > 0 and m is not None and m < 1 else None
    rows.sort(key=lambda r: -(r['req'] or 0))
    stress = []
    for d in (0.10, 0.20, 0.30, 0.40):
        e2 = lmv * (1 - d) - debt
        r2 = req * (1 - d) + puts
        stress.append({'drop': d, 'equity': e2, 'requirement': r2, 'excess': e2 - r2})
    return {'account': acct['name'], 'debt': debt, 'equity': equity, 'long_value': lmv, 'requirement': req + puts,
            'excess': excess, 'cushion': excess / lmv if lmv else None, 'drop_to_call': drop, 'avg_maint': avg_m,
            'leverage': lmv / equity if equity > 0 else None, 'rows': rows, 'stress': stress,
            'cash_confirmed': acct['cash_confirmed'], 'scale': scale, 'scale_note': scale_note,
            'note': 'Estimate, at live prices. The broker\'s own rate for the holdings you entered, about 25% for the rest'
                    + (' (calibrated to the buffer your broker showed)' if scale else '') + ', more for leveraged funds and stocks under $3.'}


def _margin_rate(rp, free=0):
    """Yearly margin rate implied by the last interest charge and the average loan in the 30 days before it."""
    charges = [e for e in rp.expenses if e['sub'] == 'margin_interest' and e['amount'] < 0]
    if not charges or not rp.snapshots:
        return None
    last = max(charges, key=lambda e: e['date'])
    end = date.fromisoformat(last['date'])
    debts, si, cash = [], 0, 0.0
    snaps = rp.snapshots
    d = end - timedelta(days=31)
    while si < len(snaps) and snaps[si][0] <= d.isoformat():
        cash = snaps[si][2]
        si += 1
    while d < end:
        while si < len(snaps) and snaps[si][0] <= d.isoformat():
            cash = snaps[si][2]
            si += 1
        debts.append(max(-cash - free, 0.0))           # some brokers charge nothing on the first part of a loan
        d += timedelta(days=1)
    avg = sum(debts) / len(debts) if debts else 0
    rate = (-last['amount'] * 12 / avg) if avg > 1000 else None
    return rate if rate and 0.01 < rate < 0.2 else None


def _history(a, rp, evs, prices, opening, today, acct):
    """Daily value of one account from its first event to today."""
    snaps = rp.snapshots
    if not snaps:
        if acct.get('value_note') and acct['equity']:
            # the broker shares only a total (Fidelity Crypto): held flat from the day it was linked, arriving as money in
            days = prices.dates('VOO', (a.get('created') or today)[:10], today)
            if not days or days[-1] != today:
                days.append(today)
            v = acct['equity']
            return {'dates': days, 'value': [v] * len(days), 'flow': [v] + [0.0] * (len(days) - 1),
                    'loan': [0.0] * len(days), 'account': a['name'], '_gflows': [(days[0], None, None, v, 'total')]}
        return {'dates': [], 'value': [], 'flow': [], 'loan': []}
    start = snaps[0][0]
    if opening:
        start = min(start, min((o['date'] or start) for o in opening))
    days = prices.dates('VOO', start, today)
    if not days or days[-1] != today:
        days.append(today)
    days = [d for d in days if d >= start]
    vals, flows, loans, held, held_prev = [], [], [], [], []
    glory = []                       # (holdings, cash, other) per day, priced once the high is known (_glory)
    # shares and coins sent in or out (not today's pending ones, which stand in for trades until the rows arrive): they
    # count as if they were there the day before, so they don't move the asset units
    moved = sorted((f['date'], f['symbol'], f['qty']) for f in rp.flows if f.get('symbol') and f.get('qty') and f['sub'] != 'pending')
    gflows = [(f['date'], f.get('symbol'), f.get('qty'), f['amount'], f['sub']) for f in rp.flows]
    mi = 0
    prev_hold, prev_d = None, None
    si, cur = 0, None
    open_hold = defaultdict(float)
    for o in opening:
        open_hold[o['symbol']] += o['qty']
    cur = (start, dict(open_hold), rp.snapshots and (snaps[0][2] - sum(0 for _ in ())) or 0, 0.0, 0.0)
    base_cash = None
    for d in days:
        f = 0.0
        while si < len(snaps) and snaps[si][0] <= d:
            cur = snaps[si]
            f += snaps[si][4]
            si += 1
        if cur[0] > d:
            hold, cash, oc = dict(open_hold), None, 0.0
        else:
            hold, cash, oc = cur[1], cur[2], cur[3]
        if cash is None:
            cash = _opening_cash(rp, evs)
        hv = sum(q * (prices.px(s, d) or 0) for s, q in hold.items())
        if d == today:
            v = acct['equity']
            loans.append(max(-acct['cash'], 0.0))
            glory.append((hold, acct['cash'], v - hv - acct['cash']))
        else:
            v = hv + cash + oc
            loans.append(max(-cash, 0.0))
            glory.append((hold, cash, oc))
        # holdings at that day's prices, and the day before's holdings at that day's prices (in that day's share terms
        # after any split): the gap between the two is what was bought or sold, which drives the asset units
        held.append(hv)
        sent = 0.0
        while mi < len(moved) and moved[mi][0] <= d:
            sent += moved[mi][2] * (prices.px(moved[mi][1], d) or 0)
            mi += 1
        held_prev.append(sum(q * prices._factor(s, prev_d) / (prices._factor(s, d) or 1) * (prices.px(s, d) or 0)
                             for s, q in prev_hold.items()) + sent if prev_hold else 0.0)
        prev_hold, prev_d = hold, d
        if not vals and open_hold:
            # holdings already there on the first day arrive from outside the tracked accounts: a flow, not a gain
            f += sum(q * (prices.px(s, d) or 0) for s, q in open_hold.items())
            gflows += [(d, s, q, q * (prices.px(s, d) or 0), 'opening') for s, q in open_hold.items()]
        vals.append(v)
        flows.append(f)
    return {'dates': days, 'value': vals, 'flow': flows, 'loan': loans, 'held': held, 'held_prev': held_prev,
            'account': a['name'], '_glory': glory, '_gflows': gflows}


def _glory(hist, hists, prices):
    """Glory Days Worth: each day's holdings at the prices they traded for on the day of the net worth high, plus that
    day's cash, loan and options. Price moves leave it alone; buying adds the gain the new shares would make on the way
    back to the high-day price, selling takes it away. A holding with no price on that day (listed since) counts at the
    day's own price, and accounts that share only a total (and cash in transit) count at their value.

    Money and shares added or taken out count as if they were there on the day of the high (shares at the high-day
    price), so from the high on the line moves only with buying and selling, dividends, interest and option marks.
    Today's pending moves stand in for trades and count at the live price they were valued at."""
    dates, value = hist['dates'], hist['value']
    if not dates:
        return None
    k = max(range(len(dates)), key=lambda i: value[i])
    hd, n = dates[k], len(dates)
    star = {}

    def pstar(sym):
        if sym not in star:
            star[sym] = prices.on(sym, hd)
        return star[sym]

    def at_star(sym, q, d):
        p = pstar(sym)
        return None if p is None else q * p * prices._factor(sym, d) / (prices._factor(sym, hd) or 1)
    idx = {d: i for i, d in enumerate(dates)}
    held_star, cash_t, other_t, gflow = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
    at_high = defaultdict(float)
    for h in hists:
        if not h['dates']:
            continue
        gd, hi, last, memo = h.get('_glory'), {d: i for i, d in enumerate(h['dates'])}, None, {}
        for d in dates:
            i = idx[d]
            if d in hi:
                j = hi[d]
                if gd:
                    hold, cash, other = gd[j]
                    if id(hold) not in memo:
                        # holdings only change between snapshots: price the ones with a high-day price once (in this
                        # day's share terms, after any split since), keep the rest to price day by day
                        sv, rest = 0.0, []
                        for sym, q in hold.items():
                            v = at_star(sym, q, d)
                            if v is None:
                                rest.append((sym, q))
                            else:
                                sv += v
                        memo[id(hold)] = (sv, rest)
                    sv, rest = memo[id(hold)]
                    last = (sv, cash, other + sum(q * (prices.px(sym, d) or 0) for sym, q in rest))
                else:
                    last = (0.0, 0.0, h['value'][j])
            if last is not None and d >= h['dates'][0]:
                held_star[i] += last[0]
                cash_t[i] += last[1]
                other_t[i] += last[2]
        if gd and hd in hi:
            for sym, q in gd[hi[hd]][0].items():
                at_high[sym] += q
        # money and shares in or out on each day (a weekend move counts on the next trading day)
        for fd, sym, q, amt, sub in h.get('_gflows') or ():
            i = bisect_left(dates, fd)
            if i <= k or i >= n:
                continue
            v = at_star(sym, q, fd) if sym and q and sub != 'pending' else None
            gflow[i] += amt if v is None else v
        h.pop('_glory', None)
        h.pop('_gflows', None)
    raw = [a + b + c for a, b, c in zip(held_star, cash_t, other_t)]
    # from the high on: each day's change less the money and shares that came in or went out
    effect = [None] * n
    for i in range(k + 1, n):
        effect[i] = raw[i] - raw[i - 1] - gflow[i]
    line, run = [None] * n, raw[n - 1]
    for i in range(n - 1, k - 1, -1):
        line[i] = run
        if i > k:
            run -= effect[i]
    return {'date': hd, 'index': k, 'high': value[k], 'raw': raw, 'line': line, 'effect': effect, 'gflow': gflow,
            'held': held_star, 'at_high': dict(at_high), 'star': star}


def _glory_summary(g, positions, prices, hist, today):
    """The Glory Days numbers and the per-holding table, in today's share terms."""
    if not g:
        return None
    hd, k, end = g['date'], g['index'], len(g['raw']) - 1
    rows, syms = [], set(g['at_high']) | {p['symbol'] for p in positions if p['qty']}
    now = {p['symbol']: p for p in positions}
    for sym in syms:
        p = now.get(sym) or {}
        q_now = p.get('qty') or 0.0
        f = prices._factor(sym, hd) or 1
        q_high = g['at_high'].get(sym, 0.0) * f / (prices._factor(sym, today) or 1)
        raw = g['star'].get(sym, prices.on(sym, hd))
        star = raw * (prices._factor(sym, today) or 1) / f if raw else None
        price = p.get('price') or prices.px(sym, today)
        if abs(q_now) < 1e-9 and abs(q_high) < 1e-9:
            continue
        rows.append({'symbol': sym, 'name': p.get('name'), 'qty': q_now, 'qty_high': q_high, 'star': star, 'price': price,
                     'worth': q_now * star if star else None, 'change': (q_now - q_high) * star if star else None,
                     'upside': q_now * (star - price) if star and price else None})
    rows.sort(key=lambda r: -(r['worth'] if r['worth'] is not None else (r['qty'] * (r['price'] or 0))))
    money_in = sum(g['gflow'][k + 1:end + 1])
    return {'date': hd, 'high': g['high'], 'now': g['raw'][end], 'net_worth': hist['value'][end], 'start': g['line'][k],
            'money_in': money_in, 'trading': g['raw'][end] - g['high'] - money_in,
            'missing': sorted(r['symbol'] for r in rows if r['star'] is None and r['qty']), 'rows': rows}


def _opening_cash(rp, evs):
    return rp.cash - sum(e['amount'] or 0 for e in evs if e['kind'] not in ('transfer_sec', 'split', 'option_ca', 'split_units', 'ysplit'))


def _combine(hists, prices):
    dates = sorted({d for h in hists for d in h['dates']})
    if not dates:
        return {'dates': [], 'value': [], 'flow': [], 'loan': [], 'assets': [], 'net_invested': [], 'accounts': []}
    idx = {d: i for i, d in enumerate(dates)}
    value, flow, loan = [0.0] * len(dates), [0.0] * len(dates), [0.0] * len(dates)
    held, held_prev = [0.0] * len(dates), [0.0] * len(dates)
    per = []
    for h in hists:
        if not h['dates']:
            continue
        series = [None] * len(dates)
        last = last_loan = None
        hi = {d: i for i, d in enumerate(h['dates'])}
        for d in dates:
            i = idx[d]
            if d in hi:
                last = h['value'][hi[d]]
                last_loan = (h.get('loan') or [0.0] * len(h['dates']))[hi[d]]
                flow[i] += h['flow'][hi[d]]
                if h.get('held'):
                    held[i] += h['held'][hi[d]]
                    held_prev[i] += h['held_prev'][hi[d]]
            if last is not None and d >= h['dates'][0]:
                value[i] += last
                loan[i] += last_loan or 0.0
                series[i] = last
        per.append({'account': h['account'], 'value': series})
    net, run = [], 0.0
    for i, d in enumerate(dates):
        if i == 0:
            run = value[0] - flow[0] + flow[0]      # money present on day one counts as invested
        else:
            run += flow[i]
        net.append(run)
    # assets: everything owned at market value before the margin loan (net worth + loan); a sale that pays down the
    # loan lowers both by the same amount
    # asset units: as if your holdings were a fund you own units of. The unit price follows the holdings' price moves
    # (yesterday's shares at today's prices); buying adds units, selling or sending out removes them
    units, unit_px = [], 1.0
    for i in range(len(dates)):
        if i and held[i - 1] >= 1 and held_prev[i] > 0:
            unit_px *= held_prev[i] / held[i - 1]
        units.append(held[i] / unit_px)
    return {'dates': dates, 'value': value, 'flow': flow, 'loan': loan, 'assets': [v + l for v, l in zip(value, loan)],
            'units': units, 'net_invested': net, 'accounts': per}


def _performance(hist, prices, today, m2=None):
    dates, value, flow = hist['dates'], hist['value'], hist['flow']
    if len(dates) < 3:
        return {'ready': False}
    # time-weighted daily returns; skip days when the account was empty
    rets, rdates, index, idx_series = [], [], 1.0, [1.0]
    for i in range(1, len(dates)):
        prev = value[i - 1]
        if prev > 50:
            r = (value[i] - flow[i]) / prev - 1
            if r < -0.9 or r > 3:
                r = 0.0                                   # a data hole, not a real day
        else:
            r = 0.0
        rets.append(r)
        rdates.append(dates[i])
        index *= 1 + r
        idx_series.append(index)
    first_val_i = next((i for i, v in enumerate(value) if v > 50), 0)
    t0 = dates[first_val_i]
    years = max((date.fromisoformat(dates[-1]) - date.fromisoformat(t0)).days / 365.25, 1 / 365)

    # benchmarks: same deposits and withdrawals, same days
    bench = {}
    for b in BENCH:
        shares, series, ok = 0.0, [], True
        bidx, bprev = [], None
        for i, d in enumerate(dates):
            p = prices.adj(b, d)
            if p is None:
                series.append(None)
                ok = False
                continue
            add = (value[i] if i == first_val_i else 0) + (flow[i] if i > first_val_i else 0)
            if i < first_val_i:
                add = 0
            shares += add / p
            series.append(shares * p)
            bidx.append(p)
        b_rets = prices.returns(b, t0)
        rs = [b_rets.get(d, 0.0) for d in rdates if d > t0]
        tw = 1.0
        for r in rs:
            tw *= 1 + r
        bench[b] = {'value': series, 'final': series[-1] if series else None, 'twr': tw - 1,
                    'px': [prices.adj(b, d) for d in dates],
                    'annual': (tw ** (1 / years) - 1) if years >= 1 else tw - 1,
                    'stats': _risk_stats(rs, prices, rdates[-len(rs):] if rs else [])}
    if m2 is not None and m2.ready:
        # M2 as a benchmark: the same deposits growing with the money supply, and its level for percent charts
        lv = [m2.level(d) for d in dates]
        units, series = 0.0, []
        for i, d in enumerate(dates):
            add = (value[i] if i == first_val_i else 0) + (flow[i] if i > first_val_i else 0)
            units += (add if i >= first_val_i else 0) / lv[i]
            series.append(units * lv[i])
        tw = lv[-1] / lv[first_val_i]
        bench['M2'] = {'value': series, 'final': series[-1], 'twr': tw - 1, 'px': lv,
                       'annual': (tw ** (1 / years) - 1) if years >= 1 else tw - 1, 'stats': None}
    my = [r for d, r in zip(rdates, rets) if d > t0]
    my_dates = [d for d in rdates if d > t0]
    twr = idx_series[-1] - 1
    stats = _risk_stats(my, prices, my_dates)
    vb = prices.returns('VOO', t0)
    pairs = [(r, vb[d]) for r, d in zip(my, my_dates) if d in vb]
    beta = corr = alpha = None
    if len(pairs) > 20:
        mx, my_ = _mean([p[1] for p in pairs]), _mean([p[0] for p in pairs])
        cov = sum((p[0] - my_) * (p[1] - mx) for p in pairs) / (len(pairs) - 1)
        var = sum((p[1] - mx) ** 2 for p in pairs) / (len(pairs) - 1)
        sp, sb = _std([p[0] for p in pairs]), _std([p[1] for p in pairs])
        beta = cov / var if var else None
        corr = cov / (sp * sb) if sp and sb else None
        if beta is not None:
            alpha = (my_ - beta * mx) * TRADING_DAYS
    final = value[-1]
    invested_flows = [(dates[first_val_i], -value[first_val_i])] + \
                     [(d, -f) for d, f in zip(dates[first_val_i + 1:], flow[first_val_i + 1:]) if f] + [(dates[-1], final)]
    irr = xirr(invested_flows)
    net_in = value[first_val_i] + sum(flow[first_val_i + 1:])
    return {
        'ready': True, 'start': t0, 'end': dates[-1], 'years': years, 'twr': twr,
        'annual': (idx_series[-1] ** (1 / years) - 1) if years >= 1 else twr, 'irr': irr,
        'index': idx_series, 'net_in': net_in, 'final': final, 'gain': final - net_in,
        'stats': stats, 'beta': beta, 'correlation': corr, 'alpha': alpha, 'bench': bench,
        'note': 'Returns are time-weighted from the first day the files show money in the account. '
                'Open options are carried at their cost until they close, and at live prices today.',
    }


def _risk_stats(rets, prices, rdates):
    if len(rets) < 20:
        return {'sharpe': None, 'sortino': None, 'vol': None, 'max_dd': None, 'days': len(rets)}
    rf = []
    for d in rdates:
        y = prices.px('^IRX', d)
        rf.append((y or 4.0) / 100 / TRADING_DAYS)
    ex = [r - f for r, f in zip(rets, rf)]
    sd = _std(rets)
    down = [min(x, 0) for x in ex]
    dd = math.sqrt(sum(x * x for x in down) / len(down)) if down else None
    peak, idx, mdd, trough_d, peak_d, cur_peak_d = 1.0, 1.0, 0.0, None, None, rdates[0] if rdates else None
    for r, d in zip(rets, rdates):
        idx *= 1 + r
        if idx > peak:
            peak, cur_peak_d = idx, d
        dd_now = idx / peak - 1
        if dd_now < mdd:
            mdd, trough_d, peak_d = dd_now, d, cur_peak_d
    return {'sharpe': (_mean(ex) / sd * math.sqrt(TRADING_DAYS)) if sd else None,
            'sortino': (_mean(ex) / dd * math.sqrt(TRADING_DAYS)) if dd else None,
            'vol': sd * math.sqrt(TRADING_DAYS) if sd else None, 'max_dd': mdd, 'dd_peak': peak_d,
            'dd_trough': trough_d, 'days': len(rets), 'best_day': max(rets), 'worst_day': min(rets),
            'rf': _mean(rf) * TRADING_DAYS if rf else None}


def _income(db, accounts, positions, today):
    """Received income from the statements, and the coming 12 months projected from each holding's own payments."""
    t = date.fromisoformat(today)
    received = defaultdict(lambda: defaultdict(float))
    rows = []
    for a in accounts:
        for r in a['income_rows']:
            m = r['date'][:7]
            received[m][r['sub']] += r['amount']
            rows.append({**r, 'account': a['name']})
        for r in a['opt_realized']:
            received[r['date'][:7]]['options'] += r['pnl']
        for r in a['expense_rows']:
            received[r['date'][:7]][r['sub']] += r['amount']
    months = sorted(received)
    by_month = [{'month': m, **{k: round(v, 2) for k, v in received[m].items()}} for m in months]

    by_symbol, upcoming, calendar = {}, [], defaultdict(float)
    year_out = (t + timedelta(days=365)).isoformat()
    for p in positions:
        sym = p['symbol']
        hist = db.q("SELECT ex, amount, pay, source FROM divs WHERE symbol=? ORDER BY ex", (sym,))
        if not hist:
            continue
        # prefer Nasdaq rows (they carry pay dates); a Yahoo row within 4 days of one is the same payment
        merged = []
        for h in sorted(hist, key=lambda h: (h['source'] != 'nasdaq', h['ex'])):
            if any(abs((date.fromisoformat(h['ex']) - date.fromisoformat(m['ex'])).days) <= 4 for m in merged):
                continue
            merged.append(h)
        merged.sort(key=lambda h: h['ex'])
        past = [h for h in merged if h['ex'] <= today]
        future = [h for h in merged if h['ex'] > today]
        yr_ago = (t - timedelta(days=365)).isoformat()
        ttm_rows = [h for h in past if h['ex'] > yr_ago]
        if not ttm_rows and not future:
            continue
        # how often it pays, from the typical gap between recent payments, snapped to a normal schedule
        recent_ex = [date.fromisoformat(h['ex']) for h in past[-14:]]
        gaps = sorted((b2 - a2).days for a2, b2 in zip(recent_ex, recent_ex[1:]))
        g = gaps[len(gaps) // 2] if gaps else None
        freq = 0 if not ttm_rows else 1 if g is None else 252 if g <= 4 else 52 if g <= 10 else 12 if g <= 45 else \
            4 if g <= 135 else 2 if g <= 270 else 1
        n = len(ttm_rows)
        k = max(1, min(n, round(freq / 4))) if n else 0
        recent = ttm_rows[-k:] if k else []
        rate = (sum(h['amount'] for h in recent) / len(recent)) if recent else 0
        annual_ps = rate * freq
        ttm_ps = sum(h['amount'] for h in ttm_rows)
        # dividend growth: last 12 months against the 12 months ending three years ago, capped to -10%..+15% a year
        old_lo, old_hi = (t - timedelta(days=365 * 4)).isoformat(), (t - timedelta(days=365 * 3)).isoformat()
        old_sum = sum(h['amount'] for h in past if old_lo < h['ex'] <= old_hi)
        ttm_sum = sum(h['amount'] for h in ttm_rows)
        growth = max(-0.10, min(0.15, (ttm_sum / old_sum) ** (1 / 3) - 1)) if old_sum > 0 and ttm_sum > 0 else 0.0
        lags = [(date.fromisoformat(h['pay']) - date.fromisoformat(h['ex'])).days for h in merged if h.get('pay')]
        lag = sorted(lags)[len(lags) // 2] if lags else 3
        qty = p['qty']
        by_symbol[sym] = {'symbol': sym, 'qty': qty, 'per_share_annual': annual_ps, 'annual': annual_ps * qty,
                          'ttm_per_share': ttm_ps, 'ttm': ttm_ps * qty, 'payments_per_year': round(freq, 1),
                          'last_ex': past[-1]['ex'] if past else None, 'last_amount': past[-1]['amount'] if past else None,
                          'yield': (annual_ps / p['price']) if p['price'] else None, 'growth': growth}
        # declared payments first, then projected ones on the same rhythm
        for h in future:
            pay = h.get('pay') or (date.fromisoformat(h['ex']) + timedelta(days=lag)).isoformat()
            upcoming.append({'symbol': sym, 'ex': h['ex'], 'pay': pay, 'per_share': h['amount'],
                             'amount': h['amount'] * qty, 'declared': True})
            if pay <= year_out:
                calendar[pay[:7]] += h['amount'] * qty
        if freq and past:
            step = 365.0 / freq
            last_ex = date.fromisoformat((future[-1] if future else past[-1])['ex'])
            i = 1
            while True:
                ex = last_ex + timedelta(days=round(step * i))
                if ex.isoformat() > year_out:
                    break
                pay = ex + timedelta(days=lag)
                calendar[pay.isoformat()[:7]] += rate * qty
                if len(upcoming) < 400 and (ex - t).days <= 60:
                    upcoming.append({'symbol': sym, 'ex': ex.isoformat(), 'pay': pay.isoformat(), 'per_share': rate,
                                     'amount': rate * qty, 'declared': False})
                i += 1
    upcoming.sort(key=lambda u: u['pay'])
    cal_months = []
    for i in range(12):
        m = (t.replace(day=1) + timedelta(days=32 * i)).replace(day=1).isoformat()[:7]
        cal_months.append({'month': m, 'amount': round(calendar.get(m, 0.0), 2)})
    annual = sum(v['annual'] for v in by_symbol.values())
    ttm_recv = sum(r['amount'] for r in rows if r['date'] > (t - timedelta(days=365)).isoformat()
                   and r['sub'] in ('dividend', 'dividend_in_lieu', 'return_of_capital'))
    # income by calendar year: what came in, then the next years projected from today's holdings
    yr_ago = (t - timedelta(days=365)).isoformat()
    years = defaultdict(lambda: {'dividends': 0.0, 'options': 0.0, 'interest': 0.0, 'other': 0.0})
    opt_ttm = int_ttm = 0.0
    for a in accounts:
        for r in a['income_rows']:
            key = 'dividends' if r['sub'] in ('dividend', 'dividend_in_lieu', 'return_of_capital', 'cash_in_lieu') else 'other'
            years[r['date'][:4]][key] += r['amount']
        for r in a['opt_realized']:
            years[r['date'][:4]]['options'] += r['pnl']
            if r['date'] > yr_ago:
                opt_ttm += r['pnl']
        for r in a['expense_rows']:
            if r['sub'] == 'margin_interest':
                years[r['date'][:4]]['interest'] += r['amount']
                if r['date'] > yr_ago:
                    int_ttm += r['amount']
    growth_w = (sum(v['annual'] * v['growth'] for v in by_symbol.values()) / annual) if annual else 0.0
    # interest ahead: today's loans at their implied rates (falls back to the last 12 months)
    ahead = [a['margin']['interest_year'] for a in accounts if a.get('margin') and a['margin'].get('interest_year')]
    int_year = sum(ahead) if ahead else int_ttm
    by_year = []
    for y in sorted(years):
        v = years[y]
        by_year.append({'year': int(y), 'kind': 'actual' if int(y) < t.year else 'so far', **{k: round(x, 2) for k, x in v.items()},
                        'net': round(sum(v.values()), 2)})
    days_left = (date(t.year, 12, 31) - t).days / 365.0
    cur = next((b for b in by_year if b['year'] == t.year), None)
    if cur is not None:
        cur['rest_dividends'] = round(annual * days_left, 2)
        cur['rest_options'] = round(opt_ttm * days_left, 2)
        cur['rest_interest'] = round(int_year * days_left, 2)
    for i in range(1, 5):
        d = sum(v['annual'] * (1 + v['growth']) ** i for v in by_symbol.values())
        by_year.append({'year': t.year + i, 'kind': 'projected', 'dividends': round(d, 2), 'options': round(opt_ttm, 2),
                        'interest': round(int_year, 2), 'other': 0.0, 'net': round(d + opt_ttm + int_year, 2)})
    rates = [a['margin']['rate'] for a in accounts if a.get('margin') and a['margin'].get('rate')]
    next12 = {'dividends': annual, 'options_pace': opt_ttm, 'interest': int_year, 'interest_ttm': int_ttm,
              'net': annual + opt_ttm + int_year, 'dividend_growth': growth_w, 'margin_rate': rates[0] if rates else None}
    return {'annual': annual, 'monthly': annual / 12, 'by_symbol': by_symbol, 'upcoming': upcoming[:60],
            'by_year': by_year, 'next12': next12,
            'calendar': cal_months, 'received_by_month': by_month, 'received_rows': sorted(rows, key=lambda r: r['date'], reverse=True)[:300],
            'received_ttm': ttm_recv,
            'note': 'Projection: each holding\'s latest payments times how often it pays, on the shares you hold now. '
                    'Declared payments use their real dates. Option-income funds change their payouts often.'}


def _diversification(positions, profiles, prices, today):
    tot = sum(p['value'] for p in positions if p['value'] > 0)
    if not tot:
        return {'ready': False}
    w = {p['symbol']: p['value'] / tot for p in positions if p['value'] > 0}
    hhi = sum(x * x for x in w.values())
    by_class, by_sector = defaultdict(float), defaultdict(float)
    for p in positions:
        if p['value'] <= 0:
            continue
        wt = w[p['symbol']]
        by_class[p.get('type') or p['class']] += wt
        prof = profiles.get(p['symbol']) or {}
        sw = prof.get('sector_weights') or {}
        if p.get('type') in (BITCOIN, CRYPTO):
            by_sector[p['type']] += wt          # Bitcoin and crypto count as sectors of their own (MSTR leaves Technology)
        elif sw and sum(sw.values()) > 0.2:
            s = sum(sw.values())
            for k, v in sw.items():
                by_sector[_sector_name(k)] += wt * v / s
        elif prof.get('sector_name'):
            by_sector[prof['sector_name']] += wt
        else:
            by_sector['Not classified'] += wt
    # correlation over the last 6 months of daily returns
    start = (date.fromisoformat(today) - timedelta(days=183)).isoformat()
    top = [p['symbol'] for p in positions if p['value'] > 0][:40]
    rets = {s: prices.returns(s, start) for s in top}
    days = sorted(set().union(*[set(r) for r in rets.values()])) if rets else []
    vecs = {s: [rets[s].get(d) for d in days] for s in top}
    num, den, vol = 0.0, 0.0, {}
    for s in top:
        xs = [x for x in vecs[s] if x is not None]
        vol[s] = _std(xs) if len(xs) > 30 else None
    pairs = []
    for i, a in enumerate(top):
        for b in top[i + 1:]:
            xy = [(x, y) for x, y in zip(vecs[a], vecs[b]) if x is not None and y is not None]
            if len(xy) < 30:
                continue
            c = _corr(xy)
            if c is None:
                continue
            ww = w[a] * w[b]
            num += ww * c
            den += ww
            pairs.append((a, b, c))
    avg_corr = num / den if den else None
    # how much of the portfolio moves with bitcoin and with the Nasdaq-100 (correlation over 0.6)
    links = {}
    for ref, label in (('BTC-USD', 'bitcoin'), ('QQQM', 'Nasdaq-100')):
        rr = prices.returns(ref, start)
        movers, wsum = [], 0.0
        for s in top:
            xy = [(rets[s][d], rr[d]) for d in rets[s] if d in rr]
            c = _corr(xy) if len(xy) > 30 else None
            if c is not None and c >= 0.6 and s != ref:
                movers.append({'symbol': s, 'corr': c, 'weight': w[s]})
                wsum += w[s]
        movers.sort(key=lambda m: -m['weight'])
        links[label] = {'weight': wsum, 'symbols': movers}
    # portfolio volatility vs the average of its parts (diversification ratio)
    port = []
    for k, d in enumerate(days):
        tot_w, r = 0.0, 0.0
        for s in top:
            x = vecs[s][k]
            if x is not None:
                r += w[s] * x
                tot_w += w[s]
        if tot_w > 0.5:
            port.append(r / tot_w)
    sp = _std(port)
    wv = sum(w[s] * vol[s] for s in top if vol.get(s)) / (sum(w[s] for s in top if vol.get(s)) or 1)
    sectors = sorted(({'name': k, 'weight': v} for k, v in by_sector.items()), key=lambda x: -x['weight'])
    classes = sorted(({'name': k, 'weight': v} for k, v in by_class.items()), key=lambda x: -x['weight'])
    pairs.sort(key=lambda p: -p[2])
    return {'ready': True, 'effective_n': 1 / hhi if hhi else None, 'hhi': hhi, 'count': len(w),
            'top1': max(w.values()), 'top5': sum(sorted(w.values(), reverse=True)[:5]),
            'top10': sum(sorted(w.values(), reverse=True)[:10]),
            'sectors': sectors, 'classes': classes, 'avg_corr': avg_corr,
            'div_ratio': (wv / sp) if sp else None, 'links': links,
            'most_alike': [{'a': a, 'b': b, 'corr': c} for a, b, c in pairs[:6]]}


_SECTOR_NAMES = {'realestate': 'Real Estate', 'consumer_cyclical': 'Consumer Cyclical', 'basic_materials': 'Basic Materials',
                 'consumer_defensive': 'Consumer Defensive', 'technology': 'Technology',
                 'communication_services': 'Communication Services', 'financial_services': 'Financial Services',
                 'utilities': 'Utilities', 'industrials': 'Industrials', 'energy': 'Energy', 'healthcare': 'Healthcare'}


def _sector_name(k):
    return _SECTOR_NAMES.get(k, k.replace('_', ' ').title())


def _corr(xy):
    n = len(xy)
    if n < 3:
        return None
    mx = sum(x for x, _ in xy) / n
    my = sum(y for _, y in xy) / n
    sxy = sum((x - mx) * (y - my) for x, y in xy)
    sxx = sum((x - mx) ** 2 for x, _ in xy)
    syy = sum((y - my) ** 2 for _, y in xy)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


def _costs(accounts, today):
    t = date.fromisoformat(today)
    yr = (t - timedelta(days=365)).isoformat()
    by_month = defaultdict(float)
    tot = defaultdict(float)
    for a in accounts:
        for r in a['expense_rows']:
            if r['date'] > yr:
                tot[r['sub']] += -r['amount']
            if r['sub'] == 'margin_interest':
                by_month[r['date'][:7]] += -r['amount']
    return {'ttm': dict(tot), 'ttm_total': sum(tot.values()),
            'margin_interest_by_month': [{'month': m, 'amount': round(v, 2)} for m, v in sorted(by_month.items())]}


def _grades(perf, divers, margin, costs, value):
    g = {}
    if perf.get('ready') and perf['bench'].get('VOO'):
        diff = perf['annual'] - perf['bench']['VOO']['annual']
        span = 'a year' if perf['years'] >= 1 else f"since {perf['start']}"
        g['performance'] = {'grade': _grade(diff, [0.05, 0, -0.05, -0.10]), 'value': diff,
                            'label': 'Return vs VOO', 'text': f"{diff * 100:+.1f} points vs VOO {span}"}
        if perf['bench'].get('M2'):
            d2 = perf['annual'] - perf['bench']['M2']['annual']
            g['m2'] = {'grade': _grade(d2, [0.10, 0.05, 0, -0.05]), 'value': d2, 'label': 'Return vs M2',
                       'text': f"{d2 * 100:+.1f} points vs M2 {span}"}
        s = perf['stats'].get('sharpe')
        g['risk_adjusted'] = {'grade': _grade(s, [1.5, 1.0, 0.5, 0]), 'value': s, 'label': 'Risk-adjusted',
                              'text': f"Sharpe {s:.2f}" if s is not None else 'Needs 20+ days'}
    if divers.get('ready'):
        n, top = divers['effective_n'] or 0, divers['sectors'][0]['weight'] if divers['sectors'] else 1
        grade = 'F'
        for letter, nn, cap in (('A', 20, .35), ('B', 12, .50), ('C', 8, .60)):
            if n >= nn and top <= cap:
                grade = letter
                break
        else:
            grade = 'D' if n >= 4 else 'F'
        g['diversification'] = {'grade': grade, 'value': n, 'label': 'Diversification',
                                'text': f"{n:.1f} effective holdings, {divers['sectors'][0]['name']} {top * 100:.0f}%"}
    if margin:
        worst = min((m['drop_to_call'] for m in margin if m['drop_to_call'] is not None), default=None)
        any_debt = any(m['debt'] > 0 for m in margin)
        if not any_debt:
            g['margin'] = {'grade': 'A', 'value': None, 'label': 'Margin risk', 'text': 'No margin loan'}
        elif worst is not None:
            below = any(m['excess'] < 0 for m in margin)
            g['margin'] = {'grade': _grade(worst, [0.5, 0.35, 0.25, 0.15]), 'value': worst, 'label': 'Margin risk',
                           'text': 'Below maintenance now (estimate)' if below else f"Call after a {worst * 100:.0f}% drop"}
    if value > 0:
        c = costs['ttm_total'] / value
        g['costs'] = {'grade': _grade(c, [0.005, 0.015, 0.03, 0.05], reverse=True), 'value': c, 'label': 'Cost drag',
                      'text': f"{c * 100:.1f}% of value in interest and fees"}
    pts = {'A': 4, 'B': 3, 'C': 2, 'D': 1, 'F': 0}
    gs = [pts[x['grade']] for x in g.values() if x.get('grade')]
    overall = None
    if gs:
        avg = sum(gs) / len(gs)
        overall = 'ABCDF'[min(4, max(0, round(4 - avg)))]
    return {'items': g, 'overall': overall, 'gpa': (sum(gs) / len(gs)) if gs else None}


def _options_summary(accounts, today):
    t = date.fromisoformat(today)
    rows = [r for a in accounts for r in a['opt_realized']]
    by_month = defaultdict(lambda: {'premium': 0.0, 'pnl': 0.0, 'n': 0})
    wins = sum(1 for r in rows if r['pnl'] > 0)
    for r in rows:
        m = by_month[r['date'][:7]]
        m['pnl'] += r['pnl']
        m['n'] += 1
    short = [r for r in rows if r.get('short')]
    long_ = [r for r in rows if not r.get('short')]
    by_und = defaultdict(float)
    for r in rows:
        by_und[r['underlying'].rstrip('0123456789') or r['underlying']] += r['pnl']
    open_ = [o for a in accounts for o in a['options']]
    return {'realized': sum(r['pnl'] for r in rows), 'count': len(rows), 'win_rate': wins / len(rows) if rows else None,
            'short_pnl': sum(r['pnl'] for r in short), 'long_pnl': sum(r['pnl'] for r in long_),
            'short_count': len(short), 'long_count': len(long_),
            'by_month': [{'month': k, **v} for k, v in sorted(by_month.items())],
            'by_underlying': sorted(({'symbol': k, 'pnl': v} for k, v in by_und.items()), key=lambda x: -x['pnl']),
            'open': open_}
