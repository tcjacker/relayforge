"""需求里的测试记录：测试用例、用例改动、测试轮次、逐条验收的标记，读出来、算成界面和需求列表要的样子。

只解析需求文件里的这几节，不运行任何东西。需求列表（requirements.load_requirements）、测试集（testsets）都要读它们；
放在这里，它们就不必导入运行测试、验收的上层模块（testing、usecases、accept）。分层见 tests/test_layers.py。
"""
import json
import re

from ..core.documents import live_items, read_doc
from ..core.identity import caller
from .owners import requirement_path


# ---------- 测试用例（usecases） ----------

CASE_HEAD, CHANGE_HEAD = '测试用例', '用例改动'
SOURCES = {'intake': '立项', 'test': '测试补充', 'user': '用户'}
SOURCE_OF = {v: k for k, v in SOURCES.items()}
OPS = {'edit': '修改', 'delete': '删除', 'add': '新增', 'classify': '归类'}
OP_OF = {v: k for k, v in OPS.items()}
CSTATES = {'pending': '待确认', 'approved': '已同意', 'rejected': '不同意'}
CSTATE_OF = {v: k for k, v in CSTATES.items()}
ACTORS = {'intake': '立项会话', 'develop': '开发会话', 'test': '测试会话', 'user': '用户'}
CASE_LINE = re.compile(r'^- 用例 (\d+)｜(.*?)｜(.*?)｜(正常|边界|异常)｜(.*?)｜(.*?)｜(.*?)｜(立项|测试补充|用户)｜(在用|已删除)(〔[^〕]*〕)?$')
CHANGE_LINE = re.compile(r'^- 改动 (\d+)｜(.*?)｜(修改|删除|新增|归类)｜(.*?)｜(.*?)｜(待确认|已同意|不同意)｜(.*?)｜(\{.*\})(〔[^〕]*〕)?$')


def parse_cases(sections):
    out = []
    for line in sections.get(CASE_HEAD, []):
        m = CASE_LINE.match(line)
        if m:
            out.append({'n': int(m[1]), 'check': m[2], 'name': m[3], 'cat': m[4], 'given': m[5], 'when': m[6], 'then': m[7],
                        'source': SOURCE_OF[m[8]], 'live': m[9] == '在用', 'cite': (m[10] or '')[1:-1]})
    return out


def parse_changes(sections):
    out = []
    for line in sections.get(CHANGE_HEAD, []):
        m = CHANGE_LINE.match(line)
        if not m:
            continue
        try:
            data = json.loads(m[8])
        except ValueError:
            data = {}
        out.append({'k': int(m[1]), 'target': m[2], 'op': OP_OF[m[3]], 'by': m[4], 'actor': next((k for k, v in ACTORS.items() if m[4].startswith(v)), ''),
                    'why': m[5], 'state': CSTATE_OF[m[6]], 'at': m[7], 'case': data.get('case'), 'check': data.get('check') or '',
                    'old': data.get('old'), 'new': data.get('new'), 'cite': (m[9] or '')[1:-1]})
    return out


def classes_of(sections):
    out = {}
    for line in sections.get('验收归类', []):
        m = CLASS_LINE.match(line)
        if m:
            out[m[2]] = {'kind': 'data' if m[1] == '数据可验' else 'ui', 'why': m[3]}
    return out


def coverage(sections):
    """[(验收条件序号, 原文)] that have neither a live test case nor the 只能人工看 class: what 确认立项 points out (it does not block)."""
    checks = live_items(sections.get('验收条件'))
    classes = classes_of(sections)
    covered = {c['check'] for c in parse_cases(sections) if c['live']}
    return [(i, text) for i, text in enumerate(checks, 1) if one(text) not in covered and (classes.get(one(text)) or {}).get('kind') != 'ui']


def cases_view(meta, sections):
    """What testing_view (and so the requirement list the app reads) carries about test cases."""
    cases = parse_cases(sections)
    changes = parse_changes(sections)
    checks = [one(t) for t in live_items(sections.get('验收条件'))]
    live = [c for c in cases if c['live']]
    return {
        'cases': cases, 'case_changes': changes, 'pending_case_changes': sum(1 for c in changes if c['state'] == 'pending'),
        'has_cases': bool(live), 'case_flow': bool(meta.get('case_flow')),
        'uncovered': [n for n, _ in coverage(sections)],
        'orphan_cases': [c['n'] for c in live if c['check'] not in checks],
        'supplements': sum(1 for c in live if c['source'] == 'test'),
    }


