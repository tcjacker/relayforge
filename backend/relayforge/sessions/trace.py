"""追溯：从一个需求、修复、问题、提交或一行代码，找到做它的需求、会话和改动。全部由程序算，不调用模型，只读。

  trace R-… | F-… | I-… [--json]     它的提交、改了哪些文件、挂了哪些会话、合并提交、有没有发布
  trace --commit 哈希 [--json]        从一个提交出发：它属于哪个需求或修复，再给出同样的内容
  why 文件[:行|:起-止] [--json]        这个文件（或这几行）最近是哪些提交改的，各属于哪个需求或修复：需求说明、验收条件、
                                      它记下的裁定、会话和会话进展文件；修复给问题的现象和修复报告；不属于任何一个的也列出来
                                      每个提交还给出产生它的那一轮：用户那一轮说了什么，从上一个提交到这个提交之间读了什么、跑了什么、
                                      改了什么、哪一步失败或被拒绝（读本机的聊天记录，和 history session --steps 同一份数据；没有记录就不给）

一个提交属于哪个需求或修复，按这个顺序认：
1. 提交说明里的行（trailers.py：RelayForge-Requirement / RelayForge-Fix / RelayForge-Issue / RelayForge-Session），工作目录里的提交都由 git 钩子带上；
2. 没有这些行的老提交（这个功能出现之前完成的）：看合并提交的第二个父提交一路上的提交，和发布（publish.py）认「哪个需求带来了哪些提交」是同一个办法：
   合并提交的编号记在需求文件头的 merge_commit 和修复记录 merge_commit 里，更早的按合并提交的标题（合并需求：……（R-…）、合并修复：……（F-…））找；
3. 还没合并的（进行中）：它自己的分支上、不在目标分支里的提交。
"""

import re
from pathlib import Path

from ..core.common import DIR, git, main_checkout
from ..core.documents import live_items, read_doc
from .steps import commit_steps, commit_steps_lines
from ..requirements.owners import all_fixes, find_issue, norm_id, num, read_fix
from ..project.publish import landing_commits, reached_remotes
from .record import read_session, stable_tag
from .trailers import KEYS, parse
from ..requirements.testrecords import testing_line, testing_view
from ..testsets.store import all_testsets

__all__ = ['trace_ref', 'trace_commit', 'trace_item', 'item_files', 'why', 'trace_text', 'why_text', 'counts']

LIST_CAP = 40          # 一次最多列这么多提交、文件
TEXT_CAP = 6           # 需求说明、验收条件各最多列几条
WHY_ITEMS = 6          # why 最多展开几个需求或修复
WHY_COMMITS = 12       # why 最多看几个提交
LINE_CAP = 220         # 一行文字最多多少字
NONE = '不属于任何需求或修复'


def _short(text, cap=LINE_CAP):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= cap else text[:cap - 1] + '…'


# ---------- 认出一个需求或修复 ----------

def _requirement(home, ident):
    for path in sorted((Path(home) / DIR / 'requirements').glob('*.md')):
        meta, sections = read_doc(path)
        if meta.get('id') == ident:
            return {'kind': 'requirement', 'id': ident, 'title': str(meta.get('title') or ''), 'state': meta.get('state') or 'open', 'branch': meta.get('branch'),
                    'merge_commit': meta.get('merge_commit'), 'sessions': [str(x) for x in meta.get('sessions') or []], 'issues': [],
                    'file': f'{DIR}/requirements/{path.name}', 'meta': meta, 'sections': sections}
    return None


def _fix(home, ident):
    fix = read_fix(home, ident)
    if not fix:
        return None
    titles = {}
    issues = []
    for one in fix.get('issues') or []:
        try:
            _, meta, _ = find_issue(home, one)
            titles[one] = str(meta.get('title') or '')
        except ValueError:
            titles[one] = ''
        issues.append({'id': one, 'num': num(one), 'title': titles[one]})
    return {'kind': 'fix', 'id': ident, 'title': '、'.join(f'{i["num"]}「{i["title"]}」' for i in issues), 'state': {'merged': 'done', 'fixing': 'open'}.get(fix.get('state'), fix.get('state') or 'open'),
            'branch': fix.get('branch'), 'merge_commit': fix.get('merge_commit'), 'sessions': [str(x) for x in fix.get('sessions') or []], 'issues': issues,
            'file': f'{DIR}/issues/fixes/{ident}.json', 'meta': fix, 'sections': {}}


