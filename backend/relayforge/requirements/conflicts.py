"""需求之间的冲突：从合并时才发现，提前到开发时发现（设计见 docs/design/会话通道与需求冲突-设计.md 第二部分）。

只读，由程序算，不调用模型。对每个进行中、有自己工作目录的需求和修复：
  1. 改动重叠：它改了哪些文件——已提交的（和合并目标分开以来，git diff 目标...分支）加上工作目录里还没提交的（git status）——两两求交集；
  2. 预演合并：git merge-tree --write-tree（git 2.38 起有）不碰任何工作目录，算出合并会不会冲突、冲突在哪些文件：
     - 每个分支对合并目标（项目主目录签出的分支）；
     - 改动有重叠的那几对分支之间。
     只覆盖已提交的改动；还没提交的部分只能由第 1 层的文件重叠提示。
记录目录（.relayforge/）不算改动：它们由程序合并时单独处理。
  3. 立项时预测（predict）：还没写代码的需求算不出改动，就按历史推测——这个需求要改的功能点（和模块）→ 以前改过同样功能点、
     已经完成的需求 → 它们的提交改过哪些文件（sessions/trace.item_files）→ 预计会动到的文件；再和进行中的需求实际改的文件比，
     也列出同样要改这些功能点的进行中需求。只是提示，由用户决定要不要设前置需求或先拆分。

  rf.py conflicts [--json]
  rf.py conflicts --predict R-… [--json]
"""
from itertools import combinations

from ..core.common import DIR, main_checkout, run_git
from ..core.documents import live_items, read_doc
from ..sessions.trace import item_files
from .owners import all_fixes, branch_places, owner_features
from ..facts.fresh import feature_files, outside_scope
from .record import ID_PATTERN, facts_path, target_branch

__all__ = ['conflicts', 'conflicts_text', 'in_progress', 'predict', 'predict_text']


def in_progress(home):
    """The requirements (立项中、进行中) and fixes (修复中) that have a working directory of their own: {id, kind, title, branch, place}."""
    places = branch_places(home)
    home_place = places.get(target_branch(home) or '')
    out = []
    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        meta = read_doc(path)[0]
        branch = str(meta.get('branch') or '')
        place = places.get(branch)
        if meta.get('id') and (meta.get('state') or 'open') in ('draft', 'open') and place and place != home_place:
            out.append({'id': meta['id'], 'kind': 'req', 'state': meta.get('state') or 'open', 'title': meta.get('title') or meta['id'], 'branch': branch, 'place': str(place)})
    for fid, fix in sorted(all_fixes(home).items()):
        branch = str(fix.get('branch') or '')
        place = places.get(branch)
        if fix.get('state') == 'fixing' and place:
            out.append({'id': fid, 'kind': 'fix', 'state': 'fixing', 'title': fix.get('title') or f'修复 {fid}', 'branch': branch, 'place': str(place)})
    return out


def _committed(home, base, branch):
    done = run_git(home, 'diff', '--name-only', f'{base}...{branch}')
    return {n for n in done.stdout.splitlines() if n and not facts_path(n)} if done.returncode == 0 else set()


def _uncommitted(place):
    done = run_git(place, 'status', '--porcelain', '--untracked-files=all')
    out = set()
    for line in done.stdout.splitlines() if done.returncode == 0 else []:
        name = line[3:].split(' -> ')[-1].strip().strip('"')
        if name and not facts_path(name):
            out.add(name)
    return out


def trial_merge(home, a, b):
    """Files a merge of a and b would conflict in ([] when it merges cleanly), or None when git cannot tell (too old, no common history)."""
    done = run_git(home, 'merge-tree', '--write-tree', '--name-only', '--no-messages', a, b)
    if done.returncode not in (0, 1):
        return None
    lines = [n for n in done.stdout.splitlines()[1:] if n.strip()]  # 第一行是合出来的树
    return sorted({n for n in lines if not facts_path(n)}) if done.returncode == 1 else []


def conflicts(root):
    """Overlaps and trial merges between the work in progress and with the branch it merges into. See the module docstring."""
    home = main_checkout(root)
    base = target_branch(home)
    items = []
    mapping = None
    for w in in_progress(home):
        committed = _committed(home, base, w['branch']) if base else set()
        loose = _uncommitted(w['place'])
        item = dict(w, committed=sorted(committed), uncommitted=sorted(loose - committed),
                    base_conflicts=trial_merge(home, base, w['branch']) if base else None, outside=[])
        if w['kind'] == 'req' and w['state'] == 'open' and (committed | loose):
            # 开发中改到了「要改的功能点」以外的功能点的代码（知识保鲜，facts/fresh.py）：只提示
            if mapping is None:
                mapping = feature_files(home)[0]
            item['outside'] = outside_scope(home, owner_features(home, w['id']), committed | loose, mapping)
        items.append(item)
    pairs = []
    for a, b in combinations(items, 2):
        shared = sorted((set(a['committed']) | set(a['uncommitted'])) & (set(b['committed']) | set(b['uncommitted'])))
        if shared:
            both_committed = set(a['committed']) & set(b['committed'])
            pairs.append({'a': a['id'], 'b': b['id'], 'files': shared,
                          'conflicts': trial_merge(home, a['branch'], b['branch']) if both_committed else []})
    for i in items:
        i.pop('place', None)
    return {'base': base, 'items': items, 'pairs': pairs}


