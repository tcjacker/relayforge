"""Turn a session's split suggestion into draft requirements after explicit approval."""

import hashlib
import json
import re
import unicodedata

from ..core.common import DIR, facts_lock, relayforge_home
from ..core.documents import read_doc
from .record import add_dependency, dependency_problem, depends_of, drop_dependency, req_new, requirement_index
from ..sessions.record import stable_tag
from ..core.transcript import read_transcript

BLOCK = re.compile(r'^ {0,3}```relayforge-split\s*\n(.*?)^ {0,3}```[ \t]*$', re.M | re.S)
CONFIRM = re.compile(r'(?:同意拆分|确认拆分|可以拆分|拆吧)[。！!]*')
SHORT_CONFIRM = re.compile(r'(?:同意|确认|好)[。！!]*')
DECLINE = re.compile(r'(?:不同意拆分|不拆分|先不拆分|先不拆|暂不拆分|取消拆分)[。！!]*')
REQ_ID = re.compile(r'R-\d{8}-[a-f0-9]{4}')
# 拆出来的需求和来源需求的先后：after 等来源需求完成后再开发、before 来源需求等它、none 互不依赖。
# waits：还要等的需求，写已有需求的编号，或者「#序号」指这次一起提出的另一条（从 1 开始）。
# 在日常聊天里没有来源需求，一次拆出的几条之间的先后只能靠「#序号」写出来。
# takes：这一条从来源需求接过去的前置需求。来源需求里只有一部分要等它时，把这部分拆出去：同意以后新需求等它，来源需求不再等它，
# 能先做的部分马上就能开始。用户想设的前置需求会和这个需求互相等待时，也是这样解开的（拆出去的部分用 waits 等对方）。
# needs：每条先后写一句要用到对方的什么（记进「前置需求」一节）。键是 waits、takes 里写的编号或「#序号」；和来源需求有先后时键写 HERE：
# after 是这一项要用到来源需求的什么，before 是来源需求要用到这一项的什么。AI 提的先后漏写了就不能同意，用户在应用里改过的先后不要求
HERE = '当前需求'
ORDERS = ('after', 'before', 'none')
SIBLING = re.compile(r'#([1-9]\d?)')


def title_key(title):
    return ' '.join(unicodedata.normalize('NFKC', title).casefold().split())


def one_line(value, limit):
    if not isinstance(value, str):
        return ''
    value = ' '.join(value.split())
    if any(unicodedata.category(char) == 'Cc' for char in value):
        return ''
    return value if len(value) <= limit else ''


def proposal_items(raw):
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            return []
        title = one_line(item.get('title'), 120)
        scope = one_line(item.get('scope'), 1000)
        reason = one_line(item.get('reason'), 500)
        existing = item.get('existing', '')
        if not all((title.strip('- '), scope.strip('- '), reason.strip('- '))) or (
            existing != '' and (not isinstance(existing, str) or not REQ_ID.fullmatch(existing))
        ):
            return []
        order = item.get('order', 'none')
        waits = item.get('waits', [])
        takes = item.get('takes', [])
        if not isinstance(takes, list) or len(takes) > 10 or not all(isinstance(t, str) and REQ_ID.fullmatch(t) for t in takes):
            return []
        needs = item.get('needs', {})
        if not isinstance(needs, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in needs.items()):
            return []
        needs = {k: one_line(v, 300) for k, v in needs.items() if k in waits or k in takes or k == HERE}
        needs = {k: v for k, v in needs.items() if v}
        if order not in ORDERS or not isinstance(waits, list) or len(waits) > 10 or not all(
                isinstance(w, str) and (REQ_ID.fullmatch(w) or (SIBLING.fullmatch(w) and int(w[1:]) <= len(items))) for w in waits):
            return []
        if f'#{len(out) + 1}' in waits:  # 等自己
            return []
        out.append({'title': title, 'scope': scope, 'reason': reason, 'existing': existing, 'order': order, 'waits': list(dict.fromkeys(waits)),
                    'takes': list(dict.fromkeys(takes)), 'needs': needs})
    return out