def usecase_ref(owner, n):
    return f'{owner}#{int(n)}'


def parse_ref(ref):
    m = re.fullmatch(r'(R-[\w-]+)#(\d+)', str(ref or ''))
    return (m[1], int(m[2])) if m else (None, None)


# ---------- 测试轮次和会话（testing） ----------

VERDICTS = {'ok': '可以验收', 'problems': '有问题', 'skipped': '跳过测试', 'manual': '手动放行'}
CLASS_LINE = re.compile(r'^- (数据可验|看界面)｜(.*?)｜(.*?)(〔[^〕]*〕)?$')
STEP_LINE = re.compile(r'^- (.*?)｜(.*?)｜(.*?)｜(.*?)(〔[^〕]*〕)?$')
ROUND_LINE = re.compile(r'^- 第 (\d+) 轮｜(.*?)｜(可以验收|有问题|跳过测试|手动放行)｜(.*?)｜(.*?)(〔[^〕]*〕)?$')
PROBLEM_LINE = re.compile(r'^- 第 (\d+) 轮 问题 (\d+)｜(.*?)｜(.*?)｜(.*?)(〔[^〕]*〕)?$')
FIXED_LINE = re.compile(r'^- 第 (\d+) 轮 修好了｜(.*?)｜(.*?)｜(.*?)(〔[^〕]*〕)?$')
UNLINKED_LINE = re.compile(r'^- 第 (\d+) 轮 没对接 用例 (\d+)｜(.*?)(〔[^〕]*〕)?$')
CASE_REF = re.compile(r'用例 ?(\d+)')


def one(value, cap=600):
    """One field of a line: one line, no field separator, no source brackets."""
    text = re.sub(r'\s+', ' ', str(value or '')).replace('｜', '，').replace('〔', '（').replace('〕', '）').strip()
    return text[:cap]


def parse_rounds(sections):
    rounds = {}
    for line in sections.get('测试结论', []):
        m = ROUND_LINE.match(line)
        if m:
            n = int(m[1])
            verdict = next(k for k, v in VERDICTS.items() if v == m[3])
            rounds[n] = dict(rounds.get(n) or {'problems': [], 'fixed': None}, n=n, at=m[2], verdict=verdict, by=m[4], summary=m[5])
            continue
        m = PROBLEM_LINE.match(line)
        if m:
            ref = CASE_REF.match(m[3])
            rounds.setdefault(int(m[1]), {'n': int(m[1]), 'problems': [], 'fixed': None})['problems'].append(
                {'k': int(m[2]), 'where': m[3], 'what': m[4], 'judge': m[5], **({'case': int(ref[1])} if ref else {})})
            continue
        m = UNLINKED_LINE.match(line)
        if m:
            rounds.setdefault(int(m[1]), {'n': int(m[1]), 'problems': [], 'fixed': None}).setdefault('unlinked', []).append({'case': int(m[2]), 'why': m[3]})
            continue
        m = FIXED_LINE.match(line)
        if m:
            rounds.setdefault(int(m[1]), {'n': int(m[1]), 'problems': [], 'fixed': None})['fixed'] = {'at': m[2], 'by': m[3], 'note': m[4]}
    return [r for _, r in sorted(rounds.items()) if r.get('verdict')]


def testing_view(meta, sections, stage=None):
    """What the panel, the app and the commands need about testing, computed from the requirement file."""
    checks = live_items(sections.get('验收条件'))
    classes, steps = {}, {}
    for line in sections.get('验收归类', []):
        m = CLASS_LINE.match(line)
        if m:
            classes[m[2]] = {'kind': 'data' if m[1] == '数据可验' else 'ui', 'why': m[3]}
    for line in sections.get('检查步骤', []):
        m = STEP_LINE.match(line)
        if m and not CLASS_LINE.match(line):
            steps.setdefault(m[1], []).append({'open': m[2], 'do': m[3], 'see': m[4]})
    marks = latest_marks(sections)
    conds = []
    cv = cases_view(meta, sections)
    for i, text in enumerate(checks, 1):
        c = classes.get(one(text)) or {}
        conds.append({'n': i, 'text': text, 'key': one(text), 'kind': c.get('kind'), 'why': c.get('why') or '', 'steps': steps.get(one(text), []), 'accept': marks.get(one(text)),
                      'cases': [x['n'] for x in cv['cases'] if x['live'] and x['check'] == one(text)]})
    rounds = parse_rounds(sections)
    last = rounds[-1] if rounds else None
    nxt = (last['n'] if last else 0) + 1
    kicked, told = int(meta.get('test_kicked') or 0), int(meta.get('test_told') or 0)
    sessions = [str(x) for x in meta.get('test_sessions') or []]
    return {
        'flow': bool(meta.get('test_flow')), 'checks': conds, 'classified': bool(conds) and all(c['kind'] for c in conds),
        'all_ui': bool(conds) and all(c['kind'] == 'ui' for c in conds), 'rounds': rounds, 'last': last, 'round': nxt,
        'sessions': sessions, 'kicked': kicked, 'told': told,
        'needs_kick': stage == 'test' and kicked < nxt,
        'to_tell': last['n'] if last and last['verdict'] in ('ok', 'problems') and last['n'] > told else 0,
        'skipped': any(r['verdict'] in ('skipped', 'manual') for r in rounds),
        'ever': bool(rounds or kicked or sessions),
        'accept_line': accept_line(conds, marks),
        **cv,
    }


