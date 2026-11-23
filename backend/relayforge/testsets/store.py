"""测试集：一份测试数据（JSON）加一段只负责读数据和调用的接入代码；程序执行、程序比对、程序记结果。

  数据    .relayforge/tests/TS-001.json          进 Git。编号、名称、功能点、来源需求或修复、验收条件、状态、语言、接入代码、cases、待确认的改动、变更记录
  计数    .relayforge/tests/stats/TS-001.json    进 Git。跑过几次、失败过几次、最近几次的结果（算提示用），以及 AI 写的分析
  结果    .relayforge/tests/results/TS-001/范围.json   不进 Git。最近一次的完整结果：每条的输入、期望、实际输出、差异、耗时
          范围是 main（主线）或需求、修复的编号：同一个测试集在主线上和在某个需求里跑，各留各的最近一次。

进行中（见 FORMAT.md 的「测试集」）：
- 还没确认完成的需求（或修复）新建的测试集，文件头 in_progress 写它的编号；给已经落定的测试集新加的数据，那一条带 in_progress。
  确认完成时去掉，放弃时整个测试集移到 history/tests/（dropped_with），新加的那几条标 dropped_with，恢复时拿回来。
- AI 改动或删除已经落定的数据不直接生效：记成 changes 里的一条（旧值、新值、理由、谁提的），用户同意或不同意（只由用户触发）。
  同意了的改动在那个需求眼里生效，需求确认完成时落定；提出它的需求已经完成（或者是主线上提的）时，同意就直接改。
- 跑某个需求的测试集，用的是「这个需求眼里的数据」：落定的、它自己进行中新加的、它提出的还没被否决的改动。
- 用户（应用）直接保存数据不用确认。功能点不再是现行的（移到 history/ 或被替代），它名下的测试集算过期，不再运行。

构建命令（Java 测试集的 build，每次运行前先执行）和测试环境的准备步骤一样：第一次执行、或命令 / 执行目录变了以后，要用户在应用里
看过实际的命令和目录、点同意（rf.py test approve-build，只认应用的标记）才执行。同意记在本机（和准备步骤的同意在同一个文件里），
按命令加目录的指纹（build_print）。没同意时这个测试集「跑不起来」，原因写明「构建命令还没经你同意」；AI 的会话运行时也一样不执行。
"""

import contextlib
import copy
import datetime as dt
import json
import os
import re
from pathlib import Path
try:
    import fcntl
except ImportError:  # not on this platform; writes are then not serialised between processes
    fcntl = None

from ..core.common import DIR, git, main_checkout, today
from ..requirements.owners import here_owner, owner_features, owner_going, requirement_path
from ..core.documents import read_doc
from ..core.trust import terminal_note
from ..sessions.record import here_source
from ..requirements.testrecords import parse_cases, parse_ref, usecase_ref

__all__ = ['TEST_CATS', 'run_text', 'compare', 'BRIDGE_MARK', 'all_testsets']

TESTS = f'{DIR}/tests'
RESULTS = f'{TESTS}/results/'
TEST_CATS = ('正常', '边界', '异常')
CAT_WORDS = {'正常': '正常', 'normal': '正常', '边界': '边界', 'edge': '边界', 'boundary': '边界', '异常': '异常', 'error': '异常', 'abnormal': '异常'}
LANGS = ('python', 'node', 'java')
ENTRY_LANGS = {'.py': 'python', '.js': 'node', '.mjs': 'node', '.cjs': 'node', '.java': 'java'}
TIMEOUT = 120          # 一个测试集最多跑多少秒
RECENT = 10            # 计数里留最近几次的结果
NEVER_FAILED = 20      # 跑过这么多次一次没失败，提示「从没失败过」
OUTPUT_CAP = 100000    # 结果里一条实际输出最多存多少字，超出的截断（比对用的是完整的）
DIFF_CAP = 20
BRIDGE_MARK = 'relayforge:testset-bridge'
STATES = {'current': '在用', 'stale': '过期'}
RESULT_WORDS = {'pass': '通过', 'fail': '失败', 'broken': '跑不起来'}


