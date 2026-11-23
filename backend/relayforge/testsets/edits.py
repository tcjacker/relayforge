"""测试集的新建、加数据、改、删、配置（AI 的会话用的）：写之前查清楚这条数据属于谁、有没有重复、会话能不能改它。

测试集的存储在 testsets.py；只由用户触发的决定在 testset_decisions.py。分层见 tests/test_layers.py。
"""
import copy
import re

from ..core.common import commit_facts, DIR, main_checkout, today
from ..core.documents import live_items, read_doc
from ..requirements.owners import here_owner, requirement_path
from ..requirements.testrecords import req_session_kind
from .views import testset_show
from .store import (
    _canon, _dup_text, _find_case, _landed_case, _link_usecase, _needs_usecase, _short, _source, clean_case, ENTRY_LANGS,
    feature_table, find_duplicate, LANGS, log_line, read_set, shelf_dir, stamp, tests_dir, tests_lock, view, write_set,
)
from pathlib import Path

__all__ = ['testset_new', 'testset_add', 'testset_edit', 'testset_remove', 'testset_config', 'clean_config', 'infer_lang']


def _resolve_checks(home, owner, checks):
    """--check 写验收条件的原文，或者写序号（这个需求的第几条验收条件）。"""
    out = []
    items = []
    if owner and not str(owner).startswith('F-'):
        path = requirement_path(home, owner)
        items = live_items(read_doc(path)[1].get('验收条件')) if path else []
    for c in checks or []:
        c = str(c).strip()
        if not c:
            continue
        if c.isdigit():
            if not 0 < int(c) <= len(items):
                raise ValueError(f'--check {c}：这个需求只有 {len(items)} 条验收条件。' if owner else f'--check {c}：不在需求里，没法按序号找验收条件，写原文。')
            out.append(items[int(c) - 1])
        else:
            out.append(c)
    return list(dict.fromkeys(out))


def _check_entry(entry):
    entry = str(entry or '').strip().replace('\\', '/')
    if not entry:
        raise ValueError('要写接入代码的位置：--entry 路径（相对代码根目录，比如 tests/rf/bills.py）。')
    if entry.startswith('/') or '..' in Path(entry).parts or entry.startswith(DIR + '/'):
        raise ValueError(f'接入代码 {entry} 要在代码目录里、相对代码根目录写，不能放进 {DIR}/。')
    return entry


def infer_lang(entry):
    """The language an entry is written in, from its extension (a bare dotted name is a Java class); None if it cannot be told."""
    entry = str(entry or '').strip()
    lang = ENTRY_LANGS.get(Path(entry).suffix.lower())
    if lang:
        return lang
    if re.fullmatch(r'([A-Za-z_$][\w$]*\.)+[A-Z][\w$]*', entry):  # com.acme.RfBills：包名加大写开头的类名
        return 'java'
    return None


def testset_new(root, title, feature, entry, lang=None, checks=(), session=None, transcript=None, classpath=None, build=None):
    """A new test set, empty; in a requirement's or fix's working directory it belongs to that one (in progress)."""
    home = main_checkout(root)
    title = ' '.join(str(title or '').split())
    if not title:
        raise ValueError('测试集要有一个名称：一种行为一个测试集，比如「按月筛选账单」。')
    feature = str(feature or '').strip().upper()
    if not feature:
        raise ValueError('测试集要挂在一个功能点上：--feature RF-101。')
    owner = here_owner(root)
    features = feature_table(home)
    f = features.get(feature)
    if not f or not f['ok']:
        raise ValueError(f'功能点 {feature} 不存在（或已下线）。')
    if f['in_progress'] and f['in_progress'] != owner:
        raise ValueError(f'功能点 {feature} 是需求 {f["in_progress"]} 进行中新加的，只有那个需求能给它建测试集。')
    entry = _check_entry(entry)
    lang = str(lang or infer_lang(entry) or '').lower()
    if not lang:
        raise ValueError(f'没法从 {entry} 看出是哪种语言：用 --lang 写 {"、".join(LANGS)} 之一。')
    if lang not in LANGS:
        raise ValueError(f'语言只能是 {"、".join(LANGS)}。')
    classpath = [str(c).strip() for c in ([classpath] if isinstance(classpath, str) else classpath or []) if str(c).strip()]
    build = ' '.join(str(build or '').split())
    if (classpath or build) and lang != 'java':
        raise ValueError('classpath 和 build 只有 Java 的测试集用。')
    source = _source(root, session, transcript)
    made_in = _made_in(home, owner)
    with tests_lock(home):
        taken = [int(p.stem[3:]) for folder in (tests_dir(home), shelf_dir(home)) for p in folder.glob('TS-*.json') if p.stem[3:].isdigit()]
        ident = f'TS-{max(taken, default=0) + 1:03d}'
        ts = {'id': ident, 'type': 'testset', 'title': title, 'feature': feature,
              'requirement': owner if owner and not owner.startswith('F-') else None, 'fix': owner if owner and owner.startswith('F-') else None,
              'checks': _resolve_checks(home, owner, checks), 'status': 'current', 'lang': lang, 'entry': entry,
              **({'classpath': classpath} if classpath else {}), **({'build': build} if build else {}),
              'in_progress': owner, 'created': stamp(), 'next': 1, 'cases': [], 'changes': [], 'log': [], **({'made_in': made_in} if made_in else {})}
        log_line(ts, '新建' + (f'（{"需求" if not owner.startswith("F-") else "修复"} {owner} 进行中' + ('，测试会话写的' if made_in else '') + '）' if owner else '') + '。', source)
        write_set(home, ts)
    return testset_show(root, ident)


