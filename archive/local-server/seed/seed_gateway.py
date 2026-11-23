"""Authenticated LAN entry point for the loopback-only development coordinator."""
import argparse
from http.client import HTTPConnection
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs

HERE = Path(__file__).resolve().parent
COOKIE = 'project_guide_session'
SESSION_SECONDS = 86400


def access_settings(path):
    path.parent.mkdir(exist_ok=True)
    settings = {'access_code': secrets.token_urlsafe(18), 'session_key': secrets.token_hex(32)}
    try:
        with open(path, 'x', encoding='utf-8', opener=lambda p, flags: os.open(p, flags, 0o600)) as output:
            json.dump(settings, output)
    except FileExistsError:
        settings = json.loads(path.read_text())
    return settings


def session_value(key):
    expires = str(int(time.time()) + SESSION_SECONDS)
    signature = hmac.new(key.encode(), expires.encode(), hashlib.sha256).hexdigest()
    return expires + '.' + signature


def valid_session(header, key):
    try:
        cookie = SimpleCookie()
        cookie.load(header or '')
        expires, signature = cookie[COOKIE].value.split('.')
        expected = hmac.new(key.encode(), expires.encode(), hashlib.sha256).hexdigest()
        return int(expires) > time.time() and secrets.compare_digest(signature, expected)
    except (CookieError, KeyError, ValueError, TypeError):
        return False


def login_page(error=False):
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · Project Guide</title>
<style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;
background:#f6f7fb;color:#293142;font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
main{width:min(400px,calc(100vw - 40px));padding:32px;background:white;border:1px solid #e7e9f1;border-radius:12px}
b{display:grid;place-items:center;width:36px;height:36px;background:#5850d8;color:white;border-radius:9px;font-size:22px}
h1{font-size:23px;margin:20px 0 5px}p{font-size:13px;color:#8992a4;margin:0 0 25px}label{font-size:13px}
input{display:block;width:100%;padding:12px;border:1px solid #dce0ec;border-radius:6px;font:inherit;margin:8px 0 18px}
input:focus{outline:2px solid #c9c5fb}button{background:#5850d8;border:0;color:white;width:100%;padding:12px;
border-radius:6px;font:inherit;cursor:pointer}.error{color:#b25365;font-size:13px;margin-top:12px}</style>
<main><b>P</b><h1>连接 Project Guide</h1><p>输入 Mac 上的访问码，进入你的开发工作台。</p>
<form method="post" action="/login"><label>访问码<input type="password" name="access_code"
autocomplete="current-password" autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus></label><button>进入工作台</button></form>''' + (
        '<p class="error" role="alert">访问码不匹配，尚未登录。请完整复制 Mac 提供的访问码，注意大小写。</p>' if error else '') + '</main></html>'


class Gateway(BaseHTTPRequestHandler):
    def host_allowed(self):
        host = self.headers.get('Host', '').lower()
        return host in self.server.allowed_hosts

    def send_body(self, status, body, content_type='text/html; charset=utf-8', headers=()):
        content = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

    def authorized(self):
        return valid_session(self.headers.get('Cookie'), self.server.settings['session_key'])

    def do_GET(self):
        if not self.host_allowed():
            return self.send_error(404)
        if self.path == '/login':
            return self.send_body(200, login_page())
        if not self.authorized():
            if self.path.startswith('/api/'):
                return self.send_body(401, '{}', 'application/json')
            return self.send_body(303, '', headers=[('Location', '/login')])
        self.relay()

    def do_POST(self):
        if not self.host_allowed():
            return self.send_error(404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 260000:
                return self.send_error(413)
        except ValueError:
            return self.send_error(400)
        if self.path == '/login':
            if length > 4096:
                return self.send_error(413)
            try:
                params = parse_qs(self.rfile.read(length).decode(errors='replace'), max_num_fields=10)
            except ValueError:
                return self.send_error(400)
            supplied = params.get('access_code', [''])[0].strip().encode()
            expected = self.server.settings['access_code'].encode()
            if not secrets.compare_digest(supplied, expected):
                return self.send_body(401, login_page(True))
            token = session_value(self.server.settings['session_key'])
            cookie = f'{COOKIE}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict'
            return self.send_body(303, '', headers=[('Set-Cookie', cookie), ('Location', '/')])
        if not self.authorized():
            return self.send_body(401, '请先登录后再提交。')
        self.relay(self.rfile.read(length))

    def relay(self, body=None):
        # GET can wait across a coordinator reload. POST is never replayed.
        attempts = 15 if self.command == 'GET' else 1
        for attempt in range(attempts):
            connection = HTTPConnection('127.0.0.1', self.server.backend_port, timeout=5)
            try:
                headers = {}
                if self.headers.get('Content-Type'):
                    headers['Content-Type'] = self.headers['Content-Type']
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                content = response.read()
                forwarded = [(key, value) for key, value in response.getheaders()
                             if key.lower() in ('location', 'x-frame-options')]
                return self.send_body(response.status, content,
                    response.getheader('Content-Type', 'text/html; charset=utf-8'), forwarded)
            except OSError:
                if attempt + 1 == attempts:
                    return self.send_body(503, '后台正在切换或暂时不可用，请稍后刷新。')
                time.sleep(.1)
            finally:
                connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True, help='Mac 的局域网 IPv4 地址')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--backend-port', type=int, default=8765)
    parser.add_argument('--hostname', help='可选的 Mac .local 主机名')
    args = parser.parse_args()
    settings = access_settings(HERE.parents[2] / '.seed' / 'lan-access.json')
    with ThreadingHTTPServer((args.host, args.port), Gateway) as server:
        server.settings = settings
        server.backend_port = args.backend_port
        server.allowed_hosts = {f'{args.host}:{args.port}', f'localhost:{args.port}', f'127.0.0.1:{args.port}'}
        if args.hostname:
            server.allowed_hosts.add(f'{args.hostname.lower()}:{args.port}')
        print(f'iPad URL http://{args.host}:{args.port}/', flush=True)
        print(f'访问码 {settings["access_code"]}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
