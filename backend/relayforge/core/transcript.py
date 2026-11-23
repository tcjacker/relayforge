"""读 Claude Code 或 Codex 的聊天记录（.jsonl）。"""

import datetime as dt
import gzip
import json
import re


def clean(text):
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.S)
    text = re.sub(r'<task-notification>.*?</task-notification>', '', text, flags=re.S)
    return text.strip()


def open_text(path):
    """A transcript, plain or gzip-compressed (the copies kept on this machine, see archive.py)."""
    if str(path).endswith('.gz'):
        return gzip.open(path, 'rt', encoding='utf-8', errors='replace')
    return open(path, encoding='utf-8', errors='replace')


def read_transcript(path):
    """Return the user and assistant text messages of a session, in order."""
    return list(iter_messages(path))


def count_messages(path):
    """How many messages read_transcript would return, without keeping their text (a session file can be tens of MB)."""
    return sum(1 for _ in iter_messages(path))


def iter_messages(path):
    """The messages of read_transcript one at a time: the file is read line by line, nothing is kept."""
    try:
        lines = open_text(path)
    except OSError:
        return
    with lines:
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get('type') == 'response_item':
                payload = obj.get('payload') or {}
                if payload.get('type') == 'message' and payload.get('role') in ('user', 'assistant'):
                    obj = {'type': payload['role'], 'message': {'content': [
                        {'type': 'text', 'text': block.get('text', '')} for block in payload.get('content', [])
                        if isinstance(block, dict) and block.get('type') in ('input_text', 'output_text')
                    ]}, 'timestamp': obj.get('timestamp')}
            if not isinstance(obj, dict) or obj.get('type') not in ('user', 'assistant') or obj.get('isSidechain'):
                continue
            content = (obj.get('message') or {}).get('content')
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
            else:
                continue
            text = clean(text)
            if text:
                entry = {'role': obj['type'], 'text': text}
                if isinstance(obj.get('timestamp'), str):
                    entry['ts'] = obj['timestamp']
                yield entry


# ---------- 按 Claude Code 的格式逐条读（停下来时的检查用） ----------
# 停下来时的几项检查（facts/notes.turn_weight、issues/probes.turn_facts、testsets/checks.turn_tests）按 Claude Code 的记录格式写：
# 用户说话开始新的一轮，助手的 tool_use 块是工具调用。Codex 的记录换成同样的形状交给它们：
#   - 工具调用：新版写在一段 JS 里（custom_tool_call「exec」：tools.exec_command({cmd:"…"})、tools.apply_patch(`…`)），
#     旧版是 function_call（exec_command / shell，参数是 JSON）。跑命令的记成 Bash（input.command），apply_patch 按补丁里的
#     每个文件记成一次 Edit（input.file_path，相对路径按这一轮的目录补全），问用户的工具记成 AskUserQuestion。
#   - 程序插进来的「用户消息」不算新的一轮：环境说明、规则说明，以及钩子让它接着做时交回的 <hook_prompt …>。
CODEX_SYNTHETIC = re.compile(r'^\s*<(?:environment_context|user_instructions|permissions instructions|hook_prompt|turn_aborted)\b|^\s*# AGENTS\.md instructions')
_JS_STR = r'"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`|\'(?:[^\'\\]|\\.)*\''  # JS 的一个字符串字面量
JS_CMD = re.compile(r'\bexec_command\(\{(?:[^"`\'{}]|' + _JS_STR + r')*?\bcmd\s*:\s*(?:"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`|\'((?:[^\'\\]|\\.)*)\')', re.S)  # cmd 前面可以有别的参数（workdir:"…"）
JS_PATCH = re.compile(r'\bapply_patch\(\s*(?:`((?:[^`\\]|\\.)*)`|"((?:[^"\\]|\\.)*)")', re.S)  # 反引号或双引号的字符串
PATCH_FILE = re.compile(r'^\*\*\* (?:Update File|Add File|Delete File|Move to): (.+?)\s*$', re.M)
SHELL_TOOLS = ('exec_command', 'shell', 'shell_command', 'local_shell', 'container.exec')
ASK_TOOLS = ('request_user_input', 'ask_user', 'AskUserQuestion')


def _js_string(raw):
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def _patch_edits(patch, cwd):
    files = PATCH_FILE.findall(str(patch or ''))
    return [{'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': f if f.startswith('/') or not cwd else f'{cwd.rstrip("/")}/{f}'}}
            for f in files] or [{'type': 'tool_use', 'name': 'Edit', 'input': {}}]


