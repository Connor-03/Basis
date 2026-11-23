#!/usr/bin/env python3
"""Basis: a portfolio dashboard. Python standard library + SQLite, nothing to install.

Two ways to run (fin/paths.py): the desktop program, started by basis.pyw, listens on this computer only; a server
copy has a config.json beside this file naming the addresses to listen on and is kept running by cron. Web files in
web/ are read from disk on every request; Python changes need a restart.
"""
import json
import os
import shutil
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fin import paths  # noqa: E402

paths.ensure()
from fin import agent, analytics, dip, doom, imports, intraday, m2, macro, market, moves, options, push, reconcile, snaptrade, sync, update, valuation  # noqa: E402
from fin.db import DB  # noqa: E402

DESKTOP = paths.MODE == 'desktop'


def _config():
    if not DESKTOP:
        with open(paths.CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f)
    port = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else 8440
    return {'port': port, 'binds': ['127.0.0.1'], 'hosts': [f'127.0.0.1:{port}', f'localhost:{port}']}


CONFIG = _config()
DATA = paths.DATA
WEB = os.path.join(HERE, 'web')
db = DB(os.path.join(DATA, 'finance.db'))
agent.SETTINGS = lambda: db.setting('agent', {}) or {}
agent.PORT[0] = CONFIG['port']
MENU = options.Menu(sync.YH, db)
push.init(db)
db.x('CREATE TABLE IF NOT EXISTS daily(d TEXT PRIMARY KEY, net_worth REAL, accounts_total REAL, invested REAL, '
     'day_change REAL, data TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP)')
db.x('CREATE TABLE IF NOT EXISTS live_points(ts REAL PRIMARY KEY, value REAL NOT NULL, session TEXT)')
if 'loan' not in {r['name'] for r in db.q('PRAGMA table_info(live_points)')}:
    db.x('ALTER TABLE live_points ADD COLUMN loan REAL')     # the margin loan at that moment, for the Assets chart
ALERT_DEFAULTS = {'monday_menu': True, 'margin': True, 'margin_threshold': 2000, 'big_move_pct': 5.0, 'doom': True}
STATE = {'data_v': 1, 'quote_v': 0, 'model': None, 'model_key': None, 'model_at': 0, 'menu': None, 'menu_at': 0,
         'sync': {}, 'errors': [], 'seen': time.time()}
JOB = {'running': False, 'step': '', 'error': None, 'done': False}       # the first sync after linking a brokerage
LOCK = threading.Lock()
MAX_UPLOAD = 25 * 1024 * 1024


def log(*a):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


def bump():
    STATE['data_v'] += 1
    STATE['menu'] = None


def model(force=False):
    with LOCK:
        key = (STATE['data_v'], STATE['quote_v'])
        if not force and STATE['model'] is not None and STATE['model_key'] == key:
            return STATE['model']
        m = analytics.build(db, sync.cached_quotes(db))
        m['sync'] = STATE['sync']
        STATE['model'], STATE['model_key'], STATE['model_at'] = m, key, time.time()
        return m


def menu(force=False):
    age = time.time() - STATE['menu_at']
    session = market.session_now()
    max_age = 600 if session == 'regular' else 3600
    if not force and STATE['menu'] is not None and age < max_age:
        return STATE['menu']
    m = model()
    rules = dict(db.setting('option_rules', {}) or {})
    auto = db.setting('macro_auto', []) or []
    rules['macro_dates'] = sorted(set(rules.get('macro_dates') or []) | {x['d'] for x in auto})
    rules['macro_labels'] = {x['d']: x['label'] for x in auto}
    watch = rules.get('watch') or options.DEFAULT_RULES['watch']
    q = sync.cached_quotes(db)
    prices = analytics.Prices(db, set(watch) | {p['symbol'] for p in m['positions']})
    STATE['menu'] = MENU.build(m, q, prices, rules)
    STATE['menu_at'] = time.time()
    return STATE['menu']


# ---- background market data ----------------------------------------------------------------------------------------
def needed_symbols():
    m = model()
    rules = db.setting('option_rules', {}) or {}
    watch = list(dict.fromkeys((rules.get('watch') or options.DEFAULT_RULES['watch']) + doom_watch()))
    held = [p['symbol'] for p in m['positions']] + [x['symbol'] for x in m['manual'] if x.get('symbol')]
    occ = [o['occ'] for o in m['options']['open'] if o.get('status') == 'open']
    return m, sorted(set(m['symbols_needed']) | set(watch)), held + watch + occ + analytics.BENCH + analytics.EXTRA + ['^TNX']


def doom_watch():
    """Tickers added on the Doom dashboard (the options watch list is analyzed too)."""
    return db.setting('doom_watch', []) or []


def dip_symbols(m):
    rules = db.setting('option_rules', {}) or {}
    watch = list(dict.fromkeys((rules.get('watch') or options.DEFAULT_RULES['watch']) + doom_watch()))
    out = []
    for p in m['positions']:
        if p['class'] == 'Crypto' or p['symbol'].endswith('-USD'):
            continue
        out.append((p['symbol'], 'stock' if p['class'] == 'Stock' else 'fund'))
    held = {x for x, _ in out}
    out += [(w, 'stock') for w in watch if w not in held]
    return out, watch


def start_dip_refresh(m, kinds=None):
    syms, _ = dip_symbols(m)
    def run():
        n = dip.refresh(db, sync.YH, syms, kinds=kinds, log=log)
        if n:
            STATE['dip'] = None
    threading.Thread(target=run, daemon=True).start()


