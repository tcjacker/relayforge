"""历史查询（只读，程序算，不调用模型）：以前怎么做过、为什么这么定。

  history search 词… [--tag 标签] [--feature RF-101] [--file 路径] [--kind req|issue|decision|session|test] [--limit N]
      在需求（含已完成、已取消）、问题、裁定（含 history/ 里被替代的）、会话边做边记、测试集、总结和标签里查，每条给编号、标题、状态、日期和命中的几行。
  history show R-… | I-… | D-… | S-… | TS-…
      一条的完整摘要：需求说明、验收条件、总结、标签、相关裁定、提交概况、有哪些会话（本机有没有存档）。
  history session 会话编号 [--grep 词] [--tail N]
      把这台电脑上存的会话记录（archive.py；没有存档就读还在的原始记录）渲染成可读的文字：谁说了什么，工具的输出不放进来，有长度上限。
  history session 会话编号 --brief
      续接用的简要版：最后 3 轮，每轮最后 8 步，4000 字以内；没有记录时什么也不输出。应用新开一个接替它的会话时放进开场白。
  history session 会话编号 --steps [--grep 词] [--tail N]
      同一份记录的步骤视图：按轮次（用户说一次话是一轮）列出读了什么、跑了什么命令、改了哪些文件、哪一步失败或被拒绝、
      产生了哪些提交、用时和 token。命令和文件照录（看着像密钥的值遮掉），工具的输出仍然不放进来，只看成功还是失败。

直接扫文件，不建索引。进行中的事实照规矩标「进行中」（需求还没确认完成、文件头 in_progress、行首〔进行中 R-…〕），不能当成依据。
"""

import json
import re

from ..core.common import DIR, PENDING, REJECTED, main_checkout
from ..core.documents import live_items, read_doc
from .record import read_session, stable_tag
from ..core.transcript import clean, open_text
from ..envs.values import all_secret_values
from ..issues.record import read_probe
from ..requirements.owners import read_fix
from .summary import _citing_decisions
from ..requirements.testrecords import testing_line, testing_view
from ..testsets.store import all_testsets, feature_table, read_stats, stale_reason
from .steps import read_turns, resolve_session, session_where, step_line, tag_map, turn_when
from .trace import trace_ref

__all__ = ['history_search', 'history_show', 'history_session', 'search_text', 'show_text', 'session_text', 'steps_text', 'recap_text', 'HISTORY_KINDS']

HISTORY_KINDS = ('req', 'issue', 'decision', 'session', 'test')
LINE_CAP = 200          # 命中的一行最多多少字
HITS = 3                # 每条最多列几行命中
MESSAGE_CAP = 2000      # history session 里一条消息最多多少字
OUT_CAP = 40000         # history session 一次最多输出多少字
REQ_WORDS = {'draft': '立项中', 'open': '进行中', 'done': '已完成', 'dropped': '已取消'}
ISSUE_WORDS = {'todo': '待处理', 'fixing': '处理中', 'review': '待你确认', 'resolved': '已解决', 'closed': '不处理'}
GOING = '进行中，尚未完成，不作为依据'
SOURCE = re.compile(r'〔[^〕]*#[^〕]*〕$')


def _short(text, cap=LINE_CAP):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= cap else text[:cap - 1] + '…'


def _text(line):
    """A statement without its trailing source; leading marks (〔待确认〕、〔进行中 R-…〕、〔已替代〕) stay, they matter here."""
    line = line[2:] if line.startswith('- ') else line
    return SOURCE.sub('', line).strip()


def _lines(sections, skip=()):
    return [(h, _text(l)) for h, body in sections.items() if h not in skip for l in body if l.strip() and l != '- 尚未讨论。']


def _date(meta):
    return str(meta.get('updated') or meta.get('created') or '')[:10]


# ---------- 读出所有可查的东西 ----------

def _requirements(home):
    for path in sorted((home / DIR / 'requirements').glob('*.md')):
        meta, sections = read_doc(path)
        if not meta.get('id'):
            continue
        state = meta.get('state') or 'open'
        going = state in ('draft', 'open')
        yield {'kind': 'req', 'id': meta['id'], 'title': str(meta.get('title') or ''), 'state': REQ_WORDS.get(state, state) + (f'（{GOING}）' if going else ''),
               'date': _date(meta), 'lines': _lines(sections), 'tags': [str(x) for x in meta.get('tags') or []],
               'topics': [str(x) for x in meta.get('topics') or []], 'files': [str(x) for x in meta.get('files') or []],
               'file': f'{DIR}/requirements/{path.name}', 'meta': meta, 'sections': sections}


