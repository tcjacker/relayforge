"""确认完成以后：打标签、写总结、查漏。

客观标签由程序算，不经模型：这件事涉及的功能点、模块、裁定、问题和文件。来源是需求文件（三个小节和完成记录里「新增」「修改」的编号）、
它的会话边做边记（会话文件头的 modules、features）、引用了它的会话的裁定、rf.py trace 算出的文件。编号在需求完成时写进需求文件头的
tags，问题解决时写进问题文件头（只经 issues.py 的读写）；文件和完成记录一样不写列表，查的时候（history search --file）由 git 现算。

概括只对需求做（rf.py summarize R-…，确认完成以后由应用在后台调用，不挡完成）：调一次无界面模型（和问题初判同一个办法、同一个模型），
一次产出三样——概括标签（关于什么、踩过什么坑，几个短词，写进文件头 topics）、几句话的总结（写进「总结」一节）、聊到了但没记下的决定。
没记下的决定一律是推断：照抽取（extract.py）的老路写成一份收件箱记录，由 facts.merge 记成草稿裁定、行首〔待确认〕，出处是那个会话的那条消息，
等用户用 /rf-confirm 或 rf.py confirm 确认；程序和模型都不确认。同一个需求再总结一次：替换上次的总结和概括标签，已经提过的决定不再提。
"""

import datetime as dt
import json
import re
from pathlib import Path

from ..core.common import DIR, commit_facts, facts_lock, log, main_checkout, today
from ..core.documents import add_line, all_docs, live_items, read_doc, write_doc
from ..core.model import run_model
from .record import read_session, stable_tag
from ..core.transcript import read_transcript
from .archive import find_archived, find_original
from ..facts.facts import merge, write_summaries

__all__ = ['SUMMARY_HEAD', 'SUMMARY_INSTRUCTION', 'requirement_tags', 'issue_tags', 'set_tags', 'summarize', 'summary_payload', 'check_summary',
           'requirement_transcripts']

SUMMARY_HEAD = '总结'
TALK_CHARS = 60000       # 给模型的原始聊天最多这么多字
MESSAGE_CAP = 1500       # 一条消息最多这么多字
IDS = re.compile(r'\b((?:RF|M|D|I)-\d+)\b')

SUMMARY_INSTRUCTION = '''标准输入里是一个已经确认完成的需求：需求文件、它的会话边做边记的进展、已经记下的裁定，以及原始聊天里用户说的话和 AI 的文字回复（带编号，按会话分开）。
请一次给出三样东西：
1. topics：概括标签，3 到 6 个短词（每个不超过 10 个字），说这件事是关于什么的、做的过程中踩过什么坑，方便以后搜索。不要写功能点编号。
2. summary：几句话的总结（2 到 5 句，每句不超过 80 字）：做了什么、为什么这样做、最后是什么样、留下了什么没做的。用普通人能看懂的话。
3. decisions：聊天里用户明确定下了、或者双方已经形成，但是在「已经记下的裁定」和需求文件里都找不到的决定。没有就给空数组，宁缺毋滥；
   已经记下的、只是实现细节的、后来又被推翻的都不要。每条必须给出 session（会话编号，如 S-20261118-ab12）、at（依据所在的消息编号）、
   evidence（那条消息里的原话，不超过 60 字）。这些都会交给用户确认，你不能替用户确认。
只输出一个 JSON 对象，不要输出任何其他文字：
{"topics":["…"],"summary":["…"],"decisions":[{"title":"短标题","statement":"一句话的决定","reason":"可省略","applies_to":["RF-101"],"session":"S-…","at":12,"evidence":"…"}]}'''


# ---------- 客观标签 ----------

def _known_ids(home):
    known = set()
    for kind in ('modules', 'features', 'decisions', 'history'):
        for path in (Path(home) / DIR / kind).glob('*.md'):
            m = re.match(r'((?:RF|M|D)-\d+)', path.name)
            if m:
                known.add(m.group(1))
    for path in (Path(home) / DIR / 'issues').glob('I-*.md'):
        m = re.match(r'(I-\d+)', path.name)
        if m:
            known.add(m.group(1))
    return known


def _feature_modules(home):
    out = {}
    for kind in ('features', 'history'):
        for path in (Path(home) / DIR / kind).glob('RF-*.md'):
            meta = read_doc(path)[0]
            if meta.get('id') and meta.get('module'):
                out.setdefault(meta['id'], str(meta['module']))
    return out


