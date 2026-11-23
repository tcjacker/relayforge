"""只在测试里用的「应用」：真的应用的标记和真的会话令牌，不是绕过校验的开关。

- app_marker()：像应用那样生成一个随机标记，在 $RELAYFORGE_HOME/apps/ 里登记它的散列，并放进环境变量。
  进程内调用 rf.main 时，rf.py 的「父进程」本来是跑测试的进程的父进程，那取决于谁启动了测试（后台起的、容器里的父进程可能是 1），
  所以这里把 trust.parent_pid（后端里一个可替换的函数，只能在测试进程里用 mock 换，不读环境变量）换成「本进程」，登记在 os.getpid()；
  subprocess 调用时真的有父进程（就是本进程），也登记在 os.getpid()（subprocess=True）。
- FakeApp：和 app/src/main/session-token.js 同一套协议的验证服务（unix socket，一行 JSON 进一行 JSON 出），密钥只在这个对象里。
  登记在 os.getpid()（rf.py 从自己往上找登记过的应用），issue() 发令牌并记到 os.getpid()（进程内调用时 rf.py 就是这个进程）。
  session(kind, owner, sid) 把令牌放进环境变量，等于「应用开出来的那种会话」。
"""

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from relayforge.core import identity
from relayforge.core import trust


@contextlib.contextmanager
def app_marker(subprocess=False):
    # 在 AI 会话的沙箱里跑测试时，子进程里的 rf.py 也在沙箱里，按设计就不认应用的标记（trust.sandboxed），这类测试只能跳过
    if subprocess and trust.sandboxed():
        raise unittest.SkipTest('测试本身在沙箱里运行：沙箱里的 rf.py 不认应用的标记')
    token = secrets.token_hex(24)
    trust.register(os.getpid(), token)
    with contextlib.ExitStack() as stack:
        if not subprocess:
            stack.enter_context(mock.patch.object(trust, 'parent_pid', return_value=os.getpid()))
            stack.enter_context(mock.patch.object(trust, 'sandboxed', return_value=False))
        stack.enter_context(mock.patch.dict(os.environ, {trust.TOKEN_ENV: token}))
        yield token


def start_app_marker(case, subprocess=False):
    """app_marker() for a whole test: entered now, left when the test ends."""
    manager = app_marker(subprocess)
    token = manager.__enter__()
    case.addCleanup(manager.__exit__, None, None, None)
    return token


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


class FakeApp:
    def __init__(self, pid=None):
        self.key = secrets.token_bytes(32)
        self.pid = pid or os.getpid()
        self.bound = {}
        self.dir = tempfile.mkdtemp(prefix='rfs')
        self.path = os.path.join(self.dir, 'app.sock')
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(8)
        self.stopped = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        apps = trust.apps_dir()
        apps.mkdir(parents=True, exist_ok=True)
        reg = apps / f'{self.pid}.json'
        try:
            data = json.loads(reg.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data.update(pid=self.pid, socket=self.path)
        reg.write_text(json.dumps(data), encoding='utf-8')
        identity.forget()

    def _sign(self, body):
        return _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())

    def issue(self, sid, kind, owner=None, bind=True):
        claims = {'v': 1, 'sid': sid, 'kind': kind, 'owner': owner, 'iat': int(time.time()), 'nonce': secrets.token_hex(12)}
        body = _b64(json.dumps(claims).encode())
        token = body + '.' + self._sign(body)
        if bind:
            self.bound[claims['nonce']] = bind if isinstance(bind, int) and not isinstance(bind, bool) else os.getpid()
        return token

    def _answer(self, line):
        try:
            req = json.loads(line)
            body, sig = str(req.get('token')).split('.')
        except (ValueError, AttributeError):
            return {'ok': False}
        if req.get('op') != 'verify' or not hmac.compare_digest(self._sign(body), sig):
            return {'ok': False}
        claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        return {'ok': True, 'claims': claims, 'pid': self.bound.get(claims.get('nonce'))}

    def _serve(self):
        while not self.stopped:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()  # a slow or vanished client never blocks the others

    def _handle(self, conn):
        try:
            with conn:
                buf = b''
                while b'\n' not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                conn.sendall((json.dumps(self._answer(buf.split(b'\n', 1)[0].decode())) + '\n').encode())
        except OSError:
            pass  # the client gave up (its timeout, a process that died): the server keeps serving

    @contextlib.contextmanager
    def session(self, kind, owner=None, sid=None, bind=True):
        """The environment of a session the app opened: the bare session id (for the record) and the signed token."""
        sid = sid or f'{kind}-{secrets.token_hex(4)}'
        token = self.issue(sid, kind, owner, bind)
        identity.forget()
        with mock.patch.dict(os.environ, {'RELAYFORGE_SESSION_ID': sid, identity.SIG_ENV: token}):
            yield token
        identity.forget()

    def close(self):
        self.stopped = True
        try:
            self.sock.close()
        except OSError:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)
        identity.forget()


def as_app(argv):
    """Run rf.py's command line in this process as the app does: with the app's marker (a real one, see app_marker)."""
    from relayforge.cli import main
    with app_marker():
        return main(argv)


def in_repo_layout(test):
    """这个测试按旧布局跑：记录是仓库里的 .relayforge/ 目录、由程序提交进 Git（RELAYFORGE_FACTS_IN_REPO=1）。
    数据外置以后，检查「事实怎样提交、随分支怎样走」的测试只对旧项目成立；新布局另见 tests/test_datastore.py。
    重新执行一次 setUp（新的临时目录），在旧布局下初始化。"""
    import functools

    @functools.wraps(test)
    def run(self, *args, **kwargs):
        with mock.patch.dict(os.environ, {'RELAYFORGE_FACTS_IN_REPO': '1'}):
            self.setUp()
            return test(self, *args, **kwargs)
    return run
