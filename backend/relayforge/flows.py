"""流程：把几样能力串成用户的一个动作。需求的确认完成、放弃、恢复、手动改状态，问题的确认修好。

这些动作要动好几样东西：合并代码、让需求带来的事实和设计稿落定、测试集的结论、改动的个数（trace）、会话记录另存（archive）、
客观标签（summary）。它们放在这里，requirements.py 和 issues.py 只管各自的记录，不用反过来认识这些上层模块。
分层见 tests/test_layers.py。
"""
import datetime as dt
import shutil
from pathlib import Path

from .core.common import DIR, TREES, commit_facts, drafts_dir, git, link_worktree, log, main_checkout, run_git, today
from .core.documents import add_line, read_doc, write_doc
from .facts.designs import adopt_designs
from .facts.facts import write_summaries
from .facts.inflight import brought_by, promote, shelve, unshelve
from .sessions.trailers import block as trailer_block, ensure_here
from .requirements.owners import branch_places, find_issue, num, read_fix
from .requirements.record import (
    REQ_STATES, STAGES, check_worktree_sync, code_changes, find_requirement, land_branch, load_requirements, waiting_reason,
    commit_loose, merge_blockers, merge_target, open_worktree, own_commits, prune_dropped_branches, put_away, remove_dropped_branch, remove_worktree,
    removed_tip, req_start, take_merge_lock,
)
from .issues.fixes import land_fix
from .issues.record import issue_in_state, append_entry, change, fix_issues, issue_lock, issue_show, issues_home, save, write_fix
from .requirements.testrecords import testing_line, testing_view
from .requirements.testing import req_skip_test, req_to_test
from .testsets.lifecycle import summary_line, testsets_promote, testsets_shelve, testsets_summary, testsets_unshelve
from .testsets.store import all_testsets
from .testsets.gate import GateFailed, gate_line, gate_run
from .sessions.trace import counts
from .sessions.archive import archive_fix, archive_requirement
from .sessions.summary import issue_tags, requirement_tags, set_tags
from .core.trust import user_trusted

__all__ = ['req_done', 'req_drop', 'req_restore', 'req_set', 'issue_confirm']


# ---------- 需求 ----------

def req_done(root, ident, skip_gate=False):
    """The user tried it and says it is done: merge the branch's code into the project directory's branch, then make what
    the requirement brought current and record it, all in the main checkout."""
    found = next((r for r in load_requirements(root) if r['id'] == ident), None)
    if not found:
        raise ValueError(f'没有找到需求 {ident}。')
    # 已经标了完成、工作目录却还在：以前合并失败后留下的半截（当时先写了「已完成」再合并）。接着把合并做完，不再写一遍记录。
    resume = found['state'] == 'done' and bool(found['worktree'])
    if found['state'] == 'done' and not resume:
        raise ValueError('这个需求已经确认完成了。')
    if found['state'] == 'draft':
        raise ValueError('这个需求还在立项，没有开始开发。先确认立项、做完开发，再确认完成。')
    waiting = [d for d in found['depends_on'] if d['id'] in found['waiting']]
    if waiting and not resume:
        raise ValueError(waiting_reason(waiting, 'done') + '。')
    if not found['worktree']:
        raise ValueError('这个需求没有自己的工作目录，没法合并。')
    home, place, branch = main_checkout(root), Path(found['worktree']), found['branch']
    base = merge_target(home, branch)
    check_worktree_sync(home, place, base, branch)
    with take_merge_lock(home, ident):
        done = _merge(found, home, place, branch, base, resume, skip_gate)
    tidy_dropped_branches(home)
    return done


def _gate(home, owner, place, skip, what='需求'):
    """合并闸门（testsets/gate.py）：在合并后的代码上跑选出来的回归测试集；没过就挡住合并（GateFailed）。
    用户选择跳过时不跑，只记一笔。返回要写进完成记录的那一行。"""
    if skip:
        return '用户选择跳过合并前的检查，直接合并。'
    result = gate_run(home, owner, place)
    if not result['ok']:
        raise GateFailed(result, what)
    return gate_line(result)


