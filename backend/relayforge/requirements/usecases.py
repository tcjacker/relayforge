"""测试用例（FORMAT.md「需求」的「测试用例」）：三个会话的协作分工。

- 立项会话出用例：用业务的话（前提、操作、预期），挂在验收条件下面，并给每条验收条件归类（能用数据验证 / 只能人工看）。
  不了解代码细节；每条验收条件要么至少有一条用例，要么标了「只能人工看」，不满足时确认立项只提示、不拦。
- 开发会话写代码，目标是让这些用例通过；它看得到用例，不能改。
- 测试会话把用例对接成测试数据（每条数据挂着它对应的用例编号）、执行、逐条用例报告结果；可以补充用例（标成「测试补充的」），
  不能减少或改动立项写的。
- 立项之后改用例一律要用户同意：任何会话要改、删立项写的用例或改归类，都记成一条「待确认的用例改动」（旧值、新值、谁提的、理由），
  用户在应用里同意或不同意（req case-ok / case-no，只由用户触发）。测试会话新增「测试补充的」用例不用同意，用户可以删。
  用户自己在应用里改（req case-save）不受限。

都写在需求文件里，由命令写，格式固定（一行一条，字段用「｜」隔开），排在「验收归类」之后：
  ## 测试用例    - 用例 3｜验收条件原文｜名称｜正常｜前提｜操作｜预期｜立项｜在用〔出处〕
                 来源是 立项、测试补充、用户；状态是 在用、已删除（删了的留着，编号不再用）。
  ## 用例改动    - 改动 2｜用例 3｜修改｜开发会话 S-…｜理由｜待确认｜2026-11-19 10:20｜{"case": 3, "old": {…}, "new": {…}}〔出处〕
                 动作是 修改、删除、新增、归类；状态是 待确认、已同意、不同意。最后一段 JSON 是程序读的旧值和新值。

谁在做只认应用签名的会话令牌（identity.py）：立项会话、开发会话、这个需求的测试会话；用户的命令另由 trust.py 校验。
"""

import json
import re

from ..core.common import commit_facts, main_checkout, today
from ..core.documents import add_line, live_items, read_doc, write_doc
from .testrecords import (
    ACTORS, CASE_HEAD, CASE_LINE, CHANGE_HEAD, CHANGE_LINE, CLASS_LINE, classes_of, CSTATES, one, OPS, parse_cases,
    parse_changes, SOURCES, testing_view,
)
from .testing import KIND_NAMES, KIND_WORDS, _cite, _cond, _lines, _not_verified_hint, _source, here_session_id, now, ordered
from ..core.identity import caller
from .owners import own_requirement
from .record import find_requirement
from ..sessions.record import stable_tag
from ..testsets.views import usecase_report
from ..core.trust import user_trusted

CATS = ('正常', '边界', '异常')
CAT_WORDS = {'正常': '正常', 'normal': '正常', '边界': '边界', 'edge': '边界', 'boundary': '边界', '异常': '异常', 'error': '异常', 'abnormal': '异常'}
FIELDS = (('name', '名称'), ('given', '前提'), ('when', '操作'), ('then', '预期'))
# 用例是业务的话：看起来在说代码（函数调用、文件名、snake_case / camelCase 的字段名）只提醒，不拦
CODE_WORDS = re.compile(r'\b[A-Za-z_]\w*\(\)|\b\w+\.(?:py|js|mjs|cjs|ts|tsx|java|go|rb|kt|json|ya?ml)\b|\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z]\w*\b')


# ---------- 读 ----------


# ---------- 谁在做、能不能直接改 ----------

def session_actor(meta):
    """'intake' | 'develop' | 'test' (by the verified session token, for this requirement), 'user' (the app's marker or a user command
    the command line checked), or None."""
    me = caller()
    if me['verified'] and me['owner'] == meta.get('id'):
        if me['kind'] in ('intake', 'develop'):
            return me['kind']
        if me['kind'] == 'test' and me['session'] in [str(x) for x in meta.get('test_sessions') or []]:
            return 'test'
    if user_trusted():
        return 'user'
    return None