def resolve(home, ref):
    """R-…, F-… or an issue (I-033, #033) -> the requirement or fix it belongs to."""
    text = str(ref or '').strip()
    if re.match(r'R-', text, re.I):
        item = _requirement(home, 'R-' + text[2:])
        if not item:
            raise ValueError(f'没有找到需求 {text}。')
        return item
    if re.match(r'F-', text, re.I):
        item = _fix(home, text.upper())
        if not item:
            raise ValueError(f'没有找到修复 {text}。')
        return item
    ident = norm_id(text)
    meta = find_issue(home, ident)[1]
    if not meta.get('fix'):
        raise ValueError(f'问题 {num(ident)} 还没有开始修复，没有可追溯的改动。')
    item = _fix(home, meta['fix'])
    if not item:
        raise ValueError(f'问题 {num(ident)} 的修复 {meta["fix"]} 找不到记录。')
    item['asked_issue'] = ident
    return item


# ---------- 一个需求或修复的提交 ----------

def _log(home, *args):
    return git(home, 'log', '--format=%H', *args).split()


def _trailer_commits(home, item):
    key = KEYS[item['kind']]
    return _log(home, '--all', '-E', f'--grep=^{key}: {re.escape(item["id"])}[[:space:]]*$')


def _anchor(home, item):
    """The merge commit that landed a finished requirement or fix, or None."""
    if item['state'] != 'done':
        return None
    state_file = (item['file'], 'state: done') if item['kind'] == 'requirement' else None
    for a in landing_commits(home, [item.get('merge_commit')], item['id'], state_file):
        parents = git(home, 'rev-list', '--parents', '-n', '1', a).split()[1:]
        if len(parents) > 1:
            return a
    return None


def _legacy_commits(home, anchor):
    parents = git(home, 'rev-list', '--parents', '-n', '1', anchor).split()[1:]
    return [anchor] + git(home, 'rev-list', f'{parents[0]}..{parents[1]}').split()


