"""会话经应用代写记录（见 FORMAT.md 的「数据放在哪」）。

数据外置以后，记录在 ~/.relayforge/projects/<项目 ID>/，在项目目录之外。Codex 的沙箱（workspace-write）只让写工作目录，
写不了那里；沙箱也不为这个放开（D-0101）。所以会话里的 rf.py 发现记录写不进去时，把这条命令整个交给开这个会话的应用去执行：

  1. 只转交 PROXIED 里列出的「记录类」写命令（边做边记、用例和归类、检查步骤、测试结论、问题、设计稿、测试集数据、环境建议）；
     运行测试集、准备步骤这类会执行别的程序的命令不转交，不然沙箱外面就多了一个替会话执行程序的口子。
  2. 经应用的 unix socket（identity.py 里同一个）发 {"op": "run", "token": 会话令牌, "argv": 命令, "stdin": 标准输入}。
  3. 应用验会话令牌（必须是还开着的会话发的），自己再按同一张单子核一遍命令，在这个会话的工作目录里用应用自带的 rf.py 执行，
     环境里带着这个会话的令牌（没有应用的标记）：执行出来的身份还是这个会话，「只由用户触发」的命令照样被拒绝。
  4. 把输出和退出码原样交回。
"""

import os
import socket
import sys
import tempfile
from pathlib import Path

from .common import DIR
from .identity import SIG_ENV, ask_app, claims_of, find_app

PROXY_ENV = 'RELAYFORGE_PROXIED'  # 应用代为执行的那个 rf.py 带着它：不会再往外转交
TIMEOUT = 180.0

# 能转交的命令：(命令, 动作)，动作为 None 表示这个命令的所有动作。和 app/src/main/proxy.js 的 PROXIED 保持一致
PROXIED = {
    ('note', None),
    ('req', 'attach'), ('req', 'classify'), ('req', 'steps'), ('req', 'verdict'), ('req', 'retest'),
    ('req', 'case-add'), ('req', 'case-edit'), ('req', 'case-rm'),
    ('issue', 'new'), ('issue', 'attach'), ('issue', 'handback'), ('issue', 'finding'),
    ('design', 'save'), ('design', 'skip'),
    ('test', 'new'), ('test', 'add'), ('test', 'edit'), ('test', 'rm'), ('test', 'analyze'),
    ('env', 'propose'), ('fresh', 'propose'),
    # 不是记录类的写命令，是读：会话的沙箱读不了保密的值，你允许了「AI 的会话带上保密值」时，运行前经应用取（envs/values.py 的 ai_values）
    ('env', 'ai-values'),
}


def proxied(cmd, action=None):
    return (cmd, None) in PROXIED or (cmd, action) in PROXIED


def writable(root):
    """Whether this process can write the project's records (a sandbox may forbid it although the files allow it)."""
    store = Path(root) / DIR
    try:
        store = store.resolve()
        fd, name = tempfile.mkstemp(prefix='.probe-', dir=store)
    except OSError:
        return False
    os.close(fd)
    try:
        os.unlink(name)
    except OSError:
        pass
    return True


def should_forward(cmd, action, root, environ=None):
    env = os.environ if environ is None else environ
    if env.get(PROXY_ENV) or not env.get(SIG_ENV) or not proxied(cmd, action) or not root:
        return False
    return not writable(root)


def _stdin_text(argv):
    """Standard input when the command reads it: design save without --file, or --file -."""
    reads = '-' in argv or (len(argv) > 1 and argv[0] == 'design' and argv[1] == 'save' and '--file' not in argv)
    if not reads or sys.stdin is None or sys.stdin.isatty():
        return None
    raw = getattr(sys.stdin, 'buffer', None)
    return raw.read().decode('utf-8', errors='replace') if raw else sys.stdin.read()


def _no_answer(path):
    """Why the app did not answer, in words a session can act on: a sandbox that forbids the connection is not an old app."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(path)
    except PermissionError:
        return ('这个会话的沙箱不让连应用的 socket（不是应用的问题）：请停下来告诉用户，请用户在应用里重新打开这个会话；'
                '不要申请在沙箱外执行命令')
    except OSError:
        return '应用没有回应（应用可能已经退出），请在应用里重新打开这个会话再试'
    return '应用没有回应（可能是旧版应用），请在应用里重新打开这个会话再试'


def forward(argv, environ=None):
    """Hand the command to the app; returns the exit code (output printed here)."""
    env = os.environ if environ is None else environ
    token = env.get(SIG_ENV) or ''
    _, path = find_app()
    if not path or not claims_of(token):
        print('这个会话写不了项目的记录（记录在项目目录之外，沙箱不让写），也找不到开这个会话的应用来代写。'
              '请在 RelayForge 应用里打开这个会话再试。', file=sys.stderr)
        return 1
    answer = ask_app(path, {'op': 'run', 'token': token, 'argv': list(argv), 'stdin': _stdin_text(argv)}, timeout=TIMEOUT)
    if not answer or not answer.get('ok'):
        why = (answer or {}).get('error') or _no_answer(path)
        print(f'交给应用代写没有成功：{why}', file=sys.stderr)
        return 1
    if answer.get('stdout'):
        sys.stdout.write(answer['stdout'])
    if answer.get('stderr'):
        sys.stderr.write(answer['stderr'])
    return int(answer.get('code') or 0)