def decide(meta, actor, op, case=None, classified=False, has_cases=False):
    """'direct' (takes effect now) or 'propose' (a 待确认的用例改动 for the user). Raises when `actor` may not touch test cases here."""
    state = meta.get('state') or 'open'
    if state not in ('draft', 'open'):
        raise ValueError('这个需求已经结束（完成或取消），测试用例不能再改。')
    if actor == 'user':
        return 'direct'
    if actor is None:
        raise ValueError('测试用例只有这个需求的立项会话、开发会话、测试会话能用命令写（应用开会话时给了它签名的会话令牌），当前会话不是。'
                         '你自己改用例请在应用的需求面板里改。' + _not_verified_hint())
    if state == 'draft':
        if actor == 'intake':
            return 'direct'
        raise ValueError('需求还在立项：测试用例由立项会话写。')
    if actor == 'test':
        if op == 'add':
            return 'direct'
        if op in ('edit', 'delete'):
            return 'direct' if case and case['source'] == 'test' else 'propose'
        if op == 'classify':  # 还没归类的、没有测试用例的需求（老需求）：测试会话照原来的做法自己归类；有用例的需求改已经归好的要用户同意
            return 'propose' if classified and has_cases else 'direct'
    return 'propose'


# ---------- 写 ----------

def clean_fields(raw, partial=False, where='这条用例'):
    out = {}
    for key, word in FIELDS:
        if key in raw and raw[key] is not None:
            out[key] = one(raw[key], 300)
            if not out[key]:
                raise ValueError(f'{where}的「{word}」不能是空的。')
        elif not partial:
            raise ValueError(f'{where}要写四样：名称（覆盖哪种情况）、前提、操作、预期（--name、--given、--when、--then），都用业务的话。'
                             f'没有特别的前提就写「无特别前提」。缺了「{word}」。')
    if raw.get('cat') is not None or not partial:
        cat = CAT_WORDS.get(str(raw.get('cat') or '正常').strip().lower())
        if not cat:
            raise ValueError('用例的类别只能是 正常、边界、异常。')
        out['cat'] = cat
    return out


def code_hints(fields):
    """Words in a case that look like code: a reminder that cases are written in business language (never a reason to refuse)."""
    found = []
    for key, _ in FIELDS:
        found += CODE_WORDS.findall(str(fields.get(key) or ''))
    return list(dict.fromkeys(found))


def case_line(c, cite=''):
    return (f'- 用例 {c["n"]}｜{c["check"]}｜{c["name"]}｜{c["cat"]}｜{c["given"]}｜{c["when"]}｜{c["then"]}｜{SOURCES[c["source"]]}｜'
            f'{"在用" if c["live"] else "已删除"}{_cite(cite)}')


def _next_case(meta, sections):
    return max([int(meta.get('case_next') or 1)] + [c['n'] + 1 for c in parse_cases(sections)])


def _put_case(meta, sections, case, cite):
    """Write `case` (new, or replacing the line with the same number)."""
    lines = _lines(sections, CASE_HEAD)
    line = case_line(case, cite)
    for i, old in enumerate(lines):
        m = CASE_LINE.match(old)
        if m and int(m[1]) == case['n']:
            lines[i] = line
            break
    else:
        lines.append(line)
        meta['case_next'] = case['n'] + 1


def _set_class(sections, key, kind, why, cite):
    lines = _lines(sections, '验收归类')
    lines[:] = [l for l in lines if not ((m := CLASS_LINE.match(l)) and m[2] == key)]
    lines.append(f'- {KIND_NAMES[kind]}｜{key}｜{one(why)}{_cite(cite)}')


def _who(home, actor):
    if actor == 'user':
        return '用户'
    sid = here_session_id()
    return ACTORS[actor] + (f' {stable_tag(home, sid)}' if sid else '')


