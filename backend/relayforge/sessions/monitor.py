"""会话监控：从会话记录里由程序算出 Agent 有没有照开场白的规矩做、哪些工具用了哪些没用、在哪里打转。只读，不调用模型。

  rf.py monitor [--since 日期] [--kind develop|test|fix|probe|intake|map|chat] [--owner R-…|F-…|I-…] [--session S-…] [--json]

看三件事：
1. 指令遵循：每种会话的开场白（app/src/main/prompts.js）里写了要做和不许做的事，按 RULES 逐条核对。
   只核对能从记录里看出来的：跑没跑某条命令、在什么之前跑的、改了哪些文件。改了开场白要同步改 RULES。
2. 工具使用：每个工具、每条 rf.py 命令用了多少次、多少个会话用过；开场白推荐的命令（GUIDED）在该用它的会话里有几成用了。
3. 打转：同一步反复失败、被规则拦下后还在重试、一轮里反复改同一个文件、很长的一轮、没有结果的步骤、用户打断。

会话从需求、修复、排查、大图和日常聊天的记录里找（同 steps.tag_map），记录用本机另存的副本或还在的原始记录。
Codex 和 Claude Code 的记录都认；被规则拦下（权限规则、rf.py 的用户关口、沙箱）和用户拒绝分开算。
"""
import datetime as dt
import re

from ..core.common import DIR, main_checkout
from ..core.documents import read_doc
from ..facts.mapfacts import map_session_id
from ..issues.record import probe_dir, read_probe
from ..requirements.owners import all_fixes
from .record import stable_tag
from .steps import _command, _describe, _events, session_where

__all__ = ['monitor', 'monitor_text', 'MONITOR_KINDS']

MONITOR_KINDS = ('develop', 'test', 'fix', 'probe', 'intake', 'map', 'chat')
KIND_WORDS = {'develop': '开发', 'test': '测试', 'fix': '修复', 'probe': '排查', 'intake': '立项', 'map': '大图', 'chat': '日常聊天'}

# 被规则拦下：Claude Code 的权限规则、rf.py 的用户关口、Codex 的沙箱
BLOCKED = re.compile(r'denied by your permission settings|Permission to use [^\n]{0,200}has been denied|has been denied by (?:the )?permission'
                     r'|只能由你在应用里做|Operation not permitted|sandbox[^\n]{0,40}(?:denied|blocked)|blocked by (?:a )?hook', re.I)
BLOCKED_HEAD = 600       # 拦截说明只在结果开头这么多字里找
INTERRUPTED = re.compile(r'^\[Request interrupted by user|^<turn_aborted>')
# 程序插进来、不是用户说的「用户消息」：上下文压缩后的续接、Codex 的环境和规则说明
SYNTHETIC = re.compile(r'^(?:This session is being continued from a previous conversation|<environment_context>|<user_instructions>|# AGENTS\.md instructions'
                       r'|<permissions instructions>|Caveat: The messages below)')
# 真正执行的 rf.py：在命令开头、; && | 换行 $( 之后，前面可以有 python3 [-B] 和路径；写在引号里、grep 里的不算
RF_CMD = re.compile(r'(?:^|[;&|\n]|\$\()[ \t]*(?:[A-Za-z_]+=\S*[ \t]+)*(?:python3?(?:[ \t]+-[A-Za-z]+)*[ \t]+)?["\']?[^\s;&|"\']*rf\.py["\']?[ \t]+([a-z][\w-]*)(?:[ \t]+([a-z][\w-]*))?')
SUBCOMMANDS = {'note', 'req', 'issue', 'test', 'env', 'design', 'map', 'history', 'publish', 'branch', 'data'}  # 第二个词是动作的

