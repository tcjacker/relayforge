"""事实：把抽取出的条目合并进模块、功能点、裁定文件；确认、否决；写出事实摘要 context.md。"""

import json
import re

from ..core.common import (
    CONTEXT_LIMIT, DECISION_HEADS, DIR, FEATURE_HEADS, MODULE_HEADS, PENDING, REJECTED, log, main_checkout, slug, today,
)
from ..core.documents import add_line, all_docs, find_doc, first_item, item_line, next_id, pending_groups, read_doc, write_doc
from .designs import current_designs
from .inflight import in_progress_of
from .page import render


def retire(root, ident, by, source, confirmed):
    """Mark an object as superseded. Unconfirmed proposals only leave a note."""
    found = find_doc(root, ident)
    if not found:
        return False
    path, meta, sections = found
    if not confirmed:
        add_line(sections, '变更记录', item_line(f'{today()} 可能已被{by or "新的内容"}替代。', source, False))
        write_doc(path, meta, sections)
        return True
    meta.update(status='superseded', superseded_by=by, updated=today())
    add_line(sections, '变更记录', f'- {today()} 被{by or "新的内容"}替代。〔{source}〕')
    target = root / DIR / 'history' / path.name
    write_doc(target, meta, sections)
    path.unlink()
    return True


def resolve_module(root, ref, source, confirmed, in_progress=None):
    """Return the id of the module named by `ref`, creating it when it is new."""
    if ref:
        for _, meta, _ in all_docs(root, ('modules',)):
            if ref in (meta['id'], meta.get('title')):
                return meta['id']
    title = ref or '未归类'
    ident = next_id(root, 'modules', 'M', 2, 1)
    order = sum(1 for _ in (root / DIR / 'modules').glob('*.md')) + 1
    meta = {'id': ident, 'type': 'module', 'title': title, 'status': 'current' if confirmed else 'draft', 'order': order, 'updated': today()}
    if in_progress:
        meta['in_progress'] = in_progress
    sections = {h: [] for h in MODULE_HEADS}
    add_line(sections, '变更记录', f'- {today()} 创建。〔{source}〕')
    write_doc(root / DIR / 'modules' / f'{ident}-{slug(title)}.md', meta, sections)
    return ident