def _propose(home, meta, sections, actor, op, target, why, payload, cite):
    why = one(why)
    if not why:
        raise ValueError('立项以后改测试用例要用户同意：加上 --why "为什么要改"（用例本身写错了、需求变了……），这条改动会等用户在应用里同意或不同意。')
    changes = parse_changes(sections)
    lines = _lines(sections, CHANGE_HEAD)
    who = _who(home, actor)
    for c in changes:  # 同一个会话种类对同一处还有一条没处理的：换成新的，不攒一串
        if c['state'] == 'pending' and c['target'] == target and c['op'] == op and c['actor'] == actor:
            k = c['k']
            lines[:] = [l for l in lines if not ((m := CHANGE_LINE.match(l)) and int(m[1]) == k)]
            break
    else:
        k = max([int(meta.get('case_change_next') or 1)] + [c['k'] + 1 for c in changes])  # 编号不重复使用
        meta['case_change_next'] = k + 1
    lines.append(f'- 改动 {k}｜{target}｜{OPS[op]}｜{who}｜{why}｜待确认｜{now()}｜{json.dumps(payload, ensure_ascii=False, sort_keys=True)}{_cite(cite)}')
    lines.sort(key=lambda l: int(m[1]) if (m := CHANGE_LINE.match(l)) else 0)
    add_line(sections, '变更记录', f'- {today()} {who}提出用例改动 {k}（{target}，{OPS[op]}），等用户确认：{why}')
    return k


def _save(home, path, meta, sections, message, commit):
    meta['updated'] = today()
    write_doc(path, meta, ordered(sections))
    if commit:
        commit_facts(home, message)


def _here(root):
    mine = own_requirement(root)
    if not mine:
        raise ValueError('这个目录不属于任何需求：这条命令要在需求的工作目录里运行。')
    return mine


def _by_id(root, ident):
    found = find_requirement(root, ident)
    home = main_checkout(root)
    path = home / found['file']
    meta, sections = read_doc(path)
    return path, meta, sections


def _case(sections, n, live=True):
    try:
        k = int(str(n).strip().lstrip('#').replace('用例', '').strip())
    except ValueError:
        raise ValueError(f'「{n}」不是用例编号：写 3（用例 3）。') from None
    found = next((c for c in parse_cases(sections) if c['n'] == k), None)
    if not found:
        raise ValueError(f'这个需求没有用例 {k}。')
    if live and not found['live']:
        raise ValueError(f'用例 {k} 已经删了。')
    return found


def _result(meta, sections, **extra):
    view = testing_view(meta, sections)
    return dict({'id': meta['id'], 'cases': view['cases'], 'case_changes': view['case_changes'], 'uncovered': view['uncovered'],
                 'checks': view['checks']}, **extra)


def _do_add(home, meta, sections, actor, check_n, fields, why, cite):
    checks = live_items(sections.get('验收条件'))
    if not checks:
        raise ValueError('这个需求还没有验收条件：先写验收条件，再在它下面写用例。')
    k, text = _cond(checks, check_n)
    key = one(text)
    f = clean_fields(fields)
    if any(c['live'] and c['check'] == key and c['name'] == f['name'] for c in parse_cases(sections)):
        raise ValueError(f'第 {k} 条验收条件下面已经有一条叫「{f["name"]}」的用例：一条用例一种情况，名称说清楚覆盖哪种情况。')
    how = decide(meta, actor, 'add')
    if how == 'propose':
        p = _propose(home, meta, sections, actor, 'add', f'第 {k} 条验收条件 新用例', why, {'check': key, 'new': dict(f, check=key)}, cite)
        return {'pending': True, 'change': p}
    case = dict(f, n=_next_case(meta, sections), check=key, source='test' if actor == 'test' else 'user' if actor == 'user' else 'intake', live=True)
    _put_case(meta, sections, case, cite)
    if actor in ('test', 'user') and meta.get('state') == 'open':
        add_line(sections, '变更记录', f'- {today()} {_who(home, actor)}{"补充" if actor == "test" else "加"}了用例 {case["n"]}「{case["name"]}」（第 {k} 条验收条件）。')
    return {'pending': False, 'case': case['n']}


def case_add(root, check_n, fields, why=None):
    """A new test case under acceptance condition `check_n`: written by the intake session (立项中), added as 测试补充 by the test session,
    or, after 立项 from the intake or development session, proposed to the user."""
    path, meta, sections = _here(root)
    actor = session_actor(meta)
    home = main_checkout(root)
    made = _do_add(home, meta, sections, actor, check_n, fields, why, _source(root))
    _save(home, path, meta, sections, f'测试用例：{meta.get("title")}', meta.get('state') == 'open')
    return _result(meta, sections, hints=code_hints(fields), **made)


