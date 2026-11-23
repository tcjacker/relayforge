"""人工验收（FORMAT.md 的「人工验收」）：从需求的工作目录（或项目主目录）把程序跑起来，人自己试，逐条点「通过」或「这里不对」。

分工：
  - 进程由应用的主进程启动、停止、看端口、收日志（app/src/main/services.js），不经过模型；
  - 这里只算「这次怎么启动」（env plan / env launch）：哪套环境、每个服务的命令和目录、环境变量（launch 只给应用，带保密值）、
    验收时额外带的环境变量（占位符代进去）、命令有没有经你同意、要执行的准备步骤；还负责每个需求一个的临时目录和项目副本；
  - 逐条验收的结果写在需求文件的「人工验收」里（rf.py req accept，只由应用触发）。

本机的位置（不进仓库）：$RELAYFORGE_HOME/accept/<项目标识>/<需求编号或 main>/
  scratch/        {{scratch}}：程序为这次验收准备的临时目录，每个需求一个，停掉以后可以清掉
  project-copy/   {{project_copy}}：项目主目录的一份副本（git clone --local 当前分支，再盖上主目录现在的 .relayforge/），
                  去掉了 origin，验收实例只碰它，改了也不会回到真实项目
  services.log    这个需求启动的服务的日志（保密值打码以后才写）
"""

import datetime as dt
import json
import re
import shutil
from ..core.language import display_text
from pathlib import Path

from ..core.common import DIR, SCRIPT, commit_facts, git, main_checkout, project_key, relayforge_home, run_git, today
from ..core.documents import add_line, live_items, write_doc
from ..envs.prep import prep_view, service_print
from ..envs.values import all_secret_values, env_for_run, redact
from ..envs.store import read_local
from .record import find_requirement
from .testing import _cond, _user_req, now, ordered
from .testrecords import one, testing_view

PLACEHOLDERS = {'req': '需求编号', 'worktree': '这个需求的工作目录', 'scratch': '这次验收的临时目录', 'project': '项目主目录', 'project_copy': '项目主目录的副本'}
PH = re.compile(r'\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}')
SCOPE = re.compile(r'^(main|R-[\w-]+)$')
COPY_MARK = 'relayforge-accept-copy.json'


# ---------- 本机的位置 ----------

def accept_root(home, scope):
    scope = str(scope or 'main')
    if not SCOPE.match(scope):
        raise ValueError(display_text(f'不认识的验收范围：{scope}',
                                      f'Unrecognized acceptance scope: {scope}'))
    return relayforge_home() / 'accept' / project_key(main_checkout(home)) / scope


def places(home, scope):
    base = accept_root(home, scope)
    return {'base': base, 'scratch': base / 'scratch', 'copy': base / 'project-copy', 'log': base / 'services.log'}


def scope_place(home, req=None):
    """Where the code runs: a requirement's working directory, or the project's main checkout ('main')."""
    home = main_checkout(home)
    if not req:
        return 'main', home
    found = find_requirement(home, req)
    if found['state'] != 'open' or not found['worktree']:
        raise ValueError(display_text(f'需求 {found["id"]} 不在开发中（没有自己的工作目录），没法从它的代码启动。',
                                      f'Requirement {found["id"]} has no active development worktree to launch from.'))
    return found['id'], Path(found['worktree'])


# ---------- 占位符 ----------

def fill(text, values):
    """{{req}} {{worktree}} {{scratch}} {{project}} {{project_copy}} → their values. Unknown names are left as they are and listed."""
    missing = []

    def sub(m):
        name = m.group(1)
        if name in values:
            return str(values[name])
        missing.append(name)
        return m.group(0)
    return PH.sub(sub, str(text or '')), missing


def uses(service, name):
    return any(name in PH.findall(str(a.get('value') or '')) for a in service.get('accept') or [])


# ---------- 项目副本 ----------