def apply_item(root, item, source, in_progress=None):
    """Write one item into the facts. in_progress: the requirement still in progress that brings it (decisions, modules and
    features it creates are marked as its, see inflight.py). Always into the main checkout."""
    root = main_checkout(root)
    confirmed = item.get('stated_by') == 'user'
    kind = item['kind']
    if kind == 'decision':
        statement = item['statement'].strip()
        for _, _, sections in all_docs(root, ('decisions',)):
            if any(statement in line for line in sections.get('决定', [])):
                return 'skip'
        ident = next_id(root, 'decisions', 'D', 4, 1)
        title = (item.get('title') or statement)[:30]
        meta = {'id': ident, 'type': 'decision', 'title': title, 'status': 'current' if confirmed else 'draft',
                'applies_to': item.get('applies_to') or [], 'decided_by': 'user' if confirmed else None,
                'decided_at': today() if confirmed else None, 'supersedes': item.get('supersedes') or [],
                'superseded_by': None, 'updated': today()}
        if in_progress:
            meta['in_progress'] = in_progress
        sections = {h: [] for h in DECISION_HEADS}
        add_line(sections, '决定', item_line(statement, source, confirmed))
        if item.get('reason'):
            add_line(sections, '理由', item_line(item['reason'], source, confirmed))
        add_line(sections, '变更记录', f'- {today()} 记录。〔{source}〕')
        write_doc(root / DIR / 'decisions' / f'{ident}-{slug(title)}.md', meta, sections)
        for old in meta['supersedes']:
            retire(root, old, ident, source, confirmed)  # titles are resolved after the whole batch
        return ident
    if kind == 'supersede':
        return 'retired' if retire(root, item['target'], item.get('by'), source, confirmed) else 'skip'
    if kind == 'open-question':
        line = f'- {item["statement"].strip()}〔{source}〕'
        found = find_doc(root, item.get('target')) if item.get('target') else None
        if found and found[1].get('type') == 'feature':
            path, meta, sections = found
            add_line(sections, '还没定的事', line)
            meta['updated'] = today()
            write_doc(path, meta, sections)
            return meta['id']
        path = root / DIR / 'product' / 'open.md'
        meta, sections = read_doc(path) if path.exists() else ({'id': 'OPEN', 'type': 'doc', 'title': '还没定的事', 'status': 'current', 'updated': today()}, {'还没定的事': []})
        add_line(sections, '还没定的事', line)
        meta['updated'] = today()
        write_doc(path, meta, sections)
        return 'OPEN'
    op = item['op']
    if op == 'add-module':
        return resolve_module(root, item['title'], source, confirmed, in_progress)
    if op == 'add-feature':
        for _, meta, _ in all_docs(root, ('features',)):
            if meta.get('title') == item['title']:
                return 'skip'
        module = resolve_module(root, item.get('module'), source, confirmed, in_progress)
        ident = next_id(root, 'features', 'RF', 3, 101)
        order = sum(1 for _, m, _ in all_docs(root, ('features',)) if m.get('module') == module) + 1
        meta = {'id': ident, 'type': 'feature', 'title': item['title'], 'status': 'current' if confirmed else 'draft',
                'module': module, 'order': order, 'depends_on': [], 'supersedes': [], 'superseded_by': None, 'updated': today()}
        if in_progress:
            meta['in_progress'] = in_progress
        sections = {h: [] for h in FEATURE_HEADS}
        if item.get('one_liner'):
            add_line(sections, '一句话定位', item_line(item['one_liner'], source, confirmed))
        add_line(sections, '变更记录', f'- {today()} 创建。〔{source}〕')
        write_doc(root / DIR / 'features' / f'{ident}-{slug(item["title"])}.md', meta, sections)
        return ident
    found = find_doc(root, item.get('id'))
    if not found:
        return 'skip'
    path, meta, sections = found
    if not confirmed:
        what = f'改名为「{item.get("title")}」' if op == 'rename' else f'移到 {item.get("module")}'
        add_line(sections, '变更记录', item_line(f'{today()} 建议{what}。', source, False))
        write_doc(path, meta, sections)
        return meta['id']
    if op == 'rename':
        add_line(sections, '变更记录', f'- {today()} 由「{meta.get("title")}」改名为「{item["title"]}」。〔{source}〕')
        meta.update(title=item['title'], updated=today())
        target = path.with_name(f'{meta["id"]}-{slug(item["title"])}.md')
        write_doc(target, meta, sections)
        if target != path:
            path.unlink()
    else:
        module = resolve_module(root, item.get('module'), source, True)
        add_line(sections, '变更记录', f'- {today()} 从 {meta.get("module")} 移到 {module}。〔{source}〕')
        meta.update(module=module, updated=today())
        write_doc(path, meta, sections)
    return meta['id']


