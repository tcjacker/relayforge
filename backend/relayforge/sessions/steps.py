"""会话步骤：从会话记录里读出每一轮做了什么（跑了哪些命令、读改了哪些文件、提交了什么），以及一个提交前后会话的那几步。

历史（history：看一个会话、一个需求做了什么）和追溯（trace：这个提交为什么这样改）都要用；放在这里，两边不必互相导入。
分层见 tests/test_layers.py。
"""
import json
import re

from ..core.common import DIR, main_checkout
from ..core.documents import read_doc
from .record import stable_tag
from ..core.transcript import CODEX_SYNTHETIC, clean, codex_script_tools, open_text
from ..envs.values import redact
from .archive import find_archived, find_original
from ..issues.record import probe_dir, read_probe
from ..requirements.owners import all_fixes

__all__ = ['commit_steps', 'commit_steps_lines', 'read_turns', 'resolve_session', 'session_where', 'step_line', 'tag_map', 'turn_when',
           'STEPS_PER_TURN', 'WHY_STEPS']


def tag_map(home):
    """session tag -> (uuid, owner id) for every session a requirement or fix holds."""
    out = {}
    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        meta = read_doc(path)[0]
        for u in (meta.get('sessions') or []) + (meta.get('test_sessions') or []):
            out.setdefault(stable_tag(home, str(u)), (str(u), meta.get('id')))
    for fid, fix in all_fixes(home).items():
        for u in fix.get('sessions') or []:
            out.setdefault(stable_tag(home, str(u)), (str(u), fid))
    for path in sorted(probe_dir(home).glob('*.json')):
        for u in (read_probe(home, path.stem) or {}).get('sessions') or []:
            out.setdefault(stable_tag(home, str(u)), (str(u), path.stem))
    for path in sorted((home / DIR / 'sessions').glob('*.md')):
        meta = read_doc(path)[0]
        if meta.get('id') and meta.get('session_id'):
            out.setdefault(meta['id'], (str(meta['session_id']), None))
    return out


def session_where(home, ident, uuid):
    """Where the session's record is on this machine: ('archive' | 'original' | None, path)."""
    kept = find_archived(uuid, home)
    if kept:
        return 'archive', kept
    found = find_original(uuid)
    return ('original', found['main']) if found else (None, None)


def resolve_session(home, ref):
    ref = str(ref or '').strip()
    if re.fullmatch(r'S-\S+', ref):
        found = tag_map(home).get(ref)
        if not found:
            raise ValueError(f'没有找到会话 {ref}。')
        return ref, found[0], found[1]
    if re.fullmatch(r'[0-9A-Za-z_.-]{8,80}', ref):
        owner = next((o for t, (u, o) in tag_map(home).items() if u == ref), None)
        return stable_tag(home, ref), ref, owner
    raise ValueError('用法：history session 会话编号（S-…）[--steps] [--grep 词] [--tail N]')


def turn_when(ts):
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00')).astimezone().strftime('%m-%d %H:%M')
    except (ValueError, TypeError):
        return ''


STEP_CAP = 160          # 一步里的命令或说明最多多少字


STEPS_PER_TURN = 30     # 一轮最多列几步（相同的连续几步合成一步算一步）


SAY_CAP = 200           # 一轮开头用户的话、结尾 AI 的回复各最多多少字


SECRETISH = re.compile(r'(?i)(\b[\w.-]*(?:api[_-]?key|token|secret|password|passwd)[\w.-]*\s*[=:]\s*|\bbearer\s+|--(?:token|password|api-key)[= ]\s*)("[^"]*"|\'[^\']*\'|\S+)')


TOOL_REFUSED = re.compile(r"doesn't want to proceed with this tool use|rejected by (?:the )?user|user (?:denied|rejected|declined)", re.I)


EXIT_CODE = re.compile(r'"exit_code"\s*:\s*(-?\d+)|Process exited with code (-?\d+)|^Exit code:? (-?\d+)', re.M)