# 打转的门槛
REPEAT_FAIL = 3         # 同一步（同一条命令、同一个文件）在一个会话里失败几次算打转
BLOCKED_RETRY = 2       # 被同一条规则拦下几次算「拦下后还在重试」
EDIT_CHURN = 8          # 一轮里同一个文件改几次算反复改
LONG_TURN = 30 * 60     # 一轮实际干活多少秒算很长（不算中间停着没动静的时间）
STALL = 10 * 60         # 一轮中间多少秒没有任何动静算停住了
RECENT = 2 * 60 * 60    # 最后一次动静在这么多秒以内的会话可能还没做完：「做完时要交回」的规矩先不算
AT_END = {'commit', 'handback', 'run', 'verdict', 'finding', 'record'}  # 这些规矩要等会话做完才看得出来
LONG_CALLS = 150        # 一轮多少次工具调用算很长


# ---------- 有哪些会话 ----------

def requirement_sessions(meta):
    intake = {str(x) for x in meta.get('intake') or []}
    tests = {str(x) for x in meta.get('test_sessions') or []}
    session_ids = dict.fromkeys(str(u) for u in (meta.get('sessions') or []) + (meta.get('test_sessions') or []) if u)
    return [(sid, 'test' if sid in tests else 'intake' if sid in intake or meta.get('state') == 'draft' else 'develop')
            for sid in session_ids]


def session_inventory(home):
    """按项目记录列出会话。一个会话出现在多个记录里时，取第一条关联。"""
    out, seen = [], set()

    def add(uuid, owner, kind):
        if uuid and (uuid := str(uuid)) not in seen:
            seen.add(uuid)
            out.append({'tag': stable_tag(home, uuid), 'uuid': uuid, 'owner': owner, 'kind': kind})

    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        meta = read_doc(path)[0]
        for sid, kind in requirement_sessions(meta):
            add(sid, meta.get('id'), kind)
    for fid, fix in all_fixes(home).items():
        for u in fix.get('sessions') or []:
            add(u, fid, 'fix')
        for one in fix.get('issues') or []:
            for u in (read_probe(home, one) or {}).get('sessions') or []:
                add(u, one, 'probe')
    for path in sorted(probe_dir(home).glob('*.json')):  # 交给 AI 排查过、还没修的问题
        probe = read_probe(home, path.stem) or {}
        for u in probe.get('sessions') or []:
            add(u, path.stem, 'probe')
    add(map_session_id(home), None, 'map')
    for path in sorted((home / DIR / 'sessions').glob('*.md')):
        meta = read_doc(path)[0]
        if meta.get('session_id'):
            add(meta['session_id'], None, 'chat')
    return out


# ---------- 一个会话做了什么 ----------

def _edited(what, detail):
    """Files an edit step touched (its detail without the line counts)."""
    if what != '改':
        return []
    return [f for f in re.sub(r'（[^（]*）$', '', detail).split('、') if f and f != '（没认出文件）']


def _seconds(a, b):
    try:
        x, y = (dt.datetime.fromisoformat(str(t).replace('Z', '+00:00')) for t in (a, b))
        return max(0, int((y - x).total_seconds()))
    except (ValueError, TypeError):
        return 0


