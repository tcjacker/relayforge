"""问题页的「刷新」：让 AI 在后台把待处理问题逐条复查一遍，查完由用户勾选、一次确认处理。

复查是一个后台任务（rf.py issue review）：
- 只查开始时「待处理」的问题，在当前提交的一份代码副本里，每条问题让模型实际排查一次（读代码、重跑相关的测试、复现），
  回答三件事：是不是真的存在、是不是已经过期、复现步骤和结果；最后再让模型把描述同一个故障的归成一组。
- 进度和结果写在任务文件里（仓库共用的 .git 目录下，不进 Git），应用按它画进度条、列表上的标记和结果窗口。
- 复查不改任何问题的状态。查完以后每条的结论和依据记进那条问题自己的对话；状态只在用户确认处理时改
  （rf.py issue review-apply，只认应用的标记）。两处核对，都以 AI 实际看到的那一版为准：
  写结论时，交给模型以后又变过的问题（比如用户补了回复）结论作废、记为「要重查」；
  确认时，写完结论以后又变过的问题不按这次的结论改，提示重新复查。

分层见 tests/test_layers.py。
"""
import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..core.common import SCRIPT, commit_facts, git, log, relayforge_home
from ..core.model import run_model
from ..core.trust import scrubbed_environ
from ..requirements.owners import find_issue, issue_dir, num
from .fixcheck import snapshot as git_snapshot
from .record import append_entry, change, issue_files, issue_lock, issues_home, read_thread, save
from .triage import triage_payload

__all__ = ['REVIEW_KINDS', 'issue_review', 'issue_review_run', 'issue_review_status', 'issue_review_discard', 'issue_review_apply', 'review_job_path']

# 结论：前三种可以勾选、确认后改状态；其余留在待处理，只把结论记进对话。要重查：查完以后这条又变过，AI 的结论作废
REVIEW_KINDS = {'gone': '不存在', 'stale': '已过期', 'merge': '可合并', 'still': '仍存在', 'unclear': '没查清', 'recheck': '要重查'}
APPLICABLE = ('gone', 'stale', 'merge')
VERDICTS = ('gone', 'stale', 'still', 'unclear')  # 每条单独查的结论；可合并由最后的归并定

INVESTIGATE_INSTRUCTION = '''你在后台复查一个软件项目的一条「待处理」问题。没有人和你对话，查完只输出结论。
当前目录是项目当前分支最新提交的一份代码副本（标准输入里写了分支和提交）；你可以读代码、运行测试和命令、写临时文件来复现，改动只能留在这个副本里。
标准输入里还写了项目原来的目录：只能读（比如借用里面已经装好的依赖），不要在那里改任何文件，也不要用 git 改它的状态。
要回答三件事：
1. 是不是真的存在：按描述，产品该是什么样，实际是什么样。
2. 是不是已经过期：曾经是毛病，但在这个提交上已经不再出现（比如已经修好了，相关代码已经改了）。
3. 能复现的，写下复现步骤和实际看到的结果。
能自己查的就去查：读相关的代码，找到并重跑相关的测试，按描述的步骤复现。只写你真的做过、真的看到的，推断要写明是推断。
把这条问题判成下面四种之一：
- gone（不存在）：描述的情况本来就不是毛病，比如产品本来就该这样、是误报、是测试数据或本机环境本身的问题。
- stale（已过期）：曾经出现过，但在这个提交上已经不再出现。写明凭什么这样判断（比如重跑了哪个测试、通过了；哪个提交改了相关代码）。
- still（仍存在）：在这个提交上实际复现了。必须写复现步骤（steps）和实际结果（observed）。
- unclear（没查清）：复现不了也排除不了，写明缺什么。
fault 用一句话写这个故障本身（哪里、什么现象、什么报错），用来和别的问题比对是不是同一个故障。
只输出一个 JSON 对象，不要输出任何其他文字：
{"verdict":"gone|stale|still|unclear","detail":"结论和依据，两三句话，用普通人能看懂的话","ran":"你实际做了什么，比如重跑了哪个测试、结果如何",
"steps":"复现步骤（still 必填）","observed":"实际看到的结果或报错（still 必填）","fault":"一句话说清故障本身"}'''

