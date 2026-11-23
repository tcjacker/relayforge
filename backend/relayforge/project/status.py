"""每个会话在干什么，给应用看：rf.py hook event / sweep 把钩子事件判断成「正在干活 / 等你 / 没事」。"""

import json
import os
import re
import sys
import time
from pathlib import Path
try:
    import fcntl
except ImportError:  # not on this platform; the state file is then written without a lock
    fcntl = None

from ..core.approvals import decision_output, headless, wait_for_decision
from ..core.inbox import WAKE, asks_user, inbox_deliver
from ..core.language import reply_language
from ..core.common import GUARD, commit_facts, find_root, main_checkout
from ..core.trust import sandboxed
from ..sessions.record import stable_tag
from ..facts.notes import stop_block
from ..issues.fixes import fix_stop_block
from ..issues.probes import probe_stop_block
from ..testsets.checks import test_stop_block
from ..requirements.testing import test_session_stop_block, turn_snapshot
from ..facts.facts import write_summaries


# One entry point (`rf.py hook event`) serves every hook the app registers. It keeps a small state file next to the
# events file ($RELAYFORGE_EVENTS + '.state') and appends already-interpreted lines to the events file:
#   {"session_id", "status": "running" | "waiting" | "idle", "kind": "reply" | "answer" | "approve", "text", "at"}
# "idle" only means "stop showing it as running" (no notification). The app reads these lines and never looks at raw hooks.

STATUS_GRACE = 180       # seconds the tracked subagents must stay gone before a held Stop is announced
STATUS_TTL = 4 * 3600    # a subagent that never reported stopping is forgotten after this long
STATUS_STALE = 30 * 60   # a running session with no hook event at all for this long is shown as stopped (e.g. the user pressed Esc)
STATUS_KEEP = 24 * 3600  # sessions untouched for this long are dropped from the state file
APPROVE_ECHO = 30        # a permission Notification this soon after a PermissionRequest is the same thing
RESUMING_PROMPT = re.compile(r'^\s*<task-notification')  # Claude Code wakes the main agent with this when a background subagent ends


def blank_status(now):
    return {'phase': 'idle', 'agents': {}, 'done': [], 'held': None, 'empty_since': None, 'approve_at': None, 'at': now, 'due': None}


def finished_event(session, text, now):
    said = str(text or '').strip()
    tail = [ln for ln in said.split('\n') if ln.strip()][-1:] or ['']
    kind = 'answer' if re.search(r'[?？]', tail[0]) else 'reply'
    shown = re.sub(r'[*_`#>]', '', tail[0] or '回复完了')[:120]
    return {'session_id': session, 'status': 'waiting', 'kind': kind, 'text': shown, 'at': now}


def status_due(s):
    """When this session next needs a look from sweep (None: never, only a hook event can change it)."""
    if s['phase'] != 'running':
        return None
    if s['held']:
        return min(s['agents'].values()) + STATUS_TTL if s['agents'] else (s['empty_since'] or s['at']) + STATUS_GRACE
    return None if s['agents'] else s['at'] + STATUS_STALE


def status_sweep(session, s, now):
    """The time-based part: forget subagents that never stopped, announce a held Stop, stop showing a silent session as running."""
    events = []
    gone = [a for a, t in s['agents'].items() if now - t >= STATUS_TTL]
    for a in gone:
        del s['agents'][a]
    if s['phase'] == 'running':
        if s['held'] and not s['agents']:
            if s['empty_since'] is None:
                s['empty_since'] = now
            if now - s['empty_since'] >= STATUS_GRACE:
                events.append(finished_event(session, s['held'].get('text'), now))
                s.update(phase='idle', held=None, empty_since=None, approve_at=None)
        elif not s['held'] and not s['agents'] and now - s['at'] >= STATUS_STALE:
            events.append({'session_id': session, 'status': 'idle', 'at': now})
            s.update(phase='idle', approve_at=None)
    s['due'] = status_due(s)
    return events