def _issues(home):
    for path in sorted((home / DIR / 'issues').glob('I-*.md')):
        meta, sections = read_doc(path)
        if meta.get('type') != 'issue' or not meta.get('id'):
            continue
        lines = _lines(sections)
        try:
            for raw in (home / DIR / 'issues' / f'{meta["id"]}.jsonl').read_text(encoding='utf-8').splitlines():
                entry = json.loads(raw)
                if isinstance(entry, dict) and entry.get('text'):
                    lines.append(('对话', _short(entry['text'], 600)))
        except (OSError, ValueError):
            pass
        ids = [str(meta[k]) for k in ('feature', 'requirement', 'source_requirement', 'fix') if meta.get(k)]
        yield {'kind': 'issue', 'id': meta['id'], 'title': str(meta.get('title') or ''), 'state': ISSUE_WORDS.get(meta.get('state'), meta.get('state') or ''),
               'date': _date(meta), 'lines': lines, 'tags': list(dict.fromkeys(ids + [str(x) for x in meta.get('tags') or []])), 'topics': [],
               'files': [str(x) for x in meta.get('files') or []], 'file': f'{DIR}/issues/{path.name}', 'meta': meta, 'sections': sections}


def _decision_state(meta, sections):
    if meta.get('in_progress'):
        return f'{meta["in_progress"]} {GOING}'
    status = meta.get('status')
    if status == 'superseded':
        rejected = any(REJECTED in l for l in sections.get('决定', []))
        return '已否决' if rejected else '已被替代' + (f'（由 {meta["superseded_by"]}）' if meta.get('superseded_by') else '')
    if status == 'draft' or any(PENDING in l for l in sections.get('决定', [])):
        return '待确认（AI 推断，用户还没确认）'
    return '现行'


def _decisions(home):
    for folder in ('decisions', 'history'):
        for path in sorted((home / DIR / folder).glob('D-*.md')):
            meta, sections = read_doc(path)
            if not meta.get('id'):
                continue
            yield {'kind': 'decision', 'id': meta['id'], 'title': str(meta.get('title') or ''), 'state': _decision_state(meta, sections),
                   'date': str(meta.get('decided_at') or meta.get('updated') or '')[:10], 'lines': _lines(sections, ('变更记录',)),
                   'tags': [str(x) for x in meta.get('applies_to') or []], 'topics': [], 'files': [], 'file': f'{DIR}/{folder}/{path.name}',
                   'meta': meta, 'sections': sections}


def _sessions(home):
    for path in sorted((home / DIR / 'sessions').glob('*.md')):
        meta, sections = read_doc(path)
        if not meta.get('id'):
            continue
        yield {'kind': 'session', 'id': meta['id'], 'title': str(meta.get('title') or ''), 'state': '已结束' if meta.get('ended') else '',
               'date': str(meta.get('updated') or '')[:10], 'lines': _lines(sections),
               'tags': [str(x) for x in (meta.get('features') or []) + (meta.get('modules') or [])], 'topics': [], 'files': [],
               'file': f'{DIR}/sessions/{path.name}', 'meta': meta, 'sections': sections}


def _testsets(home):
    features = feature_table(home)
    for ts in all_testsets(home, shelved=True):
        why = '' if ts.get('_shelved') else stale_reason(ts, features)
        state = (f'随 {ts["dropped_with"]} 放弃' if ts.get('_shelved') else f'过期：{why}' if why else '在用') + (f'（{ts["in_progress"]} {GOING}）' if ts.get('in_progress') else '')
        lines = [('数据', f'#{c["n"]} [{c.get("cat")}] {c.get("name")}' + (f'（{c["in_progress"]} {GOING}）' if c.get('in_progress') else ''))
                 for c in ts.get('cases') or [] if not c.get('dropped_with')]
        lines += [('验收条件', c) for c in ts.get('checks') or []] + [('接入代码', str(ts.get('entry') or ''))]
        analysis = (read_stats(home, ts['id']).get('analysis') or {})
        lines += [('分析（AI 写的）', a.get('text') or '') for a in analysis.values() if isinstance(a, dict)]
        lines += [('变更记录', x) for x in ts.get('log') or []]
        f = features.get(str(ts.get('feature') or ''), {})
        tags = [str(x) for x in (ts.get('feature'), ts.get('requirement'), ts.get('fix'), ts.get('in_progress')) if x]
        yield {'kind': 'test', 'id': ts['id'], 'title': str(ts.get('title') or ''), 'state': state, 'date': str(ts.get('updated') or '')[:10],
               'lines': lines, 'tags': list(dict.fromkeys(tags)), 'topics': [f['title']] if f.get('title') else [], 'files': [str(ts.get('entry') or '')],
               'file': f'{DIR}/{"history/tests" if ts.get("_shelved") else "tests"}/{ts["id"]}.json', 'meta': ts, 'sections': {}}


SOURCES = {'req': _requirements, 'issue': _issues, 'decision': _decisions, 'session': _sessions, 'test': _testsets}


# ---------- 查 ----------

