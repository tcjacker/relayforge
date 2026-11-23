"""修复：把一条或几条问题交给开发会话修、挂会话、给会话的说明，修好后合并（land_fix），以及修复会话停下来时的检查。

问题的记录本身在 issues.py；确认修好了的整套动作在 flows.py。分层见 tests/test_layers.py。
"""
import datetime as dt
import os
import re

from ..core.common import branch_of, commit_facts, DIR, find_root, git, GUARD, SCRIPT
from .record import (
    append_entry, change, feature_list, fix_issues, ISSUE_KINDS, issue_lock, issue_show, ISSUE_STATES, issues_home, judged,
    last_finding, read_probe, read_thread, save, write_fix, write_probe,
)
from .review import REVIEW_KINDS
from ..facts.mapfacts import load_journeys
from ..facts.notes import turn_weight
from ..requirements.owners import find_issue, fix_dir, fix_of_branch, here_fix, norm_id, num, read_fix
from ..requirements.record import (
    check_worktree_sync, code_changes, land_branch, merge_blockers, merge_target, open_worktree, put_away, take_merge_lock,
    valid_session_model,
)
from ..sessions.trailers import block as trailer_block, ensure_here
from pathlib import Path

__all__ = ['issue_fix', 'issue_attach', 'issue_brief', 'land_fix', 'fix_stop_block']


def fixable_problem(home, meta):
    """Why this issue cannot be fixed right now (a sentence naming it), or None."""
    ident, state = meta['id'], meta.get('state') or 'todo'
    if state != 'todo':
        return f'问题 {num(ident)} 现在是{ISSUE_STATES.get(state, state)}，不能开始修复。'
    if meta.get('kind') not in ('bug', 'request'):
        return f'问题 {num(ident)} 还没有判成「是问题」，先等初判，或者在判断卡片上选「按问题直接修」。'
    if meta.get('deferred'):
        return f'问题 {num(ident)} 放在「以后再说」里，先拿回来。'
    if meta.get('kind_by') != 'user' and judged(read_thread(home, ident))[0]:
        return f'问题 {num(ident)} 还在分析（有新的补充还没判），等判完再修。'
    return None


def issue_fix(root, idents, *, cli=None, model=None):
    """Start fixing: a fix record, a branch and a working directory of its own (like a requirement). A fix can hold several
    issues (一起修): they share the record, the branch, the working directory and the session, but each is handed back and
    confirmed on its own; the fix is merged when every one of them is confirmed. All of them must be fixable now."""
    idents = list(dict.fromkeys(norm_id(x) for x in idents))
    if not idents:
        raise ValueError('要说明修哪一条问题：issue fix #033。')
    if cli is not None and cli not in ('claude', 'codex'):
        raise ValueError('修复会话的 CLI 请选择 Claude Code 或 Codex。')
    if model is not None and (cli is None or not valid_session_model(cli, model)):
        raise ValueError('请为修复会话指定 CLI 和有效的模型名称。')
    home = issues_home(root)
    with issue_lock(home):
        found, problems = [], []
        for ident in idents:
            path, meta, sections = find_issue(home, ident)
            why = fixable_problem(home, meta)
            if why:
                problems.append(why)
            found.append((path, meta, sections))
        if problems:
            raise ValueError(' '.join(problems) if len(idents) == 1 else '这几条不能一起修：' + ' '.join(problems))
        top = max((int(p.stem[2:]) for p in fix_dir(home).glob('F-*.json') if p.stem[2:].isdigit()), default=0)
        fix_id = f'F-{top + 1:03d}'
        branch = f'fix/{fix_id.lower()}'
        place = open_worktree(home, fix_id, branch, '修复')
        fix = {'id': fix_id, 'issues': idents, 'branch': branch, 'worktree': str(place), 'state': 'fixing', 'sessions': [],
               'confirmed': [], 'created': f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}', 'base': branch_of(home)}
        if cli is not None:
            fix['session_choice'] = {'cli': cli, 'model': model or ''}
        write_fix(home, fix)
        ensure_here(place)  # commits made in this working directory carry the fix's trailers (trailers.py)
        for path, meta, sections in found:
            others = '、'.join(num(i) for i in idents if i != meta['id'])
            how = '你选择开始修复'
            if meta.get('kind') == 'request':  # 判成可转需求，你选「按问题直接修」：类别跟着改过来
                how = '你选择按问题直接修'
                meta['kind'], meta['kind_by'] = 'bug', 'user'
            if others:
                how = ('你选择按问题直接修，' if how.endswith('直接修') else '') + f'和 {others} 一起修'
            entry = append_entry(home, meta['id'], {'t': 'sys', 'who': '你', 'text': f'{how} · 修复会话 {fix_id}'})
            meta.update(state='fixing', fix=fix_id, deferred=None)
            change(sections, f'开始修复（{fix_id}，分支 {branch}' + (f'，和 {others} 一起修' if others else '') + '）。', f'{meta["id"]}#{entry["n"]}')
            save(path, meta, sections)
        commit_facts(home, f'开始修复 {"、".join(num(i) for i in idents)}（{fix_id}）')
    return fix


