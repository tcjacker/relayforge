"""只读页面：把事实收集起来，写成 .relayforge/site/index.html。页面本身在 templates/page.html。"""

import datetime as dt
import json
import os
from pathlib import Path

from ..core.common import DIR, PENDING, PLACEHOLDER, TEMPLATES, git, main_checkout, today
from ..core.documents import pending_groups, read_doc
from .designs import current_designs
from .mapfacts import layer_of, load_journeys, load_layers, relations
from .overview import branch_view, fact_delta, file_history
from ..sessions.record import load_sessions
from .inflight import in_progress_of, inflight_delta


PAGE = (TEMPLATES / 'page.html').read_text(encoding='utf-8')


def collect(root):
    """Gather everything under .relayforge/ that the read-only page shows: the main checkout's, the only copy."""
    root = main_checkout(root)
    base = root / DIR

    def load(kind, folder=None):
        out = []
        for path in sorted((base / (folder or kind)).glob('*.md')):
            meta, sections = read_doc(path)
            if meta.get('id'):
                out.append(dict(meta, sections=sections, file=f'{DIR}/{folder or kind}/{path.name}', inProgress=in_progress_of(meta, sections)))
        return out

    modules = sorted(load('modules'), key=lambda m: m.get('order') or 0)
    features = sorted(load('features'), key=lambda f: f.get('order') or 0)
    documents = load('documents')
    open_path = base / 'product' / 'open.md'
    open_lines = [l for l in read_doc(open_path)[1].get('还没定的事', []) if l != PLACEHOLDER] if open_path.exists() else []
    pending = []
    journeys = load_journeys(root)
    for doc in modules + features + load('decisions') + documents + journeys:
        for lines in doc['sections'].values():
            pending += [{'id': doc['id'], 'line': line} for line in lines if PENDING in line]
    runs = []
    for path in sorted((base / 'runs').rglob('*.md'), reverse=True)[:200]:
        text = path.read_text(encoding='utf-8')
        meta = read_doc(path)[0]
        runs.append({'session': meta.get('session'), 'agent_type': meta.get('agent_type'), 'ended_at': meta.get('ended_at'),
                     'text': text.split('## 回报原文', 1)[-1].strip()[:6000]})
    merged = sorted((base / 'inbox' / 'merged').glob('*.json'))
    last = None
    if merged:
        try:
            record = json.loads(merged[-1].read_text(encoding='utf-8'))
            last = {'captured_at': record.get('captured_at', ''), 'session': record.get('session', ''), 'items': len(record.get('items', []))}
        except ValueError:
            pass
    known = {d.get('path') for d in documents}
    loose = []
    for folder, names, files in os.walk(root):
        names[:] = [n for n in names if not n.startswith('.') and n not in ('node_modules', '__pycache__')]
        for name in files:
            rel = (Path(folder) / name).relative_to(root).as_posix()
            if name.endswith('.md') and rel not in known and rel not in ('AGENTS.md', 'CLAUDE.md'):
                loose.append(rel)
    overview_path = base / 'product' / 'overview.md'
    overview = read_doc(overview_path)[1] if overview_path.exists() else None
    history = file_history(root)
    gitlog = {doc['id']: history.get(doc['file'], []) for doc in modules + features + journeys + load('decisions') + load('history')}
    feature_open = sum(1 for f in features for l in f['sections'].get('还没定的事', []) if l != PLACEHOLDER)
    sessions = load_sessions(root)
    now = branch_view(root, modules)
    decisions = load('decisions')
    for branch in now['branches']:  # the main checkout's own branch: its facts compared with the main line
        branch.pop('root')
        branch['delta'] = fact_delta(root, now['baseRef'], modules, features, decisions)
        branch['gitlog'] = {doc['id']: history.get(doc['file'], []) for doc in modules + features + decisions + load('history')}
    # requirement worktrees: their code activity from their branch, and what they bring from the facts in the main checkout,
    # which are marked as theirs (in_progress, 〔进行中 R-…〕); their own copy of .relayforge/ is stale and not read
    owners = {str(r.get('branch')): r['id'] for r in load('requirements') if r.get('branch')}
    place = None
    for line in git(root, 'worktree', 'list', '--porcelain').splitlines():
        if line.startswith('worktree '):
            place = Path(line[9:]).resolve()
            continue
        if not line.startswith('branch ') or not place or place == Path(root).resolve():
            continue
        for branch in branch_view(place, modules)['branches']:
            if branch['name'] in {b['name'] for b in now['branches']}:
                continue
            branch.pop('root')
            ident = owners.get(branch['name'])
            if not ident and branch['name'].startswith('req/'):
                continue  # left over from the old layout: its requirement only exists on its branch, not shown
            branch.update(worktree=place.name, requirement=ident, delta=inflight_delta(modules, features, decisions, ident) if ident else {},
                          gitlog={doc['id']: history.get(doc['file'], []) for doc in modules + features + decisions})
            now['branches'].append(branch)
    drawn = current_designs(root)
    shown = {}
    for view in drawn.values():
        shown[view['id']] = {k: view[k] for k in ('id', 'title', 'features', 'requirement', 'adopted', 'changed')}
        shown[view['id']]['html'] = view['html'][len(DIR) + 1:]  # relative to .relayforge/
    return {'project': root.name, 'generated': today(),
            'designs': list(shown.values()), 'featureDesigns': {fid: v['id'] for fid, v in drawn.items()}, 'designBase': '../', 'generatedAt': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'last': last, 'modules': modules, 'features': features, 'decisions': load('decisions'),
            'relations': relations(modules), 'journeys': journeys, 'layers': load_layers(root, modules), 'moduleLayer': {m['id']: layer_of(m) for m in modules},
            'history': load('history'), 'documents': documents, 'overview': overview,
            'sessions': sessions, 'now': now, 'pendingGroups': pending_groups(root), 'gitlog': gitlog, 'open': open_lines, 'openCount': len(open_lines) + feature_open,
            'pending': pending, 'runs': runs, 'unregistered': sorted(loose)[:300]}


def render(root):
    """Write the read-only page. The file is a page fragment that browsers open directly."""
    root = main_checkout(root)
    data = json.dumps(collect(root), ensure_ascii=False).replace('</', '<\\/')
    out = root / DIR / 'site' / 'index.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.replace('__DATA__', data), encoding='utf-8')
    return out
