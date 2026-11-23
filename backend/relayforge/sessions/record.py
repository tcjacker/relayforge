"""会话状态文件（.relayforge/sessions/）：会话编号、读写、列出，以及一条陈述的出处。"""

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

from ..core.common import DIR, MARKS, PLACEHOLDER, git, main_checkout
from ..core.documents import all_docs, read_doc, write_doc
from ..core.transcript import message_time, read_transcript, session_tag


def here_source(root, session=None, transcript=None):
    """Where a command run inside a session is said to come from: that session and the user's latest message."""
    found, sid = (Path(transcript), session) if transcript and session else current_transcript(root, session)
    if not sid:
        return ''
    messages = read_transcript(found) if found else []
    at = max((i + 1 for i, m in enumerate(messages) if m['role'] == 'user'), default=0)
    return f'{stable_tag(root, sid)}#{at}'


def stable_tag(root, session_id):
    """One session keeps one number, whichever day it is seen again. Session files are in the main checkout."""
    root = main_checkout(root)
    occupied = set()
    for path in sorted((root / DIR / 'sessions').glob('*.md')):
        meta = read_doc(path)[0]
        if meta.get('session_id') == session_id and meta.get('id'):
            return meta['id']
        occupied.add(path.stem)
    recorded = []
    for path in sorted((root / DIR / 'inbox' / 'merged').glob('*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except ValueError:
            continue
        tag = record.get('session')
        if not tag:
            continue
        if record.get('session_id') == session_id:
            recorded.append(tag)
        else:
            occupied.add(tag)
    for tag in recorded:
        if tag not in occupied:
            return tag
    tag = session_tag(session_id)
    if tag not in occupied:
        return tag
    suffix = hashlib.sha256((session_id or '').encode('utf-8')).hexdigest()[:8]
    base = f'{tag}-{suffix}'
    tag, n = base, 2
    while tag in occupied:
        tag = f'{base}-{n}'
        n += 1
    return tag


def read_session(path):
    meta, sections = read_doc(path)
    marks = {v: k for k, v in MARKS.items()}
    plan, changes = [], []
    for line in sections.get('计划', []):
        m = re.match(r'- \[(.)\] (.*?)(?:｜(.*?))?(?:〔[^#〕]*#(\d+)(?: @([^〕]*))?〕)?$', line)
        if m:
            plan.append({'title': m.group(2), 'status': marks.get(m.group(1), 'todo'), 'note': m.group(3) or '', 'at': int(m.group(4) or 0), 'when': m.group(5) or ''})
    for line in sections.get('方向调整', []):
        m = re.match(r'- (.*?)(?:〔[^#〕]*#(\d+)(?: @([^〕]*))?〕)?$', line)
        if m and line != PLACEHOLDER:
            changes.append({'text': m.group(1), 'at': int(m.group(2) or 0), 'when': m.group(3) or ''})

    def text(head):
        return ' '.join(l[2:] for l in sections.get(head, []) if l != PLACEHOLDER)

    return dict(meta, goal=text('任务目标'), doing=text('正在处理'), plan=plan, changes=changes, upto=int(meta.get('upto') or 0))


def write_session(root, tag, session_id, state, upto, messages=(), named=False, ended=None, branch=None):
    """Write the session file into the main checkout; `branch` is the branch the session works on (where it runs)."""
    here, root = root, main_checkout(root)
    known = {meta['id'] for _, meta, _ in all_docs(root, ('modules',))}
    owner = {meta['id']: meta.get('module') for _, meta, _ in all_docs(root, ('features',))}
    features = [f for f in dict.fromkeys(state.get('features') or []) if f in owner]
    modules = [m for m in dict.fromkeys(list(state.get('modules') or []) + [owner[f] for f in features]) if m in known]

    def source(at):
        when = message_time(messages, at)
        return f'〔{tag}#{at}' + (f' @{when}' if when else '') + '〕'

    def one(value):
        return re.sub(r'\s+', ' ', str(value or '')).replace('｜', '，').replace('〔', '（').replace('〕', '）').strip()

    plan = []
    for step in state.get('plan') or []:
        if not isinstance(step, dict) or not one(step.get('title')):
            continue
        status = step.get('status') if step.get('status') in MARKS else 'todo'
        note = one(step.get('note'))
        at = step.get('at') if isinstance(step.get('at'), int) else 0
        plan.append(f'- [{MARKS[status]}] {one(step["title"])}' + (f'｜{note}' if note else '') + source(at))
    changes = [f'- {one(c.get("text"))}' + source(c.get('at') if isinstance(c.get('at'), int) else 0)
               for c in (state.get('changes') or [])[-10:] if isinstance(c, dict) and one(c.get('text'))]
    goal = one(state.get('goal'))
    meta = {'id': tag, 'type': 'session', 'title': one(state.get('title'))[:30] or goal[:30], 'status': 'current', 'session_id': session_id,
            'named': 'user' if named else None, 'ended': ended, 'modules': modules, 'features': features,
            'branch': branch or git(here, 'rev-parse', '--abbrev-ref', 'HEAD').strip() or None, 'upto': upto,
            'updated': dt.datetime.now().strftime('%Y-%m-%d %H:%M')}
    sections = {'任务目标': [f'- {goal}'] if goal else [], '正在处理': [f'- {one(state.get("doing"))}'] if one(state.get('doing')) else [],
                '计划': plan, '方向调整': changes}
    path = root / DIR / 'sessions' / f'{tag}.md'
    write_doc(path, meta, sections)
    return path


def plan_finished(sections):
    """Whether every step of a session's 计划 is done or dropped (and there is a plan at all)."""
    plan = sections.get('计划') or []
    return bool(plan) and all(line.startswith(f'- [{MARKS["done"]}]') or line.startswith(f'- [{MARKS["dropped"]}]') for line in plan)


def load_sessions(root):
    """Sessions of the project, newest first: all of them are in the main checkout, wherever they ran."""
    home = main_checkout(root)
    out = []
    for path in (home / DIR / 'sessions').glob('*.md'):
        data = read_session(path)
        if data.get('id'):
            data.update(file=f'{DIR}/sessions/{path.name}', root=str(home), worktree='')
            out.append(data)
    return sorted(out, key=lambda x: str(x.get('updated') or ''), reverse=True)


def current_transcript(root, session_id=None):
    """Find the transcript of the session this command is running in."""
    codex_id = os.environ.get('CODEX_THREAD_ID')
    app_session = os.environ.get('RELAYFORGE_SESSION_ID')
    session_id = session_id or app_session or os.environ.get('CLAUDE_CODE_SESSION_ID') or codex_id
    transcript = os.environ.get('RELAYFORGE_PROXY_CODEX_TRANSCRIPT')
    if transcript is not None and session_id and session_id == app_session:
        # 应用已认出这是 Codex 会话，找不到对话时不能把进展记到同目录的 Claude 会话。
        return (Path(transcript), session_id) if transcript and Path(transcript).is_file() else (None, None)
    home = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'
    if session_id:
        hits = sorted(home.glob(f'*/{session_id}.jsonl'))
        if hits:
            return hits[0], session_id
        codex_home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'sessions'
        identifiers = [session_id]
        if app_session == session_id and codex_id:
            identifiers.append(codex_id)
        for ident in dict.fromkeys(identifiers):
            hits = sorted(codex_home.rglob(f'*{ident}.jsonl'))
            if hits:
                return hits[-1], session_id
    folder = home / re.sub(r'[^0-9a-zA-Z]', '-', str(root))
    for path in sorted(folder.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True):
        return path, path.stem
    return None, None