def find_fix(home, ident):
    fix = read_fix(home, str(ident or '').strip().upper())
    if not fix:
        raise ValueError(f'没有找到修复 {ident}。')
    return fix


def issue_attach(root, ref, session_id):
    """The app records the Claude Code session it opened: for a fix (F-005) the fixing session, for an issue (#033) the
    investigation session. It comes back to the same one later."""
    home = issues_home(root)
    with issue_lock(home):
        if re.match(r'\s*F-', str(ref or ''), re.I):
            fix = find_fix(home, ref)
            if session_id not in fix['sessions']:
                fix['sessions'].append(session_id)
                write_fix(home, fix)
            if fix.get('worktree') and Path(fix['worktree']).is_dir():
                ensure_here(fix['worktree'])
            return fix
        ident = find_issue(home, ref)[1]['id']
        probe = read_probe(home, ident)
        if not probe:
            raise ValueError(f'问题 {num(ident)} 没有交给 AI 排查，没有排查会话可挂。')
        if session_id not in probe['sessions']:
            probe['sessions'].append(session_id)
            write_probe(home, probe)
        return probe


def finding_lines(e, label='排查结论'):
    """An investigation finding as text for a brief: what was concluded, how to reproduce, where, and what is verified."""
    out = [f'{label}（{e.get("at")}，排查会话读过代码）：{ISSUE_KINDS.get(e.get("kind"), e.get("kind"))}。{e.get("text") or ""}']
    for key, name in (('steps', '复现步骤'), ('where', '问题在'), ('verified', '实际复现、验证过的'), ('inferred', '只是推断、没验证的')):
        if e.get(key):
            out.append(f'  {name}：{e[key]}')
    return out


def brief_block(view, features, journeys):
    """One issue for a session's opening: the phenomenon, what was said (with screenshot paths), the investigation's finding,
    the linked feature's facts and journeys."""
    out = ['', f'## 问题 {view["num"]} {view["title"]}（{view["state_name"]}，来源：{view["source"]}）', '现象：']
    out += [f'- {x}' for x in view['description']] or ['- （没有写描述）']
    talk = []
    for e in view['thread']:
        if e.get('t') == 'user':
            talk.append(f'- 用户（{e["at"]}）：{e.get("text") or ""}' + ''.join(f' 截图：{s}' for s in e.get('shots') or []))
        elif e.get('t') == 'report' and e.get('shots'):
            talk.append('- 提报时附的截图：' + ' '.join(e['shots']))
        elif e.get('t') == 'reproduction':
            talk += [f'- Agent 复现补充（{e["origin"]}）：', f'  复现步骤：{e["steps"]}', f'  实际看到：{e["observed"]}']
            if e.get('shots'):
                talk.append('  截图：' + ' '.join(e['shots']))
        elif e.get('t') == 'judge':
            talk.append(f'- AI 初判（只看了产品事实，没有复现）：{ISSUE_KINDS.get(e.get("kind"))}。{e.get("reason")}')
        elif e.get('t') == 'finding':
            talk.append(f'- AI 排查的结论（{e["at"]}）：{ISSUE_KINDS.get(e.get("kind"))}，详见下面的「排查结论」')
        elif e.get('t') == 'review':
            word = REVIEW_KINDS.get(e.get('kind'), e.get('kind'))
            talk.append(f'- AI 复查（{e["at"]}，在提交 {str(e.get("commit") or "")[:12]} 上）：{word}。{e.get("text") or ""}'
                        + (f' 并入 {num(e["target"])}。' if e.get('target') else '') + f' 做了：{e.get("ran") or ""}')
            if e.get('steps'):
                talk += [f'  复现步骤：{e["steps"]}', f'  实际结果：{e.get("observed") or ""}']
        elif e.get('t') == 'review_resolution':
            talk.append('- 你按 AI 复查的结论处理了这条问题')
        elif e.get('t') == 'fix':
            talk.append(f'- 之前交回的修复报告：改了什么：{e.get("changed")}；怎样验证：{e.get("verify")}')
        elif e.get('t') == 'sys':
            talk.append(f'- {e.get("text")}')
    out += ['对话：'] + (talk or ['- 没有。'])
    found = last_finding(view['thread'])
    if found:
        out += [''] + finding_lines(found)
    feature = view['feature']
    if feature and feature in features:
        f = features[feature]
        out += ['', f'关联的功能点：{feature} {f["title"]}（模块 {f["module"]} {f["module_title"]}）：{f["one_liner"] or "没有写一句话定位"}',
                f'功能点文件：{DIR}/features/ 下以 {feature} 开头的那个文件，先读它的「规则与边界」。']
        for j in journeys:
            steps = [(i + 1, s) for i, s in enumerate(j['steps']) if feature in s['features']]
            if steps:
                out.append(f'用户旅程 {j["id"]} {j.get("title") or ""}：' + '；'.join(f'第 {n} 步 {s["what"]}' + (f' → {s["see"]}' if s['see'] else '') for n, s in steps))
    else:
        out += ['', '没有关联功能点。']
    return out