def make_copy(home, dest, fresh=False):
    """A copy of the project for the acceptance instance to use: `git clone --local` of the branch the main checkout has out
    (objects copied, not linked), its origin removed (nothing can be pushed back), then the main checkout's .relayforge/ as it
    is now laid over it (facts not committed yet included). Kept and reused unless `fresh`. Never a git worktree: a worktree
    shares the real repository's refs and would let the instance change them."""
    home = main_checkout(home)
    dest = Path(dest)
    mark = dest / '.git' / COPY_MARK
    if dest.exists() and not fresh and mark.exists():
        info = json.loads(mark.read_text(encoding='utf-8'))
        return dict(info, reused=True)
    if dest.exists():
        if not (dest / '.git' / COPY_MARK).exists() and any(dest.iterdir()):
            raise ValueError(display_text(f'{dest} 已经有别的东西，不是程序做的项目副本，没有覆盖。',
                                          f'{dest} already contains files. RelayForge only replaces a project copy it created.'))
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    branch = git(home, 'symbolic-ref', '--quiet', '--short', 'HEAD').strip()
    made = run_git(dest.parent, 'clone', '--local', '--no-hardlinks', '-q', *(['--branch', branch] if branch else []), str(home), str(dest))
    if made.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError(display_text('没能复制项目：' + ((made.stderr or made.stdout).strip().splitlines() or ['git 没有给出原因'])[-1],
                                      'Could not copy the project: ' + ((made.stderr or made.stdout).strip().splitlines() or ['Git returned no details'])[-1]))
    run_git(dest, 'remote', 'remove', 'origin')
    src = home / DIR
    if src.is_dir():
        shutil.rmtree(dest / DIR, ignore_errors=True)
        shutil.copytree(src, dest / DIR, ignore=shutil.ignore_patterns('site', '__pycache__', '*.pyc'), symlinks=True)
    info = {'source': str(home), 'branch': branch or None, 'head': git(home, 'rev-parse', '--short', 'HEAD').strip(),
            'at': dt.datetime.now().isoformat(timespec='seconds'), 'path': str(dest)}
    mark.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
    return dict(info, reused=False)