def _session_metas(home, uuids):
    out = []
    for uuid in uuids:
        tag = stable_tag(home, uuid)
        path = Path(home) / DIR / 'sessions' / f'{tag}.md'
        out.append((tag, read_session(path) if path.exists() else None))
    return out


def _citing_decisions(home, ident, tags):
    """Decisions the work recorded: brought by it (in_progress) or citing one of its sessions."""
    out = []
    for kind in ('decisions', 'history'):
        for path in sorted((Path(home) / DIR / kind).glob('D-*.md')):
            meta, sections = read_doc(path)
            if not meta.get('id'):
                continue
            text = '\n'.join(l for lines in sections.values() for l in lines)
            if meta.get('in_progress') == ident or any(f'〔{t}#' in text for t in tags):
                out.append(meta['id'])
    return out


def _order(ids):
    rank = {'RF': 0, 'M': 1, 'D': 2, 'I': 3}
    return sorted(dict.fromkeys(ids), key=lambda x: (rank.get(x.split('-')[0], 9), x))


def requirement_tags(home, meta, sections, merge_commit=None):
    """The objective tags of a requirement: {'ids': [RF-…, M-…, D-…, I-…]}. merge_commit: unused, kept for callers."""
    home = main_checkout(home)
    known = _known_ids(home)
    ident = meta.get('id')
    text = '\n'.join(l for lines in sections.values() for l in lines)
    ids = [i for i in IDS.findall(text) if i in known]
    sessions = _session_metas(home, [str(x) for x in meta.get('sessions') or []])
    for _, s in sessions:
        if s:
            ids += [str(x) for x in (s.get('features') or []) + (s.get('modules') or []) if str(x) in known]
    ids += _citing_decisions(home, ident, [t for t, _ in sessions])
    for key in ('source_issue', 'converted_issue'):
        if meta.get(key):
            ids.append(str(meta[key]))
    owner = _feature_modules(home)
    ids += [owner[i] for i in ids if i in owner and owner[i] in known]
    return {'ids': _order(i for i in ids if i in known or i.startswith('I-'))}


def issue_tags(home, meta, fix=None):
    """The objective tags of a resolved issue: its feature and that feature's module, decisions its fix sessions cited, the
    requirement it came from or went to, and the other issues fixed with it."""
    home = main_checkout(home)
    known = _known_ids(home)
    ids = [str(meta[k]) for k in ('feature', 'source_requirement', 'requirement') if meta.get(k)]
    owner = _feature_modules(home)
    if meta.get('feature') in owner:
        ids.append(owner[meta['feature']])
    if fix:
        tags = [stable_tag(home, u) for u in fix.get('sessions') or []]
        ids += _citing_decisions(home, fix['id'], tags)
        ids += [i for i in fix.get('issues') or [] if i != meta.get('id')]
    return {'ids': _order(i for i in ids if i in known or i.startswith(('R-', 'I-')))}


def set_tags(home, meta, tags):
    """Only the ids go into the file. The files a piece of work changed are not written down (like the 完成记录, which keeps
    only the counts): rf.py trace and rf.py history work them out from git when asked."""
    if tags['ids']:
        meta['tags'] = tags['ids']
    else:
        meta.pop('tags', None)


# ---------- 概括、总结、查漏 ----------

def requirement_transcripts(home, meta):
    """[(tag, uuid, path or None)] for the requirement's sessions: the copy kept on this machine first, else the original."""
    out = []
    for uuid in [str(x) for x in meta.get('sessions') or []]:
        kept = find_archived(uuid, home)
        found = find_original(uuid)
        out.append((stable_tag(home, uuid), uuid, kept or (found['main'] if found else None)))
    return out


