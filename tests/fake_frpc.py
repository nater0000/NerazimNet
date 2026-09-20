"""A minimal stand-in for the frpc binary, used by tests.

Mimics the frpc CLI surface NerazimNet uses:

    fake_frpc.py -c <toml>          run "daemon": serve admin API on the
                                    toml's [webServer] addr/port
    fake_frpc.py reload -c <toml>   hit /api/reload on the running instance
    fake_frpc.py stop -c <toml>     hit /api/stop -> running instance exits
    fake_frpc.py status -c <toml>   hit /api/status, print it, exit

Admin API:
  GET /api/status  -> {"tcp": [{name, status, err, remote_port}, ...]}
  GET /api/reload  -> re-reads the toml's proxy list (hot reload), 200
  GET /api/stop    -> shuts the server down
"""
import sys
import json
import argparse
import http.client
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import tomllib
    def _load_toml(path):
        with open(path, 'rb') as f:
            return tomllib.load(f)
except ImportError:
    import toml
    def _load_toml(path):
        with open(path, 'r', encoding='utf-8') as f:
            return toml.load(f)


def _parse_conf(path):
    data = _load_toml(path)
    web = data.get('webServer', {})
    return web.get('addr', '127.0.0.1'), int(web.get('port', 7400))


def _proxies(path):
    try:
        data = _load_toml(path)
    except Exception:
        return []
    return [
        {'name': p.get('name', ''), 'status': 'running', 'err': '',
         'remote_port': p.get('remotePort')}
        for p in data.get('proxies', [])
    ]


def run_daemon(conf_path):
    state = {'conf': conf_path}
    addr, port = _parse_conf(conf_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/api/status':
                self._send({'tcp': _proxies(state['conf'])})
            elif self.path == '/api/reload':
                self._send({'msg': 'reloaded'})  # proxies re-read per request anyway
            elif self.path == '/api/stop':
                self._send({'msg': 'stopping'})
                import threading
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._send({'err': 'not found'}, 404)

    class _Server(HTTPServer):
        def server_bind(self):
            # skip HTTPServer's reverse-DNS getfqdn() — it can hang for tens
            # of seconds on hosts/CI runners with no reverse DNS for loopback
            socketserver.TCPServer.server_bind(self)
            self.server_name = 'localhost'
            self.server_port = self.server_address[1]

    server = _Server((addr, port), Handler)
    print(f"fake frpc serving admin api on {addr}:{port}", flush=True)
    server.serve_forever()


def call_api(conf_path, endpoint):
    # http.client deliberately — urllib/requests honor proxy env vars, which
    # CI runners set and which break loopback calls.
    addr, port = _parse_conf(conf_path)
    try:
        conn = http.client.HTTPConnection(addr, port, timeout=5)
        conn.request('GET', f'/api/{endpoint}')
        print(conn.getresponse().read().decode())
        conn.close()
        return 0
    except Exception as e:
        print(f"api call failed: {e}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', nargs='?', default='run')
    parser.add_argument('-c', '--config', required=True)
    args = parser.parse_args()

    if args.command == 'reload':
        sys.exit(call_api(args.config, 'reload'))
    if args.command == 'stop':
        sys.exit(call_api(args.config, 'stop'))
    if args.command == 'status':
        sys.exit(call_api(args.config, 'status'))
    run_daemon(args.config)


if __name__ == '__main__':
    main()