def _haystack(item):
    return '\n'.join([item['id'], item['title'], ' '.join(item['tags']), ' '.join(item['topics']), ' '.join(item['files'])]
                     + [f'{h}：{t}' for h, t in item['lines']]).casefold()


def _work_files(home, item):
    """The files a requirement or an issue's fix changed, worked out from git (they are not written into the files)."""
    if 'work_files' not in item:
        ref = item['id'] if item['kind'] == 'req' else (item['meta'].get('fix') if item['kind'] == 'issue' else None)
        found = []
        if ref:
            try:
                found = [f['path'] for f in trace_ref(home, ref)['files']]
            except Exception:  # noqa: BLE001 - nothing to trace
                found = []
        item['work_files'] = found
        item['files'] = list(dict.fromkeys(item['files'] + found))
    return item['work_files']


def _wanted(item, words, tags, features, files):
    hay = _haystack(item)
    if any(w.casefold() not in hay for w in words):
        return False
    labels = [x.casefold() for x in item['tags'] + item['topics']]
    for t in tags:
        t = t.casefold()
        if not any(t == x or (len(t) >= 2 and t in x) for x in labels):
            return False
    for f in features:
        if not re.search(rf'(?<![\w-]){re.escape(f)}(?![\w-])', hay, re.I):
            return False
    for f in files:
        f = f.casefold().lstrip('./')
        if not any(f in x.casefold() for x in item['files']) and f not in hay:
            return False
    return True


def _hits(item, needles):
    needles = [n.casefold() for n in needles if n]
    out = []
    for head, text in item['lines']:
        if needles and any(n in text.casefold() for n in needles):
            out.append(f'{head}：{_short(text)}')
        if len(out) >= HITS:
            break
    if not out and needles:
        label = [x for x in item['tags'] + item['topics'] + item['files'] if any(n in x.casefold() for n in needles)]
        if label:
            out.append('标签：' + '、'.join(label[:8]))
    return out


def history_search(root, words=(), tags=(), features=(), files=(), kinds=(), limit=20):
    home = main_checkout(root)
    words = [w for w in (x.strip() for x in words) if w]
    if not (words or tags or features or files):
        raise ValueError('用法：history search 词… [--tag 标签] [--feature RF-101] [--file 路径] [--kind req|issue|decision|session|test] [--limit N]')
    bad = [k for k in kinds if k not in HISTORY_KINDS]
    if bad:
        raise ValueError(f'--kind 只能是 {"、".join(HISTORY_KINDS)}。')
    found = []
    for kind in kinds or HISTORY_KINDS:
        for item in SOURCES[kind](home):
            if files and kind in ('req', 'issue'):
                _work_files(home, item)
            if _wanted(item, words, tags, features, files):
                found.append(item)
    found.sort(key=lambda i: (i['date'], i['id']), reverse=True)
    limit = max(1, int(limit or 20))
    rows = [{'kind': i['kind'], 'id': i['id'], 'title': i['title'], 'state': i['state'], 'date': i['date'], 'file': i['file'],
             'topics': i['topics'], 'tags': i['tags'], 'hits': _hits(i, list(words) + list(features) + list(files) + list(tags))} for i in found[:limit]]
    return {'total': len(found), 'shown': len(rows), 'items': rows}


KIND_NAMES = {'req': '需求', 'issue': '问题', 'decision': '裁定', 'session': '会话', 'test': '测试集'}


def search_text(out):
    if not out['items']:
        return '没有找到。换个说法，或者少给几个词、去掉 --kind 再试。'
    lines = [f'找到 {out["total"]} 条' + (f'，列出最新的 {out["shown"]} 条（--limit 看更多）' if out['shown'] < out['total'] else '') + '：']
    for r in out['items']:
        paren = '，'.join(x for x in (r['state'], r['date']) if x)
        lines.append(f'■ {KIND_NAMES[r["kind"]]} {r["id"]} {r["title"]}' + (f'（{paren}）' if paren else ''))
        if r['topics']:
            lines.append('  概括：' + '、'.join(r['topics']))
        lines += [f'  {h}' for h in r['hits']]
    lines.append('')
    lines.append('看一条的完整摘要：rf.py history show 编号；看当时的聊天：rf.py history session 会话编号。')
    return '\n'.join(lines)


# ---------- 一条的完整摘要 ----------


def _session_rows(home, uuids):
    rows = []
    for u in uuids:
        tag = stable_tag(home, u)
        path = home / DIR / 'sessions' / f'{tag}.md'
        title = str(read_doc(path)[0].get('title') or '') if path.exists() else ''
        where, _ = session_where(home, None, u)
        rows.append({'tag': tag, 'session_id': u, 'title': title, 'record': {'archive': '本机有存档', 'original': '原始记录还在（还没存档）'}.get(where, '原始记录已不在')})
    return rows


