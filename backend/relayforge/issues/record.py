"""问题：使用中发现的毛病。一条问题就是一条对话：提报、AI 初判、补充、用户的决定、修复报告、用户的结论，按先后排在一条线里。

问题只存在项目主目录里（.relayforge/issues/），不跟着需求或修复的分支走：从哪个工作目录运行命令，都写到主目录的同一个地方，
所以编号是顺序的（I-033，界面上显示 #033）。和其他事实一样，改动由程序在几个时刻攒着提交到主目录当前签出的分支上
（用户对问题做的决定、会话停下来时，见 common.commit_facts），只提交 .relayforge/ 下的文件，不碰用户别的改动。截图放在 .relayforge/issues/shots/，不进 Git。

  issues/I-033-名称.md   说明文件：文件头（状态、来源、类别、功能点、修复、转成的需求……）和「现象」「修复报告」「变更记录」
  issues/I-033.jsonl     对话记录：一行一条，谁说的、说了什么、当时的判断。只由程序读写
  issues/fixes/F-005.json 一次修复：哪几条问题、分支、工作目录、会话、谁已经确认了。一次修复可以带几条问题（一起修）
  issues/probes/I-033.json 「交给 AI 排查」：这条问题的排查会话（只读，在主目录里）。排查的结论记在对话记录里，一条 t=finding
  issues/shots/I-033/    截图，不进 Git

修复复用需求的隔离和合并：自己的分支（fix/f-005）和工作目录（.worktrees/F-005），合并时同样排队加锁、有冲突交给 AI。
"""

import contextlib
import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
try:
    import fcntl
except ImportError:  # not on this platform; writes are then not serialised between processes
    fcntl = None

from ..core.common import commit_facts, git, main_checkout, slug, today
from ..requirements.owners import ISSUES, all_fixes, find_issue, fix_dir, here_fix, issue_dir, norm_id, num, read_fix
from ..requirements.record import commit_loose, remove_worktree
from ..core.documents import add_line, first_item, live_items, read_doc, write_doc
from ..sessions.record import current_transcript, here_source, stable_tag
from ..facts.facts import fact_index


__all__ = [
    'ISSUE_HEADS', 'ISSUE_STATES', 'ISSUE_KINDS', 'SOURCES', 'issues_home', 'issue_dir', 'ensure_ignored', 'norm_id',
    'find_issue', 'read_thread', 'read_fix', 'all_fixes', 'issue_list', 'issue_show', 'issue_new', 'issue_reply',
    'issue_set', 'here_fix', 'read_probe', 'last_finding',
]

ISSUE_HEADS = ['现象', '修复报告', '变更记录']
ISSUE_STATES = {'todo': '待处理', 'fixing': '处理中', 'review': '待你确认', 'resolved': '已处理', 'closed': '已取消'}
ISSUE_KINDS = {'bug': '是问题', 'request': '可转需求', 'usage': '已有用法', 'ask': '待补充'}
KIND_WORDS = dict({v: k for k, v in ISSUE_KINDS.items()}, **{k: k for k in ISSUE_KINDS})
SOURCES = ('你', 'Agent', '测试页')
SHOTS = f'{ISSUES}/shots/'
IMAGE_TYPES = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
TRIAGE_FACTS = 30000  # 功能点清单最多给模型这么多字
CHANGED, VERIFY = '改了什么：', '怎样验证：'


# ---------- 位置、锁、提交 ----------

def issues_home(root):
    """The project's main checkout: issues live there whichever checkout or worktree a command runs in."""
    return main_checkout(root)


def ensure_ignored(home):
    """Screenshots stay out of Git. `init` puts the rule in .gitignore; a project initialised before that gets it in
    .git/info/exclude, which changes no tracked file."""
    home = Path(home)
    probe = home / SHOTS / 'x.png'
    if subprocess.run(['git', '-C', str(home), 'check-ignore', '-q', str(probe)], capture_output=True).returncode == 0:
        return
    common = git(home, 'rev-parse', '--git-common-dir').strip()
    if not common:
        return
    exclude = (home / common).resolve() / 'info' / 'exclude'
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open('a', encoding='utf-8') as f:
        f.write(f'{SHOTS}\n')


