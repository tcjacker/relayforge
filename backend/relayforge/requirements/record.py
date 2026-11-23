"""需求：一个需求一条线，一个分支，一份自己的工作目录。新建、立项、完成、放弃，清晰度和影响。

需求文件和它带来的事实只在项目主目录（main checkout）的 .relayforge/ 里有一份；需求的分支和工作目录只改代码。
工作目录里检出的 .relayforge/ 是开分支那一刻的旧副本，不作数：这里所有读写都指向主目录。"""

import datetime as dt
import errno
import json
import os
import re
import secrets
import subprocess
from contextlib import contextmanager
from pathlib import Path

from ..core.common import (
    DIR, link_worktree, log, PLACEHOLDER, REQ_HEADS, TREES, branch_of, git, main_checkout, run_git, slug, today,
    commit_facts, relayforge_home,
)
from .owners import branch_places, own_requirement
from ..sessions.archivestore import archive_state
from ..core.documents import add_line, all_docs, first_item, item_line, live_items, read_doc, write_doc
from ..core.transcript import clean
from ..facts.designs import design_files, design_view
from ..sessions.record import load_sessions, plan_finished, stable_tag
from ..facts.facts import fact_index
from ..facts.mapfacts import map_session_id
from ..sessions.trailers import ensure_here
from .testrecords import coverage, testing_line, testing_view
from .intake import assessment_view
from ..core.language import display_text
from ..core.trust import app_trusted, user_trusted


def open_worktree(home, ident, branch, what='需求'):
    """A branch and a working directory of its own under .worktrees/, off the branch the project directory has checked out.
    Requirements and issue fixes both work this way, so their sessions never mix with each other or with the user's."""
    place = home / TREES / ident
    if subprocess.run(['git', '-C', str(home), 'check-ignore', '-q', str(place)], capture_output=True).returncode != 0:
        common = git(home, 'rev-parse', '--git-common-dir').strip()
        if common:
            exclude = (home / common).resolve() / 'info' / 'exclude'
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open('a', encoding='utf-8') as f:
                f.write(f'{TREES}/\n')
    base = target_branch(home) or next((b for b in ('main', 'master', 'origin/main', 'origin/master') if git(home, 'rev-parse', '--verify', '--quiet', b).strip()), 'HEAD')
    done = subprocess.run(['git', '-C', str(home), 'worktree', 'add', '-q', '-b', branch, str(place), base], capture_output=True, text=True)
    if done.returncode != 0:
        raise ValueError(f'没能为这个{what}开出分支：' + (done.stderr.strip().splitlines() or ['git 没有给出原因'])[-1])
    link_worktree(home, place)  # 数据外置：工作目录里的 .relayforge 也指向同一份记录
    return place


SESSION_PHASES = ('intake', 'development', 'testing')
# 测试会话的 CLI 和模型是可选的：没单独设置（文件头里没有 testing_cli）就「跟着开发会话」——不复制一份开发会话的值，以后开发会话的配置改了，测试会话跟着变。
# 老需求没有这个字段，同样跟着开发。要单独设置也可以，建好以后用 req choose 改（只由用户触发）；改了只对下一次新开的测试会话生效，已经开着的不动。
FOLLOWING = 'development'


def valid_session_model(cli, model):
    suffix = r'(?:\[1m\])?' if cli == 'claude' else ''
    return isinstance(model, str) and re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.:-]*' + suffix, model) is not None


def req_new(root, title, *, split_key=None, source='', split_context=(), **session_fields):
    """Create a requirement: a record, a branch and a working directory, so its sessions never mix with others."""
    title = ' '.join(title.split())
    if not title:
        raise ValueError('需求要有一个标题。')
    session_meta = {}
    for phase in SESSION_PHASES:
        cli = session_fields.get(f'{phase}_cli')
        model = session_fields.get(f'{phase}_model')
        if cli is not None and cli not in ('claude', 'codex'):
            raise ValueError('需求会话的 CLI 请选择 Claude Code 或 Codex。')
        if model is not None and (cli is None or not valid_session_model(cli, model)):
            raise ValueError('请为需求会话指定 CLI 和有效的模型名称。')
        if cli is not None:
            session_meta[f'{phase}_cli'] = cli
        if model is not None:
            session_meta[f'{phase}_model'] = model
    home = main_checkout(root)
    ident = f'R-{dt.datetime.now():%Y%m%d}-{secrets.token_hex(2)}'
    branch = f'req/{ident[2:].lower()}'
    open_worktree(home, ident, branch)
    meta = {'id': ident, 'type': 'requirement', 'title': title, 'status': 'current', 'state': 'draft', 'branch': branch,
            'sessions': [], 'created': f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}', 'updated': today()}
    meta.update(session_meta)
    sections = {h: [] for h in REQ_HEADS}
    if split_key:
        meta['split_key'] = split_key
        origin, reason, scope = split_context
        sections['需求说明'] = [item_line(origin, source, True), item_line(reason, source, False),
                            item_line(scope, source, False)]
    add_line(sections, '变更记录', f'- {today()} 新建。')
    path = home / DIR / 'requirements' / f'{ident}-{slug(title)[:30]}.md'  # in the main checkout; the title may be a whole sentence, the file name need not be
    path.parent.mkdir(parents=True, exist_ok=True)
    write_doc(path, meta, sections)  # committed with the other facts at the next moment (a session stopping, 立项)
    ensure_here(home / TREES / ident)  # commits made in this working directory carry the requirement's trailers (trailers.py)
    return next(r for r in load_requirements(home) if r['id'] == ident)


REQ_STATES = {'draft': '立项中', 'open': '进行中', 'done': '已完成', 'dropped': '已取消'}
# 看板上的六栏。open 的需求分进行中、测试中和待验收三栏：程序或用户明确改过（manual_stage）就按改过的，没改过就看计划是不是都做完了：
# 做完了，立项时就带着测试阶段的需求（test_flow）进测试中，以前的老需求照原来的规则进待验收。
STAGES = {'talk': '立项中', 'build': '进行中', 'test': '测试中', 'check': '待验收', 'done': '已完成', 'dropped': '已取消'}


def stage_of(state, manual=None, done=0, steps=0, checks=True, flow=False):
    """Which board column a requirement is in. Old requirements written without an acceptance condition still count as 立项中."""
    if state in ('done', 'dropped'):
        return state
    if state == 'draft' or not checks:
        return 'talk'
    if manual in ('build', 'test', 'check'):
        return manual
    if steps and done == steps:
        return 'test' if flow else 'check'
    return 'build'


def plan_progress(session):
    plan = [step for step in (session or {}).get('plan', []) if step['status'] != 'dropped']
    return sum(step['status'] == 'done' for step in plan), len(plan)


def stage_blocked(stage, to, gate_ok, waiting=()):
    """Why a requirement in `stage` may not be moved to `to` by hand, or None when it may. The rules are strict:
    finished and cancelled requirements are final, a started one never goes back to 立项中, and leaving 立项中 for any
    working column needs at least one acceptance condition, the same as the 立项 gate. `waiting` are the prerequisites
    (depends_on) not done yet: while there are any, it may not leave 立项中, and a started one may not be finished."""
    if to not in STAGES:
        return f'没有「{to}」这个状态。'
    if stage == to:
        return '当前就是这个状态'
    if stage == 'done':
        return '已合并进主线，不能再改'
    if stage == 'dropped':
        return '已取消的需求只读'
    if to == 'talk':
        return '已立项的需求不能退回立项中'
    if stage == 'talk' and to in ('build', 'test', 'check', 'done') and not gate_ok:
        return '还没有验收条件，先把需求聊到「可验收」'
    if waiting and (to == 'done' or (stage == 'talk' and to in ('build', 'test', 'check'))):
        return waiting_reason(waiting, 'done' if stage != 'talk' else 'start')
    return None