def _decision_rows(home, ids):
    out = []
    for item in _decisions(home):
        if item['id'] in ids:
            first = live_items(item['sections'].get('决定')) or [_text(l) for l in item['sections'].get('决定', [])]
            out.append({'id': item['id'], 'title': item['title'], 'state': item['state'], 'decision': _short(first[0]) if first else ''})
    return out


def _show_req(home, item):
    meta, sections = item['meta'], item['sections']
    uuids = [str(x) for x in meta.get('sessions') or []]
    tags = [stable_tag(home, u) for u in uuids]
    decided = list(dict.fromkeys(_citing_decisions(home, item['id'], tags) + [t for t in item['tags'] if t.startswith('D-')]))
    work = None
    try:
        t = trace_ref(home, item['id'])
        work = {'commits': t['commit_count'], 'files': t['file_count'], 'merge': t['merge_commit']['short'] if t['merge_commit'] else None,
                'published': t['published'], 'top_files': [f['path'] for f in t['files'][:8]]}
    except Exception:  # noqa: BLE001 - no git, or nothing to trace: say nothing about commits
        work = None
    going = (meta.get('state') or 'open') in ('draft', 'open')
    out = {'kind': 'req', 'id': item['id'], 'title': item['title'], 'state': item['state'], 'created': str(meta.get('created') or ''), 'updated': item['date'],
           'file': item['file'], 'going': going,
           'say': [t for _, t in _lines({'需求说明': sections.get('需求说明', [])})],
           'features': [t for _, t in _lines({'x': sections.get('要改的功能点', [])})],
           'checks': [t for _, t in _lines({'x': sections.get('验收条件', [])})],
           'summary': live_items(sections.get('总结')), 'summarized': str(meta.get('summarized') or ''),
           'topics': item['topics'], 'tags': item['tags'], 'files': (work or {}).get('top_files') or [],
           'decisions': _decision_rows(home, set(decided)), 'work': work, 'sessions': _session_rows(home, uuids),
           'proposed': [str(x) for x in meta.get('summary_decisions') or []],
           'record': [t for _, t in _lines({'x': sections.get('变更记录', [])})][-8:]}
    out['testing'] = testing_line(testing_view(meta, sections))
    return out


def _show_issue(home, item):
    meta, sections = item['meta'], item['sections']
    fix = read_fix(home, meta.get('fix')) if meta.get('fix') else None
    uuids = [str(x) for x in (fix or {}).get('sessions') or []] + [str(x) for x in (read_probe(home, meta['id']) or {}).get('sessions') or []]
    work = None
    if fix:
        try:
            t = trace_ref(home, fix['id'])
            work = {'fix': fix['id'], 'commits': t['commit_count'], 'files': t['file_count'], 'merge': t['merge_commit']['short'] if t['merge_commit'] else None,
                    'published': t['published'], 'top_files': [f['path'] for f in t['files'][:8]]}
        except Exception:  # noqa: BLE001
            work = {'fix': fix['id']}
    return {'kind': 'issue', 'id': item['id'], 'title': item['title'], 'state': item['state'], 'updated': item['date'], 'file': item['file'],
            'kind_of': meta.get('kind'), 'feature': meta.get('feature'), 'what': live_items(sections.get('现象')), 'report': live_items(sections.get('修复报告')),
            'tags': item['tags'], 'files': item['files'], 'work': work, 'sessions': _session_rows(home, list(dict.fromkeys(uuids))),
            'record': [t for _, t in _lines({'x': sections.get('变更记录', [])})][-8:]}


def _show_decision(home, item):
    meta, sections = item['meta'], item['sections']
    cited = sorted(set(re.findall(r'〔(S-[^#〕]+)#', '\n'.join(l for b in sections.values() for l in b))))
    owners = tag_map(home)
    return {'kind': 'decision', 'id': item['id'], 'title': item['title'], 'state': item['state'], 'updated': item['date'], 'file': item['file'],
            'applies_to': item['tags'], 'decision': [t for _, t in _lines({'x': sections.get('决定', [])})],
            'reason': [t for _, t in _lines({'x': sections.get('理由', [])})], 'others': [t for _, t in _lines({'x': sections.get('没有选的方案', [])})],
            'sessions': [{'tag': t, 'owner': (owners.get(t) or (None, None))[1]} for t in cited],
            'record': [t for _, t in _lines({'x': sections.get('变更记录', [])})][-8:]}


def _show_session(home, item):
    meta = item['meta']
    data = read_session(home / item['file'])
    owner = (tag_map(home).get(item['id']) or (None, None))[1]
    uuid = str(meta.get('session_id') or '')
    where, _ = session_where(home, owner, uuid) if uuid else (None, None)
    return {'kind': 'session', 'id': item['id'], 'title': item['title'], 'updated': str(meta.get('updated') or ''), 'file': item['file'],
            'owner': owner, 'branch': meta.get('branch'), 'goal': data.get('goal'), 'doing': data.get('doing'),
            'plan': [{'status': p['status'], 'title': p['title'], 'note': p['note']} for p in data.get('plan') or []],
            'changes': [c['text'] for c in data.get('changes') or []], 'touch': item['tags'],
            'record': {'archive': '本机有存档', 'original': '原始记录还在（还没存档）'}.get(where, '原始记录已不在')}


