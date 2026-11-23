"""Compare the console's share counts with a brokerage's own (from SnapTrade or typed in) and fix differences.

More shares at the broker than the files explain = shares bought before the oldest file: added as an opening holding
(with the broker's average cost when it gives one). Fewer = a sale or transfer the files miss: recorded as shares moved
out on that day at market value, so it never shows up as a gain or loss.

The broker's count is live but SnapTrade delivers trades a day or two late, so a trade made just before an account's
first matching is in the broker's count without its row. heal() takes those shares back off the matching once the row
lands (setting 'snap_setup' keeps what each first matching saw and added, for a week).
"""
from datetime import date, timedelta


def diffs(ours, theirs, tol=1e-4):
    """ours/theirs: {symbol: qty} (theirs may carry {'qty', 'avg_cost'}). Returns rows that differ."""
    out = []
    for sym in sorted(set(ours) | set(theirs)):
        t = theirs.get(sym)
        tq = (t.get('qty') if isinstance(t, dict) else t) or 0.0
        oq = ours.get(sym, 0.0)
        if abs(tq - oq) > tol:
            out.append({'symbol': sym, 'ours': oq, 'theirs': tq, 'diff': tq - oq,
                        'avg_cost': t.get('avg_cost') if isinstance(t, dict) else None})
    return out


def apply(db, account_id, row, source, d=None):
    d = d or date.today().isoformat()
    diff = row['diff']
    if diff > 0:
        first = db.one('SELECT MIN(activity_date) AS d FROM raw_rows WHERE account_id=?', (account_id,))['d']
        as_of = (date.fromisoformat(first) - timedelta(days=1)).isoformat() if first else d
        cost = round(row['avg_cost'] * diff, 2) if row.get('avg_cost') else None
        rid = db.x('INSERT INTO opening(account_id, symbol, qty, cost, as_of, note) VALUES(?,?,?,?,?,?)',
                   (account_id, row['symbol'], diff, cost, as_of, f'+{diff:g} to match {source} on {d}'))
        kind = 'opening'
    else:
        rid = db.x('INSERT INTO adjustments(account_id, d, symbol, qty, note) VALUES(?,?,?,?,?)',
                   (account_id, d, row['symbol'], diff, f'{diff:g} to match {source} on {d}'))
        kind = 'adjustments'
    db.log('reconcile', {'account': account_id, **row, 'source': source})
    return kind, rid