def worker():
    last_hist = last_prof = last_div = last_q = 0
    last_backup = None
    time.sleep(2)
    while True:
        try:
            session = market.session_now()
            now = time.time()
            m, all_syms, live = needed_symbols()
            first = min((a['first_date'] for a in m['accounts'] if a.get('first_date')), default=date.today().isoformat())
            start = (date.fromisoformat(first) - timedelta(days=45)).isoformat()
            et = market.eastern_now()
            if now - last_hist > 6 * 3600 or (et.hour == 16 and et.minute >= 30 and now - last_hist > 1800):
                n = sync.history(db, all_syms, start, log=log)
                last_hist = now
                STATE['sync']['history'] = datetime.now().isoformat(timespec='seconds')
                if n:
                    bump()
            interval = {'regular': 30, 'pre': 60, 'post': 60}.get(session, 300)
            if now - last_q > interval:
                q = sync.quotes(db, live)
                last_q = now
                STATE['quote_v'] += 1
                STATE['sync']['quotes'] = datetime.now().isoformat(timespec='seconds')
                STATE['sync']['session'] = session
                STATE['sync']['quote_count'] = len(q)
                record_live(session)
            if now - last_prof > 12 * 3600:
                if sync.profiles(db, [s for s in all_syms if s in set(live)], log=log):
                    bump()
                last_prof = now
            if now - last_div > 6 * 3600:
                held = [p['symbol'] for p in m['positions']]
                if sync.dividends(db, held, log=log):
                    bump()
                last_div = now
                STATE['sync']['dividends'] = datetime.now().isoformat(timespec='seconds')
                if sync.ath(db, held, log=log):
                    bump()
            # intraday charts: 1D every 2 minutes in the session (5 in pre/after hours), 1W every 15
            for rng, every in (('1d', {'regular': 120, 'pre': 300, 'post': 300}.get(session, 1800)),
                               ('5d', 900 if session != 'closed' else 3600)):
                if now - STATE.get('intra_at_' + rng, 0) > every:
                    STATE['intra_at_' + rng] = now
                    build_intraday(rng)
            st = snaptrade.public_state()
            if st['registered'] and db.one('SELECT snap_id FROM snap_accounts LIMIT 1') and \
                    now - STATE.get('snap_at', 0) > (3600 if session == 'regular' else 6 * 3600):
                STATE['snap_at'] = now
                try:
                    res = snaptrade.sync(db, log=log)
                    if res['new_rows'] or res['accounts']:
                        bump()
                    reconcile_new()
                except Exception as e:
                    log('snaptrade sync:', e)
            update.check(db.setting('update', {}) or {})
            if now - STATE.get('val_at', 0) > 20 * 3600 or (et.hour == 17 and now - STATE.get('val_at', 0) > 3 * 3600):
                STATE['val_at'] = now
                try:
                    valuation.refresh(db, log=log)
                    STATE['market'] = None
                except Exception as e:
                    log('valuation:', e)
            # doom dashboard data (fundamentals daily, earnings history weekly, crowd mood every 6 hours) in its own thread
            if now - STATE.get('dip_at', 0) > 1800:
                STATE['dip_at'] = now
                start_dip_refresh(m)
            # doom score: every 10 minutes in the session (CNN and the indexes move), every 3 hours otherwise; each
            # refresh also keeps the day's highest and lowest reading
            if now - STATE.get('doom_at', 0) > (600 if session == 'regular' else 3 * 3600):
                STATE['doom_at'] = now
                try:
                    doom.refresh(db, sync.YH, log=log)
                    doom.refresh_hilo(db, sync.YH, log=log)
                    build_doom()
                except Exception as e:
                    log('doom:', e)
            alerts(m, session, et)
            if now - STATE.get('m2_at', 0) > 86400:
                try:
                    m2.refresh(db)
                    bump()
                except Exception as e:
                    log('M2:', e)
                STATE['m2_at'] = now
            if now - STATE.get('macro_at', 0) > 7 * 86400 or not db.setting('macro_auto'):
                try:
                    db.set_setting('macro_auto', macro.fomc_dates())
                except Exception as e:
                    log('fomc calendar:', e)
                STATE['macro_at'] = now
            today = date.today().isoformat()
            if last_backup != today:
                backup(today)
                last_backup = today
        except Exception as e:
            log('worker:', e)
            traceback.print_exc()
            STATE['errors'] = (STATE['errors'] + [f"{datetime.now():%H:%M} {e}"])[-10:]
        time.sleep(10)


def alerts(m, session, et):
    """Phone notifications. Each one is sent at most once per key (day or week), and only to devices that turned
    them on in Settings."""
    cfg = {**ALERT_DEFAULTS, **(db.setting('alerts', {}) or {})}
    today = et.date().isoformat()
    q = sync.cached_quotes(db)
    regular = session == 'regular' and (q.get('VOO') or {}).get('marketState') == 'REGULAR'
    # daily record after the close
    if et.weekday() < 5 and et.hour * 60 + et.minute >= 16 * 60 + 10 and not db.one('SELECT d FROM daily WHERE d=?', (today,)):
        last_bar = db.one("SELECT MAX(d) AS d FROM prices WHERE symbol='VOO'")['d']
        if last_bar == today:
            db.x('INSERT OR REPLACE INTO daily(d, net_worth, accounts_total, invested, day_change, data) VALUES(?,?,?,?,?,?)',
                 (today, m['net_worth'], m['accounts_total'], m['invested'], m['day_change'],
                  json.dumps({a['name']: round(a['equity'], 2) for a in m['accounts']})))
    if not regular:
        return
    # Monday (or the first trading day of the week) after 9:45: the options menu
    minutes = et.hour * 60 + et.minute
    week = '%d-W%02d' % et.isocalendar()[:2]
    if cfg['monday_menu'] and et.weekday() <= 1 and minutes >= 9 * 60 + 45 and \
            not db.one('SELECT key FROM push_sent WHERE key=?', ('menu:' + week,)):
        mn = menu(force=True)
        puts = [f"{r['symbol']} {r['strike']:g}P {r['return_pct'] * 100:.1f}%" for r in mn['puts'] if r.get('meets_target')][:3]
        calls = [f"{r['symbol']} {r['strike']:g}C {r['return_pct'] * 100:.1f}%" for r in mn['calls'] if r.get('pick') == 'target'][:3]
        waits = sorted({x['symbol'] for x in mn['skips'] if 'wait' in x['reason'].lower()})
        body = '; '.join(filter(None, [('Puts: ' + ', '.join(puts)) if puts else '', ('Calls: ' + ', '.join(calls)) if calls else '',
                                        ('Wait: ' + ', '.join(waits[:5])) if waits else '', ' '.join(mn['notes'][:1]) if mn['macro'] else '']))
        push.once(db, 'menu:' + week, {'title': f"Options for Friday {mn['expiry'][5:].replace('-', '/')}",
                                        'body': body or 'Nothing passes your rules this week.', 'url': '/options', 'tag': 'menu'}, log=log)
    for mg in m['margin']:
        if cfg['margin'] and mg['debt'] > 0 and mg['excess'] < cfg['margin_threshold']:
            level = 'below' if mg['excess'] < 0 else 'thin'
            push.once(db, f'margin:{today}:{level}', {
                'title': 'Margin: below maintenance (estimate)' if level == 'below' else 'Margin cushion is thin',
                'body': f"{mg['account']}: cushion about ${mg['excess']:,.0f}. Check your broker's margin screen.",
                'url': '/risk', 'tag': 'margin'}, log=log)
    # doom cycles: when one starts (the Doom dashboard turns on) and when one ends
    cyc = ((STATE.get('doom') or {}).get('cycle') or {})
    if cfg.get('doom', True):
        cur = cyc.get('current')
        if cur:
            push.once(db, f"doom-start:{cur['start']}", {'title': f"Doom cycle started: score {cur['now']:.0f}",
                      'body': 'The Doom dashboard is analyzing your stocks for dip buys.', 'url': '/doom', 'tag': 'doom'}, log=log)
        done = (cyc.get('eras') or []) + (cyc.get('scares') or [])
        last = max(done, key=lambda x: x['end']) if done else None
        if last and last['end'] >= (et.date() - timedelta(days=4)).isoformat():
            push.once(db, f"doom-end:{last['start']}", {'title': f"Doom cycle ended (peak {last.get('high') or last['peak']:.0f} on {(last.get('high_date') or last['peak_date'])[5:].replace('-', '/')})",
                      'body': 'Ten trading days in a row under 45. It is now in the list of past cycles.', 'url': '/doom', 'tag': 'doom'}, log=log)
    base = m['net_worth'] - m['day_change']
    if base > 0 and abs(m['day_change']) / base * 100 >= cfg['big_move_pct']:
        push.once(db, f'move:{today}', {'title': f"Portfolio {'down' if m['day_change'] < 0 else 'up'} {abs(m['day_change']) / base * 100:.1f}% today",
                                         'body': f"${m['day_change']:+,.0f}. Net worth ${m['net_worth']:,.0f}.", 'url': '/', 'tag': 'move'}, log=log)