def history_show(root, ref):
    home = main_checkout(root)
    ref = str(ref or '').strip()
    if re.fullmatch(r'#?\d+', ref):
        ref = 'I-%03d' % int(ref.lstrip('#'))
    kind = 'test' if re.match(r'TS-', ref, re.I) else {'R': 'req', 'I': 'issue', 'D': 'decision', 'S': 'session'}.get(ref[:1].upper()) if re.match(r'[RIDS]-', ref, re.I) else None
    if not kind:
        raise ValueError('用法：history show R-… | I-033 | D-0001 | S-… | TS-001')
    if kind == 'test':
        ref = 'TS-%03d' % int(re.sub(r'\D', '', ref) or 0)
    if kind == 'issue':
        m = re.fullmatch(r'I-0*(\d+)', ref, re.I)
        ref = f'I-{int(m.group(1)):03d}' if m else ref
    item = next((i for i in SOURCES[kind](home) if i['id'] == ref), None)
    if not item:
        raise ValueError(f'没有找到 {ref}。')
    return {'req': _show_req, 'issue': _show_issue, 'decision': _show_decision, 'session': _show_session, 'test': _show_test}[kind](home, item)


def _show_test(home, item):
    ts = item['meta']
    stats = read_stats(home, ts['id'])
    last = (stats.get('recent') or [{}])[-1]
    return {'kind': 'test', 'id': item['id'], 'title': item['title'], 'state': item['state'], 'updated': item['date'], 'file': item['file'],
            'feature': ts.get('feature'), 'requirement': ts.get('requirement'), 'fix': ts.get('fix'), 'entry': ts.get('entry'), 'checks': ts.get('checks') or [],
            'cases': [t for h, t in item['lines'] if h == '数据'], 'runs': int(stats.get('runs') or 0), 'failed_runs': int(stats.get('failed') or 0),
            'last': last or None, 'analysis': [t for h, t in item['lines'] if h.startswith('分析')], 'record': (ts.get('log') or [])[-8:]}


def _bullets(title, rows, empty=None):
    if not rows:
        return [f'{title}：{empty}'] if empty else []
    return [f'{title}：'] + [f'  - {r}' for r in rows]