def clean_config(ts, payload):
    """The code-side settings of a set as the user (or an adopted AI suggestion) gives them: lang, entry, classpath (Java,
    a list or one entry per line), build (Java, one command). Fields not given keep their value. Returns the new values."""
    if not isinstance(payload, dict):
        raise ValueError('设置要是一个 JSON 对象：{"lang": …, "entry": …, "classpath": […], "build": …}')
    unknown = set(payload) - {'lang', 'entry', 'classpath', 'build'}
    if unknown:
        raise ValueError(f'不认识的设置：{"、".join(sorted(unknown))}（只能改 lang、entry、classpath、build）。')
    entry = _check_entry(payload['entry']) if 'entry' in payload else ts.get('entry')
    lang = str(payload.get('lang') or '').lower().strip() if 'lang' in payload else ts.get('lang')
    if 'lang' in payload and not lang:
        lang = infer_lang(entry) or ''
    if lang not in LANGS:
        raise ValueError(f'语言只能是 {"、".join(LANGS)}。')
    if lang != 'java' and Path(entry).suffix.lower() == '.java':
        raise ValueError(f'接入代码 {entry} 是 Java 源文件，语言要选 java。')
    raw = payload['classpath'] if 'classpath' in payload else ts.get('classpath')
    if isinstance(raw, str):
        raw = raw.splitlines()
    if raw is not None and not isinstance(raw, list):
        raise ValueError('classpath 要是一个列表，或者一行一个。')
    classpath = [str(c).strip() for c in raw or [] if str(c).strip()]
    for c in classpath:
        if '\n' in c or len(c) > 2000:
            raise ValueError(f'classpath 的一项太长或有换行：{c[:80]}')
    build = str(payload['build'] if 'build' in payload else ts.get('build') or '')
    if '\n' in build.strip():
        raise ValueError('build 只能是一条命令（一行）；要好几步时写成准备步骤（测试环境里）。')
    build = ' '.join(build.split())[:1000]
    if lang != 'java' and (classpath or build):
        raise ValueError('classpath 和 build 只有 Java 的测试集用：语言不是 java 时把它们清空。')
    return {'lang': lang, 'entry': entry, 'classpath': classpath, 'build': build}


CONFIG_WORDS = (('lang', '语言'), ('entry', '接入代码'), ('classpath', 'classpath'), ('build', 'build 命令'))


def testset_config(root, ident, payload, by='用户'):
    """Only the app, as the user (or an AI suggestion the user adopted): change a set's language, access code path, Java classpath
    and build command. Takes effect at once; the change log says what changed (old → new)."""
    home = main_checkout(root)
    with tests_lock(home):
        ts = read_set(home, ident)
        new = clean_config(ts, payload)
        said = []
        for key, word in CONFIG_WORDS:
            old = ts.get(key) or ([] if key == 'classpath' else '')
            if _canon(old) != _canon(new[key]):
                said.append(f'{word} {_short(_canon(old) if key == "classpath" else old or "（空）", 200)} → {_short(_canon(new[key]) if key == "classpath" else new[key] or "（空）", 200)}')
        if said:
            ts['lang'], ts['entry'] = new['lang'], new['entry']
            for key in ('classpath', 'build'):
                if new[key]:
                    ts[key] = new[key]
                else:
                    ts.pop(key, None)
            log_line(ts, f'{by}改了测试代码的设置：' + '；'.join(said) + '。')
            write_set(home, ts)
            commit_facts(home, f'测试集 {ts["id"]}：{by}改了测试代码的设置')
    out = testset_show(home, ts['id'])
    out['changed'] = said
    return out


