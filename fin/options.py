"""The weekly options menu: covered calls on 100-share lots and cash-secured puts at support, Monday-for-Friday by
default, plus 30-45 day calls and collars for the biggest holdings. Built from Yahoo's live chains (bid/ask/IV) and
the console's own positions, cost basis and price history. No AI: every pick follows the rules below, and the rules
are the owner's own (editable in Settings).

Premiums: 'quote' = bid/ask mid, 'last' = last trade (market closed), 'estimate' = Black-Scholes from the chain's IV.
"""
import math
import time
from datetime import date, datetime, timedelta, timezone

from .market import eastern_now

DEFAULT_RULES = {
    'target_low': 1.0, 'target_high': 1.5,          # % return on collateral per week
    'watch': [],                                    # tickers you do not hold but want on the menu (Settings)
    'macro_dates': [],                              # FOMC / CPI / jobs days you add, YYYY-MM-DD
    'symbols': {},                                  # per-stock rules, e.g. {'MSTR': {'no_puts': True, 'note': '...'}}
}


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(s, k, t, iv, r, right):
    """Black-Scholes price and delta (delta as a positive number for both calls and puts)."""
    if not (s and k and t > 0 and iv and iv > 0):
        return None, None
    d1 = (math.log(s / k) + (r + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if right == 'C':
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2), _ncdf(d1)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1), _ncdf(-d1)


def target_friday(now=None):
    """Monday to Thursday: this Friday. Friday after the close and weekends: next Friday."""
    n = now or eastern_now()
    wd = n.weekday()
    days = (4 - wd) % 7
    if wd == 4 and n.hour * 60 + n.minute >= 960:
        days = 7
    if wd >= 5:
        days = (4 - wd) % 7
    return (n.date() + timedelta(days=days))


