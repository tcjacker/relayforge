"""大图的几样事实：分几层（product/layers.md）、模块属于哪一层（模块文件头的 layer）、模块之间的关系（模块文件里的
「和其他模块的关系」）、每个模块的用户旅程（journeys/ 下，一条旅程一个文件，文件头的 module 是它所属的模块）。

层一行一层，按从上到下的顺序：
  - 〔待确认〕用户界面：用户直接看到、直接操作的地方〔代码导入#0〕
关系一行一条，写在发起的那个模块的文件里：
  - 〔待确认〕调用 M-10：提交后启动规划和执行｜RF-108、RF-114〔代码导入#0〕
用户旅程「步骤」下一行一步：
  - 〔待确认〕点「新建需求」写一两句话｜左边多出一个需求｜用户｜RF-140〔代码导入#0〕
  做什么｜看到什么｜谁来做（「用户」或模块编号，可以几个）｜用到的功能点（可以是别的模块的，可省略）
"""

import re
import uuid

from ..core.common import DIR, JOURNEY_HEADS, MODULE_HEADS, PENDING, PLACEHOLDER, REJECTED, commit_facts, main_checkout, today
from ..core.documents import read_doc, write_doc

RELATION_HEAD = '和其他模块的关系'
RELATION_KINDS = {'调用': 'call', '读写数据': 'data', '套用规则': 'rule'}
LAYER_HEAD = '层'
UNLAYERED = '未分层'
USER_WORDS = ('用户', '你')
ID = re.compile(r'[A-Za-z]+-\d+')
SOURCES = re.compile(r'〔([^〕]*#[^〕]*)〕')
RELATION = re.compile(r'^(' + '|'.join(RELATION_KINDS) + r')\s*([A-Za-z]+-\d+)\s*[：:]\s*(.+)$')


def split_line(line):
    """A statement without its dash, marks and sources: (text, waiting, rejected, sources)."""
    text = line.strip()
    if text.startswith('-'):
        text = text[1:].strip()
    waiting, rejected = text.startswith(PENDING), text.startswith(REJECTED)
    if waiting or rejected:
        text = text[len(PENDING):].strip()
    sources = SOURCES.findall(text)
    return SOURCES.sub('', text).strip(), waiting, rejected, sources


def parse_relation(owner, line):
    """One line of 「和其他模块的关系」 as an edge, or None when it does not follow the format."""
    if line == PLACEHOLDER:
        return None
    text, waiting, rejected, sources = split_line(line)
    m = RELATION.match(text)
    if not m or rejected:
        return None
    what, _, via = m.group(3).partition('｜')
    return {'from': owner, 'to': m.group(2), 'kind': RELATION_KINDS[m.group(1)], 'word': m.group(1), 'text': what.strip(),
            'via': ID.findall(via), 'waiting': waiting, 'src': sources}


def relation_line(kind_word, target, text, via=()):
    """The body of a relation line, before marks and sources are added."""
    return f'{kind_word} {target}：{text.strip()}' + (f'｜{"、".join(via)}' if via else '')


def relations(modules):
    """Every edge written in the module files; edges to modules that do not exist are left out."""
    known = {m['id'] for m in modules}
    out = []
    for m in modules:
        for line in (m.get('sections') or {}).get(RELATION_HEAD, []):
            edge = parse_relation(m['id'], line)
            if edge and edge['to'] in known and edge['to'] != m['id']:
                out.append(edge)
    return out


def load_layers(root, modules=()):
    """The layers from top to bottom: those written in product/layers.md, then any a module names that the file does not
    (an older project's module group counts as its layer), then 未分层 when some module has none."""
    path = root / DIR / 'product' / 'layers.md'
    out, seen = [], set()
    if path.exists():
        for line in read_doc(path)[1].get(LAYER_HEAD, []):
            if line == PLACEHOLDER:
                continue
            text, waiting, rejected, sources = split_line(line)
            name, _, about = text.replace('：', ':', 1).partition(':')
            name = name.strip()
            if name and not rejected and name not in seen:
                seen.add(name)
                out.append({'name': name, 'about': about.strip(), 'waiting': waiting, 'src': sources})
    for m in modules:
        name = layer_of(m)
        if name not in seen:
            seen.add(name)
            out.append({'name': name, 'about': '', 'waiting': False, 'src': []})
    if UNLAYERED in seen:  # keep the leftovers last
        out = [l for l in out if l['name'] != UNLAYERED] + [l for l in out if l['name'] == UNLAYERED]
    return out


