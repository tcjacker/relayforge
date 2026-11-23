"""装在项目里的 Claude Code 钩子：session-start、capture、subagent-stop、stop。"""

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from ..core.common import DIR, GUARD, SCRIPT, find_root, log, main_checkout
from ..core.documents import format_value
from ..core.transcript import count_messages, session_tag


def hook_input():
    try:
        return json.loads(sys.stdin.read() or '{}')
    except ValueError:
        return {}


def spawn_capture(root, transcript, session_id, trigger):
    script = str(SCRIPT)
    cmd = [sys.executable, script, 'capture', '--root', str(root), '--transcript', transcript, '--session', session_id, '--trigger', trigger]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def run_hook(name):
    if os.environ.get(GUARD):
        return 0
    data = hook_input()
    root = find_root(data.get('cwd'))
    if not root:
        return 0
    root = main_checkout(root)  # facts, context.md and the runs are in the main checkout
    try:
        session = data.get('session_id') or ''
        transcript = data.get('transcript_path') or ''
        if name == 'session-start':
            if transcript:
                catch_up(root, Path(transcript), session)
            context = root / DIR / 'context.md'
            if context.exists():
                sys.stdout.write(context.read_text(encoding='utf-8'))
        elif name == 'capture':
            if transcript and session:
                trigger = {'PreCompact': 'pre-compact', 'SessionEnd': 'session-end'}.get(data.get('hook_event_name'), 'hook')
                spawn_capture(root, transcript, session, trigger)
        elif name == 'subagent-stop':
            store_run(root, data)
    except Exception as error:  # a hook must never break the session
        log(root, f'hook {name} failed: {error!r}')
    return 0


def catch_up(root, current, session_id, limit=5, idle_seconds=600):
    """Capture earlier sessions of this project that ended without being captured."""
    cursor_path = root / DIR / 'cursor.json'
    cursor = json.loads(cursor_path.read_text(encoding='utf-8')) if cursor_path.exists() else {}
    now = dt.datetime.now().timestamp()
    others = sorted((p for p in current.parent.glob('*.jsonl') if p != current), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in others[:limit]:
        if now - path.stat().st_mtime < idle_seconds:
            continue
        if count_messages(path) > int(cursor.get(path.stem, 0)):  # 只数条数：不把旧会话的文字留在内存里
            spawn_capture(root, str(path), path.stem, 'catch-up')


def store_run(root, data):
    tag = session_tag(data.get('session_id'))
    agent = re.sub(r'[^0-9a-zA-Z_-]', '', str(data.get('agent_id') or 'agent'))[:40]
    meta = {'session': tag, 'agent_type': data.get('agent_type'), 'agent_id': data.get('agent_id'),
            'ended_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'related': [],
            'transcript': data.get('agent_transcript_path')}
    report = (data.get('last_assistant_message') or '（没有拿到回报内容）').strip()
    path = root / DIR / 'runs' / tag / f'{agent}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    head = ['---'] + [f'{k}: {format_value(v)}' for k, v in meta.items()] + ['---', '', '## 回报原文', '']
    path.write_text('\n'.join(head) + report + '\n', encoding='utf-8')