def waiting_reason(waiting, what='start'):
    """Why a requirement still waits for its prerequisites (dicts with title and state): to start developing, or to be finished."""
    dropped = [w for w in waiting if w.get('state') == 'dropped']
    if dropped:
        return f'前置需求{"、".join("《" + str(w["title"]) + "》" for w in dropped)}已取消，先去掉这条依赖，或者取消这个需求'
    names = '、'.join('《' + str(w['title']) + '》' for w in waiting)
    return f'要等{names}确认完成后才能' + ('开始开发' if what == 'start' else '确认完成')
ID_PATTERN = re.compile(r'\b((?:RF|M)-\d+)\b')


# How clear a requirement is, from vague to concrete. Each level is computed from the requirement file and its design drafts.
# `x` carries what is not in the sections: how many design drafts the requirement has and why 方案 was skipped, if it was.
# A level marked `later` is shown but not yet checked; to add one, give it a `test` once it can be checked.
LEVELS = [
    {'key': 'idea', 'name': ('想法', 'Idea'), 'need': ('有一句话的标题', 'Write a one-line title'), 'test': lambda s, x: True},
    {'key': 'direction', 'name': ('方向', 'Scope'), 'need': ('写下需求说明，并且至少有一个要改的功能点',
              'Describe the requirement and name at least one feature to change'),
     'test': lambda s, x: bool(live_items(s.get('需求说明'))) and bool(live_items(s.get('要改的功能点')))},
    {'key': 'design', 'name': ('方案', 'Design'), 'need': ('至少一份界面和交互的设计稿；没有界面的需求说明理由后跳过',
              'Add a design draft, or explain why the requirement needs no UI'),
     'test': lambda s, x: bool(x.get('designs')) or bool(x.get('skipped'))},
    {'key': 'acceptance', 'name': ('可验收', 'Acceptance'), 'need': ('至少有一条验收条件', 'Add at least one acceptance criterion'),
     'test': lambda s, x: bool(live_items(s.get('验收条件')))},
    # 立项会话出测试用例（业务的话：前提、操作、预期），每条验收条件要么至少有一条用例，要么标了「只能人工看」；这一级没到不挡立项，只提示
    {'key': 'cases', 'name': ('用例', 'Test cases'), 'need': ('每条验收条件至少有一条测试用例，或者标了「只能人工看」',
              'Cover each acceptance criterion with a test case or mark it for manual review'),
     'test': lambda s, x: bool(live_items(s.get('验收条件'))) and not _uncovered(s)},
]


def _uncovered(sections):
    return coverage(sections)


def clarity(sections, designs=0, skipped=None):
    """The ladder of levels with what each one still lacks, and the highest level reached without a gap.

    方案 counts as passed when the requirement has a design draft, or when it was skipped with a reason; a skip is shown as
    a skip, not as done."""
    extra = {'designs': designs, 'skipped': skipped or ''}
    ladder, level, gap = [], LEVELS[0]['key'], False
    criteria = display_text('验收条件', 'Acceptance criteria')
    for spec in LEVELS:
        later = bool(spec.get('later'))
        reached = False if later else bool(spec['test'](sections, extra))
        missing = []
        if spec['key'] == 'direction':
            missing = [display_text(head, en) for head, en in (('需求说明', 'Description'), ('要改的功能点', 'Features to change'))
                       if not live_items(sections.get(head))]
        elif spec['key'] == 'design' and not reached:
            missing = [display_text('设计稿（没有界面就说明理由跳过）', 'Design draft or a reason to skip it')]
        elif spec['key'] == 'acceptance' and not reached:
            missing = [criteria]
        elif spec['key'] == 'cases' and not reached:
            gaps = _uncovered(sections)
            missing = [display_text(f'第 {n} 条验收条件的用例', f'Test case for acceptance criterion {n}')
                       for n, _ in gaps] if gaps else [criteria]
        step = {'key': spec['key'], 'name': display_text(*spec['name']), 'need': display_text(*spec['need']),
                'reached': reached, 'later': later, 'missing': missing}
        if spec['key'] == 'design':
            step.update(designs=designs, skipped=extra['skipped'] if reached and not designs else '')
        ladder.append(step)
        if later:
            continue
        if reached and not gap:
            level = spec['key']
        elif not reached:
            gap = True
    return level, ladder


def gate(sections):
    """What the user may pass from 立项 to 开发 with: at least one acceptance condition that was not rejected."""
    checks = live_items(sections.get('验收条件'))
    gaps = _uncovered(sections) if checks else []
    missing = display_text('还没有验收条件：至少要有一条，说清楚怎么判断它做好了。',
                           'Add at least one acceptance criterion that describes how to verify the requirement.')
    hint = ''
    if gaps:
        # 只提示、不拦：这几条验收条件既没有测试用例，也没标「只能人工看」
        zh_items = '；'.join(f'第 {n} 条「{text}」' for n, text in gaps)
        en_items = ', '.join(f'{n}. “{text}”' for n, text in gaps)
        hint = display_text(f'还有 {len(gaps)} 条验收条件没有测试用例、也没标「只能人工看」：{zh_items}'
                            '。可以先立项，开发和测试会照着现有的用例做。',
                            f'{len(gaps)} acceptance criteria need test cases or manual review: {en_items}. '
                            'You can start development and testing with the existing test cases.')
    return {'ok': bool(checks), 'missing': '' if checks else missing,
            'uncovered': [{'n': n, 'text': text} for n, text in gaps], 'hint': hint}


def brief(sections, facts):
    """Program-computed grounding for the features a requirement names: what exists, what depends on it, which decisions govern it."""
    known, unknown, proposed, named = [], [], [], []
    for line in live_items(sections.get('要改的功能点')):
        ids = list(dict.fromkeys(ID_PATTERN.findall(line)))
        for ident in ids:
            if ident in named:
                continue
            named.append(ident)
            kind = 'features' if ident.startswith('RF-') else 'modules'
            found = facts[kind].get(ident)
            if found:
                meta = found[0]
                known.append({'id': ident, 'title': meta.get('title') or '', 'kind': 'feature' if kind == 'features' else 'module',
                              'status': meta.get('status') or '', 'module': meta.get('module') or ''})
            else:
                unknown.append(ident)
        if not any(i.startswith('RF-') for i in ids):
            module = next((i for i in ids if i.startswith('M-')), '')
            proposed.append({'text': line, 'module': module if module in facts['modules'] else ''})
    features = {k['id'] for k in known if k['kind'] == 'feature'}
    scope = features | {k['id'] for k in known if k['kind'] == 'module'} | {k['module'] for k in known if k['module']} | {p['module'] for p in proposed if p['module']}
    dependents = []
    for ident, (meta, _) in sorted(facts['features'].items()):
        on = [str(x) for x in meta.get('depends_on') or [] if str(x) in features]
        if on and ident not in features:
            dependents.append({'id': ident, 'title': meta.get('title') or '', 'on': on})
    decisions = []
    for meta, body in facts['decisions']:
        covers = [str(x) for x in meta.get('applies_to') or [] if str(x) in scope]
        if covers:
            decisions.append({'id': meta['id'], 'title': meta.get('title') or '', 'decision': first_item(body, '决定'), 'applies_to': covers})
    decisions.sort(key=lambda d: d['id'])
    designs = []
    for k in known:
        found = facts.get('designs', {}).get(k['id']) if k['kind'] == 'feature' else None
        if found:
            designs.append({'feature': k['id'], 'feature_title': k['title'], 'id': found['id'], 'title': found['title'], 'html': found['html'],
                            'path': found.get('path') or found['html'], 'requirement': found['requirement']})
    issues = [dict(i, feature_title=facts['features'].get(i['feature'], ({},))[0].get('title') or '') for i in facts.get('issues') or [] if i['feature'] in features]
    return {'known': known, 'unknown': unknown, 'proposed': proposed, 'dependents': dependents, 'decisions': decisions, 'designs': designs, 'issues': issues}