GROUP_INSTRUCTION = '''下面是一次复查里每条待处理问题单独排查的结论。找出其中描述同一个故障的问题，归成组：
同一个故障指根本原因相同、修好一处就都好了，比如同一个测试的同一个报错、同一处代码的同一个毛病；只是属于同一个功能点、现象相似但原因不同的，不算。
每组选一条保留（keep，一般选描述最清楚、证据最全的那条），其余的并入它（merge）。一条问题最多出现在一组里；不和别的重复的问题不要写。
只输出一个 JSON 对象，不要输出任何其他文字：
{"groups":[{"keep":"#008","merge":["#011","#014"],"reason":"一两句话说明为什么是同一个故障"}]}'''

# 排查在代码副本里做：能读、能跑命令、能在副本里写；不能改项目原来的目录和记录，不能改仓库状态，不能碰该由用户决定的命令
GIT_WRITES = ('add', 'commit', 'checkout', 'switch', 'merge', 'rebase', 'reset', 'stash', 'push', 'apply', 'restore', 'clean',
              'cherry-pick', 'worktree', 'tag', 'branch')
USER_ACTIONS = ('fix', 'set', 'reply', 'confirm', 'unconfirm', 'reject', 'to-req', 'from-req', 'probe', 'handback', 'review',
                'review-apply', 'review-discard', 'new', 'finding', 'triage')


def _permissions(copy, home):
    places = {str(Path(home).resolve()), str((Path(home) / '.relayforge').resolve())}
    return {
        'allow': ['Read', 'Grep', 'Glob', 'Bash', f'Edit(/{copy}/**)'],  # Edit 规则管所有改文件的工具，Write(路径) 规则对不上
        'deny': ['NotebookEdit', 'WebFetch', 'WebSearch',
                 *[f'Edit(/{p}/**)' for p in sorted(places)],
                 *[f'Bash(git {a}:*)' for a in GIT_WRITES], 'Bash(git * push*)',
                 *[f'Bash(python3 {rf} issue {a}:*)' for rf in ('.relayforge/bin/rf.py', f'{home}/.relayforge/bin/rf.py')
                   for a in USER_ACTIONS],
                 'Bash(sudo:*)', 'Bash(brew:*)', 'Bash(npm publish:*)', 'Bash(docker push:*)'],
    }


# 复查的内容里有 AI 写的问题描述和复现证据，可能夹着提示注入，而它能跑 shell 命令：上面的规则只按写法匹配，挡不住换个写法。
# 所以它的命令放进系统的沙箱（和应用开的会话一样，见 app/src/main/claude-settings.js 的 sandbox）：只能写代码副本、临时目录和构建缓存，
# 跳不出沙箱；沙箱里的 rf.py 一律不认应用的标记（core/trust.py 的 sandboxed）。没有人回答联网的确认，所以只放行常见的依赖仓库。
CACHES = ('.m2', '.gradle', '.npm', '.pnpm-store', '.yarn', '.bun', '.cargo', '.rustup', 'go', '.cache', '.nuget', '.ivy2', '.sbt',
          'Library/Caches')
REGISTRIES = ('registry.npmjs.org', 'registry.yarnpkg.com', 'pypi.org', 'files.pythonhosted.org', 'repo.maven.apache.org',
              'repo1.maven.org', 'plugins.gradle.org', 'services.gradle.org', 'downloads.gradle.org', 'crates.io', 'static.crates.io',
              'index.crates.io', 'proxy.golang.org', 'sum.golang.org', 'api.nuget.org', 'rubygems.org')


def _sandbox(copy):
    home = Path.home()
    return {
        'enabled': True, 'allowUnsandboxedCommands': False, 'failIfUnavailable': sys.platform == 'darwin',
        # 测试环境的保密值（envs/store.py 的 local_path 所在目录）不让读
        'filesystem': {'allowWrite': [str(copy), *[str(home / d) for d in CACHES]], 'denyRead': [str(relayforge_home() / 'envs')]},
        'network': {'allowedDomains': list(REGISTRIES), 'allowLocalBinding': True},
    }