COMMIT_OUT = re.compile(r'(?:^|"|\\n)\[[^\]\s"]+(?: \([^)]*\))? ([0-9a-f]{7,40})\] ((?:[^\n"\\]|\\[^n])+)', re.M)  # Codex 的输出是 JSON 里的字符串


LOG_LINE = re.compile(r'(?:^|"output":"|\\n)([0-9a-f]{7,40}) ((?:[^\n"\\]|\\[^n])+)', re.M)


JS_CMD = re.compile(r'\bcmd\s*:\s*"((?:[^"\\]|\\.)*)"')


PATCH_FILE = re.compile(r'^\*\*\* (?:Update|Add|Delete) File: (.+)$', re.M)


def _one_line(text, cap):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= cap else text[:cap] + '…'


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(b.get('text', '') if isinstance(b, dict) else str(b) for b in content)
    return '' if content is None else str(content)


PROGRAM = re.compile(r'^\s*(?:〔RelayForge 程序\s*·[^\n]*·\s*m-[^〕\n]+〕|〔RelayForge〕有新的程序消息'
                     r'|Stop hook feedback|<hook_prompt\b)')
PROGRAM_CONTEXT = re.compile(r'<system-reminder>(.*?)</system-reminder>', re.S)
REPLAY_SYNTHETIC = re.compile(r'^(?:This session is being continued from a previous conversation|Caveat: The messages below)')
REPLAY_BLOCKED = re.compile(r'denied by your permission settings|Permission to use [^\n]{0,200}has been denied'
                            r'|只能由你在应用里做|Operation not permitted|sandbox[^\n]{0,40}(?:denied|blocked)|blocked by (?:a )?hook', re.I)


def _message_text(text, replay):
    if replay:
        # 唤醒消息的正文可能由钩子放进 system-reminder。
        text = PROGRAM_CONTEXT.sub(lambda m: m[1] if PROGRAM.match(m[1]) else '', text)
        text = re.sub(r'</hook_prompt>', '', text)
    return clean(text)