def dip_state():
    """The Doom dashboard: the cycle, the activation state and every stock's dip analysis (cached 5 minutes)."""
    m = model()
    key = (STATE['data_v'], STATE['quote_v'])
    cached = STATE.get('dip')
    if cached and cached['key'] == key and time.time() - cached['at'] < 300:
        return cached['d']
    if not STATE.get('doom'):
        try:
            build_doom()
        except Exception as e:
            log('doom summary:', e)
    d = STATE.get('doom') or {}
    cyc = (d.get('cycle') or {})
    cur = cyc.get('current')
    _, watch = dip_symbols(m)
    spx = [(r['d'], r['value']) for r in db.q("SELECT d, value FROM macro_daily WHERE series='spx' AND d>=? ORDER BY d",
                                               ((date.today() - timedelta(days=400)).isoformat(),))]
    rows = dip.analyze(db, m, sync.cached_quotes(db), watch, spx)
    mg = (m.get('margin') or [None])[0] or {}
    fresh = db.one("SELECT MAX(at) AS at FROM fundamentals WHERE kind='summary'") if rows else None
    out = {'active': bool(cur), 'score': (d.get('now') or {}).get('score'), 'label': (d.get('now') or {}).get('label'),
           'cycle': cyc, 'rows': rows, 'watch': watch, 'doom_watch': doom_watch(),
           'margin': {'buffer': mg.get('excess'), 'debt': mg.get('debt'), 'crypto': mg.get('crypto')} if mg else None,
           'updated': (fresh or {}).get('at'), 'weights': dip.WEIGHTS}
    STATE['dip'] = {'key': key, 'at': time.time(), 'd': out}
    return out


def moves_state():
    """Moves you logged, with the picture at the time and how each has turned out since."""
    if not STATE.get('doom'):
        try:
            build_doom()
        except Exception as e:
            log('doom summary:', e)
    return moves.summary(db, model(), STATE.get('doom') or {}, sync.cached_quotes(db))


def build_doom():
    m = model()
    perf, hist = m.get('performance') or {}, m.get('history') or {}
    personal = {'dates': hist['dates'], 'index': perf['index']} if perf.get('index') and len(perf['index']) == len(hist.get('dates') or []) else None
    STATE['doom'] = doom.summary(db, personal)
    STATE['doom_built'] = time.time()


def build_intraday(rng):
    try:
        m = model()
        syms = [p['symbol'] for p in m['positions']] + ['VOO', 'QQQM']
        bars = intraday.spark(sync.YH, syms, *intraday.RANGES[rng])
        ser = intraday.series(m, bars, rng)
        if ser:
            STATE.setdefault('intra', {})[rng] = ser
    except Exception as e:
        log('intraday', rng, e)


def record_live(session):
    """One point per quote refresh for the Live chart (net worth is already at the newest price)."""
    try:
        m = model()
        v = m['net_worth']                       # already at the newest price, after-hours and pre-market included
        db.x('INSERT OR REPLACE INTO live_points(ts, value, session, loan) VALUES(?,?,?,?)', (time.time(), v, session, m.get('loan')))
        db.x('DELETE FROM live_points WHERE ts < ?', (time.time() - 4 * 86400,))
    except Exception as e:
        log('live point', e)


def backup(today):
    d = paths.BACKUPS
    os.makedirs(d, mode=0o700, exist_ok=True)
    dst = os.path.join(d, f'finance-{today}.db')
    if not os.path.exists(dst):
        with db.lock:
            db.c.execute(f"VACUUM INTO '{dst}'")
        os.chmod(dst, 0o600)
    for f in sorted(x for x in os.listdir(d) if x.startswith('finance-'))[:-14]:
        os.remove(os.path.join(d, f))


# ---- first run, updates, restart -----------------------------------------------------------------------------------
def _mdy(d):
    return f'{d.month}/{d.day}/{d.year}'


def setup_done():
    """A desktop copy is ready once a brokerage is linked and its first sync has run. A server copy always is."""
    return not DESKTOP or bool((db.setting('first_run', {}) or {}).get('done'))


def reconcile_new():
    """Line up share counts with the broker for accounts linked since the last time (fin/reconcile.py setup). Each
    account goes through this once: after that, a difference is a trade still on its way and shows in Settings."""
    done = db.setting('snap_reconciled', None)
    rows = db.q('SELECT snap_id, account_id FROM snap_accounts WHERE account_id IS NOT NULL')
    if done is None and not DESKTOP:
        done = [r['snap_id'] for r in rows]             # a copy set up before this existed: all were done by hand
        db.set_setting('snap_reconciled', done)
    new = [r for r in rows if r['snap_id'] not in set(done or [])]
    if not new:
        return []

    def build():
        bump()
        return model(force=True)
    changes = reconcile.setup(db, build, lambda aid: snaptrade.broker_positions(db, aid), log=log,
                              only={r['account_id'] for r in new})
    db.set_setting('snap_reconciled', sorted(set(done or []) | {r['snap_id'] for r in new}))
    db.log('snaptrade first reconcile', {'accounts': len(new), 'changes': changes[:60]})
    bump()
    return changes


def first_sync():
    """After a brokerage is linked: read it, fetch the price history its holdings need, match share counts."""
    if JOB['running']:
        return {'ok': True}
    JOB.update(running=True, error=None, done=False, step='Reading your accounts from SnapTrade')

    def work():
        try:
            res = snaptrade.sync(db, log=log)
            if not res['accounts']:
                raise ValueError('SnapTrade has no accounts to show yet. A broker can take a few minutes after it is linked. Try again shortly.')
            STATE['snap_at'] = time.time()
            bump()
            JOB['step'] = 'Downloading price history for your holdings'
            m, all_syms, live = needed_symbols()
            first = min((a['first_date'] for a in m['accounts'] if a.get('first_date')), default=date.today().isoformat())
            sync.history(db, all_syms, (date.fromisoformat(first) - timedelta(days=45)).isoformat(), log=log)
            bump()
            JOB['step'] = 'Matching share counts with your broker'
            reconcile_new()
            JOB['step'] = 'Getting live prices'
            m, all_syms, live = needed_symbols()
            sync.history(db, all_syms, (date.fromisoformat(first) - timedelta(days=45)).isoformat(), log=log)
            sync.quotes(db, live)
            STATE['quote_v'] += 1
            STATE['sync']['quotes'] = datetime.now().isoformat(timespec='seconds')
            bump()
            cur = db.setting('first_run', {}) or {}
            if not cur.get('done'):
                db.set_setting('first_run', {**cur, 'done': True, 'at': datetime.now().isoformat(timespec='seconds')})
            JOB['done'] = True
            if res['errors']:
                JOB['error'] = 'Some accounts could not be read: ' + '; '.join(res['errors'])[:300]
        except Exception as e:
            log('first sync:', e)
            JOB['error'] = str(e)[:400]
        finally:
            JOB['running'] = False
    threading.Thread(target=work, daemon=True).start()
    return {'ok': True}


