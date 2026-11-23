"""知识保鲜：功能点的描述跟上代码（设计见 docs/design/知识保鲜-设计.md）。

功能点只在需求流程里更新；修复、直接在主分支上的提交、你自己改的代码都可能让「这个功能点现在是什么样」和代码对不上，
而立项、开发、合并闸门都把功能点当依据。这里做三件事：

1. 功能点对应哪些代码（feature_files）：改过这个功能点的已完成需求、修复改过的文件，按次数加权（一个需求改了几个功能点，
   它的文件分给每个功能点的权重就小一些），只算现在还在、不是测试的文件。不用人维护。
2. 可能过时（drift）：从「上次核对到的提交」（功能点文件头 checked_at；没有就用最近一次改过它的需求或修复的合并提交，
   再没有就是功能点文件建出来的那个提交）往后，主分支上改到它对应文件的提交里，不属于「要改的功能点」里有它的需求或修复的。
   只改了注释、空白和测试文件的提交不算（用户定的：宽一点）。
3. 核对：只在你点的时候开一个只读的核对会话（check:功能点编号），它读这些提交和功能点的描述，交回建议（propose）；
   建议存在 .relayforge/fresh/RF-…json，你逐条采纳（写进功能点，带出处）或忽略；都处理完了点「核对完」，checked_at 记成核对时的提交。
"""
import datetime as dt
import json
import re
from pathlib import Path

from ..core.common import DIR, PLACEHOLDER, REPLACED, commit_facts, git, main_checkout, run_git, today
from ..core.documents import add_line, read_doc, write_doc
from ..core.identity import caller
from ..facts.mapfacts import load_journeys
from ..requirements.owners import all_fixes, owner_features
from ..requirements.record import load_requirements
from ..sessions.record import stable_tag
from ..sessions.trace import Owners, item_files

__all__ = ['feature_files', 'outside_scope', 'fresh_drift', 'fresh_brief', 'fresh_propose', 'fresh_show', 'fresh_adopt', 'fresh_ignore', 'fresh_done',
           'is_test_path', 'SECTIONS']

TEST_PATH = re.compile(r'(^|/)(tests?|__tests__|specs?|testdata|fixtures)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$|Tests?\.java$')
COMMENT = re.compile(r'^(#|//|/\*|\*|\*/|--|<!--|-->|"""|\'\'\')')
SECTIONS = ('一句话定位', '在模块中的角色', '在产品中的位置', '规则与边界', '还没定的事', '调整时要注意')  # 核对会话能提建议的小节
MAX_FILES = 200


def is_test_path(path):
    return bool(TEST_PATH.search(str(path)))


def _features(home):
    """{id: (path, meta, sections)} of the current features."""
    out = {}
    for path in sorted((Path(home) / DIR / 'features').glob('RF-*.md')):
        meta, sections = read_doc(path)
        if meta.get('id') and meta.get('status') != 'superseded':
            out[str(meta['id'])] = (path, meta, sections)
    return out


def _done_owners(home):
    """The finished requirements and merged fixes: [(id, merge commit)]."""
    merged = lambda f: read_doc(Path(home) / f)[0].get('merge_commit') if f and (Path(home) / f).is_file() else None  # 存档了的找不到就不用
    out = [(r['id'], merged(r.get('file'))) for r in load_requirements(home) if r.get('state') == 'done']
    out += [(f['id'], f.get('merge_commit')) for f in all_fixes(home).values() if f.get('state') == 'merged']
    return out


def _cache_path(home):
    common = git(home, 'rev-parse', '--git-common-dir').strip()
    return (Path(home) / common / 'relayforge-feature-files.json') if common else None


def feature_files(home, owners=None):
    """{feature: {path: weight}} and {feature: [merge commits of the work that changed it]} from finished requirements and fixes.
    What a finished piece of work changed does not change, so it is cached per (id, merge commit) in the shared .git."""
    home = main_checkout(home)
    files, anchors = {}, {}
    cache_file = _cache_path(home)
    try:
        cache = json.loads(cache_file.read_text(encoding='utf-8')) if cache_file else {}
    except (OSError, ValueError):
        cache = {}
    dirty = False
    for ident, merge in owners if owners is not None else _done_owners(home):
        feats = [f for f in owner_features(home, ident) if f]
        if not feats:
            continue
        hit = cache.get(ident)
        if merge and hit and hit.get('merge') == merge:
            touched = hit['files']
        else:
            touched = list(item_files(home, ident))
            if merge:
                cache[ident] = {'merge': merge, 'files': touched}
                dirty = True
        changed = [p for p in touched if not p.startswith(DIR + '/') and not is_test_path(p) and (home / p).exists()]
        weight = 1.0 / len(feats)
        for f in feats:
            box = files.setdefault(f, {})
            for p in changed:
                box[p] = round(box.get(p, 0) + weight, 3)
            if merge:
                anchors.setdefault(f, []).append(merge)
    if dirty and cache_file:
        try:
            cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass  # 存不下就下次再算
    return files, anchors


