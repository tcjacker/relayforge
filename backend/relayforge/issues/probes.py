"""交给 AI 排查：开排查会话、记排查结论、交回、驳回、撤回确认，以及排查会话停下来时的检查。

问题的记录本身在 issues.py。分层见 tests/test_layers.py。
"""
import datetime as dt
import json
import os
import re

from ..core.transcript import claude_shaped
from ..core.common import commit_facts, find_root, GUARD, PLACEHOLDER, REPLACED, SCRIPT
from ..core.documents import add_line
from .fixcheck import fixed_verification
from .record import (
    issue_in_state, append_entry, change, CHANGED, copy_shots, fix_issues, ISSUE_KINDS, issue_lock, issue_show, issues_home,
    KIND_WORDS, last_input, probe_of_session, read_probe, read_thread, save, statement_source, VERIFY, write_fix,
    write_probe,
)
from ..requirements.owners import find_issue, here_fix, num, read_fix
from ..requirements.record import loose_changes
from ..testsets.lifecycle import regression_cases
from pathlib import Path

__all__ = ['issue_handback', 'issue_reject', 'issue_unconfirm', 'issue_probe', 'issue_finding', 'probe_stop_block']


def issue_probe(root, ident):
    """The user hands an issue in 待处理 to the AI to investigate: a read-only session in the main checkout (the code the user is
    using), which can read, run and reproduce but changes nothing. This records that it exists; the app opens the session and
    attaches it (issue attach). Opening it again later is the same session."""
    home = issues_home(root)
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident, ('todo',), '交给 AI 排查')
        ident = meta['id']
        if not read_probe(home, ident):
            write_probe(home, {'issue': ident, 'sessions': [], 'created': f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}'})
            entry = append_entry(home, ident, {'t': 'sys', 'who': '你', 'text': '交给 AI 排查 · 只读的排查会话，读代码、复现，不改任何文件'})
            change(sections, '交给 AI 排查。', f'{ident}#{entry["n"]}')
            save(path, meta, sections)
            commit_facts(home, f'问题 {num(ident)}：交给 AI 排查')
    return issue_show(home, ident)


FINDING_HINT = '用法：issue finding #033 --kind bug|request|usage|ask --text "结论" --steps "怎么复现" --where "问题在哪" --verified "实际复现、验证过的" --inferred "只是推断、没验证的"'


def issue_finding(root, ident, kind, text, steps='', where='', verified='', inferred='', session=None, transcript=None,
                  *, observed=None, verified_fix=None):
    """The investigation session records its conclusion. It goes into the thread as an entry of its own (not the facts-only 初判:
    this one has read the code), sets the issue's kind, and the issue shows 等你决定 again with that kind's buttons. It must
    say plainly what was verified by actually reproducing and what is only inferred. Only for an issue in 待处理 that was
    handed to the AI; the user decides what happens next."""
    squash = lambda x: ' '.join(str(x or '').split())
    text, steps, where, verified, inferred = squash(text), squash(steps), squash(where), squash(verified), squash(inferred)
    kind = KIND_WORDS.get(squash(kind))
    if not kind:
        raise ValueError(f'类别写 bug、request、usage 或 ask 之一。{FINDING_HINT}')
    if not text:
        raise ValueError(f'结论是空的：--text 写一两句话说清楚。{FINDING_HINT}')
    if kind != 'ask' and not (verified and inferred):
        raise ValueError('要如实分开写：--verified 写实际复现、验证过的（没有复现过就写「没有复现」），--inferred 写只是推断、没验证的（没有就写「没有」）。')
    if kind == 'bug' and not steps:
        raise ValueError('判成是问题，要写 --steps 怎么复现（没能复现就写试过什么、卡在哪）。')
    observed = squash(observed)
    if observed and (not steps or verified_fix or kind != 'bug'):
        raise ValueError('记录实际故障需要判为「是问题」并提供复现步骤。已验证修复时请使用 --verified-fix。')
    home = issues_home(root)
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident, ('todo',), '记排查结论（只有待处理的问题能记）')
        ident = meta['id']
        if not read_probe(home, ident):
            raise ValueError(f'问题 {num(ident)} 没有交给 AI 排查，不能记排查结论。')
        thread = read_thread(home, ident)
        entry = {'t': 'finding', 'who': 'AI', 'kind': kind, 'text': text[:900], 'upto': last_input(thread)}
        for key, value in (('steps', steps), ('where', where), ('verified', verified), ('inferred', inferred)):
            if value:
                entry[key] = value[:900]
        if observed:
            entry['observed'] = observed[:900]
        if verified_fix:
            entry['fixed'] = fixed_verification(root, verified_fix, steps, verified)
        sid = session or os.environ.get('RELAYFORGE_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID')
        if sid:
            entry['session'] = sid
        entry = append_entry(home, ident, entry)
        meta.update(kind=kind, kind_by='probe')
        src = statement_source(root, home, ident, entry['n'], session, transcript)
        change(sections, f'AI 排查有了结论：{ISSUE_KINDS[kind]}。', src)
        save(path, meta, sections)
        commit_facts(home, f'问题 {num(ident)}：AI 排查的结论（{ISSUE_KINDS[kind]}）')
    return issue_show(home, ident)