def setup(db, build, broker_positions, log=print, only=None):
    """First reconciliation after linking a broker. build() returns a fresh model; broker_positions(account_id)
    returns {symbol: {'qty', 'avg_cost'}}. Steps, each followed by a rebuild:
    1. renamed tickers: a symbol the broker no longer holds and that has no price data, matched by another symbol
       short by the same number of shares, becomes an alias (stored in settings 'symbol_aliases');
    2. shares sold before the broker's history shows them bought become opening holdings (cost unknown);
    3. what still differs from the broker: more at the broker = opening holding at the broker's cost, fewer = moved out
       today (or on the last day the symbol traded, if it stopped trading).
    only: a set of account ids to do this for (accounts linked just now), so an account that was reconciled before
    is never run through it again. Returns a list of what changed."""
    from datetime import date as _date
    changes = []
    linked = [r['account_id'] for r in db.q('SELECT DISTINCT account_id FROM snap_accounts WHERE account_id IS NOT NULL')
              if only is None or r['account_id'] in only]

    def ours_for(m, aid):
        out = {}
        for p in m['positions']:
            q = sum(x['qty'] for x in p['accounts'] if x['id'] == aid)
            if q:
                out[p['symbol']] = q
        return out

    m = build()
    aliases = db.setting('symbol_aliases', {}) or {}
    for aid in linked:
        theirs = broker_positions(aid)[0] or {}
        rows = diffs(ours_for(m, aid), theirs, tol=1e-3)
        gone = [r for r in rows if r['theirs'] == 0 and r['ours'] > 0 and
                not db.one('SELECT d FROM prices WHERE symbol=? LIMIT 1', (r['symbol'],))]
        for g in gone:
            match = next((r for r in rows if r['diff'] > 0 and abs(r['diff'] - g['ours']) < 1e-3), None)
            if match and g['symbol'] not in aliases:
                aliases[g['symbol']] = match['symbol']
                changes.append(f"{g['symbol']} is now {match['symbol']} (renamed)")
    db.set_setting('symbol_aliases', aliases)

    m = build()
    for acct in m['accounts']:
        if acct['id'] not in linked:
            continue
        first = db.one('SELECT MIN(activity_date) AS d FROM raw_rows WHERE account_id=?', (acct['id'],))['d']
        as_of = (_date.fromisoformat(first) - timedelta(days=1)).isoformat() if first else None
        for g in acct['gaps']['shares']:
            # the gap is counted in today's share units; undo splits between the opening date and today
            factor = 1.0
            for sp in db.q('SELECT ratio FROM splits WHERE symbol=? AND d>?', (g['symbol'], as_of or '0000')):
                factor *= sp['ratio'] or 1.0
            db.x('INSERT INTO opening(account_id, symbol, qty, cost, as_of, note) VALUES(?,?,?,?,?,?)',
                 (acct['id'], g['symbol'], round(g['qty'] / factor, 8), None, as_of,
                  f"inferred: sold before any buy in the history (first {g['first']})"))
            changes.append(f"{acct['name']}: {g['symbol']} +{g['qty']:g} held before the history starts")

    m = build()
    seen = db.setting('snap_setup', {}) or {}
    for aid in linked:
        theirs, at = broker_positions(aid)
        if not theirs:
            continue
        rows = diffs(ours_for(m, aid), theirs, tol=1e-4)
        name = next((a['name'] for a in m['accounts'] if a['id'] == aid), aid)
        ent = seen[str(aid)] = {'day': _date.today().isoformat(), 'syms': {}}
        for r in rows:
            d = None
            if r['diff'] < 0:
                last = db.one('SELECT MAX(d) AS d FROM prices WHERE symbol=?', (r['symbol'],))['d']
                if last and last < (_date.today() - timedelta(days=7)).isoformat():
                    d = last
            kind, rid = apply(db, aid, r, 'the broker (SnapTrade)', d)
            ent['syms'][r['symbol']] = {'ours': r['ours'], 'theirs': r['theirs'], 'auto': r['diff'], 'table': kind, 'id': rid}
            changes.append(f"{name}: {r['symbol']} {r['diff']:+g} to match the broker")
    db.set_setting('snap_setup', seen)
    return changes


def heal(db, build, broker_positions, log=print, days=7):
    """For a week after an account's first matching: when the rows of trades from before it land, take the same
    shares back off what the matching added (the broker's count already had them). build() returns a model without
    the broker's not-yet-delivered trades. Returns a list of what changed."""
    seen = db.setting('snap_setup', {}) or {}
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    live = {k: v for k, v in seen.items() if v.get('day', '') >= cutoff and v.get('syms')}
    if not live:
        return []
    m = build()
    changes = []
    for key, ent in live.items():
        aid = int(key)
        theirs, _ = broker_positions(aid)
        if not theirs:
            continue
        ours = {}
        for p in m['positions']:
            q = sum(x['qty'] for x in p['accounts'] if x['id'] == aid)
            if q:
                ours[p['symbol']] = q
        for sym, x in ent['syms'].items():
            if abs(x['auto']) < 1e-9 or db.one('SELECT d FROM splits WHERE symbol=? AND d>=?', (sym, ent['day'])):
                continue                                  # nothing left to take back, or share units changed since
            now_theirs = (theirs.get(sym) or {}).get('qty') or 0.0
            late = (ours.get(sym, 0.0) - x['auto'] - x['ours']) - (now_theirs - x['theirs'])
            if abs(late) < 1e-6:
                continue
            new = x['auto'] - late
            new = min(x['auto'], max(0.0, new)) if x['auto'] > 0 else max(x['auto'], min(0.0, new))
            if abs(new - x['auto']) < 1e-9:
                continue
            if x['table'] == 'opening':
                if abs(new) < 1e-9:
                    db.x('DELETE FROM opening WHERE id=?', (x['id'],))
                else:
                    db.x('UPDATE opening SET qty=?, cost=cost*?/qty WHERE id=?', (new, new, x['id']))
            elif abs(new) < 1e-9:
                db.x('DELETE FROM adjustments WHERE id=?', (x['id'],))
            else:
                db.x('UPDATE adjustments SET qty=? WHERE id=?', (new, x['id']))
            changes.append(f"account {aid}: {sym} matching {x['auto']:+g} -> {new:+g} (its trade rows arrived)")
            x['auto'] = new
    if changes:
        db.set_setting('snap_setup', seen)
        db.log('reconcile heal', changes)
        log('share matching healed:', '; '.join(changes))
    return changes

