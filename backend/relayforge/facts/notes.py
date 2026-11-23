"""边做边记（rf.py note），以及停下来时「这一轮干了活却没记」的检查。"""

import json
import os
import re
from pathlib import Path

from ..core.common import DIR, GUARD, SCRIPT, branch_of, drafts_dir, facts_lock, find_root, main_checkout, today
from ..core.documents import add_line, find_doc, item_line, write_doc
from ..core.transcript import claude_shaped, read_transcript
from ..sessions.record import read_session, stable_tag, write_session
from .facts import apply_item
from .inflight import change_statement
from ..requirements.owners import own_requirement
from ..requirements.record import settle_after_session
from ..requirements.intake import save_assessment


STEP_WORDS = {'done': 'done', '做完': 'done', 'doing': 'doing', '在做': 'doing', 'todo': 'todo', '没做': 'todo', 'dropped': 'dropped', '不做': 'dropped'}
NOTE_KINDS = ('title', 'goal', 'doing', 'plan', 'step', 'change', 'touch', 'say', 'feature', 'check', 'decision', 'open', 'fact', 'new-feature', 'same', 'assessment')
REQ_SECTION = {'say': '需求说明', 'feature': '要改的功能点', 'check': '验收条件'}


def note(root, session_id, transcript, kind, values, guess=False, reason=None, head=None, replaces=None, module=None):
    """One thing the working session writes down. The program supplies numbers, sources and format. Everything is written
    into the main checkout, whichever checkout the session runs in; `root` (where it runs) only says which requirement it
    belongs to. What a requirement still in progress brings is marked as its (see inflight.py)."""
    home = main_checkout(root)
    with facts_lock(home):
        return _note(root, home, session_id, transcript, kind, values, guess, reason, head, replaces, module)


def _note(here, root, session_id, transcript, kind, values, guess, reason, head, replaces, module):
    mine = own_requirement(here)
    going = mine[1]['id'] if mine and (mine[1].get('state') or 'open') in ('draft', 'open') else None
    values = [v.strip() for v in values if v and v.strip()]
    messages = read_transcript(transcript) if transcript else []
    at = max((i + 1 for i, m in enumerate(messages) if m['role'] == 'user'), default=0)
    tag = stable_tag(root, session_id)
    source = f'{tag}#{at}'
    path = Path(root) / DIR / 'sessions' / f'{tag}.md'
    old = read_session(path) if path.exists() else {}
    state = {k: old.get(k) for k in ('title', 'goal', 'doing', 'modules', 'features')}
    state['plan'], state['changes'] = list(old.get('plan') or []), list(old.get('changes') or [])
    if len(values) < {'same': 0, 'step': 2}.get(kind, 1):
        raise ValueError(f'note {kind} 后面少了内容。')
    said = '已记下。'
    if kind in ('title', 'goal', 'doing'):
        state[kind] = values[0]
    elif kind == 'plan':
        known = {s['title']: s for s in state['plan']}
        state['plan'] = [known.get(v) or {'title': v, 'status': 'todo', 'note': '', 'at': at} for v in values]
        said = f'计划记为 {len(values)} 步。'
    elif kind == 'step':
        status = STEP_WORDS.get(values[1])
        if not status:
            raise ValueError('步骤的状态写 done、doing、todo 或 dropped。')
        names = values[0].split(',') if re.fullmatch(r'[\d,]+', values[0]) else [values[0]]  # "1,2,3" marks several steps at once
        for name in names:
            byindex = name.isdigit() and 0 < int(name) <= len(state['plan'])
            step = state['plan'][int(name) - 1] if byindex else next((s for s in state['plan'] if s['title'] == name), None)
            if not step:
                raise ValueError(f'计划里没有「{name}」这一步。先用 note plan 写下计划，或用序号指定。')
            step.update(status=status, at=at, note=values[2] if len(values) > 2 and len(names) == 1 else step.get('note') or '')
        said = f'{len(names)} 步标为 {status}。'
    elif kind == 'change':
        state['changes'].append({'text': values[0], 'at': at})
    elif kind == 'touch':
        state['features'] = list(dict.fromkeys(list(state.get('features') or []) + [v for v in values if v.startswith('RF-')]))
        state['modules'] = list(dict.fromkeys(list(state.get('modules') or []) + [v for v in values if v.startswith('M-')]))
    elif kind == 'assessment':
        if len(values) != 1:
            raise ValueError('用法：note assessment \'{…}\'，整份评估作为一个 JSON 参数传入。')
        said = save_assessment(mine, session_id, values[0], source)
    elif kind in REQ_SECTION:
        if not mine:
            raise ValueError('这个目录不属于任何需求，没有需求文件可写。')
        req_path, meta, sections = mine
        for v in values:
            add_line(sections, REQ_SECTION[kind], item_line(v, source, not guess))
        meta['updated'] = today()
        write_doc(req_path, meta, sections)
        said = f'已写进需求的「{REQ_SECTION[kind]}」' + ('，标为待确认。' if guess else '。')
    elif kind == 'decision':
        made = apply_item(root, {'kind': 'decision', 'title': values[0] if len(values) > 1 else None, 'statement': values[-1], 'reason': reason,
                                 'stated_by': 'inferred' if guess else 'user'}, source, going)
        said = '这条决定已经记过了。' if made == 'skip' else f'已记为 {made}' + ('，标为待确认。' if guess else '。')
    elif kind == 'open':
        apply_item(root, {'kind': 'open-question', 'statement': values[0]}, source)
        said = '已记到「还没定的事」。'
    elif kind == 'fact':  # note fact RF-101 "新的说法" [--head 规则与边界] [--replaces "旧陈述里的一段话"]
        if len(values) < 2:
            raise ValueError('用法：note fact 功能点或模块编号 "新的陈述" [--head 小节] [--replaces "要换掉的旧陈述里的一段话"]')
        found = find_doc(root, values[0])
        if not found or found[1].get('type') not in ('feature', 'module', 'journey'):
            raise ValueError(f'{values[0]} 不是现有的功能点、模块或用户旅程。')
        heads = {'feature': '规则与边界', 'module': '负责什么', 'journey': '步骤'}
        change_statement(found[0], going if found[1].get('in_progress') != going else None, head or heads[found[1]['type']], values[1], source, replaces, not guess)
        said = (f'已写进 {values[0]}，标为需求 {going} 进行中，确认完成后才算现行。' if going and found[1].get('in_progress') != going else f'已写进 {values[0]}。') + ('（推断，待确认）' if guess else '')
    elif kind == 'new-feature':  # note new-feature "名称" --module M-01 [--about "一句话定位"]
        if not module:
            raise ValueError('新功能点要说明属于哪个模块：--module M-01。')
        made = apply_item(root, {'kind': 'structure', 'op': 'add-feature', 'title': values[0], 'module': module, 'one_liner': reason,
                                 'stated_by': 'inferred' if guess else 'user'}, source, going)
        said = '已经有同名的功能点了。' if made == 'skip' else f'已新建功能点 {made}' + (f'，标为需求 {going} 进行中，确认完成后才算现行。' if going else '。')
    if not state.get('title'):
        opener = next((' '.join(m['text'].split()) for m in messages if m['role'] == 'user' and not m['text'].startswith('<')), '')
        state['title'] = (mine[1].get('title') if mine else None) or state.get('goal') or state.get('doing') or opener
    path = write_session(root, tag, session_id, state, len(messages), messages, named=bool(old.get('named')), ended=old.get('ended'), branch=branch_of(here))
    settle_after_session(root, path, facts_locked=True)
    return said