def testing_line(view):
    """One line for the 完成记录, history and trace: how many rounds, how they went, whether testing was skipped."""
    rounds = view.get('rounds') or []
    if not rounds:
        return '没有经过测试阶段。' if view.get('flow') else ''
    parts = []
    for r in rounds:
        word = VERDICTS[r['verdict']]
        parts.append(f'第 {r["n"]} 轮{word}' + (f' {len(r["problems"])} 条' if r['verdict'] == 'problems' else ''))
    tested = sum(1 for r in rounds if r['verdict'] in ('ok', 'problems'))
    if not tested:
        return f'测试：跳过了测试（{"，".join(parts)}）。'
    return f'测试：{tested} 轮测试会话的结论（{"，".join(parts)}）' + ('；跳过了测试。' if view.get('skipped') else '。')


def session_kind(meta, sid=None):
    """What the current session is for this requirement: 'test' (one of its test sessions), 'develop' (its development session)
    or 'chat' (anything else: another requirement's session, a daily chat, a session without a valid token).
    With `sid` (a hook's input: Claude Code itself says which session it is), by the requirement's test_sessions: 'test' or 'develop'.
    Without it, by the session token the app signed (identity.caller), never by the bare session id in the environment."""
    tests = [str(x) for x in meta.get('test_sessions') or []]
    if sid is not None:
        return 'test' if sid and sid in tests else 'develop'
    me = caller()
    if not me['verified'] or me['owner'] != meta.get('id'):
        return 'chat'
    if me['kind'] == 'test':
        return 'test' if me['session'] in tests else 'chat'
    return 'develop' if me['kind'] == 'develop' else 'chat'


def req_session_kind(home, owner, sid=None):
    """The kind of the current session for a requirement id (None for fixes and the main line)."""
    if not owner or str(owner).startswith('F-'):
        return None
    path = requirement_path(home, owner)
    return session_kind(read_doc(path)[0], sid) if path else None


# ---------- 逐条验收（accept） ----------

ACCEPT_LINE = re.compile(r'^- (通过|这里不对)｜(.*?)｜(\d{4}-\d\d-\d\d \d\d:\d\d)｜(.*?)(?:｜(.*))?$')


def accept_marks(sections):
    """Every click, oldest first: {result: ok|bad, text, at, by, say}."""
    out = []
    for line in sections.get('人工验收', []):
        m = ACCEPT_LINE.match(line)
        if m:
            out.append({'result': 'ok' if m[1] == '通过' else 'bad', 'text': m[2], 'at': m[3], 'by': m[4], 'say': m[5] or ''})
    return out


def latest_marks(sections):
    """The last click per acceptance condition (by its text)."""
    last = {}
    for mark in accept_marks(sections):
        last[mark['text']] = dict(mark, times=(last.get(mark['text']) or {}).get('times', 0) + 1)
    return last


def accept_line(checks, marks):
    """One line for the 完成记录: which conditions you passed, which you said were wrong last, which you did not click."""
    if not checks:
        return ''
    ok, bad, none = [], [], []
    for c in checks:
        m = marks.get(c['key'])
        (ok if m and m['result'] == 'ok' else bad if m else none).append(str(c['n']))
    parts = []
    if ok:
        parts.append(f'第 {"、".join(ok)} 条你点了通过')
    if bad:
        parts.append(f'第 {"、".join(bad)} 条最后点的是「这里不对」')
    if none:
        parts.append(f'第 {"、".join(none)} 条没有点')
    return '人工验收：' + '；'.join(parts) + '。'
