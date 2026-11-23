"""运行测试集：按语言准备驱动、构建（要你在应用里同意构建命令）、带上测试环境、逐条跑、判定、把完整结果写到本机。

测试集本身（是什么、哪几条、挂在哪个需求或功能点下面）在 testsets.py；这里只管「跑」，所以运行器（runners）和
测试环境（envs）只有这里用到。`rf.py test …` 的命令分发也在这里（test_command），它要同时用到两边。分层见 tests/test_layers.py。
"""
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import runners
from ..core import procs
from ..core.common import DIR, PACKAGE, TREES, git, main_checkout
from ..requirements.owners import here_owner, owner_going, owner_place
from ..core.trust import require_app, scrubbed_environ
from ..envs.prep import run_prep
from ..envs.values import env_for_run, redact, withheld_note
from ..envs.store import local_state, read_local
from .bridges import BRIDGE, JAVA_BRIDGE, NODE_BRIDGE, JUNIT5
from .edits import _check_entry
from .store import (
    BRIDGE_MARK, LANGS, OUTPUT_CAP, RECENT, RESULTS, TIMEOUT, _canon, _dump, _load, _short, bridge_sets, full_path, judge, read_set,
    read_stats, result_path, select, stamp, stats_path, tests_lock, view,
)

DRIVERS = {'python': PACKAGE / 'drivers' / 'python_driver.py', 'node': runners.NODE_DRIVER, 'java': runners.JAVA_DRIVER}  # 三种语言走同一段协议和结果处理

__all__ = ['testset_run', 'testset_approve_build', 'build_print', 'build_block', 'testset_bridge', 'bridge_report']


def ensure_results_ignored(home):
    """Full results stay out of Git. `init` puts the rule in .gitignore; a project initialised earlier gets it in
    .git/info/exclude, which changes no tracked file."""
    home = Path(home)
    probe = home / RESULTS / 'TS-000' / 'main.json'
    if subprocess.run(['git', '-C', str(home), 'check-ignore', '-q', str(probe)], capture_output=True).returncode == 0:
        return
    common = git(home, 'rev-parse', '--git-common-dir').strip()
    if not common:
        return
    exclude = (home / common).resolve() / 'info' / 'exclude'
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open('a', encoding='utf-8') as f:
        f.write(f'{RESULTS}\n')


def python_for(place):
    chosen = os.environ.get('RELAYFORGE_TEST_PYTHON')
    if chosen:
        return chosen
    for rel in ('.venv/bin/python', 'venv/bin/python', '.venv/Scripts/python.exe'):
        if (Path(place) / rel).exists():
            return str(Path(place) / rel)
    return sys.executable


def drive(lang, place, entry, cases, timeout=TIMEOUT, options=None, env_vars=None):
    """Start the language driver in `place`, hand it the cases, read its answers. Returns (answers by n, error): error is
    set when the set could not run at all (the code failed to load, the driver crashed or timed out, no runtime for the language).
    `options` is the test set's own optional fields (Java: classpath, build). `env_vars` are the test environment's variables
    (secrets included), added to the driver process's environment only."""
    if lang not in DRIVERS:
        return {}, f'不认识的语言 {lang}（支持 {"、".join(LANGS)}）。'
    if lang == 'python':
        command, extra_env, extra = [python_for(place), str(DRIVERS['python'])], {}, {}
    else:
        command, extra_env, extra, error = runners.prepare(lang, place, entry, options, timeout)
        if error:
            return {}, error
    request = json.dumps({'protocol': 1, 'root': str(place), 'entry': entry, **extra, 'cases': [{'n': c['n'], 'input': c.get('input')} for c in cases]}, ensure_ascii=False)
    env = scrubbed_environ(env_vars)  # 应用的标记不交给被测的代码
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', **extra_env)
    env.pop('RELAYFORGE_CAPTURE', None)
    try:  # 超时连同被测代码起的服务、子进程一起停（procs.run），只杀驱动会把它们留下
        code, out, err = procs.run(command, cwd=str(place), input=request.encode('utf-8'), env=env, timeout=timeout)
    except OSError as error:
        return {}, f'驱动没能启动：{error}'
    if code is None:
        return {}, f'超过 {timeout} 秒没有跑完，停掉了。' + _tail(err)
    answers, ready, done, load = {}, False, False, None
    for line in out.decode('utf-8', errors='replace').splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        t = msg.get('t')
        if t == 'ready':
            ready = True
        elif t == 'load_error':
            load = msg
        elif t == 'case':
            answers[msg.get('n')] = msg
        elif t == 'done':
            done = True
    if load:
        return {}, f'接入代码加载失败：{load.get("error")}' + (f'\n{load["trace"].strip()}' if load.get('trace') else '')
    if not ready:
        return {}, f'驱动没有启动起来（退出码 {code}）。' + _tail(err)
    if not done or any(c['n'] not in answers for c in cases):
        return {}, f'驱动中途退出了（退出码 {code}），没跑完所有的数据。' + _tail(err)
    return answers, None