WRITING_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
WRITING_SHELL = re.compile(r'>|\bgit\s+(add|commit|merge|rebase|checkout|switch|mv|rm|reset|stash|push)\b|\b(mkdir|rm|mv|cp|touch|tee|chmod|ln|npm|pnpm|yarn|pip|make|cargo|go)\b|\bsed\s+-i')


def changes_things(block):
    if block.get('name') in WRITING_TOOLS:
        target = str((block.get('input') or {}).get('file_path') or (block.get('input') or {}).get('notebook_path') or '')
        return not (target and Path(target).resolve().is_relative_to(drafts_dir().resolve()))  # a draft is not yet a change
    command = str((block.get('input') or {}).get('command') or '')
    return block.get('name') == 'Bash' and 'rf.py' not in command and bool(WRITING_SHELL.search(re.sub(r'\d*>&\d+|\d*>>?\s*/dev/null', '', command)))  # discarding output is not writing


def turn_weight(transcript):
    """What the assistant did since the user last spoke: how many times it changed something, and characters of text.

    Reading files and talking do not count as changes; editing files does, and so does a shell command that writes.
    """
    tools, chars = 0, 0
    for obj in claude_shaped(transcript):  # Claude Code 和 Codex 的记录都按 Claude Code 的格式读（core/transcript.py）
        if obj.get('isSidechain'):
            continue
        content = (obj.get('message') or {}).get('content')
        blocks = content if isinstance(content, list) else []
        if obj.get('type') == 'user' and (isinstance(content, str) or any(isinstance(b, dict) and b.get('type') == 'text' for b in blocks)):
            tools, chars = 0, 0  # a new turn starts with what the user said
        elif obj.get('type') == 'assistant':
            tools += sum(1 for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_use' and changes_things(b))
            chars += sum(len(b.get('text', '')) for b in blocks if isinstance(b, dict) and b.get('type') == 'text')
    return tools, chars


def stop_block(data):
    """The reason this stop must not happen yet (a turn that did real work left no note), or None."""
    root = find_root(data.get('cwd'))
    root = main_checkout(root) if root else None  # the session's notes are in the main checkout
    transcript, session = data.get('transcript_path') or '', data.get('session_id') or ''
    if root and transcript and session and not data.get('stop_hook_active') and not os.environ.get(GUARD):
        messages = read_transcript(transcript)
        last = max((i + 1 for i, m in enumerate(messages) if m['role'] == 'user'), default=0)
        path = root / DIR / 'sessions' / f'{stable_tag(root, session)}.md'
        upto = read_session(path)['upto'] if path.exists() else 0
        tools, chars = turn_weight(transcript)
        if upto < last and tools:
            cmd = f'python3 {SCRIPT} note'
            return (f'这一轮的进展还没记。直接运行 {cmd} 后面接下面任意一两种，不用查文档：doing "现在在做什么" ／ plan "步骤一" "步骤二" ／ '
                    'step 2 done "结果"（几步一起标写 step 1,2,3 done）／ decision "用户定下的事" ／ open "没定的问题" ／ same（确实没什么可记）。'
                    '记完用一句话重述结论即可，不必重复全文，也不要提记录这件事。')
    return None


def stop_check(data):
    """The older stop hook: block a turn that left no note; otherwise append the raw event for the app."""
    reason = stop_block(data)
    if reason:
        print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False))
        return 0
    events = os.environ.get('RELAYFORGE_EVENTS')
    if events:
        with open(events, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')
    return 0