# ---------- 位置、锁、读写 ----------

def tests_dir(home):
    return Path(home) / TESTS


def set_path(home, ident):
    return tests_dir(home) / f'{ident}.json'


def stats_path(home, ident):
    return tests_dir(home) / 'stats' / f'{ident}.json'


def result_path(home, ident, scope):
    return tests_dir(home) / 'results' / ident / f'{scope or "main"}.json'


def full_path(home):
    """The summary of the last full run on the main line (test run --all in the project directory). Not in Git, like results."""
    return tests_dir(home) / 'results' / '_full.json'


def full_status(home):
    """How fresh the last full run is. The merge gate (testsets/gate.py) only runs a chosen part before each merge and leaves
    the rest to a full run; this says when that last happened and on which commit:
    {last: {at, commit, totals, failing} | None, head, behind: commits merged since (None when unknown), stale: bool}."""
    home = main_checkout(home)
    last = _load(full_path(home))
    head = git(home, 'rev-parse', 'HEAD').strip()
    behind = None
    if last and last.get('commit'):
        count = git(home, 'rev-list', '--count', '--first-parent', f'{last["commit"]}..HEAD').strip()
        behind = int(count) if count.isdigit() else None
    return {'last': last, 'head': head, 'behind': behind, 'stale': not last or last.get('commit') != head}


def shelf_dir(home):
    return Path(home) / DIR / 'history' / 'tests'


@contextlib.contextmanager
def tests_lock(home):
    """One writer at a time for the test set files of a repository; the lock sits in the shared .git."""
    common = git(home, 'rev-parse', '--git-common-dir').strip()
    place = (Path(home) / common).resolve() if common else tests_dir(home)
    place.mkdir(parents=True, exist_ok=True)
    handle = open(place / 'relayforge-tests.lock', 'a')
    try:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()


