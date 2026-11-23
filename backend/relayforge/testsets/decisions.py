"""测试集的用户决定（只由你在应用里触发）：批准或驳回提议、标为过期或恢复、改挂靠、在应用里保存、标为已看。

测试集的存储在 testsets.py；AI 用的新建和修改在 testset_edits.py。分层见 tests/test_layers.py。
"""
from ..core.common import commit_facts, main_checkout, today
from ..requirements.owners import owner_going, requirement_path
from ..requirements.testrecords import parse_ref
from .views import testset_show
from .store import (
    _apply_change, _canon, _dup_text, _find_case, _link_usecase, clean_case, feature_table, find_duplicate, log_line,
    read_set, stale_reason, tests_lock, view, write_set,
)

__all__ = ['testset_stale', 'testset_revive', 'testset_move', 'testset_approve', 'testset_reject', 'testset_save', 'testset_seen']


def testset_approve(root, ident, k):
    """Only the user: agree to a change the AI proposed. It counts for its requirement until that is done; one proposed on the
    main line, or by a requirement already done, changes the data at once."""
    home = main_checkout(root)
    with tests_lock(home):
        ts = read_set(home, ident)
        change = _find_change(ts, k)
        if change['state'] != 'pending':
            raise ValueError(f'改动 {change["id"]} 已经同意过了。')
        if owner_going(home, change.get('by')):
            change['state'] = 'approved'
            log_line(ts, f'用户同意了改动 {change["id"]}（#{change["case"]}），随 {change["by"]} 完成时落定。')
        else:
            _apply_change(ts, change)
            ts['changes'] = [c for c in ts['changes'] if c is not change]
            log_line(ts, f'用户同意了改动 {change["id"]}（#{change["case"]}，' + ('删除' if change['op'] == 'delete' else '修改') + '），已生效。')
        write_set(home, ts)
        commit_facts(home, f'测试集 {ts["id"]}：同意改动 {change["id"]}')
    return testset_show(home, ts['id'], change.get('by') if owner_going(home, change.get('by')) else None)


def testset_reject(root, ident, k):
    """Only the user: do not agree; the change is thrown away (the 变更记录 keeps a line)."""
    home = main_checkout(root)
    with tests_lock(home):
        ts = read_set(home, ident)
        change = _find_change(ts, k)
        ts['changes'] = [c for c in ts['changes'] if c is not change]
        log_line(ts, f'用户不同意改动 {change["id"]}（#{change["case"]}，{change.get("by") or "主线"} 提的：{change["why"]}），丢弃。')
        write_set(home, ts)
        commit_facts(home, f'测试集 {ts["id"]}：不同意改动 {change["id"]}')
    return testset_show(home, ts['id'])


def _find_change(ts, k):
    try:
        k = int(str(k).lstrip('#'))
    except ValueError:
        raise ValueError(f'「{k}」不是改动的编号。') from None
    change = next((c for c in ts.get('changes') or [] if c['id'] == k and c.get('state') in ('pending', 'approved')), None)
    if not change:
        raise ValueError(f'{ts["id"]} 没有待确认的改动 {k}。')
    return change


def testset_stale(root, ident, reason=''):
    home = main_checkout(root)
    with tests_lock(home):
        ts = read_set(home, ident)
        if ts.get('status') == 'stale':
            raise ValueError(f'{ts["id"]} 已经是过期的了。')
        ts.update(status='stale', stale_reason=' '.join(str(reason or '').split()) or None)
        log_line(ts, '用户标成过期，不再运行' + (f'：{ts["stale_reason"]}' if ts['stale_reason'] else '') + '。')
        write_set(home, ts)
        commit_facts(home, f'测试集 {ts["id"]}：标成过期')
    return testset_show(home, ts['id'])


def testset_revive(root, ident):
    home = main_checkout(root)
    with tests_lock(home):
        ts = read_set(home, ident)
        if ts.get('status') != 'stale':
            why = stale_reason(ts, feature_table(home))
            raise ValueError(f'{ts["id"]} 不是用户标的过期' + (f'：{why}。要让它再运行，先改挂靠到现行的功能点。' if why else '，本来就在用。'))
        ts.update(status='current', stale_reason=None)
        log_line(ts, '用户恢复，重新在用。')
        write_set(home, ts)
        commit_facts(home, f'测试集 {ts["id"]}：恢复')
    return testset_show(home, ts['id'])


def testset_move(root, ident, feature=None, requirement=None):
    """Only the user: hang the set on another feature and/or requirement ('none' clears the requirement)."""
    home = main_checkout(root)
    if not feature and not requirement:
        raise ValueError('用法：test move TS-001 [--feature RF-102] [--req R-…|none]')
    with tests_lock(home):
        ts = read_set(home, ident)
        said = []
        if feature:
            feature = feature.strip().upper()
            f = feature_table(home).get(feature)
            if not f or not f['ok']:
                raise ValueError(f'功能点 {feature} 不存在（或已下线）。')
            said.append(f'功能点 {ts.get("feature")} → {feature}')
            ts['feature'] = feature
        if requirement:
            if ts.get('in_progress'):
                raise ValueError(f'{ts["id"]} 还是 {ts["in_progress"]} 进行中的，跟着它走；完成以后才能改挂到别的需求。')
            requirement = None if requirement.strip().lower() == 'none' else requirement.strip()
            if requirement and not requirement_path(home, requirement):
                raise ValueError(f'没有找到需求 {requirement}。')
            said.append(f'需求 {ts.get("requirement") or "无"} → {requirement or "无"}')
            ts['requirement'] = requirement
        log_line(ts, '用户改挂靠：' + '；'.join(said) + '。')
        write_set(home, ts)
        commit_facts(home, f'测试集 {ts["id"]}：改挂靠')
    return testset_show(home, ts['id'])