def brief_text(req):
    """The brief as plain lines, for the AI running `req brief` and for a person in a terminal."""
    b = req['brief']
    out = [f'{req["id"]} {req["title"]}（{REQ_STATES.get(req["state"], req["state"])}，清晰度：{next(l["name"] for l in req["ladder"] if l["key"] == req["level"])}）', '']
    out.append('要改的功能点里引用的编号：')
    out += [f'- {k["id"]} {k["title"]}' + ('（模块）' if k['kind'] == 'module' else f'（属于 {k["module"]}）' if k['module'] else '') + ('' if k['status'] == 'current' else '（还在待确认）') for k in b['known']] or ['- 没有。']
    if b['unknown']:
        out += ['', '这些编号在事实里不存在（写错了，或者已经被替代）：'] + [f'- {x}' for x in b['unknown']]
    if b['proposed']:
        out += ['', '没有写功能点编号的行，当作新增的功能点：'] + [f'- {p["text"]}' + (f'（归到 {p["module"]}）' if p['module'] else '（还没说归到哪个模块）') for p in b['proposed']]
    out += ['', '依赖它们的功能点（改动可能影响到）：']
    out += [f'- {d["id"]} {d["title"]}：依赖 {"、".join(d["on"])}' for d in b['dependents']] or ['- 没有。']
    out += ['', '管着它们或它们所在模块的现行裁定（注意别冲突）：']
    out += [f'- {d["id"]} {d["title"]}：{d["decision"] or "（没有写决定）"}' for d in b['decisions']] or ['- 没有。']
    out += ['', '这些功能点现在的设计稿（画方案时先读这个文件，在它的基础上改，保持现有界面的样子）：']
    out += [f'- {d["feature"]} {d["feature_title"]}：{d["id"]} {d["title"]} → {d.get("path") or d["html"]}' for d in b.get('designs') or []] or ['- 没有。要改的功能点都还没有设计稿。']
    states = {'todo': '待处理', 'fixing': '处理中', 'review': '待你确认'}
    out += ['', '这些功能点身上还没解决的问题（立项时考虑要不要一起解决，或者会不会受影响）：']
    out += [f'- #{i["id"][2:]} {i["title"]}（{i["feature"]} {i["feature_title"]}，{states.get(i["state"], i["state"])}' + ('，以后再说' if i['deferred'] else '') + '）'
            for i in b.get('issues') or []] or ['- 没有。']
    own = req.get('designs') or []
    step = next((l for l in req['ladder'] if l['key'] == 'design'), {})
    out += ['', '这个需求自己的设计稿（修改时用 rf.py design save --id 编号 覆盖同一份）：']
    out += [f'- {d["id"]} {d["title"]}（画的：{"、".join(d["features"] + ["新增：" + x for x in d["new"]]) or "没写"}）→ {d["path"]}' for d in own] or \
        [f'- 没有，方案已跳过：{step["skipped"]}' if step.get('skipped') else '- 还没有。']
    return '\n'.join(out)


def requirement_event_date(sections, event):
    # 确认完成和取消的日期在变更记录里，updated 可能随之后的编辑变化。
    for line in reversed(sections.get('变更记录', [])):
        match = re.match(r'- (\d{4}-\d{2}-\d{2}) ' + event, line)
        if match:
            return match[1]
    return ''


# ---------- 前置需求 ----------
# depends_on（需求文件头）：这些需求确认完成以后，这个需求才能开始开发；已经开始开发的，要等它们完成才能确认完成。
# 只由用户设置：req depend（应用里的「前置需求」），或者同意一条写了先后的拆分建议（splits.py）。不能依赖自己，不能形成环。
# 每条依赖可以写一句要用到对方的什么，记在需求文件的「前置需求」一节（一行一条：「- 编号：要用到什么」；文件头只放得下平铺的列表）。
# 被等的需求以后要拆分时，它的会话靠这一句知道哪些东西被别人等着、该留在哪一块

NEEDS = '前置需求'
NEED_LINE = re.compile(r'^- (R-\d{8}-[a-f0-9]{4})[：:]\s*(.*?)\s*(?:〔[^〕]*〕)?$')


def depends_of(meta):
    return [str(x) for x in meta.get('depends_on') or [] if str(x).strip()]


def dependency_needs(sections):
    """What this requirement needs from each prerequisite, from its 前置需求 section: {id: text}."""
    out = {}
    for line in sections.get(NEEDS) or []:
        m = NEED_LINE.match(line)
        if m and m[2]:
            out[m[1]] = m[2]
    return out


def write_need(sections, other, need):
    """Set (or with an empty `need`, take away) the line of `other` in the 前置需求 section, kept before the 变更记录."""
    kept = [line for line in sections.get(NEEDS) or [] if not (NEED_LINE.match(line) and NEED_LINE.match(line)[1] == other)
            and line != PLACEHOLDER]
    if need:
        kept.append(f'- {other}：{need}')
    if not kept:
        sections.pop(NEEDS, None)
        return
    if NEEDS not in sections:
        order = [h for h in sections if h != '变更记录'] + [NEEDS] + (['变更记录'] if '变更记录' in sections else [])
        rest = dict(sections)
        sections.clear()
        sections.update({h: rest.get(h, []) for h in order})
    sections[NEEDS] = kept


def waiting_on(meta, index):
    """The prerequisites of a requirement that are not done yet, as {id, title, state}. A cancelled one still counts: it is never
    going to be done, and the user decides what to do (drop the dependency or the requirement). Unknown ids are ignored."""
    out = []
    for ident in depends_of(meta):
        other = index.get(ident)
        if other is not None and (other.get('state') or 'open') != 'done':
            out.append({'id': ident, 'title': other.get('title') or ident, 'state': other.get('state') or 'open'})
    return out


def finished_lines(view):
    """What a finished requirement brought, from its 完成记录 (written by flows._finish): the counts and the new / changed facts."""
    if view['state'] != 'done':
        return []
    return [line for line in view['sections'].get('变更记录', []) if re.search(r'改动：|新增：|修改：', line)][-4:]


def link_dependencies(out):
    """Fill in, on the list load_requirements builds, each requirement's prerequisites (with their stage and progress) and the
    requirements still waiting for it (and whether that one can start now)."""
    by_id = {r['id']: r for r in out}
    ids = {r['id']: r['depends_on'] for r in out}
    for r in out:
        r['depends_on'] = [{'id': d, 'title': by_id[d]['title'], 'state': by_id[d]['state'], 'stage': by_id[d]['stage'],
                            'done': by_id[d]['done'], 'steps': by_id[d]['steps'], 'finished': finished_lines(by_id[d]),
                            'need': r['depend_needs'].get(d, '')}
                           for d in ids[r['id']] if d in by_id]
        r['dropped_deps'] = [d['id'] for d in r['depends_on'] if d['state'] == 'dropped']
        # 在等它的需求，各自要用到它的什么：它要拆分时据此决定这些东西留在哪一块
        r['dependents'] = [{'id': x['id'], 'title': x['title'], 'state': x['state'], 'ready': not x['waiting'],
                            'need': x['depend_needs'].get(r['id'], '')}
                           for x in out if r['id'] in ids[x['id']] and x['state'] in ('draft', 'open')]


def _reaches(index, start, target, seen=None):
    """Whether `start` depends on `target`, directly or through other requirements."""
    seen = set() if seen is None else seen
    for ident in depends_of(index.get(start) or {}):
        if ident == target:
            return True
        if ident not in seen:
            seen.add(ident)
            if _reaches(index, ident, target, seen):
                return True
    return False


def requirement_index(home):
    """id -> meta of every requirement in the main checkout, for the dependency checks."""
    out = {}
    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        meta = read_doc(path)[0]
        if meta.get('id'):
            out[str(meta['id'])] = dict(meta, _path=path)
    return out


def dependency_problem(index, ident, other):
    """Why `ident` may not get `other` as a prerequisite, or None."""
    mine, theirs = index.get(ident), index.get(other)
    if mine is None:
        return f'没有找到需求 {ident}。'
    if (mine.get('state') or 'open') in ('done', 'dropped'):
        return f'《{mine.get("title")}》已经{"完成" if mine.get("state") == "done" else "取消"}，不能再改前置需求。'
    if other == ident:
        return '不能把需求自己设为前置需求。'
    if theirs is None:
        return f'没有找到需求 {other}。'
    if (theirs.get('state') or 'open') == 'done':
        return f'《{theirs.get("title")}》已经完成，不用等它。'
    if theirs.get('state') == 'dropped':
        return f'《{theirs.get("title")}》已经取消，等不到它完成。'
    if other in depends_of(mine):
        return f'《{theirs.get("title")}》已经是前置需求了。'
    if _reaches(index, other, ident):
        return f'《{theirs.get("title")}》已经在等这个需求（直接或间接），设了会互相等待。'
    return None