def _build_dir(home, place):
    """The directory part of a build command's fingerprint. The command runs in the code root of the run: for any checkout of
    this project (the main one, or a requirement's or fix's working directory under .worktrees/) that is the same place, '';
    anywhere else (test run --dir) it is that absolute path, so a consent there is a consent for there only."""
    home, place = Path(home).resolve(), Path(place).resolve()
    return '' if place == home or place.is_relative_to(home / TREES) else str(place)


def build_print(ts, home, place):
    """The fingerprint the user's consent to a test set's build command is kept under: the directory (_build_dir) and the command."""
    return 'build:' + hashlib.sha256(f'{_build_dir(home, place)}\n{ts.get("build") or ""}'.encode('utf-8')).hexdigest()[:24]


def build_approved(home, ts, place):
    if not ts.get('build'):
        return True
    return build_print(ts, home, place) in (read_local(main_checkout(home)).get('approved') or {})


def build_item(home, ts, place):
    """What the consent dialog shows for a build command (the same shape as a preparation step in prep_view)."""
    return {'kind': 'build', 'id': ts['id'], 'set': ts['id'], 'n': None, 'note': f'测试集 {ts["id"]}「{ts.get("title") or ""}」的构建命令（每次运行前执行）',
            'command': ts['build'], 'dir': '', 'abs_dir': str(Path(place).resolve()), 'when': 'always', 'timeout': runners.BUILD_TIMEOUT,
            'print': build_print(ts, home, place), 'approved': False}


def testset_approve_build(root, ident, fingerprint, place=None):
    """The user agrees to run this set's build command in `place` (default: the project; any of its checkouts counts the same).
    Only from the app. `fingerprint` is the one the dialog showed: if the command or directory changed since, nothing is agreed."""
    require_app('同意执行测试集的构建命令')
    home = main_checkout(root)
    ts = read_set(home, ident)
    if not ts.get('build'):
        raise ValueError(f'{ts["id"]} 没有构建命令，不用同意。')
    where = Path(place).resolve() if place else home
    now_print = build_print(ts, home, where)
    if not fingerprint or str(fingerprint) != now_print:
        raise ValueError('构建命令或执行目录在你看过以后又变了（或者没有给出你看过的是哪一条）：重新运行一次，看过现在的命令再同意。')
    at = dt.datetime.now().isoformat(timespec='seconds')
    with local_state(home) as loc:
        loc['approved'][now_print] = {'command': ts['build'], 'dir': _build_dir(home, where), 'at': at, 'set': ts['id'], 'kind': 'build'}
    return {'id': ts['id'], 'approved': now_print, 'command': ts['build'], 'dir': str(where)}


def _tail(err):
    text = (err or b'').decode('utf-8', errors='replace').strip()
    return f'\n{text[-2000:]}' if text else ''