def _analyze(home, path):
    """One pass over a transcript: every call with its turn and status, the rf.py commands it ran, each turn's busy time and tokens,
    the stretches where nothing happened inside a turn (stalls), and user interrupts."""
    roots, calls, pending, interrupts, first, last = [str(home)], [], {}, 0, None, None
    turns, stalls, turn, prev = [], [], None, None

    def new_turn(n, ts):
        t = {'n': n, 'start': ts, 'end': ts, 'idle': 0, 'calls': 0, 'failed': 0, 'read': 0, 'write': 0, 'said': '', 'reply': '', 'interrupts': 0}
        turns.append(t)
        return t

    holder = None  # 上一个有时间的事件之后，球在谁手里：'model'（用户刚说完、工具刚返回）、'reply'（已经回复，等用户）
    for e in _events(path):
        k, ts = e['kind'], e.get('ts')
        if k == 'cwd' or (k == 'msg' and e['role'] == 'user' and SYNTHETIC.match(e['text'])):
            if k == 'cwd' and e['path'] not in roots:
                roots.insert(0, e['path'])
            continue
        if k == 'msg' and e['role'] == 'user' and not INTERRUPTED.match(e['text']):
            turn, prev = new_turn(len(turns) + 1, ts), None
            turn['said'] = e['text']
        elif turn is None:
            turn = new_turn(0, ts)
        if ts:
            first, last = first or ts, ts
            gap = _seconds(turn['end'], ts) if turn['end'] else 0
            if gap >= STALL:
                turn['idle'] += gap
                waiting = list(pending.values())
                if waiting or holder == 'model':
                    on = waiting[-1] if waiting else prev or {}
                    stalls.append({'turn': turn['n'], 'seconds': gap, 'waiting': bool(waiting), 'after': on.get('what', ''), 'detail': on.get('detail', '')})
            turn['start'], turn['end'] = turn['start'] or ts, ts
        if k == 'msg':
            holder = 'model' if e['role'] == 'user' else 'reply'
            if e['role'] != 'user':
                turn['reply'] = e['text']
        elif k in ('call', 'result'):
            holder = 'model'
        if k == 'aborted' or (k == 'msg' and e['role'] == 'user' and INTERRUPTED.match(e['text'])):
            interrupts += 1
            turn['interrupts'] += 1
            holder = 'reply'  # 打断以后球在用户手里
        elif k == 'usage':
            turn['read'] += e['read']
            turn['write'] += e['write']
        elif k == 'call':
            what, detail = _describe(e['name'], e['input'], roots)
            cmd = _command(e['name'], e['input']) or ''
            rf = [' '.join(x for x in (m.group(1), m.group(2) if m.group(1) in SUBCOMMANDS else None) if x) for m in RF_CMD.finditer(cmd)]
            call = {'turn': turn['n'], 'tool': e['name'], 'what': what, 'detail': detail, 'cmd': cmd, 'rf': rf, 'status': 'pending',
                    'files': _edited(what, detail), 'commit': bool(re.search(r'\bgit\b[^\n;&|]*\bcommit\b', cmd)), 'why': ''}
            calls.append(call)
            turn['calls'] += 1
            prev = call
            if e.get('id'):
                pending[e['id']] = call
        elif k == 'result':
            call = pending.pop(e.get('id'), None)
            if call:
                head = e['text'][:BLOCKED_HEAD]
                blocked = (e['error'] or e['refused']) and BLOCKED.search(head)  # 只认失败的调用开头的拦截说明：输出正文里提到这些字不算
                call['status'] = 'blocked' if blocked else 'refused' if e['refused'] else 'failed' if e['error'] else 'ok'
                if call['status'] == 'failed':
                    next(t for t in turns if t['n'] == call['turn'])['failed'] += 1
                if blocked:
                    call['why'] = ' '.join(head.split())[:160]
    for t in turns:
        t['busy'] = max(0, _seconds(t['start'], t['end']) - t['idle']) if t['start'] else None
        t['done'] = t is not turn or (holder == 'reply' and not pending)  # 最后一轮：回复完了、没有还在等结果的调用，才算说完
    return {'calls': calls, 'interrupts': interrupts, 'first': first, 'last': last, 'turns': len([t for t in turns if t['n']]),
            'turn_stats': turns, 'stalls': stalls, 'tokens': {'read': sum(t['read'] for t in turns), 'write': sum(t['write'] for t in turns)}}


def _ran(s, command):
    """Index of the first successful call that ran this rf.py command (prefix match on 'note plan', 'issue handback' …), or None."""
    return next((i for i, c in enumerate(s['calls']) if c['status'] == 'ok' and any(r == command or r.startswith(command + ' ') for r in c['rf'])), None)


def _tried(s, command):
    return any(r == command or r.startswith(command + ' ') for c in s['calls'] for r in c['rf'])


def _first_edit(s, code_only=True):
    return next((i for i, c in enumerate(s['calls']) if c['files'] and c['status'] == 'ok'
                 and (not code_only or any(DIR + '/' not in f for f in c['files']))), None)