def _merge(found, home, place, branch, base, resume=False, skip_gate=False):
    ident = found['id']
    code = code_changes(place, base)
    landed = None
    gated = None
    brings = bool(brought_by(home, ident)) or any(d['status'] == 'draft' for d in found['designs']) or any(ts.get('in_progress') == ident for ts in all_testsets(home))
    if not resume and not code and not brings:  # creating and starting the requirement is not work
        raise ValueError('这个需求的分支上还没有任何改动，没有可以确认的东西。')
    if code:
        merge_blockers(home, base, branch, place=place)
        if not resume:  # 这时工作目录里就是合并后的代码
            gated = _gate(home, ident, place, skip_gate)
        ignored = land_branch(home, place, branch, base, f'合并需求：{found["title"]}（{ident}）', trailer_block('requirement', ident))
        landed = git(home, 'rev-parse', 'HEAD').strip() or None  # 发布时靠它认出这个需求带来了哪些提交
    else:  # 只带来了事实和设计稿（比如只画了设计稿的需求）：没有代码要合并，收掉工作目录和分支
        ignored = [n for n in git(home, 'diff', '--name-only', f'{base}...{branch}', '--', DIR).splitlines()]
        put_away(home, place, branch, force_branch=True)
    if not resume:
        _finish(found, home, base, ignored, bool(code), landed, gated)
    shutil.rmtree(drafts_dir(ident), ignore_errors=True)  # the scratch copies; the designs themselves are in the main checkout
    return next(r for r in load_requirements(home) if r['id'] == ident)


def _finish(found, home, base, ignored, merged, merge_commit=None, gated=None):
    """After the code is in: what the requirement brought becomes current, its designs are adopted, and the 完成记录 says so."""
    ident = found['id']
    source = f'{ident}#完成'
    tested = summary_line(testsets_summary(home, ident))
    added, changed, new_features = promote(home, ident, source)
    testsets_promote(home, ident, source)
    drawn, retired = adopt_designs(home, ident, new_features)  # the drafts become what the features look like now
    path = home / found['file']
    meta, sections = read_doc(path)
    meta.update(state='done', updated=today())
    if base:
        meta['merged_into'] = base  # 合进了哪个分支：事实整个项目共用，界面靠它注明这个需求的代码在哪个分支上
    if merge_commit:
        meta['merge_commit'] = merge_commit
    add_line(sections, '变更记录', f'- {today()} 用户实际操作后确认完成，' + (f'合并进 {base}。' if merged else f'没有代码改动，不用合并（{base}）。'))
    if merged:  # 只记个数；完整的提交和文件列表用 rf.py trace 现算
        commits, files = counts(home, ident, merge_commit)
        add_line(sections, '变更记录', f'- {today()} 改动：{commits} 个提交、{files} 个文件（完整的列表用 rf.py trace {ident} 看）。')
    for word, items in (('新增', added), ('修改', changed)):
        if items:
            add_line(sections, '变更记录', f'- {today()} {word}：{"；".join(items)}。')
    if tested:
        add_line(sections, '变更记录', f'- {today()} {tested}')
    if gated:
        add_line(sections, '变更记录', f'- {today()} {gated}')
    tview = testing_view(meta, sections)
    rounds = testing_line(tview)
    if rounds:
        add_line(sections, '变更记录', f'- {today()} {rounds}')
    if tview.get('accept_line'):  # 逐条验收：哪几条点了通过、哪几条没点（不强制点完，没点的如实记）
        add_line(sections, '变更记录', f'- {today()} {tview["accept_line"]}')
    if drawn:
        add_line(sections, '变更记录', f'- {today()} 设计稿：' + '；'.join(
            f'{d["id"]} {d["title"]}' + (f'（成为 {"、".join(d["features"])} 现在的样子）' if d['features'] else '（没有对上功能点）') for d in drawn) + '。')
    if retired:
        add_line(sections, '变更记录', f'- {today()} 被替代的设计稿：' + '；'.join(f'{d["id"]} {d["title"]}（由 {d["by"]} 替代，移到 history/）' for d in retired) + '。')
    if ignored:
        add_line(sections, '变更记录', f'- {today()} 分支上改了 .relayforge/ 下的 {len(ignored)} 个文件（{"、".join(n.strip(chr(34)) for n in ignored[:5])}' + ('等' if len(ignored) > 5 else '') + '），事实只以项目主目录的为准，这些没有合并进来。')
    archive_requirement(home, ident, meta, sections)
    set_tags(home, meta, requirement_tags(home, meta, sections, merge_commit))
    write_doc(path, meta, sections)
    write_summaries(home)
    commit_facts(home, f'需求完成：{found["title"]}')