def add_dependency(index, ident, other, how, need=''):
    """Record `other` as a prerequisite of `ident` in its file (the caller commits). `how` says who set it, for the 变更记录; `need`
    what `ident` needs from it. Updates `index` so that several additions in a row are checked against each other."""
    problem = dependency_problem(index, ident, other)
    if problem:
        raise ValueError(problem)
    path = index[ident]['_path']
    meta, sections = read_doc(path)
    meta['depends_on'] = depends_of(meta) + [other]
    meta['updated'] = today()
    started = (meta.get('state') or 'open') == 'open'
    need = ' '.join(str(need or '').split())
    write_need(sections, other, need)
    add_line(sections, '变更记录', f'- {today()} {how}把《{index[other].get("title")}》（{other}）设为前置需求'
             + (f'（要用到：{need}）' if need else '') + '：它确认完成后，这个需求才能'
             + ('确认完成（已经在开发了）。' if started else '开始开发。'))
    write_doc(path, meta, sections)
    index[ident] = dict(meta, _path=path)


def drop_dependency(index, ident, other, how, note=''):
    """Take `other` off the prerequisites of `ident` in its file (the caller commits). `how` says who did it, `note` why, for the
    变更记录. Updates `index` like add_dependency."""
    mine = index.get(ident)
    if mine is None:
        raise ValueError(f'没有找到需求 {ident}。')
    if other not in depends_of(mine):
        raise ValueError(f'{other} 不是《{mine.get("title")}》的前置需求。')
    path = mine['_path']
    meta, sections = read_doc(path)
    meta['depends_on'] = [d for d in depends_of(meta) if d != other]
    if not meta['depends_on']:
        del meta['depends_on']
    meta['updated'] = today()
    write_need(sections, other, '')
    title = (index.get(other) or {}).get('title') or other
    add_line(sections, '变更记录', f'- {today()} {how}去掉了前置需求《{title}》（{other}）{note}。')
    write_doc(path, meta, sections)
    index[ident] = dict(meta, _path=path)


def set_need(index, ident, other, need, how):
    """Change what `ident` needs from `other`, a prerequisite it already has (the caller commits)."""
    mine = index.get(ident)
    if mine is None or other not in depends_of(mine):
        raise ValueError(f'{other} 不是《{(mine or {}).get("title") or ident}》的前置需求。')
    path = mine['_path']
    meta, sections = read_doc(path)
    need = ' '.join(str(need or '').split())
    write_need(sections, other, need)
    meta['updated'] = today()
    title = (index.get(other) or {}).get('title') or other
    add_line(sections, '变更记录', f'- {today()} {how}' + (f'写明了要用到《{title}》（{other}）的什么：{need}。' if need else f'去掉了要用到《{title}》的什么的说明。'))
    write_doc(path, meta, sections)
    index[ident] = dict(meta, _path=path)


def req_depend(root, ident, other, remove=False, need=None):
    """The user sets or removes a prerequisite (rf.py req depend; only the user: see trust.USER_COMMANDS). `need`: what this one
    needs from it; given for a prerequisite it already has, it only changes that."""
    home = main_checkout(root)
    index = requirement_index(home)
    ident, other = str(ident).strip(), str(other).strip()
    if remove:
        drop_dependency(index, ident, other, '用户')
    elif need is not None and other in depends_of(index.get(ident) or {}):
        set_need(index, ident, other, need, '用户')
    else:
        add_dependency(index, ident, other, '用户', need or '')
    commit_facts(home, f'需求的前置需求：{index[ident].get("title")}')
    return find_requirement(home, ident)


def start_sync_view(meta):
    """What happened when the requirement started: {base, commits, ok, files}, or None (nothing to bring in, or an older one)."""
    if meta.get('start_sync') not in ('ok', 'conflict'):
        return None
    commits = str(meta.get('start_sync_commits') or 0)
    return {'base': str(meta.get('start_sync_base') or ''), 'commits': int(commits) if commits.isdigit() else 0,
            'ok': meta['start_sync'] == 'ok', 'files': [str(x) for x in meta.get('start_sync_files') or []]}


def sync_on_start(home, found):
    """Bring the requirement's branch up to the branch it will merge into, as it starts development. Its branch was cut when the
    card was made, before its prerequisites were merged: without this, it would not have their code. A branch with no commits
    of its own is fast-forwarded; one that has some is merged only when it has prerequisites (otherwise the usual sync before
    merging back takes care of it), and on a conflict the merge is undone and left to the development session.
    Returns {base, commits, ok, files} or None when there was nothing to do."""
    place, branch, base = Path(found['worktree']), found['branch'], target_branch(home)
    if not base or not place.is_dir():
        return None
    if run_git(home, 'merge-base', '--is-ancestor', base, branch).returncode == 0:
        return None  # already has everything the base has
    count = git(home, 'rev-list', '--count', f'{branch}..{base}').strip()
    result = {'base': base, 'commits': int(count) if count.isdigit() else 0, 'ok': True, 'files': []}
    if run_git(home, 'merge-base', '--is-ancestor', branch, base).returncode == 0:
        done = run_git(place, 'merge', '--ff-only', '-q', base)
        if done.returncode != 0:
            result.update(ok=False, files=[(done.stderr or done.stdout).strip().splitlines()[-1:] or ['git 没有给出原因']][0])
        return result
    if not found.get('depends_on'):
        return None
    done = run_git(place, 'merge', '--no-edit', '-q', base)
    if done.returncode != 0:
        files = git(place, 'diff', '--name-only', '--diff-filter=U').splitlines()
        run_git(place, 'merge', '--abort')
        result.update(ok=False, files=files or [(done.stderr or done.stdout).strip().splitlines()[-1:] or ['git 没有给出原因']][0])
    return result


