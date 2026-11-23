"""进行中的事实：还没确认完成的需求带来的功能点、模块、用户旅程和裁定。

事实只在项目主目录里有一份，需求在开发时就写进去了，但代码还没合并进来，所以要标明是哪个进行中的需求带来的：
- 整个对象是需求新加的：文件头 in_progress 写需求编号。不算现行，页面上显示「进行中，尚未完成」。
- 改一个现有的对象：新的陈述行首加〔进行中 R-…〕，要被它换掉的旧陈述行首加〔R-… 完成后替代〕（旧行原样留着，照旧算数），
  文件头 changing 列出正在改它的需求。
确认完成（promote）：去掉 in_progress 和〔进行中 R-…〕，旧行改成〔已替代〕，在变更记录里记一笔。
放弃（shelve）：新加的对象移到 history/，文件头 dropped_with 写需求编号；新陈述改成〔已放弃 R-…〕，旧行上的标记改成
〔R-… 放弃前要替代〕（不再算要替代）。恢复（unshelve）把这些都改回来。设计稿本来就是这样：draft 是进行中，完成时成为现行。
"""

from pathlib import Path

from ..core.common import DIR, DROPPED_LINE, IN_PROGRESS, REPLACED, TO_REPLACE, today
from ..core.documents import add_line, read_doc, write_doc
from .designs import design_files

FOLDERS = ('modules', 'features', 'journeys', 'decisions')
NAMES = {'modules': '模块', 'features': '功能点', 'journeys': '用户旅程', 'decisions': '裁定'}


def _changing(meta):
    return [str(x) for x in meta.get('changing') or []]


def _touches(meta, sections, ident):
    if meta.get('in_progress') == ident or ident in _changing(meta):
        return True
    return any(f'〔进行中 {ident}〕' in l or f'〔{ident} 完成后替代〕' in l for lines in sections.values() for l in lines)


def brought_by(home, ident):
    """What a requirement brought into the main checkout: [(kind, path, meta, sections, 'new' | 'changed')]."""
    out = []
    for kind in FOLDERS:
        for path in sorted((Path(home) / DIR / kind).glob('*.md')):
            meta, sections = read_doc(path)
            if meta.get('id') and _touches(meta, sections, ident):
                out.append((kind, path, meta, sections, 'new' if meta.get('in_progress') == ident else 'changed'))
    return out


def in_progress_of(meta, sections=None):
    """The requirements that have this object in progress (it is new from one, or one is changing it)."""
    ids = [str(meta['in_progress'])] if meta.get('in_progress') else []
    ids += _changing(meta)
    for lines in (sections or {}).values():
        for line in lines:
            ids += IN_PROGRESS.findall(line)
    return list(dict.fromkeys(ids))


def _rewrite(sections, fn):
    for head, lines in sections.items():
        sections[head] = [fn(l) for l in lines]


def promote(home, ident, source):
    """The requirement is done: what it brought becomes current. Returns (added labels, changed labels, new features)."""
    added, changed, new_features = [], [], []
    for kind, path, meta, sections, how in brought_by(home, ident):
        label = f'{NAMES[kind]} {meta["id"]} {meta.get("title")}'
        _rewrite(sections, lambda l: TO_REPLACE.sub(lambda m: REPLACED if m.group(1) == ident else m.group(0),
                                                    l.replace(f'〔进行中 {ident}〕', '')))
        if how == 'new':
            meta.pop('in_progress', None)
            added.append(label)
            if kind == 'features':
                new_features.append((str(meta['id']), str(meta.get('title') or '')))
        else:
            changed.append(label)
        rest = [x for x in _changing(meta) if x != ident]
        if 'changing' in meta:
            meta['changing'] = rest or None
        meta['updated'] = today()
        if '变更记录' in sections:
            add_line(sections, '变更记录', f'- {today()} 随需求 {ident} 确认完成，成为现行。〔{source}〕')
        write_doc(path, meta, sections)
    return added, changed, new_features


def shelve(home, ident, source):
    """The requirement is cancelled: what it brought goes to history (new objects) or stops counting (new statements).
    Its draft designs go to history too. Returns labels of what was put away."""
    home = Path(home)
    done = []
    for kind, path, meta, sections, how in brought_by(home, ident):
        _rewrite(sections, lambda l: l.replace(f'〔进行中 {ident}〕', f'〔已放弃 {ident}〕').replace(f'〔{ident} 完成后替代〕', f'〔{ident} 放弃前要替代〕'))
        meta['updated'] = today()
        if '变更记录' in sections:
            add_line(sections, '变更记录', f'- {today()} 随需求 {ident} 放弃' + ('，移到 history/。' if how == 'new' else '，它带来的改动不再算数。') + f'〔{source}〕')
        if how == 'new':
            meta['dropped_with'] = ident
            write_doc(home / DIR / 'history' / path.name, meta, sections)
            path.unlink()
        else:
            meta['changing'] = [x for x in _changing(meta) if x != ident] or None
            meta['dropped_changes'] = list(dict.fromkeys([str(x) for x in meta.get('dropped_changes') or []] + [ident]))
            write_doc(path, meta, sections)
        done.append(f'{NAMES[kind]} {meta["id"]} {meta.get("title")}')
    for meta, sections, path, page in design_files(home):
        if meta.get('requirement') == ident and meta.get('status') == 'draft':
            meta.update(dropped_with=ident, updated=today())
            (home / DIR / 'history').mkdir(parents=True, exist_ok=True)
            write_doc(home / DIR / 'history' / path.name, meta, sections)
            path.unlink()
            if page.exists():
                page.rename(home / DIR / 'history' / page.name)
            done.append(f'设计稿 {meta["id"]} {meta.get("title")}')
    return done


