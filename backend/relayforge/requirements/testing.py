"""测试阶段和测试会话（FORMAT.md 的「需求」）。

需求在「进行中」和「待验收」之间多一个「测试中」：开发会话把计划做完（或用 req retest 说明修好了）就进测试中，应用开出这个需求的
测试会话。测试会话没参与开发：立项会话写的测试用例（usecases.py）由它对接成测试数据、执行；归类（数据可验 / 看界面）立项时已经做了，
要看界面的由它写成人工验收的检查步骤，最后交回结论：可以验收了（进待验收），或者有问题（退回进行中，问题清单交给开发会话）。
没有测试用例的老需求照原来的做法：测试会话自己归类、按验收条件写测试集。用户可以跳过测试直接验收。

这三样都写在需求文件里，由命令写，格式固定（一行一条，字段用「｜」隔开）：
  ## 验收归类    - 数据可验｜验收条件原文｜理由〔出处〕          - 看界面｜验收条件原文｜理由〔出处〕
  ## 检查步骤    - 验收条件原文｜打开哪里｜做什么｜应该看到什么〔出处〕（一条验收条件可以有几步，按先后排）
  ## 测试结论    - 第 1 轮｜2026-11-19 10:20｜有问题｜S-…｜一句话〔出处〕
                 - 第 1 轮 问题 1｜哪条验收条件 / 哪个测试集的哪条数据｜现象｜判断
                 - 第 1 轮 修好了｜2026-11-19 11:02｜S-…｜修了什么〔出处〕
                 - 第 2 轮｜2026-11-19 11:30｜可以验收｜S-…｜总结〔出处〕
                 - 第 3 轮｜…｜跳过测试｜用户｜…    （或「手动放行」：测试中由用户直接改到待验收）
文件头另有：test_flow（立项时写 1：计划做完进测试中；没有它的老需求照原来的规则，计划做完进待验收）、test_sessions（测试会话）、
test_kicked（应用已经让测试会话开始第几轮）、test_told（第几轮的结论已经交给开发会话或提醒过用户）。

谁在做由应用发给每个会话的签名令牌来认（identity.py）：令牌里写着种类（测试会话、开发会话…）和属于哪个需求，签名由应用验证，
会话改不了；环境变量里裸的会话编号（RELAYFORGE_SESSION_ID）只用来记出处。没有令牌的会话（应用升级前开的、不是应用开的）按日常聊天算。
结束检查（钩子）里，会话编号由 Claude Code 自己交给钩子，对上需求文件头的 test_sessions 来认。
"""

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

from ..core.common import DIR, GUARD, SCRIPT, commit_facts, find_root, git, main_checkout, relayforge_home, today
from ..core.documents import add_line, live_items, read_doc, write_doc
from .testrecords import STEP_LINE, VERDICTS, one, session_kind, testing_view
from ..core.identity import REOPEN, caller
from .owners import own_requirement
from .record import find_requirement
from ..sessions.record import here_source, stable_tag
from ..testsets.checks import looks_like_test, turn_tests
from ..testsets.views import usecase_report
from ..testsets.store import BRIDGE_MARK, all_testsets
from ..core.trust import app_trusted, human_terminal, user_trusted

TEST_HEADS = ['验收归类', '检查步骤', '测试结论']
KIND_NAMES = {'data': '数据可验', 'ui': '看界面'}
KIND_WORDS = {'data': 'data', '数据': 'data', '数据可验': 'data', 'ui': 'ui', '界面': 'ui', '看界面': 'ui'}
VERDICT_WORDS = {'ok': 'ok', '可以验收': 'ok', '可以验收了': 'ok', 'problems': 'problems', '有问题': 'problems'}
CMD = f'python3 {SCRIPT} req'


def now():
    return f'{dt.datetime.now():%Y-%m-%d %H:%M}'