def load_requirements(root):
    """Every requirement, read from the main checkout only. The worktrees are only asked which branch they have out, to
    know where a requirement's sessions run and what to merge."""
    home = main_checkout(root)
    sessions = {s.get('session_id'): s for s in load_sessions(home)}
    tagmap = {str(k): s['id'] for k, s in sessions.items() if k and s.get('id')}
    places = branch_places(home)
    facts = {}
    out = []
    # 只读主目录。以前的布局里只在需求分支上的需求（它们留下的 .worktrees/R-… 和 req/… 分支）不读、也不动
    records = [(path, *read_doc(path), False) for path in sorted((home / DIR / 'requirements').glob('*.md'))]
    drawn_all = [design_view(home, item) for item in design_files(home, ('designs', 'history'))]
    index = {meta['id']: meta for _, meta, _, _ in records if meta.get('id')}
    for path, meta, sections, archived in records:
        if not meta.get('id'):
            continue
        waiting = waiting_on(meta, index)
        ids = [str(x) for x in meta.get('sessions') or []]
        tests = [str(x) for x in meta.get('test_sessions') or []]
        dev = [x for x in ids if x not in tests]  # 进展和计划看开发（立项）会话；测试会话的进展另给
        last = sessions.get(dev[-1]) if dev else None
        done, steps = plan_progress(last)
        state = meta.get('state') or 'open'
        place = places.get(str(meta.get('branch') or ''))
        own = bool(place) and place != home and state != 'dropped'  # a done one with its worktree still there: a merge left half-way
        drawn = [] if archived else [d for d in drawn_all if d['requirement'] == meta['id']]
        level, ladder = clarity(sections, len(drawn), meta.get('design_skipped'))
        if state not in ('done', 'dropped') and meta['id'] not in facts:
            facts[meta['id']] = fact_index(home, meta['id'])
        passed = gate(sections)
        stage = stage_of(state, meta.get('manual_stage'), done, steps, passed['ok'], bool(meta.get('test_flow')))
        testing = testing_view(meta, sections, stage)
        tester = sessions.get(tests[-1]) if tests else None
        testing['doing'] = (tester or {}).get('doing') or ''
        testing['line'] = testing_line(testing)
        out.append({
            'id': meta['id'], 'title': meta.get('title'), 'state': state, 'stage': stage, 'branch': meta.get('branch'),
            'moves': {to: stage_blocked(stage, to, passed['ok'], waiting) for to in STAGES},
            'depends_on': depends_of(meta), 'waiting': [w['id'] for w in waiting], 'depend_needs': dependency_needs(sections),
            'start_sync': start_sync_view(meta), 'branch_removed': removed_tip(meta, sections),
            'keep_branch': bool(meta.get('keep_branch')),
            'source_issue': meta.get('source_issue'), 'converted_issue': meta.get('converted_issue'),
            'previous_state': meta.get('previous_state') or 'draft', 'previous_stage': meta.get('previous_stage'),
            'sessions': ids, 'intake': [str(x) for x in meta.get('intake') or []], 'dev_sessions': dev, 'test_sessions': tests, 'testing': testing,
            'session_choices': {
                phase: {'cli': meta[f'{phase}_cli'], 'model': meta.get(f'{phase}_model') or ''}
                for phase in SESSION_PHASES if meta.get(f'{phase}_cli') in ('claude', 'codex')
            },
            'testing_follows_development': meta.get('testing_cli') not in ('claude', 'codex'),  # 测试会话没单独设置：用开发会话的选择（没有开发会话的选择就沿用应用设置）
            'created': str(meta.get('created') or ''), 'updated': str(meta.get('updated') or ''),
            'completed_on': requirement_event_date(sections, r'用户(?:实际操作后确认完成[，。]|.*这里补记完成。)') if state == 'done' else '',
            'merged_into': str(meta.get('merged_into') or '') if state == 'done' else '',  # 代码合进了哪个分支（数据外置以后事实不随分支走，界面上注明）
            'dropped_on': requirement_event_date(sections, r'用户放弃了这个需求：') if state == 'dropped' else '',
            'file': f'{DIR}/requirements/{path.name}', 'path': str(home / DIR / 'requirements' / path.name) if not archived else None,
            'worktree': str(place) if own else None,
            'sections': {h: [re.sub(r'〔[^〕]*〕$', '', l[2:]).strip() for l in sections.get(h, []) if l != PLACEHOLDER] for h in REQ_HEADS},
            'doing': (last or {}).get('doing') or '', 'done': done, 'steps': steps,
            'level': level, 'ladder': ladder, 'gate': passed, 'designs': drawn,
            'brief': brief(sections, facts[meta['id']]) if state not in ('done', 'dropped') else None,
            'intake_assessment': assessment_view(meta) if state == 'draft' else None,
            **(after_view(home, meta, sections, tagmap) if state in ('done', 'dropped') else {}),
        })
    link_dependencies(out)
    out.sort(key=lambda r: (r['created'], r['id']), reverse=True)
    out.sort(key=lambda r: r['state'] == 'done')
    return out


def after_view(home, meta, sections, tagmap=None):
    """What a finished or cancelled requirement shows besides its sections: the summary and tags (summary.py), whether its
    sessions are kept on this machine (archive.py), and the decisions its summary proposed that still wait for the user."""
    asked = [str(x) for x in meta.get('summary_decisions') or []]
    waiting = []
    if asked:
        drafts = {m['id'] for _, m, sec in all_docs(home, ('decisions',)) if m['id'] in asked and (m.get('status') == 'draft' or any('〔待确认〕' in l for l in sec.get('决定', [])))}
        waiting = [x for x in asked if x in drafts]
    archived = [str(x) for x in meta.get('archived') or []]
    tags = [(tagmap or {}).get(str(u)) or stable_tag(home, str(u)) for u in meta.get('sessions') or []]
    return {
        'summary': live_items(sections.get('总结')), 'topics': [str(x) for x in meta.get('topics') or []], 'summarized': str(meta.get('summarized') or ''),
        'tags': [str(x) for x in meta.get('tags') or []],
        'session_tags': tags, 'archived': archived, 'archive_missing': [str(x) for x in meta.get('archive_missing') or []],
        'archive_here': archive_state(home, meta.get('id'), tags) if tags else [],
        'summary_decisions': asked, 'pending_decisions': waiting,
    }


def req_attach(root, ident, session_id, test=False, trusted=None):
    """Record that a session belongs to a requirement. A requirement may collect several sessions over time. A test session
    (test=True) is also listed in test_sessions: only the app may say so, since it decides what that session may do."""
    if test:
        if not (app_trusted() if trusted is None else trusted):
            raise ValueError('登记测试会话只由应用自己做（没有应用的标记）。')
    home = main_checkout(root)
    found = next((r for r in load_requirements(home) if r['id'] == ident), None)
    if not found or found['state'] == 'dropped' or not found['worktree']:
        return False
    path = home / found['file']
    meta, sections = read_doc(path)
    ids = [str(x) for x in meta.get('sessions') or []]
    tests = [str(x) for x in meta.get('test_sessions') or []]
    if session_id not in ids or (test and session_id not in tests):
        meta.update(sessions=ids + [session_id] if session_id not in ids else ids, updated=today())
        if test and session_id not in tests:
            meta['test_sessions'] = tests + [session_id]
        write_doc(path, meta, sections)
    ensure_here(found['worktree'])  # the session joins the list the commit hook picks from
    return stable_tag(home, session_id)


def find_requirement(root, ident=None):
    """A requirement by its number, or, without one, the requirement this checkout belongs to."""
    found = load_requirements(root)
    if not ident:
        mine = own_requirement(root)
        ident = mine[1]['id'] if mine else None
        if not ident:
            raise ValueError('没有指定需求编号，当前目录也不属于任何需求。')
    match = next((r for r in found if r['id'] == ident), None)
    if not match:
        raise ValueError(f'没有找到需求 {ident}。')
    return match


def req_start(root, ident):
    """The gate between 立项 and 开发. Only the user passes it: there must be at least one acceptance condition."""
    found = find_requirement(root, ident)
    if found['state'] == 'open':
        raise ValueError('这个需求已经立项，正在开发。')
    if found['state'] == 'done':
        raise ValueError('这个需求已经确认完成了。')
    if found['state'] != 'draft':
        raise ValueError(f'这个需求的状态是 {found["state"]}，不能立项。')
    if not found['worktree']:
        raise ValueError('这个需求没有自己的工作目录，没法立项。')
    home = main_checkout(root)
    path = home / found['file']
    meta, sections = read_doc(path)
    passed = gate(sections)
    if not passed['ok']:
        raise ValueError('还不能立项：' + passed['missing'])
    waiting = waiting_on(meta, requirement_index(home))
    if waiting:
        raise ValueError('还不能立项：' + waiting_reason(waiting) + '。')
    synced = sync_on_start(home, found)
    meta.update(state='open', intake=[str(x) for x in meta.get('sessions') or []], test_flow=1, case_flow=1, updated=today())
    add_line(sections, '变更记录', f'- {today()} 用户确认立项，开始开发。' + (f'（{len(passed["uncovered"])} 条验收条件还没有测试用例：第 {"、".join(str(x["n"]) for x in passed["uncovered"])} 条。）' if passed['uncovered'] else ''))
    if synced:  # 文件头只存得下平铺的值和列表：拆成几项，视图里再拼回来（start_sync_view）
        meta.update(start_sync='ok' if synced['ok'] else 'conflict', start_sync_base=synced['base'],
                    start_sync_commits=synced['commits'], start_sync_files=synced['files'][:20])
        add_line(sections, '变更记录', f'- {today()} 开始开发前把 {synced["base"]} 的最新内容（{synced["commits"]} 个提交）'
                 + ('同步进了这个需求的分支。' if synced['ok'] else f'同步进分支时有冲突（{"、".join(synced["files"][:5])}），已撤回，留给开发会话处理。'))
    write_doc(path, meta, sections)
    commit_facts(home, f'需求立项：{found["title"]}')  # 立项是提交事实的时刻之一：立项会话聊下来的一起提交
    return find_requirement(root, found['id'])