_conn_cache = {'at': 0, 'rows': []}


def first_run_state():
    """What the welcome page shows: is a key saved, is a brokerage linked, how the first sync is going, is Claude here."""
    st = snaptrade.public_state()
    conns, err = [], None
    if st['has_keys'] and not setup_done():
        if time.time() - _conn_cache['at'] > 6:              # the page asks every few seconds while it waits
            try:
                _conn_cache['rows'] = [{'id': c.get('id'), 'broker': ((c.get('brokerage') or {}).get('display_name') or (c.get('brokerage') or {}).get('name') or 'Brokerage'),
                                        'disabled': bool(c.get('disabled'))} for c in snaptrade.Client().connections()]
                _conn_cache['error'] = None
            except Exception as e:
                _conn_cache['error'] = str(e)[:300]
            _conn_cache['at'] = time.time()
        conns, err = _conn_cache['rows'], _conn_cache.get('error')
    cur = db.setting('first_run', {}) or {}
    return {'mode': paths.MODE, 'version': paths.version(), 'ready': setup_done(), 'has_keys': st['has_keys'], 'key_tail': st['key_tail'],
            'connections': conns, 'error': err, 'job': dict(JOB), 'agent': agent.info(), 'agent_step': cur.get('agent_step', ''),
            'accounts': db.one('SELECT COUNT(*) AS n FROM snap_accounts')['n'],
            'folder': paths.APP, 'data_folder': os.path.dirname(paths.DATA) if DESKTOP else ''}


OPEN_HOSTS = ('snaptrade.com', 'github.com', 'claude.ai', 'claude.com', 'anthropic.com', 'connoraherne.com')


def open_link(url):
    """Open a web address in the computer's own browser (the app's window is not where you sign in to a broker).
    Only https addresses of the few sites this program sends people to."""
    u = urllib.parse.urlparse(str(url or ''))
    host = (u.hostname or '').lower()
    testing = bool(os.environ.get('BASIS_TEST')) and host == '127.0.0.1'      # the stand-in SnapTrade used by tests
    if not testing and (u.scheme != 'https' or not any(host == h or host.endswith('.' + h) for h in OPEN_HOSTS)):
        raise ValueError('That address is not one this program opens.')
    if DESKTOP and not os.environ.get('BASIS_TEST'):
        threading.Thread(target=webbrowser.open, args=(u.geturl(),), daemon=True).start()
        return {'opened': True}
    return {'opened': False, 'url': u.geturl()}


def restart(snap=''):
    """Desktop: ask the launcher (basis.pyw) for a restart by leaving a note and exiting with code 75. If the program
    does not come back, the launcher restores snapshot `snap`."""
    with open(os.path.join(DATA, 'restart.json'), 'w', encoding='utf-8') as f:
        json.dump({'snap': snap or '', 'at': time.time()}, f)
    threading.Timer(0.6, lambda: os._exit(75)).start()


if DESKTOP:
    agent.RESTART[0] = restart


