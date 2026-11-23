"""归属：一个工作目录、一个分支属于哪个需求或修复，它还在不在进行，它碰了哪些功能点。

只读需求文件和修复记录，不改它们。requirements.py、issues.py、testsets.py、trailers.py 都要问这件事；
放在这里，它们就不必为了这几个读取互相导入。分层见 tests/test_layers.py。
"""
import json
import re
from pathlib import Path

from ..core.common import DIR, branch_of, git, main_checkout
from ..core.documents import live_items, read_doc
from ..facts.inflight import brought_by

ISSUES = f'{DIR}/issues'

__all__ = ['ISSUES', 'issue_dir', 'norm_id', 'num', 'find_issue', 'fix_dir', 'read_fix', 'all_fixes', 'fix_of_branch', 'here_fix',
           'branch_places', 'own_requirement', 'requirement_path', 'here_owner', 'owner_going', 'owner_place', 'owner_features']


# ---------- 问题和修复的记录 ----------

def issue_dir(home):
    return Path(home) / ISSUES


def norm_id(ident):
    """I-033, #033, 033 and 33 all name the same issue."""
    text = str(ident or '').strip().lstrip('#')
    m = re.fullmatch(r'(?:I-)?0*(\d+)', text, re.I)
    if not m:
        raise ValueError(f'「{ident}」不是问题编号，写成 #033 或 I-033。')
    return f'I-{int(m.group(1)):03d}'


def num(ident):
    return '#' + ident[2:]


def find_issue(home, ident):
    ident = norm_id(ident)
    for path in sorted(issue_dir(home).glob(f'{ident}-*.md')) + sorted(issue_dir(home).glob(f'{ident}.md')):
        meta, sections = read_doc(path)
        if meta.get('id') == ident:
            return path, meta, sections
    raise ValueError(f'没有找到问题 {num(ident)}。')


def fix_dir(home):
    return issue_dir(home) / 'fixes'


def read_fix(home, ident):
    try:
        data = json.loads((fix_dir(home) / f'{ident}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('id') == ident else None


def all_fixes(home):
    out = {}
    for path in sorted(fix_dir(home).glob('F-*.json')):
        fix = read_fix(home, path.stem)
        if fix:
            out[fix['id']] = fix
    return out


def fix_of_branch(home, branch):
    return next((f for f in all_fixes(home).values() if f.get('branch') == branch), None)


def here_fix(root):
    """The fix this checkout belongs to (by its branch), if any."""
    return fix_of_branch(main_checkout(root), branch_of(root))


# ---------- 需求 ----------

def branch_places(home):
    """Each checked-out branch and the working directory it is in (the main checkout included)."""
    out, place = {}, None
    for line in git(home, 'worktree', 'list', '--porcelain').splitlines():
        if line.startswith('worktree '):
            place = Path(line[9:]).resolve()
        elif line.startswith('branch ') and place:
            out[line[7:].replace('refs/heads/', '', 1)] = place
    return out


def own_requirement(root):
    """The requirement whose branch this checkout has out, if any: (file in the main checkout, meta, sections)."""
    here = branch_of(root)
    for path in sorted((main_checkout(root) / DIR / 'requirements').glob('*.md')):
        meta, sections = read_doc(path)
        if meta.get('branch') == here and meta.get('id'):
            return path, meta, sections
    return None


# ---------- 谁在做：需求、修复，还是主线 ----------

def requirement_path(home, ident):
    return next(iter(sorted((Path(home) / DIR / 'requirements').glob(f'{ident}-*.md'))), None) or \
        next((p for p in sorted((Path(home) / DIR / 'requirements').glob('*.md')) if read_doc(p)[0].get('id') == ident), None)


def here_owner(root):
    """The requirement or fix whose working directory this is, while it is still going; None on the main line."""
    mine = own_requirement(root)
    if mine and (mine[1].get('state') or 'open') in ('draft', 'open'):
        return mine[1]['id']
    fix = here_fix(root)
    if fix and fix.get('state') == 'fixing':
        return fix['id']
    return None


def owner_going(home, owner):
    """Whether a requirement or fix is still in progress (its data and changes are not landed yet)."""
    if not owner:
        return False
    if str(owner).startswith('F-'):
        fix = read_fix(home, owner)
        return bool(fix) and fix.get('state') == 'fixing'
    path = requirement_path(home, owner)
    return bool(path) and (read_doc(path)[0].get('state') or 'open') in ('draft', 'open')


def owner_place(home, owner):
    """The working directory a requirement or fix has, if it still has one."""
    if not owner:
        return None
    if str(owner).startswith('F-'):
        fix = read_fix(home, owner) or {}
        place = fix.get('worktree')
    else:
        path = requirement_path(home, owner)
        place = branch_places(home).get(str(read_doc(path)[0].get('branch') or '')) if path else None
    return Path(place) if place and Path(place).is_dir() and Path(place).resolve() != Path(home).resolve() else None


def owner_features(home, owner):
    """The features a requirement or fix touches: named in 要改的功能点, new from it, changed by it; for a fix, its issues' features."""
    found = []
    if not owner:
        return found
    if str(owner).startswith('F-'):
        for one in (read_fix(home, owner) or {}).get('issues') or []:
            try:
                feature = find_issue(home, one)[1].get('feature')
            except ValueError:
                continue
            if feature:
                found.append(str(feature))
        return list(dict.fromkeys(found))
    path = requirement_path(home, owner)
    if path:
        _, sections = read_doc(path)
        for line in live_items(sections.get('要改的功能点')):
            found += re.findall(r'\bRF-\d+\b', line)
    found += [str(meta['id']) for kind, _, meta, _, _ in brought_by(home, owner) if kind == 'features']
    return list(dict.fromkeys(found))