class NeedsSync(ValueError):
    """Before anything lands, the requirement's (or fix's) own session has to commit its work and merge the target branch
    in (resolving conflicts there), so that merging back can no longer conflict. files: what would conflict; loose: what
    is not committed yet."""
    def __init__(self, base, files=(), loose=(), what='需求', merging=False):
        self.base, self.files, self.loose = base, list(files), list(loose)
        self.merging = merging
        place = '这个需求的会话' if what == '需求' else f'{what}会话'
        if merging:
            detail = '还有冲突（' + '、'.join(self.files) + '）' if self.files else '冲突已处理，合并还没有提交'
            super().__init__(f'这个{what}的工作目录里有一笔未完成的合并：{detail}。')
            return
        why = []
        if self.loose:
            why.append(f'还有 {len(self.loose)} 个文件的改动没有提交')
        why.append(f'分支还没包含 {base} 的最新内容' + (f'，合并会冲突（{"、".join(self.files)}）' if self.files else ''))
        super().__init__(f'这个{what}' + '，'.join(why) + f'。要先在{place}里提交改动、把 {base} 合并进来并解决冲突，再合并回去。')


MergeConflict = NeedsSync  # 旧名字


class MergeBusy(ValueError):
    """Another requirement is being merged into the project directory right now."""
    def __init__(self, holder):
        self.holder = holder
        who = '另一笔合入'
        if holder:
            who = f'修复 {holder}' if holder.startswith('F-') else f'需求 {holder}'
        super().__init__(f'{who} 正在合并，等它合并完再试。')


def target_branch(home):
    """What a finished requirement merges into: whatever branch the user's project directory has checked out."""
    name = branch_of(home)
    return name if name and name != 'HEAD' else None


def merge_lock_path(home):
    return (Path(home) / git(home, 'rev-parse', '--git-common-dir').strip()).resolve() / 'relayforge-merge.lock'


@contextmanager
def take_merge_lock(home, ident):
    """Keep one lock file in the shared Git directory. The OS releases the lock when the handle closes, even on exit."""
    try:
        import fcntl
    except ImportError:
        raise ValueError('当前平台不支持合入锁，无法安全合并到项目目录。') from None
    # Never unlink the file: another process may already have opened that same inode to take the lock.
    with merge_lock_path(home).open('a+', encoding='utf-8') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            # The holder may still be writing its ID. Only flock decides whether the repository is busy.
            holder = None
            try:
                handle.seek(0)
                held = json.load(handle)
                if isinstance(held, dict) and isinstance(held.get('id'), str):
                    holder = held['id']
            except (OSError, ValueError):
                pass
            raise MergeBusy(holder) from None
        handle.truncate(0)
        json.dump({'id': ident, 'pid': os.getpid()}, handle)
        handle.flush()
        try:
            yield
        finally:
            handle.truncate(0)


def merge_conflicts(home, base, branch):
    """None when the branch merges into base cleanly, else the files git cannot merge on its own. Files under .relayforge/
    do not count: a merge never takes the branch's copy of the facts (see land_branch)."""
    tree = run_git(home, 'merge-tree', '--write-tree', '--name-only', base, branch)
    if tree.returncode != 1:
        return None
    lines = tree.stdout.splitlines()[1:]
    files = sorted(n for n in set(lines[:lines.index('')] if '' in lines else lines) if not facts_path(n))
    return files or None


def facts_path(name):
    return name.strip('"').startswith(DIR + '/')


def code_changes(place, base):
    return [n for n in git(place, 'diff', '--name-only', f'{base}...HEAD').splitlines() if not facts_path(n)]


def merge_target(home, branch, what='需求', verb='确认完成'):
    """The branch a finished piece of work merges into: whatever the project directory has checked out."""
    base = target_branch(home)
    if not base:
        raise ValueError(f'项目目录现在不在任何分支上（detached HEAD），先切到要合并进去的分支再{verb}。')
    if base == branch:
        raise ValueError(f'项目目录签出的就是这个{what}的分支 {branch}，先切回要合并进去的分支再{verb}。')
    return base


def loose_changes(place):
    """Uncommitted changes in a working directory outside .relayforge/ (the copy of the facts there does not count)."""
    return [l[3:] for l in git(place, 'status', '--porcelain').splitlines() if not facts_path(l[3:])]


def check_worktree_sync(home, place, base, branch, what='需求'):
    if not place.is_dir() or branch_of(place) != branch:
        raise ValueError(f'这个{what}的工作目录当前没有签出 {branch}，请检查工作目录后重新合并。')
    merge_head = git(place, 'rev-parse', '--git-path', 'MERGE_HEAD').strip()
    if merge_head and (place / merge_head).is_file():
        conflicts = git(place, 'diff', '--name-only', '--diff-filter=U').splitlines()
        raise NeedsSync(base, conflicts, what=what, merging=True)
    loose = loose_changes(place)
    if loose:
        raise NeedsSync(base, merge_conflicts(home, base, branch) or [], loose, what)


def merge_blockers(home, base, branch, what='需求', verb='确认完成', place=None):
    """Raise when the branch cannot be merged into base right now: it does not contain base's latest commits yet (NeedsSync,
    for the AI to merge base in on the branch), or files in the project directory that the merge would overwrite. Given the
    branch's working directory (place), a target that merges in without code conflicts is merged in there first."""
    # 合并回去之前，分支必须已经包含目标分支现在的最新提交，这样落到项目目录时不会再撞上冲突。
    # 没有代码冲突时程序自己在工作目录里把目标分支合进来（sync_target），几秒钟的事；有冲突才回到需求（或修复）
    # 自己的会话，让 AI 合并、解决冲突。
    # 例外：目标分支上新多出来的只是事实（.relayforge/ 下，程序在主目录里攒着提交的），合并只合代码、不碰事实，
    # 不用为此回到会话里合并一次。
    if run_git(home, 'merge-base', '--is-ancestor', base, branch).returncode != 0:
        moved = git(home, 'diff', '--name-only', f'{branch}...{base}').splitlines()
        if not moved or not all(facts_path(n) for n in moved):
            conflicts = merge_conflicts(home, base, branch)
            if conflicts or place is None or not sync_target(place, base, branch):
                raise NeedsSync(base, conflicts or [], what=what)
    # 项目目录（签出着 base）只有在合并要改到的代码文件上有没提交的改动时才挡住，别的改动不相干（.relayforge/ 下的
    # 事实合并前由程序先提交），git 合并时会原样留着。
    touched = {n for n in git(home, 'diff', '--name-only', f'{base}...{branch}').splitlines() if not facts_path(n)}
    busy = sorted(l[3:].split(' -> ')[-1] for l in git(home, 'status', '--porcelain').splitlines() if l[3:].split(' -> ')[-1] in touched)
    if busy:
        raise ValueError(f'项目目录（{base} 分支）里这些文件有没提交的改动，而这个{what}也改了它们，合并会覆盖：{"、".join(n.strip(chr(34)) for n in busy)}。先在项目目录里提交或收起这些改动，再{verb}。')
    # 项目目录里还没进 Git 的新文件，分支上也有同名的：git 不肯覆盖，合并会中途失败。
    loose = set(git(home, 'ls-files', '--others', '--exclude-standard').splitlines())
    clobber = sorted(n for n in touched if n in loose)
    if clobber:
        raise ValueError(f'项目目录（{base} 分支）里这些文件还没提交进 Git，而这个{what}也新增了它们，合并会覆盖：{"、".join(clobber)}。先在项目目录里提交、挪走或删掉它们，再{verb}。')


