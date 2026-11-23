"""合并闸门：需求（或修复）合并前，在合并后的代码上跑一遍选出来的回归测试集（设计见 docs/design/合并闸门-设计.md）。

合并时（flows.py），`merge_blockers` 之后需求的工作目录已经包含目标分支的最新提交，它的 HEAD 就是合并后的代码，所以就在
那里跑，不另开目录。全量太慢（合并队列同一时间只合一个），所以只跑三类，按优先级：
  1. 本需求的：和 `test run --req` 一样的范围（它新建的、挂在它名下的、它动到的功能点下已有的）；
  2. 依赖它的：功能点文件头 depends_on 写着本需求动到的功能点的那些功能点下的（只往外一层）；
  3. 主干：标为主干的用户旅程（旅程文件头 trunk: true，由用户定）上各步骤用到的功能点下的。
这份代码上测过而且通过了的（最近一次完整结果的代码指纹、数据指纹都和现在一样）直接用那次的结果，不再跑。
按最近一次的耗时估，超过时间预算的从后往前去掉（第 1 类不去掉），去掉的列出来留给全量。
没过的先排除「主线上本来就失败」的（最近一次主线运行就失败了同样的那几条），其余的挡住合并。
"""
import hashlib
from pathlib import Path

from ..core.common import DIR
from ..core.documents import read_doc
from ..envs.prep import run_prep
from ..envs.values import env_for_run
from ..facts.mapfacts import load_journeys
from ..requirements.owners import owner_features
from .runs import build_block, code_print, run_one
from .store import _canon, _load, result_path, select, view

__all__ = ['gate_plan', 'gate_run', 'gate_line', 'GateFailed', 'GATE_BUDGET_MS']

GATE_BUDGET_MS = 5 * 60 * 1000  # 合并队列同一时间只合一个：每个需求在这里多等几分钟，后面排着的都跟着等
KINDS = ('own', 'trunk', 'depends')  # 预算不够时的先后：本需求的、主干、依赖它的
KIND_WORDS = {'own': '本需求', 'depends': '依赖它的', 'trunk': '主干'}


class GateFailed(ValueError):
    """The gate stopped the merge: `result` is what gate_run returned (failing sets, or what needs the user's consent first)."""
    def __init__(self, result, what='需求'):
        self.result = result
        if result.get('blocked'):
            text = '合并前的检查没法开始：' + '；'.join(result['blocked']) + '。在应用里同意以后，再点「重新检查并合并」。'
        else:
            bad = '、'.join(f'{f["id"]}（{f["title"]}）' for f in result['failing'][:5])
            text = f'合并后的代码没过合并前的检查：{bad}' + ('等' if len(result['failing']) > 5 else '') + f'。先在这个{what}的会话里修好再合并。'
        super().__init__(text)


def _dependents(home, touched):
    """Features whose header depends_on names one of `touched` (one step out), excluding `touched` itself."""
    out = []
    for path in sorted((Path(home) / DIR / 'features').glob('RF-*.md')):
        meta = read_doc(path)[0]
        ident = str(meta.get('id') or '')
        needs = meta.get('depends_on') or []
        if isinstance(needs, str):
            needs = [needs]
        if ident and ident not in touched and any(str(n) in touched for n in needs):
            out.append(ident)
    return out


def trunk_features(home):
    """The features used by the steps of the journeys marked as trunk (journey header trunk: true), and those journeys."""
    features, journeys = [], []
    for j in load_journeys(Path(home)):
        if not j['trunk']:
            continue
        journeys.append({'id': j['id'], 'title': j.get('title') or ''})
        for step in j['steps']:
            features.extend(step['features'])
    return list(dict.fromkeys(features)), journeys


def _data_print(ts, scope):
    cases = view(ts, scope)
    return hashlib.sha1(_canon([[c['n'], c.get('input'), c.get('expect'), c.get('raises')] for c in cases]).encode()).hexdigest()[:12]


def _reusable(home, ts, scope, code):
    """The last full result of this set in `scope` when it ran on exactly this code and data and passed, else None."""
    last = _load(result_path(home, ts['id'], scope))
    if not last or last.get('partial') or last.get('result') != 'pass':
        return None
    if last.get('code') != code or last.get('data') != _data_print(ts, scope):
        return None
    return last


def _cost(home, ts, scope):
    """How long the set took the last time it ran (this scope, else the main line); unknown counts as a few seconds."""
    for where in (scope, None):
        last = _load(result_path(home, ts['id'], where))
        if last and last.get('ms') is not None:
            return float(last['ms'])
    return 3000.0


def _shaky(home, ident):
    """Failed lately, or comes and goes: run those first when the budget does not reach every set."""
    stats = _load(Path(home) / DIR / 'tests' / 'stats' / f'{ident}.json') or {}
    recent = stats.get('recent') or []
    return any(r.get('result') != 'pass' for r in recent[-5:])


