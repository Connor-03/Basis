"""Euphoria score: the mirror of the Doom score. How hard greed is pushing prices up, 0 to 100, scored the same way for
every trading day since 2010 so today can be lined up against past tops and against your own portfolio's highs.

Thirteen readings in four groups, from the data the Doom score already keeps (fin/doom.py). Each becomes 0-100, where
100 is the most greed: CNN Fear & Greed and Crypto Fear & Greed are already on that scale; every other reading is
ranked against every trading day of its own history since 2010 (a VIX lower than on 95% of those days scores 95).
- Greed (40%): CNN Fear & Greed; VIX (low means nobody expects big swings); VIX against the 3-month VIX (well under 1
  means nobody is paying up for protection next month); put/call ratio and junk bond spread (low means few hedges and
  easy credit).
- Price heat (30%): S&P 500 and Nasdaq-100 against their 200-day averages; S&P 500 one-year return; how many of the
  last 21 trading days closed at a new 52-week high.
- Valuation (15%): S&P 500 and Nasdaq-100 P/E against their own 10-year median.
- Crypto (15%): Crypto Fear & Greed and Bitcoin against its 200-day average.
A group with nothing on a day is left out that day and the others are reweighted, as in the Doom score.

The hindsight this page is for: a euphoria cycle is followed by a doom cycle. Each past cycle is paired with the next
Doom peak, to show what selling at the top and buying back in the panic would have done.
"""
import bisect

from . import doom

GROUPS = [('greed', 'Greed', 0.40), ('heat', 'Price heat', 0.30), ('value', 'Valuation', 0.15), ('crypto', 'Crypto', 0.15)]
# key, group, name, how it scores ('direct' = already 0-100 greed, 'high' = higher is more greed, 'low' = lower is), unit
COMPONENTS = [
    ('fg', 'greed', 'CNN Fear & Greed', 'direct', 'index'),
    ('vix', 'greed', 'VIX (expected S&P 500 swings)', 'low', 'level'),
    ('vix_ratio', 'greed', 'VIX vs 3-month VIX', 'low', 'ratio'),
    ('pc', 'greed', 'Put/call ratio (5-day)', 'low', 'ratio'),
    ('junk', 'greed', 'Junk bond spread', 'low', 'pctpt'),
    ('spx_200', 'heat', 'S&P 500 vs 200-day average', 'high', 'pct'),
    ('ndx_200', 'heat', 'Nasdaq-100 vs 200-day average', 'high', 'pct'),
    ('spx_1y', 'heat', 'S&P 500 one-year return', 'high', 'pct'),
    ('spx_highs', 'heat', 'New 52-week highs, last 21 days', 'high', 'count'),
    ('spx_pe', 'value', 'S&P 500 P/E vs its 10-year median', 'high', 'pe'),
    ('ndx_pe', 'value', 'Nasdaq-100 P/E vs its 10-year median', 'high', 'pe'),
    ('cfg', 'crypto', 'Crypto Fear & Greed', 'direct', 'index'),
    ('btc_200', 'crypto', 'Bitcoin vs 200-day average', 'high', 'pct'),
]
# past market tops to compare against: the highest reading inside each window
MARKS = [
    {'key': '2018', 'label': 'Melt-up, January 2018', 'from': '2017-12-01', 'to': '2018-01-26'},
    {'key': '2020', 'label': 'Before COVID, 2020', 'from': '2019-12-15', 'to': '2020-02-19'},
    {'key': '2021', 'label': 'Top of 2021', 'from': '2021-10-01', 'to': '2022-01-03'},
    {'key': '2025', 'label': 'Before the tariff crash', 'from': '2024-11-05', 'to': '2025-02-19'},
]
# names for the greedy stretches since 2010, matched by the date the score peaked
EVENTS = [
    ('2012-10-01', '2014-10-31', 'QE3 bull market'),
    ('2016-11-09', '2018-01-26', 'Post-election melt-up, tax cuts'),
    ('2019-07-01', '2020-02-19', 'Fed rate cuts rally, before COVID'),
    ('2020-06-01', '2020-09-02', 'Stimulus rebound, tech run'),
    ('2020-11-09', '2021-12-31', 'Vaccine rally, meme stocks, crypto mania'),
    ('2023-05-25', '2023-07-31', 'AI rally'),
    ('2023-11-01', '2024-07-16', 'AI rally, rate cut hopes'),
    ('2024-11-05', '2025-02-19', 'Post-election rally'),
    ('2025-05-12', '2025-10-31', 'Rally after the tariff truce'),
]