def _events(path, replay=False):
    """Everything the step view needs, in order, from a Claude Code or Codex transcript. Message numbering is _walk's."""
    seen_usage = set()
    with open_text(path) as lines:
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            ts = obj.get('timestamp') if isinstance(obj.get('timestamp'), str) else None
            kind = obj.get('type')
            p = obj.get('payload') if isinstance(obj.get('payload'), dict) else {}
            if kind in ('session_meta', 'turn_context'):  # Codex
                if isinstance(p.get('cwd'), str):
                    yield {'kind': 'cwd', 'path': p['cwd']}
                continue
            if kind == 'event_msg':  # Codex
                if p.get('type') == 'turn_aborted':  # 用户打断（monitor 统计）；read_turns 不认这一种，忽略
                    yield {'kind': 'aborted', 'ts': ts}
                    continue
                info = p.get('info') if p.get('type') == 'token_count' and isinstance(p.get('info'), dict) else None
                last = info.get('last_token_usage') if info else None
                if isinstance(last, dict) and all(type(last.get(k, 0)) is int for k in ('input_tokens', 'output_tokens')):
                    yield {'kind': 'usage', 'read': last.get('input_tokens', 0), 'write': last.get('output_tokens', 0), 'ts': ts}
                continue
            if kind == 'response_item':  # Codex
                t = p.get('type')
                if t == 'message' and p.get('role') in ('user', 'assistant'):
                    text = _message_text('\n'.join(b.get('text', '') for b in p.get('content', []) if isinstance(b, dict) and b.get('type') in ('input_text', 'output_text')), replay)
                    if text:
                        yield {'kind': 'msg', 'role': p['role'], 'text': text, 'ts': ts}
                elif t in ('function_call', 'custom_tool_call', 'local_shell_call'):
                    raw = p.get('arguments') if t == 'function_call' else p.get('input') if t == 'custom_tool_call' else p.get('action')
                    if isinstance(raw, str) and t == 'function_call':
                        try:
                            raw = json.loads(raw)
                        except ValueError:
                            pass
                    yield {'kind': 'call', 'id': p.get('call_id'), 'name': str(p.get('name') or 'shell'), 'input': raw, 'ts': ts}
                elif t in ('function_call_output', 'custom_tool_call_output', 'local_shell_call_output'):
                    text = _result_text(p.get('output'))
                    codes = [int(next(g for g in m.groups() if g is not None)) for m in EXIT_CODE.finditer(text)]
                    yield {'kind': 'result', 'id': p.get('call_id'), 'refused': bool(TOOL_REFUSED.search(text[:2000])),
                           'error': any(codes), 'text': text, 'ts': ts}
                continue
            if kind not in ('user', 'assistant') or obj.get('isSidechain'):
                continue
            if isinstance(obj.get('cwd'), str):
                yield {'kind': 'cwd', 'path': obj['cwd']}
            message = obj.get('message')
            if not isinstance(message, dict):
                continue
            usage = message.get('usage') if kind == 'assistant' else None
            usage_id = message.get('id')
            usage_id = usage_id if isinstance(usage_id, str) else None
            if isinstance(usage, dict) and usage_id not in seen_usage:
                if usage_id:
                    seen_usage.add(usage_id)
                read = sum(usage[k] for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
                           if type(usage.get(k)) is int)
                yield {'kind': 'usage', 'read': read, 'write': usage['output_tokens'] if type(usage.get('output_tokens')) is int else 0, 'ts': ts}
            content = message.get('content')
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get('type') == 'tool_use':
                        yield {'kind': 'call', 'id': b.get('id'), 'name': str(b.get('name') or '工具'), 'input': b.get('input'), 'ts': ts}
                    elif b.get('type') == 'tool_result':
                        text = _result_text(b.get('content'))
                        refused = bool(TOOL_REFUSED.search(text[:2000]))
                        yield {'kind': 'result', 'id': b.get('tool_use_id'), 'refused': refused, 'error': bool(b.get('is_error')) and not refused, 'text': text, 'ts': ts}
                text = '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
            else:
                continue
            text = _message_text(text, replay)
            if text:
                yield {'kind': 'msg', 'role': kind, 'text': text, 'ts': ts}


def _lines_of(text):
    return len(str(text).splitlines()) if text else 0


def _command(name, data):
    """The shell command of a call, or None when it is not one."""
    if name in ('Bash', 'shell', 'exec_command', 'local_shell', 'container.exec', 'shell_command'):
        cmd = data.get('command', data.get('cmd')) if isinstance(data, dict) else data
        if isinstance(cmd, list):
            cmd = cmd[-1] if len(cmd) >= 3 and cmd[0] in ('bash', 'sh', 'zsh') and cmd[1] in ('-lc', '-c') else ' '.join(map(str, cmd))
        return str(cmd or '')
    if name == 'exec' and isinstance(data, str):  # Codex 的脚本工具：里面一条条 exec_command({cmd: "…"})
        found = []
        for m in JS_CMD.finditer(data):
            try:
                found.append(json.loads(f'"{m.group(1)}"'))
            except ValueError:
                found.append(m.group(1))
        return ' ； '.join(found) if found else None
    return None