def req_drop(root, ident):
    """The user cancels a draft or an active requirement. Nothing is lost: code not committed yet is committed on its branch,
    which stays; what it brought into the main checkout moves to history/ (noting the requirement); the requirement file
    stays, marked cancelled; only the working directory goes."""
    found = find_requirement(root, ident)
    if found['state'] == 'done':
        raise ValueError('这个需求已经确认完成了，不能放弃。')
    if found['state'] not in ('draft', 'open'):
        raise ValueError(f'这个需求的状态是 {found["state"]}，不能放弃。')
    if not found['worktree']:
        raise ValueError('这个需求没有自己的工作目录，没法放弃。')
    home, place = main_checkout(root), Path(found['worktree'])
    if place.resolve() == home:
        raise ValueError('这个需求的工作目录就是项目目录，不能收掉。')
    try:  # code changes included: cancelling never asks the user to clean up first, and nothing is thrown away
        loose = commit_loose(place, f'放弃需求：{found["title"]}（没提交的代码改动）')
    except ValueError as error:
        raise ValueError(str(error).replace('分支上。', '这个需求的分支上，这次没有放弃。')) from None
    path = home / found['file']
    meta, sections = read_doc(path)
    meta.update(previous_state=found['state'], previous_stage=meta.get('manual_stage'), state='dropped', updated=today(), dropped_at=today())
    put = shelve(home, ident, f'{ident}#放弃')
    put += [f'测试集 {x}' for x in testsets_shelve(home, ident, f'{ident}#放弃')]
    kept = f'，连同 {len(loose)} 个没提交的代码改动' if loose else ''
    what = '聊过的内容在这个需求文件里' if found['state'] == 'draft' else '聊过的内容在这个需求文件里，已经提交的代码'
    add_line(sections, '变更记录', f'- {today()} 用户放弃了这个需求：工作目录收掉，分支 {found["branch"]} 保留，{what}{kept}在分支上。'
             + (f'它带来的 {"；".join(put)} 移到 history/，恢复时拿回来。' if put else ''))
    archive_requirement(home, ident, meta, sections)
    write_doc(path, meta, sections)
    gone = remove_worktree(home, place)
    commit = git(home, 'rev-parse', '--short', found['branch']).strip()
    if gone.returncode == 0 and own_commits(home, found['branch']) == 0:  # 分支上没有自己的提交：留着也恢复不出什么，一起删掉
        meta, sections = read_doc(path)
        if remove_dropped_branch(home, meta, sections, '分支上没有自己的提交，取消时一起删掉'):
            write_doc(path, meta, sections)
    shutil.rmtree(drafts_dir(found['id']), ignore_errors=True)  # scratch copies of designs that are in the main checkout's history now
    write_summaries(home)
    commit_facts(home, f'放弃需求：{found["title"]}')
    tidy_dropped_branches(home)
    if gone.returncode != 0:
        raise ValueError('已经标为放弃，但工作目录没能收掉：' + ((gone.stderr or gone.stdout).strip().splitlines() or ['git 没有给出原因'])[-1])
    result = find_requirement(home, found['id'])
    result['commit'] = commit
    return result


def tidy_dropped_branches(home):
    """Clean up old branches of cancelled requirements after finishing or cancelling one. Never stops what the user did."""
    try:
        removed = prune_dropped_branches(home)
        if removed:
            commit_facts(home, '清理取消的需求留下的分支：' + '、'.join(removed))
    except Exception as error:  # noqa: BLE001 - tidying up must not turn a finished or cancelled requirement into an error
        log(home, f'prune dropped branches failed: {error!r}')