def _branch_commits(home, item):
    branch = item.get('branch')
    if not branch or not git(home, 'rev-parse', '--verify', '-q', f'refs/heads/{branch}').strip():
        return []
    current = git(home, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    spec = [branch] + (['--not', current] if current and current not in ('HEAD', branch) else [])
    return git(home, 'rev-list', *spec).split()


def _details(home, hashes):
    if not hashes:
        return []
    out = git(home, 'log', '--no-walk=unsorted', '--format=%x01%H%x02%P%x02%cI%x02%ct%x02%s%x02%B%x03', *hashes)
    rows = []
    for chunk in out.split('\x01')[1:]:
        h, parents, date, stamp, subject, body = (chunk.split('\x02') + ['', '', '', '', '', ''])[:6]
        rows.append({'hash': h, 'short': h[:7], 'parents': parents.split(), 'date': date[:10], 'stamp': int(stamp or 0), 'subject': subject,
                     'links': parse(body)})
    rows.sort(key=lambda r: (r['stamp'], r['hash']))
    return rows


def _numstat(home, *spec):
    files = {}
    for line in git(home, 'diff', '--numstat', *spec).splitlines():
        a, _, rest = line.partition('\t')
        d, _, name = rest.partition('\t')
        name = name.strip('"')
        if not name or name.startswith(DIR + '/'):
            continue
        files[name] = [int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0]
    return files


def commits_of(home, item):
    """The commits of a requirement or fix: [{hash, short, date, subject, sessions, via, merge}], oldest first, and the merge commit."""
    anchor = _anchor(home, item)
    via = {}
    for h in _trailer_commits(home, item):
        via[h] = 'trailer'
    if anchor:
        for h in _legacy_commits(home, anchor):
            via.setdefault(h, 'merge')
    elif item['state'] != 'done':
        for h in _branch_commits(home, item):
            via.setdefault(h, 'branch')
    rows = _details(home, list(via))
    for r in rows:
        r['via'], r['merge'] = via[r['hash']], r['hash'] == anchor
        r['sessions'] = r['links']['session']
    return rows, anchor


def _files_of(home, item, rows, anchor):
    """The files the work changed, with line counts: the net change the merge brought in, or (not merged) the branch against its
    base, or (no merge commit to look at) the sum over its commits."""
    if anchor:
        parents = git(home, 'rev-list', '--parents', '-n', '1', anchor).split()[1:]
        return _numstat(home, parents[0], anchor)
    branch = item.get('branch')
    if item['state'] != 'done' and branch and git(home, 'rev-parse', '--verify', '-q', f'refs/heads/{branch}').strip():
        current = git(home, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
        base = git(home, 'merge-base', current, branch).strip() if current and current != 'HEAD' else ''
        if base:
            return _numstat(home, base, branch)
    files = {}
    for r in rows:
        if len(r['parents']) > 1:
            continue
        for name, (a, d) in _numstat(home, f'{r["hash"]}^!' if r['parents'] else r['hash']).items():
            files.setdefault(name, [0, 0])
            files[name][0] += a
            files[name][1] += d
    return files


def _session_view(home, tags_by_uuid, uuids, tags):
    """Sessions of an item: the ones attached to it (uuids) plus the tags seen on its commits."""
    seen, out = set(), []
    pairs = [(u, tags_by_uuid[u]) for u in uuids] + [('', t) for t in tags]
    for uuid, tag in pairs:
        if tag in seen:
            continue
        seen.add(tag)
        path = Path(home) / DIR / 'sessions' / f'{tag}.md'
        info = {'tag': tag, 'session_id': uuid or None, 'file': None, 'path': None, 'title': '', 'goal': ''}
        if path.exists():
            meta = read_session(path)
            info.update(file=f'{DIR}/sessions/{tag}.md', path=str(path), title=str(meta.get('title') or ''), goal=_short(meta.get('goal')))
        out.append(info)
    return out


def item_files(home, ref):
    """Every file a requirement or fix changed (not capped like trace_item), as {path: [added, deleted]}; {} when it cannot be found."""
    home = main_checkout(home)
    try:
        item = resolve(home, ref)
    except ValueError:
        return {}
    rows, anchor = commits_of(home, item)
    return _files_of(home, item, rows, anchor)


def trace_item(home, item):
    home = main_checkout(home)
    rows, anchor = commits_of(home, item)
    files = _files_of(home, item, rows, anchor)
    uuid_tags = {u: stable_tag(home, u) for u in item['sessions']}
    seen = []
    for r in rows:
        for t in r['sessions']:
            if t not in seen:
                seen.append(t)
    sessions = _session_view(home, uuid_tags, item['sessions'], seen)
    work = [r for r in rows if not r['merge']]
    merge = next((r for r in rows if r['merge']), None)
    published = None
    remotes = []
    if merge:
        remotes = reached_remotes(home, merge['hash'])
        published = bool(remotes)
    elif item['state'] == 'done':
        published = None  # finished without a merge commit (no code changes)
    total_files = len(files)
    return {
        'kind': item['kind'], 'id': item['id'], 'title': item['title'], 'state': item['state'], 'branch': item['branch'],
        'issues': item['issues'], 'file': item['file'],
        'merge_commit': {'hash': merge['hash'], 'short': merge['short'], 'date': merge['date'], 'subject': merge['subject']} if merge else None,
        'commit_count': len(work), 'commits': [{k: r[k] for k in ('hash', 'short', 'date', 'subject', 'sessions', 'via')} for r in work[:LIST_CAP]],
        'commits_more': max(0, len(work) - LIST_CAP),
        'file_count': total_files, 'files': [{'path': p, 'added': a, 'deleted': d} for p, (a, d) in sorted(files.items())[:LIST_CAP]],
        'files_more': max(0, total_files - LIST_CAP),
        'sessions': sessions, 'merged': bool(merge), 'published': published, 'remotes': remotes,
        'testsets': _testsets_of(home, item['id']),
        'testing': _testing_of(item),
    }


def _testing_of(item):
    """For a requirement: how its testing went (rounds, skipped), one line; empty when it never had a test stage."""
    if item['kind'] != 'requirement':
        return ''
    return testing_line(testing_view(item.get('meta') or {}, item.get('sections') or {}))


def _testsets_of(home, ident):
    """The test sets a requirement or fix created (or that were hung on it later)."""
    return [{'id': ts['id'], 'title': ts.get('title'), 'feature': ts.get('feature'), 'cases': sum(1 for c in ts.get('cases') or [] if not c.get('dropped_with')),
             'in_progress': ts.get('in_progress'), 'dropped': bool(ts.get('_shelved'))}
            for ts in all_testsets(home, shelved=True) if ident in (ts.get('requirement'), ts.get('fix'), ts.get('in_progress'))]


def counts(home, item_ref, merge_commit=None):
    """(commits, files) a requirement or fix comprised: what goes into the 完成记录 when it is finished. Called while it is being
    finished, before its file says so: `merge_commit` is the merge that just landed it."""
    item = resolve(home, item_ref)
    if merge_commit:
        item.update(state='done', merge_commit=merge_commit)
    t = trace_item(home, item)
    return t['commit_count'], t['file_count']


def trace_ref(home, ref):
    home = main_checkout(home)
    return trace_item(home, resolve(home, ref))


# ---------- 从一个提交出发 ----------

def _full(home, rev):
    out = git(home, 'rev-parse', '--verify', '-q', f'{rev}^{{commit}}').strip()
    if not out:
        raise ValueError(f'没有找到提交 {rev}。')
    return out


class Owners:
    """Which requirement or fix each commit belongs to; the old-history index is built the first time it is needed."""

    def __init__(self, home):
        self.home = main_checkout(home)
        self._legacy = None

    def index(self):
        if self._legacy is None:
            self._legacy = {}
            for path in sorted((self.home / DIR / 'requirements').glob('*.md')):
                item = _requirement(self.home, read_doc(path)[0].get('id'))
                self._add(item)
            for fid in all_fixes(self.home):
                self._add(_fix(self.home, fid))
        return self._legacy

    def _add(self, item):
        if not item or item['state'] != 'done':
            return
        anchor = _anchor(self.home, item)
        if anchor:
            for h in _legacy_commits(self.home, anchor):
                self._legacy.setdefault(h, (item['kind'], item['id']))

    def of(self, commit, links=None):
        """(kind, id, how) or None."""
        links = links or parse(git(self.home, 'log', '-1', '--format=%B', commit))
        for kind in ('requirement', 'fix'):
            if links[kind]:
                return kind, links[kind][-1], 'trailer'
        found = self.index().get(commit)
        return (found[0], found[1], 'merge') if found else None


def trace_commit(home, rev):
    home = main_checkout(home)
    full = _full(home, rev)
    owner = Owners(home).of(full)
    row = _details(home, [full])[0]
    if not owner:
        return {'commit': {k: row[k] for k in ('hash', 'short', 'date', 'subject')}, 'owner': None, 'text': f'提交 {row["short"]} 不属于任何需求或修复。'}
    item = resolve(home, owner[1])
    out = trace_item(home, item)
    out['commit'] = {k: row[k] for k in ('hash', 'short', 'date', 'subject')}
    out['owner'] = {'kind': owner[0], 'id': owner[1], 'how': owner[2]}
    return out


# ---------- 为什么这样写 ----------

def _spec(text):
    """path[:line | :from-to] -> (path, from, to)"""
    m = re.fullmatch(r'(.+?):(\d+)(?:-(\d+))?', text)
    if not m:
        return text, None, None
    a = int(m.group(2))
    b = int(m.group(3) or a)
    return m.group(1), min(a, b), max(a, b)


def _relpath(home, path):
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(home))
        except ValueError:
            return str(p)
    return str(path)


def commits_for(home, relpath, first, last):
    """The commits that last changed this file (or these lines): [(hash, line numbers or [])], newest first."""
    if first is None:
        return [(h, []) for h in _log(home, '--follow', '-n', str(WHY_COMMITS), '--', relpath)]
    out = git(home, 'blame', '--porcelain', '-M', '-C', '-L', f'{first},{last}', 'HEAD', '--', relpath)
    seen, order = {}, []
    lines = out.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r'^([0-9a-f]{40}) \d+ (\d+)', line)
        if m:
            h = m.group(1)
            if h not in seen:
                seen[h] = []
                order.append(h)
            seen[h].append(int(m.group(2)))
    stamp = {h: int(git(home, 'log', '-1', '--format=%ct', h).strip() or 0) for h in order if not set(h) == {'0'}}
    order = sorted((h for h in order if h in stamp), key=lambda h: stamp[h], reverse=True)
    return [(h, seen[h]) for h in order[:WHY_COMMITS]]