def outside_scope(home, declared, changed, mapping=None):
    """Which of the changed files belong to features the work did not say it would change:
    [{feature, files}] — a file that maps to one of the declared features (or to none at all) does not count."""
    mapping = mapping if mapping is not None else feature_files(home)[0]
    declared = set(declared)
    out = {}
    for path in changed:
        if is_test_path(path) or path.startswith(DIR + '/'):
            continue
        owners = [f for f, box in mapping.items() if path in box]
        if not owners or declared & set(owners):
            continue
        for f in owners:
            out.setdefault(f, []).append(path)
    return [{'feature': f, 'files': sorted(v)} for f, v in sorted(out.items())]


def _newest(home, commits):
    """The newest of these commits that HEAD contains."""
    best, when = None, -1
    for c in commits:
        if run_git(home, 'merge-base', '--is-ancestor', c, 'HEAD').returncode != 0:
            continue
        t = git(home, 'log', '-1', '--format=%ct', c).strip()
        if t.isdigit() and int(t) > when:
            best, when = c, int(t)
    return best


def _base(home, path, meta, anchors):
    """Where the last check (or the last work on the feature) left off: a commit on the main line."""
    checked = str(meta.get('checked_at') or '').strip()
    if checked and git(home, 'rev-parse', '--verify', '-q', checked + '^{commit}').strip():
        return checked, 'checked'
    newest = _newest(home, anchors or [])
    if newest:
        return newest, 'work'
    # 没核对过、也没有完成的工作改过它：从功能点建出来时算（数据外置的项目功能点不在 Git 里，找不到就不判断）
    added = git(home, 'log', '--diff-filter=A', '--format=%H', '-1', '--', str(path.relative_to(home))).strip()
    return (added or None), 'created'


def _meaningful(home, commit, paths):
    """Whether the commit changed more than comments, blank lines and whitespace in these (non-test) files."""
    diff = git(home, 'show', '-w', '--format=', '--unified=0', commit, '--', *paths)
    for line in diff.splitlines():
        if not line or line.startswith(('+++', '---', '@@', 'diff ', 'index ', 'new file', 'deleted file', 'similarity', 'rename')):
            continue
        if line[0] not in '+-':
            continue
        body = line[1:].strip()
        if body and not COMMENT.match(body):
            return True
    return False


def fresh_drift(root, ident=None):
    """Which features may be out of date: [{id, title, base, base_kind, mapped, stale, unmapped, commits: [{hash, short, subject, owner, files}]}]."""
    home = main_checkout(root)
    feats = _features(home)
    want = {str(ident).upper()} if ident else None
    if want and not want & set(feats):
        raise ValueError(f'没有找到功能点 {ident}。')
    mapping, anchors = feature_files(home)
    owners = Owners(home)
    touched = {}  # 需求或修复 -> 它要改的功能点（缓存）
    out = []
    for fid, (path, meta, _) in feats.items():
        if want and fid not in want:
            continue
        mapped = sorted(mapping.get(fid, {}), key=lambda p: -mapping[fid][p])[:MAX_FILES]
        row = {'id': fid, 'title': str(meta.get('title') or ''), 'mapped': len(mapped), 'unmapped': not mapped, 'stale': False, 'commits': [],
               'base': None, 'base_kind': None, 'checked_at': meta.get('checked_at')}
        base, kind = _base(home, path, meta, anchors.get(fid)) if mapped else (None, None)
        row.update(base=base, base_kind=kind)
        if mapped and base:
            spec = [f'{base}..HEAD']
            for commit in git(home, 'log', '--format=%H', '--no-merges', *spec, '--', *mapped).split():
                found = owners.of(commit)
                if found:
                    if found[1] not in touched:
                        touched[found[1]] = set(owner_features(home, found[1]))
                    if fid in touched[found[1]]:
                        continue  # 改它的需求或修复自己做的：完成时功能点已经跟着更新
                files = [p for p in git(home, 'show', '--name-only', '--format=', commit, '--', *mapped).split('\n') if p and not is_test_path(p)]
                if not files or not _meaningful(home, commit, files):
                    continue
                short, subject = git(home, 'log', '-1', '--format=%h%x09%s', commit).strip().split('\t', 1)
                row['commits'].append({'hash': commit, 'short': short, 'subject': subject, 'owner': found[1] if found else None, 'files': files[:10]})
            row['stale'] = bool(row['commits'])
        out.append(row)
    return out


