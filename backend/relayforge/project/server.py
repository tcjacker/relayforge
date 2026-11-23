"""本机服务：rf.py serve 显示本机所有登记过的项目。"""

import json
import re
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..core.common import DIR, PORT, git, is_project, log, main_checkout, worktrees
from ..facts.designs import DESIGN_CSP
from ..sessions.record import current_transcript, read_session
from .registry import project_summaries, projects, register
from ..facts.page import PAGE, collect
from ..facts.extract import sync


def page_html(root, name=None):
    data = collect(root)
    data['designBase'] = 'rf/'  # served by the local server under /p/<name>/rf/
    if name:
        data.update(project=name, projects=project_summaries(name))
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            '<body style="margin:0">' + PAGE.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('</', '<\\/')) + '</body></html>')


def version(root):
    stamps = [p.stat().st_mtime for p in (root / DIR).rglob('*.md') if 'site' not in p.parts]
    return f'{max(stamps, default=0):.3f}-{git(root, "rev-parse", "HEAD").strip()[:12]}-{len(projects())}'


LOOPBACK = {'127.0.0.1', '::1', 'localhost'}
SYNC_LIMIT = 1 << 20
MAX_REQUESTS = 32  # /api/sync 的请求只有几个路径和编号，1 MB 足够；再大就是有人在顶内存


def _host_name(header):
    """The host part of a Host or Origin header ('localhost:8765' → 'localhost', '[::1]:80' → '::1')."""
    text = str(header or '').strip().lower()
    if '://' in text:
        text = text.split('://', 1)[1].split('/', 1)[0]
    if text.startswith('['):
        return text[1:text.find(']')] if ']' in text else text
    return text.rsplit(':', 1)[0] if text.count(':') == 1 else text