def _record_edits(s, sub=''):
    return [f for c in s['calls'] if c['status'] == 'ok' for f in c['files'] if (DIR + '/' + sub) in f]  # 被拦下的尝试算在打转里


# ---------- 开场白里的规矩 ----------
# (会话种类, 编号, 规矩, 核对)。核对返回 True 做到了、False 没做到、None 这条不适用（比如一行代码都没改就不看提交）。
# 规矩的原文在 app/src/main/prompts.js：develop / following、fixing、testing*、investigating、intake。

def _did_work(s):
    return bool(s['calls'])


RULES = [
    ('develop', 'read', '先读需求文件', lambda s: None if not _did_work(s) else any(
        '/requirements/' in c['detail'] and c['what'] in ('读', '跑') for c in s['calls']) or _tried(s, 'req brief')),
    ('develop', 'plan', '动手前用 note plan 写计划', lambda s: None if _first_edit(s) is None else (
        _ran(s, 'note plan') is not None and _ran(s, 'note plan') < _first_edit(s))),
    ('develop', 'note', '边做边记（rf.py note）', lambda s: None if _first_edit(s) is None else _ran(s, 'note') is not None),
    ('develop', 'commit', '改了代码就提交到当前分支', lambda s: None if _first_edit(s) is None else any(c['commit'] and c['status'] == 'ok' for c in s['calls'])),
    ('develop', 'no-done', '不运行 req done', lambda s: not _tried(s, 'req done')),
    ('develop', 'no-hand', '不手工改 .relayforge 下的记录（用 rf.py note）', lambda s: not _record_edits(s)),
    ('fix', 'repro', '先复现：第一次改代码之前跑过命令', lambda s: None if _first_edit(s) is None else any(
        c['what'] == '跑' and not c['rf'] for c in s['calls'][:_first_edit(s)])),
    ('fix', 'handback', '修完用 issue handback 交回', lambda s: None if _first_edit(s) is None else _ran(s, 'issue handback') is not None),
    ('fix', 'note', '边做边记（rf.py note）', lambda s: None if _first_edit(s) is None else _ran(s, 'note') is not None),
    ('fix', 'commit', '改动提交到当前分支', lambda s: None if _first_edit(s) is None else any(c['commit'] and c['status'] == 'ok' for c in s['calls'])),
    ('fix', 'no-confirm', '不运行 issue confirm / unconfirm / reject', lambda s: not any(_tried(s, 'issue ' + a) for a in ('confirm', 'unconfirm', 'reject'))),
    ('fix', 'no-hand', '不手工改 .relayforge/issues/ 下的文件', lambda s: not _record_edits(s, 'issues/')),
    ('test', 'run', '用 test run 跑测试集', lambda s: None if not _did_work(s) else _ran(s, 'test run') is not None),
    ('test', 'verdict', '用 req verdict 交回结论', lambda s: None if not _did_work(s) else _ran(s, 'req verdict') is not None),
    ('test', 'no-hand', '不手工改 .relayforge 下的记录', lambda s: not _record_edits(s)),
    ('probe', 'read-only', '只读：不改任何文件', lambda s: not any(c['files'] for c in s['calls'])),
    ('probe', 'finding', '用 issue finding 记下结论', lambda s: None if not _did_work(s) else _ran(s, 'issue finding') is not None),
    ('probe', 'no-act', '不运行 issue fix / set / reply / confirm 和 req start / done', lambda s: not any(
        _tried(s, x) for x in ('issue fix', 'issue set', 'issue reply', 'issue confirm', 'issue unconfirm', 'issue reject', 'req start', 'req done', 'req drop'))),
    ('intake', 'record', '聊定的事用 note say / feature / check 记进需求', lambda s: None if not _did_work(s) else any(
        _ran(s, 'note ' + x) is not None for x in ('say', 'feature', 'check'))),
    ('intake', 'no-hand', '不手工改需求文件', lambda s: not _record_edits(s, 'requirements/')),
]

