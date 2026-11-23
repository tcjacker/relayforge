"""设计稿：一份设计稿是一个说明文件加一个同编号的 HTML 页面。这里是读、检查和需求完成时收进主线。"""

import datetime as dt
import re
from pathlib import Path

from ..core.common import DIR, today
from ..core.documents import add_line, first_item, live_items, read_doc, write_doc


# designs/DS-20261114-a3f1-名称.md   the facts: which requirement drew it, which features it shows, status, history
# designs/DS-20261114-a3f1.html      the page itself; its name never changes, so a new title only renames the .md
# While its requirement is open the draft has status draft. When the user completes the requirement it becomes current
# and shows what its features look like now. For each feature the current design is the newest current one that covers
# it; an older design all of whose features are covered by newer ones is moved to history/ with superseded_by.

DESIGN_HEADS = ['画的是什么', '新增的功能点', '变更记录']
# How the local server hands a design page out: nothing from outside, and in a sandbox of its own even when opened directly
DESIGN_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; sandbox allow-scripts"
DESIGN_LIMIT = 2_000_000
DESIGN_NAME = re.compile(r'DS-\d{8}-[0-9a-f]{4}')
EXTERNAL = re.compile(r'''(?:\b(?:src|href|action|formaction|poster|srcset|data|background|xlink:href)\s*=\s*["']?\s*'''
                      r'''|\burl\s*(?:\(\s*["']?|=)\s*|@import\s+["']?\s*'''
                      r'''|\b(?:fetch|import|open|sendBeacon|EventSource|WebSocket|Worker)\s*\(\s*["'`]\s*)(?:https?:|ftp:|wss?:|//)''', re.I)
LOCAL_FILE = re.compile(r'''<(?:script|img|iframe|frame|link|source|video|audio|track|embed|object|image|input)\b[^>]*?\s'''
                        r'''(?:src|href|xlink:href|data|poster)\s*=\s*["']?(?!data:|#|blob:|about:|javascript:|["'\s>])([^"'\s>]+)''', re.I)


def design_files(place, folders=('designs',)):
    """The design drafts of one checkout, as (meta, sections, fact file, html file)."""
    out = []
    for folder in folders:
        for path in sorted((Path(place) / DIR / folder).glob('DS-*.md')):
            meta, sections = read_doc(path)
            if meta.get('type') == 'design' and meta.get('id'):
                out.append((meta, sections, path, path.parent / str(meta.get('html') or f'{meta["id"]}.html')))
    return out


def design_view(place, item):
    meta, sections, path, html = item
    try:
        stamp = html.stat().st_mtime
    except OSError:
        stamp = 0
    rel = lambda p: p.relative_to(Path(place)).as_posix()  # noqa: E731
    return {'id': meta['id'], 'title': meta.get('title') or '', 'status': meta.get('status') or '', 'requirement': meta.get('requirement') or '',
            'features': [str(x) for x in meta.get('features') or []], 'new': live_items(sections.get('新增的功能点')),
            'note': first_item(sections, '画的是什么'), 'updated': str(meta.get('updated') or ''), 'adopted': str(meta.get('adopted') or ''),
            'file': rel(path), 'html': rel(html), 'path': str(html), 'mtime': stamp,
            'changed': dt.datetime.fromtimestamp(stamp).strftime('%Y-%m-%d %H:%M') if stamp else ''}


def current_designs(place):
    """For each feature, the design that shows what it looks like now: the newest current design that covers it."""
    best = {}
    for item in design_files(place):
        meta = item[0]
        if meta.get('status') != 'current':
            continue
        view = design_view(place, item)
        for feature in view['features']:
            old = best.get(feature)
            if not old or (view['adopted'], view['updated'], view['id']) > (old['adopted'], old['updated'], old['id']):
                best[feature] = view
    return best