def show_text(s):
    out = []
    if s['kind'] == 'req':
        out.append(f'需求 {s["id"]} {s["title"]}（{s["state"]}）  {s["file"]}')
        if s['going']:
            out.append('  注意：这个需求还没确认完成，它带来的事实是进行中的，不作为依据。')
        out += _bullets('需求说明', s['say'], '没有') + _bullets('要改的功能点', s['features'], '没有') + _bullets('验收条件', s['checks'], '没有')
        out += _bullets('总结' + (f'（{s["summarized"]}）' if s['summarized'] else ''), s['summary'], '还没总结')
        if s['topics']:
            out.append('概括标签：' + '、'.join(s['topics']))
        if s['tags']:
            out.append('涉及：' + '、'.join(s['tags']))
        out += _bullets('相关裁定', [f'{d["id"]} {d["title"]}（{d["state"]}）：{d["decision"]}' for d in s['decisions']])
        if s['proposed']:
            out.append('总结时提出、交给用户确认的决定：' + '、'.join(s['proposed']))
        w = s['work']
        if w and not w['commits'] and not w['files']:
            out.append('提交：没有认出它的提交（没有代码改动，或者是这个功能出现之前用别的办法合并的）。')
        elif w:
            out.append(f'提交：{w["commits"]} 个提交、{w["files"]} 个文件' + (f'，合并提交 {w["merge"]}' if w['merge'] else '') + ('，已发布' if w['published'] else '，还没发布' if w['merge'] else '')
                       + (f'；文件如 {"、".join(w["top_files"])}' if w['top_files'] else '') + f'（完整的用 rf.py trace {s["id"]}）')
        if s.get('testing'):
            out.append(s['testing'])
        out += _bullets('会话', [f'{r["tag"]} {r["title"]}（{r["record"]}）' for r in s['sessions']], '没有挂会话')
        out += _bullets('最近的变更记录', s['record'])
    elif s['kind'] == 'issue':
        out.append(f'问题 #{s["id"][2:]} {s["title"]}（{s["state"]}）  {s["file"]}')
        out += _bullets('现象', s['what'], '没有') + _bullets('修复报告', s['report'])
        if s['tags']:
            out.append('涉及：' + '、'.join(s['tags']))
        w = s['work']
        if w and 'commits' in w:
            out.append(f'修复 {w["fix"]}：{w["commits"]} 个提交、{w["files"]} 个文件' + (f'，合并提交 {w["merge"]}' if w['merge'] else '') + f'（完整的用 rf.py trace {w["fix"]}）')
        out += _bullets('会话', [f'{r["tag"]} {r["title"]}（{r["record"]}）' for r in s['sessions']])
        out += _bullets('最近的变更记录', s['record'])
    elif s['kind'] == 'decision':
        out.append(f'裁定 {s["id"]} {s["title"]}（{s["state"]}）  {s["file"]}')
        if s['applies_to']:
            out.append('适用于：' + '、'.join(s['applies_to']))
        out += _bullets('决定', s['decision'], '没有') + _bullets('理由', s['reason']) + _bullets('没有选的方案', s['others'])
        out += _bullets('出处的会话', [f'{x["tag"]}' + (f'（属于 {x["owner"]}）' if x['owner'] else '') for x in s['sessions']])
        out += _bullets('最近的变更记录', s['record'])
    elif s['kind'] == 'test':
        out.append(f'测试集 {s["id"]} {s["title"]}（{s["state"]}）  {s["file"]}')
        out.append(f'功能点 {s["feature"]}' + (f'，来自需求 {s["requirement"]}' if s['requirement'] else '') + (f'，来自修复 {s["fix"]}' if s['fix'] else '') + f'，接入代码 {s["entry"]}')
        out += _bullets('对应的验收条件', s['checks'])
        out += _bullets(f'数据（{len(s["cases"])} 条）', s['cases'][:30], '没有')
        last = s['last'] or {}
        out.append(f'跑过 {s["runs"]} 次，失败过 {s["failed_runs"]} 次' + (f'；最近一次 {last.get("at")}：{ {"pass": "通过", "fail": "失败", "broken": "跑不起来"}.get(last.get("result"), last.get("result")) }' if last else ''))
        out += _bullets('分析（AI 写的）', s['analysis'])
        out += _bullets('最近的变更记录', s['record'])
    else:
        out.append(f'会话 {s["id"]} {s["title"]}' + (f'（属于 {s["owner"]}）' if s['owner'] else '') + f'  {s["file"]}')
        out.append(f'目标：{s["goal"] or "没有记"}')
        if s['doing']:
            out.append(f'最后在做：{s["doing"]}')
        marks = {'done': '[x]', 'doing': '[~]', 'todo': '[ ]', 'dropped': '[-]'}
        out += _bullets('计划', [f'{marks.get(p["status"], "[ ]")} {p["title"]}' + (f'｜{p["note"]}' if p['note'] else '') for p in s['plan']])
        out += _bullets('方向调整', s['changes'])
        if s['touch']:
            out.append('动过：' + '、'.join(s['touch']))
        out.append(f'聊天记录：{s["record"]}' + ('' if s['record'] == '原始记录已不在' else f'，用 rf.py history session {s["id"]} 看'))
    return '\n'.join(out)


# ---------- 当时的聊天 ----------


def _walk(path):
    """('msg', role, text, ts) and ('tool', name) in order; the same messages and numbering as transcript.read_transcript."""
    with open_text(path) as lines:
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get('type') == 'response_item':  # Codex
                p = obj.get('payload') or {}
                if p.get('type') == 'message' and p.get('role') in ('user', 'assistant'):
                    text = clean('\n'.join(b.get('text', '') for b in p.get('content', []) if isinstance(b, dict) and b.get('type') in ('input_text', 'output_text')))
                    if text:
                        yield 'msg', p['role'], text, obj.get('timestamp')
                elif p.get('type') in ('function_call', 'custom_tool_call', 'local_shell_call'):
                    yield 'tool', str(p.get('name') or 'shell')
                continue
            if obj.get('type') not in ('user', 'assistant') or obj.get('isSidechain'):
                continue
            content = (obj.get('message') or {}).get('content')
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        yield 'tool', str(b.get('name') or '工具')
                text = '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
            else:
                continue
            text = clean(text)
            if text:
                yield 'msg', obj['type'], text, obj.get('timestamp') if isinstance(obj.get('timestamp'), str) else None


def _subagents(home, owner, uuid, where, path):
    if where == 'archive':
        return len(list((path.parent / uuid / 'subagents').glob('*.jsonl.gz')))
    if where == 'original':
        return len(list((path.parent / uuid / 'subagents').glob('*.jsonl')))
    return 0