# 开场白推荐、但不强制的命令：(命令, 推荐给哪几种会话, 什么时候算该用)
GUIDED = [
    ('why', ('develop', 'fix'), '改了代码的'),
    ('history', ('develop', 'fix', 'intake'), '干了活的'),
    ('design list', ('develop',), '改了代码的'),
    ('req cases', ('test',), '干了活的'),
    ('issue new', ('develop', 'test', 'fix'), '干了活的'),
]


# ---------- 打转 ----------

def _loops(s):
    out = []
    failed, blocked = {}, {}
    for c in s['calls']:
        key = (c['what'], c['detail'])
        if c['status'] == 'failed':
            failed[key] = failed.get(key, []) + [c['turn']]
        if c['status'] == 'blocked':
            blocked.setdefault((c['tool'], c['files'][0] if c['files'] else c['detail']), []).append(c)
    for (what, detail), turns in failed.items():
        if len(turns) >= REPEAT_FAIL:
            out.append({'kind': 'repeat-fail', 'turn': turns[0], 'times': len(turns), 'text': f'同一步失败了 {len(turns)} 次：{what} {detail}'})
    for (tool, target), hits in blocked.items():
        if len(hits) >= BLOCKED_RETRY:
            out.append({'kind': 'blocked-retry', 'turn': hits[0]['turn'], 'times': len(hits),
                        'text': f'被规则拦下后又试了 {len(hits) - 1} 次：{tool} {target}（拦下的原因：{hits[0]["why"]}）'})
    churn = {}
    for c in s['calls']:
        if c['status'] == 'ok':
            for f in c['files']:
                churn[(c['turn'], f)] = churn.get((c['turn'], f), 0) + 1
    for (turn, f), n in churn.items():
        if n >= EDIT_CHURN:
            out.append({'kind': 'edit-churn', 'turn': turn, 'times': n, 'text': f'一轮里把 {f} 改了 {n} 次'})
    for t in s['turn_stats']:
        if (t['busy'] or 0) >= LONG_TURN or t['calls'] >= LONG_CALLS:
            out.append({'kind': 'long-turn', 'turn': t['n'], 'times': t['calls'],
                        'text': f'很长的一轮：实际干活 {_span(t["busy"])}，{t["calls"]} 次工具调用' + (f'，失败 {t["failed"]} 次' if t['failed'] else '')})
    for st in s['stalls']:
        doing = f'{st["after"]} {st["detail"]}'.strip()
        out.append({'kind': 'stall', 'turn': st['turn'], 'times': st['seconds'],
                    'text': (f'等「{doing}」的结果等了 {_span(st["seconds"])}（命令跑得久或挂住了，或者在等你批准）' if st['waiting'] else f'停了 {_span(st["seconds"])}没有动静：')
                    + ('' if st['waiting']
                                                                   else f'上一步是「{doing}」，之后模型一直没有下一步' if doing else '你说完以后模型一直没有动')})
    dangling = [c for c in s['calls'] if c['status'] == 'pending']
    if dangling:
        out.append({'kind': 'no-result', 'turn': dangling[0]['turn'], 'times': len(dangling),
                    'text': f'{len(dangling)} 步没有结果（卡住、被打断，或者会话还在跑）：{dangling[-1]["what"]} {dangling[-1]["detail"]}'})
    if s['interrupts']:
        out.append({'kind': 'interrupted', 'turn': None, 'times': s['interrupts'], 'text': f'你打断了 {s["interrupts"]} 次'})
    return out


LOOP_ORDER = ['blocked-retry', 'repeat-fail', 'edit-churn', 'stall', 'no-result', 'long-turn', 'interrupted']


def _span(seconds):
    if seconds is None:
        return '用时不明'
    return f'{seconds // 3600} 小时 {seconds % 3600 // 60} 分' if seconds >= 3600 else f'{seconds // 60} 分钟'


# ---------- 汇总 ----------