def _decisions(home, item, tags):
    """The decisions a requirement recorded: those it brought (in_progress) or that cite one of its sessions."""
    out = []
    for kind in ('decisions',):
        for path in sorted((Path(home) / DIR / kind).glob('*.md')):
            meta, sections = read_doc(path)
            if not meta.get('id'):
                continue
            text = '\n'.join(l for lines in sections.values() for l in lines)
            if meta.get('in_progress') == item['id'] or any(f'〔{t}#' in text for t in tags):
                first = live_items(sections.get('决定'))
                out.append(f'{meta["id"]} {meta.get("title") or ""}：{_short(first[0]) if first else ""}'.rstrip('：'))
    return out[:TEXT_CAP]


def _explain(home, item, tags):
    out = {'kind': item['kind'], 'id': item['id'], 'title': item['title'], 'state': item['state'], 'file': item['file'], 'path': str(Path(home) / item['file'])}
    if item['kind'] == 'requirement':
        sections = item['sections']
        out['说明'] = [_short(x) for x in live_items(sections.get('需求说明'))][:TEXT_CAP]
        out['验收条件'] = [_short(x) for x in live_items(sections.get('验收条件'))][:TEXT_CAP]
        out['裁定'] = _decisions(home, item, tags)
    else:
        issues = []
        for one in item['issues']:
            try:
                _, meta, sections = find_issue(home, one['id'])
            except ValueError:
                continue
            issues.append({'num': one['num'], 'title': one['title'], '现象': [_short(x) for x in live_items(sections.get('现象'))][:TEXT_CAP],
                           '修复报告': [_short(x) for x in live_items(sections.get('修复报告'))][:TEXT_CAP]})
        out['issues'] = issues
    return out