def event_name(d):
    return next((name for lo, hi, name in EVENTS if lo <= d <= hi), None) if d else None


BANDS = [(80, 'Euphoric'), (60, 'Greedy'), (40, 'Warm'), (20, 'Cool'), (0, 'Cold')]


def label(score):
    return next(name for lo, name in BANDS if score >= lo) if score is not None else None


# ---- scoring ------------------------------------------------------------------------------------------------------
def compute(db, c=None):
    """The Doom score's aligned data, turned around: each component's 0-100 greed score, the groups and the total."""
    c = c or doom.compute(db)
    if not c:
        return None
    days, spx, ndx, raw = c['days'], c['spx'], c['ndx'], c['raw']
    n, first = len(days), c['first']
    e = {k: raw[k] for k in ('fg', 'cfg', 'vix', 'vix_ratio', 'pc', 'junk', 'spx_200', 'spx_pe', 'ndx_pe')}
    nd, run, cnt = [None] * n, 0.0, 0
    for i in range(n):                                   # Nasdaq-100 against its own 200-day average
        v = ndx[i]
        if v:
            run += v
            cnt += 1
        if i >= 200 and ndx[i - 200]:
            run -= ndx[i - 200]
            cnt -= 1
        if v and cnt >= 195:
            nd[i] = v / (run / cnt) - 1
    e['ndx_200'] = nd
    e['spx_1y'] = [spx[i] / spx[i - 252] - 1 if i >= 252 else None for i in range(n)]
    hi = doom._rolling_max(spx, 252)
    new_hi = [1 if i >= 251 and spx[i] >= hi[i] else 0 for i in range(n)]
    acc, highs = 0, [None] * n
    for i in range(n):
        acc += new_hi[i] - (new_hi[i - 21] if i >= 21 else 0)
        if i >= 271:
            highs[i] = acc
    e['spx_highs'] = highs
    bd, bv = c['btc']                                    # Bitcoin trades every day: its average uses its own calendar
    b200, run = {}, 0.0
    for j, v in enumerate(bv):
        run += v
        if j >= 200:
            run -= bv[j - 200]
        if j >= 199:
            b200[bd[j]] = v / (run / 200) - 1
    at = doom._asof(b200)
    e['btc_200'] = [at(d, max_age=4) for d in days]

    scores, refs = {}, {}
    for key, grp, name, how, unit in COMPONENTS:
        vals = e[key]
        if how == 'direct':
            scores[key] = list(vals)
            continue
        ref = sorted(v for v in vals[first:] if v is not None)
        if len(ref) < 100:
            scores[key] = [None] * n
            continue
        refs[key] = ref
        m = len(ref)
        if how == 'high':
            scores[key] = [100.0 * bisect.bisect_left(ref, v) / m if v is not None else None for v in vals]
        else:
            scores[key] = [100.0 * (m - bisect.bisect_right(ref, v)) / m if v is not None else None for v in vals]
    group_scores = {g: [] for g, _, _ in GROUPS}
    total = []
    for i in range(n):
        a = wsum = 0.0
        for g, _, w in GROUPS:
            xs = [scores[k][i] for k, kg, *_ in COMPONENTS if kg == g and scores[k][i] is not None]
            s = sum(xs) / len(xs) if xs else None
            group_scores[g].append(s)
            if s is not None:
                a += w * s
                wsum += w
        total.append(a / wsum if wsum else None)
    return {'days': days, 'spx': spx, 'raw': e, 'scores': scores, 'groups': group_scores, 'total': total,
            'pe': c['pe'], 'first': first, 'ref': refs, 'doom': c['total']}


def _snapshot(u, i):
    return {'date': u['days'][i], 'score': u['total'][i], 'label': label(u['total'][i]),
            'groups': {g: u['groups'][g][i] for g, _, _ in GROUPS},
            'components': {k: {'raw': u['raw'][k][i], 'score': u['scores'][k][i]} for k, *_ in COMPONENTS},
            'pe': {'spx': u['pe']['spx'][i], 'ndx': u['pe']['ndx'][i]}, 'doom': u['doom'][i]}