def ordered(sections):
    """The requirement's sections in their fixed order: the three test sections sit before 变更记录; others keep their place after."""
    order = ['需求说明', '要改的功能点', '验收条件', '验收归类', '测试用例', '用例改动', '检查步骤', '测试结论', '人工验收', '变更记录']
    out = {h: sections[h] for h in order if h in sections}
    out.update({h: v for h, v in sections.items() if h not in out})
    return out


def _lines(sections, head):
    lines = sections.setdefault(head, [])
    if lines and lines[0].startswith('- 尚未讨论'):
        lines.clear()
    return lines


# ---------- 读：需求文件里的三样 ----------


# ---------- 谁在做 ----------

def here_session_id():
    """The session this command runs in, for the record (who wrote a line). Taken from the verified session token when there is
    one, else from the environment; never used to decide what the caller may do (session_kind does that)."""
    me = caller()
    if me['verified'] and me['session']:
        return me['session']
    return os.environ.get('RELAYFORGE_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID') or ''


def _not_verified_hint():
    me = caller()
    return '' if me['verified'] else f'（{me["why"]}。{REOPEN}）'


def _mine(root):
    mine = own_requirement(root)
    if not mine:
        raise ValueError('这个目录不属于任何需求：这条命令要在需求的工作目录里运行。')
    path, meta, sections = mine
    if (meta.get('state') or 'open') != 'open':
        raise ValueError('这个需求不在开发里（还在立项，或已经完成、取消），没有测试阶段。')
    return path, meta, sections


def _stage(root, ident):
    return find_requirement(root, ident)['stage']


def _test_only(meta, what):
    if session_kind(meta) != 'test':
        raise ValueError(f'{what}只有这个需求的测试会话能做：应用开测试会话时给了它签名的会话令牌，当前会话不是。开发会话做完了用 {CMD} retest "修了什么" 交给测试。'
                         + _not_verified_hint())


def _source(root):
    try:
        return here_source(root) or ''
    except Exception:  # noqa: BLE001 - the source is a help, never a reason to fail
        return ''


def _cite(source):
    return f'〔{source}〕' if source else ''


def _cond(checks, n):
    try:
        k = int(str(n).strip().lstrip('#第').rstrip('条'))
    except ValueError:
        raise ValueError(f'「{n}」不是验收条件的序号：写 1、2、3（第几条验收条件）。') from None
    if not 0 < k <= len(checks):
        raise ValueError(f'这个需求只有 {len(checks)} 条验收条件，没有第 {k} 条。')
    return k, checks[k - 1]


def _write(home, path, meta, sections, message):
    meta['updated'] = today()
    write_doc(path, meta, ordered(sections))
    commit_facts(home, message)


# ---------- 测试会话用的 ----------

def req_steps(root, n, where=None, do=None, see=None, clear=False):
    """The test session writes the manual check steps for one acceptance condition (one step per call), or clears them."""
    path, meta, sections = _mine(root)
    _test_only(meta, '写人工验收的检查步骤')
    checks = live_items(sections.get('验收条件'))
    k, text = _cond(checks, n)
    key = one(text)
    lines = _lines(sections, '检查步骤')
    if clear:
        lines[:] = [l for l in lines if not ((m := STEP_LINE.match(l)) and m[1] == key)]
    else:
        parts = [one(x) for x in (where, do, see)]
        if not all(parts):
            raise ValueError('一步检查要写三样：打开哪里、做什么、应该看到什么。用法：req steps 2 "打开哪里" "做什么" "应该看到什么"')
        lines.append(f'- {key}｜{parts[0]}｜{parts[1]}｜{parts[2]}{_cite(_source(root))}')
    _write(main_checkout(root), path, meta, sections, f'测试会话写检查步骤：{meta.get("title")}')
    cond = next(c for c in testing_view(meta, sections)['checks'] if c['n'] == k)
    return {'id': meta['id'], 'check': cond}