def req_restore(root, ident):
    """Restore a cancelled requirement: its working directory from the preserved branch, and what it brought from history/."""
    found = find_requirement(root, ident)
    if found['state'] != 'dropped':
        raise ValueError('只有已取消的需求可以恢复。')
    home = main_checkout(root)
    branch = found['branch']
    path = home / found['file']
    meta, sections = read_doc(path)
    previous = meta.get('previous_state') or 'draft'  # Older cancellations only supported drafts.
    if previous not in ('draft', 'open'):
        raise ValueError('取消前的状态无效，未恢复。')
    place = branch_places(home).get(branch)
    fresh = ''
    if place is None:
        place = home / TREES / found['id']
        if place.exists():
            raise ValueError('恢复目录已经存在，不会覆盖。请先检查：' + str(place))
        if git(home, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').strip():
            result = run_git(home, 'worktree', 'add', '-q', str(place), branch)
            if result.returncode != 0:
                raise ValueError('没能恢复需求工作目录：' + (result.stderr or result.stdout).strip())
            link_worktree(home, place)
        else:  # 分支已经清理掉了（取消时它没有自己的提交，或者取消太久）：从项目当前的分支重新开一个同名分支
            open_worktree(home, found['id'], branch)
            fresh = f'原来的分支已经清理掉了（最后的提交是 {removed_tip(meta, sections)}），从项目当前的分支重新开了分支 {branch}。' \
                if meta.get('branch_removed') else f'原来的分支找不到了，从项目当前的分支重新开了分支 {branch}。'
            meta.pop('branch_removed', None)
    ensure_here(place)
    back = unshelve(home, ident, f'{ident}#恢复')
    back += [f'测试集 {x}' for x in testsets_unshelve(home, ident, f'{ident}#恢复')]
    meta.pop('dropped_at', None)
    meta.update(state=previous, manual_stage=meta.get('previous_stage'), updated=today())
    add_line(sections, '变更记录', f'- {today()} 用户恢复已取消的需求，回到{REQ_STATES[previous]}，' + (fresh or '保留原分支与会话。')
             + (f'拿回：{"；".join(back)}。' if back else ''))
    write_doc(path, meta, sections)
    write_summaries(home)
    commit_facts(home, f'恢复需求：{found["title"]}')
    return find_requirement(home, ident)


def req_set(root, ident, to, trusted=None):
    """The user moves a requirement to another board column by hand (right-click menu or dragging its card).
    立项中 → 进行中 is 立项 (req start), → 已完成 is req done, → 已取消 is req drop; 进行中, 测试中 and 待验收 are all state
    open, told apart by manual_stage. → 测试中 is 交给测试 (req to-test); → 待验收 from 进行中 or 测试中 skips testing and
    says so (req skip-test). Those two are checked against the app's marker (trust.py). The rules are those of stage_blocked."""
    to = {'draft': 'talk', 'open': 'build'}.get(to, to)
    found = find_requirement(root, ident)
    why = found['moves'].get(to) if to in STAGES else f'没有「{to}」这个状态。'
    if why:
        raise ValueError(f'不能把「{found["title"]}」改成{STAGES.get(to, to)}：{why}。')
    if to in ('test', 'check'):
        if not user_trusted(trusted):
            raise ValueError(f'改成{STAGES[to]}：这个操作只能由你在应用里做，AI 的会话不能代替你做（没有应用的标记）。')
    if to == 'dropped':
        return req_drop(root, found['id'])
    if found['state'] == 'draft':
        found = req_start(root, found['id'])
    if to == 'done':
        return req_done(root, found['id'])
    if found['stage'] == to:  # 刚立项，计划还空着，本来就在进行中
        return found
    if to == 'test':
        return req_to_test(root, found['id'], trusted=True)
    if to == 'check' and found['stage'] in ('build', 'test') and found['testing']['flow']:
        return req_skip_test(root, found['id'], 'skipped' if found['stage'] == 'build' else 'manual', trusted=True)
    if not found['worktree']:
        raise ValueError('这个需求没有自己的工作目录，没法改状态。')
    home = main_checkout(root)
    path = home / found['file']
    meta, sections = read_doc(path)
    meta.update(manual_stage=to, updated=today())
    add_line(sections, '变更记录', f'- {today()} 用户手动把状态从{STAGES[found["stage"]]}改为{STAGES[to]}。')
    write_doc(path, meta, sections)
    commit_facts(home, f'需求改为{STAGES[to]}：{found["title"]}')
    return find_requirement(root, found['id'])


# ---------- 问题 ----------

def issue_confirm(root, ident, skip_gate=False):
    """Only the user: it is fixed. When every issue of the fix is confirmed, the fix is merged and they are all resolved."""
    home = issues_home(root)
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident, ('review',), '确认修好了')
        fix = read_fix(home, meta.get('fix')) if meta.get('fix') else None
        if not fix:
            raise ValueError(f'问题 {num(meta["id"])} 没有挂在任何修复上。')
        again = meta['id'] in fix['confirmed']
        if not again:
            fix['confirmed'].append(meta['id'])
            write_fix(home, fix)
        waiting = [i for i in fix_issues(fix) if i not in fix['confirmed']]
        if waiting:
            if again:  # 已经确认过，同批还没齐：不重复记
                return issue_show(home, meta['id'])
            entry = append_entry(home, meta['id'], {'t': 'sys', 'who': '你', 'text': f'你确认修好了 · 等同批的 {"、".join(num(i) for i in waiting)} 确认后一起合并'})
            change(sections, '你确认修好了，等同批的问题确认后一起合并。', f'{meta["id"]}#{entry["n"]}')
            save(path, meta, sections)
            commit_facts(home, f'问题 {num(meta["id"])}：确认修好了')
            return issue_show(home, meta['id'])
        commit_facts(home, f'问题 {num(meta["id"])}：确认修好了')  # 合并前先把问题文件提交干净
    gated = []
    # NeedsSync / MergeBusy / GateFailed / ValueError go to the caller; the confirmation stays recorded
    base, merged, ignored, landed = land_fix(home, fix, lambda place: gated.append(_gate(home, fix['id'], place, skip_gate, '修复')))
    with issue_lock(home):
        fix = read_fix(home, fix['id'])
        fix.update(state='merged', merged=f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}', into=base)
        if landed:
            fix['merge_commit'] = landed
            fix['commits'], fix['files'] = counts(home, fix['id'], landed)
        archive_fix(home, fix)
        tested = testsets_summary(home, fix['id'])
        if tested['sets']:
            fix['tests'] = tested
        testsets_promote(home, fix['id'], f'{fix["id"]}#完成')
        write_fix(home, fix)
        active = fix_issues(fix)
        for one in active:
            p, m, s = find_issue(home, one)
            text = f'你确认修好了 · 已合并进 {base} · 已解决' if merged else '你确认修好了 · 修复分支上没有改动，不用合并 · 已解决'
            entry = append_entry(home, one, {'t': 'sys', 'who': '你', 'text': text, 'close': True})
            change(s, '你确认修好了，' + (f'修复 {fix["id"]} 合并进 {base}（{fix["commits"]} 个提交、{fix["files"]} 个文件，列表用 rf.py trace {fix["id"]} 看）。' if merged and 'commits' in fix else f'修复 {fix["id"]} 合并进 {base}。' if merged else f'修复 {fix["id"]} 没有改动，不用合并。')
                   + (f'修复分支上改了 .relayforge/ 下的 {len(ignored)} 个文件，事实以项目主目录为准，没有合并进来。' if ignored else '')
                   + (summary_line(tested) if tested['sets'] else '') + ''.join(gated), f'{one}#{entry["n"]}')
            m['state'] = 'resolved'
            try:
                set_tags(home, m, issue_tags(home, m, fix))
            except Exception as error:  # noqa: BLE001 - tags are a help, never a reason to fail
                log(home, f'tags {one}: {error!r}')
            save(p, m, s)
        commit_facts(home, f'问题 {"、".join(num(i) for i in active)}：已解决（{fix["id"]}）')
    shutil.rmtree(drafts_dir(fix['id']), ignore_errors=True)
    return issue_show(home, meta['id'])
