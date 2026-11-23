"""从本机聊天记录按会话种类和模型统计 token。"""

import json
import re

from ..core.common import main_checkout
from ..core.transcript import open_text
from ..issues.record import read_probe
from ..requirements.owners import find_issue, read_fix
from .archive import find_archived, find_original
from .monitor import KIND_WORDS, requirement_sessions, session_inventory
from .record import stable_tag
from .steps import resolve_session
from .trace import resolve

COUNTERS = ('input', 'output', 'cache_read', 'cache_creation')
CLAUDE_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
CODEX_FIELDS = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens')


def _counts(data, fields):
    if not isinstance(data, dict) or not any(type(data.get(k)) is int and data[k] >= 0 for k in fields):
        return None
    return tuple(max(0, data[k]) if type(data.get(k)) is int else 0 for k in fields)


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _view(counts):
    return dict(zip(COUNTERS, counts)) | {'total': counts[0] + counts[1]}


def _usage_by_model(paths):
    models, claude_messages = {}, {}

    def add(agent, model, counts):
        key = (agent, model)
        models[key] = _add(models.get(key, (0, 0, 0, 0)), counts)

    for path, subagent in paths:
        model, previous = '', (0, 0, 0, 0)
        with open_text(path) as lines:
            for line in lines:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                kind = obj.get('type')
                payload = obj.get('payload')
                if kind in ('session_meta', 'turn_context') and isinstance(payload, dict):
                    model = str(payload.get('model') or model)
                if kind == 'event_msg' and isinstance(payload, dict) and payload.get('type') == 'token_count':
                    info = payload.get('info')
                    if not isinstance(info, dict):
                        continue
                    total = _counts(info.get('total_token_usage'), CODEX_FIELDS)
                    last = _counts(info.get('last_token_usage'), CODEX_FIELDS)
                    if total is not None:
                        # 限额刷新和上下文压缩也会发事件，累计计数没变就没有新用量。
                        delta = tuple(x - y for x, y in zip(total, previous))
                        if any(delta) or not any(total):
                            add('codex', model, (last or total) if min(delta) < 0 else delta)
                        previous = total
                    elif last is not None:
                        add('codex', model, last)
                        previous = _add(previous, last)
                message = obj.get('message')
                if kind != 'assistant' or not isinstance(message, dict) or (obj.get('isSidechain') and not subagent):
                    continue
                counts = _counts(message.get('usage'), CLAUDE_FIELDS)
                model_name = str(message.get('model') or '')
                if counts is None or model_name == '<synthetic>':
                    continue
                key = message.get('id') or obj.get('uuid')
                if isinstance(key, str):
                    old_model, old = claude_messages.get(key, ('', (0, 0, 0, 0)))
                    # 同一条回复会分块落盘，后面的块可能才有完整用量。
                    claude_messages[key] = (model_name or old_model, tuple(max(x, y) for x, y in zip(old, counts)))
                else:
                    add('claude', model_name, counts)
    for model, counts in claude_messages.values():
        add('claude', model, counts)
    return [{'agent': agent, 'model': model,
             **_view((counts[0] + counts[2] + counts[3], *counts[1:]) if agent == 'claude' else counts)}
            for (agent, model), counts in sorted(models.items())]


def _session_row(home, row):
    sid = row['uuid']
    source, models = None, []
    try:
        kept = find_archived(sid, home)
        original = find_original(sid)
        if original:
            source = 'original'
            paths = [(original['main'], False)] + [(p, True) for p in original['subagents']]
        elif kept:
            source = 'archive'
            paths = [(kept, False)] + [(p, True) for p in sorted((kept.parent / sid / 'subagents').glob('*.gz'))]
        if source:
            models = _usage_by_model(paths)
    except (OSError, EOFError):
        source = None
    return {'session': row['tag'], 'session_id': sid, 'kind': row['kind'], 'source': source,
            'models': models, 'tokens': _sum(models) if models else None}


def _sum(rows):
    return _view(tuple(sum(row[k] for row in rows) for k in COUNTERS))


def _selected(home, ref, session):
    if session:
        tag, sid, _ = resolve_session(home, session)
        row = next((r for r in session_inventory(home) if r['uuid'] == sid), {'tag': tag, 'uuid': sid, 'kind': 'chat'})
        return tag, [row]
    if re.fullmatch(r'I-\d+', ref, re.I):
        issue = find_issue(home, ref)[1]
        fix_id = issue.get('fix')
        fix = read_fix(home, fix_id) if fix_id and re.fullmatch(r'F-\d+', str(fix_id)) else None
        # 同批修复的会话属于整批，每条问题只计自己的排查会话。
        probes = (read_probe(home, issue['id']) or {}).get('sessions') or []
        rows = [(u, 'probe') for u in probes] + [(u, 'fix') for u in (fix or {}).get('sessions') or []]
        ident = issue['id']
    elif re.fullmatch(r'(?:R-[\w-]+|F-\d+)', ref, re.I):
        item = resolve(home, ref)
        ident = item['id']
        rows = requirement_sessions(item['meta']) if item['kind'] == 'requirement' else [(u, 'fix') for u in item['sessions']]
    else:
        raise ValueError('用法：usage R-… | F-… | I-… | --session 会话编号 [--json]。')
    return ident, [{'tag': stable_tag(home, str(u)), 'uuid': str(u), 'kind': k} for u, k in rows if u]


def usage_report(root, ref=None, session=None):
    if bool(ref) == bool(session):
        raise ValueError('请提供一个需求、修复、问题编号，或用 --session 指定一个会话。')
    home = main_checkout(root)
    ident, picked = _selected(home, (ref or '').strip(), session)
    sessions, groups, seen = [], {}, set()
    for row in picked:
        if row['uuid'] in seen:
            continue
        seen.add(row['uuid'])
        one = _session_row(home, row)
        sessions.append(one)
        for model in one['models']:
            groups.setdefault((one['kind'], model['agent'], model['model']), []).append(model)
    breakdown = [{'kind': kind, 'agent': agent, 'model': model, **_sum(rows)}
                 for (kind, agent, model), rows in sorted(groups.items())]
    return {'id': ident, 'sessions': sessions, 'groups': breakdown, 'tokens': _sum(breakdown)}


def usage_text(report):
    sessions = report['sessions']
    lines = [report['id'] + (f' 已统计 {report["tokens"]["total"]:,} token' if report['groups'] else ' 没有可用的 token 数据。')]
    for one in sessions:
        total = one['tokens']
        label = f'{total["total"]:,} token' if total else ('没有 token 数据' if one['source'] else '本机没有可读取的聊天记录')
        lines.append(f'  {KIND_WORDS[one["kind"]]} {one["session"]}  {label}')
    if report['groups']:
        lines.append('按会话种类和模型')
    for row in report['groups']:
        model = row['model'] or '模型未知'
        lines.append(f'  {KIND_WORDS[row["kind"]]} · {row["agent"]} · {model}  {row["total"]:,} token'
                     f'（输入 {row["input"]:,}，输出 {row["output"]:,}，缓存读取 {row["cache_read"]:,}，缓存写入 {row["cache_creation"]:,}）')
    return '\n'.join(lines)