def clean_problems(raw):
    if isinstance(raw, dict) and 'problems' in raw:
        raw = raw['problems']
    if not isinstance(raw, list):
        raise ValueError('问题清单要是一个 JSON 数组：[{"where": "哪条验收条件 / 哪个测试集的哪条数据", "what": "现象", "judge": "你的判断"}]')
    out = []
    for i, p in enumerate(raw, 1):
        if isinstance(p, str):
            parts = [x.strip() for x in p.split('｜')]
            p = dict(zip(('where', 'what', 'judge'), parts + [''] * 3))
        if not isinstance(p, dict):
            raise ValueError(f'第 {i} 条问题要写成 {{"where", "what", "judge"}}。')
        item = {k: one(p.get(k)) for k in ('where', 'what', 'judge')}
        if not item['where'] or not item['what']:
            raise ValueError(f'第 {i} 条问题要写清楚是哪条（where：第几条验收条件，或测试集编号和数据序号）和现象（what）。')
        item['judge'] = item['judge'] or '（没写判断）'
        out.append(item)
    return out


TEST_CMD = f'python3 {SCRIPT} test'


def clean_unlinked(raw):
    """{用例编号: 为什么对接不了} from "3｜原因" strings or [{"case", "why"}]."""
    out = {}
    for i, p in enumerate(raw or [], 1):
        if isinstance(p, str):
            n, _, why = p.partition('｜')
            p = {'case': n, 'why': why}
        try:
            n = int(str(p.get('case')).replace('用例', '').strip().lstrip('#'))
        except (ValueError, AttributeError):
            raise ValueError(f'第 {i} 条「没对接」要写成 "用例编号｜为什么对接不了"。') from None
        why = one(p.get('why'))
        if not why:
            raise ValueError(f'用例 {n} 没对接的原因不能是空的。')
        out[n] = why
    return out


def verdict_gaps(home, meta, view):
    """The 立项 test cases (written at 立项, or by the user) under conditions classified 数据可验 that are not joined to any test data, or joined
    but never run: they keep the test session from handing back 可以验收了 unless it says why for each. Requirements without test cases: none."""
    if not view.get('has_cases'):
        return []
    report = usecase_report(home, meta['id'])
    data = {c['key'] for c in view['checks'] if c['kind'] == 'data'}
    gaps = []
    for c in view['cases']:
        if not c['live'] or c['source'] == 'test' or c['check'] not in data:
            continue
        status = (report['by_case'].get(c['n']) or {}).get('status') or 'unlinked'
        if status in ('unlinked', 'unrun'):
            gaps.append({'n': c['n'], 'name': c['name'], 'status': status})
    return gaps