def history_session(root, ref, grep=None, tail=None, steps=False, *, offset=None):
    if offset is not None and (not steps or offset < 0 or tail is not None):
        raise ValueError('--offset 需要配合 --steps，从 0 开始，不能与 --tail 同时使用。')
    home = main_checkout(root)
    tag, uuid, owner = resolve_session(home, ref)
    where, path = session_where(home, owner, uuid)
    base = {'session': tag, 'session_id': uuid, 'owner': owner, 'source': where, 'messages': [], 'total': 0, 'cut': False, 'subagents': 0}
    if steps:
        base = dict(base, steps=True, turns=[], total_turns=0, totals=_totals([]))
        return _session_steps(home, base, path, grep, tail, offset) if where else base
    if not where:
        return base
    messages, tools = [], []
    for step in _walk(path):
        if step[0] == 'tool':
            tools.append(step[1])
            continue
        _, role, text, ts = step
        messages.append({'n': len(messages) + 1, 'role': role, 'when': turn_when(ts), 'text': text, 'tools': _tool_note(tools)})
        tools = []
    picked = [m for m in messages if not grep or grep.casefold() in m['text'].casefold()]
    if tail:
        picked = picked[-max(1, int(tail)):]
    out, size, cut = [], 0, False
    for m in picked:
        text = m['text'] if len(m['text']) <= MESSAGE_CAP else m['text'][:MESSAGE_CAP] + f'…（这条有 {len(m["text"])} 字，截断了）'
        size += len(text) + 40
        if size > OUT_CAP:
            cut = True
            break
        out.append(dict(m, text=text))
    return dict(base, messages=out, total=len(messages), matched=len(picked), cut=cut, subagents=_subagents(home, owner, uuid, where, path))


def _tool_note(names):
    if not names:
        return ''
    counted = {}
    for n in names:
        counted[n] = counted.get(n, 0) + 1
    return '、'.join(f'{n}×{c}' if c > 1 else n for n, c in counted.items())


def session_text(s, grep=None):
    head = f'会话 {s["session"]}' + (f'（属于 {s["owner"]}）' if s['owner'] else '')
    if not s['source']:
        return head + '：这台电脑上没有它的聊天记录（原始记录已经被 Claude Code 清理，也没有存档）。进展可以用 rf.py history show ' + s['session'] + ' 看。'
    lines = [head + f'：{"本机存档" if s["source"] == "archive" else "原始记录（还没存档）"}，共 {s["total"]} 条消息'
             + (f'，含「{grep}」的 {s["matched"]} 条' if grep else '') + (f'，另有 {s["subagents"]} 个子代理的记录没有展开' if s['subagents'] else '') + '。工具的输出不放进来。']
    for m in s['messages']:
        if m['tools'] and m['role'] == 'assistant':
            lines.append(f'  （用了工具：{m["tools"]}）')
        lines.append(f'【{m["n"]}】{"用户" if m["role"] == "user" else "AI"}' + (f' {m["when"]}' if m['when'] else '') + f'：{m["text"]}')
    if not s['messages']:
        lines.append('没有符合的消息。')
    if s['cut']:
        lines.append(f'…输出到上限了（{OUT_CAP} 字）。用 --tail N 看最后几条，或 --grep 词 只看相关的。')
    return '\n'.join(lines)


# ---------- 步骤视图（history session --steps） ----------
# 和上面读的是同一份记录、同样的消息编号（【n】和出处里的 S-…#n 对得上）。一轮从用户说的一条话开始，到下一条为止。
# 工具的输出不进结果：只用来判断成功、失败、被拒绝，和从 git commit 的输出里认出提交编号。


def _totals(turns):
    return {k: sum(t[k] for t in turns) for k in ('calls', 'failed', 'refused', 'read', 'write')} | {
        'seconds': sum(t['seconds'] or 0 for t in turns), 'commits': sum(len(t['commits']) for t in turns)}


def _session_steps(home, base, path, grep, tail, offset):
    replay = offset is not None
    if replay:
        try:
            turns = read_turns(home, path, cap=None, replay=True, secrets=all_secret_values(home))
        except (OSError, EOFError):
            return dict(base, source=None)
    else:
        turns = read_turns(home, path)
    totals, total_turns = _totals(turns), len(turns)
    if grep:
        g = grep.casefold()
        turns = [t for t in turns if g in t['said'].casefold() or g in t['reply'].casefold()
                 or any(g in s['detail'].casefold() for s in t['steps']) or any(g in c['subject'].casefold() for c in t['commits'])]
    matched = len(turns)
    if tail:
        turns = turns[-max(1, int(tail)):]
    if replay:
        turns = turns[offset:]
    out, size, cut = [], 0, False
    for t in turns:
        size += len(t['said']) + len(t['reply']) + sum(len(s['detail']) + 12 for s in t['steps']) + 80
        if size > OUT_CAP and (out or not replay):
            cut = True
            break
        out.append(t)
    if replay:
        base = dict(base, offset=offset, next_offset=offset + len(out) if cut else None)
    return dict(base, turns=out, total_turns=total_turns, totals=totals, matched=matched, cut=cut,
                subagents=_subagents(home, base['owner'], base['session_id'], base['source'], path))