def _do_edit(home, meta, sections, actor, n, fields, check_n, why, cite):
    case = _case(sections, n)
    new = dict(case, **clean_fields(fields, partial=True))
    if check_n is not None:
        new['check'] = one(_cond(live_items(sections.get('验收条件')), check_n)[1])
    keys = ('check', 'name', 'cat', 'given', 'when', 'then')
    if all(new[k] == case[k] for k in keys):
        raise ValueError(f'用例 {case["n"]} 没有变化。')
    if decide(meta, actor, 'edit', case) == 'propose':
        p = _propose(home, meta, sections, actor, 'edit', f'用例 {case["n"]}', why,
                     {'case': case['n'], 'old': {k: case[k] for k in keys}, 'new': {k: new[k] for k in keys}}, cite)
        return {'pending': True, 'change': p, 'case': case['n']}
    _put_case(meta, sections, new, cite or case['cite'])
    if actor in ('test', 'user') and meta.get('state') == 'open':
        add_line(sections, '变更记录', f'- {today()} {_who(home, actor)}改了用例 {case["n"]}「{new["name"]}」。')
    return {'pending': False, 'case': case['n']}


def case_edit(root, n, fields, check_n=None, why=None):
    path, meta, sections = _here(root)
    actor = session_actor(meta)
    home = main_checkout(root)
    made = _do_edit(home, meta, sections, actor, n, fields, check_n, why, _source(root))
    _save(home, path, meta, sections, f'测试用例：{meta.get("title")}', meta.get('state') == 'open')
    return _result(meta, sections, hints=code_hints(fields), **made)


def _do_remove(home, meta, sections, actor, n, why, cite):
    case = _case(sections, n)
    if decide(meta, actor, 'delete', case) == 'propose':
        p = _propose(home, meta, sections, actor, 'delete', f'用例 {case["n"]}', why,
                     {'case': case['n'], 'old': {k: case[k] for k in ('check', 'name', 'cat', 'given', 'when', 'then')}, 'new': None}, cite)
        return {'pending': True, 'change': p, 'case': case['n']}
    _put_case(meta, sections, dict(case, live=False), case['cite'])
    if actor in ('test', 'user') and meta.get('state') == 'open':
        add_line(sections, '变更记录', f'- {today()} {_who(home, actor)}删了用例 {case["n"]}「{case["name"]}」' + (f'（测试补充的）。' if case['source'] == 'test' else '。'))
    return {'pending': False, 'case': case['n']}


def case_remove(root, n, why=None):
    path, meta, sections = _here(root)
    actor = session_actor(meta)
    home = main_checkout(root)
    made = _do_remove(home, meta, sections, actor, n, why, _source(root))
    _save(home, path, meta, sections, f'测试用例：{meta.get("title")}', meta.get('state') == 'open')
    return _result(meta, sections, **made)


def _do_classify(home, meta, sections, actor, items, kind, why, cite):
    kind = KIND_WORDS.get(str(kind or '').strip().lower())
    if not kind:
        raise ValueError('归类只能是「数据」（能用数据验证：写用例、对接成测试数据）或「界面」（只能人工看：写清楚应该看到什么）。')
    why = one(why)
    if not why:
        raise ValueError('每条归类要写一句理由：--why "为什么这样归"。')
    checks = live_items(sections.get('验收条件'))
    if not checks:
        raise ValueError('这个需求还没有验收条件。')
    classes = classes_of(sections)
    has_cases = any(c['live'] for c in parse_cases(sections))
    done, proposed = [], []
    for n in items:
        k, text = _cond(checks, n)
        key = one(text)
        old = classes.get(key)
        how = decide(meta, actor, 'classify', classified=bool(old), has_cases=has_cases)
        if how == 'propose' and old and old['kind'] == kind:
            raise ValueError(f'第 {k} 条验收条件已经归为「{KIND_NAMES[kind]}」了。')
        if how == 'propose':
            proposed.append(_propose(home, meta, sections, actor, 'classify', f'第 {k} 条验收条件', why,
                                     {'check': key, 'old': old, 'new': {'kind': kind, 'why': why}}, cite))
            continue
        _set_class(sections, key, kind, why, cite)
        done.append(k)
    return done, proposed, kind


