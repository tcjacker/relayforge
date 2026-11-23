"""测试集随需求或修复落定和撤掉：进行中的测试集在确认完成时落定成现行，放弃时收到 history/，恢复时拿回来；以及完成记录里那一行测试结论。

测试集的存储在 testsets.py。分层见 tests/test_layers.py。
"""
from ..core.common import main_checkout, today
from .store import (
    _apply_change, _dump, _load, all_testsets, log_line, result_path, select, set_path, shelf_dir, tests_lock, view,
    write_set,
)

__all__ = ['testsets_promote', 'testsets_shelve', 'testsets_unshelve', 'testsets_summary', 'testsets_of', 'regression_cases']


def testsets_of(home, owner):
    """The sets a requirement or fix brought or ran (new in progress, attached to it, or on the features it touches)."""
    return select(home, owner)[0]


def testsets_summary(home, owner):
    """For the 完成记录: how many sets, cases and how the last run in its view went. Computed before landing."""
    picked = testsets_of(home, owner) if owner else []
    out = {'sets': len(picked), 'new': sum(1 for _, o in picked if o == 'new'), 'existing': sum(1 for _, o in picked if o == 'existing'),
           'cases': 0, 'pass': 0, 'fail': 0, 'broken': 0, 'unrun': 0}
    for ts, _ in picked:
        cases = view(ts, owner)
        out['cases'] += len(cases)
        rec = _load(result_path(home, ts['id'], owner)) or {}
        status = {r['n']: r['status'] for r in rec.get('cases') or []}
        for c in cases:
            s = status.get(c['n'])
            out[s if s in ('pass', 'fail', 'broken') else 'unrun'] += 1
    return out


def regression_cases(home, owner):
    """What `owner` (a fix) added to the test sets, with how each case did in its last run in owner's view:
    [{set, title, feature, n, name, status}] where status is pass / fail / broken / unrun. A new set counts with all its cases;
    a landed set counts with the cases marked in_progress for owner."""
    out = []
    for ts in all_testsets(home):
        mine = ts.get('in_progress') == owner
        cases = [c for c in view(ts, owner) if mine or c.get('in_progress') == owner]
        if not cases:
            continue
        rec = _load(result_path(home, ts['id'], owner)) or {}
        status = {r['n']: r['status'] for r in rec.get('cases') or []}
        for c in cases:
            out.append({'set': ts['id'], 'title': ts.get('title') or '', 'feature': ts.get('feature'), 'n': c['n'], 'name': c.get('name') or '',
                        'status': status.get(c['n']) if status.get(c['n']) in ('pass', 'fail', 'broken') else 'unrun'})
    return out


def summary_line(s):
    if not s['sets']:
        return ''
    return (f'测试集：{s["sets"]} 个（新建 {s["new"]} 个，动到的功能点原有 {s["existing"]} 个），共 {s["cases"]} 条；最近一次运行通过 {s["pass"]} 条、失败 {s["fail"]} 条、跑不起来 {s["broken"]} 条'
            + (f'、还没跑 {s["unrun"]} 条' if s['unrun'] else '') + '。')


def testsets_promote(home, owner, source=''):
    """The requirement or fix is done: what it brought to the test sets becomes landed. Returns the ids touched."""
    home = main_checkout(home)
    done = []
    with tests_lock(home):
        for ts in all_testsets(home):
            hit = False
            if ts.get('in_progress') == owner:
                ts.pop('in_progress')
                hit = True
            for c in ts.get('cases') or []:
                if c.get('in_progress') == owner:
                    c.pop('in_progress')
                    hit = True
            for change in list(ts.get('changes') or []):
                if change.get('by') != owner:
                    continue
                if change['state'] == 'approved':
                    _apply_change(ts, change)
                    ts['changes'].remove(change)
                    hit = True
                # 还没确认的改动留着：提出它的需求完成以后，用户同意就直接生效
            if hit:
                log_line(ts, f'随 {owner} 确认完成，成为在用的数据。', source)
                write_set(home, ts)
                done.append(ts['id'])
    return done


def testsets_shelve(home, owner, source=''):
    """The requirement is cancelled: the sets it created go to history/tests/, the cases it added and its changes stop counting."""
    home = main_checkout(home)
    done = []
    with tests_lock(home):
        for ts in all_testsets(home):
            if ts.get('in_progress') == owner:
                ts['dropped_with'] = owner
                log_line(ts, f'随 {owner} 放弃，移到 history/tests/。', source)
                clean = {k: v for k, v in ts.items() if not k.startswith('_')}
                _dump(shelf_dir(home) / f'{ts["id"]}.json', dict(clean, updated=today()))
                set_path(home, ts['id']).unlink()
                done.append(ts['id'])
                continue
            hit = False
            for c in ts.get('cases') or []:
                if c.get('in_progress') == owner:
                    c['dropped_with'] = c.pop('in_progress')
                    hit = True
            for change in ts.get('changes') or []:
                if change.get('by') == owner and change['state'] in ('pending', 'approved'):
                    change['was'], change['state'] = change['state'], 'dropped'
                    hit = True
            if hit:
                log_line(ts, f'随 {owner} 放弃，它加的数据和提出的改动不再算数。', source)
                write_set(home, ts)
                done.append(ts['id'])
    return done


def testsets_unshelve(home, owner, source=''):
    """The cancelled requirement is restored: bring back what shelve put away."""
    home = main_checkout(home)
    done = []
    with tests_lock(home):
        for path in sorted(shelf_dir(home).glob('TS-*.json')):
            ts = _load(path)
            if not ts or ts.get('dropped_with') != owner:
                continue
            ts.pop('dropped_with')
            log_line(ts, f'{owner} 恢复，拿回来，仍是进行中。', source)
            write_set(home, ts)
            path.unlink()
            done.append(ts['id'])
        for ts in all_testsets(home):
            hit = False
            for c in ts.get('cases') or []:
                if c.get('dropped_with') == owner:
                    c['in_progress'] = c.pop('dropped_with')
                    hit = True
            for change in ts.get('changes') or []:
                if change.get('by') == owner and change['state'] == 'dropped':
                    change['state'] = change.pop('was', 'pending')
                    hit = True
            if hit and ts['id'] not in done:
                log_line(ts, f'{owner} 恢复，它加的数据和提出的改动又算进行中。', source)
                write_set(home, ts)
                done.append(ts['id'])
    return done