def why(home, spec):
    home = main_checkout(home)
    relpath, first, last = _spec(spec)
    relpath = _relpath(home, relpath)
    if not git(home, 'ls-files', '--', relpath).strip() and not (home / relpath).exists():
        raise ValueError(f'项目里没有 {relpath}。')
    picked = commits_for(home, relpath, first, last)
    owners = Owners(home)
    grouped, lone, turns = {}, [], {}
    for h, lines in picked:
        row = _details(home, [h])
        if not row:
            continue
        row = row[0]
        owner = owners.of(h, row['links'])
        entry = {'hash': row['hash'], 'short': row['short'], 'date': row['date'], 'subject': row['subject'], 'lines': lines,
                 'sessions': row['links']['session']}
        try:
            entry['turn'] = commit_steps(home, row['links']['session'], row['hash'], row['subject'], turns) if row['links']['session'] else None
        except Exception:
            entry['turn'] = None  # 读不了聊天记录不妨碍其余的说明
        if owner:
            grouped.setdefault((owner[0], owner[1]), []).append(dict(entry, how=owner[2]))
        else:
            lone.append(entry)
    items = []
    for (kind, ident), commits in list(grouped.items())[:WHY_ITEMS]:
        item = resolve(home, ident)
        uuid_tags = {u: stable_tag(home, u) for u in item['sessions']}
        seen = [t for c in commits for t in c['sessions']]
        sessions = _session_view(home, uuid_tags, item['sessions'], seen)
        explained = _explain(home, item, [s['tag'] for s in sessions])
        explained.update(commits=commits, sessions=sessions)
        items.append(explained)
    return {'path': relpath, 'from': first, 'to': last, 'looked_at': len(picked), 'items': items, 'items_more': max(0, len(grouped) - WHY_ITEMS),
            'unlinked': lone, 'note': NONE}


# ---------- 文字 ----------

def _state_text(t):
    word = {'requirement': {'draft': '立项中', 'open': '进行中', 'done': '已完成', 'dropped': '已取消'}, 'fix': {'open': '修复中', 'done': '已解决'}}[t['kind']].get(t['state'], t['state'])
    pub = ''
    if t['merged']:
        pub = '，已发布' + (f'（{"、".join(t["remotes"][:3])}）' if t['remotes'] else '') if t['published'] else '，还没发布'
    return word + ('，已合并' if t['merged'] else '') + pub


