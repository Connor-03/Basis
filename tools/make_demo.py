"""Builds tools/demo_data.json: a made-up portfolio for tests and screenshots.

Nobody's real account: three accounts at a pretend "Demo Brokerage" with monthly deposits and buys since mid-2024,
priced at each day's real closing price (from Yahoo, fetched when this is run). tools/mock_snaptrade.py serves the
result in SnapTrade's shapes, so the program can be set up against it exactly as against the real service.

    python3 tools/make_demo.py
"""
import json
import os
import random
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fin.market import Yahoo  # noqa: E402

START = '2024-07-01'
INDIVIDUAL = ['NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'PLTR', 'AMD', 'MSTR', 'COIN', 'HOOD', 'VOO', 'QQQM', 'SCHD', 'COST']
ROTH = ['VOO', 'QQQM', 'SCHD', 'AAPL']
CRYPTO = {'BTC': 'BTC-USD', 'ETH': 'ETH-USD'}


def main():
    rng = random.Random(21)
    y = Yahoo()
    hist, names = {}, {}
    for sym in sorted(set(INDIVIDUAL + ROTH) | set(CRYPTO.values())):
        h = y.history(sym, START)
        if any(d >= START for d, _ in h['splits']):
            print('skipping', sym, '(split in the window)')
            continue
        hist[sym] = {'px': {d: c for d, c, _ in h['bars']}, 'divs': h['divs']}
        names[sym] = h.get('name') or sym
        print(sym, len(h['bars']), 'days')
    days = sorted(hist['VOO']['px'])
    accts = {
        'demo-individual': {'display_name': 'Individual', 'raw_type': 'Margin', 'cash_or_margin': 'margin', 'number': '****4417'},
        'demo-roth': {'display_name': 'Roth IRA', 'raw_type': 'ROTH IRA', 'cash_or_margin': 'cash', 'number': '****9026'},
        'demo-crypto': {'display_name': 'Crypto', 'raw_type': 'CRYP', 'cash_or_margin': 'cash', 'number': '****5530'},
    }
    cash = {a: 0.0 for a in accts}
    units = {a: {} for a in accts}
    cost = {a: {} for a in accts}
    acts = {a: [] for a in accts}
    n = [0]

    def add(acct, d, typ, amount, sym=None, qty=None, price=None, desc='', crypto=False):
        n[0] += 1
        a = {'id': f'demo-{n[0]:05d}', 'type': typ, 'trade_date': d + 'T15:30:00.000000Z', 'settlement_date': d + 'T15:30:00.000000Z',
             'amount': round(amount, 2), 'fee': 0, 'units': qty, 'price': price, 'description': desc, 'option_symbol': None,
             'currency': {'code': 'USD'}, 'institution': 'Demo Brokerage', 'symbol': None}
        if sym:
            a['symbol'] = {'symbol': sym, 'raw_symbol': sym, 'description': names.get(CRYPTO.get(sym, sym), sym),
                           'type': {'code': 'crypto' if crypto else 'cs'}}
        acts[acct].append(a)
        cash[acct] += amount

    def trade(acct, d, sym, dollars, crypto=False):
        px = hist[CRYPTO[sym] if crypto else sym]['px'].get(d)
        if not px or abs(dollars) < 5:
            return
        held = units[acct].get(sym, 0.0)
        q = round(dollars / px, 6 if crypto else 4)
        if q < 0:
            q = -min(-q, held)
            if not q:
                return
            cost[acct][sym] = cost[acct].get(sym, 0.0) * (held + q) / held
        else:
            cost[acct][sym] = cost[acct].get(sym, 0.0) + q * px
        units[acct][sym] = round(held + q, 6)
        add(acct, d, 'BUY' if q > 0 else 'SELL', -q * px, sym, abs(q), round(px, 4),
            f"{'Bought' if q > 0 else 'Sold'} {abs(q):g} {sym}", crypto)

    ind = [s for s in INDIVIDUAL if s in hist]
    roth = [s for s in ROTH if s in hist]
    month, k = None, 0
    paid = set()
    for d in days:
        # dividends, paid ten days after the ex date on what was held then
        for acct, syms in (('demo-individual', ind), ('demo-roth', roth)):
            for sym in syms:
                for ex, amt in hist[sym]['divs']:
                    pay = (date.fromisoformat(ex) + timedelta(days=10)).isoformat()
                    if pay <= d and (acct, sym, ex) not in paid and ex >= START:
                        paid.add((acct, sym, ex))
                        held = units[acct].get(sym, 0.0)
                        if held * amt >= 0.01:
                            add(acct, d, 'DIVIDEND', held * amt, sym, None, None, f'{sym} dividend')
        if d[:7] == month or int(d[8:]) < 5:
            continue
        month, k = d[:7], k + 1
        add('demo-individual', d, 'CONTRIBUTION', 2000, desc='Deposit from bank')
        add('demo-roth', d, 'CONTRIBUTION', 550, desc='IRA contribution')
        add('demo-crypto', d, 'CONTRIBUTION', 300, desc='Deposit from bank')
        borrow = 1400 if d >= '2026-01-01' else 0             # this investor starts using margin in 2026
        if cash['demo-individual'] < -100:
            add('demo-individual', d, 'FEE', cash['demo-individual'] * 0.06 / 12, desc='Margin interest')
        picks = rng.sample(ind, 3)
        spend = 2000 + borrow if borrow else max(cash['demo-individual'], 0)      # the loan grows month by month
        for sym, w in zip(picks, (0.45, 0.33, 0.22)):
            trade('demo-individual', d, sym, spend * w)
        if k % 5 == 0:                                        # now and then, trim a third of something held
            held = [s for s in ind if units['demo-individual'].get(s, 0) > 0]
            if held:
                sym = rng.choice(held)
                trade('demo-individual', d, sym, -units['demo-individual'][sym] * hist[sym]['px'][d] / 3)
        two = rng.sample(roth, 2)
        trade('demo-roth', d, two[0], cash['demo-roth'] / 2)
        trade('demo-roth', d, two[1], cash['demo-roth'])
        trade('demo-crypto', d, 'BTC', cash['demo-crypto'] * 0.7, crypto=True)
        trade('demo-crypto', d, 'ETH', cash['demo-crypto'], crypto=True)
    last = days[-1]
    out = {'made': date.today().isoformat(), 'brokerage': 'Demo Brokerage', 'accounts': []}
    for aid, meta in accts.items():
        pos = []
        for sym, q in sorted(units[aid].items()):
            if q <= 1e-9:
                continue
            crypto = sym in CRYPTO
            px = hist[CRYPTO[sym] if crypto else sym]['px'][last] if not crypto else list(hist[CRYPTO[sym]]['px'].values())[-1]
            pos.append({'instrument': {'kind': 'crypto' if crypto else 'equity', 'symbol': sym, 'raw_symbol': sym,
                                       'description': names.get(CRYPTO.get(sym, sym), sym)},
                        'units': str(q), 'price': str(round(px, 4)), 'cost_basis': str(round(cost[aid][sym] / q, 4)),
                        'cash_equivalent': False, 'currency': 'USD'})
        out['accounts'].append({'id': aid, **meta, 'cash': round(cash[aid], 2), 'positions': pos, 'activities': acts[aid]})
        print(aid, 'cash', round(cash[aid], 2), 'positions', len(pos), 'activities', len(acts[aid]))
    with open(os.path.join(HERE, 'demo_data.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'))


if __name__ == '__main__':
    main()