def proposal_key(session, index, items):
    """The proposal's id. Items without an order or waits hash as they did before those fields existed, so proposals confirmed
    earlier keep their id (and their receipt)."""
    shape = [{k: v for k, v in item.items() if not ((k == 'order' and v == 'none') or (k in ('waits', 'takes', 'needs') and not v))} for item in items]
    return hashlib.sha256(json.dumps([session, index, shape], sort_keys=True).encode()).hexdigest()


def session_proposal(messages, session):
    """Use the latest assistant proposal and a subsequent user message in the same transcript."""
    proposal = None
    source = 0
    for index, message in enumerate(messages, 1):
        text = message['text'].strip()
        if message['role'] == 'assistant':
            blocks = list(BLOCK.finditer(text))
            if blocks:
                items = proposal_items(blocks[-1][1])
                key = proposal_key(session, index, items)
                proposal = {'id': key, 'source': source, 'at': index, 'items': items, 'decision': 0, 'declined': False} if items and source else None
            continue
        if proposal and not proposal['decision'] and not proposal['declined']:
            if CONFIRM.fullmatch(text) or (index == proposal['at'] + 1 and SHORT_CONFIRM.fullmatch(text)):
                proposal['decision'] = index
            elif DECLINE.fullmatch(text):
                proposal['declined'] = True
        source = index
    return proposal


def requirement_records(home):
    return [meta for file in sorted((home / DIR / 'requirements').glob('*.md'))
            if (meta := read_doc(file)[0]).get('status') == 'current' and meta.get('id')]


def resolve_items(proposal, records, parent):
    """Prefer a previous result, then a matching requirement ID or normalized title."""
    by_id = {meta['id']: meta for meta in records if meta['id'] != parent}
    by_title = {}
    split_keys = {}
    for meta in by_id.values():
        by_title.setdefault(title_key(meta.get('title') or ''), meta)
        if meta.get('split_key'):
            split_keys[meta['split_key']] = meta
    seen = {}
    out = []
    for index, item in enumerate(proposal['items']):
        key = f"{proposal['id']}-{index}"
        found = split_keys.get(key)
        if not found and item['existing']:
            found = by_id.get(item['existing'])
            if not found:
                raise ValueError(f"已有需求 {item['existing']} 无法复用，请让 AI 更新拆分建议。")
        found = found or by_title.get(title_key(item['title']))
        if not found and parent and any(meta['id'] == parent and title_key(meta.get('title') or '') == title_key(item['title']) for meta in records):
            raise ValueError('拆分建议包含来源需求，请让 AI 更新拆分建议。')
        identity = found['id'] if found else title_key(item['title'])
        if identity not in seen:
            seen[identity] = len(out)
            out.append(dict(item, key=key, requirement=found['id'] if found else '',
                            title=found['title'] if found else item['title'],
                            created=bool(found and found.get('split_key') == key), number=index + 1))
        else:  # 同一个需求写了两遍：后一条并进前一条，别的条目用「#序号」指它时指到前一条
            kept = out[seen[identity]]
            kept['waits'] = list(dict.fromkeys(kept['waits'] + item['waits']))
            kept['takes'] = list(dict.fromkeys(kept['takes'] + item['takes']))
            kept['needs'] = dict(item['needs'], **kept['needs'])
            kept.setdefault('same', []).append(index + 1)
    return out


def sibling(items, wait):
    """The item a wait refers to: an existing requirement id as it is, 「#序号」 as that item of the same proposal."""
    if not SIBLING.fullmatch(wait):
        return None
    n = int(wait[1:])
    return next(item for item in items if item['number'] == n or n in item.get('same', ()))


def node(item):
    """How an item is named in the dependency edges: its requirement, or new:<key> before it is created."""
    return item['requirement'] or 'new:' + item['key']


