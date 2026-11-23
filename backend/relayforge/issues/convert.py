"""问题和需求互转：问题其实是想改产品时转成需求草稿；需求里发现的问题记成一条问题。

分层见 tests/test_layers.py。
"""
import shutil

from ..core.common import commit_facts, drafts_dir, PLACEHOLDER, REQ_HEADS, today
from ..core.documents import add_line, live_items, read_doc, write_doc
from .record import (
    issue_in_state, append_entry, change, issue_files, ISSUE_KINDS, issue_lock, issue_new, issue_show, issues_home, read_thread,
    save,
)
from ..requirements.owners import issue_dir, num
from ..requirements.record import find_requirement, req_new

__all__ = ['issue_to_req', 'issue_from_req']


def issue_to_req(root, ident):
    """The issue is really a wish for the product to change: a draft requirement takes its description, screenshots and
    thread; the issue goes to 不处理, marked as turned into that requirement."""
    home = issues_home(root)
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, ident, ('todo',), '转为需求')
        thread = read_thread(home, meta['id'])
    made = req_new(home, meta.get('title') or meta['id'])
    req_path = home / made['file']  # 需求文件也在主目录里
    req_meta, req_sections = read_doc(req_path)
    by_user = meta.get('source') == '你'
    for line in live_items(sections.get('现象')):
        add_line(req_sections, '需求说明', f'- {"" if by_user else "〔待确认〕"}{line}〔{meta["id"]}#1〕')
    for e in thread:
        if e.get('t') == 'user' and (e.get('text') or '').strip():
            add_line(req_sections, '需求说明', f'- {" ".join(e["text"].split())}〔{meta["id"]}#{e["n"]}〕')
    if meta.get('feature'):
        add_line(req_sections, '要改的功能点', f'- {meta["feature"]}〔{meta["id"]}#1〕')
    # 截图和完整的对话放进这个需求的草稿目录，应用打开立项会话时交给 AI
    drafts = drafts_dir(made['id'])
    drafts.mkdir(parents=True, exist_ok=True)
    shots = issue_dir(home) / 'shots' / meta['id']
    images = []
    for e in thread:
        for s in e.get('shots') or []:
            if (shots / s).is_file():
                shutil.copyfile(shots / s, drafts / f'{meta["id"]}-{s}')
                images.append(str(drafts / f'{meta["id"]}-{s}'))
    talk = [f'# 问题 {num(meta["id"])} {meta.get("title")}', '', f'来源：{meta.get("source")}', '', '## 现象'] + [f'- {x}' for x in live_items(sections.get('现象'))] + ['', '## 对话']
    for e in thread:
        if e.get('t') in ('report', 'user'):
            talk.append(f'- {e["at"]} {e.get("who")}：{e.get("text") or ""}')
        elif e.get('t') == 'judge':
            talk.append(f'- {e["at"]} AI 初判：{ISSUE_KINDS.get(e.get("kind"))}。{e.get("reason")}' + (f' 用法：{e["howto"]}' if e.get('howto') else '') + (f' 问：{e["question"]}' if e.get('question') else ''))
        elif e.get('t') == 'sys':
            talk.append(f'- {e["at"]} {e.get("text")}')
    talk_file = drafts / f'问题-{meta["id"]}.md'
    talk_file.write_text('\n'.join(talk) + '\n', encoding='utf-8')
    add_line(req_sections, '变更记录', f'- {today()} 由问题 {num(meta["id"])} 转来，带来了描述、{len(images)} 张截图和 {len(thread)} 条对话。〔{meta["id"]}#{len(thread) + 1}〕')
    req_meta.update(source_issue=meta['id'], updated=today())  # 需求面板上「查看来源问题」
    write_doc(req_path, req_meta, {h: req_sections.get(h, []) for h in REQ_HEADS})
    with issue_lock(home):
        path, meta, sections, state = issue_in_state(home, meta['id'])
        append_entry(home, meta['id'], {'t': 'sys', 'who': '你', 'text': f'转为需求 · 草稿 {made["id"]}', 'requirement': made['id']})
        entry = append_entry(home, meta['id'], {'t': 'sys', 'who': '程序', 'text': '问题已关闭，后续在需求里跟进', 'close': True})
        meta.update(state='closed', closed_as='requirement', requirement=made['id'], deferred=None)
        change(sections, f'转为需求 {made["id"]}，问题关闭。', f'{meta["id"]}#{entry["n"]}')
        save(path, meta, sections)
        commit_facts(home, f'问题 {num(meta["id"])}：转为需求 {made["id"]}')
    return {'issue': issue_show(home, meta['id']), 'requirement': find_requirement(home, made['id']), 'images': images, 'thread_file': str(talk_file)}


def issue_from_req(root, req_id, feature=None):
    """A draft requirement turns out to be a defect, not a change: record it as an issue through the same path as any
    other (source 你), its 现象 being the requirement's 需求说明 with their own marks and sources. The requirement stays as it
    is and records the link (converted_issue, a line in its 变更记录). Asking twice gives the same issue."""
    home = issues_home(root)
    req = find_requirement(home, req_id)
    known = next((m for _, m, _ in issue_files(home) if m.get('source_requirement') == req['id']), None)
    if known:
        return issue_show(home, known['id'])
    if req['state'] != 'draft' or not req.get('worktree'):
        raise ValueError('只有立项中的需求可以转为问题。')
    path = home / req['file']  # 需求文件也在主目录里
    meta, sections = read_doc(path)
    said = [l for l in sections.get('需求说明', []) if l != PLACEHOLDER]
    desc = '\n'.join(l[2:] if l.startswith('- ') else l for l in said)
    made = issue_new(home, req['title'], desc, '你', feature=feature or None, statements=said or None,
                     extra={'source_requirement': req['id']}, note=f'由立项中的需求 {req["id"]} 转来，需求说明和讨论仍留在需求里。')
    meta.update(converted_issue=made['id'], updated=today())
    add_line(sections, '变更记录', f'- {today()} 转为问题 {made["num"]}（{made["id"]}）；原来的说明和讨论仍留在这个需求里。〔{made["id"]}#1〕')
    write_doc(path, meta, {h: sections.get(h, []) for h in REQ_HEADS})
    commit_facts(home, f'需求转为问题 {made["num"]}：{req["title"]}')
    return issue_show(home, made['id'])