def code_print(place):
    """A fingerprint of the code in a checkout: HEAD's tree, uncommitted changes and untracked files (outside .relayforge/)."""
    h = hashlib.sha1(git(place, 'rev-parse', 'HEAD^{tree}').strip().encode())
    h.update(git(place, 'diff', 'HEAD', '--binary', '--', '.', f':(exclude){DIR}').encode('utf-8', 'replace'))
    for name in git(place, 'ls-files', '--others', '--exclude-standard', '--', '.', f':(exclude){DIR}').splitlines()[:300]:
        h.update(name.encode())
        try:
            p = Path(place) / name
            if p.stat().st_size < 2_000_000:
                h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


def _cap(value):
    text = _canon(value)
    return value if len(text) <= OUTPUT_CAP else {'$truncated': text[:2000], 'size': len(text)}


def run_one(home, ts, scope, place, only=None, timeout=None, envinfo=None, pre_error=None):
    """Run one test set as `scope` sees it, in `place`. Records the full result (not in Git) and, for a full run, the counts.
    `envinfo` (envs.env_for_run): the test environment whose variables the driver gets; the record keeps only its name, and
    every secret value of it is scrubbed from what is recorded and returned."""
    cases = view(ts, scope)
    if only is not None:
        cases = [c for c in cases if c['n'] == int(str(only).lstrip('#'))]
        if not cases:
            raise ValueError(f'{ts["id"]} 里（{scope or "主线"}眼里）没有第 #{only} 条数据。')
    started = time.perf_counter()
    unagreed = build_block(home, ts, place) if cases and not pre_error else None
    if pre_error and cases:  # 准备步骤没过：这个测试集跑不起来，原因是那一步
        answers, error = {}, pre_error
    elif unagreed:  # 构建命令还没经你同意：不执行它，这个测试集跑不起来
        answers, error = {}, unagreed
    elif cases:
        answers, error = drive(ts.get('lang') or 'python', place, ts['entry'], cases, timeout or ts.get('timeout') or TIMEOUT, test_options(ts, home, place),
                               (envinfo or {}).get('vars'))
    else:
        answers, error = {}, None
    if error and not pre_error and not unagreed and (envinfo or {}).get('withheld'):
        error += '\n' + withheld_note(envinfo['withheld'])
    rows = []
    for c in cases:
        row = {k: c.get(k) for k in ('n', 'name', 'cat', 'input')}
        row.update({k: c[k] for k in ('expect', 'raises', 'changed', 'in_progress', 'by', 'unseen') if k in c})
        if error:
            row.update(status='broken', actual=None, error=_short(error, 4000), diff=[], ms=None)
        else:
            a = answers[c['n']]
            status, actual, err, diff = judge(c, a)
            row.update(status=status, actual=_cap(actual), error=err, trace=_short(a.get('trace') or '', 4000) or None, diff=diff, ms=a.get('ms'))
        rows.append(row)
    counts = {k: sum(1 for r in rows if r['status'] == k) for k in ('pass', 'fail', 'broken')}
    counts['total'] = len(rows)
    result = 'broken' if error else ('fail' if counts['fail'] else 'pass')
    record = {'set': ts['id'], 'scope': scope or 'main', 'at': stamp(), 'place': str(place), 'code': code_print(place),
              'data': hashlib.sha1(_canon([[c['n'], c.get('input'), c.get('expect'), c.get('raises')] for c in cases]).encode()).hexdigest()[:12],
              'partial': only is not None, 'result': result, 'error': _short(error, 4000) if error else None, 'counts': counts,
              'ms': round((time.perf_counter() - started) * 1000, 1), 'cases': rows, 'env': {'id': envinfo['id'], 'name': envinfo['name']} if envinfo else None,
              'secrets_withheld': list((envinfo or {}).get('withheld') or []), 'prep_failed': bool(pre_error), 'build_unagreed': bool(unagreed)}
    if envinfo and envinfo.get('secrets'):
        record = redact(record, envinfo['secrets'])
        rows = record['cases']
    ensure_results_ignored(home)
    path = result_path(home, ts['id'], scope)
    if only is not None:  # 只跑了一条：并进上一次的完整结果里，不算一次运行
        last = _load(path)
        if last and not last.get('error'):
            keep = [r for r in last.get('cases') or [] if r['n'] != rows[0]['n']] + rows
            keep.sort(key=lambda r: r['n'])
            last['cases'] = keep
            last['counts'] = {k: sum(1 for r in keep if r['status'] == k) for k in ('pass', 'fail', 'broken')}
            last['counts']['total'] = len(keep)
            last['result'] = 'fail' if last['counts']['fail'] or last['counts']['broken'] else 'pass'
            last['at'] = record['at']
            last['env'] = record['env']
            _dump(path, last)
        else:
            _dump(path, record)
    else:
        _dump(path, record)
        with tests_lock(home):
            _count(home, ts['id'], record, rows)
    return record