def regression_check(home, fix_id, no_regression=''):
    """修好的问题要留下回归数据：这次修复在测试集里加的数据里，至少有一条在修复眼里跑过而且通过了；
    以后有人再把它弄坏，合并闸门（testsets/gate.py）会拦住。实在写不成测试集的（只能看界面、要真实外部服务……），
    用 --no-regression 写清理由，记进修复报告，由用户确认时看到。返回要写进修复报告的那一行。"""
    reason = ' '.join(str(no_regression or '').split())
    cases = regression_cases(home, fix_id)
    passed = [c for c in cases if c['status'] == 'pass']
    if passed:
        sets = sorted({c['set'] for c in passed})
        return f'回归数据：{"、".join(sets)} 里 {len(passed)} 条（这次修复加的，已经跑过并通过）。'
    if reason:
        return f'没有留回归数据：{reason}'
    if not cases:
        raise ValueError('交回之前要留下回归数据：在这个问题对应功能点的测试集里加至少一条能重现这次毛病的数据'
                         '（rf.py test add，没有合适的测试集就 rf.py test new），修好以后跑 rf.py test run 确认它通过。'
                         '实在写不成测试集（只能看界面、要真实的外部服务……），交回时加 --no-regression "为什么写不成"。')
    bad = [f'{c["set"]} #{c["n"]}（{"还没跑" if c["status"] == "unrun" else "没过" if c["status"] == "fail" else "跑不起来"}）' for c in cases]
    raise ValueError(f'这次修复加的回归数据还没有一条跑过并通过：{"、".join(bad[:6])}。先运行 rf.py test run 让它们通过，再交回。')


def issue_handback(root, ident, changed, verify, session=None, transcript=None, no_regression=''):
    """The fixing session reports back: what it changed and how it checked. The issue then waits for the user."""
    changed, verify = ' '.join(str(changed or '').split()), ' '.join(str(verify or '').split())
    if not changed or not verify:
        raise ValueError('交回要写两项：--changed "改了什么" 和 --verify "怎样验证"。')
    home = issues_home(root)
    with issue_lock(home):
        if not ident:
            mine = here_fix(root)
            if not mine:
                raise ValueError('没有指定问题编号，当前目录也不属于任何修复。')
            left = [i for i in fix_issues(mine) if find_issue(home, i)[1].get('state') == 'fixing']
            if len(left) != 1:
                raise ValueError('这个修复里有几条问题在修，写明交回哪一条：issue handback #033 …。' if left else '这个修复里没有正在修的问题。')
            ident = left[0]
        path, meta, sections, state = issue_in_state(home, ident, ('fixing',), '交回（只有修复中的问题能交回）')
        fix = read_fix(home, meta.get('fix')) if meta.get('fix') else None
        if not fix:
            raise ValueError(f'问题 {num(meta["id"])} 没有挂在任何修复上。')
        place = Path(fix['worktree'])
        if place.is_dir():
            loose = loose_changes(place)
            if loose:
                raise ValueError(f'修复的工作目录里还有 {len(loose)} 个文件的改动没有提交（{"、".join(x.strip(chr(34)) for x in loose[:5])}）。先提交到当前分支，再交回。')
        regression = regression_check(home, fix['id'], no_regression)
        entry = append_entry(home, meta['id'], {'t': 'fix', 'who': '修复会话', 'fix_id': fix['id'], 'changed': changed, 'verify': verify,
                                                'regression': regression})
        src = statement_source(root, home, meta['id'], entry['n'], session, transcript)
        sections['修复报告'] = [l if l == PLACEHOLDER or REPLACED in l else l.replace('- ', '- ' + REPLACED, 1) for l in sections.get('修复报告', []) if l != PLACEHOLDER]
        add_line(sections, '修复报告', f'- {CHANGED}{changed}〔{src}〕')
        add_line(sections, '修复报告', f'- {VERIFY}{verify}〔{src}〕')
        add_line(sections, '修复报告', f'- {regression}〔{src}〕')
        change(sections, f'修复会话交回了修复报告（{fix["id"]}），等你确认。', src)
        meta['state'] = 'review'
        save(path, meta, sections)
    return issue_show(home, meta['id'])


def issue_reject(root, ident, text, images=()):
    """The user looked and it is not fixed: back to the same fix session, with what the user said."""
    text = str(text or '').strip()
    if not text:
        raise ValueError('说一句还有什么问题。')
    home = issues_home(root)
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident, ('review',), '退回')
        fix = read_fix(home, meta.get('fix')) if meta.get('fix') else None
        if not fix or not Path(fix['worktree']).is_dir():
            raise ValueError('这条问题的修复工作目录已经不在了，没法退回。')
        fix['confirmed'] = [i for i in fix.get('confirmed') or [] if i != meta['id']]
        write_fix(home, fix)
        append_entry(home, meta['id'], {'t': 'user', 'who': '你', 'text': text, 'shots': copy_shots(home, meta['id'], images)})
        entry = append_entry(home, meta['id'], {'t': 'sys', 'who': '你', 'text': f'还有问题 · 回到修复会话 {fix["id"]}'})
        change(sections, f'你说还有问题，回到修复会话 {fix["id"]}。', f'{meta["id"]}#{entry["n"]}')
        meta['state'] = 'fixing'
        save(path, meta, sections)
        commit_facts(home, f'问题 {num(meta["id"])}：还有问题，回到修复')
    return issue_show(home, meta['id'])