# ---- HTTP ----------------------------------------------------------------------------------------------------------
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json',
         '.woff2': 'font/woff2', '.json': 'application/json'}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class H(BaseHTTPRequestHandler):
    server_version = 'finance'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        return (self.headers.get('Host') or '').lower() in CONFIG['hosts']

    def _send(self, code, body, ctype='application/json', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str, separators=(',', ':')).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {'error': msg})

    def _body(self, limit=1_000_000):
        n = int(self.headers.get('Content-Length') or 0)
        if n > limit:
            raise ValueError('too large')
        return self.rfile.read(n) if n else b''

    def _json(self, limit=1_000_000):
        b = self._body(limit)
        return json.loads(b) if b else {}

    def _write_ok(self):
        if self.headers.get('X-Requested-With') != 'finance':
            return False
        origin = self.headers.get('Origin')
        if origin:
            host = urllib.parse.urlparse(origin).netloc.lower()
            if host != (self.headers.get('Host') or '').lower():
                return False
        return True

    def do_GET(self):
        if not self._host_ok():
            return self._err(421, 'unknown host')
        u = urllib.parse.urlparse(self.path)
        qs = dict(urllib.parse.parse_qsl(u.query))
        p = u.path
        if p != '/api/status':
            STATE['seen'] = time.time()                 # the launcher reads this to tell an open window from a closed one
        try:
            if p == '/' or p in ('/positions', '/income', '/analysis', '/risk', '/options', '/activity', '/settings', '/market', '/doom', '/assets', '/customize'):
                return self._file('index.html')
            if p == '/recover':
                return self._file('recover.html')
            if p == '/api/agent':
                return self._send(200, agent.state(db.setting('agent', {}) or {}, qs.get('chat') or None,
                                                   int(qs.get('rev') or 0), qs.get('wait') == '1'))
            if p == '/api/agent/changes':
                return self._send(200, agent.recent_changes())
            if p == '/api/agent/diff':
                return self._send(200, agent.diff(qs.get('id') or ''))
            if p.startswith('/api/agent/file/'):
                path, ctype, ext = agent.file_path(p.rsplit('/', 1)[1])
                with open(path, 'rb') as f:
                    body = f.read()
                # only real images are shown in the page; anything else is handed over as a download
                extra = None if ctype.startswith('image/') else {'Content-Disposition': f'attachment; filename="attachment.{ext}"'}
                return self._send(200, body, ctype, extra)
            if p == '/static/recover.html':
                return self._err(404, 'not found')
            if p == '/sw.js':
                return self._file('sw.js', {'Service-Worker-Allowed': '/'})
            if p.startswith('/static/'):
                return self._file(p[len('/static/'):])
            if p == '/api/model':
                m = model(force=qs.get('force') == '1')
                return self._send(200, slim(m))
            if p == '/api/options':
                return self._send(200, menu(force=qs.get('refresh') == '1'))
            if p == '/api/activity':
                return self._send(200, activity(qs))
            if p == '/api/symbol':
                return self._send(200, symbol_detail(qs.get('s', '').upper()))
            if p == '/api/setup':
                return self._send(200, setup_data())
            if p == '/api/first-run':
                return self._send(200, first_run_state())
            if p == '/api/update':
                return self._send(200, update.state(db.setting('update', {}) or {}))
            if p == '/api/ping':
                return self._send(200, {'ok': True})
            if p == '/api/bye':                              # the page is closing (see basis.pyw)
                STATE['bye'] = STATE['seen'] = time.time()
                return self._send(200, {'ok': True})
            if p == '/api/push':
                if not push.AVAILABLE:
                    return self._send(200, {'available': False})
                return self._send(200, {'available': True, 'public_key': push.public_key(), 'devices': push.public_subs(db),
                                        'alerts': {**ALERT_DEFAULTS, **(db.setting('alerts', {}) or {})},
                                        'macro': [x for x in (db.setting('macro_auto', []) or []) if x['d'] >= date.today().isoformat()][:8]})
            if p == '/api/intraday':
                rng = qs.get('range', '1d')
                if rng == 'live':
                    now = time.time()
                    since = now - min(240.0, float(qs.get('minutes', 60))) * 60
                    rows = db.q('SELECT ts, value, session, loan FROM live_points WHERE ts>=? ORDER BY ts', (since,))
                    # carry the reading just before the window to its left edge (if it is recent), so the line spans the hour
                    before = db.one('SELECT ts, value, session, loan FROM live_points WHERE ts<? AND ts>=? ORDER BY ts DESC LIMIT 1',
                                    (since, since - 1800))
                    if before:
                        rows = [{'ts': since, 'value': before['value'], 'session': intraday.session_of(since), 'loan': before['loan']}] + rows
                    return self._send(200, {'range': 'live', 'ts': [r['ts'] for r in rows], 'value': [r['value'] for r in rows],
                                            'loan': [r['loan'] for r in rows],
                                            'session': [r['session'] for r in rows], 'domain': [since, now],
                                            'et_offset': intraday.et_offset(now),
                                            'labels': [intraday._et(r['ts']).strftime('%Y-%m-%d %H:%M:%S') for r in rows]})
                if rng not in intraday.RANGES:
                    return self._err(400, 'range')
                if rng not in STATE.get('intra', {}):
                    build_intraday(rng)
                return self._send(200, STATE.get('intra', {}).get(rng) or {'range': rng, 'ts': [], 'value': []})
            if p == '/api/market':
                if not STATE.get('market') or time.time() - STATE.get('market_at', 0) > 600:
                    STATE['market'] = valuation.summary(db, sync.cached_quotes(db))
                    STATE['market_at'] = time.time()
                return self._send(200, STATE['market'])
            if p == '/api/dip':
                return self._send(200, dip_state())
            if p == '/api/moves':
                return self._send(200, moves_state())
            if p == '/api/doom':
                if not STATE.get('doom'):
                    try:
                        build_doom()
                    except Exception as e:
                        log('doom summary:', e)
                d = STATE.get('doom') or {'error': 'The doom score data is still loading.'}
                if qs.get('brief') and 'history' in d:
                    cyc = d.get('cycle') or {}
                    cur = cyc.get('current')
                    d = {k: v for k, v in d.items() if k not in ('history', 'episodes', 'peaks', 'cycle')}
                    d['cycle'] = {'rules': cyc.get('rules'), 'current': {k: v for k, v in cur.items() if k != 'path'} if cur else None}
                return self._send(200, d)
            if p == '/api/snaptrade':
                return self._send(200, snap_state())
            if p == '/api/status':
                return self._send(200, {'app': 'basis', 'version': paths.version(), 'idle': round(time.time() - STATE['seen'], 1),
                                        'closed': STATE.get('bye', 0) >= STATE['seen'],
                                        'sync': STATE['sync'], 'errors': STATE['errors'], 'session': market.session_now()})
            return self._err(404, 'not found')
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:
            traceback.print_exc()
            return self._err(500, str(e)[:300])

    def do_POST(self):
        if not self._host_ok():
            return self._err(421, 'unknown host')
        if not self._write_ok():
            return self._err(403, 'forbidden')
        u = urllib.parse.urlparse(self.path)
        qs = dict(urllib.parse.parse_qsl(u.query))
        p = u.path
        STATE['seen'] = time.time()
        try:
            if p == '/api/import':
                data = self._body(MAX_UPLOAD)
                acct = int(qs.get('account') or 0)
                if not db.one('SELECT id FROM accounts WHERE id=?', (acct,)):
                    return self._err(400, 'pick an account')
                name = urllib.parse.unquote(self.headers.get('X-Filename') or 'upload.csv')[:200]
                res = imports.import_file(db, acct, name, data)
                bump()
                return self._send(200, res)
            body = self._json(48_000_000 if p == '/api/agent' else 1_000_000)      # a chat message can carry images
            res = post_action(p, body)
            if p != '/api/agent':
                bump()
            return self._send(200, res)
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:
            traceback.print_exc()
            return self._err(500, str(e)[:300])

    def _file(self, name, extra=None):
        path = os.path.realpath(os.path.join(WEB, name))
        if not path.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(path):
            return self._err(404, 'not found')
        ext = os.path.splitext(path)[1]
        with open(path, 'rb') as f:
            body = f.read()
        self._send(200, body, TYPES.get(ext, 'application/octet-stream'), extra)


def slim(m):
    """What the page needs: drop per-lot detail and long arrays from positions."""
    out = dict(m)
    out['positions'] = [{k: v for k, v in p.items() if k != 'lots'} for p in m['positions']]
    today = date.today().isoformat()
    out['macro'] = [x for x in (db.setting('macro_auto', []) or []) if x['d'] >= today and x['label'] == 'FOMC decision'][:3]
    out['app'] = {'mode': paths.MODE, 'version': paths.version(), 'ready': setup_done(), 'alerts': push.AVAILABLE,
                  'phone_url': CONFIG.get('phone_url')}

    return out