def _hottest(u, i, k=3):
    """The readings that stood out most that day: the highest component scores."""
    xs = [(u['scores'][key][i], key) for key, *_ in COMPONENTS if u['scores'][key][i] is not None]
    return [{'key': key, 'score': s, 'raw': u['raw'][key][i]} for s, key in sorted(xs, reverse=True)[:k]]


# ---- what followed ------------------------------------------------------------------------------------------------
def _episodes(u, level, gap=40, horizon=126):
    """Each time the score first reached `level` after at least `gap` trading days below it: the S&P 500's deepest
    drop from that day in the next six months, the highest score in that time, and the S&P 500 return after."""
    days, tot, spx = u['days'], u['total'], u['spx']
    out, below = [], gap
    for i in range(u['first'], len(days)):
        s = tot[i]
        if s is None:
            continue
        if s >= level and below >= gap:
            end = min(len(days) - 1, i + horizon)
            low_i = min(range(i, end + 1), key=lambda j: spx[j])
            peak_i = max(range(i, end + 1), key=lambda j: tot[j] or 0)
            fwd = {str(k): (spx[i + k] / spx[i] - 1) if i + k < len(days) else None for k in (63, 252)}
            out.append({'date': days[i], 'score': s, 'drop': spx[low_i] / spx[i] - 1, 'low_date': days[low_i],
                        'days_to_low': low_i - i, 'peak_score': tot[peak_i], 'peak_date': days[peak_i], 'fwd': fwd,
                        'complete': i + horizon < len(days), 'name': event_name(days[peak_i]) or event_name(days[i])})
        below = 0 if s >= level else below + 1
    return out


def _cycles(tot, first, start, calm, calm_days):
    out, cur, k = [], None, 0
    for i in range(first, len(tot)):
        s = tot[i]
        if s is None:
            continue
        if cur is None:
            if s >= start:
                cur, k = {'start': i, 'peak': i}, 0
            continue
        if s > tot[cur['peak']]:
            cur['peak'] = i
        k = k + 1 if s < calm else 0
        if k >= calm_days:
            cur['end'] = i
            out.append(cur)
            cur = None
    if cur:
        cur['calm'] = k
        out.append(cur)
    return out


# euphoria cycles: one starts when the score reaches CYCLE_START, its peak is the highest score until it ends, and it
# ends after CALM_DAYS trading days in a row under CALM. A cycle that peaked at ERA or higher is an era of euphoria.
CYCLE_START, CALM, CALM_DAYS, ERA = 70, 50, 10, 80


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    return (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2) if xs else None


def _cycle_rows(u):
    """Each euphoria cycle, paired with the Doom cycle that came after its peak."""
    days, tot, spx, dtot = u['days'], u['total'], u['spx'], u['doom']
    n = len(days)
    dc = _cycles(dtot, u['first'], doom.CYCLE_START, doom.CALM, doom.CALM_DAYS)
    out = []
    for x in _cycles(tot, u['first'], CYCLE_START, CALM, CALM_DAYS):
        st, pk, end = x['start'], x['peak'], x.get('end')
        last = end if end is not None else n - 1
        row = {'start': days[st], 'peak_date': days[pk], 'peak': tot[pk], 'end': days[end] if end is not None else None,
               'name': event_name(days[pk]), 'era': tot[pk] >= ERA, 'days_to_peak': pk - st, 'length': last - st,
               'hot': _hottest(u, pk),
               'spx_from_peak': {str(k): (spx[pk + k] / spx[pk] - 1) if pk + k < n else None for k in (63, 252)},
               'path': [round(tot[j], 1) if tot[j] is not None else None for j in range(st, min(last, st + 250) + 1)]}
        nxt = next((d for d in dc if d['peak'] > pk), None)
        if nxt:
            dpk, dend = nxt['peak'], nxt.get('end', n - 1)
            top = max(range(st, dpk + 1), key=lambda j: spx[j])
            low = min(range(top, dend + 1), key=lambda j: spx[j])
            row['next_doom'] = {'start': days[nxt['start']], 'peak_date': days[dpk], 'peak': dtot[dpk], 'open': 'end' not in nxt,
                                'name': doom.event_name(days[dpk]), 'days_after': dpk - pk,
                                'spx_peak_to_peak': spx[dpk] / spx[pk] - 1, 'spx_start_to_peak': spx[dpk] / spx[st] - 1,
                                'spx_top_date': days[top], 'spx_low_date': days[low], 'spx_top_to_low': spx[low] / spx[top] - 1}
        if end is None:
            now, since = tot[n - 1], n - 1 - pk
            phase = 'Rising' if since <= 2 else 'Cooling' if now < CALM else 'Fading' if tot[pk] - now >= 10 else 'Peaking'
            row.update({'open': True, 'phase': phase, 'now': now, 'days_since_peak': since, 'calm_days': x.get('calm', 0),
                        'spx_from_start': spx[n - 1] / spx[st] - 1})
        out.append(row)
    return out


