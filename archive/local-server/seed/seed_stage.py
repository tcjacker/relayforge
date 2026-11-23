"""Stage domain rules, independent of the database and model adapter."""
from uuid import UUID


def validate(cards):
    if not isinstance(cards, list) or not 2 <= len(cards) <= 6:
        raise ValueError("阶段需要 2 到 6 张任务卡")
    ids = []
    for card in cards:
        try:
            ident = str(UUID(card['id']))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("任务标识无效") from None
        if ident != card['id'] or ident in ids:
            raise ValueError("任务标识重复或格式无效")
        ids.append(ident)
        for key, label in [('title', '标题'), ('goal', '需求'), ('acceptance', '验收条件')]:
            if not isinstance(card.get(key), str) or not card[key].strip() or len(card[key]) > 12000:
                raise ValueError(f"任务 {card.get('title', ident)} 的{label}为空或过长")
        if not isinstance(card.get('blockedBy'), list) or any(not isinstance(x, str) for x in card['blockedBy']):
            raise ValueError("依赖必须是任务标识列表")
    by_id = {c['id']: c for c in cards}
    for c in cards:
        for dep in c['blockedBy']:
            if dep not in by_id:
                raise ValueError(f"{c['title']}：缺失依赖 {dep}")
            if dep == c['id']:
                raise ValueError(f"{c['title']}：不能依赖自身")
    done = set()
    def visit(ident, path):
        if ident in path:
            raise ValueError("循环依赖：" + ' → '.join(by_id[x]['title'] for x in path + [ident]))
        if ident not in done:
            for dep in by_id[ident]['blockedBy']:
                visit(dep, path + [ident])
            done.add(ident)
    for ident in ids:
        visit(ident, [])
    return cards


def merged(rows):
    return {k: v for row in rows for k, v in row['data'].items()}


def blockers(task, jobs, stage):
    data = merged(jobs[task])
    if not data.get('stage_id'):
        return []
    if (not stage or stage['id'] != data['stage_id'] or stage['state'] not in ('authorized', 'accepted')
            or task not in stage['tasks'] or data.get('draft_version') != stage['authorized_version']):
        return ['阶段草稿尚未授权或授权映射不匹配']
    result = []
    for dep in data.get('blockedBy', []):
        rows = jobs.get(dep)
        if not rows or rows[-1]['state'] != 'activated':
            title = merged(rows).get('title', dep) if rows else dep
            result.append(f"等待 {title} 人工启用（{rows[-1]['state'] if rows else '缺失'}）")
    return result


def select_task(jobs, stage):
    return next(((task, rows) for task, rows in jobs.items()
                 if rows[-1]['state'] in ('queued', 'answered') and not blockers(task, jobs, stage)), None)


def ready(stage, jobs):
    return bool(stage and stage['tasks']) and all(
        jobs.get(task) and jobs[task][-1]['state'] == 'activated' for task in stage['tasks'])


def context(task, jobs, stage):
    data = merged(jobs[task])
    if not data.get('stage_id'):
        return {}
    if blockers(task, jobs, stage):
        raise ValueError('；'.join(blockers(task, jobs, stage)))
    return {'stage_goal': stage['goal'], 'stage_acceptance': stage['acceptance'],
            'draft_version': stage['authorized_version'],
            'upstream': [{'task': dep, 'candidate': merged(jobs[dep])['candidate'],
                          'summary': merged(jobs[dep]).get('summary', '')}
                         for dep in data.get('blockedBy', [])]}