def status_apply(session, s, data, now):
    """Fold one hook payload into this session's state; returns the lines for the app."""
    name = data.get('hook_event_name')
    agent = data.get('agent_id') if isinstance(data.get('agent_id'), str) else ''
    s['at'] = now
    if name == 'UserPromptSubmit':
        if RESUMING_PROMPT.match(str(data.get('prompt') or '')) and s['phase'] == 'running':
            s.update(held=None, empty_since=None)  # the main agent is back; other subagents keep running
        else:
            s.update(blank_status(now))
        s['phase'] = 'running'
        return [{'session_id': session, 'status': 'running', 'at': now}]
    if name == 'SubagentStart':
        # Only a working session has subagents of its own; helpers started after it finished are not tracked.
        if s['phase'] == 'running' and agent and agent not in s['done'] and agent not in s['agents']:
            s['agents'][agent] = now
            s['empty_since'] = None
        return []
    if name == 'SubagentStop':
        if s['phase'] == 'running' and agent:
            s['agents'].pop(agent, None)
            s['done'] = (s['done'] + [agent])[-200:]
            if s['held'] and not s['agents']:
                s['empty_since'] = now  # the main agent is normally woken now and its own Stop follows
        return []
    if name == 'Stop':
        tasks = data.get('background_tasks')
        if isinstance(tasks, list):  # Claude Code says itself which subagents are still running
            alive = {t.get('id') for t in tasks if isinstance(t, dict) and t.get('type') == 'subagent' and t.get('status') == 'running' and isinstance(t.get('id'), str)}
            s['agents'] = {a: s['agents'].get(a, now) for a in alive}
        for a in [a for a, t in s['agents'].items() if now - t >= STATUS_TTL]:
            del s['agents'][a]
        s['approve_at'] = None
        if s['agents']:
            s.update(phase='running', held={'text': data.get('last_assistant_message') or ''}, empty_since=None)
            return []
        s.update(phase='idle', held=None, empty_since=None)
        return [finished_event(session, data.get('last_assistant_message'), now)]
    approve = name == 'PermissionRequest' or (name == 'Notification' and data.get('notification_type') == 'permission_prompt')
    if approve:
        if name == 'Notification' and s['approve_at'] is not None and now - s['approve_at'] < APPROVE_ECHO:
            return []
        s.update(phase='running', approve_at=now)
        tool = data.get('tool_name')
        return [{'session_id': session, 'status': 'waiting', 'kind': 'approve', 'text': f'要用 {tool}，等你批准' if isinstance(tool, str) and tool else '要做一个操作，等你批准', 'at': now}]
    if name == 'Interrupt':  # Codex：用户按 Esc 打断了这一轮（Claude Code 没有这个事件，打断以后靠 STATUS_STALE 了结）
        s.update(phase='idle', held=None, empty_since=None, approve_at=None)
        return [{'session_id': session, 'status': 'idle', 'at': now}]
    if name in ('PostToolUse', 'PostToolUseFailure') and s['approve_at'] is not None:
        s['approve_at'] = None  # a tool ran, so the question was answered
        return [{'session_id': session, 'status': 'running', 'at': now}]
    return []


def status_path(events):
    return Path(str(events) + '.state')


def read_status(events):
    try:
        data = json.loads(status_path(events).read_text(encoding='utf-8'))
        return data['sessions'] if isinstance(data.get('sessions'), dict) else {}
    except (OSError, ValueError, KeyError, AttributeError):
        return {}


def status_update(events, data=None, now=None):
    """Apply one hook payload (or, with none, only the time-based sweep) to the state next to `events`."""
    now = time.time() if now is None else now
    sessions, lines = None, []
    lock = open(str(events) + '.lock', 'a')
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        sessions = {k: v for k, v in read_status(events).items() if isinstance(v, dict) and now - v.get('at', 0) < STATUS_KEEP}
        for sid, s in sessions.items():
            lines += status_sweep(sid, s, now)
        sid = data.get('session_id') if data else None
        if isinstance(sid, str) and sid:
            s = sessions.setdefault(sid, blank_status(now))
            lines += status_apply(sid, s, data, now)
            s['due'] = status_due(s)
        tmp = status_path(events).with_suffix('.tmp')
        tmp.write_text(json.dumps({'sessions': sessions}, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, status_path(events))
        if lines:
            with open(events, 'a', encoding='utf-8') as f:
                f.write(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in lines))
    finally:
        lock.close()
    return lines


def commit_session_facts(data):
    """A session stopping is one of the moments the program commits the facts (in the main checkout, .relayforge/ only)."""
    try:
        root = find_root(data.get('cwd'))
        if root:
            home = main_checkout(root)
            write_summaries(home)
            sid = str(data.get('session_id') or '')
            commit_facts(home, f'记录会话进展（{stable_tag(home, sid)}）' if sid else '记录会话进展')
    except Exception:
        pass  # never in the way of the session