def _talk(sessions, budget=TALK_CHARS):
    """User and assistant text, numbered as everywhere else (from 1, per session); cut to fit, keeping each session's
    beginning and end."""
    blocks, counts = [], {}
    per = max(4000, budget // max(1, len(sessions)))
    for tag, _, path in sessions:
        messages = read_transcript(path) if path else []
        counts[tag] = len(messages)
        if not messages:
            blocks.append(f'## 会话 {tag}\n（原始记录已不在）')
            continue
        rows = []
        for n, m in enumerate(messages, 1):
            text = ' '.join(m['text'].split())
            text = text if len(text) <= MESSAGE_CAP else text[:MESSAGE_CAP] + '…（已截断）'
            rows.append(f'【{n}】{"用户" if m["role"] == "user" else "AI"}：{text}')
        if sum(len(r) for r in rows) > per:
            head, tail, size = [], [], 0
            for r in rows:
                if size + len(r) > per * 0.4:
                    break
                head.append(r)
                size += len(r)
            for r in reversed(rows[len(head):]):
                if size + len(r) > per:
                    break
                tail.insert(0, r)
                size += len(r)
            rows = head + [f'…（中间省略 {len(rows) - len(head) - len(tail)} 条）'] + tail
        blocks.append(f'## 会话 {tag}\n' + '\n'.join(rows))
    return '\n\n'.join(blocks), counts


def summary_payload(home, path, meta, sections):
    sessions = requirement_transcripts(home, meta)
    talk, counts = _talk(sessions)
    req = '\n'.join(f'## {h}\n' + '\n'.join(lines) for h, lines in sections.items() if h != SUMMARY_HEAD)
    notes = []
    for tag, s in _session_metas(home, [str(x) for x in meta.get('sessions') or []]):
        if not s:
            notes.append(f'## {tag}\n（没有进展记录）')
            continue
        plan = '\n'.join(f'- [{p["status"]}] {p["title"]}' + (f'｜{p["note"]}' if p['note'] else '') for p in s.get('plan') or [])
        changes = '\n'.join(f'- {c["text"]}' for c in s.get('changes') or [])
        notes.append(f'## {tag} {s.get("title") or ""}\n目标：{s.get("goal") or ""}\n正在处理：{s.get("doing") or ""}\n计划：\n{plan or "（没有）"}\n方向调整：\n{changes or "（没有）"}')
    tags = [t for t, _, _ in sessions]
    recorded = []
    for _, dmeta, dsections in all_docs(home, ('decisions',)):
        recorded.append(f'- {dmeta["id"]} {dmeta.get("title") or ""}：' + '；'.join(live_items(dsections.get('决定'))[:2]))
    asked = [str(x) for x in meta.get('summary_decisions') or []]
    payload = (f'# 需求 {meta["id"]} {meta.get("title")}\n{req}\n\n# 会话边做边记\n' + ('\n\n'.join(notes) or '（没有）')
               + '\n\n# 已经记下的裁定（包括以前提出、还在等用户确认的）\n' + ('\n'.join(recorded[-200:]) or '（没有）')
               + (f'\n其中 {"、".join(asked)} 是上一次总结时提出的，不要再提。' if asked else '')
               + '\n\n# 原始聊天（只有用户说的话和 AI 的文字回复，工具的输出没有放进来）\n' + talk)
    return payload, counts, tags


def _one(text, cap):
    text = ' '.join(str(text or '').split()).replace('〔', '（').replace('〕', '）')
    return text[:cap]


def check_summary(answer, counts, known_ids=()):
    """The model's answer, checked: None when it cannot be used. Decisions that do not point at a real message are dropped."""
    if not isinstance(answer, dict):
        return None
    topics = [t for t in (_one(x, 12) for x in answer.get('topics') or [] if isinstance(x, str)) if t and not IDS.fullmatch(t)]
    summary = [s for s in (_one(x, 200) for x in answer.get('summary') or [] if isinstance(x, str)) if s]
    if not topics or not summary:
        return None
    decisions = []
    for d in answer.get('decisions') or []:
        if not isinstance(d, dict):
            continue
        statement = _one(d.get('statement'), 300)
        session, at = str(d.get('session') or '').strip(), d.get('at')
        if not statement or session not in counts or not isinstance(at, int) or not 1 <= at <= counts[session]:
            continue
        decisions.append({'kind': 'decision', 'title': _one(d.get('title') or statement, 30), 'statement': statement,
                          'reason': _one(d.get('reason'), 300) or None, 'applies_to': [str(x) for x in d.get('applies_to') or [] if str(x) in known_ids],
                          'supersedes': [], 'evidence': _one(d.get('evidence'), 80), 'evidence_at': at, 'stated_by': 'inferred', 'session': session})
    return {'topics': list(dict.fromkeys(topics))[:8], 'summary': summary[:6], 'decisions': decisions[:8]}


def _with_summary(sections, lines):
    """The sections with 总结 (replaced) just before 变更记录."""
    out = {}
    for head, body in sections.items():
        if head == SUMMARY_HEAD:
            continue
        if head == '变更记录':
            out[SUMMARY_HEAD] = lines
        out[head] = body
    if SUMMARY_HEAD not in out:
        out[SUMMARY_HEAD] = lines
    return out


def _propose(home, ident, decisions, uuids):
    """Hand the decisions nobody wrote down to the existing 待确认 flow: one inbox record per session, merged like a capture.
    Returns the decision ids created."""
    seen = [l for kind in ('decisions', 'history') for p in (Path(home) / DIR / kind).glob('D-*.md') for l in read_doc(p)[1].get('决定', [])]
    decisions = [d for d in decisions if not any(d['statement'] in l for l in seen)]  # 记过的、提过又被否决的，都不再提
    if not decisions:
        return []
    before = {meta['id'] for _, meta, _ in all_docs(home, ('decisions',))}
    inbox = Path(home) / DIR / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime('%Y%m%dT%H%M%S')
    by_tag = {stable_tag(home, u): u for u in uuids}
    for tag in dict.fromkeys(d['session'] for d in decisions):
        items = [{k: v for k, v in d.items() if k != 'session'} for d in decisions if d['session'] == tag]
        record = {'session': tag, 'session_id': by_tag.get(tag), 'tool': 'summarize', 'trigger': f'summarize {ident}',
                  'captured_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'), 'items': items}
        (inbox / f'{stamp}-{tag[-4:]}-sum.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    merge(home)
    tags = set(by_tag)
    made = []
    for _, meta, sections in all_docs(home, ('decisions',)):
        if meta['id'] in before:
            continue
        text = ' '.join(sections.get('决定', []))
        if any(f'〔{t}#' in text for t in tags):
            made.append(meta['id'])
    return sorted(made)


def summarize(root, ident, run=None):
    """One headless model call for a finished requirement. Writes topics, the 总结 section, the objective tags, and proposes
    the decisions nobody wrote down as 待确认. Raises ValueError when it cannot (the requirement stays finished, unsummarized)."""
    home = main_checkout(root)
    path = next((p for p in sorted((home / DIR / 'requirements').glob('*.md')) if read_doc(p)[0].get('id') == ident), None)
    if not path:
        raise ValueError(f'没有找到需求 {ident}。')
    meta, sections = read_doc(path)
    if meta.get('state') != 'done':
        raise ValueError(f'需求 {ident} 还没有确认完成，不总结。')
    payload, counts, _ = summary_payload(home, path, meta, sections)
    known = _known_ids(home)
    try:
        answer = (run or run_model)(home, SUMMARY_INSTRUCTION, payload)
    except Exception as error:  # noqa: BLE001 - the model could not be run, timed out or said no JSON
        log(home, f'summarize {ident} failed: {error!r}')
        raise ValueError(f'没总结出来：{str(error)[:200]}') from error
    made = check_summary(answer, counts, known)
    if not made:
        log(home, f'summarize {ident}: answer not usable')
        raise ValueError('没总结出来：回答的格式不对。')
    with facts_lock(home):
        meta, sections = read_doc(path)  # read again: the model took a while
        uuids = [str(x) for x in meta.get('sessions') or []]
        proposed = _propose(home, ident, made['decisions'], uuids)
        meta, sections = read_doc(path)
        source = f'{ident}#总结'
        lines = [f'- {s}〔{source}〕' for s in made['summary']]
        first = not meta.get('summarized')
        set_tags(home, meta, requirement_tags(home, meta, sections))
        asked = list(dict.fromkeys([str(x) for x in meta.get('summary_decisions') or []] + proposed))
        meta.update(topics=made['topics'], summarized=dt.datetime.now().strftime('%Y-%m-%d %H:%M'), summary_decisions=asked or None, updated=today())
        sections = _with_summary(sections, lines)
        add_line(sections, '变更记录', f'- {today()} 程序{"写了" if first else "重新写了"}总结和概括标签（{"、".join(made["topics"])}）'
                 + (f'；聊到了但没记下的决定 {len(proposed)} 条，作为待确认交给你：{"、".join(proposed)}' if proposed else '') + f'。〔{source}〕')
        write_doc(path, meta, sections)
        write_summaries(home)
        commit_facts(home, f'需求总结：{meta.get("title")}', locked=True)
    return {'id': ident, 'topics': made['topics'], 'summary': made['summary'], 'proposed': proposed, 'tags': meta.get('tags') or [],
            'summarized': meta['summarized']}