def _count(home, ident, record, rows):
    """One more full run in the small history that the hints are computed from."""
    result, counts = record['result'], record['counts']
    stats = read_stats(home, ident)
    stats['runs'] = int(stats.get('runs') or 0) + 1
    stats['failed'] = int(stats.get('failed') or 0) + (result != 'pass')
    stats['broken'] = int(stats.get('broken') or 0) + (result == 'broken')
    stats['recent'] = (list(stats.get('recent') or []) + [{'at': record['at'], 'scope': record['scope'], 'result': result, **{k: counts[k] for k in ('pass', 'fail', 'broken')},
                                                            'failing': [r['n'] for r in rows if r['status'] != 'pass'][:50], 'code': record['code'], 'data': record['data']}])[-RECENT:]
    _dump(stats_path(home, ident), stats)


def testset_run(root, ids=(), req=None, fix=None, feature=None, everything=False, case=None, place=None, timeout=None, env=None, no_env=False,
                trusted=None, preflight=False):
    """Run test sets. The scope (whose view of the data) is the requirement or fix named, else the one this working
    directory belongs to, else the main line; the code is the scope's working directory unless --dir says otherwise.
    The test environment (its variables go into the driver's environment) is `env`, else the one in use on this machine; none with no_env.
    Its secret variables go in only when `trusted` (the user ran it from the app, see trust.py; None means: find out) or the user
    allowed AI sessions to use them. Before the sets run, the environment's preparation steps that are due run (envs.run_prep);
    one that fails or has not been agreed to makes every set of this run 跑不起来, with that step as the reason.
    With `preflight`, nothing runs: the answer says which due steps still need the user's consent."""
    home = main_checkout(root)
    scope = req or fix or (here_owner(root) if not everything else None)
    if scope and not owner_going(home, scope):
        scope = None  # 已完成的需求：它的测试集已经落定，按主线跑
    if place:
        where = Path(place).resolve()
    elif req or fix:
        where = owner_place(home, scope) or home
    else:
        where = Path(root).resolve()
    if not (where / '.git').exists() and not git(where, 'rev-parse', '--git-dir').strip():
        raise ValueError(f'{where} 不是代码目录。')
    if case is not None and len(ids) != 1:
        raise ValueError('只跑某一条时要指定一个测试集：test run TS-001 --case 3。')
    picked, skipped = select(home, scope, ids, feature, everything, req or fix)
    envinfo = env_for_run(home, env, no_env, trusted)
    out = {'scope': scope, 'place': str(where), 'at': stamp(), 'sets': [], 'skipped': [{'id': i, 'why': w} for i, w in skipped],
           'env': {'id': envinfo['id'], 'name': envinfo['name']} if envinfo else None, 'secrets_withheld': (envinfo or {}).get('withheld') or [], 'prep': None}
    if envinfo and envinfo['env'].get('prep') and picked:
        prep = run_prep(home, envinfo, where, dry=preflight)
        out['prep'] = {k: prep[k] for k in ('ran', 'skipped', 'blocked', 'error')}
    if preflight:  # 准备步骤和构建命令里还没经你同意的，交给应用弹确认框
        builds = [build_item(home, ts, where) for ts, _ in picked if build_block(home, ts, where)]
        return {'scope': scope, 'place': str(where), 'env': out['env'], 'sets': [ts['id'] for ts, _ in picked], 'blocked': ((out['prep'] or {}).get('blocked') or []) + builds}
    pre_error = (out['prep'] or {}).get('error')
    for ts, origin in picked:
        rec = run_one(home, ts, scope, where, case, timeout, envinfo, pre_error)
        out['sets'].append({'id': ts['id'], 'title': ts.get('title'), 'feature': ts.get('feature'), 'origin': origin, 'in_progress': ts.get('in_progress'),
                            'result': rec['result'], 'error': rec['error'], 'counts': rec['counts'], 'partial': rec['partial'], 'cases': rec['cases']})
    totals = {k: sum(s['counts'][k] for s in out['sets']) for k in ('pass', 'fail', 'broken', 'total')}
    out['totals'] = dict(totals, sets=len(out['sets']))
    out['by_origin'] = {o: dict({k: sum(s['counts'][k] for s in out['sets'] if s['origin'] == o) for k in ('pass', 'fail', 'broken', 'total')},
                                sets=sum(1 for s in out['sets'] if s['origin'] == o)) for o in ('new', 'existing')}
    if everything and not scope and not ids and case is None and where == home:  # 主线上的全量：记下来，合并闸门留下的就靠它（full_status）
        _dump(full_path(home), {'at': out['at'], 'commit': git(home, 'rev-parse', 'HEAD').strip(), 'totals': out['totals'],
                                'failing': [s['id'] for s in out['sets'] if s['result'] != 'pass']})
    return out


