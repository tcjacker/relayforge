"""发布：用户在应用里把项目目录当前分支推送到远端，推送之前先看清楚有什么要出去。

确认完成、确认修好了之后，代码只合并进了项目目录当前分支，没有推送；有的服务一推送就会触发部署，所以推送只由用户做，
AI 不能做（应用启动的会话里 git push 和 rf.py publish run / pull 都被禁止）。

  publish status [--json] [--fetch] [--remote R]  现在的分支、上游、领先落后几个提交、还没发布的有什么、挡住的和要注意的
  publish run [--remote R] [--json]               用户：先提交攒着的事实，写好发布记录并提交，再推送当前分支；不强推、不推标签、不推别的分支
  publish pull [--json]                           用户：远端有新提交时，抓取后只做快进；做不了快进就什么都不动
  publish list [--json]                           以前的发布记录，新的在前

哪些提交算「没发布」：当前分支有上游就是 上游..HEAD；还没有上游就是「不在这个远端任何分支上的提交」（--not --remotes=远端）。
哪个需求、哪个修复带来了哪些提交：确认完成（req done）和确认修好了（issue confirm）合并时，把合并提交的编号记在需求文件头的
merge_commit 和修复记录 issues/fixes/F-xxx.json 的 merge_commit 里；合并提交的第二个父提交一路上的提交（需求分支上的）都算它的。
这些字段出现之前完成的，按合并提交的标题（合并需求：……（R-…）、合并修复：……（F-…））找；没有合并的（只带来事实的需求、没有改动的修复），
按「第一次把 state: done 写进文件的那个提交」或「……已解决（F-…）」的事实提交找。不是合并、只动 .relayforge/ 的提交算「程序自己的事实提交」，只数个数。

发布记录（.relayforge/publishes/P-日期-随机4位-分支.md，文件头 type: publish）：推送之前写好并和事实一起提交，随这次推送一起出去，
所以推送成功以后没有任何新提交，未发布数回到 0。记录里不写「已发布」：是否已发布由程序判断——写它的那个提交是不是已经在远端分支里。
推送没成功时记录留在本地、还没发布；下一次发布先找这样的记录（同一个远端、同一个目标分支），更新它的内容再提交，不会越堆越多。
"""

import datetime as dt
import os
import re
import secrets
import signal
import subprocess
from pathlib import Path

from ..core.common import DIR, commit_facts, git, git_busy, is_external, main_checkout, slug, today
from ..core.documents import add_line, read_doc, write_doc
from ..requirements.owners import all_fixes, num
from ..issues.record import fix_issues, issue_files
from ..requirements.record import gate, plan_progress, stage_of
from ..sessions.record import load_sessions
from ..testsets.store import all_testsets, full_status

__all__ = ['PublishError', 'publish_status', 'publish_run', 'publish_pull', 'publish_list', 'PUBLISH_CODES']

FOLDER = 'publishes'
HEADS = ['包含的需求', '包含的问题', '其他提交', '变更记录']
FETCH_TIMEOUT, PUSH_TIMEOUT, LIST_CAP = 30, 120, 200
# 命令的退出码：0 成功；1 现在不能做（有挡住的事、用法不对）；下面这些是推送或抓取本身没成
PUBLISH_CODES = {'rejected': 10, 'auth': 11, 'network': 12, 'hook': 13, 'remote-declined': 14, 'failed': 15, 'diverged': 16, 'timeout': 12}


class PublishError(Exception):
    def __init__(self, code, text, detail='', **extra):
        super().__init__(text)
        self.code, self.detail, self.extra = code, detail, extra


# ---------- 运行 git：不弹口令、有超时、不挂在终端上 ----------

def _env(home):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', LC_ALL='C', LANG='C')
    env.pop('GIT_ASKPASS', None)
    env.pop('SSH_ASKPASS', None)
    if 'GIT_SSH_COMMAND' not in env:  # ssh 不许问口令；用户自己配了 core.sshCommand 就在它后面加
        mine = git(home, 'config', '--get', 'core.sshCommand').strip()
        env['GIT_SSH_COMMAND'] = (mine or 'ssh') + ' -o BatchMode=yes'
    return env


