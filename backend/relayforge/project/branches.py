"""项目主目录的分支：列出本地与已获取的远端分支，并安全切换或创建本地跟踪分支（见 FORMAT.md 的「切换分支」）。

- 只列用户的本地分支。RelayForge 为需求、修复开的分支（需求文件头和修复记录里的 branch、签出在 .worktrees/ 下的，以及名字是 req/日期-编号、fix/f-编号的）不列。
- 每个分支的「待发布需求数」和顶栏、发布对话框同一个口径：分支有上游、上游在本地有记录时，算它领先上游的提交里带了几个确认完成的需求
  （publish._unpublished）。没有上游的分支不报数。
- 切换只作用于项目主目录；需求和修复的工作目录各自签出自己的分支，不受影响，开着的会话照常继续。
- 切换用 git switch，不加 --force：Git 拒绝时（会覆盖本地改动、分支在别的工作目录签出……），本地改动和当前分支原样不动，把原因翻成人话交回。
- 数据外置的项目：项目主目录的 .relayforge 是指向本机记录的链接（Git 忽略它）。目标分支上如果还提交着旧布局的 .relayforge/ 目录，
  Git 会把这个被忽略的链接换成那份旧文件，所以先拦下来。
- 切换是用户的操作（只由用户触发，见 trust.USER_COMMANDS）：AI 的会话不切项目主目录的分支。
"""

import re
from time import monotonic
from pathlib import Path

from ..core.common import DIR, TREES, git, is_external, main_checkout
from ..core.documents import read_doc
from ..requirements.owners import all_fixes
from .publish import _busy_kind, _ref_exists, _remotes, _run, _unpublished, _upstream, classify, meaningful


# RelayForge 起的分支名（requirements.py 的 req/<编号>、issues.py 的 fix/<修复编号>）：记录已经不在了的旧分支也按名字认出来
MANAGED_NAME = re.compile(r'^(req/\d{8}-[0-9a-f]{4}|fix/f-\d+)$')


class BranchError(Exception):
    def __init__(self, code, text, detail=''):
        super().__init__(text)
        self.code, self.detail = code, detail


def _local_branches(home):
    return [b for b in git(home, 'for-each-ref', '--format=%(refname:lstrip=2)', 'refs/heads/').splitlines() if b.strip()]


def _checkouts(home):
    """{branch: path} for every checkout of the repository other than the main one."""
    out, path = {}, None
    for line in git(home, 'worktree', 'list', '--porcelain', '-z').split('\0'):
        if line.startswith('worktree '):
            path = Path(line[9:])
        elif line.startswith('branch refs/heads/') and path is not None:
            out[line[len('branch refs/heads/'):]] = path
    home = Path(home).resolve()
    return {b: p for b, p in out.items() if p.resolve() != home}


def managed_branches(home):
    """The branches RelayForge opened for requirements and fixes: hidden from the list, never switched to."""
    home = Path(home)
    names = set()
    for folder in ('requirements', 'history'):
        for path in (home / DIR / folder).glob('*.md'):
            try:
                meta, _ = read_doc(path)
            except (OSError, ValueError):
                continue
            if meta.get('type') in (None, 'requirement') and meta.get('branch'):
                names.add(str(meta['branch']))
    for fix in all_fixes(home).values():
        if fix.get('branch'):
            names.add(str(fix['branch']))
    trees = (home / TREES).resolve()
    for branch, path in _checkouts(home).items():
        try:
            path.resolve().relative_to(trees)
        except ValueError:
            continue
        names.add(branch)
    names.update(b for b in _local_branches(home) if MANAGED_NAME.match(b))
    return names


def _current(home):
    return git(home, 'symbolic-ref', '--quiet', '--short', 'HEAD').strip() or None


def _pending(home, name, up):
    """Use the same comparison and requirement attribution as the publish indicator."""
    if not up:
        return 0, [], 0
    ref = f'refs/remotes/{up[0]}/{up[1]}'
    spec = [f'refs/heads/{name}', '--not']
    spec.append(ref if _ref_exists(home, ref) else f'--remotes={up[0]}')
    un = _unpublished(home, spec)
    return un['total'], un['requirements'], un['others_total']


def _old_layout(home, ref):
    return is_external(home) and bool(git(home, 'ls-tree', '--name-only', ref, '--', DIR).strip())


def _old_layout_error(name):
    return (f'{name} 上还提交着旧的 .relayforge/ 目录。切过去会覆盖本机记录链接。'
            '先在终端里到那个分支上删掉旧目录并提交，再来切换。')


def _elsewhere_error(name, path):
    return f'{name} 已经在另一个工作目录（{path}）签出，Git 不让同一个分支签出两处。先在那边切走，再来切换。'