def sync_target(place, base, branch):
    """Merge base into the branch in its own working directory, when git can do it without code conflicts. Conflicts in the
    stale copy of .relayforge/ take base's side: land_branch never brings the branch's facts along anyway. Returns whether
    the branch now has base merged in; on anything else the working directory is left as it was."""
    started = run_git(place, 'merge', '--no-ff', '--no-commit', '-q', base)
    stuck = [n.strip('"') for n in git(place, 'diff', '--name-only', '--diff-filter=U').splitlines()]
    in_merge = run_git(place, 'rev-parse', '-q', '--verify', 'MERGE_HEAD').returncode == 0
    if not in_merge or any(not facts_path(n) for n in stuck) or (started.returncode != 0 and not stuck):
        if in_merge:
            run_git(place, 'merge', '--abort')
        return False
    for name in stuck:
        if run_git(place, 'cat-file', '-e', f'{base}:{name}').returncode == 0:
            run_git(place, 'checkout', base, '--', name)
        else:
            run_git(place, 'rm', '-q', '--', name)
    message = f'合并 {base} 到 {branch}：合并回去之前先同步（没有代码冲突，程序自动合并）'
    done = run_git(place, 'commit', '-q', '--no-verify', '-m', message)
    if done.returncode != 0:
        run_git(place, 'merge', '--abort')
        return False
    return True


def land_branch(home, place, branch, base, message, trailers=''):
    """Merge a branch's code into the project directory's branch, then put its working directory and the branch away.
    The branch's copy of .relayforge/ never comes along: the main checkout's facts are kept as they are. Returns the
    .relayforge/ paths the branch had changed and that were therefore left out."""
    ignored = [n.strip('"') for n in git(home, 'diff', '--name-only', f'{base}...{branch}', '--', DIR).splitlines()]
    commit_facts(home, '记录事实')  # facts not committed yet in the main checkout: committed first, the merge leaves them alone
    if not ignored:
        merged = run_git(home, 'merge', '--no-ff', '-q', '-m', message + (f'\n\n{trailers}' if trailers else ''), branch)
        if merged.returncode != 0:
            run_git(home, 'merge', '--abort')
            raise ValueError(f'合并进 {base} 时失败：' + ((merged.stderr or merged.stdout).strip().splitlines() or ['git 没有给出原因'])[-1])
    else:
        merged = run_git(home, 'merge', '--no-ff', '--no-commit', '-q', branch)
        run_git(home, 'restore', '--source=HEAD', '--staged', '--worktree', '--', DIR)  # keep the main checkout's facts
        left = git(home, 'diff', '--name-only', '--diff-filter=U').strip()
        done = run_git(home, 'commit', '-q', '--no-verify', '-m', message + f'\n\n分支上 .relayforge/ 下的改动没有合并进来，事实以项目主目录为准：{"、".join(ignored[:20])}' + (f'\n\n{trailers}' if trailers else '')) if not left else merged
        if left or done.returncode != 0:
            run_git(home, 'merge', '--abort')
            raise ValueError(f'合并进 {base} 时失败：' + ((done.stderr or done.stdout or left).strip().splitlines() or ['git 没有给出原因'])[-1])
    put_away(home, place, branch)
    return ignored


def commit_loose(place, message):
    """Commit what is left uncommitted in a working directory (outside .relayforge/) on its branch, so that putting the directory
    away loses nothing. Returns the paths it committed; raises ValueError (and undoes the staging) when git cannot commit."""
    loose = loose_changes(place)
    if loose:
        run_git(place, 'add', '-A', '--', '.', f':(exclude){DIR}')
        done = run_git(place, 'commit', '-q', '--no-verify', '-m', message)
        if done.returncode != 0 and loose_changes(place):
            run_git(place, 'reset', '-q')
            raise ValueError('没能把没提交的代码改动提交到分支上。先设置 git 的 user.name 和 user.email 再试。')
    return loose


def remove_worktree(home, place):
    """Take a working directory away, keeping its branch. Left-over edits to its stale copy of .relayforge/ and ignored files do
    not keep it. Returns git's result."""
    gone = run_git(home, 'worktree', 'remove', str(place))
    if gone.returncode != 0 and not loose_changes(place):
        gone = run_git(home, 'worktree', 'remove', '--force', str(place))
    return gone


# ---------- 取消的需求留下的分支 ----------
# 取消需求时分支保留，恢复要用它；但取消的需求越积越多，分支也跟着积累。
# 分支上没有自己的提交（比如立项阶段就取消的）：取消时一起删掉，什么都不会丢。
# 有提交的：保留若干天、最多若干个（最近取消的优先），超出的在确认完成、取消需求时顺手清理。天数和个数在应用的设置里
# （settings.json 的 droppedBranches，应用和 rf.py 读同一个文件；没设或设得不对用默认的 30 天、20 个）。
# 用户点了「保留」（keep_branch）的不清理。删掉时在需求文件里记下分支最后的提交（git 回收之前还能用它找回）；
# 恢复这样的需求，会从项目当前的分支重新开一个同名分支
DROPPED_KEEP_DAYS = 30
DROPPED_KEEP_MAX = 20


def dropped_branch_limits():
    """(days, count) a cancelled requirement's branch is kept for, from the app's settings."""
    try:
        saved = json.loads((relayforge_home() / 'settings.json').read_text(encoding='utf-8')).get('droppedBranches') or {}
    except (OSError, ValueError, AttributeError):
        saved = {}
    def pick(key, default, top):
        value = saved.get(key) if isinstance(saved, dict) else None
        return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= top else default
    return pick('keepDays', DROPPED_KEEP_DAYS, 3650), pick('keepMax', DROPPED_KEEP_MAX, 1000)


def own_commits(home, branch):
    """How many commits `branch` has that the project directory's branch does not, or None when git cannot tell."""
    out = git(home, 'rev-list', '--count', f'HEAD..refs/heads/{branch}').strip()
    return int(out) if out.isdigit() else None


def removed_tip(meta, sections):
    """The last commit of a deleted branch, as text. Older versions read 0754688 as a number and wrote back 754688;
    the 变更记录 line written at deletion still has the full value, so those records need no migration."""
    value = meta.get('branch_removed')
    tip = str(value or '')
    if isinstance(value, int):
        for line in reversed(sections.get('变更记录') or []):
            found = re.search(rf'最后的提交是 (0+{tip})(?![0-9a-f])', line)
            if found:
                return found.group(1)
    return tip


def remove_dropped_branch(home, meta, sections, why):
    """Delete a cancelled requirement's branch, noting its last commit in the 变更记录 (the caller writes the file and commits).
    Returns whether it was deleted."""
    branch = str(meta.get('branch') or '')
    tip = git(home, 'rev-parse', '--short', f'refs/heads/{branch}').strip()
    if not tip or run_git(home, 'branch', '-D', branch).returncode != 0:
        return False
    meta['branch_removed'] = tip
    add_line(sections, '变更记录', f'- {today()} {why}，分支 {branch} 已删除（最后的提交是 {tip}，git 回收之前还能用这个编号找回）。'
             '恢复这个需求时会重新开一个分支。')
    return True


