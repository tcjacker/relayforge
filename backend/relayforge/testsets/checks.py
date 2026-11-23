"""停下来时的检查：会话写了测试文件、跑了测试，却没把它做成测试集（或没用测试集跑）时，停下来之前提醒它。

测试集本身在 testsets.py。分层见 tests/test_layers.py。
"""
import json
import os
import re
from pathlib import Path

from ..core.transcript import claude_shaped
from ..core.common import DIR, find_root, GUARD, main_checkout, SCRIPT
from ..requirements.owners import here_owner
from .store import all_testsets, BRIDGE_MARK

__all__ = ['test_stop_block']


TEST_FILE = re.compile(r'(^|/)(tests?|__tests__|specs?)/|(^|/)(test_[^/]+\.py|[^/]+_test\.(py|go)|[^/]+\.(test|spec)\.[cm]?[jt]sx?|[^/]+Tests?\.(java|kt))$')


TEST_RUN = re.compile(r'rf\.py["\']?\s+test\b')


NOTE_RUN = re.compile(r'rf\.py["\']?\s+note\b')


def looks_like_test(rel):
    rel = rel.replace('\\', '/')
    if rel.startswith(DIR + '/') or rel.startswith('.worktrees/'):
        return False
    if not re.search(r'\.(py|[cm]?[jt]sx?|java|kt|go|rb|rs)$', rel):
        return False
    return bool(TEST_FILE.search(rel))


def turn_tests(transcript, place):
    """Since the user last spoke: the files the assistant wrote with an edit tool (relative to `place`), whether it ran
    `rf.py test`, and the text of any `rf.py note` it ran."""
    written, used, notes = [], False, []
    for obj in claude_shaped(transcript):  # Claude Code 和 Codex 的记录都按 Claude Code 的格式读（core/transcript.py）
        if obj.get('isSidechain'):
            continue
        content = (obj.get('message') or {}).get('content')
        blocks = content if isinstance(content, list) else []
        if obj.get('type') == 'user' and (isinstance(content, str) or any(isinstance(b, dict) and b.get('type') == 'text' for b in blocks)):
            written, used, notes = [], False, []
        elif obj.get('type') == 'assistant':
            for b in blocks:
                if not isinstance(b, dict) or b.get('type') != 'tool_use':
                    continue
                data = b.get('input') or {}
                if b.get('name') in ('Edit', 'Write', 'MultiEdit'):
                    target = str(data.get('file_path') or '')
                    if target:
                        try:
                            written.append(Path(target).resolve().relative_to(Path(place).resolve()).as_posix())
                        except ValueError:
                            pass
                elif b.get('name') == 'Bash':
                    command = str(data.get('command') or '')
                    used = used or bool(TEST_RUN.search(command))
                    if NOTE_RUN.search(command):
                        notes.append(command)
    return list(dict.fromkeys(written)), used, notes


def test_stop_block(data):
    """A development or fix session that wrote ordinary test code this turn, without using test sets (rf.py test) and
    without a note saying why, must not stop yet. Conservative: only in a requirement's or fix's working directory, only
    files written with the edit tools, and the access code and bridge files of test sets do not count."""
    if data.get('stop_hook_active') or os.environ.get(GUARD):
        return None
    root = find_root(data.get('cwd'))
    transcript = data.get('transcript_path') or ''
    if not root or not transcript:
        return None
    place = Path(root).resolve()
    home = main_checkout(place)
    if place == home or not here_owner(place):
        return None
    written, used, notes = turn_tests(transcript, place)
    if used or any('测试' in n or 'test' in n.lower() for n in notes):
        return None
    entries = {str(ts.get('entry') or '') for ts in all_testsets(home, shelved=True)}
    hits = []
    for rel in written:
        if rel in entries or not looks_like_test(rel):
            continue
        try:
            if BRIDGE_MARK in (place / rel).read_text(encoding='utf-8', errors='replace')[:400]:
                continue
        except OSError:
            continue  # written and then removed again
        hits.append(rel)
    if not hits:
        return None
    cmd = f'python3 {SCRIPT} test'
    return (f'这一轮写了测试代码（{"、".join(hits[:5])}{"等" if len(hits) > 5 else ""}），但没有用测试集。新测试一律写成测试集：数据进 JSON，接入代码只读数据和调用：'
            f'{cmd} new "测什么行为" --feature RF-xxx --entry 接入代码路径 → {cmd} add 测试集编号 --file 数据.json → {cmd} run 测试集编号（写法见 .relayforge/FORMAT.md 的「测试集」）。'
            f'这几个文件实在没法写成测试集（比如要测界面、并发、时序），就用 python3 {SCRIPT} note doing "这些测试没写成测试集的原因：…" 说明原因。')