def _name_taken(name, names):
    return any(n == name or n.startswith(name + '/') or name.startswith(n + '/') for n in names)


def _remote_branches(home, remotes, hidden):
    prefixes = [remote + '/' for remote in sorted(remotes, key=len, reverse=True)]
    refs = git(home, 'for-each-ref', '--format=%(refname:lstrip=2)%00%(symref)', 'refs/remotes/')
    for line in refs.splitlines():
        name, _, symref = line.partition('\0')
        prefix = next((p for p in prefixes if name.startswith(p)), None)
        if not prefix or symref:
            continue
        short = name[len(prefix):]
        if short not in hidden and not MANAGED_NAME.fullmatch(short):
            yield name, (prefix[:-1], short)


def _tracking_branch(name, short, tracking, current):
    return min((b for b, upstream in tracking.items() if upstream == name),
               key=lambda b: (b != current, b != short, b), default=None)


def branch_list(root):
    home = main_checkout(root)
    current = _current(home)
    hidden = managed_branches(home)
    elsewhere = _checkouts(home)
    names = _local_branches(home)
    remotes = _remotes(home)
    tracking = {}
    rows = []
    for name in names:
        if name in hidden:
            continue
        up = _upstream(home, name)
        if up and up[0] not in remotes:
            up = None
        upstream = '/'.join(up) if up else None
        tracking[name] = upstream
        ahead, reqs, others = _pending(home, name, up)
        blocked = None
        if name in elsewhere:
            blocked = _elsewhere_error(name, elsewhere[name])
        elif name != current and _old_layout(home, f'refs/heads/{name}'):
            blocked = _old_layout_error(name)
        rows.append({'name': name, 'current': name == current, 'upstream': upstream, 'ahead': ahead,
                     'pending': len(reqs), 'requirements': reqs, 'others': others, 'blocked': blocked})
    rows.sort(key=lambda row: (not row['current'], row['name'].lower()))
    now = next((row for row in rows if row['current']), None)
    if now is None and current:
        up = _upstream(home, current)
        if up and up[0] not in remotes:
            up = None
        _, reqs, _ = _pending(home, current, up)
        now = {'name': current, 'pending': len(reqs), 'upstream': '/'.join(up) if up else None}
    remote_rows = [
        {'name': name, 'short_name': short, 'tracking': _tracking_branch(name, short, tracking, current),
         'name_taken': _name_taken(short, names)}
        for name, (_, short) in _remote_branches(home, remotes, hidden)
    ]
    return {'root': str(home), 'current': current, 'detached': current is None, 'busy': _busy_kind(home),
            'branches': rows, 'remotes': remote_rows, 'has_remotes': bool(remotes),
            'pending': now['pending'] if now else 0, 'upstream': now['upstream'] if now else None}


def branch_refresh(root, timeout=35):
    home = main_checkout(root)
    errors = []
    deadline = monotonic() + timeout
    for remote in _remotes(home):
        remaining = deadline - monotonic()
        if remaining <= 0:
            errors.append(f'{remote}：刷新超时。')
            continue
        # Each fetch updates its refs atomically and retains the last known refs if a remote is unavailable.
        code, _, err = _run(home, ['fetch', '--atomic', '--no-prune', '--no-prune-tags', '--no-tags',
                                   '--no-recurse-submodules', '--', remote], min(30, remaining))
        if code:
            kind, words = classify(home, err, pushing=False)
            if kind == 'failed':
                words = '抓取远端没有成功。'
            errors.append(f'{remote}：{words}（Git：{meaningful(err)}）')
        elif code is None:
            errors.append(f'{remote}：刷新超时，远端没有及时回应。')
    return {**branch_list(home), 'ok': not errors, 'refresh_error': '\n'.join(errors)}


def _files_in(err):
    return [line.strip() for line in err.splitlines() if line.startswith('\t') and line.strip()]


def _explain(home, name, err, timed_out=False):
    text = err.lower()
    if timed_out:
        return 'timeout', '切换等了太久，已经停下。当前分支和本地改动没有动。', ''
    if 'would be overwritten by checkout' in text:
        files = _files_in(err)
        if 'untracked working tree files' in text:
            return 'untracked', (f'项目目录里有 {len(files)} 个还没加入 Git 的文件，{name} 上有同名的文件，切过去会把它们盖掉，Git 没有切换。'
                                 '先把这些文件挪开或者提交，再来切换。当前分支和本地改动都没有动。'), '、'.join(files[:5])
        return 'dirty', (f'项目目录里有 {len(files)} 个文件的改动还没提交，{name} 上这些文件不一样，切过去会覆盖这些改动，Git 没有切换。'
                         '先提交或暂存（git stash）这些改动，再来切换。当前分支和本地改动都没有动。'), '、'.join(files[:5])
    if 'already checked out' in text or 'already used by worktree' in text:
        return 'elsewhere', f'{name} 已经在另一个工作目录签出，Git 不让同一个分支签出两处。先在那边切走，再来切换。', meaningful(err)
    if 'you need to resolve your current index first' in text or 'unmerged' in text:
        return 'busy', '项目目录里还有没解决的冲突，Git 没有切换。先把冲突解决、提交，再来切换。', meaningful(err)
    if 'index.lock' in text:
        return 'locked', '另一个 Git 命令正在用这个仓库，Git 没有切换。等它结束再试一次。', meaningful(err)
    return 'failed', 'Git 没有切换分支。当前分支和本地改动都没有动。', meaningful(err)