def _describe(name, data, roots):
    """(what, detail) for one tool call, paths made relative to where the session ran."""
    def rel(text):
        text = str(text or '')
        for r in roots:
            text = text.replace(r + '/', '').replace(r, '.')
        return text

    def short(text):
        return _one_line(SECRETISH.sub(lambda m: m.group(1) + '***', rel(text)), STEP_CAP)

    d = data if isinstance(data, dict) else {}
    cmd = _command(name, data)
    if cmd is not None:
        noted = re.search(r'rf\.py\s+note\s+(.*)', cmd, re.S)
        return ('记进展', short(noted.group(1))) if noted else ('跑', short(cmd))
    if name in ('Read', 'NotebookRead', 'view_image'):
        return '读', short(d.get('file_path') or d.get('notebook_path') or d.get('path'))
    if name in ('Glob', 'Grep', 'LS'):
        return '找', short(' '.join(str(d.get(k)) for k in ('pattern', 'path') if d.get(k)))
    if name in ('Edit', 'MultiEdit', 'Write', 'NotebookEdit'):
        if name == 'Write':
            delta = f'（写入 {_lines_of(d.get("content"))} 行）'
        else:
            edits = d.get('edits') if isinstance(d.get('edits'), list) else [d]
            plus = sum(_lines_of(e.get('new_string') or e.get('new_source')) for e in edits if isinstance(e, dict))
            minus = sum(_lines_of(e.get('old_string')) for e in edits if isinstance(e, dict))
            delta = f'（+{plus} −{minus}）'
        return '改', short(d.get('file_path') or d.get('notebook_path')) + delta
    if name == 'apply_patch':
        patch = data if isinstance(data, str) else d.get('input') or d.get('patch') or ''
        body = [l for l in str(patch).splitlines() if not l.startswith('***')]
        files = '、'.join(PATCH_FILE.findall(str(patch))) or '（没认出文件）'
        return '改', short(files) + f'（+{sum(1 for l in body if l.startswith("+"))} −{sum(1 for l in body if l.startswith("-"))}）'
    if name in ('Task', 'Agent'):
        return '子代理', short(d.get('description') or d.get('prompt'))
    if name in ('TodoWrite', 'update_plan'):
        return '更新计划', ''
    if name in ('WebFetch', 'WebSearch', 'web_search'):
        return '查网页', short(d.get('url') or d.get('query'))
    shown = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data or '')
    return name, short(shown) if shown not in ('{}', '') else ''


def _new_turn(n, msg, replay=False, secrets=()):
    said = msg['text'] if msg else ''
    if replay:
        said = _safe_text(re.sub(r'^<hook_prompt\b[^>]*>\s*', '', said), secrets)
        if len(said) > 4000:
            said = said[:4000] + '…'
    else:
        said = _one_line(said, SAY_CAP)
    return {'n': n, 'when': turn_when(msg['ts']) if msg else '', 'said': said, 'steps': [],
            'commits': [], 'reply': '', 'reply_n': None, 'calls': 0, 'failed': 0, 'refused': 0, 'read': 0, 'write': 0,
            '_start': msg['ts'] if msg else None, '_end': msg['ts'] if msg else None}


def _seconds(start, end):
    import datetime as dt
    try:
        a, b = (dt.datetime.fromisoformat(str(x).replace('Z', '+00:00')) for x in (start, end))
        return max(0, int((b - a).total_seconds()))
    except (ValueError, TypeError):
        return None


def _finish_turn(t, cap=STEPS_PER_TURN):
    """Merge repeated steps and add up the turn. cap=None (why) keeps every step and, for each commit, where it falls among them ('at')."""
    steps, merged, place = t['steps'], [], []
    for s in steps:
        if merged and (merged[-1]['what'], merged[-1]['detail'], merged[-1]['status']) == (s['what'], s['detail'], s['status']):
            merged[-1]['times'] += 1
        else:
            merged.append({'what': s['what'], 'detail': s['detail'], 'status': s['status'], 'times': 1})
        place.append(len(merged))
    for c in t['commits']:
        at = c.pop('_at', len(steps))
        if cap is None:
            c['at'] = place[at] if at < len(place) else len(merged)
    failed = {}
    for s in steps:
        if s['status'] == 'failed':
            failed[(s['what'], s['detail'])] = failed.get((s['what'], s['detail']), 0) + 1
    t['repeats'] = [{'what': w, 'detail': d, 'times': c} for (w, d), c in failed.items() if c >= 2]
    t['more'] = max(0, len(merged) - cap) if cap else 0
    t['steps'] = merged[:cap] if cap else merged
    t['seconds'] = _seconds(t.pop('_start'), t.pop('_end'))
    return t


