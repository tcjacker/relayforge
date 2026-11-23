"""无界面运行的会话（app/src/main/sessions.js 的 headless）要你批准时，等你在应用里点：
CLI 的「等批准」钩子（PermissionRequest，Claude Code 和 Codex 一样）在无界面模式下也会触发，但没有终端可以弹确认框。
这时钩子把请求写进应用给的目录（会话环境变量 RELAYFORGE_APPROVALS），一条一个文件 <编号>.json，然后等应用写回
<编号>.decision（{"behavior": "allow" | "deny", "message": "…"}）；等太久就按拒绝回答，并告诉会话为什么。
有交互终端的会话（没有 RELAYFORGE_HEADLESS）不经过这里，照旧由 CLI 自己在终端里问。
"""
import datetime as dt
import json
import os
import secrets
import time
from pathlib import Path

__all__ = ['headless', 'wait_for_decision', 'decision_output', 'WAIT_SECONDS']

WAIT_SECONDS = 50 * 60  # 等你多久；钩子的超时（应用里设的）要比它长
POLL = 1.0


def headless():
    return os.environ.get('RELAYFORGE_HEADLESS') == '1' and bool(os.environ.get('RELAYFORGE_APPROVALS'))


def wait_for_decision(data, wait=WAIT_SECONDS, poll=POLL):
    """Write this permission request for the app and wait for the user's answer: {'behavior', 'message'?}."""
    folder = Path(os.environ['RELAYFORGE_APPROVALS'])
    folder.mkdir(parents=True, exist_ok=True)
    ident = 'a-' + secrets.token_hex(4)
    given = data.get('tool_input') if isinstance(data.get('tool_input'), dict) else {}
    request = {'id': ident, 'session': os.environ.get('RELAYFORGE_SESSION_ID') or str(data.get('session_id') or ''),
               'tool': str(data.get('tool_name') or ''), 'input': given,
               'description': str(given.get('description') or ''), 'cwd': str(data.get('cwd') or ''),
               'at': dt.datetime.now().astimezone().isoformat(timespec='seconds')}
    temp = folder / f'{ident}.json.tmp'
    temp.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, folder / f'{ident}.json')
    answer = folder / f'{ident}.decision'
    deadline = time.monotonic() + wait
    try:
        while time.monotonic() < deadline:
            try:
                got = json.loads(answer.read_text(encoding='utf-8'))
                if isinstance(got, dict) and got.get('behavior') in ('allow', 'deny'):
                    return {'behavior': got['behavior'], **({'message': str(got['message'])} if got.get('message') else {})}
            except (OSError, ValueError):
                pass
            time.sleep(poll)
        return {'behavior': 'deny', 'message': f'等了 {wait // 60} 分钟没有人在应用里批准，这一步先不做。把要做什么、为什么写清楚，停下来等用户。'}
    finally:
        for f in (folder / f'{ident}.json', answer):
            try:
                f.unlink()
            except OSError:
                pass


def decision_output(decision):
    """What the PermissionRequest hook prints (same shape for Claude Code and Codex)."""
    return {'hookSpecificOutput': {'hookEventName': 'PermissionRequest', 'decision': decision}}