def req_verdict(root, verdict, summary='', problems=None, unlinked=None):
    """The test session hands back its conclusion for this round: ok (to 待验收) or problems (back to 进行中, for the development session)."""
    path, meta, sections = _mine(root)
    _test_only(meta, '交回测试结论')
    verdict = VERDICT_WORDS.get(str(verdict or '').strip().lower())
    if not verdict:
        raise ValueError('结论只能二选一：ok（可以验收了）或 problems（有问题）。')
    if _stage(root, meta['id']) != 'test':
        raise ValueError('这个需求现在不在测试中，不能交回测试结论。')
    view = testing_view(meta, sections, 'test')
    missing = [str(c['n']) for c in view['checks'] if not c['kind']]
    if missing:
        raise ValueError(f'先把每条验收条件归类再交回：第 {"、".join(missing)} 条还没归类（{CMD} classify 序号 数据|界面 --why "理由"）。')
    bare = [str(c['n']) for c in view['checks'] if c['kind'] == 'ui' and not c['steps']]
    if bare:
        raise ValueError(f'归为「看界面」的第 {"、".join(bare)} 条还没写检查步骤（{CMD} steps 序号 "打开哪里" "做什么" "应该看到什么"）。')
    summary = one(summary)
    reasons = clean_unlinked(unlinked or [])
    if verdict == 'ok':
        need = verdict_gaps(main_checkout(root), meta, view)
        left = [g for g in need if g['n'] not in reasons]
        if left:
            raise ValueError('交回「可以验收了」之前，立项写的、能用数据验证的每条用例都要至少对接一条测试数据并运行过：'
                             + '；'.join(f'用例 {g["n"]}「{g["name"]}」{"还没对接" if g["status"] == "unlinked" else "对接了还没跑"}' for g in left)
                             + f'。对接：{TEST_CMD} add 测试集编号 --usecase 用例编号 …，再 {TEST_CMD} run --req {meta["id"]}；'
                             + '实在对接不了的，逐条说明原因：--unlinked "用例编号｜为什么对接不了"。')
    items = clean_problems(problems or []) if verdict == 'problems' else []
    if verdict == 'problems' and not items:
        raise ValueError('交回「有问题」要逐条列出问题：--problem "哪条｜现象｜判断"（可以写几次），或 --file 问题.json。')
    if verdict == 'ok' and not summary:
        raise ValueError('交回「可以验收了」要附一句总结：--summary "测了什么、结果怎样"。')
    home = main_checkout(root)
    sid = here_session_id()
    n = view['round']
    source = _source(root)
    lines = _lines(sections, '测试结论')
    lines.append(f'- 第 {n} 轮｜{now()}｜{VERDICTS[verdict]}｜{stable_tag(home, sid) if sid else "测试会话"}｜{summary or f"{len(items)} 条问题"}{_cite(source)}')
    for k, p in enumerate(items, 1):
        lines.append(f'- 第 {n} 轮 问题 {k}｜{p["where"]}｜{p["what"]}｜{p["judge"]}')
    for k, why in sorted(reasons.items()):
        lines.append(f'- 第 {n} 轮 没对接 用例 {k}｜{why}')
    meta.update(manual_stage='check' if verdict == 'ok' else 'build', test_kicked=max(view['kicked'], n))
    add_line(sections, '变更记录', f'- {today()} 测试会话交回第 {n} 轮：' + (f'可以验收了（{summary}），进入待验收。' if verdict == 'ok'
                                                                       else f'有问题 {len(items)} 条，退回进行中，问题清单交给开发会话。'))
    _write(home, path, meta, sections, f'测试结论（第 {n} 轮{VERDICTS[verdict]}）：{meta.get("title")}')
    return {'id': meta['id'], 'round': n, 'verdict': verdict, 'summary': summary, 'problems': items, 'unlinked': reasons}


# ---------- 开发会话用的 ----------

def req_retest(root, note):
    """The development session says it fixed what testing found (or finished): back to 测试中 for another round."""
    path, meta, sections = _mine(root)
    kind = session_kind(meta)
    if kind == 'test':
        raise ValueError('这是测试会话：测试会话不改产品代码，发现问题用 req verdict problems 交回。req retest 是开发会话修好以后用的。')
    if kind != 'develop' and not (user_trusted() or human_terminal()):
        raise ValueError('req retest 是这个需求的开发会话修好以后用的：当前会话不是这个需求的开发会话。' + _not_verified_hint())
    note = one(note)
    if not note:
        raise ValueError(f'要写一句修了什么：{CMD} retest "修了什么"。')
    stage = _stage(root, meta['id'])
    if stage != 'build':
        raise ValueError('这个需求现在不在进行中' + ('（已经在测试中了）。' if stage == 'test' else '。'))
    home = main_checkout(root)
    view = testing_view(meta, sections)
    last = view['last']
    sid = here_session_id()
    asked = [c for c in view.get('case_changes') or [] if c['state'] == 'pending' and c['actor'] == 'develop']
    if asked:  # 开发会话认为是用例写错了：它提的改动在等用户确认，再测时一起说清楚
        note = one(note + '（另外提了 ' + '、'.join(f'用例改动 {c["k"]}（{c["target"]}，{c["why"]}）' for c in asked) + '，等用户确认）')
    if last and last['verdict'] == 'problems' and not last.get('fixed'):
        _lines(sections, '测试结论').append(f'- 第 {last["n"]} 轮 修好了｜{now()}｜{stable_tag(home, sid) if sid else "开发会话"}｜{note}{_cite(_source(root))}')
    meta.update(manual_stage='test', test_flow=1)
    add_line(sections, '变更记录', f'- {today()} 开发会话说修好了，再进测试中：{note}')
    _write(home, path, meta, sections, f'修好了，再测：{meta.get("title")}')
    return {'id': meta['id'], 'round': view['round'], 'note': note}


