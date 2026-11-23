"""另起一次模型调用读聊天记录：抽取事实条目（capture），整理会话进展（sync）。"""

import datetime as dt
import json

from ..core.common import CHUNK_CHARS, DIR, KINDS, MESSAGE_CHARS, facts_lock, main_checkout, today
from ..core.documents import all_docs, first_item
from ..core.transcript import read_transcript
from ..sessions.record import read_session, stable_tag, write_session
from ..core.model import run_model
from .page import render
from .facts import merge
from ..requirements.record import settle_after_session


INSTRUCTION = '''标准输入里有两部分：项目的「现行事实摘要」，和一段带编号的对话。
请只从对话里抽取以下三类条目，其他内容一律不要抽取：
1. decision：用户明确作出的决定，或 AI 认为已经形成但用户没有明说的决定。
2. supersede：某条已有的裁定（用摘要里的 D- 编号指明）被替代或作废。
3. open-question：提出了但还没有定下来的问题。
层、模块、功能点、用户旅程和模块之间的关系（也就是大图）不在这里抽取：它们只在初始化和大图编辑会话里产生和修改，
对话里说到「加一个模块」「把某个功能点挪走」之类，也不要输出。
每个条目必须给出 evidence_at（依据所在的消息编号）和 evidence（那条消息里的原话，不超过 60 字）。
stated_by 只有在用户用自己的话明确表态时才写 "user"，否则写 "inferred"。拿不准就写 "inferred"。
只输出一个 JSON 对象，不要输出任何其他文字，格式：
{"items":[
 {"kind":"decision","title":"短标题","statement":"一句话","reason":"可省略","applies_to":["RF-101"],"supersedes":["D-0001"],"evidence":"…","evidence_at":3,"stated_by":"user"},
 {"kind":"supersede","target":"D-0001","by":"D-0002 或省略","statement":"为什么作废","evidence":"…","evidence_at":8,"stated_by":"user"},
 {"kind":"open-question","target":"RF-101 或省略","statement":"问题","evidence":"…","evidence_at":9,"stated_by":"inferred"}
]}
supersedes 里可以写摘要中已有对象的编号，也可以写本次输出里另一条 decision 的 title。
如果对话里先定了甲、后来改成了乙：甲和乙都输出，并在乙的 supersedes 里写甲的 title。
没有可抽取的内容时输出 {"items":[]}。'''


def run_extractor(root, payload):
    items = run_model(root, INSTRUCTION, payload).get('items', [])
    return [i for i in items if isinstance(i, dict)]


def chunks(messages, first):
    """Yield (payload_text, lo, hi) pieces, numbering messages from `first`."""
    piece, size, lo = [], 0, first
    for offset, m in enumerate(messages):
        n = first + offset
        who = '用户' if m['role'] == 'user' else 'AI'
        text = m['text'] if len(m['text']) <= MESSAGE_CHARS else m['text'][:MESSAGE_CHARS] + '…（已截断）'
        entry = f'【{n}】{who}：{text}\n'
        if piece and size + len(entry) > CHUNK_CHARS:
            yield ''.join(piece), lo, n - 1
            piece, size, lo = [], 0, n
        piece.append(entry)
        size += len(entry)
    if piece:
        yield ''.join(piece), lo, first + len(messages) - 1


def valid(item, lo, hi):
    if item.get('kind') not in KINDS:
        return False
    at = item.get('evidence_at')
    if not isinstance(at, int) or not lo <= at <= hi:
        return False
    if item['kind'] == 'structure':
        return False  # the big picture changes only at initialization and in the map editing session
    if item['kind'] == 'supersede':
        return str(item.get('target') or '').startswith('D-')
    return bool((item.get('statement') or '').strip())


def capture(root, transcript, session_id, trigger):
    root = main_checkout(root)  # the facts, the inbox and the cursor are in the main checkout
    messages = read_transcript(transcript)
    cursor_path = root / DIR / 'cursor.json'
    cursor = json.loads(cursor_path.read_text(encoding='utf-8')) if cursor_path.exists() else {}
    seen = int(cursor.get(session_id, 0))
    if len(messages) <= seen:
        return None
    context = (root / DIR / 'context.md')
    facts = context.read_text(encoding='utf-8') if context.exists() else '（还没有任何内容）'
    items = []
    for text, lo, hi in chunks(messages[seen:], seen + 1):
        payload = f'# 现行事实摘要\n{facts}\n\n# 对话\n{text}'
        found = run_extractor(root, payload)
        items += [i for i in found if valid(i, lo, hi)]
    tag = stable_tag(root, session_id)
    stamp = dt.datetime.now().strftime('%Y%m%dT%H%M%S')
    record = {'session': tag, 'session_id': session_id, 'tool': 'claude-code', 'trigger': trigger,
              'captured_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
              'range': [seen + 1, len(messages)], 'items': items}
    inbox = root / DIR / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    out = inbox / f'{stamp}-{tag[-4:]}.json'
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    cursor[session_id] = len(messages)
    cursor_path.write_text(json.dumps(cursor, indent=2), encoding='utf-8')
    merge(root)
    return inbox / 'merged' / out.name


