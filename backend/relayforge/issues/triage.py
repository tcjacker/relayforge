"""问题的初判：新记下的问题交给后台模型，判断是不是问题、可不可以转需求，对上哪个功能点。

问题的记录本身在 issues.py。分层见 tests/test_layers.py。
"""
from ..core.common import DIR, log
from ..core.documents import live_items
from .record import (
    issue_in_state, append_entry, feature_list, ISSUE_KINDS, issue_lock, issue_show, ISSUE_STATES, issues_home, KIND_WORDS,
    read_thread, save, TRIAGE_FACTS,
)
from ..facts.mapfacts import load_journeys
from ..core.model import run_model
from ..requirements.owners import issue_dir, num

__all__ = ['TRIAGE_INSTRUCTION', 'issue_triage', 'check_judgement', 'triage_payload']


TRIAGE_INSTRUCTION = '''你在给一个软件产品的「问题」做初判。标准输入里有：这个产品现在的事实摘要、功能点清单（每个功能点出现在哪些用户旅程的哪一步）、
这条问题的标题和描述、截图文件的路径、以及到目前为止的对话（用户的补充和你之前的判断）。
只根据这些产品事实判断，不要读代码，也不要去猜代码怎么写的。截图路径如果你能打开就看一眼，打不开就忽略。
把它判成下面四类之一：
- bug（是问题）：产品该是什么样没变，只是现在不是那样，已有的功能出了偏差。你没有复现过，所以依据要如实写成推测，比如「从描述看像是……，还需要复现确认」。
- request（可转需求）：用户希望产品多做一件事或换一种做法，产品该是什么样要变。
- usage（已有用法）：产品已经能做到，用户没找到或用法不对。必须在 howto 里写清楚怎么做，一两句话，按产品事实里的说法。
- ask（待补充）：信息不够，判断不了。必须在 question 里问一个最关键的具体问题，一次只问一个，最好让用户能用一句话回答。
用户在对话里补充过的内容要考虑进去；用户反驳过你之前的判断，要认真对待，不要原样重复。
只输出一个 JSON 对象，不要输出任何其他文字：
{"kind":"bug|request|usage|ask","reason":"一两句话的依据，用普通人能看懂的话","question":"只在 ask 时写","howto":"只在 usage 时写"}'''


def triage_payload(home, meta, sections, thread):
    context = home / DIR / 'context.md'
    facts = context.read_text(encoding='utf-8') if context.exists() else '（还没有事实摘要）'
    features = feature_list(home)
    journeys = load_journeys(home)
    rows = []
    for f in features.values():
        row = f'- {f["id"]} {f["title"]}（模块 {f["module"]} {f["module_title"]}）：{f["one_liner"] or "没有写一句话定位"}'
        for j in journeys:
            steps = [i + 1 for i, s in enumerate(j['steps']) if f['id'] in s['features']]
            if steps:
                row += '\n  - 用户旅程 ' + f'{j["id"]} {j.get("title") or ""}：第 {"、".join(map(str, steps))} 步（' + '；'.join(j['steps'][i - 1]['what'] for i in steps) + '）'
        rows.append(row)
    listing = '\n'.join(rows) or '（还没有功能点）'
    if len(listing) > TRIAGE_FACTS:
        listing = listing[:TRIAGE_FACTS] + '\n…（已截断）'
    shots = issue_dir(home) / 'shots' / meta['id']
    talk = []
    for e in thread:
        if e.get('t') == 'report':
            talk.append(f'【{e["n"]}】提报（{e.get("who")}）：{e.get("text")}' + ''.join(f'\n  截图：{shots / s}' for s in e.get('shots') or []))
        elif e.get('t') == 'user':
            talk.append(f'【{e["n"]}】用户：{e.get("text")}' + ''.join(f'\n  截图：{shots / s}' for s in e.get('shots') or []))
        elif e.get('t') == 'judge':
            talk.append(f'【{e["n"]}】你之前的判断：{ISSUE_KINDS.get(e.get("kind"), e.get("kind"))}。{e.get("reason") or ""}'
                        + (f' 问：{e["question"]}' if e.get('question') else '') + (f' 用法：{e["howto"]}' if e.get('howto') else ''))
        elif e.get('t') == 'finding':
            talk.append(f'【{e["n"]}】排查会话读过代码后的结论（比你的判断可靠）：{ISSUE_KINDS.get(e.get("kind"), e.get("kind"))}。{e.get("text") or ""}')
        elif e.get('t') == 'sys':
            talk.append(f'【{e["n"]}】{e.get("text")}')
    linked = f'{meta["feature"]} {features.get(meta["feature"], {}).get("title", "")}' if meta.get('feature') else '没有关联'
    return (f'# 现行事实摘要\n{facts}\n\n# 功能点清单\n{listing}\n\n# 这条问题\n编号：{num(meta["id"])}\n标题：{meta.get("title")}\n'
            f'来源：{meta.get("source")}\n用户关联的功能点：{linked}\n描述：\n' + '\n'.join(f'- {x}' for x in live_items(sections.get('现象')))
            + '\n\n# 对话\n' + '\n'.join(talk))


def check_judgement(answer):
    """The model's answer, checked; None when it cannot be used (nothing is written then)."""
    if not isinstance(answer, dict):
        return None
    kind = KIND_WORDS.get(str(answer.get('kind') or '').strip())
    reason = ' '.join(str(answer.get('reason') or '').split())
    question = ' '.join(str(answer.get('question') or '').split())
    howto = ' '.join(str(answer.get('howto') or '').split())
    if not kind or not reason:
        return None
    if kind == 'ask' and not question:
        return None
    if kind == 'usage' and not howto:
        return None
    out = {'kind': kind, 'reason': reason[:600]}
    if kind == 'ask':
        out['question'] = question[:300]
    if kind == 'usage':
        out['howto'] = howto[:600]
    return out


def issue_triage(root, ident):
    """One headless model call over the product facts only. A valid answer is written as a judgement; anything else is
    recorded as a failed attempt, so the app can offer 「没判出来，重试」, and nothing about the kind changes."""
    home = issues_home(root)
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident, ('todo',), '初判')
        thread = read_thread(home, meta['id'])
        upto = max((e['n'] for e in thread), default=0)
        payload = triage_payload(home, meta, sections, thread)
    ident = meta['id']
    error = None
    try:
        made = check_judgement(run_model(home, TRIAGE_INSTRUCTION, payload))
        if not made:
            error = '回答的格式不对'
    except Exception as e:  # the model could not be run, timed out or said no JSON
        made, error = None, str(e)[:200]
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident)
        if state != 'todo':
            raise ValueError(f'问题 {num(ident)} 在初判的时候已经变成{ISSUE_STATES.get(state, state)}，这次的判断不写了。')
        if any(e.get('t') == 'finding' and e['n'] > upto for e in read_thread(home, ident)):
            raise ValueError(f'问题 {num(ident)} 在初判的时候排查会话已经有了结论，这次的判断不写了。')
        if made is None:
            append_entry(home, ident, {'t': 'fail', 'who': 'AI', 'text': '没判出来', 'error': error, 'upto': upto})
            log(home, f'triage {ident} failed: {error}')
            raise ValueError(f'问题 {num(ident)} 没判出来：{error}')
        append_entry(home, ident, dict({'t': 'judge', 'who': 'AI', 'upto': upto}, **made))
        meta.update(kind=made['kind'], kind_by='ai')
        save(path, meta, sections)
    return issue_show(home, ident)