def layer_of(module):
    return str(module.get('layer') or module.get('group') or UNLAYERED)


def layer_line(name, about=''):
    return f'{name.strip()}：{about.strip()}' if about else name.strip()


def parse_step(line):
    if line == PLACEHOLDER:
        return None
    text, waiting, rejected, sources = split_line(line)
    if rejected or not text:
        return None
    parts = [p.strip() for p in text.split('｜')]
    if len(parts) == 3:  # 做什么｜谁来做｜功能点, without 看到什么
        parts = [parts[0], '', parts[1], parts[2]]
    parts += [''] * (4 - len(parts))
    who = parts[2]
    return {'what': parts[0], 'see': parts[1], 'user': any(w in who for w in USER_WORDS), 'modules': ID.findall(who),
            'features': ID.findall(parts[3]), 'waiting': waiting, 'src': sources}


def step_line(what, see, who, features=()):
    return f'{what.strip()}｜{see.strip()}｜{"、".join(who)}' + (f'｜{"、".join(features)}' if features else '')


def load_journeys(root):
    """The user journeys under .relayforge/journeys/, in order, with their steps parsed."""
    out = []
    for path in sorted((root / DIR / 'journeys').glob('*.md')):
        meta, sections = read_doc(path)
        if not meta.get('id'):
            continue
        steps = [s for s in (parse_step(l) for l in sections.get('步骤', [])) if s]
        out.append(dict(meta, sections=sections, steps=steps, file=f'{DIR}/journeys/{path.name}', trunk=is_trunk(meta)))
    return sorted(out, key=lambda j: (str(j.get('module') or ''), j.get('order') or 0, j['id']))


def is_trunk(meta):
    """Journey header trunk: true (written by set_trunk; a hand-written false or no means no)."""
    return str(meta.get('trunk') or '').strip().lower() in ('true', '1', 'yes')


def set_trunk(root, ident, on=True):
    """Only the user: mark a user journey as trunk (主干) or not. The merge gate (testsets/gate.py) always runs the test sets
    of the features its steps use, so which journeys are trunk is a product decision, not the AI's."""
    home = main_checkout(root)
    want = str(ident or '').strip().upper()
    for path in sorted((home / DIR / 'journeys').glob('*.md')):
        meta, sections = read_doc(path)
        if str(meta.get('id') or '').upper() != want:
            continue
        if is_trunk(meta) != bool(on):
            if on:
                meta['trunk'] = 'true'
            else:
                meta.pop('trunk', None)
            meta['updated'] = today()
            write_doc(path, meta, sections)
            commit_facts(home, f'用户旅程 {meta["id"]}：' + ('设为主干' if on else '不再是主干'))
        return {'id': meta['id'], 'title': meta.get('title') or '', 'trunk': bool(on)}
    raise ValueError(f'没有找到用户旅程 {ident}。用户旅程在 {DIR}/journeys/ 下，编号像 J-01。')


def ordered(sections, heads):
    """Sections in the order the format fixes; headings the format does not know keep their place at the end."""
    out = {h: sections.get(h, []) for h in heads}
    out.update({h: v for h, v in sections.items() if h not in out})
    return out


def module_sections(sections):
    return ordered(sections, MODULE_HEADS)


def journey_sections(sections):
    return ordered(sections, JOURNEY_HEADS)


def map_session_id(root):
    """The one Claude Code session that edits a project's big picture: fixed per project, so the edit window always comes back to it."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'relayforge-map:' + str(main_checkout(root).resolve())))