@contextlib.contextmanager
def issue_lock(home):
    """One writer at a time for the issue files of a repository (the app triages two issues at once, sessions record
    issues, the user replies). The lock file sits in the shared .git so every worktree sees the same one."""
    common = git(home, 'rev-parse', '--git-common-dir').strip()
    place = (Path(home) / common).resolve() if common else issue_dir(home)
    place.mkdir(parents=True, exist_ok=True)
    handle = open(place / 'relayforge-issues.lock', 'a')
    try:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()


def now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M')


# ---------- 读写 ----------


def issue_files(home):
    for path in sorted(issue_dir(home).glob('I-*.md')):
        meta, sections = read_doc(path)
        if meta.get('type') == 'issue' and meta.get('id'):
            yield path, meta, sections


def thread_path(home, ident):
    return issue_dir(home) / f'{ident}.jsonl'


def read_thread(home, ident):
    out = []
    try:
        lines = thread_path(home, ident).read_text(encoding='utf-8').splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def append_entry(home, ident, entry):
    entries = read_thread(home, ident)
    entry = dict({'n': len(entries) + 1, 'at': now()}, **entry)
    path = thread_path(home, ident)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


def save(path, meta, sections):
    meta['updated'] = today()
    write_doc(path, meta, {h: sections.get(h, []) for h in ISSUE_HEADS})


def change(sections, text, source):
    add_line(sections, '变更记录', f'- {today()} {text}〔{source}〕')


