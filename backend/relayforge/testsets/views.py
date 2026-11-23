"""测试集的查看：列表、一个测试集的详情、分析和提示、需求的测试用例对上了哪些测试集。

只读不写。测试集的存储在 testsets.py。分层见 tests/test_layers.py。
"""
from ..core.common import main_checkout
from ..core.documents import read_doc
from ..core.language import display_text
from ..requirements.owners import here_owner, owner_going, owner_place, requirement_path
from ..requirements.testrecords import parse_cases, parse_ref, testing_view
from .store import (
    full_status,
    _canon, _dump, _load, _source, all_testsets, feature_table, NEVER_FAILED, read_set, read_stats, result_path, select,
    set_path, set_visible, stale_reason, stamp, stats_path, TEST_CATS, tests_lock, view,
)

__all__ = ['testset_analyze', 'testset_list', 'testset_show']


def testset_analyze(root, ident, text, session=None, transcript=None):
    """The AI writes what it makes of the last run: what passed, what did not and why. Marked as written by the AI."""
    home = main_checkout(root)
    text = str(text or '').strip()
    if not text:
        raise ValueError('分析要写内容：--text "哪些过了、哪些没过、为什么"。')
    owner = here_owner(root)
    scope = owner if owner_going(home, owner) else None
    with tests_lock(home):
        ts = read_set(home, ident)
        last = _load(result_path(home, ts['id'], scope))
        if not last:
            raise ValueError(f'{ts["id"]} 在{scope or "主线"}上还没运行过，先 test run 再写分析。')
        stats = read_stats(home, ts['id'])
        kept = dict(stats.get('analysis') or {})
        kept[scope or 'main'] = {'text': text, 'by': 'ai', 'at': stamp(), 'run_at': last.get('at'), 'result': last.get('result'), 'source': _source(root, session, transcript)}
        stats['analysis'] = dict(sorted(kept.items(), key=lambda kv: kv[1].get('at') or '')[-6:])
        _dump(stats_path(home, ts['id']), stats)
    return {'id': ts['id'], 'scope': scope, 'analysis': kept[scope or 'main']}


def hints(stats):
    """Fixed rules the program can compute, no judgement: 跑不起来, 从没失败过, 时过时不过."""
    out = []
    recent = [r for r in stats.get('recent') or [] if isinstance(r, dict)]
    if recent and recent[-1].get('result') == 'broken':
        out.append({'key': 'broken', 'text': display_text('跑不起来：最近一次接入代码没能运行，一条都没有执行',
                                                        'The test runner failed before executing any cases.')})
    runs, failed = int(stats.get('runs') or 0), int(stats.get('failed') or 0)
    if runs >= NEVER_FAILED and failed == 0:
        out.append({'key': 'never_failed', 'text': display_text(f'从没失败过：跑过 {runs} 次，一次都没失败',
                                                              f'No failures across {runs} runs.')})
    groups = {}
    for r in recent:
        groups.setdefault((r.get('code'), r.get('data')), set()).add((r.get('result'), tuple(r.get('failing') or ())))
    if any(len(v) > 1 for v in groups.values()):
        out.append({'key': 'flaky', 'text': display_text('时过时不过：同一份代码和数据，最近几次的结果不一样',
                                                        'Recent runs produced different results with the same code and data.')})
    return out


def _last(home, ident, scope):
    rec = _load(result_path(home, ident, scope))
    if not rec:
        return None
    return {k: rec.get(k) for k in ('scope', 'at', 'result', 'error', 'counts', 'partial', 'place', 'env')}