def hook_event(data, now=None):
    """The one hook the app registers for every event. Never fails: the only thing it may do to the session is the note check."""
    try:
        if os.environ.get(GUARD):
            return 0
        if data.get('hook_event_name') == 'Stop':
            # 几道检查一起做、一起说：被拦下一次以后，下一次停下来时 Claude Code 会带上 stop_hook_active，几道都不再拦
            reasons = []
            for check in (stop_block, fix_stop_block, probe_stop_block, test_stop_block, test_session_stop_block):
                try:
                    reason = check(data)
                except Exception:
                    reason = None
                if reason:
                    reasons.append(reason)
            # 会话收件箱（core/inbox.py）：程序排给这个会话的话，在它这一轮说完时交过去；最后一句在问用户问题时不交，等用户回答完
            if not asks_user(data.get('last_assistant_message')):
                note = inbox_deliver(str(data.get('session_id') or ''), 'stop')
                if note:
                    reasons.append(note)
            if reasons:
                language = reply_language() if os.environ.get('RELAYFORGE_LANGUAGE') else ''
                print(json.dumps({'decision': 'block', 'reason': '\n'.join(filter(None, [*reasons, language]))},
                                 ensure_ascii=False))
                return 0
            commit_session_facts(data)
        elif data.get('hook_event_name') == 'UserPromptSubmit':
            prompt = str(data.get('prompt') or '')
            context = []
            if prompt.lstrip().startswith(WAKE):
                # 正文通过补充上下文交付，唤醒句留在终端对话里。
                context.append(inbox_deliver(str(data.get('session_id') or ''), 'prompt') or '（程序消息已经交过了，没有新的。）')
            elif not RESUMING_PROMPT.match(prompt):
                try:
                    turn_snapshot(data)
                except Exception:
                    pass
            if os.environ.get('RELAYFORGE_LANGUAGE'):
                context.append(reply_language())
            if any(context):
                print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                                                         'additionalContext': '\n\n'.join(filter(None, context))}},
                                 ensure_ascii=False))
        events = os.environ.get('RELAYFORGE_EVENTS')
        if events:
            status_update(events, data, now)  # 先报「在等你批准」，应用据此提醒你
        if data.get('hook_event_name') == 'PermissionRequest' and headless():
            # 无界面运行的会话没有终端可以弹确认框：等你在应用里点批准或拒绝（core/approvals.py）
            print(json.dumps(decision_output(wait_for_decision(data)), ensure_ascii=False))
    except Exception:
        pass
    return 0


def codex_event(data, now=None):
    """rf.py hook codex for everything but PreToolUse (that one is core/guard.py's, without loading the program).
    Codex reports its own thread id; the app and the records know the session by the RelayForge id it put in the environment,
    so the payload is read as that session's. Not started by RelayForge (no id in the environment): nothing to do."""
    sid = os.environ.get('RELAYFORGE_SESSION_ID')
    if not isinstance(data, dict) or not sid:
        return 0
    return hook_event(dict(data, session_id=sid, codex_session_id=data.get('session_id')), now)


def hook_sweep(now=None):
    """Called by the app when read_status() says a session is due: only the time-based part."""
    try:
        events = os.environ.get('RELAYFORGE_EVENTS')
        if events:
            status_update(events, None, now)
    except Exception:
        pass
    return 0


# ---------- 会话没进沙箱 ----------

def report_unsandboxed(cmd, environ=None):
    """An AI session's own rf.py found itself outside the sandbox: the sandbox did not take (an old Claude Code that ignores the
    settings, an administrator's settings turning it off …), so nothing here stops the session from forging the app's marker or
    reading the secrets. Tell the app (a line in RELAYFORGE_EVENTS; attention.js warns you) and say so on stderr. True when told.
    Not a session's own command: the app's calls (no session token), hooks and what they start, commands the app runs for a
    session (RELAYFORGE_PROXIED). Only macOS can tell (sandboxed())."""
    env = os.environ if environ is None else environ
    sid, events = env.get('RELAYFORGE_SESSION_ID'), env.get('RELAYFORGE_EVENTS')
    if not (sid and events and env.get('RELAYFORGE_SESSION_SIG')) or cmd == 'hook' or env.get('RELAYFORGE_FROM_HOOK') \
            or env.get('RELAYFORGE_PROXIED') or sys.platform != 'darwin' or sandboxed():
        return False
    try:
        with open(events, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'session_id': sid, 'status': 'unsandboxed', 'at': time.time()}) + '\n')
    except OSError:
        pass
    print('注意：这条命令没有在系统的沙箱里运行，RelayForge 挡不住它冒充你的操作、读保密值。不要申请在沙箱外执行命令；'
          '要做的事在沙箱里做不了，就停下来告诉用户。', file=sys.stderr)
    return True
