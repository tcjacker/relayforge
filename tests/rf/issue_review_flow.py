"""测试集的接入代码：问题页的「刷新」（AI 复查待处理问题）在后端的整个流程。

只按 input 搭项目、按步骤调用被测的函数（issue_review、issue_review_status、issue_review_apply、issue_review_discard、
issue_brief），把看得到的结果原样交回；不含测试数据，不做比对。
模型换成一个小脚本（RELAYFORGE_EXTRACTOR）：按标准输入里的「编号：#00x」回答 input 里给的结论，归并时回答 input 里的分组；
两个项目各有一份回答；结论写 "FAIL" 时像模型出错那样退出，写了 "block": true 时等到 release 步骤再回答（用来停在复查中途）。
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / 'backend'
sys.path.insert(0, str(BACKEND))
_HOME = tempfile.mkdtemp(prefix='rf-review-home-')
os.environ.setdefault('RELAYFORGE_HOME', _HOME)

import rf  # noqa: E402
from relayforge.issues.record import issue_files  # noqa: E402

STUB = r'''import json, os, re, sys, time
payload = sys.stdin.read()
place = os.environ["RF_REVIEW_DIR"]
answers = json.load(open(os.path.join(place, "answers-" + os.environ["RF_REVIEW_PROJECT"] + ".json"), encoding="utf-8"))
hit = re.search(r"编号：(#\d+)", payload)
key = hit.group(1) if hit else "group"
with open(os.path.join(place, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"key": key, "project": os.environ.get("RF_REVIEW_PROJECT")}) + "\n")
if key == "group":
    answer = answers.get("group") or {"groups": []}
else:
    answer = answers.get("answers", {}).get(key, answers.get("default"))
if answer == "FAIL":
    print("模型出错了", file=sys.stderr)
    sys.exit(2)
if isinstance(answer, dict) and answer.get("block"):
    for _ in range(1200):
        if os.path.exists(os.path.join(place, "release")):
            break
        time.sleep(0.05)
    answer = {k: v for k, v in answer.items() if k != "block"}
print(json.dumps(answer, ensure_ascii=False))
'''

NAMES = ('CLAUDE_PROJECT_DIR', 'CLAUDE_CODE_SESSION_ID', 'RELAYFORGE_SESSION_ID', 'RELAYFORGE_SESSION_SIG')


def _git(place, *args):
    subprocess.run(['git', *args], cwd=place, check=True, capture_output=True, text=True)


def _project(base, name, issues, states):
    root = (base / name).resolve()
    root.mkdir()
    rf.init(root, hooks=False)
    _git(root, 'init', '-q', '-b', 'main')
    _git(root, 'add', '-A')
    _git(root, 'commit', '-qm', 'init')
    for n in range(1, issues + 1):
        rf.issue_new(root, f'{name} 的问题 {n}', f'{name} 的问题 {n} 的描述', '你')
    for number, state in (states or {}).items():
        _set_state(root, number, state)
    return root


def _set_state(root, number, state):
    path, meta, sections = rf.find_issue(root, number)
    meta['state'] = state
    rf.write_doc(path, meta, sections)


def _num(ident):
    return ('#' + ident[2:]) if ident else None


def run(input):
    saved = {k: os.environ.get(k) for k in ('RELAYFORGE_HOME', 'RELAYFORGE_EXTRACTOR', 'RF_REVIEW_DIR', 'RF_REVIEW_PROJECT',
                                            rf.GUARD, *NAMES,
                                            'GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL')}
    with tempfile.TemporaryDirectory(prefix='rf-review-') as folder:
        base = Path(folder).resolve()
        stub = base / 'stub.py'
        stub.write_text(STUB, encoding='utf-8')
        os.environ.update({'RELAYFORGE_HOME': str(base / 'home'), 'RELAYFORGE_EXTRACTOR': json.dumps([sys.executable, str(stub)]),
                           'RF_REVIEW_DIR': str(base), 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
                           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'})
        for name in (rf.GUARD, *NAMES):
            os.environ.pop(name, None)
        try:
            return _scenario(base, input)
        finally:
            (base / 'release').touch()  # 别让停在中途的假模型一直等
            for job_pid in _bg_pids:
                try:
                    os.killpg(job_pid, signal.SIGKILL)
                except OSError:
                    pass
            _bg_pids.clear()
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


_bg_pids = []


def _write_answers(base, data, project='A'):
    (base / f'answers-{project}.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def _scenario(base, input):
    projects = {'A': _project(base, 'A', input.get('issues', 0), input.get('states'))}
    if 'other_project' in input:
        other = input['other_project']
        projects['B'] = _project(base, 'B', other.get('issues', 0), other.get('states'))
    _write_answers(base, {'answers': input.get('answers', {}), 'default': input.get('default'), 'group': input.get('group')})
    if 'other_project' in input:  # 两个项目的问题编号会重，各答各的
        other = input['other_project']
        _write_answers(base, {'answers': other.get('answers', {}), 'default': other.get('default'), 'group': other.get('group')}, 'B')
    current = {'name': 'A'}
    os.environ['RF_REVIEW_PROJECT'] = 'A'
    reviews = []  # 见过的复查，按出现的先后编成第 1、2… 次

    def root():
        return projects[current['name']]

    def summary(job):
        out = {'state': job.get('state')}
        if job.get('id'):  # 复查编号只精确到秒：同一秒里重来的另一次靠运行它的进程区分
            key = (job['id'], job.get('pid'))
            if key not in reviews:
                reviews.append(key)
            out['review_no'] = reviews.index(key) + 1
        if 'message' in job:
            out['message'] = job['message']
        if 'items' in job:
            out['items'] = [x['num'] for x in job['items']]
            out['done'] = job.get('done')
            out['current'] = _num(job.get('current'))
        if job.get('state') == 'ready':
            out['rows'] = [{'num': r['num'], 'kind': r['kind'], 'target': _num(r.get('target')), 'pickable': bool(r.get('stamp'))}
                           for r in job.get('rows', [])]
        if job.get('error'):
            out['error'] = job['error']
        return out

    def wait(until):
        status = rf.issue_review_status(root())
        for _ in range(1200):
            if until(status):
                break
            time.sleep(0.05)
            status = rf.issue_review_status(root())
        return status

    results = []
    for step in input.get('steps', []):
        do = step['do']
        try:
            if do == 'refresh':
                got = summary(rf.issue_review(root()))
            elif do == 'refresh_bg':  # 应用点「刷新」走的路：另起后台进程
                job = rf.issue_review(root(), background=True)
                if job.get('pid') and job.get('state') == 'running' and job['pid'] != os.getpid():
                    _bg_pids.append(job['pid'])
                got = summary(job)
                if not step.get('progress'):  # 刚起的后台进程查到哪了不固定，只在要看进度时交回
                    got.pop('done', None)
                    got.pop('current', None)
            elif do == 'wait_done':  # 等到查完 n 条、下一条已经交给模型（假模型已经收到）
                n = step['done']

                def reached(s):
                    if s.get('state') != 'running':
                        return True
                    item = next((x for x in s.get('items', []) if x['id'] == s.get('current')), None)
                    asked = (_calls(base).get(os.environ['RF_REVIEW_PROJECT']) or [])
                    return s.get('done', 0) >= n and item and item['status'] == 'running' and item['num'] in asked
                got = summary(wait(reached))
            elif do == 'wait_finish':
                got = summary(wait(lambda s: s.get('state') != 'running'))
            elif do == 'status':
                got = summary(rf.issue_review_status(root()))
            elif do == 'release':
                (base / 'release').touch()
                got = 'ok'
            elif do == 'kill':
                pid = rf.issue_review_status(root()).get('pid')
                os.killpg(int(pid), signal.SIGKILL)
                for _ in range(200):  # 等进程真的没了
                    try:
                        os.kill(int(pid), 0)
                    except OSError:
                        break
                    try:
                        os.waitpid(int(pid), os.WNOHANG)
                    except ChildProcessError:
                        pass
                    time.sleep(0.05)
                got = 'ok'
            elif do == 'answers':
                _write_answers(base, {'answers': step.get('answers', {}), 'default': step.get('default'), 'group': step.get('group')},
                               current['name'])
                got = 'ok'
            elif do == 'set_state':
                _set_state(root(), step['num'], step['state'])
                got = 'ok'
            elif do == 'apply':
                review = rf.issue_review_status(root()).get('id')
                selected = [rf.find_issue(root(), x)[1]['id'] for x in step['selected']]
                out = rf.issue_review_apply(root(), {'review': review, 'selected': selected})
                got = {'applied': [_num(x) for x in out['applied']], 'skipped': [x['reason'] for x in out['skipped']]}
            elif do == 'discard':
                got = summary(rf.issue_review_discard(root()))
            elif do == 'project':
                current['name'] = step['name']
                os.environ['RF_REVIEW_PROJECT'] = step['name']
                got = 'ok'
            elif do == 'pending':
                got = sum(1 for _, meta, _ in issue_files(root()) if (meta.get('state') or 'todo') == 'todo')
            elif do == 'states':
                got = {_num(meta['id']): meta.get('state') or 'todo' for _, meta, _ in issue_files(root())}
                got = dict(sorted(got.items()))
            elif do == 'brief':
                text = rf.issue_brief(root(), step['num'])
                got = [line.strip() for line in text.splitlines() if line.strip().startswith(('复现步骤：', '实际结果：'))]
            else:
                raise ValueError(f'不认识的步骤 {do}')
        except ValueError as e:
            got = {'error': str(e)}
        results.append(got)
    return {'steps': results, 'final': _final(projects, input), 'model_calls': _calls(base)}


def _final(projects, input):
    out = {}
    for name, place in projects.items():
        issues = {}
        for _, meta, _ in issue_files(place):
            number = _num(meta['id'])
            show = rf.issue_show(place, meta['id'])
            entry = {'state': meta.get('state') or 'todo'}
            if meta.get('closed_as'):
                entry['closed_as'] = meta['closed_as']
            if meta.get('duplicate_of'):
                entry['duplicate_of'] = _num(meta['duplicate_of'])
            reviews = [{k: e[k] if k != 'target' else _num(e[k]) for k in ('kind', 'text', 'ran', 'steps', 'observed', 'target') if e.get(k)}
                       for e in show['thread'] if e.get('t') == 'review']
            if reviews:
                entry['reviews'] = reviews
            issues[number] = entry
        out[name] = {'pending': sum(1 for x in issues.values() if x['state'] == 'todo'), 'issues': dict(sorted(issues.items()))}
    return out if len(out) > 1 else out['A']


def _calls(base):
    try:
        lines = (base / 'calls.jsonl').read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}
    out = {}  # 按项目分开：两个项目同时复查时，彼此的先后不固定
    for line in lines:
        call = json.loads(line)
        out.setdefault(call['project'] or '', []).append(call['key'])
    return out
