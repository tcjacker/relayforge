"""Owner-facing choices are stored with the question, never trusted from form text."""
def card(data):
    value = data.get('decision')
    if isinstance(value, dict) and isinstance(value.get('options'), list):
        options = value['options']
        if 2 <= len(options) <= 4 and all(isinstance(o, dict) and all(isinstance(o.get(k), str) and o[k].strip() for k in ('id', 'label', 'impact', 'reply')) for o in options) and len({o['id'] for o in options}) == len(options) and all(o['id'] != 'other' for o in options):
            return value
    return {'background': data.get('question', 'Agent 未提供问题背景。'),
            'recommendation': '信息不足时，先要求 Agent 补充选项及影响。',
            'options': [{'id': 'clarify', 'label': '请 Agent 先解释清楚（推荐）',
                         'impact': '要求补充背景、具体选项、影响和推荐理由，保留已有工作，不擅自改变需求。',
                         'reply': '请先补充结构化裁定卡，解释背景、2至4个具体选项及影响、推荐项和理由。保留工作，不擅自改变产品约束。'},
                        {'id': 'recommend', 'label': '按 Agent 当前建议继续',
                         'impact': data.get('recommendation') or '尚无明确建议，请先要求补充。',
                         'reply': '按当前建议继续：' + (data.get('recommendation') or '先补充具体建议。')}]}


def answer(data, choice, other=''):
    if choice == 'other':
        if not other.strip() or len(other) > 12000:
            raise ValueError('选择其他时，请填写你的意见（最多12000字）')
        return other.strip()
    for option in card(data)['options']:
        if option['id'] == choice:
            return option['reply']
    raise ValueError('请选择有效选项')