def conflicts_text(out):
    if not out['items']:
        return '没有进行中、有自己工作目录的需求或修复。'
    lines = [f'合并目标：{out["base"] or "（项目主目录不在任何分支上）"}']
    for i in out['items']:
        bad = i['base_conflicts']
        lines.append(f'- {i["id"]} {i["title"]}：改了 {len(i["committed"])} 个文件（已提交）' + (f'，另有 {len(i["uncommitted"])} 个没提交' if i['uncommitted'] else '')
                     + (f'；和 {out["base"]} 合并会冲突：{"、".join(bad)}' if bad else '；和合并目标合并不冲突' if bad == [] else ''))
        for o in i.get('outside') or []:
            lines.append(f'  改到了 {o["feature"]} 的代码（{"、".join(o["files"][:5])}），它不在要改的功能点里')
    for p in out['pairs']:
        lines.append(f'- {p["a"]} 和 {p["b"]} 都改了：{"、".join(p["files"][:8])}' + (' …' if len(p['files']) > 8 else '')
                     + (f'；两个分支合并会冲突：{"、".join(p["conflicts"])}' if p['conflicts'] else ''))
    if not out['pairs']:
        lines.append('需求之间没有改到同一个文件。')
    return '\n'.join(lines)


# ---------- 立项时预测 ----------

PREDICT_CAP = 20  # 预计会动到的文件最多列几个


def _features(sections):
    """The feature and module ids a requirement says it will change (要改的功能点)."""
    found = []
    for line in live_items(sections.get('要改的功能点')):
        for ident in ID_PATTERN.findall(line):
            if ident not in found:
                found.append(ident)
    return found


def predict(root, ident):
    """Which files this requirement will probably touch, judged from finished requirements that changed the same features,
    and which requirements in progress it may run into. See the module docstring."""
    home = main_checkout(root)
    mine, others = None, []
    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        meta, sections = read_doc(path)
        if not meta.get('id'):
            continue
        row = {'id': meta['id'], 'title': meta.get('title') or meta['id'], 'state': meta.get('state') or 'open', 'features': _features(sections)}
        if meta['id'] == ident:
            mine = row
        else:
            others.append(row)
    if not mine:
        raise ValueError(f'没有找到需求 {ident}。')
    wanted = set(mine['features'])
    counts, sources = {}, {}
    history = [r for r in others if r['state'] == 'done' and wanted & set(r['features'])]
    for r in history:
        for path in item_files(home, r['id']):
            if facts_path(path):
                continue
            counts[path] = counts.get(path, 0) + 1
            sources.setdefault(path, []).append(r['id'])
    present = set(run_git(home, 'ls-files').stdout.splitlines())  # 以前改过、后来删掉或改名了的文件不算
    ranked = sorted((p for p in counts if p in present), key=lambda p: (-counts[p], p))[:PREDICT_CAP]
    predicted = [{'path': p, 'count': counts[p], 'from': sources[p]} for p in ranked]
    likely = set(ranked)
    live = {i['id']: i for i in conflicts(home)['items']}
    against = []
    for r in others:
        if r['state'] not in ('draft', 'open'):
            continue
        shared = [f for f in r['features'] if f in wanted]
        item = live.get(r['id'])
        touched = sorted(likely & (set(item['committed']) | set(item['uncommitted']))) if item else []
        if shared or touched:
            against.append({'id': r['id'], 'title': r['title'], 'state': r['state'], 'features': shared, 'files': touched})
    return {'id': ident, 'features': mine['features'], 'history': [{'id': r['id'], 'title': r['title']} for r in history],
            'predicted': predicted, 'against': against}


def predict_text(out):
    lines = [f'{out["id"]} 要改的功能点：{"、".join(out["features"]) or "（还没写）"}']
    if out['history']:
        lines.append(f'以前改过同样功能点的需求：{"、".join(h["id"] for h in out["history"])}')
        lines.append('预计会动到的文件：' + '、'.join(f'{p["path"]}（{p["count"]} 次）' for p in out['predicted'][:10]) + (' …' if len(out['predicted']) > 10 else ''))
    else:
        lines.append('没有改过同样功能点的历史需求，预测不了会动到哪些文件。')
    for a in out['against']:
        why = [f'同样要改 {"、".join(a["features"])}'] if a['features'] else []
        if a['files']:
            why.append(f'已经在改预计会动到的 {"、".join(a["files"][:5])}')
        lines.append(f'- 可能撞上 {a["id"]}《{a["title"]}》：{"；".join(why)}')
    if not out['against']:
        lines.append('进行中的需求里没有会撞上的。')
    return '\n'.join(lines)