def trace_text(t):
    name = {'requirement': '需求', 'fix': '修复'}[t['kind']]
    lines = []
    if t.get('commit'):
        lines.append(f'提交 {t["commit"]["short"]} {t["commit"]["date"]} {t["commit"]["subject"]}')
        if t.get('owner'):
            lines.append(f'  属于{name} {t["id"]}（{"提交说明里的行" if t["owner"]["how"] == "trailer" else "合并提交的第二个父提交一路上的提交（老办法）"}）')
        else:
            return '\n'.join(lines + [f'  {NONE}。'])
    lines.append(f'{name} {t["id"]} {t["title"]}（{_state_text(t)}）')
    if t['kind'] == 'fix' and t['issues']:
        lines.append('  问题：' + '、'.join(f'{i["num"]}「{i["title"]}」' for i in t['issues']))
    if t['merge_commit']:
        m = t['merge_commit']
        lines.append(f'  合并提交：{m["short"]} {m["date"]} {m["subject"]}')
    lines.append(f'  提交 {t["commit_count"]} 个：')
    for c in t['commits']:
        lines.append(f'    {c["short"]} {c["date"]} {c["subject"]}' + (f'  [{"、".join(c["sessions"])}]' if c['sessions'] else ''))
    if t['commits_more']:
        lines.append(f'    …另有 {t["commits_more"]} 个')
    lines.append(f'  文件 {t["file_count"]} 个：')
    for f in t['files']:
        lines.append(f'    {f["path"]}  +{f["added"]} -{f["deleted"]}')
    if t['files_more']:
        lines.append(f'    …另有 {t["files_more"]} 个')
    if t.get('testsets'):
        lines.append(f'  测试集 {len(t["testsets"])} 个：' + '；'.join(f'{x["id"]} {x["title"]}（{x["feature"]}，{x["cases"]} 条' + ('，随放弃移走' if x['dropped'] else '，进行中' if x['in_progress'] else '') + '）' for x in t['testsets']))
    if t.get('testing'):
        lines.append('  ' + t['testing'])
    lines.append(f'  会话 {len(t["sessions"])} 个：' if t['sessions'] else '  会话：没有记录')
    for s in t['sessions']:
        lines.append(f'    {s["tag"]}' + (f'  {s["title"]}' if s['title'] else '') + (f'  进展：{s["path"]}' if s['path'] else '  （没有进展文件）'))
    return '\n'.join(lines)


def why_text(w):
    span = f'{w["path"]}' + (f' 第 {w["from"]}' + (f'-{w["to"]}' if w['to'] != w['from'] else '') + ' 行' if w['from'] else '')
    lines = [f'{span}：看了最近改它的 {w["looked_at"]} 个提交，属于 {len(w["items"]) + w["items_more"]} 个需求或修复' + (f'，另有 {len(w["unlinked"])} 个{NONE}' if w['unlinked'] else '') + '。']
    for it in w['items']:
        name = {'requirement': '需求', 'fix': '修复'}[it['kind']]
        word = {'requirement': {'draft': '立项中', 'open': '进行中', 'done': '已完成', 'dropped': '已取消'}, 'fix': {'open': '修复中', 'done': '已解决'}}[it['kind']].get(it['state'], it['state'])
        lines += ['', f'■ {name} {it["id"]} {it["title"]}（{word}）']
        for c in it['commits']:
            lines.append(f'  提交 {c["short"]} {c["date"]} {c["subject"]}' + (f'（第 {"、".join(map(str, c["lines"][:8]))} 行）' if c['lines'] else '') + ('' if c['how'] == 'trailer' else '（按合并提交认的）'))
            lines += commit_steps_lines(c.get('turn'))
        if it['kind'] == 'requirement':
            for head, key in (('需求说明', '说明'), ('验收条件', '验收条件'), ('它记下的裁定', '裁定')):
                if it[key]:
                    lines.append(f'  {head}：')
                    lines += [f'    - {x}' for x in it[key]]
        else:
            for one in it['issues']:
                lines.append(f'  问题 {one["num"]}「{one["title"]}」')
                for head in ('现象', '修复报告'):
                    if one[head]:
                        lines.append(f'    {head}：')
                        lines += [f'      - {x}' for x in one[head]]
        for s in it['sessions']:
            lines.append(f'  会话 {s["tag"]}' + (f'「{s["title"]}」' if s['title'] else '') + (f'：进展文件 {s["path"]}' if s['path'] else '（没有进展文件）'))
        lines.append(f'  {name}记录：{it["path"]}')
    if w['items_more']:
        lines += ['', f'…另有 {w["items_more"]} 个需求或修复没有展开。']
    if w['unlinked']:
        lines += ['', f'■ {NONE}的提交：']
        for c in w['unlinked']:
            lines.append(f'  {c["short"]} {c["date"]} {c["subject"]}')
            lines += commit_steps_lines(c.get('turn'))
    if not w['items'] and not w['unlinked']:
        lines += ['', '没有找到改过它的提交（可能还没提交）。']
    return '\n'.join(lines)
