"""立项会话里保存和跳过设计稿（rf.py design save / skip）。"""

import datetime as dt
import re
import secrets
from pathlib import Path

from ..core.common import DIR, main_checkout, slug, today
from ..core.documents import add_line, live_items, write_doc
from .designs import DESIGN_HEADS, check_design, design_files, design_view
from .facts import fact_index
from ..requirements.owners import own_requirement


def own_draft_requirement(root):
    mine = own_requirement(root)
    if not mine:
        raise ValueError('这个目录不属于任何需求。设计稿要在需求自己的工作目录里画。')
    if (mine[1].get('state') or 'open') in ('done', 'dropped'):
        raise ValueError('这个需求已经结束，不能再改它的设计稿。')
    return mine


def design_save(root, html, title, features, ident=None, note=None, source=''):
    """Save a design draft for the requirement of this checkout, or revise one it already has (same number, or same title).
    The draft goes into the main checkout's designs/, like every fact; it is committed with the others at the next moment."""
    req_path, req_meta, req_sections = own_draft_requirement(root)
    root = main_checkout(root)
    title = ' '.join((title or '').split())
    if not title:
        raise ValueError('设计稿要有一个标题：--title "…"。')
    known = fact_index(root)['features']
    ids, new = [], []
    for raw in features or []:
        text = ' '.join(str(raw).split())
        m = re.fullmatch(r'(RF-\d+)(?:\s.*)?', text)
        if m:
            if m.group(1) not in known:
                raise ValueError(f'{m.group(1)} 在事实里不存在。画现有的功能点写它的编号；还没有编号的新功能点写 --feature "新增：名称"。')
            ids.append(m.group(1))
        elif re.match(r'新增\s*[：:]', text) and re.sub(r'^新增\s*[：:]\s*', '', text):
            new.append(re.sub(r'^新增\s*[：:]\s*', '', text))
        elif text:
            raise ValueError(f'「{text}」不是功能点：写现有的编号（如 RF-101），或者「新增：名称」。')
    page = check_design(html)
    mine = [item for item in design_files(root) if item[0].get('requirement') == req_meta['id']]
    if ident:
        hit = next((item for item in mine if item[0]['id'] == ident), None)
        if not hit:
            other = next((item for item in design_files(root, ('designs', 'history')) if item[0]['id'] == ident), None)
            raise ValueError(f'{ident} 不是这个需求画的设计稿，不能直接改。要从它改起：先读它的 HTML 文件，改好以后不带 --id 保存成这个需求自己的一份。'
                             if other else f'没有找到设计稿 {ident}。')
    else:
        hit = next((item for item in mine if item[0].get('title') == title), None)
    if not ids and not new:
        if not hit:
            raise ValueError('要说明这份设计稿画的是哪个功能点：--feature RF-101，新功能点写 --feature "新增：名称"。')
        ids, new = [str(x) for x in hit[0].get('features') or []], live_items(hit[1].get('新增的功能点'))  # a revision keeps what it covers
    stamp = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    mark = f'〔{source}〕' if source else ''
    folder = root / DIR / 'designs'
    folder.mkdir(parents=True, exist_ok=True)
    if hit:
        meta, sections, old_path, page_path = hit
        meta.update(title=title, features=list(dict.fromkeys(ids)), updated=today())
        add_line(sections, '变更记录', f'- {stamp} 修改。{mark}')
    else:
        taken = {item[0]['id'] for item in design_files(root, ('designs', 'history'))}
        ident = next(x for x in (f'DS-{dt.datetime.now():%Y%m%d}-{secrets.token_hex(2)}' for _ in range(100)) if x not in taken)
        meta = {'id': ident, 'type': 'design', 'title': title, 'status': 'draft', 'requirement': req_meta['id'], 'features': list(dict.fromkeys(ids)),
                'html': f'{ident}.html', 'adopted': None, 'supersedes': [], 'superseded_by': None, 'updated': today()}
        sections = {h: [] for h in DESIGN_HEADS}
        add_line(sections, '变更记录', f'- {stamp} 新画，属于需求 {req_meta["id"]}。{mark}')
        old_path, page_path = None, folder / f'{ident}.html'
    sections['新增的功能点'] = [f'- {x}{mark}' for x in dict.fromkeys(new)]
    if note and note.strip():
        sections['画的是什么'] = [f'- {" ".join(note.split())}{mark}']
    path = folder / f'{meta["id"]}-{slug(title)[:30]}.md'
    page_path.write_text(page, encoding='utf-8')
    write_doc(path, meta, {h: sections.get(h, []) for h in DESIGN_HEADS})
    if old_path and old_path != path:
        old_path.unlink()
    if req_meta.get('design_skipped'):  # drawing after all: the skip no longer holds
        req_meta.update(design_skipped=None, updated=today())
        add_line(req_sections, '变更记录', f'- {today()} 方案改为画设计稿，不再跳过。{mark}')
        write_doc(req_path, req_meta, req_sections)
    view = design_view(root, (meta, sections, path, page_path))
    return dict(view, created=not hit)


def design_skip(root, reason, source=''):
    """Say why this requirement needs no design draft. The ladder shows 方案 as skipped, with the reason."""
    req_path, meta, sections = own_draft_requirement(root)
    root = main_checkout(root)
    reason = ' '.join((reason or '').split()).replace(' #', ' ＃')
    if not reason:
        raise ValueError('跳过方案要写理由，例如："只改导出的文件格式，没有界面上的变化"。')
    if any(item[0].get('requirement') == meta['id'] for item in design_files(root)):
        raise ValueError('这个需求已经有设计稿了，不用跳过方案。')
    meta.update(design_skipped=reason, updated=today())
    add_line(sections, '变更记录', f'- {today()} 跳过方案：{reason}' + (f'〔{source}〕' if source else ''))
    write_doc(req_path, meta, sections)
    return reason