def _load(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def norm_id(ident):
    text = str(ident or '').strip().upper()
    m = re.fullmatch(r'(?:TS-)?0*(\d+)', text)
    if not m:
        raise ValueError(f'「{ident}」不是测试集编号，写成 TS-001。')
    return f'TS-{int(m.group(1)):03d}'


def all_testsets(home, shelved=False):
    """Every test set in tests/ (and, with shelved, the ones a cancelled requirement put away in history/tests/)."""
    out = []
    for folder in [tests_dir(home)] + ([shelf_dir(home)] if shelved else []):
        for path in sorted(folder.glob('TS-*.json')):
            data = _load(path)
            if data and data.get('id') == path.stem:
                data['_shelved'] = folder != tests_dir(home)
                out.append(data)
    return out


def read_set(home, ident):
    ident = norm_id(ident)
    data = _load(set_path(home, ident))
    if not data:
        raise ValueError(f'没有找到测试集 {ident}。' + ('它随取消的需求移到了 history/tests/。' if (shelf_dir(home) / f'{ident}.json').exists() else ''))
    return data


def write_set(home, ts):
    ts = {k: v for k, v in ts.items() if not k.startswith('_')}
    ts['updated'] = today()
    _dump(set_path(home, ts['id']), ts)


def read_stats(home, ident):
    return _load(stats_path(home, ident)) or {'id': ident, 'runs': 0, 'failed': 0, 'broken': 0, 'recent': [], 'analysis': {}}


def stamp():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log_line(ts, text, source=''):
    ts.setdefault('log', []).append(terminal_note(f'{today()} {text}' + (f'〔{source}〕' if source else '')))


def _source(root, session=None, transcript=None):
    try:
        return here_source(root, session, transcript) or ''
    except Exception:  # noqa: BLE001 - the source is a help, never a reason to fail
        return ''


# ---------- 功能点表、测试集为什么过期 ----------


def feature_table(home):
    """{feature id: {title, ok, in_progress}}: ok is False when it left features/ (history/) or was superseded."""
    out = {}
    for folder in ('history', 'features'):
        for path in sorted((Path(home) / DIR / folder).glob('RF-*.md')):
            meta = read_doc(path)[0]
            if meta.get('id') and (folder == 'features' or meta.get('type') == 'feature'):
                out[str(meta['id'])] = {'title': str(meta.get('title') or ''), 'ok': folder == 'features' and meta.get('status') != 'superseded',
                                        'in_progress': meta.get('in_progress')}
    return out


def stale_reason(ts, features):
    """Why a test set no longer runs, or ''. Marked by the user, or its feature went offline or was replaced."""
    if ts.get('status') == 'stale':
        return ts.get('stale_reason') or '用户标成了过期'
    f = features.get(str(ts.get('feature') or ''))
    if not f:
        return f'功能点 {ts.get("feature")} 不存在了'
    if not f['ok']:
        return f'功能点 {ts.get("feature")} {f["title"]} 已下线或被替代'
    return ''


# ---------- 数据：一条、去重、某个范围眼里的数据 ----------

def _canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def clean_case(raw, where='这一条'):
    """Check one case as given by the AI or the user; returns the fields that are stored."""
    if not isinstance(raw, dict):
        raise ValueError(f'{where}不是一个 JSON 对象。')
    name = ' '.join(str(raw.get('name') or '').split())
    if not name:
        raise ValueError(f'{where}没有 name：每条数据都要写明它覆盖哪种情况。')
    if 'input' not in raw:
        raise ValueError(f'{where}（{name}）没有 input。')
    if 'expect' not in raw and not raw.get('raises'):
        raise ValueError(f'{where}（{name}）没有 expect（期望输出）；期望它报错的写 raises。')
    cat = CAT_WORDS.get(str(raw.get('cat') or '正常').strip().lower())
    if not cat:
        raise ValueError(f'{where}（{name}）的 cat 只能是 正常、边界、异常。')
    out = {'name': name, 'cat': cat, 'input': raw['input']}
    if raw.get('raises'):
        out['raises'] = str(raw['raises'])
    else:
        out['expect'] = raw['expect']
    if raw.get('usecase') not in (None, ''):  # 对应的测试用例（需求文件里「测试用例」的编号）；_link_usecase 再核对、写成「需求编号#用例编号」
        out['usecase'] = raw['usecase']
    return out


def _link_usecase(home, owner, case, required, where='这一条'):
    """Check and normalise case['usecase'] to "R-…#n" (a live test case of the requirement `owner`). In a requirement that has test cases,
    the test session must say which one each piece of data joins (required); requirements without test cases keep the old way."""
    raw = case.get('usecase')
    if raw in (None, ''):
        if required:
            raise ValueError(f'{where}（{case.get("name")}）没有挂用例：这个需求有立项写的测试用例，每条测试数据都要挂上它对应的那条用例（--usecase 用例编号，'
                             '或数据里写 "usecase": 3）。立项没想到的情况，先用 req case-add 补一条用例（测试补充的），再对接。')
        case.pop('usecase', None)
        return case
    if not owner or str(owner).startswith('F-'):
        raise ValueError(f'{where}挂了用例 {raw}：只有需求的测试数据挂用例，这里不属于任何需求。')
    req, n = parse_ref(raw)
    if req is None:
        try:
            req, n = owner, int(str(raw).replace('用例', '').strip().lstrip('#'))
        except ValueError:
            raise ValueError(f'{where}的用例编号「{raw}」看不懂：写 3（这个需求的用例 3）。') from None
    if req != owner:
        raise ValueError(f'{where}挂的用例 {raw} 是需求 {req} 的，不是这个需求（{owner}）的。')
    path = requirement_path(home, owner)
    cases = {c['n']: c for c in parse_cases(read_doc(path)[1])} if path else {}
    if n not in cases:
        raise ValueError(f'{where}挂的用例 {n} 在需求 {owner} 里不存在（用 req cases 看有哪些）。')
    if not cases[n]['live']:
        raise ValueError(f'{where}挂的用例 {n} 已经删了。')
    case['usecase'] = usecase_ref(owner, n)
    return case


def _needs_usecase(home, owner, made_in):
    """The test session of a requirement with test cases must join every piece of data to one."""
    if made_in != 'test' or not owner or str(owner).startswith('F-'):
        return False
    path = requirement_path(home, owner)
    return bool(path) and any(c['live'] for c in parse_cases(read_doc(path)[1]))


def _key(case):
    return _canon(case.get('input'))


def _same_data(a, b):
    return _key(a) == _key(b) and _canon(a.get('expect')) == _canon(b.get('expect')) and (a.get('raises') or '') == (b.get('raises') or '')


def live(case):
    return not case.get('dropped_with')


def view(ts, scope):
    """The cases as `scope` (a requirement or fix id, or None for the main line) sees them: landed ones, its own new ones,
    and its pending or approved changes applied. Each copy carries `changed` (change id) when a change of scope applies."""
    pending = {}
    for c in ts.get('changes') or []:
        if scope and c.get('by') == scope and c.get('state') in ('pending', 'approved'):
            pending[c['case']] = c
    out = []
    for case in ts.get('cases') or []:
        if not live(case) or case.get('in_progress') not in (None, scope):
            continue
        c = copy.deepcopy(case)
        change = pending.get(case['n'])
        if change:
            if change['op'] == 'delete':
                continue
            for k in ('expect', 'raises'):
                c.pop(k, None)
            c.update(copy.deepcopy(change['new']))
            c['changed'] = change['id']
        out.append(c)
    return out


def set_visible(ts, scope):
    return not ts.get('_shelved') and ts.get('in_progress') in (None, scope)


def find_duplicate(case, others):
    """('same' | 'conflict', other case) when `case` has exactly the input of another one, else None."""
    for o in others:
        if _key(o) == _key(case):
            return ('same' if _same_data(o, case) else 'conflict'), o
    return None


def _dup_text(kind, other):
    return f'和 #{other["n"]}「{other["name"]}」' + ('完全相同' if kind == 'same' else '的输入相同、期望不同（互相矛盾）')


def _landed_case(ts, case):
    """A case that is landed: neither it nor its set is in progress."""
    return not ts.get('in_progress') and not case.get('in_progress')


# ---------- 新建、加、改、删（AI 用的） ----------


def _find_case(ts, n):
    try:
        n = int(str(n).lstrip('#'))
    except ValueError:
        raise ValueError(f'「{n}」不是数据的序号，写成 3 或 #3。') from None
    case = next((c for c in ts.get('cases') or [] if c['n'] == n and live(c)), None)
    if not case:
        raise ValueError(f'{ts["id"]} 里没有第 #{n} 条数据。')
    return case


# ---------- 用户的决定（只由用户在应用里触发） ----------

def _apply_change(ts, change):
    case = next((c for c in ts['cases'] if c['n'] == change['case'] and live(c)), None)
    if case is None:
        return
    if change['op'] == 'delete':
        ts['cases'] = [c for c in ts['cases'] if c is not case]
    else:
        for k in ('expect', 'raises'):
            case.pop(k, None)
        case.update(copy.deepcopy(change['new']), edited=today())


# ---------- 比对：期望和实际，差在哪里 ----------

KIND_WORDS = {'null': '空值 null', 'bool': '布尔值', 'number': '数字', 'string': '文字', 'array': '列表', 'object': '对象'}


def _json_kind(v):
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'bool'
    if isinstance(v, (int, float)):
        return 'number'
    if isinstance(v, str):
        return 'string'
    if isinstance(v, list):
        return 'array'
    if isinstance(v, dict):
        return 'object'
    return type(v).__name__


def path_words(path):
    """A path into the output in plain words: () -> 返回值, (1,) -> 返回值的第 2 项, ('items', 0, 'amount') -> 返回值的 items 的第 1 项的 amount."""
    words = '返回值'
    for p in path:
        words = _then(words, f'的第 {p + 1} 项' if isinstance(p, int) else f'的 {p}')
    return words


def _then(words, more):
    """Put a space between a field name (ASCII) and the Chinese that follows it."""
    return words + (' ' if words[-1:].isascii() and words[-1:].isalnum() or words[-1:] in '_-' else '') + more


def compare(expect, actual, path=(), out=None):
    """Deep equality as JSON sees it (true is not 1; 1 and 1.0 are the same number). Returns the differences found, each
    one plain sentence: 类型不同、缺少字段、多出字段、数量不同、值不同, with where it is (返回值的第 2 项的 amount)."""
    out = [] if out is None else out
    if len(out) >= DIFF_CAP:
        return out
    ke, ka = _json_kind(expect), _json_kind(actual)
    show = lambda v: _short(_canon(v), 120)  # noqa: E731
    where = path_words(path)
    if ke != ka:
        out.append(f'{where}：类型不同，期望是{KIND_WORDS.get(ke, ke)} {show(expect)}，实际是{KIND_WORDS.get(ka, ka)} {show(actual)}'
                   if ke != 'null' and ka != 'null' else f'{where}：期望 {show(expect)}，实际 {show(actual)}')
    elif ke == 'array':
        if len(expect) != len(actual):
            out.append(f'{where}：数量不同，期望 {len(expect)} 项，实际 {len(actual)} 项')
        for i, (a, b) in enumerate(zip(expect, actual)):
            compare(a, b, (*path, i), out)
    elif ke == 'object':
        for key in expect:
            if key not in actual:
                out.append(_then(where, f'缺少字段 {key}（期望是 {show(expect[key])}）'))
            else:
                compare(expect[key], actual[key], (*path, key), out)
        for key in actual:
            if key not in expect:
                out.append(_then(where, f'多出字段 {key}（实际是 {show(actual[key])}）'))
    elif expect != actual:
        out.append(f'{where}：值不同，期望 {show(expect)}，实际 {show(actual)}')
    return out[:DIFF_CAP]


def _short(text, cap):
    text = str(text)
    return text if len(text) <= cap else text[:cap - 1] + '…'


# ---------- 判定一条、选出要跑的测试集、结果的文字 ----------


def judge(case, answer):
    """(status, actual, error, diff) for one case from its driver answer: the program compares, never the access code."""
    if answer.get('ok'):
        actual = answer.get('output')
        if case.get('raises'):
            return 'fail', actual, None, [f'期望报错（含「{case["raises"]}」），实际正常返回 {_short(_canon(actual), 120)}']
        diff = compare(case.get('expect'), actual)
        return ('pass' if not diff else 'fail'), actual, None, diff
    error = str(answer.get('error') or '出错了')
    if case.get('raises') and case['raises'] in error:
        return 'pass', None, error, []
    return 'fail', None, error, ([f'期望报错（含「{case["raises"]}」），实际报的是：{_short(error, 200)}'] if case.get('raises') else ['这一条抛了异常：' + _short(error, 300)])


def select(home, scope=None, ids=(), feature=None, everything=False, owner=None):
    """The test sets a run (or a list) covers, as `scope` sees them: [(set, origin)] where origin is 'new' (the scope's own,
    in progress) or 'existing'; and the ones left out [(id, why)]. `owner` picks a requirement's or fix's sets when it is
    no longer in progress (its sets are landed, seen as the main line sees them)."""
    owner = owner or scope
    features = feature_table(home)
    picked, skipped = [], []
    sets = {ts['id']: ts for ts in all_testsets(home)}
    if ids:
        chosen = []
        for raw in ids:
            ident = norm_id(raw)
            if ident not in sets:
                raise ValueError(f'没有找到测试集 {ident}。')
            chosen.append(sets[ident])
    elif feature:
        chosen = [ts for ts in sets.values() if ts.get('feature') == feature.upper()]
    elif owner and not everything:
        touched = set(owner_features(home, owner))
        chosen = [ts for ts in sets.values() if ts.get('in_progress') == owner or ts.get('requirement') == owner or ts.get('fix') == owner
                  or (ts.get('feature') in touched and not ts.get('in_progress'))]
    else:
        chosen = list(sets.values())
    for ts in chosen:
        if not set_visible(ts, scope):
            skipped.append((ts['id'], f'是 {ts["in_progress"]} 进行中的，只在它里面跑'))
            continue
        why = stale_reason(ts, features)
        if why:
            skipped.append((ts['id'], f'过期：{why}'))
            continue
        picked.append((ts, 'new' if scope and ts.get('in_progress') == scope else 'existing'))
    return picked, skipped


def run_text(out):
    lines = []
    for s in out['sets']:
        c = s['counts']
        head = f'{s["id"]} {s["title"]}（{s["feature"]}' + ('，新建' if s['origin'] == 'new' else '') + '）：'
        if s['result'] == 'broken':
            lines.append(head + f'跑不起来，{c["total"]} 条都没有执行。{(s["error"] or "").splitlines()[0] if s["error"] else ""}')
            continue
        lines.append(head + f'{c["pass"]} 通过，{c["fail"]} 失败' + ('（只跑了一条）' if s['partial'] else ''))
        for r in s['cases']:
            if r['status'] == 'fail':
                lines.append(f'  ✗ #{r["n"]} {r["name"]}：' + ('；'.join(r['diff'][:3]) or (r['error'] or '')))
    for sk in out['skipped']:
        lines.append(f'{sk["id"]}：没有运行，{sk["why"]}。')
    if not out['sets'] and not out['skipped']:
        lines.append('没有要运行的测试集。')
    t = out['totals']
    if out['scope'] and out['sets']:
        for o, word in (('new', '这次新建的'), ('existing', '以前就有的')):
            b = out['by_origin'][o]
            if b['sets']:
                lines.append(f'{word} {b["sets"]} 个测试集：{b["pass"]} 通过，{b["fail"]} 失败，{b["broken"]} 跑不起来。')
    prep = out.get('prep') or {}
    for r in prep.get('ran') or []:
        lines.insert(0, f'准备步骤 {r["n"]}「{r["note"]}」：' + ('执行完了' if r['ok'] else '失败了') + f'（{r["ms"]} 毫秒）。')
    if prep.get('error'):
        lines.append(prep['error'])
    if out.get('env') and out['sets']:
        lines.append(f'用的测试环境：{out["env"]["name"]}（{out["env"]["id"]}）' + ('，这次没有带保密值（' + '、'.join(out['secrets_withheld']) + '）' if out.get('secrets_withheld') else '') + '。')
    if out['sets']:
        lines.append(f'合计 {t["sets"]} 个测试集、{t["total"]} 条：{t["pass"]} 通过，{t["fail"]} 失败，{t["broken"]} 跑不起来。'
                     + ('跑完用 rf.py test analyze 测试集编号 --text "…" 写分析。' if t['fail'] or t['broken'] else ''))
    return '\n'.join(lines)


# ---------- 桥接文件导入的接口（对外兼容：模板在 bridge_templates.py，运行在 testruns.py） ----------


def bridge_sets(root, lang='python'):
    """The test sets of one language that a checkout's own test command runs: in use, as this checkout's requirement or fix sees them."""
    home = main_checkout(root)
    scope = here_owner(root)
    scope = scope if owner_going(home, scope) else None
    return [(ts['id'], ts.get('title')) for ts, _ in select(home, scope, everything=True)[0] if (ts.get('lang') or 'python') == lang]


def bridge_run(root, ident):
    """Kept here on purpose: the Python bridge `rf.py test bridge` writes into the project's own repository (BRIDGE above) does
    `from relayforge.testsets import bridge_sets, bridge_run`, and bridges committed by older versions stay in users' repositories.
    The running itself is in testruns.py; this only forwards to it (an allowed exception in tests/test_layers.py)."""
    from .runs import bridge_run as run
    return run(root, ident)