class Menu:
    def __init__(self, yahoo, db):
        self.y = yahoo
        self.db = db
        self.cache = {}

    def _chain(self, sym, exp_date=None, max_age=600):
        key = (sym, exp_date)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        base = self.cache.get((sym, None))
        if not base or time.time() - base[0] > max_age:
            base = (time.time(), self.y.options(sym))
            self.cache[(sym, None)] = base
        if exp_date is None:
            return base[1]
        exps = base[1]['expirations']
        want = int(datetime(exp_date.year, exp_date.month, exp_date.day, tzinfo=timezone.utc).timestamp())
        if want not in exps:
            return None
        ch = base[1] if base[1].get('expiration') == want else self.y.options(sym, want)
        self.cache[key] = (time.time(), ch)
        return ch

    def _expiry_near(self, sym, lo_days, hi_days, today):
        base = self._chain(sym)
        best = None
        for e in base['expirations']:
            d = datetime.fromtimestamp(e, timezone.utc).date()
            dd = (d - today).days
            if lo_days <= dd <= hi_days and (best is None or d.weekday() == 4 and best.weekday() != 4 or
                                             abs(dd - 38) < abs((best - today).days - 38)):
                best = d
        return best

    def build(self, model, quotes, prices, rules=None):
        rules = {**DEFAULT_RULES, **(rules or {})}
        sym_rules = {**DEFAULT_RULES['symbols'], **(rules.get('symbols') or {})}
        today = eastern_now().date()
        fri = target_friday()
        t_years = max((fri - today).days, 0.5) / 365.0 + (6.5 / 24 / 365 if fri == today else 0)
        r = ((quotes.get('^IRX') or {}).get('regularMarketPrice') or 4.0) / 100
        lo, hi = rules['target_low'] / 100, rules['target_high'] / 100
        state = (quotes.get('VOO') or {}).get('marketState') or 'CLOSED'
        macro = [d for d in rules.get('macro_dates') or [] if today.isoformat() <= d <= fri.isoformat()]

        positions = {p['symbol']: p for p in model['positions']}
        open_short_calls = {}
        for o in model['options']['open']:
            if o.get('status') == 'open' and o['qty'] < 0 and o['right'] == 'C':
                open_short_calls[o['underlying']] = open_short_calls.get(o['underlying'], 0) + int(-o['qty'])
        calls, puts, longs, skips, notes = [], [], [], [], []
        stock_value = sum(p['value'] for p in model['positions'] if p['class'] != 'Crypto') or 1

        def tech(sym):
            b = prices.bars.get(sym)
            if not b or len(b[1]) < 30:
                return None
            c = b[1]
            px = (quotes.get(sym) or {}).get('regularMarketPrice') or c[-1]
            sma = lambda n: sum(c[-n:]) / n if len(c) >= n else None
            # swing lows that formed before the last week, so a level is one the stock came back to, not today's low
            return {'px': px, 'chg5': px / c[-6] - 1 if len(c) > 6 else 0, 'hi20': max(c[-20:]),
                    'lo20': min(c[-30:-5]) if len(c) > 35 else min(c[:-5]),
                    'lo60': min(c[-65:-5]) if len(c) > 70 else min(c[:-5]),
                    'last3': min(c[-3:]), 'sma50': sma(50), 'sma200': sma(200)}

        def earnings_inside(sym):
            ts = (quotes.get(sym) or {}).get('earningsTimestamp')
            if not ts:
                return None
            d = datetime.fromtimestamp(ts, timezone.utc).date()
            return d if today <= d <= fri else None

        def price_of(opt, s, right, tyrs):
            bid, ask, last = opt.get('bid') or 0, opt.get('ask') or 0, opt.get('lastPrice') or 0
            iv = opt.get('impliedVolatility') or 0
            est, delta = bs(s, opt['strike'], tyrs, iv if iv > 0.05 else None, r, right)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2, 'quote', delta, iv, bid, ask
            if est is not None and iv > 0.05:
                return est, 'estimate', delta, iv, bid, ask
            return last, 'last', delta, iv, bid, ask

        def row(sym, right, opt, exp, s, prem, kind, delta, iv, collateral, contracts, reason, extra=None):
            ret = prem * 100 / collateral if collateral else None
            days = max((exp - today).days, 1)
            return {'symbol': sym, 'right': right, 'strike': opt['strike'], 'expiry': exp.isoformat(), 'premium': prem,
                    'premium_kind': kind, 'bid': opt.get('bid'), 'ask': opt.get('ask'), 'delta': delta, 'iv': iv,
                    'contracts': contracts, 'credit': prem * 100 * contracts, 'collateral': collateral * 1.0,
                    'return_pct': ret, 'week': (ret / max(days / 7.0, 1.0)) if ret is not None else None, 'distance': opt['strike'] / s - 1, 'reason': reason, 'price': s,
                    'oi': opt.get('openInterest'), 'contract': opt.get('contractSymbol'), **(extra or {})}

        # ---- covered calls ------------------------------------------------------------------------------------
        for sym, p in sorted(positions.items(), key=lambda kv: -kv[1]['value']):
            if p['qty'] < 100 or p['class'] == 'Crypto':
                continue
            rule = sym_rules.get(sym, {})
            free = int((p['qty'] - (rule.get('keep_uncovered') or 0)) // 100) - open_short_calls.get(sym, 0)
            if rule.get('max_contracts') is not None:
                free = min(free, rule['max_contracts'] - open_short_calls.get(sym, 0))
            if free <= 0:
                skips.append({'symbol': sym, 'side': 'call', 'reason': 'All eligible shares already covered or held back'})
                continue
            basis = p.get('avg_cost') or (p['cost'] / p['qty'] if p.get('cost') else None)
            tch = tech(sym)
            s = (quotes.get(sym) or {}).get('regularMarketPrice') or (tch or {}).get('px')
            try:
                ch = self._chain(sym, fri)
            except Exception as e:
                skips.append({'symbol': sym, 'side': 'call', 'reason': f'Chain unavailable ({str(e)[:40]})'})
                continue
            if not ch:
                skips.append({'symbol': sym, 'side': 'call', 'reason': f'No {fri.month}/{fri.day} weekly expiry'})
            else:
                er = earnings_inside(sym)
                if er:
                    skips.append({'symbol': sym, 'side': 'call', 'reason': f'Earnings {er.month}/{er.day} inside the contract: wait'})
                elif tch and tch['chg5'] <= -0.08:
                    skips.append({'symbol': sym, 'side': 'call',
                                  'reason': f"Down {tch['chg5'] * -100:.0f}% in 5 days: wait for a rebound, don't sell the bounce for pennies"})
                else:
                    opts = [o for o in ch['calls'] if o['strike'] > s * 0.995]
                    priced = [(o, *price_of(o, s, 'C', t_years)) for o in opts]
                    priced = [x for x in priced if x[1] and x[1] >= 0.02]
                    underwater = basis is not None and s < basis
                    coll = s * 100
                    picks = []
                    if basis is not None:
                        safe = next((x for x in priced if x[0]['strike'] >= basis), None)
                        if safe:
                            picks.append(('safe', safe))
                    if not rule.get('calls_above_basis') or not underwater:
                        cands = [x for x in priced if (x[3] or 0) <= 0.35 and x[1] * 100 / coll >= lo
                                 and (not rule.get('calls_above_basis') or basis is None or x[0]['strike'] >= basis)]
                        tgt = max(cands, key=lambda x: x[0]['strike']) if cands else \
                            min(priced, key=lambda x: abs((x[3] or 0) - 0.28), default=None)
                        if tgt and (not picks or tgt[0]['strike'] != picks[0][1][0]['strike']):
                            picks.append(('target', tgt))
                    for kind, (o, prem, pk, delta, iv, bid, ask) in picks:
                        ret = prem * 100 / coll
                        why = []
                        if kind == 'safe':
                            why.append(f"at or above your ${basis:.2f} basis")
                        else:
                            why.append(f"{delta:.2f} delta" if delta else 'nearest to 0.28 delta')
                        if ret < lo:
                            why.append(f"under the {rules['target_low']:g}% target")
                        lock = (o['strike'] - basis) * 100 * free if basis is not None and o['strike'] < basis else None
                        if lock is not None:
                            why.append(f"locks in ${-lock:,.0f} loss if called")
                        if rule.get('note') and kind == 'safe':
                            why.append(rule['note'])
                        calls.append(row(sym, 'C', o, fri, s, prem, pk, delta, iv, coll, free, '; '.join(why),
                                         {'pick': kind, 'basis': basis, 'locked_loss': lock, 'iv_note': iv}))
            # longer calls and a collar for big holdings
            weight = p['value'] / stock_value
            if rule.get('long_dated') or weight >= 0.10:
                try:
                    exp = self._expiry_near(sym, 28, 50, today)
                    ch2 = self._chain(sym, exp) if exp else None
                except Exception:
                    ch2 = None
                if ch2:
                    ty = max((exp - today).days, 1) / 365.0
                    pc = [(o, *price_of(o, s, 'C', ty)) for o in ch2['calls'] if o['strike'] > s]
                    pc = [x for x in pc if x[1] and x[1] >= 0.05]
                    chosen = []
                    if basis is not None:
                        b1 = next((x for x in pc if x[0]['strike'] >= basis), None)
                        if b1:
                            chosen.append(('at basis', b1))
                    far = min(pc, key=lambda x: abs((x[3] or 0) - 0.18), default=None)
                    if far and all(far[0]['strike'] != c[1][0]['strike'] for c in chosen) and \
                            (basis is None or far[0]['strike'] >= basis or not rule.get('calls_above_basis')):
                        chosen.append(('higher strike', far))
                    n = max(free, 1)
                    for label, (o, prem, pk, delta, iv, bid, ask) in chosen:
                        longs.append(row(sym, 'C', o, exp, s, prem, pk, delta, iv, s * 100, n,
                                         f"{(exp - today).days}-day call, {label}; about {prem * 100 / (s * 100) / ((exp - today).days / 7) * 100:.2f}% a week",
                                         {'strategy': 'call', 'days': (exp - today).days}))
                    # collar: sell ~0.25 delta call, buy ~0.20 delta put, same expiry
                    if weight >= 0.10:
                        pp = [(o, *price_of(o, s, 'P', ty)) for o in ch2['puts'] if o['strike'] < s]
                        put = min(pp, key=lambda x: abs((x[3] or 0) - 0.20), default=None)
                        cl = min([x for x in pc if basis is None or x[0]['strike'] >= basis or not rule.get('calls_above_basis')],
                                 key=lambda x: abs((x[3] or 0) - 0.25), default=None)
                        if put and cl:
                            net = cl[1] - put[1]
                            longs.append({'symbol': sym, 'strategy': 'collar', 'expiry': exp.isoformat(), 'days': (exp - today).days,
                                          'call_strike': cl[0]['strike'], 'put_strike': put[0]['strike'], 'call_premium': cl[1],
                                          'put_premium': put[1], 'net': net, 'contracts': n, 'credit': net * 100 * n,
                                          'premium_kind': 'quote' if cl[2] == put[2] == 'quote' else 'estimate',
                                          'floor': put[0]['strike'] / s - 1, 'cap': cl[0]['strike'] / s - 1, 'price': s,
                                          'reason': f"{weight * 100:.0f}% of your stock value. Protects below ${put[0]['strike']:g} "
                                                    f"({(put[0]['strike'] / s - 1) * 100:.0f}%) and caps at ${cl[0]['strike']:g} for "
                                                    f"{'a credit' if net >= 0 else 'a cost'} of ${abs(net) * 100 * n:,.0f}"})

        # ---- cash-secured puts at support -------------------------------------------------------------------
        watch = list(dict.fromkeys(rules.get('watch') or []))
        for sym in watch:
            rule = sym_rules.get(sym, {})
            if rule.get('no_puts'):
                skips.append({'symbol': sym, 'side': 'put', 'reason': 'Your rule: no puts' + (f" ({rule['note']})" if rule.get('note') else '')})
                continue
            tch = tech(sym)
            if not tch:
                skips.append({'symbol': sym, 'side': 'put', 'reason': 'Not enough price history yet'})
                continue
            s = tch['px']
            er = earnings_inside(sym)
            levels = [(tch['sma50'], '50-day average'), (tch['sma200'], '200-day average'), (tch['lo20'], 'swing low'),
                      (tch['lo60'], '60-day swing low')]
            below = sorted([(v, n) for v, n in levels if v and v <= s * 1.001], key=lambda x: -x[0])
            near = below[0] if below else None
            new_low = s < tch['lo60']
            dipped = s <= tch['hi20'] * 0.95
            if er:
                skips.append({'symbol': sym, 'side': 'put', 'reason': f'Earnings {er.month}/{er.day} inside the contract: wait'})
                continue
            if tch['chg5'] >= 0.10:
                skips.append({'symbol': sym, 'side': 'put', 'reason': f"Up {tch['chg5'] * 100:.0f}% in 5 days: wait for a pullback"})
                continue
            if new_low and not (tch['sma200'] and tch['sma200'] < s and tch['sma200'] >= s * 0.92):
                skips.append({'symbol': sym, 'side': 'put', 'reason': 'New 60-day low with no support close underneath: falling knife'})
                continue
            if not near:
                skips.append({'symbol': sym, 'side': 'put', 'reason': 'No support level below the price'})
                continue
            at_support = dipped and near[0] >= s * 0.96 and tch['last3'] >= near[0] * 0.99
            try:
                ch = self._chain(sym, fri)
            except Exception as e:
                skips.append({'symbol': sym, 'side': 'put', 'reason': f'Chain unavailable ({str(e)[:40]})'})
                continue
            if not ch:
                skips.append({'symbol': sym, 'side': 'put', 'reason': f'No {fri.month}/{fri.day} weekly expiry'})
                continue
            pp = [(o, *price_of(o, s, 'P', t_years)) for o in ch['puts'] if o['strike'] <= near[0] * 1.002]
            pp = [x for x in pp if x[1] and x[1] >= 0.02]
            if not pp:
                skips.append({'symbol': sym, 'side': 'put', 'reason': f"Nothing bid at or under the {near[1]} (${near[0]:.2f})"})
                continue
            hit = [x for x in pp if x[1] / x[0]['strike'] >= lo]
            o, prem, pk, delta, iv, bid, ask = max(hit or pp, key=lambda x: x[0]['strike'])
            ret = prem / o['strike']
            why = [f"{near[1]} ${near[0]:.2f}" + (', dipped to it and held' if at_support else '')]
            if not hit:
                why.append(f"best at support is {ret * 100:.2f}%, under the {rules['target_low']:g}% target")
            if iv:
                why.append(f"IV {iv * 100:.0f}%")
            own = positions.get(sym)
            if own:
                why.append(f"you own {own['qty']:g}" + (f" @ ${own['avg_cost']:.2f}" if own.get('avg_cost') else ''))
            puts.append(row(sym, 'P', o, fri, s, prem, pk, delta, iv, o['strike'] * 100, 1, '; '.join(why),
                            {'setup': 'at support' if at_support else 'near support', 'support': near[0],
                             'support_name': near[1], 'meets_target': bool(hit)}))
        puts.sort(key=lambda x: (x['setup'] != 'at support', not x['meets_target'], -(x['return_pct'] or 0)))

        # margin cushion and what a 10% move in the biggest holding does to it
        cushion = None
        if model.get('margin'):
            m = model['margin'][0]
            big = max(model['positions'], key=lambda p: p['value'], default=None)
            mrow = next((x for x in m['rows'] if big and x['symbol'] == big['symbol']), None)
            move = big['value'] * 0.10 * (1 - (mrow['pct'] if mrow else 0.25)) if big else None
            cushion = {'excess': m['excess'], 'equity': m['equity'], 'requirement': m['requirement'],
                       'symbol': big['symbol'] if big else None, 'ten_pct': move, 'confirmed': m['cash_confirmed']}
        if macro:
            labels = rules.get('macro_labels') or {}
            notes.append('News this week: ' + ', '.join(f"{labels.get(d, 'news')} {d[5:].replace('-', '/')}" for d in macro)
                         + '. Your rule says consider waiting.')
        if state not in ('REGULAR',):
            notes.append('Market is closed, so quotes are the last bid/ask or estimates from IV. Check your broker\'s mid before selling.')
        return {'generated': datetime.now().isoformat(timespec='seconds'), 'expiry': fri.isoformat(), 'market': state,
                'target': [rules['target_low'], rules['target_high']], 'calls': calls, 'puts': puts, 'long': longs,
                'skips': skips, 'notes': notes, 'cushion': cushion, 'macro': macro}