def unshelve(home, ident, source):
    """The cancelled requirement is restored: bring back what shelve put away."""
    home = Path(home)
    done = []
    for path in sorted((home / DIR / 'history').glob('*.md')):
        meta, sections = read_doc(path)
        if meta.get('dropped_with') != ident:
            continue
        meta.pop('dropped_with', None)
        meta['updated'] = today()
        if meta.get('type') == 'design':
            target_dir = home / DIR / 'designs'
            page = path.parent / str(meta.get('html') or f'{meta["id"]}.html')
            target_dir.mkdir(parents=True, exist_ok=True)
            if page.exists():
                page.rename(target_dir / page.name)
        else:
            folder = {'module': 'modules', 'feature': 'features', 'journey': 'journeys', 'decision': 'decisions'}.get(str(meta.get('type')))
            if not folder:
                continue
            target_dir = home / DIR / folder
        if '变更记录' in sections:
            add_line(sections, '变更记录', f'- {today()} 需求 {ident} 恢复，拿回来，仍是进行中。〔{source}〕')
        write_doc(target_dir / path.name, meta, sections)
        path.unlink()
        done.append(f'{meta.get("id")} {meta.get("title")}')
    for kind in FOLDERS:
        for path in sorted((home / DIR / kind).glob('*.md')):
            meta, sections = read_doc(path)
            if ident not in [str(x) for x in meta.get('dropped_changes') or []]:
                continue
            _rewrite(sections, lambda l: l.replace(f'〔已放弃 {ident}〕', f'〔进行中 {ident}〕').replace(f'〔{ident} 放弃前要替代〕', f'〔{ident} 完成后替代〕'))
            meta['dropped_changes'] = [x for x in meta['dropped_changes'] if str(x) != ident] or None
            meta['changing'] = list(dict.fromkeys(_changing(meta) + [ident]))
            meta['updated'] = today()
            if '变更记录' in sections:
                add_line(sections, '变更记录', f'- {today()} 需求 {ident} 恢复，它的改动又算进行中。〔{source}〕')
            write_doc(path, meta, sections)
            done.append(f'{meta.get("id")} {meta.get("title")}')
    return done


def change_statement(path, ident, head, text, source, replaces=None, confirmed=True):
    """A requirement in progress changes an existing object: add the new statement as 〔进行中 R-…〕 and, when it takes the
    place of an old one, mark that old line 〔R-… 完成后替代〕. Without a requirement the statement is current at once."""
    meta, sections = read_doc(path)
    if head not in sections:
        raise ValueError(f'{meta.get("id")} 没有「{head}」这一节。')
    mark = f'〔进行中 {ident}〕' if ident else ''
    line = f'- {mark}{"" if confirmed else "〔待确认〕"}{text.strip()}〔{source}〕'
    if replaces:
        hits = [i for i, l in enumerate(sections[head]) if replaces in l and REPLACED not in l and not IN_PROGRESS.search(l) and not DROPPED_LINE.search(l)]
        if len(hits) != 1:
            raise ValueError(f'「{replaces}」在「{head}」里' + ('没有找到' if not hits else f'找到 {len(hits)} 行') + '，要换掉的旧陈述要能唯一认出来。')
        old = sections[head][hits[0]]
        sections[head][hits[0]] = (old.replace('- ', f'- 〔{ident} 完成后替代〕', 1) if ident else old.replace('- ', '- ' + REPLACED, 1))
    add_line(sections, head, line)
    if ident and meta.get('in_progress') != ident:
        meta['changing'] = list(dict.fromkeys(_changing(meta) + [ident]))
    meta['updated'] = today()
    write_doc(path, meta, sections)
    return meta



def inflight_delta(modules, features, decisions, ident):
    """Per module, what a requirement still in progress brings, in the shape the map's progress view shows
    (the same as overview.fact_delta): {module: {state, features: [{id, title, state}], newRules, changedRules}}."""
    def how(doc):
        if doc.get('in_progress') == ident:
            return 'new'
        return 'changed' if ident in in_progress_of(doc, doc.get('sections')) else None
    out = {}
    for m in modules:
        feats = [{'id': f['id'], 'title': f.get('title'), 'state': how(f)} for f in features if f.get('module') == m['id'] and how(f)]
        rules = [how(d) for d in decisions if m['id'] in (d.get('applies_to') or []) and how(d)]
        if how(m) or feats or rules:
            out[m['id']] = {'state': how(m), 'features': feats, 'newRules': rules.count('new'), 'changedRules': rules.count('changed')}
    return out

__all__ = ['inflight_delta', 'brought_by', 'in_progress_of', 'promote', 'shelve', 'unshelve', 'change_statement']