# ---- your portfolio's tops ------------------------------------------------------------------------------------------
def _swings(xs, th):
    """Alternating highs and lows of a series where each move is at least th (a zigzag): ([(kind, i)], still falling)."""
    pts, mode, ext = [], None, 0
    for i in range(1, len(xs)):
        if xs[i] is None or xs[ext] is None:
            if xs[ext] is None:
                ext = i
            continue
        if mode in (None, 'up'):
            if xs[i] > xs[ext]:
                ext = i
            elif xs[i] <= xs[ext] * (1 - th):
                pts.append(('high', ext))
                mode, ext = 'down', i
        else:
            if xs[i] < xs[ext]:
                ext = i
            elif xs[i] >= xs[ext] * (1 + th):
                pts.append(('low', ext))
                mode, ext = 'up', i
    if mode == 'down':
        pts.append(('low', ext))                         # the low so far of a fall still going
    return pts, mode == 'down'


def your_tops(u, personal, th=0.15):
    """Every high of your time-weighted portfolio that was followed by a fall of th or more, with the market's
    readings that day and what came after."""
    if not personal or not personal.get('dates'):
        return []
    pd_, ix, lev = personal['dates'], personal['index'], personal.get('leverage') or []
    days = u['days']
    pos = {d: i for i, d in enumerate(days)}
    out = []
    pts, falling = _swings(ix, th)
    for k, (kind, i) in enumerate(pts):
        if kind != 'high':
            continue
        j = pts[k + 1][1] if k + 1 < len(pts) else len(ix) - 1
        d, dlow = pd_[i], pd_[j]
        ui = pos.get(d)
        if ui is None:
            ui = bisect.bisect_right(days, d) - 1
        if ui < u['first'] or u['total'][ui] is None:
            continue
        lo = max(u['first'], ui - 30)
        best = max(range(lo, ui + 1), key=lambda q: u['total'][q] or 0)
        uj = min(len(days) - 1, max(ui, bisect.bisect_right(days, dlow) - 1))
        dpk = max(range(ui, uj + 1), key=lambda q: u['doom'][q] or 0)
        ago = max(0, i - 63)
        run_up = ix[i] / ix[ago] - 1 if ix[ago] else None
        ongoing = falling and k + 2 == len(pts)
        back = next((q for q in range(j + 1, len(ix)) if ix[q] is not None and ix[q] >= ix[i]), None)
        out.append({'date': d, 'score': u['total'][ui], 'label': label(u['total'][ui]), 'max_before': u['total'][best],
                    'max_before_date': days[best], 'hot': _hottest(u, ui), 'run_up_3m': run_up,
                    'leverage': lev[i] if i < len(lev) else None, 'low_date': dlow, 'fall': ix[j] / ix[i] - 1,
                    'days_to_low': j - i, 'ongoing': ongoing, 'back': pd_[back] if back is not None else None,
                    'now_vs_high': ix[-1] / ix[i] - 1 if ix[-1] else None, 'spx_same': u['spx'][uj] / u['spx'][ui] - 1,
                    'doom_peak': u['doom'][dpk], 'doom_peak_date': days[dpk], 'doom_now': u['doom'][ui],
                    'name': event_name(days[ui])})
    return out