def testset_save(root, ident, payload, n=None, scope=None):
    """Only the app, as the user: save the whole set's data (payload {"cases": […], "title"?} or a list), or one case
    (n given, or a single object without one to add it). The user's edits take effect at once, no confirmation. A whole
    save covers the cases `scope` sees (the main line, or a requirement's view); the others are left as they are."""
    home = main_checkout(root)
    with tests_lock(home):
        ts = read_set(home, ident)
        seen = view(ts, scope)
        shown = {c['n']: c for c in seen}
        added, changed, removed, voided = [], [], [], []
        def store(raw, case=None):
            new = clean_case(raw, f'#{case["n"]}' if case else '新的一条')
            if new.get('usecase') is not None and parse_ref(new['usecase'])[0] is None:  # 用户在查看器里写了用例编号：核对、写成「需求编号#用例编号」；已经是那样的原样留着
                _link_usecase(home, scope or ts.get('in_progress') or ts.get('requirement'), new, False, f'#{case["n"]}' if case else '新的一条')
            if case is not None and case['n'] in shown and all(_canon(new.get(k)) == _canon(shown[case['n']].get(k)) for k in ('name', 'cat', 'input', 'expect', 'raises', 'usecase')):
                return  # 和用户看到的一样，没改：不动它（不记「改过」，不清 AI 新加的标记，也不会把还没同意的改动当成用户改的写进去）
            dup = find_duplicate(new, [c for c in pool if not case or c['n'] != case['n']])
            if dup:
                raise ValueError(f'「{new["name"]}」{_dup_text(*dup)}，没有保存。')
            if case is None:
                new.update(n=ts.get('next') or 1, by='user', added=today())
                if scope and not ts.get('in_progress'):
                    new['in_progress'] = scope
                ts['next'] = new['n'] + 1
                ts['cases'].append(new)
                pool.append(new)
                added.append(new['n'])
                return
            for k in ('expect', 'raises'):
                case.pop(k, None)
            case.update(new, edited=today())
            case.pop('unseen', None)
            mine = [x for x in ts.get('changes') or [] if x['case'] == case['n'] and x.get('state') in ('pending', 'approved')]
            if mine:  # 用户直接改了这一条：AI 对它提的改动作废，不然在需求眼里还会盖过用户改的
                ts['changes'] = [x for x in ts['changes'] if x not in mine]
                voided.extend(x['id'] for x in mine)
            for p in pool:
                if p['n'] == case['n']:
                    p.clear()
                    p.update(case)
            changed.append(case['n'])
        pool = [dict(c) for c in seen]
        if n is not None and str(n).lower() != 'new':
            store(payload, _find_case(ts, n))
        elif isinstance(payload, dict) and 'cases' not in payload:
            store(payload)
        else:
            items = payload.get('cases') if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                raise ValueError('整份保存要给 {"cases": [...]} 或一个数组。')
            if isinstance(payload, dict) and str(payload.get('title') or '').strip():
                ts['title'] = ' '.join(str(payload['title']).split())
            keep = set()
            pool = []
            for raw in items:
                if isinstance(raw, dict) and raw.get('n') is not None:
                    keep.add(_find_case(ts, raw['n'])['n'])
            for c in seen:  # 先把要删的拿掉，再逐条比对重复
                if c['n'] in keep:
                    pool.append(dict(c))
            for raw in items:
                if isinstance(raw, dict) and raw.get('n') is not None:
                    store(raw, _find_case(ts, raw['n']))
                else:
                    store(raw)
            for c in seen:
                if c['n'] not in keep and c['n'] not in added:
                    ts['cases'] = [x for x in ts['cases'] if x['n'] != c['n']]
                    ts['changes'] = [x for x in ts.get('changes') or [] if x['case'] != c['n']]
                    removed.append(c['n'])
        log_line(ts, '用户保存了数据：' + '，'.join(x for x in (f'新加 {len(added)} 条' if added else '', f'改了 {len(changed)} 条' if changed else '',
                                                        f'删了 {len(removed)} 条' if removed else '') if x) + '。' if added or changed or removed else '用户保存了数据，没有变化。')
        if voided:
            log_line(ts, '用户直接改了数据，AI 提的改动 ' + '、'.join(str(k) for k in voided) + ' 作废。')
        write_set(home, ts)
        commit_facts(home, f'测试集 {ts["id"]}：用户保存数据')
    out = testset_show(home, ts['id'], scope)
    out['saved'] = {'added': added, 'changed': changed, 'removed': removed, 'voided': voided}
    return out


def testset_seen(root, ident, n=None):
    """The user has looked at what the AI added: clear the 'AI 新加' mark (one case, or all)."""
    home = main_checkout(root)
    with tests_lock(home):
        ts = read_set(home, ident)
        hit = 0
        for c in ts.get('cases') or []:
            if c.get('unseen') and (n is None or c['n'] == int(str(n).lstrip('#'))):
                c.pop('unseen')
                hit += 1
        if hit:
            write_set(home, ts)
    return {'id': ts['id'], 'cleared': hit}