def branch_switch(root, name, timeout=60, *, remote=False, local_name=None,
                  expected_current=None, confirmed_pending=None, expected_tracking=None):
    home = main_checkout(root)
    name = (name or '').strip()
    if not name:
        raise BranchError('usage', '要给出切到哪个分支：rf.py branch switch 分支名')
    if name.startswith('-'):
        raise BranchError('missing', f'没有叫 {name} 的本地分支。')
    current = _current(home)
    hidden = managed_branches(home)
    names = _local_branches(home)
    remotes = _remotes(home)
    creating = False
    if remote:
        target = dict(_remote_branches(home, remotes, hidden)).get(name)
        if not target:
            raise BranchError('missing', f'找不到已获取的远端分支 {name}。请刷新列表后再试。')
        _, short = target
        tracking = {b: '/'.join(_upstream(home, b) or ()) for b in names if b not in hidden}
        tracked = _tracking_branch(name, short, tracking, current)
        if expected_tracking is not None and tracked != expected_tracking:
            raise BranchError('stale', f'{name} 的本地跟踪分支已变化，请重新选择。')
        if local_name is not None and tracked:
            raise BranchError('stale', f'{name} 已有本地跟踪分支 {tracked}，请重新选择。')
        creating = tracked is None
        dest = (local_name if local_name is not None else short) if creating else tracked
        if creating:
            if not dest or dest.startswith('-') or git(home, 'check-ref-format', '--branch', dest).strip() != dest:
                raise BranchError('invalid-name', '请输入有效的本地分支名。')
            if _name_taken(dest, names):
                raise BranchError('name-taken', f'本地名称 {dest} 已占用，请输入另一个名称。')
    else:
        dest = name
        if dest not in names:
            raise BranchError('missing', f'没有叫 {dest} 的本地分支。')

    if dest in hidden or MANAGED_NAME.fullmatch(dest):
        raise BranchError('managed', f'{dest} 是 RelayForge 为需求或修复保留的分支名。')
    if expected_current is not None and (current or '') != expected_current:
        raise BranchError('stale', '项目当前分支已变化，请重新选择并确认。')
    if dest == current and not creating:
        return {'ok': True, 'from': current, 'to': dest, 'changed': False}
    if confirmed_pending is not None and current:
        up = _upstream(home, current)
        _, reqs, _ = _pending(home, current, up if up and up[0] in remotes else None)
        if len(reqs) != confirmed_pending:
            raise BranchError('stale', '当前分支的待发布需求数已变化，请重新选择并确认。')

    busy = _busy_kind(home)
    if busy:
        raise BranchError('busy', {'merge': '项目目录里有一次合并还没做完，先把它做完再来切换。',
                                   'rebase': '项目目录里有一次变基还没做完，先把它做完再来切换。'}.get(busy, '项目目录里有一次拣选或回退还没做完，先处理完再来切换。'))
    elsewhere = _checkouts(home)
    if dest in elsewhere:
        raise BranchError('elsewhere', _elsewhere_error(dest, elsewhere[dest]))
    start = f'refs/remotes/{name}' if creating else f'refs/heads/{dest}'
    if _old_layout(home, start):
        raise BranchError('old-layout', _old_layout_error(name))
    args = ['switch', '--no-guess', '--no-overwrite-ignore']
    # Git creates the branch and its tracking config only when the checkout succeeds.
    args += ['--track', '-c', dest, '--', start] if creating else ['--', dest]
    code, _, err = _run(home, args, timeout)
    result = {'ok': True, 'from': current, 'to': dest, 'changed': True}
    if code != 0:
        # A post-checkout hook can fail after Git has already updated HEAD.
        if _current(home) == dest:
            result['warning'] = '切换已完成，Git 在收尾时返回：' + meaningful(err)
        else:
            kind, text, detail = _explain(home, dest, err, timed_out=code is None)
            raise BranchError(kind, text, detail)
    return result