def _amount(n):
    return f'{n / 10000:.1f}'.rstrip('0').rstrip('.') + ' 万' if n >= 10000 else str(n)


def _span(seconds):
    if seconds is None:
        return ''
    return f'{seconds} 秒' if seconds < 60 else f'{seconds // 60} 分钟' if seconds < 3600 else f'{seconds // 3600} 小时 {seconds % 3600 // 60} 分钟'


def _cost(seconds, read, write):
    parts = ([f'用时 {_span(seconds)}'] if seconds else []) + ([f'token 读入 {_amount(read)}、写出 {_amount(write)}'] if read or write else [])
    return '，'.join(parts)


def _turn_lines(turn, last=None):
    """One turn as text. last=N keeps only its last N steps (what it was doing when the turn ended)."""
    cost = _cost(turn['seconds'], turn['read'], turn['write'])
    if turn['n'] is None:
        lines = ['开场（用户开口之前）' + (f'（{cost}）' if cost else '') + '：']
    else:
        speaker = '程序消息' if turn.get('speaker') == 'program' else '用户'
        lines = [f'【{turn["n"]}】{speaker}' + (f' {turn["when"]}' if turn['when'] else '') + (f'（{cost}）' if cost else '') + f'：{turn["said"]}']
    steps, before = turn['steps'], turn['more']
    if last is not None and len(steps) > last:
        steps, before = steps[-last:], 0
        lines.append(f'  · …前面还有 {len(turn["steps"]) - last + turn["more"]} 步')
    lines += [step_line(st) for st in steps]
    if before:
        lines.append(f'  · …另有 {before} 步没有列出')
    for c in turn['commits']:
        lines.append(f'  · 提交 {c["commit"] or "（输出里没有提交号）"} {c["subject"]}')
    for r in turn['repeats']:
        lines.append(f'  ！同一步失败了 {r["times"]} 次：{r["what"]} {r["detail"]}')
    if turn['reply']:
        lines.append(f'  【{turn["reply_n"]}】AI：{turn["reply"]}')
    return lines


RECAP_TURNS = 3         # 续接时放进开场白的最后几轮
RECAP_STEPS = 8         # 其中每轮最多几步（留最后几步：这一轮停在哪）
RECAP_CAP = 4000        # 最多多少字


def recap_text(s):
    """For the opening prompt of the session that takes over: what the last few turns of the previous one did. '' when there is no record."""
    if not s.get('source') or not s['turns']:
        return ''
    t = s['totals']
    head = (f'上一个会话 {s["session"]} 最后 {len(s["turns"])} 轮做了什么（程序从它的聊天记录算的，它一共 {s["total_turns"]} 轮、提交 {t["commits"]} 个；'
            f'工具的输出不在里面，要看全部用 rf.py history session {s["session"]} --steps）：')
    body = []
    for turn in reversed(s['turns']):  # 超出上限时丢最早的轮
        lines = _turn_lines(turn, RECAP_STEPS)
        if body and sum(len(x) + 1 for x in lines + body) > RECAP_CAP:
            break
        body = lines + body
    return head + '\n<<<\n' + '\n'.join(body)[:RECAP_CAP] + '\n>>>'


def steps_text(s, grep=None):
    head = f'会话 {s["session"]}' + (f'（属于 {s["owner"]}）' if s['owner'] else '')
    if not s['source']:
        return head + '：这台电脑上没有它的聊天记录（原始记录已经被 Claude Code 清理，也没有存档）。进展可以用 rf.py history show ' + s['session'] + ' 看。'
    t = s['totals']
    cost = _cost(t['seconds'], t['read'], t['write'])
    lines = [head + f'：{"本机存档" if s["source"] == "archive" else "原始记录（还没存档）"}，{s["total_turns"]} 轮，工具调用 {t["calls"]} 次'
             + (f'，失败 {t["failed"]} 次' if t['failed'] else '') + (f'，被拒绝 {t["refused"]} 次' if t['refused'] else '')
             + (f'，提交 {t["commits"]} 个' if t['commits'] else '') + (f'，{cost}' if cost else '')
             + (f'；含「{grep}」的 {s["matched"]} 轮' if grep else '') + (f'；另有 {s["subagents"]} 个子代理的记录没有展开' if s['subagents'] else '')
             + '。命令和文件照录，工具的输出不放进来。']
    for turn in s['turns']:
        lines += _turn_lines(turn)
    if not s['turns']:
        lines.append('没有符合的轮次。')
    if s.get('next_offset') is not None:
        lines.append(f'…用 --offset {s["next_offset"]} 看后续轮次。')
    elif s['cut']:
        lines.append(f'…输出到上限了（{OUT_CAP} 字）。用 --tail N 看最后几轮，或 --grep 词 只看相关的。')
    return '\n'.join(lines)