SESSION_INSTRUCTION = '''标准输入里有两部分：这次会话「目前的状态」（JSON，第一次时为空），和接下来的一段带编号的对话。
请根据新的这段对话更新状态，只输出更新后的完整 JSON 对象，不要输出任何其他文字。格式：
{"title":"十个字以内的短标题，让人一眼认出这是哪件事",
 "goal":"整个任务想做成什么，一两句话",
 "doing":"对话结束时正在处理的事，一句话",
 "plan":[{"title":"计划里的一步，短句","status":"done|doing|todo|dropped","note":"做到什么程度或为什么放弃，可为空","at":12}],
 "changes":[{"at":34,"text":"计划或方向的一次重大调整，一句话"}],
 "modules":["M-01"],
 "features":["RF-101"]}
规则：
- plan 是双方商定的、跨很多轮对话的工作计划，不是某一轮里的工具调用步骤。保持 4 到 12 步。
- 用户改了方向时：不再做的步骤标 dropped 并在 note 里写原因，新方向的步骤加进来，同时在 changes 里记一条。
- 已经做完的步骤保留并标 done，不要删除。at 写这一步最近一次被提出或更新时的消息编号。
- status 只有在对话里能看到明确依据时才写 done；说过要做但没看到做完的写 todo；正在做的写 doing。
- changes 只记方向性的调整，最多保留 10 条，按时间顺序。
- modules 写这次会话正在改动或接下来要改动的模块编号，features 写正在改动或接下来要改动的功能点编号，都只能从输入里的「模块和功能点清单」中选；没有清单或都不相关时写空数组。
- 只有改它的功能才算在动：改它的代码、行为或设计。只是提到它、讨论它、整理或补写它的事实记录和说明，都不算。
- 能确定到功能点时一定写功能点，确定不了才只写模块。
- 状态里已有的 modules 和 features 默认保留；只有按上面的定义它本来就不该算，或者对话表明已经不再改它时才去掉。
- 状态里已经有 title 时保持不变，除非整个任务换了一件事。
- 用普通人能看懂的话，不要写文件名以外的代码细节。'''


def sync(root, transcript, session_id, name=None, end=False):
    """Bring one session's goal, current work and plan up to date. Only new messages are read. The session file is in the
    main checkout, wherever the session runs."""
    here, root = root, main_checkout(root)
    messages = read_transcript(transcript)
    state, seen, old = {}, 0, {}
    with facts_lock(root):
        tag = stable_tag(root, session_id)
        path = root / DIR / 'sessions' / f'{tag}.md'
        if path.exists():
            old = read_session(path)
    if old:
        state, seen = {k: old.get(k) for k in ('title', 'goal', 'doing', 'plan', 'changes', 'modules', 'features')}, old['upto']
        if not old.get('named') and len(str(state.get('title') or '')) > 14:
            state['title'] = None  # a cut-off goal from before sessions had names; let the model name it
    if seen > len(messages):
        state, seen = {}, 0
    fresh = len(messages) > seen
    rename_only = not fresh
    if not fresh and not name and not end:
        return path if old else None
    if name == 'auto':  # hand the naming back to the model
        name, old = None, dict(old, named=None)
        fresh = fresh or bool(state.get('goal'))
    named = bool(name) or bool(old.get('named'))
    if fresh and len(messages) > seen:
        kept = state.get('title') if named else None
        feats = list(all_docs(root, ('features',)))
        listing = '\n'.join(
            f'- {meta["id"]} {meta.get("title")}：{first_item(sections, "负责什么")}' +
            ''.join(f'\n  - {fm["id"]} {fm.get("title")}：{first_item(fs, "一句话定位")}' for _, fm, fs in feats if fm.get('module') == meta['id'])
            for _, meta, sections in all_docs(root, ('modules',)))
        for text, lo, hi in chunks(messages[seen:], seen + 1):
            payload = '## 模块和功能点清单\n' + (listing or '（还没有模块）') + '\n\n## 目前的状态\n' + json.dumps(state, ensure_ascii=False) + f'\n\n## 对话（第 {lo} 到 {hi} 条）\n' + text
            new = run_model(root, SESSION_INSTRUCTION, payload)
            if not isinstance(new.get('plan'), list) or not new.get('goal'):
                raise ValueError('extractor returned no goal or plan')
            state = new
        if kept:
            state['title'] = kept
    if not state.get('goal'):
        return None
    if name:
        state['title'] = name
    if rename_only:  # only the name or the ended mark changes; keep the recorded times
        for entry in (state.get('plan') or []) + (state.get('changes') or []):
            entry.pop('when', None)
    ended = today() if end else (None if not rename_only else old.get('ended'))
    # A note may have claimed the short number while the model was running.
    with facts_lock(root):
        tag = stable_tag(root, session_id)
        path = write_session(here, tag, session_id, state, len(messages), messages, named=named, ended=ended)
        settle_after_session(here, path, facts_locked=True)
    render(root)
    return path