def _summary(home, ts, scope, features, stats=None):
    stats = stats or read_stats(home, ts['id'])
    cases = view(ts, scope if set_visible(ts, scope) else ts.get('in_progress'))
    owner = ts.get('in_progress')
    reason = stale_reason(ts, features)
    f = features.get(str(ts.get('feature') or ''), {})
    pending = [c for c in ts.get('changes') or [] if c.get('state') in ('pending', 'approved')]
    own_scope = scope if scope else owner
    return {
        'id': ts['id'], 'title': ts.get('title'), 'feature': ts.get('feature'), 'feature_title': f.get('title') or '',
        'requirement': ts.get('requirement'), 'fix': ts.get('fix'), 'checks': ts.get('checks') or [], 'lang': ts.get('lang'), 'entry': ts.get('entry'), 'classpath': ts.get('classpath'), 'build': ts.get('build'),
        'status': 'stale' if reason else 'current', 'stale': bool(reason), 'stale_reason': reason or None, 'in_progress': owner,
        'cases': len(cases), 'cats': {k: sum(1 for c in cases if c.get('cat') == k) for k in TEST_CATS},
        'unseen': sum(1 for c in cases if c.get('unseen')), 'new_cases': sum(1 for c in cases if c.get('in_progress')),
        'pending_changes': [_change_view(ts, c) for c in pending if c['state'] == 'pending'],
        'approved_changes': [_change_view(ts, c) for c in pending if c['state'] == 'approved'],
        'last': _last(home, ts['id'], own_scope) or (_last(home, ts['id'], None) if own_scope else None),
        'last_main': _last(home, ts['id'], None),
        'runs': int(stats.get('runs') or 0), 'failed_runs': int(stats.get('failed') or 0), 'hints': hints(stats),
        'analysis': (stats.get('analysis') or {}).get(own_scope or 'main') or (stats.get('analysis') or {}).get('main'),
        'created': ts.get('created'), 'updated': ts.get('updated'),
    }


def _change_view(ts, c):
    case = next((x for x in ts.get('cases') or [] if x['n'] == c['case']), {})
    return {'id': c['id'], 'case': c['case'], 'name': case.get('name') or c['old'].get('name'), 'op': c['op'], 'old': c['old'], 'new': c['new'],
            'why': c['why'], 'by': c.get('by'), 'state': c['state'], 'at': c.get('at')}


def testset_list(root, req=None, fix=None, feature=None, stale=None):
    """Every test set with what the app shows in a row. With --req/--fix: the sets that requirement or fix runs, in its view."""
    home = main_checkout(root)
    features = feature_table(home)
    scope = req or fix
    if scope:
        owner = scope
        scope = scope if owner_going(home, scope) else None
        picked, skipped = select(home, scope, owner=owner)
        rows = [dict(_summary(home, ts, scope, features), origin=o) for ts, o in picked]
        rows += [dict(_summary(home, read_set(home, i), scope, features), origin='existing', skipped=w) for i, w in skipped]
    else:
        rows = [_summary(home, ts, None, features) for ts in all_testsets(home) if not feature or ts.get('feature') == feature.upper()]
    if stale is not None:
        rows = [r for r in rows if r['stale'] == stale]
    rows.sort(key=lambda r: (r['feature'] or '', r['id']))
    totals = {'sets': len(rows), 'cases': sum(r['cases'] for r in rows), 'stale': sum(1 for r in rows if r['stale']),
              'in_progress': sum(1 for r in rows if r['in_progress']), 'pending_changes': sum(len(r['pending_changes']) for r in rows),
              'with_hints': sum(1 for r in rows if r['hints'])}
    out = {'scope': req or fix, 'in_progress': bool(scope), 'sets': rows, 'totals': totals}
    if not scope:  # 主线的列表：上次全量是什么时候、在哪个提交上（合并闸门只跑一部分，剩下的靠它）
        out['full'] = full_status(home)
    if req and requirement_path(home, req):  # 需求面板：验收条件 → 用例 → 对接的数据 → 结果
        out['usecases'] = usecase_report(home, req)
    return out


def testset_show(root, ident, scope=None):
    """One test set in full for the viewer: its cases (as `scope` sees them) each with the last result, pending changes,
    analysis, hints and the access code."""
    home = main_checkout(root)
    ts = read_set(home, ident)
    features = feature_table(home)
    own = scope or ts.get('in_progress')
    out = _summary(home, ts, scope, features)
    rec = _load(result_path(home, ts['id'], own)) or (_load(result_path(home, ts['id'], None)) if own else None) or {}
    results = {r['n']: r for r in rec.get('cases') or []}
    cases = []
    for c in view(ts, own):
        r = results.get(c['n'])
        same = bool(r) and _canon([r.get('input'), r.get('expect'), r.get('raises')]) == _canon([c.get('input'), c.get('expect'), c.get('raises')])
        row = {k: c[k] for k in ('n', 'name', 'cat', 'input', 'expect', 'raises', 'by', 'unseen', 'in_progress', 'changed', 'added', 'source', 'usecase', 'made_in') if k in c}
        row['result'] = {k: r.get(k) for k in ('status', 'actual', 'error', 'diff', 'ms')} if r and same else None
        row['stale_result'] = bool(r) and not same  # 数据改过，上次的结果不再对应
        cases.append(row)
    out['cases_list'] = cases
    out['usecases'] = usecase_details(home, {c['usecase'] for c in cases if c.get('usecase')})
    out['log'] = ts.get('log') or []
    out['data_file'] = str(set_path(home, ts['id']))
    place = owner_place(home, own) or home
    entry = place / str(ts.get('entry') or '')
    try:
        out['entry_code'] = entry.read_text(encoding='utf-8', errors='replace')[:100000]
    except OSError:
        out['entry_code'] = None
    out['entry_path'] = str(entry)
    out['scope'] = own
    return out


