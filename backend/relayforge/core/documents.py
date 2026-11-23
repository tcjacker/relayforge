"""事实文件的读写：YAML 样式的文件头、固定的二级标题、一行一条的列表项；编号和待确认的条目。"""

import re
from pathlib import Path

from .common import DIR, DROPPED_LINE, IN_PROGRESS, PENDING, PLACEHOLDER, REJECTED, REPLACED
from .trust import terminal_note


def parse_value(raw):
    raw = raw.strip()
    if raw in ('null', '~', ''):
        return None
    if raw.startswith('[') and raw.endswith(']'):
        inner = raw[1:-1].strip()
        return [x.strip().strip('"\'') for x in inner.split(',') if x.strip()] if inner else []
    # 只认写回去一字不差的整数：0754688 这样的短提交号转成整数会丢掉前导 0，下次写文件就永远找不回来了
    if re.fullmatch(r'0|-?[1-9][0-9]*', raw):
        return int(raw)
    return raw.strip('"\'')


def format_value(value):
    if value is None:
        return 'null'
    if isinstance(value, list):
        return '[' + ', '.join(str(x) for x in value) + ']'
    return str(value)


def read_doc(path):
    """Return (meta, sections). sections maps heading -> list of lines."""
    return parse_doc(Path(path).read_text(encoding='utf-8'))


def parse_doc(text):
    """Read the same format from a committed Git blob or a file."""
    meta, sections = {}, {}
    body = text
    if text.startswith('---\n'):
        end = text.find('\n---', 4)
        if end >= 0:
            for line in text[4:end].splitlines():
                if ':' in line and not line.lstrip().startswith('#'):
                    key, raw = line.split(':', 1)
                    meta[key.strip()] = parse_value(raw.split(' #')[0])
            body = text[end + 4:]
    head = None
    for line in body.splitlines():
        if line.startswith('## '):
            head = line[3:].strip()
            sections[head] = []
        elif head is not None and line.strip():
            sections[head].append(line.rstrip())
    return meta, sections


def write_doc(path, meta, sections):
    out = ['---'] + [f'{k}: {format_value(v)}' for k, v in meta.items()] + ['---', '']
    for head, lines in sections.items():
        out += [f'## {head}'] + (lines or [PLACEHOLDER]) + ['']
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('\n'.join(out), encoding='utf-8')


def add_line(sections, head, line):
    if head == '变更记录':  # 你在终端里直接运行只由你触发的命令时，变更记录注明（见 trust.py）
        line = terminal_note(line)
    lines = sections.setdefault(head, [])
    if lines == [PLACEHOLDER]:
        lines.clear()
    if line not in lines:
        lines.append(line)


def item_line(statement, source, confirmed):
    text = statement.strip().lstrip('-').strip()
    return f'- {"" if confirmed else PENDING}{text}〔{source}〕'


def all_docs(root, kinds=('modules', 'features', 'decisions', 'documents', 'journeys')):
    for kind in kinds:
        for path in sorted((root / DIR / kind).glob('*.md')):
            meta, sections = read_doc(path)
            if meta.get('id'):
                yield path, meta, sections


def find_doc(root, ident):
    for path, meta, sections in all_docs(root):
        if meta.get('id') == ident:
            return path, meta, sections
    return None


def next_id(root, kind, prefix, width, first):
    top = first - 1
    for folder in (kind, 'history'):
        for path in (root / DIR / folder).glob(f'{prefix}-*.md'):
            m = re.match(rf'{prefix}-(\d+)', path.name)
            if m:
                top = max(top, int(m.group(1)))
    return f'{prefix}-{top + 1:0{width}d}'


def pending_groups(root):
    """Everything waiting for the user, grouped by the module it belongs to; the rest goes under 通用."""
    modules = sorted((m for _, m, _ in all_docs(root, ('modules',))), key=lambda m: m.get('order') or 0)
    home = {f['id']: f.get('module') for _, f, _ in all_docs(root, ('features',))}
    groups, loose = {m['id']: [] for m in modules}, []
    docs = list(all_docs(root, ('modules', 'features', 'decisions', 'journeys')))
    docs += [(p,) + read_doc(p) for p in sorted((root / DIR / 'product').glob('*.md'))]
    for _, meta, sections in docs:
        lines = [line for body in sections.values() for line in body if PENDING in line]
        kind = meta.get('type')
        if not meta.get('id') or meta.get('status') == 'reference' or not (lines or (meta.get('status') == 'draft' and kind != 'doc')):
            continue
        if kind == 'module':
            key = meta['id']
        elif kind == 'feature':
            key = meta.get('module')
        else:
            key = next((home.get(a, a) for a in meta.get('applies_to') or [] if home.get(a, a) in groups), None)
        item = {'id': meta['id'], 'title': meta.get('title'), 'type': kind, 'status': meta.get('status'), 'lines': lines}
        (groups[key] if key in groups else loose).append(item)
    out = [{'id': m['id'], 'title': m.get('title'), 'items': groups[m['id']]} for m in modules if groups[m['id']]]
    return out + ([{'id': '', 'title': '通用', 'items': loose}] if loose else [])


def counts(line):
    """A statement that is current: not rejected or replaced, and not one that a requirement still in progress (or one that
    was cancelled) brought."""
    return line != PLACEHOLDER and REJECTED not in line and REPLACED not in line and not IN_PROGRESS.search(line) and not DROPPED_LINE.search(line)


def first_item(sections, head):
    for line in sections.get(head, []):
        if counts(line):
            return re.sub(r'〔[^〕]*〕', '', line.lstrip('- ')).strip()
    return ''


def live_items(lines):
    """The statements of one section that still count: no placeholder, nothing the user rejected or replaced; marks and sources removed."""
    out = []
    for line in lines or []:
        if not counts(line):
            continue
        text = re.sub(r'〔[^〕]*〕', '', line[2:] if line.startswith('- ') else line).strip()
        if text:
            out.append(text)
    return out