def _made_in(home, owner):
    """'test' when the current session is the requirement's test session (from the session id the app started it with)."""
    return 'test' if req_session_kind(home, owner) == 'test' else None


def _test_locked(home, ts, case, owner):
    """Data the test session wrote for this requirement counts as landed for its development session (FORMAT.md「测试集」):
    changing or deleting it waits for the user, like data an earlier requirement left. The test session itself and fixes are free."""
    if not owner or str(owner).startswith('F-'):
        return False
    made = case.get('made_in') or (ts.get('made_in') if ts.get('in_progress') == owner else None)
    if made != 'test':
        return False
    return _made_in(home, owner) != 'test'


def _guard(ts, owner):
    if ts.get('in_progress') and ts['in_progress'] != owner:
        raise ValueError(f'{ts["id"]} 是{"修复" if ts["in_progress"].startswith("F-") else "需求"} {ts["in_progress"]} 进行中新建的，只有它的会话能改。')


def testset_add(root, ident, cases, session=None, transcript=None):
    """Add cases (the AI). Each needs a name; a case with exactly the input of one already there is skipped and reported.
    Added from a requirement or fix still going, a case is in progress with it (unless the whole set already is)."""
    home = main_checkout(root)
    owner = here_owner(root)
    source = _source(root, session, transcript)
    made_in = _made_in(home, owner) or ('develop' if owner and not str(owner).startswith('F-') else None)
    need_uc = _needs_usecase(home, owner, made_in)
    if not cases:
        raise ValueError('没有收到数据。用 --name/--input/--expect 加一条，或用 --file 给一个 JSON 数组。')
    with tests_lock(home):
        ts = read_set(home, ident)
        _guard(ts, owner)
        seen = view(ts, owner)
        added, skipped = [], []
        for i, raw in enumerate(cases, 1):
            case = clean_case(raw, f'第 {i} 条' if len(cases) > 1 else '这一条')
            _link_usecase(home, owner, case, need_uc, f'第 {i} 条' if len(cases) > 1 else '这一条')
            dup = find_duplicate(case, seen)
            if dup:
                skipped.append({'name': case['name'], 'why': _dup_text(*dup), 'same_as': dup[1]['n']})
                continue
            case.update(n=ts.get('next') or 1, by='ai', unseen=True, added=today())
            if made_in:
                case['made_in'] = made_in
            if source:
                case['source'] = source
            if owner and not ts.get('in_progress'):
                case['in_progress'] = owner
            ts['next'] = case['n'] + 1
            ts['cases'].append(case)
            seen.append(case)
            added.append(case)
        if added:
            log_line(ts, f'AI 加了 {len(added)} 条：' + '、'.join(f'#{c["n"]} {c["name"]}' for c in added[:8]) + ('等' if len(added) > 8 else '')
                     + (f'（{owner} 进行中）' if owner and not ts.get('in_progress') else '') + '。', source)
            write_set(home, ts)
    return {'id': ts['id'], 'added': [{'n': c['n'], 'name': c['name']} for c in added], 'skipped': skipped}