def write_fix(home, fix):
    fix_dir(home).mkdir(parents=True, exist_ok=True)
    (fix_dir(home) / f'{fix["id"]}.json').write_text(json.dumps(fix, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fix_issues(fix):
    """Issues still part of the repair, excluding those the user finished manually."""
    finished = (fix or {}).get('finished') or []
    return [i for i in (fix or {}).get('issues') or [] if i not in finished]


def probe_dir(home):
    return issue_dir(home) / 'probes'


def read_probe(home, ident):
    """The investigation session record of an issue ({issue, sessions, created}), or None when it was never handed to the AI."""
    try:
        data = json.loads((probe_dir(home) / f'{ident}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('issue') == ident else None


def write_probe(home, probe):
    probe_dir(home).mkdir(parents=True, exist_ok=True)
    (probe_dir(home) / f'{probe["issue"]}.json').write_text(json.dumps(probe, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def probe_of_session(home, session):
    """The issue a Claude Code session is investigating, if the app attached it as a probe session."""
    for path in sorted(probe_dir(home).glob('I-*.json')):
        probe = read_probe(home, path.stem)
        if probe and session in (probe.get('sessions') or []):
            return probe
    return None


def last_finding(thread):
    return next((e for e in reversed(thread) if e.get('t') == 'finding'), None)


def copy_shots(home, ident, images):
    """Copy the given image files next to the issue (outside Git); returns the names kept in the thread."""
    names = []
    folder = issue_dir(home) / 'shots' / ident
    for raw in images or []:
        src = Path(raw).expanduser()
        if not src.is_file() or src.suffix.lower() not in IMAGE_TYPES:
            raise ValueError(f'截图「{raw}」不是能用的图片文件（png、jpg、gif、webp）。')
        folder.mkdir(parents=True, exist_ok=True)
        name = f'{dt.datetime.now():%Y%m%d-%H%M%S}-{len(list(folder.iterdir())) + 1}{src.suffix.lower()}'
        shutil.copyfile(src, folder / name)
        names.append(name)
    return names


def statement_source(root, home, ident, n, session=None, transcript=None):
    """Where a statement in the issue file comes from: the session that ran the command, or else the thread entry."""
    try:
        found = here_source(root, session, transcript) if (session or os.environ.get('RELAYFORGE_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID')) else ''
    except Exception:
        found = ''
    return found or f'{ident}#{n}'


# ---------- 看一条问题 ----------

def last_input(thread):
    """The newest thing the user said that a judgement should cover. A reply that went into the investigation session
    (probe: true) is not one: the session has it, and a facts-only judgement must not answer it."""
    return max((e['n'] for e in thread if e.get('t') in ('report', 'user') and not e.get('probe')), default=0)


def judged(thread):
    """(needs a judgement, the last attempt failed): a judgement covers the thread up to its `upto`; a report or a reply
    after that is not judged yet. A failed attempt is not retried by itself; the user presses 重试. An investigation
    finding is a judgement too (it covers everything said before it)."""
    attempts = [e for e in thread if e.get('t') in ('judge', 'fail', 'finding')]
    covered = max((int(e.get('upto') or 0) for e in attempts), default=0)
    if covered >= last_input(thread):
        return False, bool(attempts) and attempts[-1].get('t') == 'fail'
    return True, False


def report_lines(sections):
    lines = live_items(sections.get('修复报告'))
    return {'changed': [l[len(CHANGED):].strip() for l in lines if l.startswith(CHANGED)],
            'verify': [l[len(VERIFY):].strip() for l in lines if l.startswith(VERIFY)]}


def sibling_views(home, fix, ident, metas=None):
    """The other issues of the same fix (一起修): who they are, where they stand, whether each is confirmed."""
    out = []
    for other in fix_issues(fix):
        if other == ident:
            continue
        meta = (metas or {}).get(other)
        if meta is None:
            try:
                meta = find_issue(home, other)[1]
            except ValueError:
                continue
        state = meta.get('state') or 'todo'
        out.append({'id': other, 'num': num(other), 'title': meta.get('title') or '', 'state': state, 'state_name': ISSUE_STATES.get(state, state),
                    'confirmed': state == 'review' and other in (fix.get('confirmed') or [])})
    return out


def issue_view(home, path, meta, sections, fixes=None, features=None, metas=None):
    ident = meta['id']
    thread = read_thread(home, ident)
    shots = issue_dir(home) / 'shots' / ident
    for e in thread:
        e['shots'] = [str(shots / s) for s in e.get('shots') or []]
    needs, failed = judged(thread)
    state = meta.get('state') or 'todo'
    fixes = fixes if fixes is not None else all_fixes(home)
    fix = fixes.get(meta.get('fix')) if meta.get('fix') else None
    feature = meta.get('feature') or None
    title_of = (features or {}).get(feature, {}).get('title') if feature else None
    view = {
        'id': ident, 'num': num(ident), 'title': meta.get('title') or '', 'state': state, 'state_name': ISSUE_STATES.get(state, state),
        'source': meta.get('source') or '你', 'kind': meta.get('kind') or None, 'kind_by': meta.get('kind_by') or None,
        'feature': feature, 'feature_title': title_of or '', 'deferred': bool(meta.get('deferred')),
        'fix': fix, 'requirement': meta.get('requirement') or None, 'duplicate_of': meta.get('duplicate_of') or None,
        'closed_as': meta.get('closed_as') or None, 'source_requirement': meta.get('source_requirement') or None,
        'created': str(meta.get('created') or ''), 'updated': str(meta.get('updated') or ''),
        'file': f'{ISSUES}/{path.name}', 'description': live_items(sections.get('现象')), 'report': report_lines(sections),
        'thread': thread, 'needs_triage': state == 'todo' and needs, 'triage_failed': state == 'todo' and failed,
        'confirmed': bool(fix and ident in (fix.get('confirmed') or [])),
        'probe': read_probe(home, ident),
    }
    # 一起修：同一次修复里的别的问题；「已确认，等同批的……」只在修复还没合并、同批还有没确认的时候成立
    sibs = sibling_views(home, fix, ident, metas) if ident in fix_issues(fix) else []
    view['batch'] = 1 + len(sibs)
    view['siblings'] = sibs
    view['waiting_for'] = [x['id'] for x in sibs if not x['confirmed']] if state == 'review' and view['confirmed'] and fix.get('state') == 'fixing' else []
    view['confirmed_waiting'] = bool(view['waiting_for'])
    return view


def feature_list(home):
    facts = fact_index(home)
    modules = {k: (m.get('title') or '') for k, (m, _) in facts['modules'].items()}
    out = {}
    for ident, (meta, sections) in sorted(facts['features'].items(), key=lambda kv: (str(kv[1][0].get('module') or ''), kv[1][0].get('order') or 0, kv[0])):
        if meta.get('status') not in ('current', 'draft'):
            continue
        out[ident] = {'id': ident, 'title': meta.get('title') or '', 'module': meta.get('module') or '', 'module_title': modules.get(meta.get('module'), ''),
                      'one_liner': first_item(sections, '一句话定位'), 'status': meta.get('status')}
    return out


def issue_list(root):
    home = issues_home(root)
    fixes, features = all_fixes(home), feature_list(home)
    files = list(issue_files(home))
    metas = {m['id']: m for _, m, _ in files}
    issues = [issue_view(home, p, m, s, fixes, features, metas) for p, m, s in files]
    issues.sort(key=lambda i: i['id'], reverse=True)
    return {'issues': issues, 'features': list(features.values()), 'root': str(home)}


def issue_show(root, ident):
    home = issues_home(root)
    path, meta, sections = find_issue(home, ident)
    return issue_view(home, path, meta, sections, None, feature_list(home))


# ---------- 新建、回复、改设置 ----------

def default_source():
    return 'Agent' if os.environ.get('RELAYFORGE_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID') else '你'


def issue_new(root, title, desc='', source=None, images=(), feature=None, ops=None, session=None, transcript=None, statements=None, extra=None, note=None,
              *, duplicate_of=None, possible_duplicate=None, duplicate_reason=None, steps=None, observed=None):
    """Record one issue. Anyone may: the user in the app, or any session with --source Agent.
    statements: 现象 lines to keep exactly as they are (with their own marks and sources), e.g. from a requirement;
    extra: more header fields (source_requirement); note: the 变更记录 line instead of the default."""
    title = ' '.join(str(title or '').split())
    if not title:
        raise ValueError('问题要有一句话的标题：哪里出了问题、看到了什么。')
    source = source or default_source()
    if source not in SOURCES:
        raise ValueError(f'来源只能是 {"、".join(SOURCES)} 之一。')
    steps, observed, duplicate_reason = (str(value or '').strip() for value in (steps, observed, duplicate_reason))
    if source != 'Agent' and any((duplicate_of, possible_duplicate, duplicate_reason, steps, observed)):
        raise ValueError('查重补充和候选只用于 Agent 提报。')
    if duplicate_of and possible_duplicate:
        raise ValueError('确认重复和可能重复只能选一种。')
    if duplicate_of and not (steps and observed):
        raise ValueError('确认重复时请提供 --steps 复现步骤和 --observed 实际结果。')
    if not duplicate_of and (steps or observed):
        raise ValueError('--steps 和 --observed 用于确认重复，普通提报请用 --desc。')
    if bool(possible_duplicate) != bool(duplicate_reason):
        raise ValueError('可能重复时请同时提供 --possible-duplicate 候选编号和 --duplicate-reason 提示原因。')
    home = issues_home(root)
    if duplicate_of:
        session = session or os.environ.get('RELAYFORGE_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID') or os.environ.get('CODEX_THREAD_ID')
        if not session:
            raise ValueError('没有识别到来源会话，请用 --session 提供会话 ID。')
        if not transcript:
            found, sid = current_transcript(root, session)
            transcript = found if sid == session else None
        origin = here_source(root, session, transcript) if transcript else f'{stable_tag(root, session)}#0'
    known = feature_list(home)
    if feature and feature not in known:
        raise ValueError(f'{feature} 在事实里不存在。')
    issue_dir(home).mkdir(parents=True, exist_ok=True)
    ensure_ignored(home)
    with issue_lock(home):
        if duplicate_of:
            path, meta, sections = find_issue(home, duplicate_of)
            ident = meta['id']
            shots = copy_shots(home, ident, images)
            append_entry(home, ident, {'t': 'reproduction', 'who': 'Agent', 'steps': steps, 'observed': observed, 'origin': origin, 'shots': shots})
            change(sections, 'Agent 补充了复现证据。', origin)
            save(path, meta, sections)
            return issue_show(home, ident)
        if possible_duplicate:
            _, candidate, _ = find_issue(home, possible_duplicate)
        top = max((int(m.group(1)) for p in issue_dir(home).glob('I-*') for m in [re.match(r'I-(\d+)', p.name)] if m), default=0)
        ident = f'I-{top + 1:03d}'
        shots = copy_shots(home, ident, images)
        lines = [' '.join(l.split()) for l in str(desc or '').splitlines() if l.strip()]
        entry = {'t': 'report', 'who': source, 'text': '\n'.join(lines) or title, 'shots': shots}
        if extra and extra.get('source_requirement'):
            entry['requirement'] = extra['source_requirement']
        if ops:
            entry['ops'] = [str(o) for o in ops]  # 测试页记下的操作步骤：先只留数据，界面不显示
        entry = append_entry(home, ident, entry)
        if possible_duplicate:
            append_entry(home, ident, {'t': 'possible_duplicate', 'who': 'Agent', 'candidate': candidate['id'], 'reason': duplicate_reason})
        src = statement_source(root, home, ident, entry['n'], session, transcript) if source == 'Agent' else f'{ident}#{entry["n"]}'
        meta = {'id': ident, 'type': 'issue', 'title': title, 'status': 'current', 'state': 'todo', 'source': source, 'kind': None, 'kind_by': None,
                'feature': feature or None, 'deferred': None, 'fix': None, 'requirement': None, 'duplicate_of': None, 'closed_as': None,
                'created': f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}', 'updated': today()}
        meta.update(extra or {})
        sections = {h: [] for h in ISSUE_HEADS}
        if statements:
            for line in statements:
                add_line(sections, '现象', line)
        else:
            for line in lines or [title]:
                add_line(sections, '现象', f'- {line}〔{src}〕')
        change(sections, note or f'记下，来源：{source}。', src)
        path = issue_dir(home) / f'{ident}-{slug(title)[:30]}.md'
        save(path, meta, sections)
    return issue_show(home, ident)


def issue_in_state(home, ident, allowed=None, what='这样做'):
    path, meta, sections = find_issue(home, ident)
    state = meta.get('state') or 'todo'
    if allowed and state not in allowed:
        raise ValueError(f'问题 {num(meta["id"])} 现在是{ISSUE_STATES.get(state, state)}，不能{what}。')
    return path, meta, sections, state


def issue_reply(root, ident, text, images=(), to_probe=False):
    """The user says something in the thread. In 待处理 it is answered by a fresh judgement (the app triages again), unless
    it went into the open investigation session (to_probe): then the session has it and no facts-only judgement is made."""
    text = str(text or '').strip()
    if not text and not images:
        raise ValueError('回复是空的。')
    home = issues_home(root)
    ensure_ignored(home)
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident, ('todo', 'fixing', 'review'), '回复（先重新打开）')
        if to_probe and not (state == 'todo' and read_probe(home, meta['id'])):
            raise ValueError(f'问题 {num(meta["id"])} 没有开着的排查会话，这句话不能交给它。')
        shots = copy_shots(home, meta['id'], images)
        append_entry(home, meta['id'], dict({'t': 'user', 'who': '你', 'text': text, 'shots': shots}, **({'probe': True} if to_probe else {})))
        if state == 'todo' and meta.get('deferred'):
            meta['deferred'] = None  # 回复就是拿回来
        save(path, meta, sections)
    return issue_show(home, meta['id'])


def issue_set(root, ident, action, value=None):
    """What the user decides about an issue: link a feature, defer or take back, dismiss (a reason, or #18 for a
    duplicate), manually finish a repair, reopen, correct the kind, say an existing usage solved it."""
    home = issues_home(root)
    remark = str(value or '').strip()
    value = ' '.join(remark.split())
    with issue_lock(home):
        path, meta, sections = find_issue(home, ident)
        ident, state = meta['id'], meta.get('state') or 'todo'
        src = f'{ident}#{len(read_thread(home, ident)) + 1}'
        close = False
        if action == 'feature':
            if value in ('', 'none', '无'):
                if not meta.get('feature'):
                    raise ValueError('这条问题本来就没有关联功能点。')
                said = f'去掉关联的功能点 {meta["feature"]}'
                meta['feature'] = None
            else:
                known = feature_list(home)
                if value not in known:
                    raise ValueError(f'{value} 在事实里不存在。')
                meta['feature'] = value
                said = f'关联功能点 · {value} {known[value]["title"]}'
        elif action == 'defer':
            _need(meta, state, ('todo',), '以后再说')
            if meta.get('deferred'):
                raise ValueError('已经是「以后再说」了。')
            meta['deferred'] = 'yes'
            said = '以后再说'
        elif action == 'undefer':
            if not meta.get('deferred'):
                raise ValueError('这条问题没有放到「以后再说」。')
            meta['deferred'] = None
            said = '拿回来处理'
        elif action == 'dismiss':
            _need(meta, state, ('todo',), '不处理')
            if not value:
                raise ValueError('不处理要写一句原因，或者重复的问题编号（如 #18）。')
            dup = re.fullmatch(r'(?:重复\s*[：:]?\s*)?#\s*(\d+)\s*', value)
            meta.update(state='closed', deferred=None)
            if dup:
                other = find_issue(home, dup.group(1))[1]['id']
                if other == ident:
                    raise ValueError('不能标成和自己重复。')
                meta.update(closed_as='duplicate', duplicate_of=other)
                said = f'标记为重复 · {num(other)}'
            else:
                meta['closed_as'] = 'drop'
                said = f'不处理 · {value}'
            close = True
        elif action in ('finish-resolved', 'finish-closed'):
            _need(meta, state, ('fixing',), '结束处理')
            if len(remark) > 200:
                raise ValueError('备注最多写 200 字。')
            destination = 'resolved' if action == 'finish-resolved' else 'closed'
            said = f'你将问题转入「{ISSUE_STATES[destination]}」' + (f' · {remark}' if remark else '')
            meta.update(state=destination, closed_as='manual' if destination == 'closed' else None,
                        duplicate_of=None, deferred=None)
            fix = read_fix(home, meta.get('fix')) if meta.get('fix') else None
            if fix and ident in fix_issues(fix):
                fix.setdefault('finished', []).append(ident)
                if not fix_issues(fix):
                    # 修复跟着结束：现场留在分支上供追溯（没提交的改动先提交上去），工作目录收掉，免得一直留在 .worktrees/ 下
                    place = Path(fix.get('worktree') or '')
                    if fix.get('worktree') and place.exists():
                        kept = commit_loose(place, f'结束修复 {fix["id"]}（没提交的代码改动）')
                        gone = remove_worktree(home, place)
                        said += (f'；修复 {fix["id"]} 结束，' + (f'{len(kept)} 个没提交的改动提交到了分支 {fix["branch"]}，' if kept else '')
                                 + ('工作目录收掉了，分支保留' if gone.returncode == 0 else '工作目录没能收掉，分支保留'))
                    fix['state'] = 'ended'
                write_fix(home, fix)
            close = True
        elif action == 'reopen':
            _need(meta, state, ('resolved', 'closed'), '重新打开')
            if meta.get('closed_as') == 'requirement':
                raise ValueError(f'这条问题已经转成需求 {meta.get("requirement")}，在需求里跟进。')
            meta.update(state='todo', closed_as=None, duplicate_of=None, fix=None, deferred=None)
            said = '重新打开'
        elif action == 'kind':
            kind = KIND_WORDS.get(value)
            if kind not in ('bug', 'request'):
                raise ValueError('类别只能手动改成「是问题」（bug）或「可转需求」（request）。')
            _need(meta, state, ('todo',), '改类别')
            meta.update(kind=kind, kind_by='user')
            said = f'你改了类别：{ISSUE_KINDS[kind]}'
        elif action == 'solved':
            _need(meta, state, ('todo',), '标为已解决')
            if meta.get('kind') != 'usage':
                raise ValueError('只有判为「已有用法」的问题能直接标为解决了；要改代码的请开始修复。')
            meta.update(state='resolved', deferred=None)
            said = '你确认已有用法解决了问题 · 已解决'
            close = True
        else:
            raise ValueError('改设置的写法：feature RF-101|none、defer、undefer、dismiss "原因或 #18"、reopen、kind bug|request、solved、finish-resolved、finish-closed。')
        append_entry(home, ident, dict({'t': 'sys', 'who': '你', 'text': said}, **({'close': True} if close else {})))
        summary = ' '.join(said.split())
        change(sections, summary + '。', src)
        save(path, meta, sections)
        commit_facts(home, f'问题 {num(ident)}：{summary}')
    return issue_show(home, ident)


def _need(meta, state, allowed, what):
    if state not in allowed:
        if state in ('fixing', 'review') and meta.get('fix'):  # 在没完成的修复里：先完成或退回，不在这里改状态
            raise ValueError(f'问题 {num(meta["id"])} 在修复 {meta["fix"]} 里（{ISSUE_STATES.get(state, state)}），不能{what}：先等这次修复完成，或者说「还有问题」把它退回修复会话。')
        raise ValueError(f'问题 {num(meta["id"])} 现在是{ISSUE_STATES.get(state, state)}，不能{what}。')
