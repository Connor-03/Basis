"""Replays one account's events into holdings, cost basis, cash, options, income and cash flows.

Cost basis is FIFO by lot (Robinhood's default for realized gains). Options are tracked per contract with the net cash
paid to open (positive for long, negative for short, so a short call sold for $117.95 has cost -117.95). In the daily
history an open option is carried at that cost, which means selling premium shows no gain until the contract closes or
expires; today's view marks open contracts at live quotes instead.

Anything the files cannot explain (shares sold that were never bought, contracts closed that were never opened) is
collected in result['gaps'] so the app can ask for an older export or an opening position instead of guessing.
"""
from collections import defaultdict
from datetime import date, timedelta

EPS = 1e-6


def _sort_key(ev):
    # oldest first; inside a day the export lists newest first, so a larger file position is earlier
    return (ev['date'], ev.get('import_id', 0), -ev.get('pos', 0))


class Replay:
    def __init__(self, account, opening=None, opening_cash=0.0, basis=None, price_on=None):
        """opening: list of {symbol, qty, cost (or None), date}; basis: {(symbol, date): cost_total} for transfers in,
        or a list of lots [{date, qty, cost}] carried from the account they left (original buy dates and cost);
        price_on(symbol, iso_date) -> close or None, used to value transfers in."""
        self.account = account
        self.price_on = price_on or (lambda s, d: None)
        self.basis = basis or {}
        self.lots = defaultdict(list)          # symbol -> [ {date, qty, cost, known, src} ]
        self.names = {}
        self.opts = {}                         # key -> {opt, qty, cost, opened}
        self.cash = float(opening_cash or 0)
        self.realized = []                     # stock sales
        self.opt_realized = []                 # option closes
        self.income = []                       # dividends, interest, lending, bonus
        self.expenses = []                     # margin interest, fees
        self.flows = []                        # money and securities in/out of the account
        self.trades = []                       # every buy/sell for the statement log per symbol
        self.moved_out = []                    # shares transferred out, with the lots they took (date, qty, cost)
        self.gaps = {'shares': defaultdict(lambda: {'qty': 0.0, 'first': None, 'evidence': []}),
                     'options': [], 'unknown_codes': defaultdict(int), 'basis_estimated': []}
        self.snapshots = []                    # (date, holdings{sym: qty}, cash, option_cost, flow)
        self._day_flow = 0.0
        self._assigned = {}
        self._opening_lots = [(o['symbol'], float(o['qty'])) for o in opening or [] if o.get('qty')]
        self._opening_rows = [o for o in opening or [] if o.get('qty')]
        for o in opening or []:
            if o.get('qty'):
                self.lots[o['symbol']].append({'date': o.get('date'), 'qty': float(o['qty']),
                                               'cost': None if o.get('cost') is None else float(o['cost']),
                                               'known': o.get('cost') is not None, 'src': 'opening'})

    # ---- stocks -------------------------------------------------------------------------------------------------
    def held(self, sym):
        return sum(l['qty'] for l in self.lots.get(sym, ()))

    def _add_lot(self, sym, d, qty, cost, known=True, src='buy'):
        self.lots[sym].append({'date': d, 'qty': qty, 'cost': cost, 'known': known, 'src': src})

    def _remove(self, sym, d, qty, proceeds, kind):
        """FIFO out. Returns (cost_removed, known, short_by)."""
        lots, left, cost, known, gains = self.lots[sym], qty, 0.0, True, []
        while left > EPS and lots:
            lot = lots[0]
            take = min(left, lot['qty'])
            part = (lot['cost'] or 0) * take / lot['qty'] if lot['qty'] else 0
            if lot['cost'] is None:
                known = False
            gains.append((lot['date'], take, part, lot['cost'] is not None))
            lot['qty'] -= take
            if lot['cost'] is not None:
                lot['cost'] -= part
            cost += part
            left -= take
            if lot['qty'] <= EPS:
                lots.pop(0)
        self._last_out = gains
        if kind == 'sale':
            for (ld, q, c, k) in gains:
                share = proceeds * q / qty if qty else 0
                long_term = bool(ld and d and (date.fromisoformat(d) - date.fromisoformat(ld)).days > 365)
                self.realized.append({'date': d, 'symbol': sym, 'qty': q, 'proceeds': share, 'cost': c if k else None,
                                      'gain': share - c if k else None, 'long_term': long_term, 'acquired': ld})
            if left > EPS:
                share = proceeds * left / qty if qty else 0
                self.realized.append({'date': d, 'symbol': sym, 'qty': left, 'proceeds': share, 'cost': None,
                                      'gain': None, 'long_term': None, 'acquired': None})
        return cost, known, max(left, 0.0)

    def _gap(self, sym, d, qty, why):
        g = self.gaps['shares'][sym]
        qty = round(qty, 4)
        g['qty'] = max(g['qty'], qty) if why == 'dividend' else g['qty'] + qty
        g['first'] = min(g['first'] or d, d)
        if len(g['evidence']) < 6:
            g['evidence'].append({'date': d, 'qty': round(qty, 6), 'why': why})

    # ---- options ------------------------------------------------------------------------------------------------
    def _opt_trade(self, ev):
        opt, n, amt = ev['option'], ev['qty'] or 0, ev['amount']
        signed = n if ev['side'] == 'buy' else -n
        pos = self.opts.get(opt['key'])
        if pos is None or abs(pos['qty']) < EPS or (pos['qty'] > 0) == (signed > 0):
            if pos is None or abs(pos['qty']) < EPS:
                pos = self.opts[opt['key']] = {'opt': opt, 'qty': 0.0, 'cost': 0.0, 'opened': ev['date']}
            if ev['code'] in ('BTC', 'STC'):
                # closing something the files never opened
                self.gaps['options'].append({'date': ev['date'], 'contract': opt['key'], 'code': ev['code'], 'qty': n,
                                             'amount': amt})
                self.opt_realized.append({'date': ev['date'], 'key': opt['key'], 'underlying': opt['underlying'],
                                          'qty': n, 'pnl': amt, 'premium': amt if amt > 0 else 0,
                                          'how': 'closed (opened before your files)', 'known': False, 'short': ev['code'] == 'BTC'})
                if abs(pos['qty']) < EPS:
                    del self.opts[opt['key']]
                return
            pos['qty'] += signed
            pos['cost'] += -amt
            return
        self._opt_close(opt['key'], n, amt, ev['date'], 'bought back' if ev['side'] == 'buy' else 'sold')

    def _assignment_premium(self, e):
        """Find the contract behind an assignment/exercise trade, close it into the stock, return its signed cost."""
        sym, px, n = e['symbol'], e['price'], (e['qty'] or 0) / 100.0
        rights = ('P', 'C') if e['side'] == 'buy' else ('C', 'P')     # (short leg, long leg)
        best = None
        for key, p in self.opts.items():
            o = p['opt']
            if o['underlying'].rstrip('0123456789') != sym or px is None or abs(o['strike'] - px) > 0.005:
                continue
            want = rights[0] if p['qty'] < 0 else rights[1]
            if o['right'] != want:
                continue
            if best is None or o['expiry'] < best[1]['opt']['expiry']:
                best = (key, p)
        if not best:
            return 0.0
        key, p = best
        take = min(n, abs(p['qty']))
        part = p['cost'] * take / abs(p['qty'])
        short = p['qty'] < 0
        self.opt_realized.append({'date': e['date'], 'key': key, 'underlying': p['opt']['underlying'], 'qty': take,
                                  'pnl': -part, 'premium': -part if short else 0, 'how': e['sub'] + ' (in stock basis)',
                                  'known': True, 'short': short, 'opened': p['opened'], 'right': p['opt']['right'],
                                  'to_basis': True})
        p['cost'] -= part
        p['qty'] += take if short else -take
        if abs(p['qty']) < EPS:
            del self.opts[key]
        self._assigned[key] = self._assigned.get(key, 0) + take
        return part

    def _opt_close(self, key, n, amt, d, how):
        if how in ('assigned', 'exercised') and self._assigned.get(key, 0) > EPS:
            used = min(n, self._assigned[key])
            self._assigned[key] -= used
            n -= used
            if n <= EPS:
                return
        pos = self.opts.get(key)
        if not pos or abs(pos['qty']) < EPS:
            if how not in ('bought back', 'sold'):
                self.gaps['options'].append({'date': d, 'contract': key, 'code': how, 'qty': n, 'amount': amt})
            return
        n = min(n, abs(pos['qty']))
        frac = n / abs(pos['qty'])
        part = pos['cost'] * frac
        short = pos['qty'] < 0
        self.opt_realized.append({'date': d, 'key': key, 'underlying': pos['opt']['underlying'], 'qty': n,
                                  'pnl': amt - part, 'premium': -part if short else 0, 'how': how, 'known': True,
                                  'short': short, 'opened': pos['opened'], 'right': pos['opt']['right']})
        pos['cost'] -= part
        pos['qty'] += n if short else -n
        if abs(pos['qty']) < EPS:
            del self.opts[key]

    def option_cost(self):
        return sum(p['cost'] for p in self.opts.values())

    # ---- replay -------------------------------------------------------------------------------------------------
    def run(self, events, splits=None):
        events = [e for e in events if e and e.get('date')]
        if splits:
            # Yahoo's split history, applied where the broker did not report the split itself (Robinhood through
            # SnapTrade reports none; Robinhood CSVs and Fidelity do)
            explicit = defaultdict(list)
            for e in events:
                if e['kind'] in ('split', 'split_units'):
                    explicit[e['symbol']].append(date.fromisoformat(e['date']))
            syms = {e['symbol'] for e in events if e.get('symbol') and e['kind'] in ('trade', 'transfer_sec')}
            syms |= {o['symbol'] for o in self._opening_rows}
            first = min(e['date'] for e in events) if events else None
            for sym in syms:
                for d, ratio in splits.get(sym, ()):
                    if not first or d < first or not ratio or ratio == 1:
                        continue
                    if any(abs((x - date.fromisoformat(d)).days) <= 7 for x in explicit.get(sym, ())):
                        continue
                    events.append({'date': d, 'settle': d, 'code': 'YSPLIT', 'symbol': sym, 'qty': ratio, 'price': None,
                                   'amount': 0.0, 'desc': f'Split {ratio:g} for 1 (from market data)', 'kind': 'ysplit',
                                   'sub': 'split', 'pos': 10 ** 9, 'import_id': -1})
        events = sorted(events, key=_sort_key)
        splits = defaultdict(dict)        # (date, sym) -> {'removed': q, 'added': q}
        for e in events:
            if e['kind'] == 'split':
                splits[(e['date'], e['symbol'])]['removed' if e.get('removed') else 'added'] = e['qty'] or 0
        self._ca, self._ca_done = defaultdict(list), set()
        for e in events:
            if e['kind'] == 'option_ca':
                self._ca[e['date']].append(e)
        div_checks = []
        day = None
        for e in events:
            if day is not None and e['date'] != day:
                self._snapshot(day)
            day = e['date']
            self._apply(e, splits, div_checks)
        if day is not None:
            self._snapshot(day)
        self._dividend_evidence(events, div_checks)
        return self

    def _snapshot(self, d):
        hold = {}
        for s, lots in self.lots.items():
            q = sum(l['qty'] for l in lots)
            if abs(q) > EPS:
                hold[s] = round(q, 8)
        self.snapshots.append((d, hold, self.cash, self.option_cost(), self._day_flow))
        self._day_flow = 0.0

    def _flow(self, d, amount, sub, desc, symbol=None, qty=None):
        self.flows.append({'date': d, 'amount': amount, 'sub': sub, 'desc': desc, 'symbol': symbol, 'qty': qty})
        self._day_flow += amount

    def _apply(self, e, splits, div_checks):
        k, d, sym, amt = e['kind'], e['date'], e['symbol'], e['amount'] or 0.0
        if k not in ('transfer_sec', 'split', 'option_ca', 'split_units', 'ysplit'):
            self.cash += amt
        if e.get('name') and sym:
            self.names.setdefault(sym, e['name'])

        if k == 'trade':
            q = e['qty'] or 0
            self.trades.append({'date': d, 'symbol': sym, 'side': e['side'], 'qty': q, 'price': e['price'],
                                'amount': amt, 'sub': e['sub']})
            adj = self._assignment_premium(e) if e['sub'] in ('assignment', 'exercise') else 0.0
            if e['side'] == 'buy':
                # an assigned short put lowers the basis of the shares it delivered (a long call's cost raises it)
                self._add_lot(sym, d, q, -amt + adj, True, e['sub'])
            else:
                # an assigned short call adds its premium to the sale (a long put's cost reduces it)
                _, _, short = self._remove(sym, d, q, amt - adj, 'sale')
                if short > EPS:
                    self._gap(sym, d, short, 'sold')
        elif k == 'option_trade':
            self._opt_trade(e)
        elif k == 'option_close':
            self._opt_close(e['option']['key'], e['qty'] or 0, 0.0, d, e['sub'])
        elif k == 'option_ca':
            # adjusted contracts after a split (BYND -> BYND1). Which row is the old contract depends on long/short,
            # so the old one is whichever key is open right now; the rest of the day's rows are the new contracts.
            if d not in self._ca_done:
                self._ca_done.add(d)
                group = self._ca.get(d, [])
                olds = [g for g in group if g['option']['key'] in self.opts]
                news = [g for g in group if g['option']['key'] not in self.opts]
                for o in olds:
                    oo = o['option']
                    match = next((n for n in news if (n['option']['expiry'], n['option']['right'], n['option']['strike'])
                                  == (oo['expiry'], oo['right'], oo['strike'])), news[0] if news else None)
                    if match:
                        news.remove(match)
                        pos = self.opts.pop(oo['key'])
                        pos['opt'] = match['option']
                        self.opts[match['option']['key']] = pos
        elif k == 'split':
            sp = splits.get((d, sym), {})
            if not e.get('removed') and sp.get('removed'):
                ratio = sp['added'] / sp['removed']
                for lot in self.lots.get(sym, ()):
                    lot['qty'] *= ratio
        elif k == 'ysplit':
            for lot in self.lots.get(sym, ()):
                lot['qty'] *= e['qty']
            g = self.gaps['shares'].get(sym)
            if g and g['qty']:
                g['qty'] *= e['qty']
        elif k == 'split_units':
            held = self.held(sym)
            if held > EPS:
                ratio = (held + (e['qty'] or 0)) / held
                for lot in self.lots.get(sym, ()):
                    lot['qty'] *= ratio
            elif (e['qty'] or 0) > 0:
                self._add_lot(sym, d, e['qty'], 0.0, True, 'split')
        elif k == 'transfer_sec':
            q = e['qty'] or 0
            px = self.price_on(sym, d)
            if q > 0:
                given = self.basis.get((sym, d))
                if isinstance(given, list):
                    # lots carried from another of your accounts: keep their buy dates and cost
                    for lot in given:
                        self._add_lot(sym, lot['date'] or d, lot['qty'], lot['cost'], lot['cost'] is not None, e['sub'])
                else:
                    cost = given if given is not None else (px * q if px else None)
                    self._add_lot(sym, d, q, cost, given is not None, e['sub'])
                    if given is None and e['sub'] != 'pending':
                        self.gaps['basis_estimated'].append({'symbol': sym, 'date': d, 'qty': q, 'price': px})
            else:
                self._remove(sym, d, -q, 0, 'transfer')
                self.moved_out.append({'date': d, 'symbol': sym, 'qty': -q, 'sub': e['sub'],
                                       'lots': [{'date': ld, 'qty': lq, 'cost': lc if k else None}
                                                for ld, lq, lc, k in self._last_out]})
            self.trades.append({'date': d, 'symbol': sym, 'side': 'in' if q > 0 else 'out', 'qty': abs(q),
                                'price': px, 'amount': 0.0, 'sub': e['sub']})
            self._flow(d, (px or 0) * q, e['sub'], e['desc'], sym, q)
        elif k == 'income':
            self.income.append({'date': d, 'symbol': sym, 'amount': amt, 'sub': e['sub'],
                                'pay_date': e.get('pay_date'), 'record_date': e.get('record_date'),
                                'shares': e.get('div_shares'), 'rate': e.get('div_rate')})
            if e.get('record_date') and sym:
                div_checks.append(e)
        elif k == 'expense':
            self.expenses.append({'date': d, 'amount': amt, 'sub': e['sub'], 'desc': e['desc']})
        elif k == 'flow':
            self._flow(d, amt, e['sub'], e['desc'])
        else:
            if e.get('code'):
                self.gaps['unknown_codes'][e['code']] += 1

    def _dividend_evidence(self, events, div_checks):
        """A dividend row says how many shares were held on the record date. If that is more than the files can
        account for, the difference was held before the first file starts."""
        by = defaultdict(float)
        for e in div_checks:
            by[(e['symbol'], e['record_date'])] += e.get('div_shares') or 0
        if not by:
            return
        trades = sorted((e for e in events if e['kind'] in ('trade', 'transfer_sec')), key=_sort_key)
        opening = defaultdict(float)
        for lot in self._opening_lots:
            opening[lot[0]] += lot[1]
        for (sym, rd), shares in sorted(by.items(), key=lambda x: x[0][1]):
            held = opening.get(sym, 0.0)
            for e in trades:
                if e['symbol'] != sym:
                    continue
                settled = e.get('settle') or e['date']
                if settled > rd:
                    break
                q = e['qty'] or 0
                held += q if (e['kind'] == 'transfer_sec' or e.get('side') == 'buy') else -q
            g = self.gaps['shares'].get(sym)
            already = g['qty'] if g else 0
            missing = shares - held
            if missing > already + 1e-4 and missing > 1e-4:
                self._gap(sym, rd, missing, 'dividend')

    # ---- outputs ------------------------------------------------------------------------------------------------
    def holdings(self):
        out = {}
        for sym, lots in self.lots.items():
            q = sum(l['qty'] for l in lots)
            if q <= EPS:
                continue
            known = [l for l in lots if l['cost'] is not None]
            kq = sum(l['qty'] for l in known)
            cost = sum(l['cost'] for l in known)
            out[sym] = {'symbol': sym, 'qty': q, 'cost': cost if kq >= q - 1e-6 else None,
                        'cost_known_qty': kq, 'cost_known': cost, 'avg_cost': cost / kq if kq > EPS else None,
                        'first': min((l['date'] for l in lots if l['date']), default=None),
                        'lots': [dict(l) for l in lots], 'name': self.names.get(sym, '')}
        return out

    def open_options(self):
        return [{'key': k, **p['opt'], 'qty': p['qty'], 'cost': p['cost'], 'opened': p['opened']}
                for k, p in self.opts.items()]

    def gap_list(self):
        sh = [{'symbol': s, **g} for s, g in self.gaps['shares'].items() if g['qty'] > 1e-4]
        sh.sort(key=lambda g: g['first'] or '')
        return {'shares': sh, 'options': self.gaps['options'], 'unknown_codes': dict(self.gaps['unknown_codes']),
                'basis_estimated': self.gaps['basis_estimated']}


def daterange(a, b):
    d, e = date.fromisoformat(a), date.fromisoformat(b)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)
