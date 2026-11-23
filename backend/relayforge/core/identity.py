"""会话的身份：这是哪种会话（开发、测试、修复、排查、立项、大图编辑、日常聊天），属于哪个需求、修复或问题。

不信环境变量里裸的会话编号（RELAYFORGE_SESSION_ID 只用来找聊天记录、给提交挂会话，是记录用的，不是权限判断）。
应用启动每个会话时发一个签过名的会话令牌，放在会话的环境变量 RELAYFORGE_SESSION_SIG 里（名字里不带 TOKEN、KEY、SECRET：
Codex 默认会把名字里带这几个词的环境变量从命令的环境里去掉）：

  令牌 = base64url(内容 JSON) "." base64url(HMAC-SHA256(密钥, 前一段))
  内容 = {v: 1, sid: 会话编号, kind: 种类, owner: 需求 R-… / 修复 F-… / 问题 I-… 或 null, iat: 签发时间（秒）, nonce: 随机串}

密钥只在应用进程的内存里（应用每次启动重新生成），不写进任何文件。rf.py 自己没有密钥，验证是问应用：
  1. 从 rf.py 自己往上找祖先进程，第一个在 $RELAYFORGE_HOME/apps/<pid>.json 登记了 socket 的就是开这个会话的应用；
  2. 连它的 unix socket（本机文件，不是网络端口），发一行 {"op": "verify", "token": …}，应用验签名，并告诉 rf.py
     这个令牌发给了哪个进程（应用开出会话后立刻记下的那个会话进程的 pid）；
  3. 那个 pid 也得是 rf.py 的祖先：别的会话的令牌（比如从别的进程的环境里读到的）拿到这里用不上。
任何一步不成（没有令牌、应用没在运行、签名不对、进程对不上）都按最低的身份算：日常聊天（kind 'chat'，verified False）。
"""

import base64
import json
import os
import socket
from pathlib import Path

from .common import relayforge_home
from .trust import process_chain

SIG_ENV = 'RELAYFORGE_SESSION_SIG'
KINDS = {'develop': '开发会话', 'test': '测试会话', 'fix': '修复会话', 'probe': '排查会话', 'check': '核对会话', 'intake': '立项会话', 'map': '大图编辑会话', 'chat': '日常聊天'}
LOWEST = 'chat'
TIMEOUT = 3.0
REOPEN = '如果你是在应用里开的会话，这个会话可能是应用升级前开的（没有会话令牌）：请在应用里关掉它、重新打开这个会话。'

_cache = {}


def _b64(part):
    return base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))


def claims_of(token):
    """The unverified content of a token, or None when it is not shaped like one. Never trusted on its own."""
    try:
        body, sig = str(token).split('.')
        data = json.loads(_b64(body).decode('utf-8'))
        _b64(sig)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def find_app(chain=None):
    """(pid, socket path) of the RelayForge app this process runs under: the nearest ancestor registered with a socket."""
    apps = relayforge_home() / 'apps'
    for pid, _ in (process_chain() if chain is None else chain):
        try:
            data = json.loads((apps / f'{int(pid)}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get('socket'):
            return int(pid), str(data['socket'])
    return None, None


def ask_app(path, request, timeout=TIMEOUT):
    """One request to the app over its unix socket: a JSON line out, a JSON line back. None when it does not answer."""
    if not hasattr(socket, 'AF_UNIX'):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall((json.dumps(request) + '\n').encode('utf-8'))
            buf = b''
            while b'\n' not in buf and len(buf) < 65536:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        answer = json.loads(buf.split(b'\n', 1)[0].decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return answer if isinstance(answer, dict) else None


def lowest(why, sid=None):
    return {'kind': LOWEST, 'owner': None, 'session': sid, 'verified': False, 'why': why}


def verify(token, chain=None):
    """The caller's identity from `token` (see the module docstring): {kind, owner, session, verified, why}."""
    if not token:
        return lowest('没有会话令牌')
    shaped = claims_of(token)
    if not shaped:
        return lowest('会话令牌的格式不对')
    chain = process_chain() if chain is None else chain
    pid, path = find_app(chain)
    if not path:
        return lowest('找不到开这个会话的应用（应用没在运行，或这个命令不是在应用开的会话里运行的）', shaped.get('sid'))
    answer = ask_app(path, {'op': 'verify', 'token': str(token)})
    if not answer or not answer.get('ok') or not isinstance(answer.get('claims'), dict):
        return lowest('应用没有认这个会话令牌（签名不对，或者是上一次启动的应用发的）', shaped.get('sid'))
    claims = answer['claims']
    ours = {int(p) for p, _ in chain}
    bound = [answer.get('pid'), *(answer.get('pids') or [])]  # pids：应用代写时它自己开的 rf.py（proxy.py）
    if not any(p and int(p) in ours for p in bound):
        return lowest('这个会话令牌不是发给这个会话的（发给的会话进程不在这条命令的上层进程里）', claims.get('sid'))
    kind = claims.get('kind') if claims.get('kind') in KINDS else LOWEST
    return {'kind': kind, 'owner': claims.get('owner') or None, 'session': claims.get('sid') or None, 'verified': True, 'why': ''}


def caller(environ=None, chain=None):
    """Who is calling, from the verified session token in the environment (cached per token in this process)."""
    token = (os.environ if environ is None else environ).get(SIG_ENV) or ''
    if chain is not None:
        return verify(token, chain)
    if token not in _cache:
        _cache[token] = verify(token)
    return dict(_cache[token])


def forget():
    """Drop the cache (tests)."""
    _cache.clear()


def kind_words(kind):
    return KINDS.get(kind, kind)