def copy_info(dest):
    try:
        return json.loads((Path(dest) / '.git' / COPY_MARK).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def clean(root, req=None):
    """Remove a requirement's (or the main line's) scratch directory and project copy. The app stops the services first."""
    home = main_checkout(root)
    scope = req or 'main'
    p = places(home, scope)
    gone = []
    for key in ('scratch', 'copy'):
        if p[key].exists():
            shutil.rmtree(p[key], ignore_errors=True)
            gone.append(str(p[key]))
    return {'scope': scope, 'removed': gone}


# ---------- 这次怎么启动 ----------


def opens_what(service, values):
    """In plain words, what an instance of the RelayForge app started with these variables opens (the window title tag, the data
    directory and the project); None for anything else."""
    acc = {a['name']: a.get('value') or '' for a in service.get('accept') or []}
    if not any(k in acc for k in ('RELAYFORGE_USER_DATA', 'RELAYFORGE_PROJECT', 'RELAYFORGE_WINDOW_TAG')):
        return None
    project = acc.get('RELAYFORGE_PROJECT', '')
    if 'project_copy' in PH.findall(project):
        what = display_text('当前项目的副本（只读用途，改了也不会回到真实项目）',
                            'Project copy. Changes stay in the copy.')
    elif project.strip() in ('', 'demo'):
        what = display_text('演示项目', 'Demo project')
    elif 'project' in PH.findall(project) or project.strip() == str(values.get('project')):
        what = display_text('真实的项目主目录（验收实例会改到它）', 'Project directory. Acceptance runs can modify it.')
    else:
        what = fill(project, values)[0]
    return what


def plan(root, ref=None, req=None, names=None, trusted=None, prepare=False, copy='reuse'):
    """How the services of the environment in use (or `ref`) start for acceptance in `req`'s working directory (or the main checkout).
    Without `prepare` nothing is made and secret values are masked (anyone may look). With `prepare` (the app's 「启动」, checked
    against the app's marker by the caller): the scratch directory and, when a service uses {{project_copy}}, the project copy are
    made, and `vars` carries every value the processes get, secrets included, with `secrets` for scrubbing the logs."""
    home = main_checkout(root)
    scope, place = scope_place(home, req)
    p = places(home, scope)
    envinfo = env_for_run(home, ref, trusted=trusted)
    out = {'scope': scope, 'place': str(place), 'project': str(home), 'scratch': str(p['scratch']), 'copy': {'path': str(p['copy']), 'info': copy_info(p['copy'])},
           'log': str(p['log']), 'env': None, 'services': [], 'prep': [], 'needs': {'services': [], 'prep': []}, 'withheld': []}
    if not envinfo:
        out['why'] = display_text('现在没有在用的测试环境：先在测试页的「测试环境」里建一套，填「怎么启动」。',
                                  'No test environment is active. Create one under Test environments and add a service.')
        return out
    env = envinfo['env']
    out['env'] = {'id': env['id'], 'name': env['name']}
    loc = read_local(home)
    services = env.get('services') or []
    if names:
        want = set(names)
        missing = want - {s['name'] for s in services}
        if missing:
            raise ValueError(display_text(f'{env["name"]} 里没有服务 {"、".join(sorted(missing))}。',
                                          f'{env["name"]} has no services named {", ".join(sorted(missing))}.'))
        services = [s for s in services if s['name'] in want]
    if not services:
        out['why'] = display_text(
            f'测试环境「{env["name"]}」还没有填「怎么启动」：去测试环境里填服务（名称、启动命令、端口、子目录），或者让 AI 读项目，帮你填。',
            f'Test environment “{env["name"]}” has no services. Add a name, start command, port and directory, or ask AI to help.',
        )
    values = {'req': scope if scope != 'main' else '主线', 'worktree': str(place), 'scratch': str(p['scratch']), 'project': str(home), 'project_copy': str(p['copy'])}
    if prepare:
        p['scratch'].mkdir(parents=True, exist_ok=True)
        if any(uses(s, 'project_copy') for s in services):
            out['copy']['info'] = make_copy(home, p['copy'], fresh=copy == 'fresh')
    approved = loc.get('approved') or {}
    for s in services:
        cwd = Path(place) / s['dir'] if s.get('dir') else Path(place)
        acc, missing = [], []
        for a in s.get('accept') or []:
            value, miss = fill(a.get('value'), values)
            missing += miss
            acc.append({'name': a['name'], 'template': a.get('value') or '', 'value': value, 'missing': miss})
        item = {'name': s['name'], 'command': s.get('command') or '', 'dir': s.get('dir') or '', 'cwd': str(cwd), 'cwd_exists': cwd.is_dir(), 'port': s.get('port'),
                'accept': acc, 'missing': sorted(set(missing)), 'approved': service_print(s) in approved, 'print': service_print(s),
                'uses_copy': uses(s, 'project_copy'), 'opens': opens_what(s, values), 'auto_restart': s.get('auto_restart') is not False}
        if not item['command']:
            item['problem'] = f'服务「{s["name"]}」没有启动命令。'
        elif not item['cwd_exists']:
            item['problem'] = f'服务「{s["name"]}」的目录 {cwd} 不存在。'
        elif item['missing']:
            item['problem'] = f'服务「{s["name"]}」验收时的环境变量里有认不出的占位符：' + '、'.join('{{' + m + '}}' for m in item['missing']) + '（能用的：' + '、'.join('{{' + k + '}}' for k in PLACEHOLDERS) + '）。'
        out['services'].append(item)
        if not item['approved'] and item['command']:
            out['needs']['services'].append(s['name'])
    steps = prep_view(home, env, loc, place)
    out['prep'] = [{k: x.get(k) for k in ('id', 'n', 'note', 'command', 'dir', 'abs_dir', 'when', 'watch', 'approved', 'due', 'due_why')} for x in steps]
    out['needs']['prep'] = [x['id'] for x in steps if x['due'] and not x['approved']]
    out['withheld'] = envinfo.get('withheld') or []
    secret_names = {v['name'] for v in env.get('vars') or [] if v.get('secret')}
    out['var_names'] = [{'name': v['name'], 'secret': bool(v.get('secret'))} for v in env.get('vars') or []]
    if prepare and envinfo.get('trusted'):
        out['vars'] = dict(envinfo['vars'])
        out['secrets'] = envinfo['secrets']
        out['secret_names'] = sorted(secret_names)
        return out
    return redact(out, envinfo['secrets']) if envinfo.get('secrets') else out


# ---------- 「让 AI 来处理」：启动失败 ----------


# ---------- 逐条验收（需求文件的「人工验收」） ----------

RESULTS = {'ok': '通过', 'bad': '这里不对'}
LOG_LINES = 60


def _who(home):
    name = git(home, 'var', 'GIT_AUTHOR_IDENT').split('<')[0].strip()  # git 认的名字（user.name，或环境变量 GIT_AUTHOR_NAME）
    return f'用户 {one(name, 40)}' if name else '用户'


def req_accept(root, ident, n, result, say='', log='', trusted=None, ran=''):
    """The user, in the app, marks one acceptance condition: ok (通过) or bad (这里不对, with a sentence). A bad one sends the
    requirement back to 进行中 and gives the words for its development session: the sentence, the condition and its check
    steps, the tail of the log at that moment (secrets scrubbed)."""
    result = {'ok': 'ok', '通过': 'ok', 'bad': 'bad', '不对': 'bad', '这里不对': 'bad'}.get(str(result or '').strip())
    if not result:
        raise ValueError(display_text('结果只能是 ok（通过）或 bad（这里不对）。',
                                      'Use ok (Pass) or bad (Report a problem) for the result.'))
    found, home, path, meta, sections = _user_req(root, ident, '逐条验收', trusted)
    if found['stage'] != 'check':
        raise ValueError(display_text('只有待验收的需求能逐条验收。',
                                      'Acceptance criteria can only be marked while the requirement is ready for acceptance.'))
    checks = live_items(sections.get('验收条件'))
    k, text = _cond(checks, n)
    key = one(text)
    say = one(say, 400)
    if result == 'bad' and not say:
        raise ValueError(display_text('点「这里不对」要写一句哪里不对。',
                                      'Describe what went wrong in one sentence.'))
    secrets = all_secret_values(home)
    say = redact(say, secrets)
    lines = sections.setdefault('人工验收', [])
    if lines and lines[0].startswith('- 尚未讨论'):
        lines.clear()
    lines.append(f'- {RESULTS[result]}｜{key}｜{now()}｜{_who(home)}' + (f'｜{say}' if result == 'bad' else ''))
    out = {'id': found['id'], 'n': k, 'result': result, 'text': key}
    if result == 'bad':
        meta['manual_stage'] = 'build'
        add_line(sections, '变更记录', f'- {today()} 用户人工验收时说第 {k} 条验收条件不对：{say}。需求退回进行中，连同当时的日志交给开发会话。')
        view = testing_view(meta, sections)
        cond = next((c for c in view['checks'] if c['n'] == k), {'steps': []})
        tail = redact('\n'.join(str(log or '').rstrip().splitlines()[-LOG_LINES:]), secrets)
        rf = 'python3 ' + (str(SCRIPT) if re.match(r'^[\w./-]+$', str(SCRIPT)) else "'" + str(SCRIPT).replace("'", "'\\''") + "'")
        words = [f'【人工验收：第 {k} 条验收条件不对，需求退回进行中】',
                 f'我在人工验收里试了需求「{found["title"]}」（{found["id"]}），第 {k} 条验收条件不对。',
                 f'验收条件：{text}',
                 f'我说：{say}']
        ran = redact(one(ran, 600), secrets)
        if ran:
            words.append(f'当时运行的服务：{ran}')
        if cond.get('steps'):
            words.append('测试会话写的检查步骤：')
            words += [f'  {i}. {s["open"]}：{s["do"]} → 应该看到：{s["see"]}' for i, s in enumerate(cond['steps'], 1)]
        words += ['当时服务的日志（最后部分，保密值已打码）：', '```', tail or '（没有日志）', '```',
                  f'请找出原因、修好、提交。修好以后运行 {rf} req retest "修了什么"：需求回到测试中，测试会话再测一轮，然后回到待验收，我再试。'
                  '不要运行 rf.py req done、req set、req accept：这些由我在应用里点。']
        out['say'] = redact('\n'.join(words), secrets)
    meta['updated'] = today()
    write_doc(path, meta, ordered(sections))
    commit_facts(home, f'人工验收（第 {k} 条{RESULTS[result]}）：{found["title"]}')
    return out