def issue_brief(root, ref=None):
    """What a session needs to know, as text. Given a fix (F-005, or from inside its working directory): the issues it fixes,
    what was said, any investigation finding, the linked features and their journeys. Given an issue (#033): the same for that one."""
    home = issues_home(root)
    features, journeys = feature_list(home), load_journeys(home)
    if ref and not re.match(r'\s*F-', str(ref), re.I):
        view = issue_show(home, ref)
        return '\n'.join([f'问题 {view["num"]}：要排查的问题。'] + brief_block(view, features, journeys))
    fix = find_fix(home, ref) if ref else here_fix(root)
    if not fix:
        raise ValueError('没有指定修复编号，当前目录也不属于任何修复。')
    out = [f'修复 {fix["id"]}：分支 {fix["branch"]}，工作目录 {fix["worktree"]}。要修的问题：']
    for ident in fix_issues(fix):
        out += brief_block(issue_show(home, ident), features, journeys)
    return '\n'.join(out)


def land_fix(home, fix, before_land=None):
    """Merge a fix into the project directory's branch, the same way a finished requirement is merged: one at a time; loose
    work or a branch that does not contain the target's latest commits goes back to the fix session first (NeedsSync), so
    merging back can no longer conflict; files the merge would overwrite are refused. `before_land(place)` runs once the
    working directory holds the merged code, right before it lands; it may raise to stop the merge."""
    place, branch = Path(fix['worktree']), fix['branch']
    base = merge_target(home, branch, '修复', '确认修好')
    check_worktree_sync(home, place, base, branch, '修复')
    with take_merge_lock(home, fix['id']):
        if not code_changes(place, base):  # no code was changed: nothing to merge, just tidy up
            put_away(home, place, branch, force_branch=True)
            return base, False, [], None
        merge_blockers(home, base, branch, '修复', '确认修好', place)
        if before_land:  # 合并闸门（flows.py）：这时工作目录里就是合并后的代码
            before_land(place)
        active = fix_issues(fix)
        titles = '、'.join(num(i) for i in active)
        ignored = land_branch(home, place, branch, base, f'合并修复：问题 {titles}（{fix["id"]}）', trailer_block('fix', fix['id'], active))
        return base, True, ignored, git(home, 'rev-parse', 'HEAD').strip() or None  # 发布时靠它认出这次修复带来了哪些提交


def fix_stop_block(data):
    """A fixing session that changed code this turn and has not handed back must not stop yet."""
    if data.get('stop_hook_active') or os.environ.get(GUARD):
        return None
    root = find_root(data.get('cwd'))
    transcript = data.get('transcript_path') or ''
    if not root or not transcript:
        return None
    branch = branch_of(root)
    if not branch.startswith('fix/'):
        return None
    home = issues_home(root)
    fix = fix_of_branch(home, branch)
    if not fix or fix.get('state') != 'fixing':
        return None
    left = []
    active = fix_issues(fix)
    for ident in active:
        try:
            if find_issue(home, ident)[1].get('state') == 'fixing':
                left.append(ident)
        except ValueError:
            continue
    if not left or not turn_weight(transcript)[0]:
        return None
    ids = '、'.join(num(i) for i in left)
    each = '，每一条各交回一次（一起修的几条要逐条交回，各写各的修复报告）' if len(active) > 1 else ''
    return (f'这一轮改了代码，但问题 {ids} 还没有交回修复报告。修好并自己验证过以后，先把改动提交到当前分支，再运行 '
            f'python3 {SCRIPT} issue handback {left[0]} --changed "改了什么" --verify "怎样验证（你自己怎么验证的，我怎么验证）"{each}。'
            '还没修好、复现不了或者需要我决定，就直接说明卡在哪、需要我做什么，不用交回。不要运行 issue confirm、unconfirm 或 reject。')