def check_design(html):
    """A design draft is one page that opens offline: no addresses outside it, no other files next to it."""
    text = (html or '').strip()
    if not text:
        raise ValueError('设计稿是空的。把整个 HTML 从标准输入给进来（或者用 --file 指定文件）。')
    if len(text.encode('utf-8')) > DESIGN_LIMIT:
        raise ValueError(f'设计稿太大了（超过 {DESIGN_LIMIT // 1000000} MB）。图片用简单的内联 SVG，不要嵌大图。')
    if not re.search(r'<\s*(?:!doctype|html|head|body|div|main|section|header|svg|table|style|form|ul)\b', text, re.I):
        raise ValueError('这不像一份 HTML：至少要有页面的标签。')
    found = [text[max(0, m.start() - 10):m.end() + 30].replace('\n', ' ') for m in EXTERNAL.finditer(text)]
    if found:
        raise ValueError('设计稿要能断网打开，不能引用外部网址：' + '；'.join(f'「…{x}…」' for x in found[:3]) +
                         '。样式和脚本直接写在文件里，字体用系统字体，图片用内联 SVG 或 data:，链接写成 #。')
    local = [m.group(1) for m in LOCAL_FILE.finditer(text)]
    if local:
        raise ValueError('设计稿要是一个独立的文件，不能引用别的文件：' + '、'.join(local[:3]) + '。把内容直接写进这个文件里。')
    return text + '\n'


def adopt_designs(place, ident, added=()):
    """The requirement is complete: its drafts become what their features look like now.

    `added` holds (id, title) of the features the requirement created, so that a draft drawn for "新增：名称" is tied to the
    feature that now carries that name. A current design that is no longer the newest for any of its features moves to
    history/, superseded by the design that took its place."""
    place = Path(place)
    mine, retired, others = [], [], []
    for item in design_files(place):
        (mine if item[0].get('requirement') == ident else others).append(item)
    now = dt.datetime.now().replace(microsecond=0)
    for meta, _, _, _ in others:  # newer always sorts after older, even within the same second
        try:
            now = max(now, dt.datetime.strptime(str(meta.get('adopted') or ''), '%Y-%m-%d %H:%M:%S') + dt.timedelta(seconds=1))
        except ValueError:
            pass
    stamp = now.strftime('%Y-%m-%d %H:%M:%S')
    for meta, sections, path, page in mine:
        features = [str(x) for x in meta.get('features') or []]
        for name in live_items(sections.get('新增的功能点')):
            bare = re.sub(r'（[^）]*）$', '', name).strip()
            hit = next((fid for fid, title in added if title and (title == bare or title in bare or bare in title)), None)
            if hit and hit not in features:
                features.append(hit)
        meta.update(status='current', features=features, adopted=stamp, updated=today())
        add_line(sections, '变更记录', f'- {today()} 需求 {ident} 完成，成为' + ('、'.join(features) or '它画的功能点') + '现在的设计稿。')
        write_doc(path, meta, sections)
    newest = current_designs(place)  # feature -> the design that shows it now, mine included
    showing = {v['id'] for v in newest.values()}
    for meta, sections, path, page in others:
        features = [str(x) for x in meta.get('features') or []]
        if meta.get('status') != 'current' or not features or meta['id'] in showing:
            continue  # still what at least one feature looks like now
        by = next((newest[f]['id'] for f in features if newest.get(f, {}).get('requirement') == ident), newest[features[-1]]['id'])
        meta.update(status='superseded', superseded_by=by, updated=today())
        add_line(sections, '变更记录', f'- {today()} 被 {by} 替代（需求 {ident}）。')
        target = place / DIR / 'history'
        target.mkdir(parents=True, exist_ok=True)
        write_doc(target / path.name, meta, sections)
        path.unlink()
        if page.exists():
            page.replace(target / page.name)
        retired.append({'id': meta['id'], 'title': meta.get('title') or '', 'by': by})
    return [design_view(place, item) for item in design_files(place) if item[0].get('requirement') == ident], retired