def usecase_report(home, owner):
    """For a requirement: each acceptance condition → its test cases → the test data joined to each (in its view) → the latest result.
    A case's result comes from its data: pass (all passed), fail (some failed), broken (could not run), unrun (joined, not run since),
    unlinked (no data yet). Data not joined to any case is counted apart (old requirements, or data the development session added)."""
    path = requirement_path(home, owner)
    if not path:
        raise ValueError(f'没有找到需求 {owner}。')
    meta, sections = read_doc(path)
    tv = testing_view(meta, sections)
    links, loose, other = {}, 0, []
    for ts, origin in (select(home, owner)[0] if owner else []):
        rec = _load(result_path(home, ts['id'], owner)) or {}
        results = {r['n']: r for r in rec.get('cases') or []}
        for c in view(ts, owner):
            req, n = parse_ref(c.get('usecase'))
            if req != owner:
                loose += 1
                continue
            r = results.get(c['n'])
            same = bool(r) and _canon([r.get('input'), r.get('expect'), r.get('raises')]) == _canon([c.get('input'), c.get('expect'), c.get('raises')])
            status = r['status'] if r and same and r.get('status') in ('pass', 'fail', 'broken') else 'unrun'
            links.setdefault(n, []).append({'set': ts['id'], 'title': ts.get('title'), 'case': c['n'], 'name': c['name'], 'cat': c.get('cat'), 'status': status,
                                            'origin': origin, 'made_in': c.get('made_in') or (ts.get('made_in') if ts.get('in_progress') == owner else None)})
    by_case = {}
    known = {c['n'] for c in tv['cases']}
    for c in tv['cases']:
        got = links.get(c['n']) or []
        states = {x['status'] for x in got}
        status = ('unlinked' if not got else 'fail' if 'fail' in states else 'broken' if 'broken' in states else 'unrun' if 'unrun' in states else 'pass')
        by_case[c['n']] = {'status': status, 'links': got}
    other = [dict(x, usecase=n) for n, got in links.items() if n not in known for x in got]
    conds = []
    for cond in tv['checks']:
        cases = [dict(c, **by_case[c['n']]) for c in tv['cases'] if c['live'] and c['check'] == cond['key']]
        conds.append(dict(cond, cases=cases))
    live = [c for c in tv['cases'] if c['live']]
    counts = {k: sum(1 for c in live if by_case[c['n']]['status'] == k) for k in ('pass', 'fail', 'broken', 'unrun', 'unlinked')}
    return {'id': owner, 'has_cases': tv['has_cases'], 'case_flow': tv['case_flow'], 'conditions': conds,
            'deleted': [dict(c, **by_case[c['n']]) for c in tv['cases'] if not c['live']],
            'orphans': [dict(c, **by_case[c['n']]) for c in live if c['n'] in tv['orphan_cases']],
            'changes': tv['case_changes'], 'pending_changes': tv['pending_case_changes'], 'uncovered': tv['uncovered'],
            'by_case': by_case, 'counts': counts, 'cases': len(live), 'supplements': tv['supplements'],
            'unjoined_data': loose, 'unknown_links': other}


def usecase_details(home, refs):
    """{"R-…#3": the test case (name, category, 前提 / 操作 / 预期, condition, live, source)} for the viewer."""
    out, docs = {}, {}
    for ref in refs:
        req, n = parse_ref(ref)
        if not req:
            continue
        if req not in docs:
            path = requirement_path(home, req)
            docs[req] = {c['n']: c for c in parse_cases(read_doc(path)[1])} if path else {}
        c = docs[req].get(n)
        out[ref] = {k: c[k] for k in ('n', 'check', 'name', 'cat', 'given', 'when', 'then', 'source', 'live')} if c else None
    return out