def _java_bridge_target(place, home, path):
    """(relative path, error): where the JUnit 5 bridge goes — the project of the first Java set (or the code root), and only
    if that project has JUnit 5 (looked for in its pom.xml / build.gradle[.kts])."""
    projects = []
    for ident, _ in bridge_sets(place, 'java'):
        ts = read_set(home, ident)
        project, _ = runners.project_of(place, Path(place) / str(ts.get('entry') or ''))
        projects.append(project or Path(place).resolve())
    project = projects[0] if projects else Path(place).resolve()
    names = ('pom.xml', 'build.gradle', 'build.gradle.kts')
    text = ''.join((d / n).read_text(encoding='utf-8', errors='replace') for d in dict.fromkeys([project, Path(place).resolve()]) for n in names if (d / n).is_file())
    if not text:
        return None, f'{project} 下没有 pom.xml 或 build.gradle：没法确认项目用的是 JUnit 5，没有生成 Java 桥接文件。'
    if not JUNIT5.search(text):
        return None, f'{project} 的 pom.xml / build.gradle 里没有 JUnit 5（junit-jupiter）：没有生成 Java 桥接文件。要用的话先把 JUnit 5 加进项目。'
    rel = path or (Path(os.path.relpath(project, place)) / 'src' / 'test' / 'java' / 'RelayForgeTestSetsTest.java').as_posix()
    return rel, None


# ---------- 构建和桥接 ----------

def test_options(ts, home=None, place=None):
    """The set's own optional fields for its driver. The build command goes in only when the user agreed to it (build_approved):
    run_one and bridge_run stop before this when it is not, so this is a second lock, not the message."""
    out = {k: ts[k] for k in ('classpath', 'build') if ts.get(k)}
    if out.get('build') and not (home is not None and place is not None and build_approved(home, ts, place)):
        out.pop('build')
    return out


def build_block(home, ts, place):
    """Why this set cannot run here yet (its build command has not been agreed to), or None."""
    if not ts.get('build') or (ts.get('lang') or 'python') != 'java' or build_approved(home, ts, place):
        return None
    return (f'构建命令还没经你同意，没有执行：测试集的构建命令第一次执行、或命令 / 执行目录改了以后，要你在应用里看过实际的命令和目录、点同意'
            f'（在应用里运行这个测试集时会弹出确认框；AI 的会话不能替你同意）。\n命令：{ts["build"]}\n执行目录：{Path(place).resolve()}')


