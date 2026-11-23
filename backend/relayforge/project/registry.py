"""这台电脑上登记过的项目。"""

import datetime as dt
import json
import re
from pathlib import Path

from ..core.common import git, is_project, link_facts, registry_path, today, worktrees
from ..sessions.record import load_sessions


def read_registry():
    try:
        data = json.loads(registry_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [p for p in data.get('projects', []) if isinstance(p, dict) and p.get('root') and p.get('name')]


def projects():
    """Registered projects whose directory still holds a .relayforge."""
    return [p for p in read_registry() if is_project(Path(p['root']))]


def project_of(root):
    """The registered project that `root` is, or is a worktree of."""
    root = Path(root).resolve()
    for entry in projects():
        base = Path(entry['root'])
        if root == base or root in worktrees(base):
            return entry
    return None


def register(root):
    """Remember this project so that one local server can show all of them. Worktrees join their repository's entry."""
    root = Path(root).resolve()
    link_facts(root)  # 数据外置的项目：重新克隆或挪了位置以后，按项目 ID 接回本机那份数据
    found = project_of(root)
    if found:
        return found
    first = next((Path(line[9:]) for line in git(root, 'worktree', 'list', '--porcelain').splitlines() if line.startswith('worktree ')), None)
    if first and is_project(first):
        root = first.resolve()
    entries = read_registry()
    taken = {p['name'] for p in entries}
    base = re.sub(r'[\\/?#%\s]+', '-', root.name).strip('-') or 'project'
    name, n = base, 2
    while name in taken:
        name, n = f'{base}-{n}', n + 1
    entry = {'name': name, 'root': str(root), 'added': today()}
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix('.tmp')
    scratch.write_text(json.dumps({'projects': [p for p in entries if p['root'] != str(root)] + [entry]}, ensure_ascii=False, indent=2), encoding='utf-8')
    scratch.replace(path)
    return entry


def project_summaries(current=None):
    """One line per project for the switcher: how many sessions are under way and when one last synced."""
    out, week = [], dt.datetime.now() - dt.timedelta(days=7)
    for entry in projects():
        sessions = load_sessions(Path(entry['root']))
        active = 0
        for x in sessions:
            try:
                recent = dt.datetime.strptime(str(x.get('updated')), '%Y-%m-%d %H:%M') > week
            except ValueError:
                recent = False
            active += 1 if recent and not x.get('ended') else 0
        out.append({'name': entry['name'], 'root': entry['root'], 'active': active, 'sessions': len(sessions),
                    'last': str(sessions[0].get('updated') or '') if sessions else '', 'current': entry['name'] == current})
    return sorted(out, key=lambda x: x['last'], reverse=True)