def waits_info(items, titles):
    """What each item waits for, and the prerequisites it takes over from the source requirement, as the app shows them: {id, title};
    an item of the same proposal not created yet has no id."""
    for item in items:
        item['takes_info'] = [{'id': t, 'title': titles.get(t, t), 'need': item['needs'].get(t, '')} for t in item['takes']]
        shown = []
        for wait in item['waits']:
            other = sibling(items, wait)
            shown.append(dict({'id': other['requirement'], 'title': other['title']} if other else {'id': wait, 'title': titles.get(wait, wait)},
                              need=item['needs'].get(wait, '')))
        item['waits_info'] = shown
        item['order_need'] = here_need(item)
    return items


def here_need(item):
    """What the item and the source requirement need from each other, as the AI wrote it for its own order: once the user changed the
    order in the app, the sentence may point the wrong way, so it is not used."""
    return item['needs'].get(HERE, '') if item['order'] != 'none' and item['order'] == item.get('ai_order', item['order']) else ''


def missing_needs(items, titles, parent):
    """The orders the AI proposed without saying what is needed, as one sentence for the app, or ''."""
    out = []
    for item in items:
        for wait in item['waits'] + item['takes']:
            if not item['needs'].get(wait):
                other = sibling(items, wait)
                out.append(f'《{item["title"]}》要等《{other["title"] if other else titles.get(wait, wait)}》')
        if parent and item['order'] != 'none' and item['order'] == item.get('ai_order') and not item['needs'].get(HERE):
            out.append(f'《{item["title"]}》和当前需求的先后')
    if not out:
        return ''
    return '没写要用到对方的什么：' + '、'.join(out) + '。让 AI 在拆分建议的 needs 里补上。'


def apply_orders(items, orders, parent):
    """The order of each item: the AI's, or what the user changed in the app before agreeing ({index: order}, index as in the
    proposal). Without a source requirement there is nothing to be before or after."""
    chosen = {}
    if orders:
        try:
            chosen = json.loads(orders) if isinstance(orders, str) else orders
        except ValueError:
            raise ValueError('先后的格式不对。') from None
        if not isinstance(chosen, dict) or not all(str(v) in ORDERS for v in chosen.values()):
            raise ValueError('先后只能是 after（等来源需求完成）、before（来源需求等它）或 none（互不依赖）。')
    for item in items:
        index = item['key'].rsplit('-', 1)[1]
        item['ai_order'] = item.get('ai_order', item['order'])
        item['order'] = str(chosen.get(index, item['order'])) if parent else 'none'
        if not parent:
            item['takes'] = []  # 没有来源需求，没有可以接过去的
    return items


def dependency_edges(items, parent):
    """(需求, 它的前置需求, 要用到什么) the proposal asks for. Items not created yet are named new:<key>."""
    edges = []
    for item in items:
        me = node(item)
        if item['order'] == 'after':
            edges.append((me, parent, here_need(item)))
        elif item['order'] == 'before':
            edges.append((parent, me, here_need(item)))
        for wait in item['waits'] + item['takes']:
            other = sibling(items, wait)
            edges.append((me, node(other) if other else wait, item['needs'].get(wait, '')))
    return edges


def taken(index, items, parent):
    """The prerequisites the items take over from the source requirement, after checking that it has them; and the index as it will
    be once they are taken off the source (to check the new order against)."""
    gone = list(dict.fromkeys(t for item in items for t in item['takes']))
    if not gone:
        return gone, index
    mine = depends_of(index.get(parent) or {})
    for t in gone:
        if t not in mine:
            title = (index.get(t) or {}).get('title') or t
            raise ValueError(f'拆分建议的先后有问题：《{title}》不是这个需求的前置需求，接不过去。让 AI 更新拆分建议。')
    after = dict(index)
    after[parent] = dict(index[parent], depends_on=[d for d in mine if d not in gone])
    return gone, after