COMMIT_MSG = re.compile(r"""\bgit\b[^\n;&|]*\bcommit\b[^\n;&|]*?\s-[a-zA-Z]*m\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+))""")


HEREDOC_MSG = re.compile(r"""\bgit\b[^\n;&|]*\bcommit\b[^\n;&|]*\s-F\s*-[^\n]*<<-?\s*['"]?(\w+)['"]?[^\n]*\n\s*([^\n]*)""")


def _commit_message(cmd):
    """The subject a git commit command gives (-m "…" or -F - with a here-document), or ''."""
    m = COMMIT_MSG.search(cmd or '')
    if m:
        text = next(g for g in m.groups() if g is not None).replace('\\"', '"')
    else:
        m = HEREDOC_MSG.search(cmd or '')
        text = m.group(2) if m and m.group(2).strip() != m.group(1) else ''
    return text.split('\n')[0].strip()


def _safe_text(text, secrets):
    return SECRETISH.sub(lambda m: m.group(1) + '***', redact(text, secrets))


def read_turns(home, path, cap=STEPS_PER_TURN, *, replay=False, secrets=()):
    """Every turn of one transcript (see steps_text). cap=None keeps all steps and where each commit falls among them."""
    roots, turns, calls, n = [str(home)], [], {}, 0
    turn = None
    for e in _events(path, replay):
        k = e['kind']
        if k == 'cwd':
            if e['path'] not in roots:
                roots.insert(0, e['path'])
            continue
        if k == 'msg':
            n += 1
            if e['role'] == 'user':
                program = replay and bool(PROGRAM.match(e['text']))
                if replay and not program and (CODEX_SYNTHETIC.match(e['text']) or REPLAY_SYNTHETIC.match(e['text'])):
                    continue
                if turn:
                    turns.append(turn)
                turn = _new_turn(n, e, replay, secrets)
                if program:
                    turn['speaker'] = 'program'
                continue
        if replay and turn is None and k in ('usage', 'aborted'):
            continue
        if turn is None:  # 用户开口之前的东西（很少有）算作第 0 轮
            turn = _new_turn(None, None)
        if e.get('ts'):
            turn['_start'] = turn['_start'] or e['ts']
            turn['_end'] = e['ts']
        if k == 'msg':
            turn['reply'], turn['reply_n'] = _one_line(_safe_text(e['text'], secrets) if replay else e['text'], SAY_CAP), n
        elif k == 'usage':
            turn['read'] += e['read']
            turn['write'] += e['write']
        elif k == 'call':
            data = redact(e['input'], secrets) if replay else e['input']
            what, detail = _describe(e['name'], data, roots)
            if replay and e['name'] == 'exec' and isinstance(data, str):
                actions = codex_script_tools(data)
                if actions:
                    parts = [_describe(a['name'], a['input'], roots) for a in actions]
                    # Codex 为整段脚本交回一个结果，按一次调用展示，保留里面的操作顺序。
                    if len(parts) == 1:
                        what, detail = parts[0]
                    else:
                        what = '脚本'
                        detail = ' · '.join(f'{action} {target}'.strip() for action, target in parts)
            cmd = _command(e['name'], e['input']) or ''
            step = {'what': what, 'detail': detail, 'status': 'pending', 'commit': bool(re.search(r'\bgit\b[^\n;&|]*\bcommit\b', cmd)), 'cmd': cmd,
                    'log': bool(re.search(r'\bgit\b[^\n;&|]*\blog\b[^\n;&|]*--oneline[^\n;&|]*(?:-1|-n ?1)\b', cmd))}
            turn['steps'].append(step)
            turn['calls'] += 1
            if e.get('id'):
                calls[e['id']] = (step, turn)
        elif k == 'result':
            step, owner = calls.pop(e.get('id'), (None, None))
            if not step:
                continue
            refused = e['refused'] or (replay and e['error'] and REPLAY_BLOCKED.search(e['text'][:600]))
            step['status'] = 'refused' if refused else 'failed' if e['error'] else 'ok'
            if step['status'] != 'ok':
                owner['refused' if refused else 'failed'] += 1
            elif step['commit']:
                found = COMMIT_OUT.findall(e['text'][:4000])
                if not found and step['log']:  # git commit -q 之后紧跟 git log --oneline -1：认那一行
                    found = LOG_LINE.findall(e['text'][:4000])[:1]
                if not found:  # git commit -q：输出里没有提交号，从命令里拿标题，why 按标题认
                    said = _commit_message(step['cmd'])
                    found = [('', said)] if said else []
                at = next((i for i, x in enumerate(owner['steps']) if x is step), len(owner['steps']))
                owner['commits'] += [dict({'commit': h[:7], 'subject': _one_line(_safe_text(m, secrets) if replay else m, 80)}, **({'_at': at} if '_start' in owner else {})) for h, m in found]
    if turn:
        turns.append(turn)
    # 结果可能在下一条程序消息之后才到，等它更新原步骤再合并相邻操作。
    return [_finish_turn(t, cap) for t in turns]