def _day(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return ''


def monitor(root, since=None, kinds=(), owner=None, session=None):
    """Read every recorded session (narrowed by the filters) and work out rules kept, tools used and where it went round in circles."""
    home = main_checkout(root)
    if since and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', since):
        raise ValueError('--since 写成 2026-11-01 这样的日期。')
    picked = [x for x in session_inventory(home) if (not kinds or x['kind'] in kinds) and (not owner or x['owner'] == owner)
              and (not session or session in (x['tag'], x['uuid']))]
    sessions, missing = [], []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for item in picked:
        where, path = session_where(home, item['owner'], item['uuid'])
        if not where:
            if item['kind'] != 'map':  # 大图会话的编号是固定算出来的，没打开过就没有记录
                missing.append(item)
            continue
        s = dict(item, where=where, **_analyze(home, path))
        if since and (_day(s['last']) or '0000') < since:
            continue
        s['live'] = bool(s['last']) and _seconds(s['last'], now) < RECENT
        s['rules'] = {key: None if s['live'] and key in AT_END else check(s) for kind, key, _, check in RULES if kind == s['kind']}
        s['loops'] = sorted(_loops(s), key=lambda x: LOOP_ORDER.index(x['kind']))
        sessions.append(s)
    return {'root': str(home), 'since': since, 'sessions': [_public(s) for s in sessions], 'missing': missing,
            'rules': _rule_totals(sessions), 'tools': _tool_totals(sessions), 'guided': _guided(sessions)}


def _public(s):
    """A session for the report and --json: the counts and findings, not every call."""
    statuses = {}
    for c in s['calls']:
        statuses[c['status']] = statuses.get(c['status'], 0) + 1
    return {'tag': s['tag'], 'uuid': s['uuid'], 'owner': s['owner'], 'kind': s['kind'], 'where': s['where'], 'first': s['first'], 'last': s['last'],
            'live': s['live'], 'turns': s['turns'], 'calls': len(s['calls']), 'tokens': s['tokens'], 'status': statuses, 'interrupts': s['interrupts'],
            'stalls': s['stalls'], 'rules': s['rules'], 'loops': s['loops'], 'blocked': [{'turn': c['turn'], 'tool': c['tool'], 'detail': c['detail'], 'why': c['why']}
                                                                    for c in s['calls'] if c['status'] == 'blocked']}


def _rule_totals(sessions):
    out = []
    for kind, key, text, _ in RULES:
        mine = [s for s in sessions if s['kind'] == kind and s['rules'].get(key) is not None]
        broke = [s for s in mine if s['rules'][key] is False]
        if mine:
            out.append({'kind': kind, 'key': key, 'text': text, 'applies': len(mine), 'kept': len(mine) - len(broke),
                        'broke': [{'tag': s['tag'], 'owner': s['owner']} for s in broke]})
    return out


def _tool_totals(sessions):
    tools, rf = {}, {}
    for s in sessions:
        for c in s['calls']:
            for bucket, name in [(tools, c['tool'])] + [(rf, r) for r in c['rf']]:
                row = bucket.setdefault(name, {'calls': 0, 'sessions': set(), 'failed': 0, 'blocked': 0})
                row['calls'] += 1
                row['sessions'].add(s['tag'])
                row['failed'] += c['status'] == 'failed'
                row['blocked'] += c['status'] == 'blocked'
    flat = lambda bucket: sorted(({'name': k, 'calls': v['calls'], 'sessions': len(v['sessions']), 'failed': v['failed'], 'blocked': v['blocked']}
                                  for k, v in bucket.items()), key=lambda r: (-r['calls'], r['name']))
    return {'tools': flat(tools), 'rf': flat(rf)}


def _guided(sessions):
    out = []
    for command, kinds, when in GUIDED:
        mine = [s for s in sessions if s['kind'] in kinds and (_first_edit(s) is not None if when == '改了代码的' else _did_work(s))]
        used = [s for s in mine if _tried(s, command)]
        if mine:
            out.append({'command': command, 'kinds': list(kinds), 'when': when, 'applies': len(mine), 'used': len(used)})
    return out


# ---------- 文字报告 ----------

def monitor_text(out):
    ss = out['sessions']
    if not ss:
        return '没有找到会话记录' + ('（按筛选条件）' if out.get('since') else '') + '。' + (f'有 {len(out["missing"])} 个会话的记录在本机已经找不到了。' if out['missing'] else '')
    counts = {}
    for s in ss:
        counts[s['kind']] = counts.get(s['kind'], 0) + 1
    calls = sum(s['calls'] for s in ss)
    status = {}
    for s in ss:
        for k, v in s['status'].items():
            status[k] = status.get(k, 0) + v
    lines = [f'会话监控：{len(ss)} 个会话（' + '、'.join(f'{KIND_WORDS[k]} {n}' for k, n in counts.items()) + f'），{calls} 次工具调用'
             + f'：失败 {status.get("failed", 0)}、被规则拦下 {status.get("blocked", 0)}、你拒绝 {status.get("refused", 0)}、没有结果 {status.get("pending", 0)}。'
             + (f'另有 {len(out["missing"])} 个会话的记录在本机找不到了。' if out['missing'] else ''),
             '全部由程序从会话记录算出，没有调用模型；只看得到工具调用和命令，看不到 Agent 心里怎么想的。', '']

    lines.append('一、开场白里的规矩做到了没有（做到的会话 / 适用的会话）')
    for r in sorted(out['rules'], key=lambda r: (r['kept'] / r['applies'], r['kind'])):
        mark = '  ' if r['kept'] == r['applies'] else '✗ '
        who = '、'.join(f'{b["tag"]}' + (f'（{b["owner"]}）' if b['owner'] else '') for b in r['broke'][:5]) + (' …' if len(r['broke']) > 5 else '')
        lines.append(f'  {mark}{KIND_WORDS[r["kind"]]}：{r["text"]}　{r["kept"]}/{r["applies"]}' + (f'　没做到：{who}' if r['broke'] else ''))
    lines.append('')

    lines.append('二、工具')
    lines.append('  开场白推荐的命令，该用的会话里用了几个：')
    for g in out['guided']:
        lines.append(f'    rf.py {g["command"]}：{g["used"]}/{g["applies"]}（{"、".join(KIND_WORDS[k] for k in g["kinds"])}会话里{g["when"]}）' + ('　从来没用过' if not g['used'] else ''))
    lines.append('  用得最多的工具（次数，会话数，失败，被拦下）：')
    for t in out['tools']['tools'][:12]:
        lines.append(f'    {t["name"]}：{t["calls"]} 次，{t["sessions"]} 个会话' + (f'，失败 {t["failed"]}' if t['failed'] else '') + (f'，被拦下 {t["blocked"]}' if t['blocked'] else ''))
    lines.append('  rf.py 命令：')
    for t in out['tools']['rf'][:20]:
        lines.append(f'    {t["name"]}：{t["calls"]} 次，{t["sessions"]} 个会话' + (f'，失败 {t["failed"]}' if t['failed'] else '') + (f'，被拦下 {t["blocked"]}' if t['blocked'] else ''))
    lines.append('')

    loops = [(s, l) for s in ss for l in s['loops']]
    lines.append(f'三、打转（{len(loops)} 处）' if loops else '三、打转：没有发现。')
    for s, l in sorted(loops, key=lambda x: (LOOP_ORDER.index(x[1]['kind']), -x[1]['times'])):
        at = f'【{l["turn"]}】' if l['turn'] else ''
        lines.append(f'  · {s["tag"]}（{KIND_WORDS[s["kind"]]}' + (f'，{s["owner"]}' if s['owner'] else '') + f'）{at}{l["text"]}')
    lines.append('')
    lines.append('看某个会话具体怎么走的：rf.py history session 会话编号 --steps')
    return '\n'.join(lines)