def bridge_run(root, ident):
    """Run one set for the bridge, the same way `test run` does, without recording anything."""
    home = main_checkout(root)
    scope = here_owner(root)
    scope = scope if owner_going(home, scope) else None
    ts = read_set(home, ident)
    cases = view(ts, scope)
    place = Path(root).resolve()
    unagreed = build_block(home, ts, place) if cases else None
    answers, error = ({}, unagreed) if unagreed else drive(ts.get('lang') or 'python', place, ts['entry'], cases, ts.get('timeout') or TIMEOUT,
                                                          test_options(ts, home, place)) if cases else ({}, None)
    rows = []
    for c in cases:
        if error:
            rows.append({'n': c['n'], 'name': c['name'], 'status': 'broken', 'diff': [], 'error': error})
        else:
            status, _, err, diff = judge(c, answers[c['n']])
            rows.append({'n': c['n'], 'name': c['name'], 'status': status, 'diff': diff, 'error': err})
    return {'result': 'broken' if error else 'done', 'error': error, 'cases': rows}


def bridge_report(root, lang):
    """Every in-use set of `lang` run for the bridge of that language: {"sets": [{id, title, result, error, cases}]}."""
    sets = []
    for ident, title in bridge_sets(root, lang):
        out = bridge_run(root, ident)
        sets.append({'id': ident, 'title': title, 'result': out['result'], 'error': out['error'], 'cases': out['cases']})
    return {'lang': lang, 'sets': sets}


def testset_bridge(root, path=None, lang=None):
    """Write the bridge files into this checkout's code, one per language in use (or the language asked for; Python when no set
    exists yet): Python unittest/pytest (default tests/test_relayforge_testsets.py), node --test (tests/relayforge-testsets.test.mjs),
    JUnit 5 (src/test/java/RelayForgeTestSetsTest.java, only when the project has JUnit 5). Returns the first file's path/relative/changed
    for the callers that expect one, plus files and skipped."""
    place = Path(root).resolve()
    home = main_checkout(root)
    if lang:
        lang = str(lang).lower()
        if lang not in LANGS:
            raise ValueError(f'语言只能是 {"、".join(LANGS)}。')
        langs = [lang]
    else:
        scope = here_owner(root)
        scope = scope if owner_going(home, scope) else None
        used = {(ts.get('lang') or 'python') for ts, _ in select(home, scope, everything=True)[0]}
        langs = [k for k in LANGS if k in used] or ['python']
    if path and len(langs) > 1:
        raise ValueError(f'项目里用到了 {"、".join(langs)}，--path 只能配一种语言：加上 --lang。')
    files, skipped = [], []
    for k in langs:
        if k == 'java':
            rel, why = _java_bridge_target(place, home, _check_entry(path) if path else None)
            if why:
                skipped.append({'lang': k, 'why': why})
                continue
            text = JAVA_BRIDGE.format(mark=BRIDGE_MARK)
        elif k == 'node':
            rel = _check_entry(path) if path else ('tests/relayforge-testsets.test.mjs' if (place / 'tests').is_dir() else 'relayforge-testsets.test.mjs')
            text = NODE_BRIDGE.format(mark=BRIDGE_MARK)
        else:
            rel = _check_entry(path) if path else ('tests/test_relayforge_testsets.py' if (place / 'tests').is_dir() else 'test_relayforge_testsets.py')
            text = BRIDGE.format(mark=BRIDGE_MARK)
        target = place / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        same = target.exists() and target.read_text(encoding='utf-8', errors='replace') == text
        if not same:
            target.write_text(text, encoding='utf-8')
        files.append({'lang': k, 'path': str(target), 'relative': rel, 'changed': not same})
    if not files:
        raise ValueError('；'.join(x['why'] for x in skipped))
    return dict(files[0], files=files, skipped=skipped)