# ---------- 用户用的（只由应用触发：程序校验应用的标记） ----------

def _user_req(root, ident, what, trusted=None):
    if not user_trusted(trusted):
        raise ValueError(f'{what}：这个操作只能由你在应用里做，AI 的会话不能代替你做（没有应用的标记）。')
    found = find_requirement(root, ident)
    if found['state'] != 'open' or not found['worktree']:
        raise ValueError('只有开发中的需求（有自己的工作目录）能' + what + '。')
    home = main_checkout(root)
    path = home / found['file']
    meta, sections = read_doc(path)
    return found, home, path, meta, sections


def req_to_test(root, ident, trusted=None):
    """The user hands a requirement in 进行中 (or 待验收) to testing."""
    found, home, path, meta, sections = _user_req(root, ident, '交给测试', trusted)
    if found['stage'] == 'test':
        raise ValueError('这个需求已经在测试中了。')
    meta.update(manual_stage='test', test_flow=1)
    add_line(sections, '变更记录', f'- {today()} 用户把需求交给测试（从{"进行中" if found["stage"] == "build" else "待验收"}），进入测试中。')
    _write(home, path, meta, sections, f'交给测试：{found["title"]}')
    return find_requirement(home, found['id'])


def req_skip_test(root, ident, how='skipped', trusted=None):
    """The user skips testing (from 进行中 or 测试中) and goes straight to 待验收; the record says so plainly."""
    found, home, path, meta, sections = _user_req(root, ident, '跳过测试', trusted)
    if found['stage'] not in ('build', 'test'):
        raise ValueError('只有进行中或测试中的需求能跳过测试。')
    view = testing_view(meta, sections)
    n = view['round']
    said = '用户点了「跳过测试，直接验收」' if how == 'skipped' else '测试中由用户直接改到待验收'
    _lines(sections, '测试结论').append(f'- 第 {n} 轮｜{now()}｜{VERDICTS["manual" if how == "manual" else "skipped"]}｜用户｜{said}')
    meta.update(manual_stage='check', test_kicked=max(view['kicked'], n), test_told=max(view['told'], n))
    add_line(sections, '变更记录', f'- {today()} 用户跳过了测试，' + ('从进行中' if found['stage'] == 'build' else '从测试中') + '直接进入待验收' + ('（手动放行）。' if how == 'manual' else '。'))
    _write(home, path, meta, sections, f'跳过测试：{found["title"]}')
    return find_requirement(home, found['id'])


def req_test_mark(root, ident, field, n, trusted=None):
    """The app's bookkeeping: it started the test session on round n (kicked), or passed round n's conclusion on (told)."""
    if not (app_trusted() if trusted is None else trusted):
        raise ValueError('这条命令只由应用自己用（没有应用的标记）。')
    found = find_requirement(root, ident)
    home = main_checkout(root)
    path = home / found['file']
    meta, sections = read_doc(path)
    key = {'kicked': 'test_kicked', 'told': 'test_told'}[field]
    meta[key] = max(int(meta.get(key) or 0), int(n))
    meta['updated'] = today()
    write_doc(path, meta, sections)
    return {'id': found['id'], key: meta[key]}


# ---------- 结束检查：测试会话不改产品代码 ----------

TEST_DIR = re.compile(r'(^|/)(tests?|__tests__|specs?|testdata|fixtures|src/test)/')


def _turn_path(sid):
    return relayforge_home() / 'turns' / (re.sub(r'[^0-9A-Za-z_-]', '_', sid) + '.json')