def _run(home, args, timeout=30):
    """(returncode, stdout, stderr); returncode None when it ran out of time (and was killed)."""
    proc = subprocess.Popen(['git', '-C', str(home), '-c', 'core.quotepath=off', *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=_env(home), start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, AttributeError):
            proc.kill()
        out, err = proc.communicate()
        return None, out or '', err or ''
    return proc.returncode, out, err


def meaningful(text):
    """The last line of git's output that says something: not blank, not a hint, not the generic 'failed to push some refs'."""
    lines = [l.strip() for l in (text or '').splitlines() if l.strip()]
    useful = [l for l in lines if not l.startswith('hint:') and 'failed to push some refs' not in l and not l.startswith('To ')]
    return (useful or lines or ['git 没有给出原因'])[-1]


def classify(home, text, timed_out=False, pushing=True):
    """What went wrong, from git's output: (code, plain words)."""
    low = (text or '').lower()
    if timed_out:
        return 'timeout', '等了很久远端都没有回应，已经停下。检查网络后再试。'
    if any(x in low for x in ('non-fast-forward', 'fetch first', 'stale info', 'tip of your current branch is behind', 'updates were rejected because')):
        return 'rejected', '远端这个分支上有本地没有的提交，推送被拒绝了。没有强推，也没有改动任何东西。先把远端的新提交拉下来。'
    if any(x in low for x in ('authentication failed', 'permission denied', 'could not read username', 'could not read password', 'terminal prompts disabled',
                              'invalid username or password', 'access denied', 'the requested url returned error: 403', 'the requested url returned error: 401',
                              'host key verification failed', 'permission to ')):
        return 'auth', '远端不让进：没有登录，或者没有权限。程序不会弹出口令框，请先在终端里把凭据配好（比如 ssh 密钥、凭据管理器），再回来发布。'
    if any(x in low for x in ('pre-receive hook declined', 'update hook declined', 'remote rejected', 'protected branch', 'hook declined')):
        return 'remote-declined', '远端的规则拒绝了这次推送（比如分支受保护，或远端的检查没通过）。'
    if any(x in low for x in ('could not resolve host', 'unable to access', 'connection refused', 'connection timed out', 'network is unreachable', 'could not read from remote',
                              'operation timed out', 'connection reset', 'unable to connect', 'does not appear to be a git repository', 'repository not found', 'no route to host')):
        return 'network', '连不上远端：网络不通，或者远端地址不对。检查网络和远端地址后再试。'
    if pushing and _has_hook(home, 'pre-push'):
        return 'hook', '推送前的检查（项目里的 pre-push 钩子）没有通过，没有推送。'
    return 'failed', '推送没有成功。'


def _has_hook(home, name):
    found = git(home, 'rev-parse', '--git-path', f'hooks/{name}').strip()
    path = (Path(home) / found) if found else None
    return bool(path and path.is_file() and os.access(path, os.X_OK))


# ---------- 现在的状态 ----------

def _busy_kind(home):
    """What is half done in the main checkout: merge, rebase, pick (cherry-pick or revert), or None."""
    if not git_busy(home):
        return None
    def there(name):
        found = git(home, 'rev-parse', '--git-path', name).strip()
        return bool(found) and (Path(home) / found).exists()
    if there('rebase-merge') or there('rebase-apply'):
        return 'rebase'
    return 'merge' if there('MERGE_HEAD') else 'pick'


def _divergence(home, ref, cap=20):
    """Where the two sides touched the same files since they split: {both: [...], facts_both: [...] (under .relayforge/), total}.
    Only a hint for whoever merges; git decides what really conflicts."""
    base = git(home, 'merge-base', 'HEAD', ref).strip()
    if not base:
        return {'both': [], 'facts_both': [], 'total': 0}
    mine = set(git(home, 'diff', '--name-only', base, 'HEAD').splitlines())
    theirs = set(git(home, 'diff', '--name-only', base, ref).splitlines())
    both = sorted(f.strip('"') for f in mine & theirs if f.strip())
    facts = [f for f in both if _facts_path(f)]
    return {'both': [f for f in both if not _facts_path(f)][:cap], 'facts_both': facts[:cap], 'total': len(both)}


def _facts_path(name):
    return name.strip('"').startswith(DIR + '/')


def _remotes(home):
    return [r for r in git(home, 'remote').split() if r]


def _upstream(home, branch):
    """(remote, branch on the remote) the current branch tracks, or None. An upstream that is a local branch does not count."""
    remote = git(home, 'config', '--get', f'branch.{branch}.remote').strip()
    merge = git(home, 'config', '--get', f'branch.{branch}.merge').strip()
    if not remote or remote == '.' or not merge.startswith('refs/heads/'):
        return None
    return remote, merge[len('refs/heads/'):]


def _ref_exists(home, ref):
    return bool(git(home, 'rev-parse', '--verify', '-q', ref).strip())


def _count(home, *spec):
    out = git(home, 'rev-list', '--count', *spec).strip()
    return int(out) if out.isdigit() else 0


def _choose_target(home, branch, remotes, upstream, chosen):
    """Where a publish would push: {remote, branch, creates, sets_upstream} or None (none, or several to pick from)."""
    if chosen and chosen not in remotes:
        raise PublishError('usage', f'没有叫 {chosen} 的远端。现有的远端：' + ('、'.join(remotes) or '没有') + '。')
    if upstream:
        if chosen and chosen != upstream[0]:
            raise PublishError('usage', f'当前分支的上游是 {upstream[0]}/{upstream[1]}，不能改推到 {chosen}。要换远端，先在终端里改上游。')
        remote, dest = upstream
        return {'remote': remote, 'branch': dest, 'creates': not _ref_exists(home, f'refs/remotes/{remote}/{dest}'), 'sets_upstream': False}
    remote = chosen or (remotes[0] if len(remotes) == 1 else None)
    if not remote:
        return None
    return {'remote': remote, 'branch': branch, 'creates': not _ref_exists(home, f'refs/remotes/{remote}/{branch}'), 'sets_upstream': True}


def _read_commits(home, spec, cap=2000):
    out = git(home, 'log', '-n', str(cap), '--format=%x01%H%x02%P%x02%s', '--name-only', *spec)
    commits = []
    for chunk in out.split('\x01')[1:]:
        head, _, files = chunk.partition('\n')
        h, parents, subject = (head.split('\x02') + ['', '', ''])[:3]
        commits.append({'hash': h, 'parents': parents.split(), 'subject': subject, 'files': [f for f in files.splitlines() if f.strip()]})
    return commits


def landing_commits(home, hashes, grep, newest_state_file=None):
    """Commits that stand for a finished requirement or fix, in order of preference (see the module docstring)."""
    found = [h for h in hashes if h and git(home, 'cat-file', '-t', h).strip() == 'commit']
    if found:
        return found
    found = git(home, 'log', '--merges', '--format=%H', '-F', f'--grep=（{grep}）').split()
    if found:
        return found
    if newest_state_file:
        pick = git(home, 'log', '--format=%H', '-S', newest_state_file[1], '--', newest_state_file[0]).split()
        return pick[-1:]  # the oldest one: the commit that first wrote it
    pick = git(home, 'log', '--format=%H', '-F', f'--grep=已解决（{grep}）').split()
    return pick[-1:]


def _carried(home, unpublished, changed):
    """Which finished requirements and fixes (and their issues) this unpublished set carries, and the commits that belong to them."""
    reqs, fixes, covered = [], [], set()

    def take(anchor_list):
        hit = [a for a in anchor_list if a in unpublished]
        for a in hit:
            covered.add(a)
            parents = git(home, 'rev-list', '--parents', '-n', '1', a).split()[1:]
            if len(parents) > 1:  # a merge: what the branch brought belongs to it too
                covered.update(c for c in git(home, 'rev-list', f'{parents[0]}..{parents[1]}').split() if c in unpublished)
        return hit

    for path in sorted((Path(home) / DIR / 'requirements').glob('*.md')):
        meta, _ = read_doc(path)
        if meta.get('state') != 'done' or not meta.get('id'):
            continue
        rel = f'{DIR}/requirements/{path.name}'
        if meta.get('merge_commit') in unpublished:
            hit = take([meta['merge_commit']])
        elif rel in changed:
            hit = take(landing_commits(home, [meta.get('merge_commit')], meta['id'], (rel, 'state: done')))
        else:
            hit = []
        if hit:
            reqs.append({'id': str(meta['id']), 'title': str(meta.get('title') or '')})
    titles = {m['id']: str(m.get('title') or '') for _, m, _ in issue_files(home)}
    for fix in all_fixes(home).values():
        if fix.get('state') != 'merged':
            continue
        rel = f'{DIR}/issues/fixes/{fix["id"]}.json'
        if fix.get('merge_commit') in unpublished:
            hit = take([fix['merge_commit']])
        elif rel in changed:
            hit = take(landing_commits(home, [fix.get('merge_commit')], fix['id']))
        else:
            hit = []
        if hit:
            fixes.append(fix)
    issues = [{'id': i, 'num': num(i), 'title': titles.get(i, '')} for fix in fixes for i in fix_issues(fix)]
    return reqs, issues, covered


def _unpublished(home, spec):
    """What goes out with this set of commits, worked out by the program."""
    empty = {'total': 0, 'requirements': [], 'issues': [], 'facts': 0, 'others': [], 'others_total': 0, 'carried_commits': 0}
    if spec is None:
        return empty
    hashes = git(home, 'rev-list', *spec).split()
    if not hashes:
        return empty
    commits = _read_commits(home, spec)
    unpublished = set(hashes)
    changed = {f for c in commits for f in c['files']}
    reqs, issues, covered = _carried(home, unpublished, changed)
    facts, others = 0, []
    for c in commits:
        if c['hash'] in covered:
            continue
        if len(c['parents']) <= 1 and c['files'] and all(_facts_path(f) for f in c['files']):
            facts += 1
        else:
            others.append({'hash': c['hash'][:7], 'subject': c['subject']})
    listed = len(commits)
    unknown = len(hashes) - listed  # beyond the cap: not looked at one by one
    return {'total': len(hashes), 'requirements': reqs, 'issues': issues, 'facts': facts, 'others': others[:LIST_CAP],
            'others_total': len(others) + max(unknown, 0), 'carried_commits': len(covered)}


def _loose_paths(home):
    out = []
    for line in git(home, 'status', '--porcelain').splitlines():
        name = line[3:].split(' -> ')[-1].strip('"')
        if name and not _facts_path(name):
            out.append(name)
    return out


def _pending_facts(home):
    return len([l for l in git(home, 'status', '--porcelain', '--', DIR).splitlines() if l.strip()])


def _pending_requirements(home):
    count = 0
    sessions = {s.get('session_id'): s for s in load_sessions(home)}
    for path in (Path(home) / DIR / 'requirements').glob('*.md'):
        try:
            meta, sections = read_doc(path)
        except FileNotFoundError:
            continue
        state = meta.get('state') or 'open'
        if not meta.get('id') or meta.get('status') != 'current' or state != 'open':
            continue
        ids = meta.get('sessions') or []
        last = sessions.get(str(ids[-1])) if ids else None
        done, steps = plan_progress(last)
        if stage_of(state, meta.get('manual_stage'), done, steps, gate(sections)['ok']) == 'check':
            count += 1
    return count


def _fetch(home, remote, timeout=FETCH_TIMEOUT):
    code, out, err = _run(home, ['fetch', '--no-tags', remote], timeout)
    if code == 0:
        return {'ok': True}
    kind, words = classify(home, err, timed_out=code is None, pushing=False)
    return {'ok': False, 'code': kind, 'error': words if kind != 'failed' else '抓取远端没有成功。', 'detail': meaningful(err)}


def _last_fetch(home):
    found = git(home, 'rev-parse', '--git-path', 'FETCH_HEAD').strip()
    path = Path(home) / found if found else None
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S') if path and path.exists() else None
    except OSError:
        return None


def publish_status(root, fetch=False, remote=None, timeout=FETCH_TIMEOUT):
    home = main_checkout(root)
    branch = git(home, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    detached = branch in ('', 'HEAD')
    remotes = _remotes(home)
    blockers, warnings = [], []
    st = {'root': str(home), 'branch': None if detached else branch, 'detached': detached,
          'remotes': [{'name': r, 'url': git(home, 'remote', 'get-url', r).strip()} for r in remotes],
          'upstream': None, 'target': None, 'needs_remote': False, 'ahead': None, 'behind': None, 'fetch': None,
          'last_fetch': _last_fetch(home), 'pending_facts': _pending_facts(home), 'pending_requirements': _pending_requirements(home),
          'record': None, 'busy': None, 'divergence': None}
    busy = _busy_kind(home)
    st['busy'] = busy
    if busy:
        blockers.append({'code': 'busy', 'kind': busy, 'text': {'merge': '项目目录里有一次合并还没做完（可能停在冲突上），先把它做完、提交，再发布。',
                         'rebase': '项目目录里有一次变基还没做完，先把它做完，再发布。'}.get(busy, '项目目录里有一次拣选或回退还没做完，先处理完它再发布。')})
    if detached:
        blockers.append({'code': 'detached', 'text': '项目目录现在不在任何分支上（detached HEAD），先切到要发布的分支。'})
        st['state'] = 'detached'
    upstream = None if detached else _upstream(home, branch)
    if upstream:
        st['upstream'] = {'remote': upstream[0], 'branch': upstream[1], 'ref': f'{upstream[0]}/{upstream[1]}'}
    if not detached and not remotes:
        blockers.append({'code': 'no-remote', 'text': '这个项目还没有远端，没有地方可以发布。先在终端里 git remote add 一个。'})
        st['state'] = 'no-remote'
    elif upstream and upstream[0] not in remotes:
        blockers.append({'code': 'bad-upstream', 'text': f'上游分支指向的远端 {upstream[0]} 已经不存在了。'})
        upstream = None
    if not detached and remotes:
        try:
            target = _choose_target(home, branch, remotes, upstream, remote)
        except PublishError as error:
            target = None
            blockers.append({'code': 'usage', 'text': str(error)})
        st['target'] = target
        if target is None and not any(b['code'] == 'usage' for b in blockers):
            st['needs_remote'] = True
            blockers.append({'code': 'pick-remote', 'text': '当前分支还没有上游，有好几个远端：' + '、'.join(remotes) + '。要选一个。'})
        if target and fetch:
            st['fetch'] = _fetch(home, target['remote'], timeout)
            st['last_fetch'] = _last_fetch(home)
            if st['fetch']['ok'] and target['sets_upstream']:
                target['creates'] = not _ref_exists(home, f'refs/remotes/{target["remote"]}/{target["branch"]}')
        # 和远端比：有对应的远端分支就和它比，没有就看「哪些提交不在这个远端的任何分支上」
        spec = None
        if target:
            ref = f'refs/remotes/{target["remote"]}/{target["branch"]}'
            if _ref_exists(home, ref):
                spec = ['HEAD', '--not', ref]
                st['ahead'], st['behind'] = _count(home, f'{ref}..HEAD'), _count(home, f'HEAD..{ref}')
            else:
                spec = ['HEAD', '--not', f'--remotes={target["remote"]}']
                st['ahead'], st['behind'] = _count(home, *spec), 0
        st['unpublished'] = _unpublished(home, spec)
        if target:
            st['state'] = ('diverged' if st['behind'] else 'ahead') if st['ahead'] else ('behind' if st['behind'] else ('no-upstream' if upstream is None else 'synced'))
            if upstream is None and not st['behind']:
                st['state'] = 'no-upstream'  # 没有上游可比：不报数，只提示（对话框里照样列出会发出去的东西）
        else:
            st['state'] = 'no-upstream' if st['needs_remote'] else st.get('state', 'unknown')
        if st['behind']:
            if st['ahead']:
                blockers.append({'code': 'diverged', 'text': f'远端有 {st["behind"]} 个提交本地没有，本地也有 {st["ahead"]} 个远端没有：两边分叉了。' + ('合并已经开始了，先把它做完。' if busy else '可以点「让 AI 来合并」，合并好了再回来发布。') + '（远端的情况只到上次检查为止）'})
                st['divergence'] = _divergence(home, f'refs/remotes/{target["remote"]}/{target["branch"]}')
            else:
                blockers.append({'code': 'behind', 'text': f'远端比本地多 {st["behind"]} 个提交（到上次检查远端为止）。先拉取，再发布。'})
    else:
        st['unpublished'] = _unpublished(home, None)
        st.setdefault('state', 'no-remote' if not detached else 'detached')
    loose = [] if detached else _loose_paths(home)
    if loose:
        warnings.append({'code': 'loose', 'text': f'项目目录里有 {len(loose)} 处代码改动还没提交，它们不会被发布。', 'paths': loose[:5], 'count': len(loose)})
    if st['pending_facts']:
        warnings.append({'code': 'pending-facts', 'text': f'有 {st["pending_facts"]} 个事实文件的改动还没提交，发布时会先一起提交再推送。', 'count': st['pending_facts']})
    full = full_status(home)
    if all_testsets(home) and full['stale']:  # 合并闸门每次只跑一部分，剩下的留给全量：发布前提醒，不挡
        last = full['last']
        warnings.append({'code': 'full-tests', 'text': (f'主线上还没跑过全量测试集。' if not last else
                         f'上次全量测试集是 {last["at"]} 在 {last["commit"][:8]} 上跑的' + (f'，之后又合进来 {full["behind"]} 次' if full['behind'] else '')
                         + (f'，当时没过：{"、".join(last["failing"][:5])}' if last.get('failing') else '') + '。')
                         + '合并前的检查只跑了一部分，发布前可以在「测试」页点「整套跑一遍」。'})
    elif full['last'] and full['last'].get('failing'):
        warnings.append({'code': 'full-tests', 'text': f'这份代码上全量测试集没过的：{"、".join(full["last"]["failing"][:5])}。'})
    prepared = _prepared_record(home, st['target'])
    st['record'] = prepared['id'] if prepared else None
    st['blockers'], st['warnings'] = blockers, warnings
    st['can_publish'] = not blockers and bool(st['target']) and (bool(st['ahead']) or bool(st['target'] and st['target']['sets_upstream']))
    st['can_pull'] = bool(upstream) and not detached and not git_busy(home) and bool(st['behind']) and not st['ahead']
    return st


# ---------- 发布记录 ----------

def _record_dir(home):
    return Path(home) / DIR / FOLDER


def _plain(line):
    return re.sub(r'〔[^〕]*〕$', '', line[2:] if line.startswith('- ') else line).strip()


def _record_view(home, path):
    meta, sections = read_doc(path)
    if meta.get('type') != 'publish' or not meta.get('id'):
        return None
    rel = f'{DIR}/{FOLDER}/{path.name}'
    if is_external(home):  # 数据外置：记录不进 Git，看它要推送的那个提交（to）有没有到远端
        to = str(meta.get('to') or '')
        last = to if to and git(home, 'cat-file', '-t', to).strip() == 'commit' else ''
    else:
        last = git(home, 'log', '-1', '--format=%H', '--', rel).strip()
    remote = str(meta.get('remote') or '')
    contained = bool(last) and bool(git(home, 'for-each-ref', '--contains', last, '--format=%(refname)', f'refs/remotes/{remote}/' if remote else 'refs/remotes/').strip())
    reqs = [{'id': t.split(' ', 1)[0], 'title': t.split(' ', 1)[1] if ' ' in t else ''} for t in map(_plain, sections.get('包含的需求', [])) if t != '无']
    issues = [{'num': t.split(' ', 1)[0], 'title': t.split(' ', 1)[1] if ' ' in t else ''} for t in map(_plain, sections.get('包含的问题', [])) if t != '无']
    others = [{'hash': t.split(' ', 1)[0], 'subject': t.split(' ', 1)[1] if ' ' in t else ''} for t in map(_plain, sections.get('其他提交', [])) if t != '无' and not t.startswith('另有 ')]
    return {'id': str(meta['id']), 'title': str(meta.get('title') or ''), 'remote': remote, 'branch': str(meta.get('branch') or ''),
            'target': str(meta.get('target') or meta.get('branch') or ''), 'created': str(meta.get('created') or ''), 'prepared': str(meta.get('prepared') or ''),
            'from': meta.get('from'), 'to': meta.get('to'), 'commits': meta.get('commits') or 0, 'facts': meta.get('facts') or 0, 'attempts': meta.get('attempts') or 1,
            'requirements': reqs, 'issues': issues, 'others': others, 'published': contained, 'commit': last or None, 'order': _count(home, last) if last else 0, 'file': rel, 'path': str(path)}


def reached_remotes(home, commit):
    """The remote-tracking branches that contain this commit (as of the last fetch): the same test a publish record uses to say it went out."""
    out = git(home, 'for-each-ref', '--contains', commit, '--format=%(refname:short)', 'refs/remotes/')
    return [l for l in out.split() if l and not l.endswith('/HEAD')]


def publish_list(root):
    home = main_checkout(root)
    out = [v for v in (_record_view(home, p) for p in sorted(_record_dir(home).glob('P-*.md'))) if v]
    out.sort(key=lambda r: (r['created'], r['order'], r['id']), reverse=True)  # 同一秒里写的，后写的提交在历史里更靠后
    return out


def _prepared_record(home, target):
    """A record written for this target whose push did not get through: the next publish reuses it."""
    if not target:
        return None
    for view in publish_list(home):
        if not view['published'] and view['remote'] == target['remote'] and view['target'] == target['branch']:
            return view
    return None


def _new_id(home):
    while True:
        ident = f'P-{dt.datetime.now():%Y%m%d}-{secrets.token_hex(2)}'
        if not list(_record_dir(home).glob(f'{ident}-*.md')):
            return ident


def _write_record(home, st, head, reuse):
    now = f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}'
    target, un = st['target'], st['unpublished']
    ident = reuse['id'] if reuse else _new_id(home)
    path = Path(home) / reuse['file'] if reuse else _record_dir(home) / f'{ident}-{slug(st["branch"])[:30]}.md'
    source = f'〔{ident}#发布〕'
    meta = {'id': ident, 'type': 'publish', 'title': f'发布 {st["branch"]} 到 {target["remote"]}/{target["branch"]}', 'status': 'current',
            'remote': target['remote'], 'branch': st['branch'], 'target': target['branch'],
            'created': reuse['created'] if reuse else now, 'prepared': now,
            'from': (git(home, 'rev-parse', f'refs/remotes/{target["remote"]}/{target["branch"]}').strip() or None) if not target['creates'] else None,
            'to': head, 'commits': un['total'], 'facts': un['facts'], 'attempts': (reuse['attempts'] + 1) if reuse else 1, 'updated': today()}
    sections = {h: [] for h in HEADS}
    if reuse:  # the change log only grows
        sections['变更记录'] = [f'- {l}' if not l.startswith('- ') else l for l in read_doc(path)[1].get('变更记录', [])]
    sections['包含的需求'] = [f'- {r["id"]} {r["title"]}{source}' for r in un['requirements']] or [f'- 无{source}']
    sections['包含的问题'] = [f'- {i["num"]} {i["title"]}{source}' for i in un['issues']] or [f'- 无{source}']
    rows = [f'- {o["hash"]} {o["subject"]}{source}' for o in un['others'][:100]]
    if un['others_total'] > len(rows):
        rows.append(f'- 另有 {un["others_total"] - len(rows)} 个提交没有逐条列出{source}')
    sections['其他提交'] = rows or [f'- 无{source}']
    where = f'{target["remote"]}/{target["branch"]}' + ('（新建远端分支）' if target['creates'] else '')
    add_line(sections, '变更记录', f'- {now} ' + ('再次准备发布（上一次没有推成），' if reuse else '准备发布，') + f'要推送到 {where}，共 {un["total"]} 个提交。{source}')
    write_doc(path, meta, sections)
    return ident


# ---------- 发布 ----------

def publish_run(root, remote=None, timeout=PUSH_TIMEOUT):
    """Commit the facts waiting, write (or update) the 发布记录 and commit it, then push the current branch. Returns what went out.
    Raises PublishError; the record stays in the repository when the push does not get through."""
    home = main_checkout(root)
    st = publish_status(home, remote=remote)
    if st['blockers']:
        first = st['blockers'][0]
        raise PublishError('blocked', first['text'], blockers=st['blockers'])
    if not st['can_publish']:
        raise PublishError('nothing', '没有需要发布的提交，远端已经是最新的。')
    target, branch = st['target'], st['branch']
    commit_facts(home, '记录事实')  # what the program keeps waiting under .relayforge/ goes in first, so it goes out too
    st = publish_status(home, remote=remote)  # the facts commit may have added one
    if st['blockers']:
        raise PublishError('blocked', st['blockers'][0]['text'], blockers=st['blockers'])
    target = st['target']
    head = git(home, 'rev-parse', 'HEAD').strip()
    reuse = _prepared_record(home, target)
    ident = _write_record(home, st, head, reuse)
    external = is_external(home)
    if not external and not commit_facts(home, f'发布记录：{branch} → {target["remote"]}/{target["branch"]}'):
        raise PublishError('failed', '发布记录没有提交成功，没有推送。看看项目目录里有没有别的 git 操作正在进行，再试一次。', record=ident)
    # 推送：明确写出推哪个分支到哪个分支。用户的 git 配置（push.default、followTags……）可能让一次普通的 git push 带上别的分支或标签，这里一律不要。
    args = ['-c', 'push.followTags=false', '-c', 'push.recurseSubmodules=no', 'push']
    if target['sets_upstream']:
        args.append('-u')
    args += [target['remote'], f'refs/heads/{branch}:refs/heads/{target["branch"]}']
    code, out, err = _run(home, args, timeout)
    if code != 0:
        kind, words = classify(home, err + out, timed_out=code is None)
        raise PublishError(kind, words, meaningful(err or out), record=ident, retry=True)
    after = publish_status(home, remote=remote)
    return {'ok': True, 'record': ident, 'remote': target['remote'], 'branch': branch, 'target': target['branch'], 'created': target['creates'],
            'upstream_set': target['sets_upstream'], 'commits': st['unpublished']['total'] + (0 if external else 1), 'requirements': st['unpublished']['requirements'],
            'issues': st['unpublished']['issues'], 'facts': st['unpublished']['facts'] + (0 if external else 1), 'others': st['unpublished']['others'],
            'others_total': st['unpublished']['others_total'], 'ahead': after['ahead'], 'status': after}


def publish_pull(root, timeout=FETCH_TIMEOUT):
    """Fetch, then fast-forward only. Never a merge or a rebase."""
    home = main_checkout(root)
    st = publish_status(home)
    for b in st['blockers']:
        if b['code'] in ('busy', 'detached', 'no-remote'):
            raise PublishError('blocked', b['text'], blockers=st['blockers'])
    if not st['upstream']:
        raise PublishError('blocked', '当前分支还没有上游分支，没有可以拉取的。')
    remote, dest = st['upstream']['remote'], st['upstream']['branch']
    fetched = _fetch(home, remote, timeout)
    if not fetched['ok']:
        raise PublishError(fetched['code'], fetched['error'], fetched['detail'])
    ref = f'refs/remotes/{remote}/{dest}'
    if not _ref_exists(home, ref):
        raise PublishError('blocked', f'远端 {remote} 上没有 {dest} 分支，没有可以拉取的。')
    ahead, behind = _count(home, f'{ref}..HEAD'), _count(home, f'HEAD..{ref}')
    if not behind:
        return {'ok': True, 'pulled': 0, 'message': '已经是最新的，没有新提交要拉取。'}
    if ahead:
        raise PublishError('diverged', f'本地有 {ahead} 个提交远端没有，远端也有 {behind} 个提交本地没有：两边分叉了。什么都没有动。'
                           '可以点「让 AI 来合并」，它会在新开的聊天里把远端的合并进来；弄好以后再回来发布。', behind=behind, ahead=ahead)
    code, out, err = _run(home, ['merge', '--ff-only', '-q', ref], timeout)
    if code != 0:
        raise PublishError('failed', '没有拉下来：项目目录里有没提交的改动，会被远端的新提交覆盖，或者当前不能快进。什么都没有动。', meaningful(err or out))
    return {'ok': True, 'pulled': behind, 'message': f'已拉取 {behind} 个新提交（快进），当前分支和 {remote}/{dest} 一致。'}