def _proposal_path(home, fid):
    return Path(home) / DIR / 'fresh' / f'{fid}.json'


def _load(home, fid):
    try:
        data = json.loads(_proposal_path(home, fid).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save(home, fid, data):
    path = _proposal_path(home, fid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def fresh_brief(root, ident):
    """What a check session gets: the feature as written, its journeys, and the commits since the last check with their diffs (capped)."""
    home = main_checkout(root)
    fid = str(ident or '').upper()
    feats = _features(home)
    if fid not in feats:
        raise ValueError(f'没有找到功能点 {ident}。')
    path, meta, sections = feats[fid]
    row = fresh_drift(home, fid)[0]
    lines = [f'功能点 {fid}「{meta.get("title") or ""}」（{path.relative_to(home)}）现在写的：']
    for head in SECTIONS:
        items = [l for l in sections.get(head, []) if l != PLACEHOLDER and REPLACED not in l]
        lines.append(f'## {head}')
        lines += items or ['（没写）']
    journeys = [j for j in load_journeys(home) if any(fid in s['features'] for s in j['steps'])]
    if journeys:
        lines.append('用到它的用户旅程：' + '；'.join(f'{j["id"]} {j.get("title") or ""}' for j in journeys))
    if row['unmapped']:
        lines.append('还没有已完成的需求或修复改过它，对应不上具体的代码文件：自己在代码里找它的实现，读完再判断描述对不对。')
    elif not row['commits']:
        lines.append(f'上次核对（{(row["base"] or "")[:8]}）以后，没有不属于它的需求改过它对应的代码。')
    else:
        lines.append(f'上次核对（{(row["base"] or "")[:8]}）以后，这些提交改了它对应的代码，但不是为了改它（看这些改动有没有让上面的描述不对了）：')
        budget = 12000
        for c in row['commits']:
            lines.append(f'- {c["short"]} {c["subject"]}' + (f'（属于 {c["owner"]}）' if c['owner'] else '（直接提交）') + '：' + '、'.join(c['files']))
            diff = git(home, 'show', '-w', '--format=', '--unified=3', c['hash'], '--', *c['files'])
            if budget > 0:
                lines.append(diff[:min(budget, 4000)])
                budget -= 4000
    return '\n'.join(lines)


def fresh_propose(root, ident, payload, session=None):
    """The check session hands back what it found: {verdict: changed|unchanged, summary, items: [{section, op: replace|add|remove, old, new, why, evidence}]}.
    Only the check session opened for this feature may do it (checked through its signed session token)."""
    home = main_checkout(root)
    fid = str(ident or '').upper()
    feats = _features(home)
    if fid not in feats:
        raise ValueError(f'没有找到功能点 {ident}。')
    me = caller()
    if not (me['verified'] and me['kind'] == 'check' and str(me['owner'] or '').upper() == fid):
        raise ValueError(f'只有为 {fid} 开的核对会话能交回核对的建议。' + ('' if me['verified'] else f'（{me["why"]}）'))
    if not isinstance(payload, dict) or payload.get('verdict') not in ('changed', 'unchanged'):
        raise ValueError('交回的内容要写 verdict：changed（描述要改）或 unchanged（没有变化），changed 时 items 里逐条写建议。')
    items = []
    for raw in payload.get('items') or []:
        if not isinstance(raw, dict):
            continue
        section, op = str(raw.get('section') or ''), str(raw.get('op') or '')
        if section not in SECTIONS or op not in ('replace', 'add', 'remove'):
            raise ValueError(f'每条建议要写 section（{"、".join(SECTIONS)} 之一）和 op（replace、add、remove）。')
        old, new, why = (' '.join(str(raw.get(k) or '').split()) for k in ('old', 'new', 'why'))
        if op in ('replace', 'remove') and not old:
            raise ValueError('replace 和 remove 要写 old：原来的那句（照功能点里写的抄）。')
        if op in ('replace', 'add') and not new:
            raise ValueError('replace 和 add 要写 new：改成什么。')
        if not why:
            raise ValueError('每条建议都要写 why：为什么（哪个提交改了什么）。')
        items.append({'k': len(items) + 1, 'section': section, 'op': op, 'old': old, 'new': new, 'why': why,
                      'evidence': ' '.join(str(raw.get('evidence') or '').split()), 'state': 'pending'})
    if payload['verdict'] == 'changed' and not items:
        raise ValueError('verdict 是 changed 时 items 里至少写一条建议。')
    sid = me['session'] or session or ''
    data = {'feature': fid, 'head': git(home, 'rev-parse', 'HEAD').strip(), 'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
            'session': sid, 'tag': stable_tag(home, sid) if sid else '', 'verdict': payload['verdict'],
            'summary': ' '.join(str(payload.get('summary') or '').split()), 'items': items}
    _save(home, fid, data)
    commit_facts(home, f'功能点 {fid}：核对会话交回了建议')
    return data


def fresh_show(root, ident=None):
    """The check results waiting for the user: one feature, or all of them."""
    home = main_checkout(root)
    if ident:
        return _load(home, str(ident).upper())
    return [d for d in (_load(home, p.stem) for p in sorted((home / DIR / 'fresh').glob('RF-*.json'))) if d]


def _pick(data, ks):
    want = {int(k) for k in ks} if ks else None
    return [i for i in data['items'] if i['state'] == 'pending' and (want is None or i['k'] in want)]


def fresh_adopt(root, ident, ks=()):
    """Only the user: write the chosen suggestions into the feature, each with the check session as its source."""
    home = main_checkout(root)
    fid = str(ident or '').upper()
    data = _load(home, fid)
    feats = _features(home)
    if not data or fid not in feats:
        raise ValueError(f'{fid} 没有等你处理的核对建议。')
    path, meta, sections = feats[fid]
    src = (data.get('tag') or fid) + '#核对'
    done = []
    for item in _pick(data, ks):
        lines = [l for l in sections.get(item['section'], []) if l != PLACEHOLDER]
        if item['op'] in ('replace', 'remove'):
            hit = next((i for i, l in enumerate(lines) if REPLACED not in l and item['old'] in l), None)
            if hit is None:
                raise ValueError(f'第 {item["k"]} 条要改的那句在「{item["section"]}」里找不到了（功能点可能已经改过），忽略它，或者重新核对。')
            lines[hit] = lines[hit].replace('- ', '- ' + REPLACED, 1)
        if item['op'] in ('replace', 'add'):
            lines.append(f'- {item["new"]}〔{src}〕')
        sections[item['section']] = lines or [PLACEHOLDER]
        item['state'] = 'adopted'
        done.append(item)
    if done:
        add_line(sections, '变更记录', f'- {today()} 核对代码改动后采纳了 {len(done)} 条建议（' + '；'.join(f'{i["section"]}：{i["new"] or "删去「" + i["old"] + "」"}' for i in done)[:300] + '）。〔' + src + '〕')
        meta['updated'] = today()
        write_doc(path, meta, sections)
    _save(home, fid, data)
    commit_facts(home, f'功能点 {fid}：采纳了核对的建议')
    return data


def fresh_ignore(root, ident, ks=()):
    """Only the user: drop the chosen suggestions."""
    home = main_checkout(root)
    fid = str(ident or '').upper()
    data = _load(home, fid)
    if not data:
        raise ValueError(f'{fid} 没有等你处理的核对建议。')
    for item in _pick(data, ks):
        item['state'] = 'ignored'
    _save(home, fid, data)
    commit_facts(home, f'功能点 {fid}：忽略了核对的建议')
    return data


def fresh_done(root, ident):
    """Only the user: the check is over. The feature's checked_at becomes the commit the check looked at, so the same commits
    no longer make it look out of date; the suggestions file goes away (adopted ones are in the feature, with their source)."""
    home = main_checkout(root)
    fid = str(ident or '').upper()
    data = _load(home, fid)
    feats = _features(home)
    if not data or fid not in feats:
        raise ValueError(f'{fid} 还没有核对结果。先点「核对」，等核对会话交回。')
    left = [i['k'] for i in data['items'] if i['state'] == 'pending']
    if left:
        raise ValueError(f'还有 {len(left)} 条建议没处理（第 {"、".join(map(str, left))} 条），逐条采纳或忽略以后再点「核对完」。')
    path, meta, sections = feats[fid]
    meta['checked_at'] = data['head']
    add_line(sections, '变更记录', f'- {today()} 核对到 {data["head"][:8]}：' + ('描述没有变化。' if data['verdict'] == 'unchanged' else '处理完了核对的建议。')
             + f'〔{(data.get("tag") or fid)}#核对〕')
    write_doc(path, meta, sections)
    _proposal_path(home, fid).unlink(missing_ok=True)
    commit_facts(home, f'功能点 {fid}：核对完')
    return {'id': fid, 'checked_at': data['head']}