def activity(qs):
    acct = qs.get('account')
    sym = (qs.get('symbol') or '').upper()
    kind = qs.get('kind') or ''
    limit = min(int(qs.get('limit') or 400), 3000)
    sql = ('SELECT r.id, r.account_id, r.import_id, r.fp, r.data, r.activity_date, a.name AS account '
           'FROM raw_rows r JOIN accounts a ON a.id=r.account_id')
    where, args = [], []
    if acct:
        where.append('r.account_id=?')
        args.append(int(acct))
    rows = db.q(sql + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY r.activity_date DESC, r.import_id, r.pos', args)
    # inside the dates an uploaded file covers, the file's rows are the record and SnapTrade's copies of the same
    # trades are left out, exactly as the numbers are worked out (fin/imports.py account_events)
    windows = {w['account_id']: (w['a'], w['b']) for w in db.q(
        "SELECT account_id, MIN(activity_date) AS a, MAX(activity_date) AS b FROM raw_rows WHERE fp NOT LIKE 'snap:%' "
        "AND fp NOT LIKE 'snapshot:%' GROUP BY account_id")}
    out = []
    for r in rows:
        win = windows.get(r['account_id'])
        if win and r['fp'].startswith('snap:') and win[0] < (r['activity_date'] or '') < win[1]:
            continue
        data = json.loads(r['data'])
        if 'positions' in data:
            e = {'date': r.get('activity_date') or '', 'kind': 'snapshot', 'sub': 'positions', 'symbol': '',
                 'desc': f"Positions snapshot ({len(data['positions'])} holdings)", 'amount': 0, 'code': 'SNAP'}
        else:
            e = imports.normalize_row(r['fp'], data)
            if not e:
                continue
        if sym and e.get('symbol') != sym and not (e.get('option') and e['option']['underlying'].rstrip('0123456789') == sym):
            continue
        if kind and e['kind'] != kind and not (kind == 'option' and e['kind'].startswith('option')):
            continue
        out.append({'id': r['id'], 'account': r['account'], 'import_id': r['import_id'], 'date': e['date'],
                    'kind': e['kind'], 'sub': e.get('sub'), 'code': e.get('code'), 'symbol': e.get('symbol'),
                    'qty': e.get('qty'), 'price': e.get('price'), 'amount': e.get('amount'), 'desc': e.get('desc'),
                    'option': e.get('option')})
        if len(out) >= limit:
            break
    return {'rows': out, 'total': len(out)}


def symbol_detail(sym):
    if not sym:
        raise ValueError('symbol')
    m = model()
    bars = db.q('SELECT d, close FROM prices WHERE symbol=? ORDER BY d', (sym,))
    splits = db.q('SELECT d, ratio FROM splits WHERE symbol=?', (sym,))
    prof = db.one('SELECT profile, name, type FROM symbols WHERE symbol=?', (sym,))
    pos = next((p for p in m['positions'] if p['symbol'] == sym), None)
    acts = activity({'symbol': sym, 'limit': 500})['rows']
    inc = [r for r in m['income']['received_rows'] if r['symbol'] == sym]
    divs = db.q("SELECT ex, amount, pay, source FROM divs WHERE symbol=? ORDER BY ex DESC LIMIT 40", (sym,))
    return {'symbol': sym, 'bars': [[b['d'], b['close']] for b in bars], 'splits': splits,
            'profile': json.loads(prof['profile']) if prof and prof['profile'] else None,
            'position': pos, 'activity': acts, 'income': inc[:100], 'dividends': divs,
            'quote': sync.cached_quotes(db).get(sym), 'income_projection': m['income']['by_symbol'].get(sym)}


def snap_state():
    st = snaptrade.public_state()
    linked = db.q('SELECT s.snap_id, s.account_id, s.institution, s.name, s.number, s.kind, s.synced_at, s.last_error, s.authorization_id, '
                  'a.name AS console_name FROM snap_accounts s LEFT JOIN accounts a ON a.id=s.account_id ORDER BY s.institution')
    m = model()
    diffs = []
    for row in linked:
        theirs, at = snaptrade.broker_positions(db, row['account_id'])
        if theirs is None:
            continue
        ours = {}
        for pos in m['positions']:
            q = sum(x['qty'] for x in pos['accounts'] if x['id'] == row['account_id'])
            if q:
                ours[pos['symbol']] = q
        for d in reconcile.diffs(ours, theirs):
            diffs.append({**d, 'account_id': row['account_id'], 'account': row['console_name'], 'at': at})
    held = sum(1 for pos in m['positions'] if any(x['id'] in {r['account_id'] for r in linked} for x in pos['accounts']))
    suspicious = len(diffs) > max(5, 0.3 * held)
    return {**st, 'linked': linked, 'diffs': diffs, 'suspicious': suspicious}


def setup_data():
    return {'accounts': db.q('SELECT * FROM accounts ORDER BY sort, id'),
            'imports': db.q('SELECT * FROM imports ORDER BY id DESC'),
            'opening': db.q('SELECT * FROM opening ORDER BY account_id, symbol'),
            'balances': db.q('SELECT * FROM balances ORDER BY account_id, d'),
            'manual': db.q('SELECT * FROM manual ORDER BY id'),
            'basis': db.q('SELECT * FROM basis ORDER BY account_id, symbol, d'),
            'maintenance': db.setting('maintenance', {}) or {},
            'type_groups': analytics.type_groups(db),
            'option_rules': {**options.DEFAULT_RULES, **(db.setting('option_rules', {}) or {})},
            'log': db.q('SELECT * FROM log ORDER BY id DESC LIMIT 50')}


def _num(v, allow_none=True):
    if v in (None, ''):
        if allow_none:
            return None
        raise ValueError('number required')
    return float(str(v).replace('$', '').replace(',', ''))


def _d(v):
    return date.fromisoformat(str(v)).isoformat()


def post_action(p, b):
    if p == '/api/import/undo':
        n = imports.undo_import(db, int(b['id']))
        return {'removed': n}
    if p == '/api/account':
        name = (b.get('name') or '').strip()[:60]
        if not name:
            raise ValueError('name required')
        kind = b.get('kind') if b.get('kind') in ('taxable', 'roth', 'traditional', 'hsa', 'crypto', 'other') else 'taxable'
        broker = (b.get('broker') or 'other').strip()[:30].lower()
        if b.get('id'):
            db.x('UPDATE accounts SET name=?, broker=?, kind=?, margin=? WHERE id=?',
                 (name, broker, kind, 1 if b.get('margin') else 0, int(b['id'])))
            return {'id': int(b['id'])}
        i = db.x('INSERT INTO accounts(name, broker, kind, margin, sort) VALUES(?,?,?,?,?)',
                 (name, broker, kind, 1 if b.get('margin') else 0, 10))
        db.log('account added', name)
        return {'id': i}
    if p == '/api/account/delete':
        aid = int(b['id'])
        if db.one('SELECT id FROM raw_rows WHERE account_id=? LIMIT 1', (aid,)):
            raise ValueError('This account has imported rows. Undo its imports first.')
        db.x('DELETE FROM accounts WHERE id=?', (aid,))
        return {'ok': True}
    if p == '/api/balance':
        aid = int(b['account_id'])
        kind = 'equity' if b.get('kind') == 'equity' else 'cash'
        v = _num(b.get('value'), False)
        if b.get('margin_balance'):
            v = -abs(v)
            kind = 'cash'
        i = db.x('INSERT INTO balances(account_id, d, kind, value, note) VALUES(?,?,?,?,?)',
                 (aid, _d(b.get('d') or date.today().isoformat()), kind, v, (b.get('note') or '')[:200]))
        db.log('balance confirmed', {'account': aid, 'kind': kind, 'value': v})
        return {'id': i}
    if p == '/api/balance/delete':
        db.x('DELETE FROM balances WHERE id=?', (int(b['id']),))
        return {'ok': True}
    if p == '/api/maintenance':
        aid = int(b['account_id'])
        known = _num(b.get('requirement'))
        if known:
            m = model(force=True)
            mg = next((x for x in m['margin'] if x['account'] == db.one('SELECT name FROM accounts WHERE id=?', (aid,))['name']), None)
            acct = db.one('SELECT maint_scale FROM accounts WHERE id=?', (aid,))
            cur = acct['maint_scale'] or 1.0
            if mg and mg['requirement'] > 0:
                # only holdings without a rate of their own move with the scale
                fixed = sum(r['req'] for r in mg['rows'] if r['why'] in ('short put held as cash', 'your rate', 'long option'))
                free = mg['requirement'] - fixed
                scale = max(0.2, min(4.0, cur * (known - fixed) / free)) if free > 0 and known > fixed else cur
                db.x('UPDATE accounts SET maint_scale=?, maint_note=? WHERE id=?',
                     (round(scale, 4), f"Scaled so {_mdy(date.today())} matches the ${known:,.0f} maintenance you entered.", aid))
        overrides = db.setting('maintenance', {}) or {}
        for sym, pct in (b.get('overrides') or {}).items():
            sym = sym.strip().upper()
            if pct in (None, ''):
                overrides.pop(sym, None)
            else:
                overrides[sym] = max(0.0, min(100.0, float(pct)))
        db.set_setting('maintenance', overrides)
        return {'ok': True}
    if p == '/api/agent':
        cfg = db.setting('agent', {}) or {}
        new = dict(cfg)
        if b.get('model') in agent.MODELS:
            new['model'] = b['model']
        if b.get('effort') in agent.EFFORTS:
            new['effort'] = b['effort']
        for k in ('enabled', 'web', 'shell'):
            if k in b:
                new[k] = bool(b[k])
        if new != cfg:
            cfg = new
            db.set_setting('agent', cfg)
        act, chat = b.get('action'), b.get('chat') or None
        if act == 'send':
            return agent.send(cfg, b.get('message'), b.get('files'), chat)
        if act == 'stop':
            return agent.stop()
        if act == 'retry':
            return agent.retry(cfg, chat)
        if act == 'unqueue':
            return agent.unqueue(chat, str(b.get('id') or ''))
        if act == 'rename':
            return agent.rename(chat, b.get('title'))
        if act == 'delete':
            return agent.delete_chat(chat)
        if act == 'undo':
            return agent.undo_change(str(b.get('id') or ''))
        if act == 'apply':
            return agent.apply(str(b.get('id') or ''))
        return {'ok': True}
    if p == '/api/type-groups':
        groups = {}
        for key, name in (('bitcoin', analytics.BITCOIN), ('crypto', analytics.CRYPTO), ('none', 'none')):
            for sym in str(b.get(key) or '').replace(' ', ',').split(','):
                sym = sym.strip().upper()
                if sym and len(sym) <= 12 and all(ch.isalnum() or ch in '.-' for ch in sym):
                    groups[sym] = name
        db.set_setting('type_groups', groups)
        return {'ok': True}
    if p == '/api/moves':
        if not STATE.get('doom'):
            try:
                build_doom()
            except Exception as e:
                log('doom summary:', e)
        return moves.save(db, model(), STATE.get('doom') or {}, sync.cached_quotes(db), b)
    if p == '/api/dip/watch':
        sym = (b.get('symbol') or '').strip().upper()
        if not sym or len(sym) > 12 or not all(ch.isalnum() or ch in '.-' for ch in sym):
            raise ValueError('symbol')
        w = doom_watch()
        if b.get('remove'):
            w = [x for x in w if x != sym]
        elif sym not in w:
            w.append(sym)
        db.set_setting('doom_watch', w)
        STATE['dip'] = None
        if not b.get('remove'):
            def fetch():
                try:
                    first = (date.today() - timedelta(days=4 * 366)).isoformat()
                    sync.history(db, [sym], first, log=log)
                    sync.quotes(db, [sym])
                    STATE['quote_v'] += 1
                    dip.refresh(db, sync.YH, [(sym, 'stock')], log=log)
                except Exception as e:
                    log('dip watch', sym, e)
                STATE['dip'] = None
            threading.Thread(target=fetch, daemon=True).start()
        return {'watch': w}
    if p == '/api/margin-rate':
        acct = db.one('SELECT broker FROM accounts WHERE id=?', (int(b['account_id']),))
        if not acct:
            raise ValueError('pick an account')
        rates = db.setting('margin_rates', {}) or {}
        if b.get('rate') in (None, ''):
            rates.pop(acct['broker'], None)               # back to the rate the interest charges imply
        else:
            rate = float(b['rate']) / 100
            if not 0 < rate < 0.3:
                raise ValueError('Enter the rate as a percent a year, for example 6.25.')
            rates[acct['broker']] = {'tiers': [[0, rate]], 'free': max(float(b.get('free') or 0), 0), 'note': 'the rate you entered'}
        db.set_setting('margin_rates', rates)
        return {'ok': True}
    if p == '/api/margin-nudge':
        # the broker shows a different buffer: move the rate of holdings without a rate of their own until the estimate matches
        aid = int(b['account_id'])
        target = _num(b.get('buffer'))
        if target is None:
            raise ValueError('buffer')
        m = model(force=True)
        name = db.one('SELECT name FROM accounts WHERE id=?', (aid,))['name']
        mg = next((x for x in m['margin'] if x['account'] == name), None)
        if not mg:
            raise ValueError('not a margin account')
        cur = db.one('SELECT maint_scale FROM accounts WHERE id=?', (aid,))['maint_scale'] or 1.0
        free = sum(r['req'] for r in mg['rows'] if r['why'] not in ('short put held as cash', 'your rate', 'long option'))
        want = free + (mg['excess'] - target)
        if free <= 0 or want <= 0:
            raise ValueError('That buffer cannot be reached by changing the estimated rates; check the loan first.')
        scale = max(0.2, min(4.0, cur * want / free))
        db.x('UPDATE accounts SET maint_scale=?, maint_note=? WHERE id=?',
             (round(scale, 4), f"Holdings with the broker's own rate use it; the rest are calibrated so the estimate matched the ${target:,.0f} buffer the broker showed on {_mdy(date.today())}.", aid))
        db.log('margin estimate nudged', {'account': aid, 'buffer': target, 'estimate_before': mg['excess'], 'old_scale': cur, 'new_scale': scale})
        bump()
        return {'ok': True, 'before': mg['excess'], 'scale': scale}
    if p == '/api/opening':
        aid = int(b['account_id'])
        sym = (b.get('symbol') or '').strip().upper()
        if not sym:
            raise ValueError('symbol required')
        qty = _num(b.get('qty'), False)
        if b.get('id'):
            db.x('UPDATE opening SET symbol=?, qty=?, cost=?, note=? WHERE id=?',
                 (sym, qty, _num(b.get('cost')), (b.get('note') or '')[:200], int(b['id'])))
            return {'id': int(b['id'])}
        first = db.one('SELECT MIN(activity_date) AS d FROM raw_rows WHERE account_id=?', (aid,))['d']
        as_of = (date.fromisoformat(first) - timedelta(days=1)).isoformat() if first else date.today().isoformat()
        i = db.x('INSERT INTO opening(account_id, symbol, qty, cost, as_of, note) VALUES(?,?,?,?,?,?)',
                 (aid, sym, qty, _num(b.get('cost')), as_of, (b.get('note') or 'added by hand')[:200]))
        return {'id': i}
    if p == '/api/opening/delete':
        db.x('DELETE FROM opening WHERE id=?', (int(b['id']),))
        return {'ok': True}
    if p == '/api/basis':
        db.x('INSERT OR REPLACE INTO basis(account_id, symbol, d, cost) VALUES(?,?,?,?)',
             (int(b['account_id']), b['symbol'].upper(), _d(b['d']), _num(b['cost'], False)))
        return {'ok': True}
    if p == '/api/manual':
        label = (b.get('label') or '').strip()[:60]
        if not label:
            raise ValueError('label required')
        sym = (b.get('symbol') or '').strip().upper() or None
        vals = (label, b.get('kind') or 'other', sym, _num(b.get('qty')), _num(b.get('value')), _num(b.get('cost')),
                1 if b.get('liability') else 0, (b.get('note') or '')[:200])
        if b.get('id'):
            db.x('UPDATE manual SET label=?, kind=?, symbol=?, qty=?, value=?, cost=?, liability=?, note=?, '
                 'updated=CURRENT_TIMESTAMP WHERE id=?', vals + (int(b['id']),))
            return {'id': int(b['id'])}
        return {'id': db.x('INSERT INTO manual(label, kind, symbol, qty, value, cost, liability, note) '
                           'VALUES(?,?,?,?,?,?,?,?)', vals)}
    if p == '/api/manual/delete':
        db.x('DELETE FROM manual WHERE id=?', (int(b['id']),))
        return {'ok': True}
    if p == '/api/push/subscribe':
        return {'id': push.subscribe(db, b.get('subscription'), b.get('label'))}
    if p == '/api/push/remove':
        db.x('DELETE FROM push_subs WHERE id=?', (int(b['id']),))
        return {'ok': True}
    if p == '/api/push/test':
        n = push.deliver(db, {'title': 'Basis', 'body': 'Notifications work on this device.', 'url': '/settings', 'tag': 'test'},
                         only=int(b['id']) if b.get('id') else None, log=log)
        return {'sent': n}
    if p == '/api/alerts':
        cur = {**ALERT_DEFAULTS, **(db.setting('alerts', {}) or {})}
        for k in ('monday_menu', 'margin'):
            if k in b:
                cur[k] = bool(b[k])
        for k in ('margin_threshold', 'big_move_pct'):
            if k in b and b[k] not in (None, ''):
                cur[k] = float(b[k])
        db.set_setting('alerts', cur)
        return cur
    if p == '/api/first-run':
        act = b.get('action')
        if act == 'sync':
            return first_sync()
        if act == 'agent':                                # 'skipped' or 'done': the optional Claude step was answered
            cur = db.setting('first_run', {}) or {}
            db.set_setting('first_run', {**cur, 'agent_step': str(b.get('value') or 'skipped')[:20]})
            return {'ok': True}
        raise ValueError('unknown action')
    if p == '/api/open':
        return open_link(b.get('url'))
    if p == '/api/quit':
        if not DESKTOP:
            raise ValueError('Only the desktop program quits this way.')
        threading.Timer(0.3, lambda: os._exit(0)).start()
        return {'ok': True}
    if p == '/api/update':
        cfg = db.setting('update', {}) or {}
        if 'check' in b:
            cfg['check'] = bool(b['check'])
            db.set_setting('update', cfg)
        act = b.get('action')
        if act == 'check':
            update.check(cfg, force=True)
        elif act == 'apply':
            if agent._run['busy']:
                raise ValueError('Claude is working in the Customize chat. Wait for it to finish, then update.')
            return update.apply(agent.snapshot, restart)
        elif act == 'dismiss':
            update.dismiss()
        return update.state(cfg)
    if p == '/api/snaptrade/keys':
        conf = snaptrade.load()
        cid, key = (b.get('client_id') or '').strip(), (b.get('consumer_key') or '').strip()
        if not cid or not key:
            raise ValueError('Both the client id and the consumer key are needed.')
        if cid != conf.get('client_id'):
            conf = {}                                     # a different SnapTrade app: start a fresh user
        conf.update(client_id=cid, consumer_key=key)
        snaptrade.save(conf)
        db.log('snaptrade keys saved', {'client_id': cid})
        return snaptrade.public_state()
    if p == '/api/snaptrade/connect':
        try:
            return {'url': snaptrade.Client().login_url(broker=(b.get('broker') or None))}
        except snaptrade.SnapError as e:
            raise ValueError(str(e))
    if p == '/api/snaptrade/sync':
        try:
            res = snaptrade.sync(db, log=log)
        except snaptrade.SnapError as e:
            raise ValueError(str(e))
        STATE['snap_at'] = time.time()
        done = set(db.setting('snap_reconciled', None) or [])
        if any(r['snap_id'] not in done for r in db.q('SELECT snap_id FROM snap_accounts WHERE account_id IS NOT NULL')):
            first_sync()                                  # a brokerage linked just now: fetch its history and match its counts
            res['first_sync'] = True
        return res
    if p == '/api/snaptrade/map':
        db.x('UPDATE snap_accounts SET account_id=? WHERE snap_id=?', (int(b['account_id']), b['snap_id']))
        return {'ok': True}
    if p == '/api/snaptrade/disconnect':
        try:
            snaptrade.Client().delete_connection(b['authorization_id'])
        except snaptrade.SnapError as e:
            raise ValueError(str(e))
        db.x('DELETE FROM snap_accounts WHERE authorization_id=?', (b['authorization_id'],))
        return {'ok': True}
    if p == '/api/snaptrade/match':
        state = snap_state()
        if state['suspicious']:
            raise ValueError('The broker list looks incomplete; sync again before matching.')
        rows = state['diffs']
        n = 0
        for r in rows:
            if b.get('symbol') and r['symbol'] != b['symbol']:
                continue
            reconcile.apply(db, r['account_id'], r, 'SnapTrade')
            n += 1
        return {'changed': n}
    if p == '/api/reconcile':
        aid = int(b['account_id'])
        acct = db.one('SELECT name FROM accounts WHERE id=?', (aid,))
        sym = (b.get('symbol') or '').strip().upper()
        theirs = _num(b.get('qty'), False)
        m = model(force=True)
        ours = 0.0
        for pos in m['positions']:
            if pos['symbol'] == sym:
                ours = sum(x['qty'] for x in pos['accounts'] if x['id'] == aid)
        rows = reconcile.diffs({sym: ours}, {sym: {'qty': theirs, 'avg_cost': _num(b.get('avg_cost'))}})
        for r in rows:
            reconcile.apply(db, aid, r, b.get('source') or 'your count')
        return {'changed': rows, 'account': acct['name'] if acct else None}
    if p == '/api/option-rules':
        cur = db.setting('option_rules', {}) or {}
        for k in ('target_low', 'target_high'):
            if k in b:
                cur[k] = float(b[k])
        if 'watch' in b:
            cur['watch'] = [s.strip().upper() for s in b['watch'] if s.strip()][:60]
        if 'macro_dates' in b:
            cur['macro_dates'] = sorted({_d(x) for x in b['macro_dates'] if x})
        if 'symbols' in b:
            cur['symbols'] = {k.upper(): v for k, v in b['symbols'].items()}
        db.set_setting('option_rules', cur)
        STATE['menu'] = None
        return {'ok': True}
    raise ValueError('unknown action')


def main():
    if not DESKTOP:
        reconcile_new()                                   # only marks what an older copy already had as done
    threading.Thread(target=worker, daemon=True).start()
    servers = []
    for host in CONFIG['binds']:
        srv = ThreadingHTTPServer((host, CONFIG['port']), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        log(f'listening on {host}:{CONFIG["port"]}')
    while True:
        time.sleep(3600)


if __name__ == '__main__':
    main()