WHY_STEPS = 12          # why 里一个提交最多列几步


def commit_steps(root, tags, commit, subject='', cache=None):
    """The turn that made this commit, for why: the user's words and the steps since the previous commit in that turn.
    tags are the sessions its trailers name; matched by hash, else (amended or rebased) by subject. None when not found."""
    home = main_checkout(root)
    cache = {} if cache is None else cache
    for tag in tags:
        if tag not in cache:
            cache[tag] = None
            try:
                tag_, uuid, owner = resolve_session(home, tag)
                where, path = session_where(home, owner, uuid)
                if where:
                    cache[tag] = read_turns(home, path, cap=None)
            except (ValueError, OSError):
                pass
        turns = cache[tag] or []
        for exact in (True, False):
            for t in turns:
                for i, c in enumerate(t['commits']):
                    if (c['commit'] and str(commit).startswith(c['commit'])) if exact else (subject and c['subject'] == _one_line(subject, 80)):
                        start = t['commits'][i - 1]['at'] if i else 0
                        window = t['steps'][start:c['at']]
                        failed = {}
                        for st in window:
                            if st['status'] == 'failed':
                                failed[(st['what'], st['detail'])] = failed.get((st['what'], st['detail']), 0) + st['times']
                        return {'session': tag, 'turn': t['n'], 'when': t['when'], 'said': t['said'], 'exact': exact,
                                'steps': window[-WHY_STEPS:], 'before': max(0, len(window) - WHY_STEPS),
                                'repeats': [{'what': w, 'detail': d, 'times': n} for (w, d), n in failed.items() if n >= 2]}
    return None


def commit_steps_lines(found, indent='    '):
    """Text for why: one line for the turn, then its steps."""
    if not found:
        return []
    head = f'{indent}产生它的那一轮：会话 {found["session"]} 【{found["turn"]}】用户' + (f' {found["when"]}' if found['when'] else '') + f'：{_one_line(found["said"], 100)}'
    lines = [head + ('' if found['exact'] else '（按提交标题认的：聊天记录里没有提交号，或者对不上）')]
    if found['before']:
        lines.append(f'{indent}  · …前面还有 {found["before"]} 步')
    lines += [indent + step_line(st) for st in found['steps']]
    for r in found['repeats']:
        lines.append(f'{indent}  ！同一步失败了 {r["times"]} 次：{r["what"]} {r["detail"]}')
    return lines


STEP_MARK = {'ok': '', 'failed': ' ✗ 失败', 'refused': ' ⊘ 被拒绝', 'pending': ' …没有结果'}


def step_line(st):
    return f'  · {st["what"]}' + (f' {st["detail"]}' if st['detail'] else '') + STEP_MARK.get(st['status'], '') + (f' ×{st["times"]}' if st['times'] > 1 else '')