def req_classify(root, items, why, kind=None):
    """Sort acceptance conditions: data (can be checked with data: write test cases) or ui (only a person looking can tell).
    The intake session does it while 立项中; after 立项 a change is proposed to the user (FORMAT.md). A test session still classifies what nobody
    classified yet, and old requirements (立项 before test cases existed) the way it always did."""
    mine = own_requirement(root)
    if not mine:
        raise ValueError('这个目录不属于任何需求：这条命令要在需求的工作目录里运行。')
    path, meta, sections = mine
    actor = session_actor(meta)
    if actor == 'user' and (meta.get('state') or 'open') not in ('draft', 'open'):
        raise ValueError('这个需求已经结束，不能再归类。')
    home = main_checkout(root)
    done, proposed, kind = _do_classify(home, meta, sections, actor, items, kind, why, _source(root))
    _save(home, path, meta, sections, f'验收条件归类：{meta.get("title")}', meta.get('state') == 'open')
    out = _result(meta, sections, classified=done, proposed=proposed, kind=kind)
    out['left'] = [c['n'] for c in out['checks'] if not c['kind']]
    return out


# ---------- 用户的（只由用户在应用里触发：trust.py 的 USER_COMMANDS） ----------

def _user(trusted, what):
    if not user_trusted(trusted):
        raise ValueError(f'{what}：这个操作只能由你在应用里做，AI 的会话不能代替你做（没有应用的标记）。')


def case_save(root, ident, payload, trusted=None):
    """The user edits test cases in the app (never restricted): {"op": "add"|"edit"|"delete"|"classify", "case": n, "check": k,
    "name", "cat", "given", "when", "then", "kind", "why"}."""
    _user(trusted, '改测试用例')
    if not isinstance(payload, dict):
        raise ValueError('要给一个 JSON 对象：{"op": "add"|"edit"|"delete"|"classify", …}。')
    path, meta, sections = _by_id(root, ident)
    home = main_checkout(root)
    op = payload.get('op')
    fields = {k: payload[k] for k in ('name', 'cat', 'given', 'when', 'then') if k in payload}
    if op == 'add':
        made = _do_add(home, meta, sections, 'user', payload.get('check'), fields, None, '')
    elif op == 'edit':
        made = _do_edit(home, meta, sections, 'user', payload.get('case'), fields, payload.get('check'), None, '')
    elif op == 'delete':
        made = _do_remove(home, meta, sections, 'user', payload.get('case'), None, '')
    elif op == 'classify':
        done, _, _ = _do_classify(home, meta, sections, 'user', [payload.get('check')], payload.get('kind'), payload.get('why') or '用户在应用里归的类', '')
        made = {'classified': done}
    else:
        raise ValueError('op 只能是 add、edit、delete、classify。')
    if meta.get('state') == 'open' and op == 'classify':
        add_line(sections, '变更记录', f'- {today()} 用户把第 {payload.get("check")} 条验收条件归为「{KIND_NAMES[KIND_WORDS[str(payload.get("kind")).lower()]]}」。')
    _save(home, path, meta, sections, f'用户改测试用例：{meta.get("title")}', meta.get('state') == 'open')
    return _result(meta, sections, **made)


def _apply(meta, sections, change):
    op, new = change['op'], change.get('new') or {}
    if op == 'classify':
        _set_class(sections, change['check'], new['kind'], new['why'], change['cite'])
        return
    if op == 'add':
        checks = [one(t) for t in live_items(sections.get('验收条件'))]
        if new.get('check') not in checks:
            raise ValueError(f'改动 {change["k"]} 要加用例的那条验收条件已经不在了，没法同意；可以点「不同意」。')
        _put_case(meta, sections, {**{k: new[k] for k in ('check', 'name', 'cat', 'given', 'when', 'then')}, 'n': _next_case(meta, sections),
                                   'source': 'test' if change['actor'] == 'test' else 'intake', 'live': True}, change['cite'])
        return
    case = _case(sections, change['case'])
    if op == 'delete':
        _put_case(meta, sections, dict(case, live=False), case['cite'])
    else:
        _put_case(meta, sections, dict(case, **{k: new[k] for k in ('check', 'name', 'cat', 'given', 'when', 'then') if k in new}), change['cite'] or case['cite'])


