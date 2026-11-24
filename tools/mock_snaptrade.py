"""A stand-in for SnapTrade's API, for tests and screenshots. It serves the made-up portfolio in demo_data.json in the
shapes fin/snaptrade.py reads, including a pretend connection portal, so the whole first-run flow can be exercised
without a real brokerage.

    python3 tools/mock_snaptrade.py [--port 8460] [--connected]

Point the program at it with SNAPTRADE_BASE=http://127.0.0.1:8460 and BASIS_TEST=1. Any client id and key are accepted.
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, 'demo_data.json'), encoding='utf-8') as f:
    DEMO = json.load(f)
PORT = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else 8460
STATE = {'connected': '--connected' in sys.argv}
CONN = 'demo-connection'
PORTAL = """<!doctype html><meta charset="utf-8"><title>Demo Brokerage</title>
<body style="font:16px system-ui;background:#0A0A14;color:#EEEDF8;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h1>Demo Brokerage</h1><p>This page stands in for SnapTrade's connection portal in tests.</p>
<p><a id="go" href="/portal/done" style="color:#7FD3E6">Link the demo accounts</a></p></div>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def out(self, obj, code=200, ctype='application/json'):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        p = u.path
        n = int(self.headers.get('Content-Length') or 0)
        if n:
            self.rfile.read(n)
        if p == '/portal':
            return self.out(PORTAL, ctype='text/html; charset=utf-8')
        if p == '/portal/done':
            STATE['connected'] = True
            return self.out('<!doctype html><meta charset="utf-8"><body style="font:16px system-ui;background:#0A0A14;color:#EEEDF8;padding:40px">Linked. You can close this tab.', ctype='text/html; charset=utf-8')
        if not q.get('clientId') or not self.headers.get('Signature'):
            return self.out({'detail': 'missing clientId or Signature'}, 401)
        if p == '/snapTrade/login':
            return self.out({'redirectURI': f'http://127.0.0.1:{PORT}/portal'})
        if p == '/brokerages':
            return self.out([{'slug': 'DEMO', 'display_name': DEMO['brokerage']}])
        if p == '/authorizations':
            return self.out([{'id': CONN, 'type': 'read', 'disabled': False, 'brokerage': {'slug': 'DEMO', 'name': DEMO['brokerage'], 'display_name': DEMO['brokerage']}}] if STATE['connected'] else [])
        if p.startswith('/connection/') and self.command == 'DELETE':
            STATE['connected'] = False
            return self.out({})
        if not STATE['connected']:
            return self.out({'results': []} if p == '/accounts/all' else [])
        if p == '/accounts/all':
            return self.out({'results': [{'id': a['id'], 'connection_id': CONN, 'display_name': a['display_name'], 'name': a['display_name'],
                                          'masked_account_number': a['number'], 'institution_name': DEMO['brokerage'], 'raw_type': a['raw_type'],
                                          'cash_or_margin': a['cash_or_margin'], 'kind': 'investment', 'status': 'open',
                                          'sync_status': {'holdings': {'initial_sync_completed': True}}} for a in DEMO['accounts']]})
        if p == '/aumPercentile':
            return self.out({'data': {'percentile': 62, 'bucket': 31, 'cohort_size': 48, 'cohort_member_count': 1000, 'currency': 'USD', 'as_of': '2026-11-01'}})
        parts = p.strip('/').split('/')
        if len(parts) >= 3 and parts[0] == 'accounts':
            acct = next((a for a in DEMO['accounts'] if a['id'] == parts[1]), None)
            if not acct:
                return self.out({'detail': 'no such account'}, 404)
            if parts[2] == 'balances':
                return self.out([{'currency': {'code': 'USD'}, 'cash': acct['cash'], 'buying_power': max(acct['cash'], 0)}])
            if parts[2] == 'positions':
                return self.out({'results': acct['positions']})
            if parts[2] == 'activities':
                rows = [a for a in acct['activities'] if q.get('startDate', '') <= a['trade_date'][:10] <= q.get('endDate', '9999')]
                off, lim = int(q.get('offset') or 0), int(q.get('limit') or 1000)
                return self.out({'data': rows[off:off + lim], 'pagination': {'offset': off, 'limit': lim, 'total': len(rows)}})
        return self.out({'detail': 'not found: ' + p}, 404)

    do_GET = do_POST = do_DELETE = handle_any


if __name__ == '__main__':
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    srv.daemon_threads = True
    print('mock SnapTrade on', PORT, flush=True)
    srv.serve_forever()
