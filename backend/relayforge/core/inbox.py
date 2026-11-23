"""会话收件箱的钩子一侧：程序发给会话的话由应用记在收件箱里（app/src/main/inbox.js），这里在会话的钩子里把它交过去，
不往终端里粘贴正文（设计见 docs/design/会话通道与需求冲突-设计.md 第一部分第 4 步）。

  - 这一轮说完时（Stop）：把排给这个会话的话作为钩子的理由交回，Claude Code 和 Codex 都会把它交给模型、接着做。
    最后一句是在问用户问题时不交：那会把程序的话当成对问题的回答，等用户回答完、下一次说完再交。
  - 会话早就停下了：应用只往终端里输一行固定的唤醒句（WAKE），用户提交消息时（UserPromptSubmit）用 additionalContext 把正文带进去。

收件箱文件由应用写，路径在会话的环境变量 RELAYFORGE_INBOX 里；钩子只读它。交出去的消息编号追加到旁边的
<收件箱文件>.delivered（一行「编号<TAB>方式<TAB>时间」），应用读到后把它们记成已送出，不再往终端里输。
只交「排给这个会话」的（消息的 session 是这个会话的 RelayForge 会话编号）；每条只交一次。
"""
import datetime as dt
import json
import os
import re
from pathlib import Path

try:
    import fcntl
except ImportError:  # 没有这个模块的平台：不加锁
    fcntl = None

__all__ = ['WAKE', 'asks_user', 'inbox_deliver', 'inbox_pending']

WAKE = '〔RelayForge〕有新的程序消息'  # 应用唤醒停着的会话时输的那一行的开头（inbox.js 的 WAKE 必须一样）
ENV = 'RELAYFORGE_INBOX'


def asks_user(text):
    """Whether a reply ends with a question to the user (same rule as the status shown in the app: the last line has a question mark)."""
    tail = [ln for ln in str(text or '').split('\n') if ln.strip()][-1:]
    return bool(tail) and bool(re.search(r'[?？]', tail[0]))


def _path():
    value = os.environ.get(ENV)
    return Path(value) if value else None


def _delivered(path):
    try:
        return {line.split('\t', 1)[0] for line in Path(str(path) + '.delivered').read_text(encoding='utf-8').splitlines() if line.strip()}
    except OSError:
        return set()


def inbox_pending(session):
    """The messages waiting for this session that no hook has handed over yet, oldest first."""
    path = _path()
    if not path or not session:
        return []
    try:
        messages = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    done = _delivered(path)
    return [m for m in messages if isinstance(m, dict) and m.get('state') == 'queued' and m.get('session') == session
            and m.get('id') and m['id'] not in done and str(m.get('text') or '').strip()]


def _wrap(m):
    return f'〔RelayForge 程序 · {m.get("label") or "应用"} · {m["id"]}〕\n{m["text"]}'


def inbox_deliver(session, via):
    """Take what waits for this session (marking it handed over by `via`: stop | prompt) and return it as one text, or ''."""
    path = _path()
    if not path or not session:
        return ''
    lock = None
    try:
        if fcntl:
            lock = open(str(path) + '.lock', 'a')
            fcntl.flock(lock, fcntl.LOCK_EX)
        found = inbox_pending(session)
        if not found:
            return ''
        now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        with open(str(path) + '.delivered', 'a', encoding='utf-8') as f:
            f.write(''.join(f'{m["id"]}\t{via}\t{now}\n' for m in found))
        return '\n\n'.join(_wrap(m) for m in found)
    except OSError:
        return ''
    finally:
        if lock:
            lock.close()