def case_decide(root, ident, k, ok, trusted=None):
    """The user agrees (ok=True: it takes effect now) or not (the change is dropped; the line stays, marked 不同意)."""
    _user(trusted, '同意用例的改动' if ok else '不同意用例的改动')
    path, meta, sections = _by_id(root, ident)
    if (meta.get('state') or 'open') not in ('draft', 'open'):
        raise ValueError('这个需求已经结束。')
    try:
        k = int(str(k).lstrip('#').replace('改动', '').strip())
    except ValueError:
        raise ValueError(f'「{k}」不是改动编号。') from None
    change = next((c for c in parse_changes(sections) if c['k'] == k), None)
    if not change:
        raise ValueError(f'这个需求没有用例改动 {k}。')
    if change['state'] != 'pending':
        raise ValueError(f'用例改动 {k} 已经{CSTATES[change["state"]]}了。')
    if ok:
        _apply(meta, sections, change)
    lines = _lines(sections, CHANGE_HEAD)
    for i, line in enumerate(lines):
        m = CHANGE_LINE.match(line)
        if m and int(m[1]) == k:
            lines[i] = line.replace('｜待确认｜', '｜' + CSTATES['approved' if ok else 'rejected'] + '｜', 1)
    add_line(sections, '变更记录', f'- {today()} 用户{"同意" if ok else "不同意"}用例改动 {k}（{change["target"]}，{OPS[change["op"]]}，{change["by"]} 提的）'
             + ('，已生效。' if ok else '，丢弃。'))
    home = main_checkout(root)
    _save(home, path, meta, sections, f'用例改动 {k}：{"同意" if ok else "不同意"}', True)
    return _result(meta, sections, change=dict(change, state='approved' if ok else 'rejected'))


# ---------- 给开场白、命令行看的文字 ----------

def cases_text(view, with_links=None):
    """The requirement's acceptance conditions with their class and test cases, as plain lines (prompts, `req cases`)."""
    lines = []
    by_check = {}
    for c in view.get('cases') or []:
        if c['live']:
            by_check.setdefault(c['check'], []).append(c)
    for cond in view.get('checks') or []:
        kind = {'data': '能用数据验证', 'ui': '只能人工看'}.get(cond.get('kind'), '还没归类')
        lines.append(f'第 {cond["n"]} 条验收条件：{cond["text"]}（{kind}' + (f'：{cond["why"]}' if cond.get('why') else '') + '）')
        for c in by_check.get(cond['key'], []):
            tail = ''
            if with_links is not None:
                link = with_links.get(c['n']) or {}
                tail = f' —— {STATUS_WORDS.get(link.get("status"), "还没对接")}' + (f'（{"、".join(x["set"] + " #" + str(x["case"]) for x in link.get("links") or [])}）' if link.get('links') else '')
            lines.append(f'  - 用例 {c["n"]}「{c["name"]}」〔{c["cat"]}' + ('，测试补充的' if c['source'] == 'test' else '') + f'〕前提：{c["given"]}；操作：{c["when"]}；预期：{c["then"]}{tail}')
        if not by_check.get(cond['key']) and cond.get('kind') != 'ui':
            lines.append('  - （还没有用例）')
    return '\n'.join(lines)


STATUS_WORDS = {'pass': '都通过', 'fail': '有失败', 'broken': '跑不起来', 'unrun': '对接了还没跑', 'unlinked': '还没对接'}


def req_cases(root, ident=None):
    """`rf.py req cases`: the requirement's acceptance conditions → test cases → the test data they were joined to → the latest result."""
    found = find_requirement(root, ident)
    home = main_checkout(root)
    report = usecase_report(home, found['id'])
    report['text'] = cases_text(found['testing'], report['by_case'])
    return report