def merge(root):
    inbox = root / DIR / 'inbox'
    done = inbox / 'merged'
    applied = 0
    for path in sorted(inbox.glob('*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except ValueError:
            log(root, f'unreadable inbox file {path.name}')
            continue
        for item in record.get('items', []):
            if item.get('kind') == 'structure' or (item.get('kind') == 'supersede' and not str(item.get('target') or '').startswith('D-')):
                continue  # everyday sessions do not touch the big picture; older inbox files may still carry such items
            source = f'{record.get("session", "S-unknown")}#{item.get("evidence_at")}'
            try:
                if apply_item(root, item, source) != 'skip':
                    applied += 1
            except Exception as error:  # one bad item must not block the rest
                log(root, f'item failed in {path.name}: {error!r}')
        resolve_titles(root)
        done.mkdir(parents=True, exist_ok=True)
        path.rename(done / path.name)
    write_summaries(root)
    render(root)
    return applied


def resolve_titles(root):
    """Turn `supersedes` entries given as titles into ids and retire what they name."""
    decisions = list(all_docs(root, ('decisions',)))
    by_title = {meta.get('title'): meta['id'] for _, meta, _ in decisions}
    for path, meta, sections in decisions:
        names = [x for x in meta.get('supersedes') or [] if not re.fullmatch(r'[A-Z]+-\d+', str(x))]
        if not names:
            continue
        source = (re.findall(r'〔([^〕]+)〕', ' '.join(sections.get('决定', []))) or ['S-unknown'])[-1]
        kept = [x for x in meta['supersedes'] if x not in names]
        for name in names:
            old = by_title.get(str(name)[:30])
            if old and old != meta['id']:
                kept.append(old)
                retire(root, old, meta['id'], source, meta.get('status') == 'current')
        meta['supersedes'] = kept
        write_doc(path, meta, sections)


def doc_by_id(root, ident):
    found = find_doc(root, ident)
    if found:
        return found
    for path in sorted((root / DIR / 'product').glob('*.md')):
        meta, sections = read_doc(path)
        if meta.get('id') == ident:
            return path, meta, sections
    return None


def confirm(root, ident, source):
    """The user accepts an inferred object: the marks come off, a draft becomes current, a proposal is carried out."""
    found = doc_by_id(root, ident)
    if not found:
        return False
    path, meta, sections = found
    changed, proposals = meta.get('status') == 'draft', []
    for head, lines in sections.items():
        for i, line in enumerate(lines):
            if PENDING in line:
                lines[i], changed = line.replace(PENDING, '', 1), True
                if head == '变更记录':
                    proposals.append(lines[i])
    if not changed:
        return False
    if meta.get('status') == 'draft':
        meta['status'] = 'current'
        if meta.get('type') == 'decision':
            meta.update(decided_by='user', decided_at=today())
    meta['updated'] = today()
    if '变更记录' in sections:
        add_line(sections, '变更记录', f'- {today()} 用户确认。〔{source}〕')
    write_doc(path, meta, sections)
    for line in proposals:
        replaced = re.search(r'可能已被\s*([A-Z]+-\d+)?[^。]*替代', line)
        renamed = re.search(r'建议改名为「(.+?)」', line)
        moved = re.search(r'建议移到 (.+?)。', line)
        if replaced:
            retire(root, ident, replaced.group(1), source, True)
            break
        if renamed:
            apply_item(root, {'kind': 'structure', 'op': 'rename', 'id': ident, 'title': renamed.group(1), 'stated_by': 'user'}, source)
        elif moved:
            apply_item(root, {'kind': 'structure', 'op': 'move', 'id': ident, 'module': moved.group(1), 'stated_by': 'user'}, source)
    return True


def reject(root, ident, source):
    """The user turns an inferred object down. Nothing is deleted: a draft moves to history, lines are marked."""
    found = doc_by_id(root, ident)
    if not found:
        return False
    path, meta, sections = found
    draft = meta.get('status') == 'draft'
    marked = False
    for lines in sections.values():
        for i, line in enumerate(lines):
            if PENDING in line:
                lines[i], marked = line.replace(PENDING, REJECTED, 1), True
    if not draft and not marked:
        return False
    meta['updated'] = today()
    if '变更记录' in sections:
        add_line(sections, '变更记录', f'- {today()} 用户否决。〔{source}〕')
    if draft:
        meta.update(status='superseded', superseded_by=None)
        write_doc(root / DIR / 'history' / path.name, meta, sections)
        path.unlink()
    else:
        write_doc(path, meta, sections)
    return True


def write_summaries(root):
    root = main_checkout(root)
    base = root / DIR
    groups = pending_groups(root)
    asking = sum(len(g['items']) for g in groups)
    lines = ['# 待确认（自动生成，不要手改）', '', '在对话里运行 /rf-confirm 按模块确认；逐个处理用 rf.py confirm 和 rf.py reject。']
    for group in groups:
        lines += ['', f'## {group["title"]}（{len(group["items"])} 个）']
        for item in group['items']:
            lines.append(f'- {item["id"]} {item["title"]}' + ('（整个对象是 AI 推断的）' if item['status'] == 'draft' else ''))
            lines += [f'  - {re.sub(r"〔[^〕]*〕", "", line.lstrip("- ")).strip()}' for line in item['lines']]
    if not groups:
        lines += ['', '- 目前没有。']
    (base / 'pending.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    out = ['# 现行事实摘要（自动生成，不要手改）', '', '事实只在项目主目录的 .relayforge/ 里有一份，下面的路径都相对项目主目录；需求和修复的工作目录里那份是开分支时的旧副本，不作数。',
           '细节按路径去读对应的文件。status 不是 current 的内容、进行中的需求带来的内容（文件头有 in_progress，或行首有〔进行中 R-…〕）不作为依据，除非你就在做那个需求。', '', '## 产品']
    overview = base / 'product' / 'overview.md'
    intro = first_item(read_doc(overview)[1], '产品介绍') if overview.exists() else ''
    out.append(f'- {intro or "还没有产品介绍。"}')
    out += ['', '## 模块与功能点']
    every = sorted((m for _, m, _ in all_docs(root, ('modules',))), key=lambda m: m.get('order') or 0)
    modules = [m for m in every if not m.get('in_progress')]
    features = [f for f in all_docs(root, ('features',)) if not f[1].get('in_progress')]
    for module in modules:
        paths = '、'.join(str(x) for x in module.get('paths') or [])
        mine = [f for f in features if f[1].get('module') == module['id']]
        waiting = sum(1 for f in mine if f[1].get('status') != 'current')
        out.append(f'- {module["id"]} {module.get("title")}' + ('（待确认）' if module.get('status') != 'current' else '') + (f'（代码：{paths}）' if paths else '') + (f'；另有 {waiting} 个待确认的功能点没有列出' if waiting else ''))
        for path, meta, sections in sorted(mine, key=lambda f: f[1].get('order') or 0):
            if meta.get('status') == 'current':
                out.append(f'  - {meta["id"]} {meta.get("title")}：{first_item(sections, "一句话定位") or "尚未讨论"} → {DIR}/features/{path.name}')
    if not modules:
        out.append('- 还没有模块。')
    drawn = current_designs(root)
    if drawn:
        out += ['', '## 功能点现在的设计稿（改这些功能点的界面时从它改起）']
        out += [f'- {fid}：{v["id"]} {v["title"]} → {v["html"]}' for fid, v in sorted(drawn.items())]
    out += ['', '## 现行裁定']
    decisions = [(p, m) for p, m, _ in all_docs(root, ('decisions',)) if m.get('status') == 'current' and not m.get('in_progress')]
    out += [f'- {m["id"]} {m.get("title")} → {DIR}/decisions/{p.name}' for p, m in decisions] or ['- 还没有。']
    going = []
    for kind, word in (('modules', '模块'), ('features', '功能点'), ('journeys', '用户旅程'), ('decisions', '裁定')):
        for path, meta, sections in all_docs(root, (kind,)):
            ids = in_progress_of(meta, sections)
            if ids:
                how = '新加的' if meta.get('in_progress') else '在改'
                going.append(f'- {word} {meta["id"]} {meta.get("title")}：{"、".join(ids)} {how}，尚未完成 → {DIR}/{kind}/{path.name}')
    if going:
        out += ['', '## 进行中，尚未完成（这些需求确认完成后才算现行）'] + going
    out += ['', f'## 待你确认（{asking} 个对象）']
    out += [f'- {group["title"]}：{len(group["items"])} 个' for group in groups] or ['- 目前没有。']
    if groups:
        out.append(f'明细在 {DIR}/pending.md。用户想处理时用 /rf-confirm 按模块确认；没有用户的明确表态，不要替用户确认。')
    text = '\n'.join(out) + '\n'
    if len(text) > CONTEXT_LIMIT:
        text = text[:CONTEXT_LIMIT] + f'\n…（已截断，完整内容见 {DIR}/）\n'
    (base / 'context.md').write_text(text, encoding='utf-8')


def fact_index(place, own=None):
    """Modules, features and current decisions of the main checkout, for grounding a requirement in what exists.
    Decisions another requirement still in progress brings do not govern yet; the requirement `own` sees its own."""
    place = main_checkout(place)
    docs = {'modules': {}, 'features': {}, 'decisions': []}
    for kind in ('modules', 'features'):
        for _, meta, sections in all_docs(place, (kind,)):
            docs[kind][meta['id']] = (meta, sections)
    for _, meta, sections in all_docs(place, ('decisions',)):
        if meta.get('status') == 'current' and meta.get('in_progress') in (None, own):
            docs['decisions'].append((meta, sections))
    docs['designs'] = current_designs(place)
    docs['issues'] = open_issues(place)
    return docs


def open_issues(place):
    """Issues not yet resolved or dismissed, from the main checkout (issues live only there), for the requirement brief."""
    out = []
    for path in sorted((main_checkout(place) / DIR / 'issues').glob('I-*.md')):
        meta = read_doc(path)[0]
        if meta.get('type') == 'issue' and meta.get('id') and meta.get('state') not in ('resolved', 'closed'):
            out.append({'id': meta['id'], 'title': meta.get('title') or '', 'state': meta.get('state') or 'todo', 'kind': meta.get('kind') or None,
                        'feature': meta.get('feature') or None, 'deferred': bool(meta.get('deferred'))})
    return out