def summary(db, personal=None, c=None):
    """Everything the Euphoria dashboard shows. personal: {'dates', 'index', 'leverage'} from the model."""
    u = compute(db, c)
    if not u:
        return None
    n = len(u['days'])
    now_i = max(i for i in range(n) if u['total'][i] is not None)
    now = _snapshot(u, now_i)
    now['hot'] = _hottest(u, now_i)
    marks = []
    for mk in MARKS:
        rng = [i for i in range(n) if mk['from'] <= u['days'][i] <= mk['to'] and u['total'][i] is not None]
        if not rng:
            continue
        i = max(rng, key=lambda j: u['total'][j])
        # the S&P 500's high near that reading, and how far it fell before getting back above it
        end = bisect.bisect_right(u['days'], mk['to'])
        top = max(range(i, max(end, i + 1)), key=lambda j: u['spx'][j])
        rec = next((j for j in range(top + 1, n) if u['spx'][j] >= u['spx'][top]), None)
        low = min(range(top, rec if rec else n), key=lambda j: u['spx'][j])
        marks.append({**_snapshot(u, i), 'key': mk['key'], 'name': mk['label'], 'spx_top': u['days'][top],
                      'spx_low': u['days'][low], 'spx_fall': u['spx'][low] / u['spx'][top] - 1,
                      'spx_back': u['days'][rec] if rec else None})
    tops = your_tops(u, personal)
    if personal and personal.get('dates'):
        run, dd = None, {}
        for d, v in zip(personal['dates'], personal['index']):
            if v is None:
                continue
            run = v if run is None else max(run, v)
            dd[d] = v / run - 1
        at = doom._asof(dd)
        for snap in [now] + marks:
            snap['you_dd'] = at(snap['date'], max_age=5)
    level = int(round(now['score']))
    levels = []
    for lv in sorted({60, 70, 80} | ({level} if level >= 50 else set())):
        done = [e for e in _episodes(u, lv) if e['complete']]
        if not done:
            continue
        f12 = [e['fwd']['252'] for e in done if e['fwd']['252'] is not None]
        levels.append({'level': lv, 'now': lv == level, 'count': len(done), 'median_drop': _median([e['drop'] for e in done]),
                       'worst_drop': min(e['drop'] for e in done), 'worst_date': min(done, key=lambda e: e['drop'])['date'],
                       'dip5': sum(1 for e in done if e['drop'] <= -0.05), 'dip10': sum(1 for e in done if e['drop'] <= -0.10),
                       'median_peak': _median([e['peak_score'] for e in done]),
                       'up_12m': sum(1 for x in f12 if x > 0), 'n_12m': len(f12), 'median_12m': _median(f12)})
    eps = _episodes(u, max(level, 60))
    cyc = _cycle_rows(u)
    cur = cyc[-1] if cyc and cyc[-1].get('open') else None
    past = [x for x in cyc if not x.get('open')]
    paired = [x for x in past if x.get('next_doom')]
    lower = [x for x in paired if x['next_doom']['spx_peak_to_peak'] < 0]
    eras = [x for x in past if x['era']]
    cycle = {'rules': {'start': CYCLE_START, 'calm': CALM, 'calm_days': CALM_DAYS, 'era': ERA,
                       'doom_start': doom.CYCLE_START}, 'current': cur, 'past': past,
             'hindsight': {'count': len(paired), 'lower': len(lower),
                           'median_peak_to_peak': _median([x['next_doom']['spx_peak_to_peak'] for x in paired]),
                           'median_start_to_peak': _median([x['next_doom']['spx_start_to_peak'] for x in paired]),
                           'lower_from_start': sum(1 for x in paired if x['next_doom']['spx_start_to_peak'] < 0),
                           'median_top_to_low': _median([x['next_doom']['spx_top_to_low'] for x in paired]),
                           'median_days': _median([x['next_doom']['days_after'] for x in paired]),
                           'longest': max((x['length'] for x in past), default=None),
                           'longest_start': max(past, key=lambda x: x['length'])['start'] if past else None} if paired else None,
             'era_stats': {'count': len(eras), 'median_peak': _median([x['peak'] for x in eras]),
                           'median_length': _median([x['length'] for x in eras])} if eras else None}
    start = u['first']
    return {'now': now, 'marks': marks, 'levels': levels, 'episodes': eps, 'cycle': cycle, 'tops': tops,
            'history': {'dates': u['days'][start:], 'score': [round(x, 1) if x is not None else None for x in u['total'][start:]],
                        'doom': [round(x, 1) if x is not None else None for x in u['doom'][start:]],
                        'spx': [round(x, 2) for x in u['spx'][start:]]},
            'groups': [{'key': g, 'name': nm, 'weight': w} for g, nm, w in GROUPS],
            'components': [{'key': k, 'group': g, 'name': nm, 'how': how, 'unit': un} for k, g, nm, how, un in COMPONENTS],
            'bands': [{'from': lo, 'name': nm} for lo, nm in BANDS]}
