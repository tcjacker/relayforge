"""从立项对话读取明确授权，并把开发会话的委派请求交给应用。"""

import os
import re

from ..core.identity import SIG_ENV, ask_app, find_app, verify
from ..core.transcript import iter_messages

SUBAGENT = r'\s*(?:subagents?|子代理)'
ALLOW = re.compile(
    r'(?:(?:我)?(?:明确)?(?:授权|允许|同意)(?:这个|当前|本)?需求(?:的开发(?:会话|\s*Agent)?)?(?:使用|用)'
    r'|(?:这个|当前|本)需求(?:的开发会话|开发时)?可以(?:使用|用))' + SUBAGENT
    + r'|this requirement can use subagents?', re.I,
)
DENY = re.compile(
    r'撤回(?:本需求的)?' + SUBAGENT + r'\s*授权'
    r'|(?:这个|当前|本)需求(?:不再|不可以|不能)(?:使用|用)' + SUBAGENT
    + r'|revoke subagent authorization', re.I,
)
CHOICE = re.compile(
    r'[，,。]\s*(?:(?:请)?(?:改用|使用|用)|use)\s*(Codex|Claude Code)|[，,。]\s*(?:沿用设置|follow settings)', re.I,
)


def intake_decisions(transcript):
    decisions = []
    for number, entry in enumerate(iter_messages(transcript), 1):
        if entry['role'] != 'user':
            continue
        line = entry['text'].strip().rstrip('。！!.')
        if DENY.fullmatch(line):
            decisions.append({'number': number, 'authorized': False})
            continue
        match = ALLOW.match(line)
        if not match:
            continue
        tail = line[match.end():]
        choice = CHOICE.fullmatch(tail)
        if tail and not choice:
            continue
        decision = {'number': number, 'authorized': True}
        if choice:
            decision['cli'] = {'codex': 'codex', 'claude code': 'claude'}.get((choice[1] or '').casefold(), '')
        decisions.append(decision)
    return decisions


def request(action, **fields):
    token = os.environ.get(SIG_ENV)
    claims = verify(token)
    if not claims['verified'] or claims['kind'] != 'develop' or not claims['owner']:
        raise ValueError('只能在 RelayForge 应用打开的需求开发会话中委派 subagent。')
    _, socket = find_app()
    answer = ask_app(socket, {'op': 'subagent', 'token': token, 'action': action, **fields}, timeout=630) if socket else None
    if not answer or not answer.get('ok'):
        raise ValueError((answer or {}).get('error') or '应用没有返回 subagent 结果，请在应用里重新打开开发会话后重试。')
    return answer['result']
