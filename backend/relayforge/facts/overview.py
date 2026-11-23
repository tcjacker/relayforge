"""大图上的关系和变化：模块之间的依赖、事实文件的历史、分支相对主线改了什么。"""

import datetime as dt
import re
from pathlib import Path

from ..core.common import DIR, git


def module_edges(root, modules):
    """Which module's code imports which other module's code (Python files only)."""
    owner = {}
    for m in modules:
        for p in m.get('paths') or []:
            if str(p).endswith('.py'):
                owner.setdefault(Path(str(p)).stem, m['id'])
    edges, seen = [], set()
    if not owner:
        return edges
    pattern = re.compile(r'^\s*(?:import|from)\s+(' + '|'.join(re.escape(n) for n in owner) + r')\b', re.M)
    for m in modules:
        for p in m.get('paths') or []:
            src = root / str(p)
            if not str(p).endswith('.py') or not src.is_file() or owner.get(src.stem) != m['id']:
                continue
            for name in pattern.findall(src.read_text(encoding='utf-8', errors='replace')):
                pair = (m['id'], owner[name])
                if pair[0] != pair[1] and pair not in seen:
                    seen.add(pair)
                    edges.append({'from': pair[0], 'to': pair[1]})
    return edges


def file_history(root):
    """Commits that touched each fact file, newest first, from one pass over the log."""
    out, commit = {}, None
    for line in git(root, 'log', '--format=@%h|%ad|%s', '--date=short', '--name-only', '--', DIR).splitlines():
        if line.startswith('@') and line.count('|') >= 2:
            commit = dict(zip('hds', line[1:].split('|', 2)))
        elif line.strip() and commit:
            out.setdefault(line.strip(), []).append(commit)
    return out


def fact_delta(root, base, modules, features, decisions):
    """Per module: what this branch added or changed in the facts, compared with the main branch."""
    status = {}
    for line in git(root, 'diff', '--name-status', base, 'HEAD', '--', DIR).splitlines():
        parts = line.split('\t')
        if len(parts) >= 2:
            status[parts[-1]] = 'new' if parts[0].startswith('A') else 'changed'
    out = {}
    for m in modules:
        feats = [{'id': f['id'], 'title': f.get('title'), 'state': status[f['file']]} for f in features if f.get('module') == m['id'] and f['file'] in status]
        rules = [status[d['file']] for d in decisions if m['id'] in (d.get('applies_to') or []) and d['file'] in status]
        if m['file'] in status or feats or rules:
            out[m['id']] = {'state': status.get(m['file']), 'features': feats, 'newRules': rules.count('new'), 'changedRules': rules.count('changed')}
    return out


def branch_view(root, modules, hours=4):
    """What the current branch changed compared with the main branch."""
    view = {'base': None, 'baseEmpty': False, 'edges': module_edges(root, modules), 'branches': []}
    branch = git(root, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    base = next((b for b in ('origin/main', 'main', 'origin/master', 'master') if git(root, 'rev-parse', '--verify', '--quiet', b).strip()), None)
    if not branch or not base:
        return view
    view['base'], view['baseRef'] = base.split('/')[-1], base
    view['baseEmpty'] = not git(root, 'ls-tree', '--name-only', base, DIR).strip()
    commits = [dict(zip(('h', 'ts', 't', 's'), line.split('|', 3)))
               for line in git(root, 'log', '--format=%h|%at|%ad|%s', '--date=format:%m-%d %H:%M', f'{base}..HEAD').splitlines() if line.count('|') >= 3]
    if not commits:
        return view

    stamps = [int(c['ts']) for c in commits]
    start = dt.datetime.fromtimestamp(min(stamps)).replace(hour=0, minute=0, second=0).timestamp()
    bins = [0] * (int((max(stamps) - start) // (hours * 3600)) + 1)
    for stamp in stamps:
        bins[int((stamp - start) // (hours * 3600))] += 1
    groups = {}
    for c in commits:
        groups.setdefault(c['t'][:5], []).append(c)
    view['branches'].append({
        'name': branch, 'base': view['base'], 'ahead': len(commits), 'last': commits[0]['t'], 'root': str(root),
        'activity': {'bins': bins[-60:], 'hours': hours, 'from': dt.datetime.fromtimestamp(start).strftime('%m-%d'), 'to': commits[0]['t']},
        'groups': [{'date': day, 'commits': cs} for day, cs in groups.items()],
    })
    return view