def serve(root=None, port=PORT, host='127.0.0.1'):
    """Local, unauthenticated server for every registered project: pages, facts as JSON, and a sync endpoint.

    No login, so who may ask is limited instead:
      - Bound to this machine only (the default): requests must name this machine in Host. A web page can point a domain of
        its own at 127.0.0.1 (DNS rebinding) and then read the pages and session summaries; with the Host check it cannot.
      - /api/sync (it runs the model and rewrites the records) only from this machine, only JSON (a web page cannot send
        that without a preflight we never answer), never with another site's Origin, and at most SYNC_LIMIT bytes."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    busy = threading.Lock()
    home = register(root) if root else None
    local_only = _host_name(host) in LOOPBACK

    def default():
        ranked = project_summaries()
        return next((p for p in ranked if p['last']), None) or home or (ranked[0] if ranked else None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, kind='application/json; charset=utf-8', extra=()):
            raw = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            for key, value in extra:
                self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)

        def do_GET(self):
            path = urllib.parse.unquote(self.path.split('?')[0])
            if path == '/':
                pick = default()
                if not pick:
                    return self.reply(200, '<!doctype html><meta charset="utf-8"><title>RelayForge</title><p style="font:16px system-ui;margin:40px">还没有登记任何项目。在项目目录里运行 <code>python3 rf.py init .</code>，然后刷新这个页面。</p>', 'text/html; charset=utf-8')
                return self.reply(302, '', 'text/plain', [('Location', f'/p/{urllib.parse.quote(pick["name"])}/')])
            m = re.fullmatch(r'/p/([^/]+)(/.*)?', path)
            if m:
                entry = next((p for p in projects() if p['name'] == m.group(1)), None)
                if not entry:
                    return self.reply(404, {'error': 'no such project'})
                if not m.group(2):
                    return self.reply(302, '', 'text/plain', [('Location', f'/p/{urllib.parse.quote(entry["name"])}/')])
                rest = m.group(2)
            else:  # addresses from before there were several projects
                entry, rest = home or default(), path
                if not entry:
                    return self.reply(404, {'error': 'no project'})
            base = Path(entry['root'])
            if rest == '/':
                return self.reply(200, page_html(base, entry['name']), 'text/html; charset=utf-8')
            if rest == '/api/version':
                return self.reply(200, {'version': version(base)})
            if rest == '/api/state':
                return self.reply(200, dict(collect(base), project=entry['name'], projects=project_summaries(entry['name'])))
            if rest == '/api/projects':
                return self.reply(200, {'projects': project_summaries(entry['name'])})
            drawn = re.fullmatch(r'/rf/(designs|history)/(DS-\d{8}-[0-9a-f]{4})\.html', rest)
            if drawn and (base / DIR / drawn.group(1) / f'{drawn.group(2)}.html').is_file():
                return self.reply(200, (base / DIR / drawn.group(1) / f'{drawn.group(2)}.html').read_bytes(), 'text/html; charset=utf-8', [
                    ('Content-Security-Policy', DESIGN_CSP), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer')])
            found = re.fullmatch(r'/api/sessions/([0-9A-Za-z-]+)\.md', rest)
            target = base / DIR / 'sessions' / f'{found.group(1)}.md' if found else None
            target = target if target and target.is_file() else None
            if target:
                return self.reply(200, target.read_text(encoding='utf-8'), 'text/markdown; charset=utf-8')
            self.reply(404, {'error': 'not found'})

        do_HEAD = do_GET

        def refused(self):
            """Why this request is not answered (see serve), or None."""
            if local_only and _host_name(self.headers.get('Host')) not in LOOPBACK:
                return 421, '这个服务只认本机的地址（127.0.0.1、localhost）。'
            return None

        def parse_request(self):
            if not super().parse_request():
                return False
            why = self.refused()
            if why:
                self.close_connection = True
                self.reply(why[0], {'error': why[1]})
                return False
            return True

        def do_POST(self):
            if self.path.split('?')[0] != '/api/sync':
                return self.reply(404, {'error': 'not found'})
            if _host_name(self.client_address[0]) not in LOOPBACK and not str(self.client_address[0]).startswith('::ffff:127.'):
                return self.reply(403, {'error': '同步只接受本机发来的请求。'})
            if (self.headers.get('Content-Type') or '').split(';')[0].strip().lower() != 'application/json':
                return self.reply(415, {'error': '只接受 JSON（Content-Type: application/json）。'})
            origin = self.headers.get('Origin')
            if origin and _host_name(origin) not in LOOPBACK:
                return self.reply(403, {'error': '不接受别的网站发来的同步请求。'})
            try:
                size = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                size = -1
            if size < 0 or size > SYNC_LIMIT:
                return self.reply(413, {'error': f'请求太大（上限 {SYNC_LIMIT} 字节）。'})
            try:
                body = json.loads(self.rfile.read(size) or b'{}')
                asked = Path(body['root']).resolve() if body.get('root') else (Path(home['root']) if home else None)
                if not asked or not is_project(asked):
                    return self.reply(400, {'error': '这个目录还没有初始化，先运行 rf.py init。'})
                entry = register(asked)
                place = asked if asked in worktrees(Path(entry['root'])) else Path(entry['root'])
                with busy:
                    out = sync(place, body['transcript'], body['session_id'], body.get('name'), bool(body.get('end')))
                if not out:
                    return self.reply(200, {'file': None, 'session': None, 'project': entry['name']})
                home_dir = main_checkout(place)  # the session file is in the main checkout, wherever the session ran
                self.reply(200, {'project': entry['name'], 'root': str(home_dir), 'file': out.relative_to(home_dir).as_posix(), 'session': read_session(out)})
            except Exception as error:
                log(Path(home['root']) if home else Path(tempfile.gettempdir()), f'sync failed: {error!r}')
                self.reply(500, {'error': str(error)})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        address_family = socket.AF_INET6 if ':' in host else socket.AF_INET
        slots = threading.BoundedSemaphore(MAX_REQUESTS)  # 同时处理的请求有上限：每个请求一个线程，不设上限会被连接顶爆

        def process_request(self, request, client_address):
            self.slots.acquire()  # 满了就等前面的做完再接新的
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()

        def server_bind(self):
            if self.address_family == socket.AF_INET6:  # one socket for IPv4 and IPv6
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    return Server((host, port), Handler)


def sync_command(root, session_id, transcript, port, local, name=None, end=False):
    """Sync through the local server when it is running, otherwise right here."""
    found, session_id = (Path(transcript), session_id or Path(transcript).stem) if transcript else current_transcript(root, session_id)
    if not found or not Path(found).is_file():
        print('没有找到这次会话的记录文件。可以用 --transcript 指定。', file=sys.stderr)
        return 1
    out, where, project = None, '本机服务', register(root)['name']
    if not local:
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/sync', method='POST', headers={'Content-Type': 'application/json'},
                                         data=json.dumps({'transcript': str(found), 'session_id': session_id, 'root': str(root), 'name': name, 'end': end}).encode('utf-8'))
        try:
            with urllib.request.urlopen(request, timeout=1800) as response:
                answer = json.loads(response.read())
                out = Path(answer.get('root') or root) / answer['file'] if answer.get('file') else None
                project = answer.get('project') or project
        except urllib.error.HTTPError as error:
            print(f'同步失败：{error.read().decode("utf-8", "replace")}', file=sys.stderr)
            return 1
        except (urllib.error.URLError, OSError):
            local = True
    if local:
        where = '本地（本机服务没有运行）'
        try:
            out = sync(root, str(found), session_id, name, end)
        except Exception as error:
            log(root, f'sync failed: {error!r}')
            print(f'同步失败：{error}', file=sys.stderr)
            return 1
    if not out:
        print('这次会话还没有内容可以整理。')
        return 0
    print(f'已同步，由{where}处理。会话文件：{out.resolve()}')
    if not local:
        print(f'页面：http://127.0.0.1:{port}/p/{urllib.parse.quote(project)}/#{out.stem}')
    print()
    print(out.read_text(encoding='utf-8'))
    return 0