def check_edges(index, items, edges):
    """Try the dependencies on a copy of the requirements (items not created yet as placeholders), before anything is created.
    Waiting for one that is done already, or one already waited for, is simply left out. Returns the edges to record."""
    trial = {k: dict(v) for k, v in index.items()}
    for item in items:
        if not item['requirement']:
            trial['new:' + item['key']] = {'id': 'new:' + item['key'], 'title': item['title'], 'state': 'draft'}
    usable = []
    for ident, other, need in edges:
        problem = dependency_problem(trial, ident, other)
        if problem and ('不用等它' in problem or '已经是前置需求' in problem):
            continue
        if problem:
            raise ValueError('拆分建议的先后有问题：' + problem + '在右侧改一下先后，或者让 AI 更新拆分建议。')
        trial[ident]['depends_on'] = depends_of(trial[ident]) + [other]
        usable.append((ident, other, need))
    return usable


def receipt_file(home, key):
    project = hashlib.sha256(str(home).encode()).hexdigest()
    return relayforge_home() / 'splits' / project / f'{key}.json'


def review(home, transcript, session, parent=None, approve=None, orders=None):
    """Preview a proposal or apply it once, using the same project facts as the requirements list. `orders`: the order of each
    item as the user left it in the app ({index: after|before|none}), when they agreed there."""
    proposal = session_proposal(read_transcript(transcript), session)
    if approve and (not proposal or proposal['id'] != approve or proposal['declined']):
        raise ValueError('拆分建议已变化，请查看当前会话。')
    if not proposal or proposal['declined']:
        return None
    key = proposal['id']
    receipt = receipt_file(home, key)
    if receipt.exists():
        return json.loads(receipt.read_text(encoding='utf-8'))
    if not approve and not proposal['decision']:
        records = requirement_records(home)
        titles = {meta['id']: meta.get('title') or meta['id'] for meta in records}
        items = waits_info(apply_orders(resolve_items(proposal, records, parent), None, parent), titles)
        try:
            check_edges(taken(requirement_index(home), items, parent)[1], items, dependency_edges(items, parent))
            problem = ''
        except ValueError as error:
            problem = str(error)
        problem = ' '.join(x for x in (problem, missing_needs(items, titles, parent)) if x)
        return {'id': key, 'items': items, 'state': 'pending', 'parent': parent or '', 'problem': problem}
    with facts_lock(home):
        if receipt.exists():
            return json.loads(receipt.read_text(encoding='utf-8'))
        records = requirement_records(home)
        if parent and not any(meta['id'] == parent and meta.get('state') in ('draft', 'open') for meta in records):
            raise ValueError('来源需求已结束。')
        items = apply_orders(resolve_items(proposal, records, parent), orders, parent)
        titles = {meta['id']: meta.get('title') or meta['id'] for meta in records}
        missing = missing_needs(items, titles, parent)
        if missing:
            raise ValueError(missing)
        gone, after = taken(requirement_index(home), items, parent)
        edges = check_edges(after, items, dependency_edges(items, parent))  # 先查先后，有问题什么都不建
        tag = stable_tag(home, session)
        source = f"{tag}#{proposal['source']}"
        for item in items:
            if item['requirement']:
                continue
            origin = f'来源：会话 {tag}' + (f'，从需求 {parent} 拆出。' if parent else '。')
            made = req_new(home, item['title'], split_key=item['key'], source=source,
                           split_context=(origin, '拆分理由：' + item['reason'], '拟议范围：' + item['scope']))
            item.update(requirement=made['id'], created=True)
        real = {'new:' + item['key']: item['requirement'] for item in items}
        index = requirement_index(home)
        for t in gone:  # 先从来源需求上拿掉：接过去的那条依赖要按拿掉以后的样子查环
            by = '、'.join(f'《{i["title"]}》（{i["requirement"]}）' for i in items if t in i['takes'])
            drop_dependency(index, parent, t, '同意拆分时，用户', f'：要等它的部分拆成了{by}，由那边来等')
        for ident, other, need in edges:
            add_dependency(index, real.get(ident, ident), real.get(other, other), '同意拆分时，用户', need)
        result = {'id': key, 'state': 'done', 'items': waits_info(items, titles)}
        receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # 同一项目的确认在事实锁内串行。若中途退出，已写入的需求仍可按 split_key 找到。
        temp = receipt.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        temp.replace(receipt)
        return result
