from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
import unittest
from urllib.parse import urlencode

import seed_gateway as gateway


class GatewayTests(unittest.TestCase):
    def test_access_settings_persist_and_signed_sessions_expire(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'access.json'
            first = gateway.access_settings(path)
            self.assertEqual(gateway.access_settings(path), first)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch.object(gateway.time, 'time', return_value=100):
            token = gateway.session_value(first['session_key'])
            self.assertTrue(gateway.valid_session(gateway.COOKIE+'='+token, first['session_key']))
            self.assertFalse(gateway.valid_session(gateway.COOKIE+'='+token, 'wrong-key'))
        with patch.object(gateway.time, 'time', return_value=100+gateway.SESSION_SECONDS+1):
            self.assertFalse(gateway.valid_session(gateway.COOKIE+'='+token, first['session_key']))

    def test_login_protects_reads_and_actions_and_preserves_backend_csrf(self):
        requests = []
        class Backend(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(('GET', self.path, self.headers['Host']))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'backend csrf token')
            def do_POST(self):
                requests.append(('POST', self.path, self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(303)
                self.send_header('Location', '/')
                self.end_headers()
            def log_message(self, *args):
                pass
        backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        server = ThreadingHTTPServer(('127.0.0.1', 0), gateway.Gateway)
        server.backend_port = backend.server_port
        server.settings = {'access_code': 'example-secret', 'session_key': 'example-signing-key'}
        server.allowed_hosts = {f'127.0.0.1:{server.server_port}'}
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (backend, server)]
        for thread in threads:
            thread.start()
        def request(method, path, body=None, cookie=None):
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            if cookie:
                headers['Cookie'] = cookie
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                connection.request(method, path, body, headers)
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()
        try:
            status, headers, _ = request('GET', '/')
            self.assertEqual((status, headers['Location']), (303, '/login'))
            self.assertEqual(request('GET', '/api/revision')[0], 401)
            self.assertEqual(request('POST', '/action', 'token=example')[0], 401)
            self.assertFalse(requests)
            status, _, body = request('POST', '/login', urlencode({'access_code': 'wrong'}))
            self.assertEqual(status, 401)
            self.assertNotIn(b'example-secret', body)
            status, headers, _ = request('POST', '/login', urlencode({'access_code': '  example-secret\n'}))
            self.assertEqual(status, 303)
            cookie = headers['Set-Cookie']
            self.assertIn('HttpOnly', cookie)
            self.assertIn('SameSite=Strict', cookie)
            self.assertEqual(request('GET', '/', cookie=cookie)[2], b'backend csrf token')
            self.assertEqual(request('POST', '/action', 'token=original', cookie)[0], 303)
            self.assertEqual(requests[-1], ('POST', '/action', b'token=original'))
            self.assertEqual(request('GET', '/', cookie=cookie.replace('project_guide_session=', 'project_guide_session=broken'))[0], 303)
        finally:
            for instance in (server, backend):
                instance.shutdown()
                instance.server_close()
            for thread in threads:
                thread.join(timeout=2)