def prune_dropped_branches(home, now=None):
    """Delete the branches of cancelled requirements kept beyond the days or the count in the settings (the caller commits).
    Branches the user chose to keep, checked out somewhere, or whose date cannot be told are left alone. Returns the requirement
    ids whose branch went."""
    day0 = now or dt.date.today()
    days, most = dropped_branch_limits()
    places = branch_places(home)
    rows = []
    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        meta, sections = read_doc(path)
        branch = str(meta.get('branch') or '')
        if meta.get('state') != 'dropped' or not branch or meta.get('branch_removed') or meta.get('keep_branch') or branch in places:
            continue
        if not git(home, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').strip():
            continue
        when = str(meta.get('dropped_at') or requirement_event_date(sections, '用户放弃了这个需求：') or '')
        try:
            rows.append((dt.date.fromisoformat(when[:10]), path, meta, sections))
        except ValueError:
            continue
    rows.sort(key=lambda r: r[0], reverse=True)
    removed = []
    for n, (day, path, meta, sections) in enumerate(rows):
        old = (day0 - day).days > days
        if not old and n < most:
            continue
        why = f'取消已超过 {days} 天' if old else f'取消的需求超过 {most} 个，较早取消的这个'
        if remove_dropped_branch(home, meta, sections, why):
            write_doc(path, meta, sections)
            removed.append(meta['id'])
    return removed


def req_keep(root, ident, keep=True):
    """The user keeps a cancelled requirement's branch from being cleaned up, or lets it go again (rf.py req keep; only the user:
    see trust.USER_COMMANDS)."""
    home = main_checkout(root)
    found = find_requirement(home, ident)
    if found['state'] != 'dropped':
        raise ValueError('只有已取消的需求要保留分支：别的需求的分支不会被清理。')
    path = home / found['file']
    meta, sections = read_doc(path)
    if keep and meta.get('branch_removed'):
        raise ValueError(f'分支 {found["branch"]} 已经清理掉了，没有可保留的；恢复这个需求会重新开一个分支。')
    if bool(meta.get('keep_branch')) == keep:
        raise ValueError('已经在保留这个分支了。' if keep else '这个分支本来就没有设为保留。')
    if keep:
        meta['keep_branch'] = 'yes'
    else:
        meta.pop('keep_branch', None)
    meta['updated'] = today()
    add_line(sections, '变更记录', f'- {today()} 用户' + (f'设为保留：分支 {found["branch"]} 不会被自动清理。' if keep
                                                       else f'取消了保留：分支 {found["branch"]} 和别的已取消需求一样，到期会被清理。'))
    write_doc(path, meta, sections)
    commit_facts(home, f'需求的分支{"设为保留" if keep else "取消保留"}：{found["title"]}')
    return find_requirement(home, ident)


def put_away(home, place, branch, force_branch=False):
    """Remove a finished working directory and its branch. Left-over edits to its stale copy of .relayforge/ do not keep
    the directory: they never counted."""
    remove_worktree(home, place)
    run_git(home, 'branch', '-D' if force_branch else '-d', branch)


CHOOSABLE = {'testing': '测试会话', 'development': '开发会话'}  # 建好以后能改的：测试会话、开发会话；立项会话的不放开


def req_choose(root, ident, phase, cli=None, model=None, follow=False, trusted=None):
    """The user changes which CLI and model a requirement's testing or development sessions use (the intake session's choice cannot change
    after the requirement is made). The testing session can also go back to following the development session (follow=True: the fields are
    removed, nothing is copied). A session that is already open keeps what it started with; the choice is for the next one opened.
    Only the user (trust.py)."""
    what = CHOOSABLE.get(phase, '会话')
    if not user_trusted(trusted):
        raise ValueError(f'改{what}的 CLI 和模型：这个操作只能由你在应用里做，AI 的会话不能代替你做（没有应用的标记）。')
    if phase not in CHOOSABLE:
        raise ValueError('建好以后只能改开发会话和测试会话的 CLI 和模型，立项会话的不能改。')
    if follow and (phase != 'testing' or cli or model):
        raise ValueError('「跟着开发会话」只有测试会话能选，也不用再指定 CLI 和模型。')
    if not follow:
        if cli not in ('claude', 'codex'):
            raise ValueError(f'{what}的 CLI 请选择 Claude Code 或 Codex' + ('，或者选「跟开发一样」。' if phase == 'testing' else '。'))
        if model not in (None, '') and not valid_session_model(cli, model):
            raise ValueError(f'请为{what}指定有效的模型名称。')
    found = find_requirement(root, ident)
    if found['state'] in ('done', 'dropped'):
        raise ValueError(f'这个需求已经结束，不再开{what}。')
    home = main_checkout(root)
    path = home / found['file']
    meta, sections = read_doc(path)
    names = {'claude': 'Claude Code', 'codex': 'Codex'}
    if follow:
        meta.pop('testing_cli', None)
        meta.pop('testing_model', None)
        said = '测试会话的 CLI 和模型改为跟着开发会话。'
    else:
        meta[f'{phase}_cli'] = cli
        if model:
            meta[f'{phase}_model'] = model
        else:
            meta.pop(f'{phase}_model', None)
        said = f'{what}的 CLI 和模型改为 {names[cli]}' + (f'（{model}）' if model else '（默认模型）') + '。'
    started = bool(found['test_sessions'] if phase == 'testing' else found['dev_sessions'] and found['state'] == 'open' and
                   any(x not in found['intake'] for x in found['dev_sessions']))
    follows = phase == 'development' and meta.get('testing_cli') not in ('claude', 'codex')
    meta['updated'] = today()
    add_line(sections, '变更记录', f'- {today()} 用户把{said}' + (f'已经开着的{what}不受影响，下一次新开的{what}用这个选择。' if started else '')
             + ('测试会话跟着开发会话，也用这个选择。' if follows else ''))
    write_doc(path, meta, sections)
    commit_facts(home, f'需求的{what}配置：{found["title"]}')
    return find_requirement(root, found['id'])


def settle_after_session(root, session_path, *, facts_locked=False):
    """Called right after a session file is written (extract.capture, notes.note): a development session that has done every
    step of its plan moves its requirement into 测试中, written down the first time (settle_auto_test, only once).
    The record is a help; the stage is computed the same either way, so a failure is only logged."""
    meta, sections = read_doc(session_path)
    if not meta.get('branch') or not plan_finished(sections):
        return None
    try:
        return settle_auto_test(root, branch=meta['branch'], facts_locked=facts_locked)
    except Exception as error:  # noqa: BLE001
        log(root, f'settle auto test failed: {error!r}')
        return None


def settle_auto_test(root, branch=None, ident=None, *, facts_locked=False):
    """计划做完自动进测试中，落成记录。A requirement in the test flow whose development session has done every step of its plan
    is computed to be in 测试中 (stage_of). The first time that is so, write it down: manual_stage 'test' in the file head and one
    line in 变更记录 with the time; from then on the stage is the record's. Called whenever a session file is written (note, sync).
    Does nothing once the stage was written (by this or by anyone moving it), so there is never a second line or commit.
    Returns the requirement id it wrote, or None."""
    home = main_checkout(root)
    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        meta, sections = read_doc(path)
        if not meta.get('id') or (ident and meta['id'] != ident) or (branch and meta.get('branch') != branch):
            continue
        if (meta.get('state') or 'open') != 'open' or meta.get('manual_stage') or not meta.get('test_flow'):
            return None
        found = find_requirement(home, meta['id'])
        if found['stage'] != 'test' or not found['steps'] or not found['worktree']:
            return None
        meta.update(manual_stage='test', updated=today())
        add_line(sections, '变更记录', f'- {today()} {dt.datetime.now():%H:%M} 开发会话做完了计划（{found["steps"]} 步），进入测试中。')
        write_doc(path, meta, sections)
        commit_facts(home, f'计划做完，进入测试中：{meta.get("title")}', locked=facts_locked)
        return meta['id']
    return None


def past_sessions(root, limit=40):
    """Claude Code sessions that were held in this project's directory and do not belong to a requirement, newest first."""
    home = main_checkout(root)
    folder = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects' / re.sub(r'[^0-9a-zA-Z]', '-', str(home))
    taken = {sid for r in load_requirements(home) for sid in r['sessions']} | {map_session_id(home)}  # the map editing session lives in the map
    synced = {s.get('session_id'): s for s in load_sessions(home)}
    out = []
    files = sorted((f for f in folder.glob('*.jsonl') if f.stem not in taken and not f.name.startswith('agent-')), key=lambda f: f.stat().st_mtime, reverse=True)
    for path in files:
        if len(out) >= limit:
            break
        first, cwd = '', None
        with open(path, encoding='utf-8', errors='replace') as lines:
            for n, line in enumerate(lines):
                if n > 400:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get('type') != 'user' or obj.get('isSidechain') or obj.get('isMeta'):
                    continue
                cwd = cwd or obj.get('cwd')
                content = (obj.get('message') or {}).get('content')
                text = content if isinstance(content, str) else '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text') if isinstance(content, list) else ''
                text = ' '.join(clean(text).split())
                if text and not text.startswith('<'):
                    first = text
                    break
        if not first or first.startswith('标准输入里有两部分') or (cwd and Path(cwd).resolve() != home):
            continue  # nothing was said, one of this tool's own background runs, or a different directory with the same folder name
        known = synced.get(path.stem) or {}
        out.append({'session_id': path.stem, 'title': known.get('title') or first[:40], 'first': first[:200], 'synced': bool(known),
                    'updated': f'{dt.datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M}', 'transcript': str(path)})
    return out


