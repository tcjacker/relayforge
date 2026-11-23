"""立项会话的右栏评估。评估随讨论更新，不参与清晰度和立项门槛的计算。"""

import json

from ..core.common import today
from ..core.documents import write_doc
from ..core.identity import caller


def clean_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('评估中的说明要写成非空文字。')
    return ' '.join(value.split())


def list_items(value, convert, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f'评估的列表最多写 {limit} 项。')
    return [convert(item) for item in value]


def highlight(block):
    if not isinstance(block, dict) or set(block) not in (
        {'title', 'text'}, {'title', 'scenarios'}, {'title', 'steps'},
    ):
        raise ValueError('每个重点写 title，以及 text、scenarios、steps 中的一项。')
    result = {'title': clean_text(block['title'])}
    if 'text' in block:
        result['text'] = clean_text(block['text'])
    elif 'steps' in block:
        result['steps'] = list_items(block['steps'], clean_text, 5)
        if len(result['steps']) < 2:
            raise ValueError('影响路径至少写两步。')
    else:
        cards = block['scenarios']
        if not isinstance(cards, list) or len(cards) != 2 or any(
            not isinstance(card, dict) or set(card) != {'title', 'text'} for card in cards
        ):
            raise ValueError('场景对照要写两项，每项包含 title 和 text。')
        result['scenarios'] = [{key: clean_text(value) for key, value in card.items()} for card in cards]
    return result


def assessment(raw):
    if not isinstance(raw, str) or len(raw) > 12000:
        raise ValueError('立项评估要用简短的 JSON 对象。')
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) - {'risk', 'highlights', 'decisions', 'decided', 'refine'}:
        raise ValueError('评估只接受 risk、highlights、decisions、decided、refine。')
    result = {}
    if 'risk' in data:
        risk = data['risk']
        if not isinstance(risk, dict) or set(risk) != {'status', 'title', 'consequence', 'evidence'} or risk['status'] not in ('pending', 'confirmed'):
            raise ValueError('风险要写 status（pending 或 confirmed）、title、consequence 和 evidence。')
        result['risk'] = {key: clean_text(value) for key, value in risk.items()}
    for key in ('highlights', 'decisions', 'decided', 'refine'):
        if key in data:
            result[key] = list_items(data[key], highlight if key == 'highlights' else clean_text, 6)
    return result


def save_assessment(mine, session_id, raw, source):
    me = caller()
    if not mine:
        raise ValueError('立项评估要在需求自己的工作目录里记录。')
    path, meta, sections = mine
    if meta.get('state') != 'draft':
        raise ValueError('只有立项中的需求可以更新立项评估。')
    if not (me['verified'] and me['kind'] == 'intake' and me['owner'] == meta['id']
            and me['session'] == session_id and session_id in (meta.get('sessions') or [])):
        raise ValueError('只有这个需求的立项会话能记录评估。')
    view = assessment(raw)
    # 文件头读取器按「空格 + #」识别注释。转义内容里的井号，保留原样显示。
    meta['intake_assessment'] = json.dumps(view, ensure_ascii=False).replace(' #', r' \u0023')
    meta['intake_assessment_source'] = source
    meta['updated'] = today()
    write_doc(path, meta, sections)
    return '已更新立项右栏的评估。'


def assessment_view(meta):
    try:
        return assessment(meta.get('intake_assessment'))
    except ValueError:
        return {}