def _propose(ts, case, owner, op, new, why, source):
    """A change to landed data by the AI: recorded, waiting for the user."""
    why = ' '.join(str(why or '').split())
    if not why and not _landed_case(ts, case):
        raise ValueError(f'#{case["n"]}「{case["name"]}」是测试会话为这个需求写的数据：进入测试以后，开发会话改它或删它要用户确认，'
                         '和改已经落定的数据一样。加上 --why "为什么要改"，写清楚是需求有意改了这个行为，还是这条数据本身写错了。')
    if not why:
        raise ValueError(f'#{case["n"]}「{case["name"]}」是已经落定的数据（' + (f'需求 {ts["requirement"]} 留下的' if ts.get('requirement') else '在用的')
                         + '），改它或删它要用户确认：加上 --why "为什么要改"，写清楚是需求有意改了这个行为，还是别的原因。')
    old = {k: copy.deepcopy(case[k]) for k in ('name', 'cat', 'input', 'expect', 'raises', 'usecase') if k in case}
    for c in ts.setdefault('changes', []):
        if c['case'] == case['n'] and c.get('by') == owner and c.get('state') in ('pending', 'approved'):
            c.update(op=op, new=new, why=why, state='pending', at=stamp(), source=source)
            return c
    k = max([int(ts.get('next_change') or 1)] + [c['id'] + 1 for c in ts['changes']])  # 编号不重复使用
    ts['next_change'] = k + 1
    change = {'id': k, 'case': case['n'], 'op': op, 'old': old, 'new': new, 'why': why, 'by': owner, 'state': 'pending', 'at': stamp(), 'source': source}
    ts['changes'].append(change)
    return change


def testset_edit(root, ident, n, fields, why=None, session=None, transcript=None):
    """Change one case (the AI). Own in-progress data changes at once; landed data becomes a change waiting for the user."""
    home = main_checkout(root)
    owner = here_owner(root)
    source = _source(root, session, transcript)
    with tests_lock(home):
        ts = read_set(home, ident)
        _guard(ts, owner)
        case = _find_case(ts, n)
        if case.get('in_progress') not in (None, owner):
            raise ValueError(f'#{case["n"]} 是 {case["in_progress"]} 进行中新加的，只有它的会话能改。')
        current = next((c for c in view(ts, owner) if c['n'] == case['n']), None)
        if current is None:
            raise ValueError(f'#{case["n"]} 已经有一条删除它的改动在等用户确认。')
        merged = {k: current[k] for k in ('name', 'cat', 'input', 'expect', 'raises', 'usecase') if k in current}
        for k, v in fields.items():
            if k == 'raises':
                merged.pop('expect', None)
            if k == 'expect':
                merged.pop('raises', None)
            merged[k] = v
        new = clean_case(merged, f'#{case["n"]}')
        _link_usecase(home, owner, new, _needs_usecase(home, owner, _made_in(home, owner)), f'#{case["n"]}')
        dup = find_duplicate(new, [c for c in view(ts, owner) if c['n'] != case['n']])
        if dup:
            raise ValueError(f'改完以后{_dup_text(*dup)}，没有改。')
        if _landed_case(ts, case) or _test_locked(home, ts, case, owner):
            change = _propose(ts, case, owner, 'edit', new, why, source)
            log_line(ts, f'AI 提出改 #{case["n"]} {case["name"]}（改动 {change["id"]}，等用户确认）：{change["why"]}', source)
            write_set(home, ts)
            return {'id': ts['id'], 'case': case['n'], 'pending': True, 'change': change}
        for k in ('expect', 'raises', 'usecase'):
            case.pop(k, None)
        case.update(new, unseen=True if case.get('by') == 'ai' else case.get('unseen', False))
        log_line(ts, f'AI 改了 #{case["n"]} {case["name"]}。', source)
        write_set(home, ts)
    return {'id': ts['id'], 'case': case['n'], 'pending': False}


def testset_remove(root, ident, n, why=None, session=None, transcript=None):
    """Delete one case (the AI): own in-progress data goes at once; landed data becomes a change waiting for the user."""
    home = main_checkout(root)
    owner = here_owner(root)
    source = _source(root, session, transcript)
    with tests_lock(home):
        ts = read_set(home, ident)
        _guard(ts, owner)
        case = _find_case(ts, n)
        if case.get('in_progress') not in (None, owner):
            raise ValueError(f'#{case["n"]} 是 {case["in_progress"]} 进行中新加的，只有它的会话能删。')
        if _landed_case(ts, case) or _test_locked(home, ts, case, owner):
            change = _propose(ts, case, owner, 'delete', None, why, source)
            log_line(ts, f'AI 提出删 #{case["n"]} {case["name"]}（改动 {change["id"]}，等用户确认）：{change["why"]}', source)
            write_set(home, ts)
            return {'id': ts['id'], 'case': case['n'], 'pending': True, 'change': change}
        ts['cases'] = [c for c in ts['cases'] if c is not case]
        log_line(ts, f'AI 删了 #{case["n"]} {case["name"]}（它自己进行中加的）。', source)
        write_set(home, ts)
    return {'id': ts['id'], 'case': case['n'], 'pending': False}