def gate_plan(home, owner, place, budget_ms=GATE_BUDGET_MS):
    """Which sets the gate covers for `owner` (R-… or F-…) in its working directory `place`:
    {sets: [{id, title, feature, kind, reuse}], left_out: [{id, title, kind}], journeys: [...], code}."""
    touched = list(dict.fromkeys(owner_features(home, owner)))
    own, skipped = select(home, owner, owner=owner)
    deps = _dependents(home, set(touched))
    trunk, journeys = trunk_features(home)
    every, _ = select(home, owner, everything=True)
    kind_of = {ts['id']: 'own' for ts, _ in own}
    for ts, _ in every:
        if ts['id'] in kind_of:
            continue
        if ts.get('feature') in deps:
            kind_of[ts['id']] = 'depends'
        elif ts.get('feature') in trunk:
            kind_of[ts['id']] = 'trunk'
    sets = {ts['id']: ts for ts, _ in every}
    sets.update({ts['id']: ts for ts, _ in own})
    code = code_print(place)
    rows = []
    for ident, kind in kind_of.items():
        ts = sets[ident]
        reuse = _reusable(home, ts, owner, code)
        rows.append({'ts': ts, 'kind': kind, 'reuse': bool(reuse), 'cost': 0.0 if reuse else _cost(home, ts, owner), 'shaky': _shaky(home, ident)})
    # 预算：第 1 类都跑；其余按主干、依赖的顺序，同一类里最近失败过的在前，放不下的留给全量
    rows.sort(key=lambda r: (KINDS.index(r['kind']), not r['shaky'], r['ts']['id']))
    spent, kept, left = 0.0, [], []
    for r in rows:
        if r['kind'] != 'own' and not r['reuse'] and spent + r['cost'] > budget_ms:
            left.append(r)
            continue
        spent += r['cost']
        kept.append(r)
    pick = lambda r: {'id': r['ts']['id'], 'title': r['ts'].get('title') or '', 'feature': r['ts'].get('feature'), 'kind': r['kind'], 'reuse': r['reuse']}
    return {'sets': [pick(r) for r in kept], 'left_out': [pick(r) for r in left], 'journeys': journeys, 'code': code, 'estimate_ms': round(spent),
            'skipped': [{'id': i, 'why': w} for i, w in skipped]}


def _preexisting(home, ident, failing):
    """The main line's last run of this set failed these same cases (or more): not this requirement's doing."""
    last = _load(result_path(home, ident, None))
    if not last or last.get('result') == 'pass':
        return False
    if last.get('error'):
        return bool(failing) and last.get('result') == 'broken' and failing == ['*']
    before = {r['n'] for r in last.get('cases') or [] if r.get('status') != 'pass'}
    return bool(failing) and failing != ['*'] and set(failing) <= before


def gate_run(home, owner, place, trusted=None, budget_ms=GATE_BUDGET_MS):
    """Run the gate. {ok, blocked: [...], sets: [...], failing: [...], preexisting: [...], left_out, journeys, ran, reused}.
    ok is False when a set this requirement may have broken fails, or when something needs the user's consent first (blocked)."""
    plan = gate_plan(home, owner, place, budget_ms)
    out = dict(plan, ok=True, blocked=[], failing=[], preexisting=[], ran=0, reused=0)
    todo = [s for s in plan['sets'] if not s['reuse']]
    out['reused'] = len(plan['sets']) - len(todo)
    if not todo:
        return out
    sets = {ts['id']: ts for ts, _ in select(home, owner, ids=[s['id'] for s in todo], owner=owner)[0]}
    envinfo = env_for_run(home, None, False, trusted)
    pre_error = None
    if envinfo and envinfo['env'].get('prep'):
        dry = run_prep(home, envinfo, place, dry=True)
        if dry.get('blocked'):
            out.update(ok=False, blocked=[f'测试环境的准备步骤 {b.get("id") or ""} 还没经你同意' for b in dry['blocked']])
            return out
        pre_error = run_prep(home, envinfo, place).get('error')
    builds = [f'{i} 的构建命令还没经你同意' for i, ts in sets.items() if build_block(home, ts, place)]
    if builds:
        out.update(ok=False, blocked=builds)
        return out
    for s in todo:
        ts = sets.get(s['id'])
        if not ts:
            continue
        rec = run_one(home, ts, owner, place, envinfo=envinfo, pre_error=pre_error)
        out['ran'] += 1
        s.update(result=rec['result'], counts=rec['counts'])
        if rec['result'] == 'pass':
            continue
        failing = ['*'] if rec['error'] else [r['n'] for r in rec['cases'] if r['status'] != 'pass']
        row = {'id': s['id'], 'title': s['title'], 'kind': s['kind'], 'result': rec['result'], 'cases': failing,
               'error': (rec['error'] or '')[:500] or None,
               'names': [r.get('name') for r in rec['cases'] if r['status'] != 'pass'][:10]}
        (out['preexisting'] if _preexisting(home, s['id'], failing) else out['failing']).append(row)
    out['ok'] = not out['failing']
    return out


def gate_line(result):
    """The line the 完成记录 gets."""
    count = lambda kind: sum(1 for s in result['sets'] if s['kind'] == kind)
    parts = [f'{KIND_WORDS[k]} {count(k)} 个' for k in KINDS if count(k)]
    text = f'合并前在合并后的代码上检查了 {len(result["sets"])} 个测试集' + (f'（{"、".join(parts)}）' if parts else '')
    if result['reused']:
        text += f'，其中 {result["reused"]} 个这份代码上已经测过'
    if result['ok']:
        text += '，都通过了'
    if result['preexisting']:
        text += f'；{"、".join(f["id"] for f in result["preexisting"])} 在主线上原来就失败，不算这次的'
    if result['left_out']:
        text += f'；{len(result["left_out"])} 个超出时间预算没跑，留给全量'
    if not result['journeys']:
        text += '；还没有标主干的用户旅程'
    return text + '。'