def _dirty(place):
    """{path: content digest or 'gone'} for every changed or new file in the working directory (outside .relayforge/)."""
    out = {}
    for line in git(place, 'status', '--porcelain', '-uall').splitlines():
        rel = line[3:].split(' -> ')[-1].strip('"')
        if not rel or rel.startswith(DIR + '/'):
            continue
        p = Path(place) / rel
        try:
            out[rel] = hashlib.sha1(p.read_bytes()).hexdigest() if p.is_file() else 'gone'
        except OSError:
            out[rel] = 'gone'
    return out


def _test_owner(data):
    """(working directory, requirement meta) when the hook's session is a requirement's test session, else None."""
    sid = str(data.get('session_id') or '')
    root = find_root(data.get('cwd'))
    if not sid or not root:
        return None
    place = Path(root).resolve()
    home = main_checkout(place)
    if place == home:
        return None
    mine = own_requirement(place)
    if not mine or session_kind(mine[1], sid) != 'test':
        return None
    return place, home, mine[1]


def turn_snapshot(data):
    """UserPromptSubmit of a test session: remember what the working directory looked like when the turn began."""
    if os.environ.get(GUARD):
        return
    found = _test_owner(data)
    if not found:
        return
    place = found[0]
    path = _turn_path(str(data['session_id']))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'head': git(place, 'rev-parse', 'HEAD').strip(), 'dirty': _dirty(place), 'at': now()}), encoding='utf-8')


def allowed_for_test(place, rel, entries):
    rel = rel.replace('\\', '/')
    if rel.startswith(DIR + '/') or rel in entries or TEST_DIR.search(rel):
        return True
    if looks_like_test(rel):
        return True
    try:
        return BRIDGE_MARK in (Path(place) / rel).read_text(encoding='utf-8', errors='replace')[:400]
    except OSError:
        return False


def turn_changes(data, place):
    """Files this turn changed and that are still changed: compared with the snapshot taken when the turn began (the working
    directory and the commits), plus files written with the edit tools that are still different from the last commit.
    Changing a file and putting it back within the turn does not count; neither does undoing a change left from before."""
    try:
        snap = json.loads(_turn_path(str(data.get('session_id') or '')).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        snap = {}
    before, after = snap.get('dirty') or {}, _dirty(place)
    changed = [rel for rel, digest in after.items() if snap and before.get(rel) != digest]
    head = snap.get('head')
    if head and head != git(place, 'rev-parse', 'HEAD').strip():
        changed += [n for n in git(place, 'diff', '--name-only', f'{head}..HEAD').splitlines() if n]
    written, _, _ = turn_tests(data.get('transcript_path') or '', place)
    changed += [rel for rel in written if rel in after and before.get(rel) != after[rel]]
    return list(dict.fromkeys(c for c in changed if c and not c.startswith(DIR + '/')))


def test_session_stop_block(data):
    """A test session that changed product code this turn must not stop: undo it and hand the problem back instead."""
    if data.get('stop_hook_active') or os.environ.get(GUARD):
        return None
    found = _test_owner(data)
    if not found:
        return None
    place, home, _ = found
    entries = {str(ts.get('entry') or '') for ts in all_testsets(home, shelved=True)}
    hits = [rel for rel in turn_changes(data, place) if not allowed_for_test(place, rel, entries)]
    if not hits:
        return None
    return (f'测试会话不改产品代码：这一轮改了 {"、".join(hits[:6])}{"等" if len(hits) > 6 else ""}，它们既不是测试集的接入代码、桥接文件，也不在测试目录下。'
            f'先把这些改动撤回（还没提交的用 git restore 文件 或 git checkout -- 文件，新建的文件删掉；已经提交的用 git revert），'
            f'然后把你发现的问题交回开发会话去改：{CMD} verdict problems --problem "哪条验收条件或哪个测试集的哪条数据｜现象｜你的判断"。')