def codex_script_tools(code, cwd=''):
    """从 Codex 一次脚本调用里按出现顺序认出命令、改动和用户提问。"""
    calls = []
    for match in JS_CMD.finditer(code):
        double, tick, single = match.groups()
        command = _js_string(double) if double is not None else (tick if tick is not None else single)
        calls.append((match.start(), [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}]))
    for match in JS_PATCH.finditer(code):
        tick, quoted = match.groups()
        patch = tick.replace('\\`', '`') if tick is not None else _js_string(quoted)
        calls.append((match.start(), _patch_edits(patch, cwd)))
    ask = re.search(r'\b(?:' + '|'.join(ASK_TOOLS) + r')\w*\(', code)
    if ask:
        calls.append((ask.start(), [{'type': 'tool_use', 'name': 'AskUserQuestion', 'input': {}}]))
    return [block for _, blocks in sorted(calls, key=lambda c: c[0]) for block in blocks]


def _codex_calls(payload, cwd):
    """The tool_use blocks one Codex tool call stands for."""
    kind, name = payload.get('type'), str(payload.get('name') or '')
    if kind == 'custom_tool_call' and name == 'exec':  # 一段 JS，里面可能有好几次调用
        return codex_script_tools(str(payload.get('input') or ''), cwd) or [{'type': 'tool_use', 'name': 'exec', 'input': {}}]
    if name == 'apply_patch' or kind == 'custom_tool_call' and 'Begin Patch' in str(payload.get('input') or ''):
        return _patch_edits(payload.get('input') or payload.get('arguments'), cwd)
    if kind == 'local_shell_call':
        action = payload.get('action') if isinstance(payload.get('action'), dict) else {}
        cmd = action.get('command')
        return [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': cmd[-1] if isinstance(cmd, list) and cmd else str(cmd or '')}}]
    args = payload.get('arguments')
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    args = args if isinstance(args, dict) else {}
    if name in SHELL_TOOLS:
        cmd = args.get('cmd', args.get('command'))
        if isinstance(cmd, list):
            cmd = cmd[-1] if len(cmd) >= 3 and cmd[0] in ('bash', 'sh', 'zsh') and cmd[1] in ('-lc', '-c') else ' '.join(map(str, cmd))
        return [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': str(cmd or '')}}]
    if name.startswith(ASK_TOOLS):
        return [{'type': 'tool_use', 'name': 'AskUserQuestion', 'input': args}]
    return [{'type': 'tool_use', 'name': name or 'tool', 'input': args}]


def claude_shaped(path):
    """Each entry of a Claude Code or Codex transcript as Claude Code writes it ({'type': 'user'|'assistant', 'message': {'content': …}}),
    for the stop checks. Claude Code's lines pass through unchanged; see the section comment for Codex."""
    try:
        lines = open_text(path)
    except OSError:
        return
    cwd = ''
    with lines:
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get('type')
            if kind in ('user', 'assistant'):
                yield obj
                continue
            p = obj.get('payload') if isinstance(obj.get('payload'), dict) else {}
            if kind in ('session_meta', 'turn_context'):
                cwd = str(p.get('cwd') or cwd)
            if kind != 'response_item':
                continue
            if p.get('type') == 'message' and p.get('role') in ('user', 'assistant'):
                text = '\n'.join(b.get('text', '') for b in p.get('content', []) if isinstance(b, dict) and b.get('type') in ('input_text', 'output_text'))
                if p['role'] == 'user' and CODEX_SYNTHETIC.match(text):
                    continue
                yield {'type': p['role'], 'message': {'content': [{'type': 'text', 'text': text}]}}
            elif p.get('type') in ('function_call', 'custom_tool_call', 'local_shell_call'):
                yield {'type': 'assistant', 'message': {'content': _codex_calls(p, cwd)}}


def message_time(messages, at):
    """Local date and time of message number `at` (counted from 1), or '' when unknown."""
    try:
        stamp = dt.datetime.fromisoformat(messages[at - 1]['ts'].replace('Z', '+00:00')) if at >= 1 else None
    except (IndexError, KeyError, ValueError, TypeError):
        return ''
    return stamp.astimezone().strftime('%Y-%m-%d %H:%M') if stamp else ''


def session_tag(session_id, when=None):
    day = (when or dt.date.today()).strftime('%Y%m%d')
    return f'S-{day}-{re.sub(r"[^0-9a-zA-Z]", "", session_id or "none")[:4]}'
