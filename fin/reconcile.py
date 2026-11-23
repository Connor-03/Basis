"""Compare the console's share counts with a brokerage's own (from SnapTrade or typed in) and fix differences.

More shares at the broker than the files explain = shares bought before the oldest file: added as an opening holding
(with the broker's average cost when it gives one). Fewer = a sale or transfer the files miss: recorded as shares moved
out on that day at market value, so it never shows up as a gain or loss.
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
        db.x('INSERT INTO opening(account_id, symbol, qty, cost, as_of, note) VALUES(?,?,?,?,?,?)',
             (account_id, row['symbol'], diff, cost, as_of, f'+{diff:g} to match {source} on {d}'))
    else:
        db.x('INSERT INTO adjustments(account_id, d, symbol, qty, note) VALUES(?,?,?,?,?)',
             (account_id, d, row['symbol'], diff, f'{diff:g} to match {source} on {d}'))
    db.log('reconcile', {'account': account_id, **row, 'source': source})


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
    for aid in linked:
        theirs, at = broker_positions(aid)
        if not theirs:
            continue
        rows = diffs(ours_for(m, aid), theirs, tol=1e-4)
        name = next((a['name'] for a in m['accounts'] if a['id'] == aid), aid)
        for r in rows:
            d = None
            if r['diff'] < 0:
                last = db.one('SELECT MAX(d) AS d FROM prices WHERE symbol=?', (r['symbol'],))['d']
                if last and last < (_date.today() - timedelta(days=7)).isoformat():
                    d = last
            apply(db, aid, r, 'the broker (SnapTrade)', d)
            changes.append(f"{name}: {r['symbol']} {r['diff']:+g} to match the broker")
    return changes