def issue_unconfirm(root, ident):
    """Only the user: take back a confirmation while the fix has not been merged (the others' confirmations stay)."""
    home = issues_home(root)
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident, ('review',), '撤回确认')
        fix = read_fix(home, meta.get('fix')) if meta.get('fix') else None
        if not fix or fix.get('state') != 'fixing':
            raise ValueError(f'问题 {num(meta["id"])} 所在的修复已经合并，不能撤回确认。')
        if meta['id'] not in (fix.get('confirmed') or []):
            raise ValueError(f'问题 {num(meta["id"])} 还没有确认过。')
        fix['confirmed'] = [i for i in fix['confirmed'] if i != meta['id']]
        write_fix(home, fix)
        entry = append_entry(home, meta['id'], {'t': 'sys', 'who': '你', 'text': '你撤回了确认 · 回到等你确认'})
        change(sections, '你撤回了确认，回到等你确认。', f'{meta["id"]}#{entry["n"]}')
        save(path, meta, sections)
        commit_facts(home, f'问题 {num(meta["id"])}：撤回确认')
    return issue_show(home, meta['id'])


def turn_facts(transcript):
    """What the assistant did since the user last spoke: (tool calls, whether it ran `issue finding`, whether it used the
    ask-the-user tool, its last text)."""
    calls, found, asked, last = 0, False, False, ''
    for obj in claude_shaped(transcript):  # Claude Code 和 Codex 的记录都按 Claude Code 的格式读（core/transcript.py）
        if obj.get('isSidechain'):
            continue
        content = (obj.get('message') or {}).get('content')
        blocks = content if isinstance(content, list) else []
        if obj.get('type') == 'user' and (isinstance(content, str) or any(isinstance(b, dict) and b.get('type') == 'text' for b in blocks)):
            calls, found, asked, last = 0, False, False, ''
        elif obj.get('type') == 'assistant':
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get('type') == 'tool_use':
                    calls += 1
                    found = found or (b.get('name') == 'Bash' and bool(re.search(r'\bissue\s+finding\b', str((b.get('input') or {}).get('command') or ''))))
                    asked = asked or b.get('name') == 'AskUserQuestion'
                elif b.get('type') == 'text' and b.get('text', '').strip():
                    last = b['text']
    return calls, found, asked, last


CONCLUSION_WORDS = re.compile(r'结论|根因|根本原因|原因(是|在于)|问题出在|已经?(复现|定位|确认)|可以确定|确定是|复现了|定位到')


PROBE_WORK = 3  # this many tool calls in one turn count as an investigation that went somewhere


def probe_stop_block(data):
    """An investigation session must not end a turn after real work with a conclusion only in the chat: either record it
    (issue finding), or ask me something, or say plainly what it is stuck on. A turn that records a finding, ends with a
    question, or was only a few quick looks is left alone, since it may simply be working with the user."""
    if data.get('stop_hook_active') or os.environ.get(GUARD):
        return None
    root = find_root(data.get('cwd'))
    transcript, session = data.get('transcript_path') or '', str(data.get('session_id') or '')
    if not root or not transcript or not session:
        return None
    home = issues_home(root)
    probe = probe_of_session(home, session)
    if not probe:
        return None
    try:
        meta = find_issue(home, probe['issue'])[1]
    except ValueError:
        return None
    if (meta.get('state') or 'todo') != 'todo':
        return None
    calls, found, asked, last = turn_facts(transcript)
    said = str(data.get('last_assistant_message') or last or '')
    tail = [ln for ln in said.strip().split('\n') if ln.strip()][-1:] or ['']
    if found or asked or re.search(r'[?？]', tail[0]) or not calls:
        return None
    if calls < PROBE_WORK and not CONCLUSION_WORDS.search(said):
        return None
    ident = probe['issue']
    return (f'这一轮查了一阵（{calls} 次操作），但没有记下排查结论，也没有问我问题。结论不能只留在聊天里：已经查清是不是问题、怎么复现、问题在哪的话，运行 '
            f'python3 {SCRIPT} issue finding {ident} --kind bug|request|usage|ask --text "结论" --steps "怎么复现" --where "问题在哪" '
            '--verified "实际复现、验证过的" --inferred "只是推断、没验证的" 记下来（实际跑过的和只是推断的要分开写）。'
            '还没有结论，就说明卡在哪、还需要我什么，并直接问我一个问题。不要改任何文件，不要运行 issue fix、set、reply、confirm、reject。')