# ---------- 任务文件 ----------

def review_job_path(home):
    """One review per repository, shared by every checkout: next to the issue lock in the common .git, out of Git."""
    common = git(home, 'rev-parse', '--git-common-dir').strip()
    place = (Path(home) / common).resolve() if common else issue_dir(home)
    return place / 'relayforge-issue-review.json'


def _read_job(home):
    try:
        job = json.loads(review_job_path(home).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return job if isinstance(job, dict) and job.get('state') in ('running', 'ready', 'failed') else None


def _write_job(home, job):
    path = review_job_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def _status(job):
    """What the app shows: the job, with a run whose process is gone reported as interrupted."""
    if not job:
        return {'state': 'idle'}
    if job['state'] == 'running' and not _alive(job.get('pid')):
        job = dict(job, state='failed', error='复查中断了：运行复查的程序已经退出（可能是应用关掉了）。没有问题的状态被改动，可以再点「刷新」重来。')
    return job


def issue_review_status(root):
    return _status(_read_job(issues_home(root)))


def issue_review_discard(root):
    """Drop finished results (or a failure notice) without changing any issue. A running review is left alone."""
    home = issues_home(root)
    with issue_lock(home):
        job = _status(_read_job(home))
        if job['state'] == 'running':
            raise ValueError('复查还在进行，等它查完再关掉结果。')
        with contextlib.suppress(FileNotFoundError):
            review_job_path(home).unlink()
    return {'state': 'idle'}


# ---------- 复查 ----------

def _now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _pending(home):
    return {meta['id']: (path, meta, sections) for path, meta, sections in issue_files(home)
            if (meta.get('state') or 'todo') == 'todo'}


def _order(ids):
    return sorted(ids, key=lambda value: int(value[2:]))


def _code_copy(home, commit):
    """The committed code of the current branch, exported to a temporary directory the investigation may change."""
    place = Path(tempfile.mkdtemp(prefix='relayforge-review-')).resolve()
    archive = subprocess.Popen(['git', '-C', str(home), 'archive', '--format=tar', commit], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    unpack = subprocess.run(['tar', '-x', '-C', str(place)], stdin=archive.stdout, capture_output=True)
    archive.stdout.close()
    error = archive.stderr.read().decode(errors='replace')
    archive.stderr.close()
    if archive.wait() != 0 or unpack.returncode != 0:
        shutil.rmtree(place, ignore_errors=True)
        raise ValueError(f'导出当前提交的代码失败：{(error or unpack.stderr.decode(errors="replace")).strip()[:200]}')
    return place


def _clip(value, limit):
    return ' '.join(str(value or '').split())[:limit]


def _check_verdict(answer):
    if not isinstance(answer, dict):
        raise ValueError('回答的格式不对')
    verdict = str(answer.get('verdict') or '').strip()
    out = {key: _clip(answer.get(key), 900) for key in ('detail', 'ran', 'steps', 'observed', 'fault')}
    if verdict not in VERDICTS:
        raise ValueError(f'回答的结论「{verdict}」不在 gone、stale、still、unclear 里')
    if not out['detail'] or not out['ran']:
        raise ValueError('回答缺少结论依据或做了什么')
    if verdict == 'still' and not (out['steps'] and out['observed']):
        raise ValueError('判为仍存在却没有写复现步骤和实际结果')
    return dict(out, verdict=verdict)


def _history(thread):
    """Reproductions and earlier reviews recorded on the issue: the investigation starts from them."""
    out = []
    for e in thread:
        if e.get('t') == 'reproduction':
            out.append(f'- 复现补充（{e.get("at")}）：步骤：{e.get("steps")}；实际：{e.get("observed")}')
        elif e.get('t') == 'review':
            out.append(f'- 之前的 AI 复查（{e.get("at")}，提交 {str(e.get("commit") or "")[:12]}）：'
                       f'{REVIEW_KINDS.get(e.get("kind"), e.get("kind"))}。{e.get("text")} 做了：{e.get("ran")}')
    return out


def _payload(home, job, meta, sections, thread, others):
    head = (f'# 复查的位置\n分支：{job["branch"] or "（不在任何分支上）"}\n提交：{job["commit"]}\n'
            f'当前目录：当前提交的代码副本\n项目原来的目录（只读）：{home}\n\n')
    rest = _history(thread)
    tail = '\n\n# 之前记下的复现和复查\n' + '\n'.join(rest) if rest else ''
    listing = '\n'.join(f'- {num(i)} {t}' for i, t in others) or '（没有别的）'
    return head + triage_payload(home, meta, sections, thread) + tail + '\n\n# 这次一起复查的别的待处理问题（供参考，不用查它们）\n' + listing


def _group(home, rows):
    """Ask once which of the investigated issues are the same fault; returns {merged id: (kept id, reason)}."""
    if len(rows) < 2:
        return {}
    by_num = {row['num']: row['id'] for row in rows}
    lines = [f'- {row["num"]} {row["title"]}｜结论：{REVIEW_KINDS[row["verdict"]]}｜故障：{row["fault"] or row["detail"]}'
             + (f'｜实际结果：{row["observed"]}' if row['observed'] else '') for row in rows]
    answer = run_model(home, GROUP_INSTRUCTION, '\n'.join(lines))
    groups = answer.get('groups') if isinstance(answer, dict) else None
    if not isinstance(groups, list):
        raise ValueError('归并的回答格式不对')
    merged, used = {}, set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        keep = by_num.get('#' + str(group.get('keep') or '').strip().lstrip('#').zfill(3))
        names = group.get('merge') if isinstance(group.get('merge'), list) else []
        others = [by_num.get('#' + str(x).strip().lstrip('#').zfill(3)) for x in names]
        others = [x for x in others if x and x != keep and x not in used]
        if not keep or keep in used or keep in merged or not others:
            continue
        reason = _clip(group.get('reason'), 600) or '和保留的那条是同一个故障。'
        used.update([keep, *others])
        merged.update({x: (keep, reason) for x in others})
    return merged


def _stamp(home, ids):
    """What an issue (and the original it merges into) looked like: confirming checks nothing changed since."""
    data = [(find_issue(home, ident)[1], read_thread(home, ident)) for ident in ids]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _record(home, job, rows, seen):
    """Write every conclusion into its issue's thread (nothing else changes) and stamp what the user may confirm.
    seen holds each issue's stamp from when the model was given it: an issue that changed since was judged on
    evidence the model never saw, so its conclusion is void."""
    out = []
    with issue_lock(home):
        pending = _pending(home)
        # 先全部核对再写：下面写进对话的结论本身也会改动指纹
        changed = {row['id'] for row in rows if row['id'] in pending and _stamp(home, [row['id']]) != seen.get(row['id'])}
        for row in rows:
            if row['id'] not in pending:
                continue  # 复查期间被处理了：这次的结论不记，也不给勾选
            if row['id'] in changed:
                row = dict(row, kind='recheck', target=None,
                           detail=f'复查这条之后，它又有了新的回复或改动，AI 没有看到，这次的结论作废，需要重新复查。'
                                  f'（作废的结论：{REVIEW_KINDS[row["kind"]]}。{row["detail"]}）')
            elif row.get('target') and (row['target'] not in pending or row['target'] in changed):
                row = dict(row, kind=row['verdict'], target=None)  # 原单变了：归并时看的是它的旧样子，只留单独查的结论
            entry = {'t': 'review', 'who': 'AI', 'kind': row['kind'], 'text': row['detail'], 'ran': row['ran'],
                     'review': job['id'], 'branch': job['branch'], 'commit': job['commit']}
            for key in ('steps', 'observed', 'target'):
                if row.get(key):
                    entry[key] = row[key]
            append_entry(home, row['id'], entry)
            out.append(row)
        for row in out:
            if row['kind'] in APPLICABLE:
                row['stamp'] = _stamp(home, [row['id']] + ([row['target']] if row.get('target') else []))
        commit_facts(home, f'AI 复查待处理问题：{len(out)} 条')
    return out


def _start(home):
    """Create the job unless one is running or has results waiting; returns (job, whether this process runs it)."""
    with issue_lock(home):
        job = _status(_read_job(home))
        if job['state'] in ('running', 'ready'):
            return job, False
        pending = _pending(home)
        if not pending:
            return {'state': 'empty', 'message': '当前没有待处理问题。'}, False
        snapshot = git_snapshot(home)
        if not snapshot['commit']:
            raise ValueError('项目还没有任何提交，没法在当前分支上复查。')
        stamp = dt.datetime.now()
        job = {'id': f'RV-{stamp:%Y%m%d-%H%M%S}', 'state': 'running', 'pid': os.getpid(), 'started': _now(),
               'root': str(home), **snapshot, 'phase': 'investigate', 'done': 0, 'current': None,
               'items': [{'id': i, 'num': num(i), 'title': pending[i][1].get('title') or '', 'status': 'queued'}
                         for i in _order(pending)], 'rows': []}
        _write_job(home, job)
    return job, True


def _fail(home, job, error):
    job.update(state='failed', current=None, finished=_now(),
               error=f'{error}。没有问题的状态被改动，可以再点「刷新」重来。')
    _write_job(home, job)
    log(home, f'issue review {job["id"]} failed: {error}')
    return job


def _run(home, job):
    copy = None
    found = []
    seen = {}  # 每条交给模型时的指纹：写结论前核对，之后变过的结论作废
    try:
        copy = _code_copy(home, job['commit'])
        permissions = _permissions(copy, home)
        titles = [(item['id'], item['title']) for item in job['items']]
        for item in job['items']:
            with issue_lock(home):
                hit = _pending(home).get(item['id'])
                thread = read_thread(home, item['id']) if hit else []
                if hit:
                    seen[item['id']] = _stamp(home, [item['id']])
            if not hit:
                item['status'] = 'skipped'  # 复查期间已经不在待处理
                job['done'] += 1
                _write_job(home, job)
                continue
            item['status'], job['current'] = 'running', item['id']
            _write_job(home, job)
            _, meta, sections = hit
            others = [x for x in titles if x[0] != item['id']]
            try:
                answer = _check_verdict(run_model(home, INVESTIGATE_INSTRUCTION, _payload(home, job, meta, sections, thread, others),
                                                  cwd=copy, permissions=permissions, timeout=1800, sandbox=_sandbox(copy)))
            except Exception as e:  # 模型启动不了、超时、回答不对：这次复查失败，什么都不改
                return _fail(home, job, f'复查 {item["num"]} 时出错：{str(e)[:300]}')
            found.append(dict(answer, id=item['id'], num=item['num'], title=item['title'], kind=answer['verdict']))
            item.update(status='done', kind=answer['verdict'], ran=answer['ran'])
            job['done'] += 1
            _write_job(home, job)
        job.update(phase='group', current=None)
        _write_job(home, job)
        try:
            merged = _group(home, found)
        except Exception as e:
            return _fail(home, job, f'归并重复的问题时出错：{str(e)[:300]}')
        for row in found:
            if row['id'] in merged:
                row['target'], reason = merged[row['id']]
                row.update(kind='merge', detail=f'{reason}（单独查的结论：{REVIEW_KINDS[row["verdict"]]}。{row["detail"]}）')
        if git_snapshot(home) != {'branch': job['branch'], 'commit': job['commit']}:
            return _fail(home, job, '复查期间当前分支有了新的提交或切换了分支，结论可能已经不对')
        job['rows'] = _record(home, job, found, seen)
        job.update(state='ready', phase='done', finished=_now())
        _write_job(home, job)
        return job
    except Exception as e:
        return _fail(home, job, str(e)[:300])
    finally:
        if copy:
            shutil.rmtree(copy, ignore_errors=True)


def issue_review(root, background=False):
    """Start the review. A review that is running or has results waiting is returned as it is: clicking again only
    opens it. In the background (what the app does) a separate process runs it, so closing the window or the app, or
    switching projects, does not stop it; otherwise it runs to the end in this process."""
    home = issues_home(root)
    job, mine = _start(home)
    if not mine:
        return job
    if not background:
        return _run(home, job)
    try:
        proc = subprocess.Popen([sys.executable, str(SCRIPT), 'issue', 'review-run', job['id'], '--root', str(home)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=scrubbed_environ(), start_new_session=True)
    except OSError as e:
        return _fail(home, job, f'启动复查失败：{e}')
    with issue_lock(home):
        now = _read_job(home) or job
        if now.get('id') == job['id'] and not now.get('claimed'):
            now['pid'] = proc.pid  # 还没接手时记下它，免得这边退出以后被当成中断
            _write_job(home, now)
    return now


def issue_review_run(root, ident):
    """The background process: take over the review the app just started (once) and run it."""
    home = issues_home(root)
    with issue_lock(home):
        job = _read_job(home)
        if not job or job.get('id') != ident or job['state'] != 'running' or job.get('claimed'):
            raise ValueError('没有等着运行的这次复查。')
        job.update(pid=os.getpid(), claimed=True)
        _write_job(home, job)
    return _run(home, job)


# ---------- 确认处理 ----------

def issue_review_apply(root, request):
    """Change the selected issues by the review's conclusions. One whose issue (or original) changed since the review
    is left as it is and reported; the others go through."""
    selected = request.get('selected') if isinstance(request, dict) else None
    if not isinstance(selected, list) or not selected:
        raise ValueError('请选择要确认处理的问题。')
    home = issues_home(root)
    with issue_lock(home):
        job = _status(_read_job(home))
        if job['state'] != 'ready' or job.get('id') != request.get('review'):
            raise ValueError('这次复查的结果已经不在了，请重新刷新。')
        if git_snapshot(home) != {'branch': job['branch'], 'commit': job['commit']}:
            raise ValueError('复查之后当前分支有了新的提交或切换了分支，请重新刷新。')
        rows = {row['id']: row for row in job['rows']}
        if any(ident not in rows or rows[ident]['kind'] not in APPLICABLE for ident in selected):
            raise ValueError('只能确认处理「不存在」「已过期」「可合并」的问题。')
        pending = _pending(home)
        go, skipped = [], []
        for ident in dict.fromkeys(selected):
            row = rows[ident]
            ids = [ident] + ([row['target']] if row.get('target') else [])
            if any(x not in pending for x in ids) or _stamp(home, ids) != row['stamp']:
                skipped.append({'id': ident, 'num': num(ident),
                                'reason': f'{num(ident)} ' + ('或它要并入的原单 ' if row.get('target') else '') + '在复查之后变过，没有处理，需要重新复查。'})
            else:
                go.append(row)
        for row in go:
            ident, target = row['id'], row.get('target')
            path, meta, sections = pending[ident]
            if row['kind'] == 'gone':
                meta.update(state='closed', closed_as='not_issue', duplicate_of=None, deferred=None)
                summary = '确认不是问题，转入已取消'
            elif row['kind'] == 'stale':
                meta.update(state='resolved', closed_as=None, duplicate_of=None, deferred=None)
                summary = '确认已过期，转入已处理'
            else:
                meta.update(state='closed', closed_as='duplicate', duplicate_of=target, deferred=None)
                summary = f'标记为重复，并入 {num(target)}'
            entry = append_entry(home, ident, {'t': 'review_resolution', 'who': '你', 'review': job['id'], 'branch': job['branch'],
                                               'commit': job['commit'], 'kind': row['kind'], 'target': target, 'evidence': row['detail']})
            change(sections, f'AI 复查：{summary}。', f'{ident}#{entry["n"]}')
            save(path, meta, sections)
            if target:
                append_entry(home, target, {'t': 'sys', 'who': '你', 'text': f'AI 复查：{num(ident)} 并入这条（同一个故障）'})
        with contextlib.suppress(FileNotFoundError):
            review_job_path(home).unlink()
        if go:
            commit_facts(home, f'AI 复查：处理 {"、".join(num(row["id"]) for row in go)}')
    return {'applied': [row['id'] for row in go], 'skipped': skipped}
