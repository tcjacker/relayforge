"""后台记录：会话每说完一轮，应用（设置里打开「后台记录」时）调用 rf.py monitor record，这里把新说完的几轮整理成精简记录，
追加到本机的 ~/.relayforge/monitor/项目/events-年-月.jsonl。不进仓库，不调用模型。

为什么要边用边记：会话的原始记录会被 CLI 清掉，没完成的需求也不会另存，等想分析时已经找不到了。
一轮一行，记的是行为（用户说了什么、每次工具调用做了什么、成功失败还是被拦下、用时、token、等批准等了多久），
不记工具的输出；命令里像密钥的部分打码。之后让 Agent 读这个文件夹分析，字段的说明在同一个文件夹的 README.md 里。

  rf.py monitor record --session 会话ID --kind 种类 [--owner R-…] [--agent claude|codex] [--transcript 路径] [--waits JSON] [--closed]

--waits：应用看到的「停下来等你批准」，[{"at": 开始时间, "seconds": 等了多久}]，记到它落在的那一轮上（那一轮还没说完就先存着）。
--closed：会话关掉了，最后一轮没说完也照样记下（标 done: false）。
"""
import datetime as dt
import fcntl
import json
import os
from contextlib import contextmanager

from ..core.common import main_checkout, project_key, relayforge_home
from .monitor import KIND_WORDS, _analyze
from .record import stable_tag
from .archive import find_archived, find_original
from .steps import SECRETISH

__all__ = ['monitor_dir', 'monitor_record', 'RECORD_VERSION']

RECORD_VERSION = 1
SAID_CAP = 300          # 用户的话、AI 回复的开头各留多少字
CALLS_CAP = 200         # 一轮最多记多少次工具调用（再多的只记个数）


def monitor_dir(home):
    return relayforge_home() / 'monitor' / project_key(main_checkout(home))


@contextmanager
def _locked(place):
    """Several sessions can finish a turn at once: one writer at a time for the state file and the log."""
    place.mkdir(parents=True, exist_ok=True)
    with open(place / '.lock', 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read_state(place):
    try:
        data = json.loads((place / 'state.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(place, data):
    temp = place / 'state.json.tmp'
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    os.replace(temp, place / 'state.json')


def _clip(text, cap=SAID_CAP):
    text = SECRETISH.sub(lambda m: m.group(1) + '***', ' '.join(str(text or '').split()))
    return text if len(text) <= cap else text[:cap] + '…'


def _when(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def _within(at, start, end):
    """Whether a wait that began at `at` falls in a turn (end None: the turn is still open)."""
    a, s, e = _when(at), _when(start), _when(end) if end else None
    return bool(a and s and a >= s and (e is None or a <= e))


def _transcript(home, uuid, given):
    if given and os.path.isfile(given):
        return given
    found = find_original(uuid)
    return str(found['main']) if found else (str(find_archived(uuid, home) or '') or None)


def monitor_record(root, session, kind='chat', owner=None, agent=None, transcript=None, waits=(), closed=False):
    """Append the turns this session finished since the last call. Returns {written, file, pending_waits}."""
    home = main_checkout(root)
    if not session:
        raise ValueError('用法：monitor record --session 会话ID --kind 种类')
    if kind not in KIND_WORDS:
        raise ValueError(f'不认识的会话种类：{kind}')
    place = monitor_dir(home)
    with _locked(place):
        state = _read_state(place)
        mine = state.setdefault(str(session), {'recorded': 0, 'waits': []})
        mine['waits'] = (mine.get('waits') or []) + [w for w in waits or [] if isinstance(w, dict) and w.get('at')]
        path = _transcript(home, session, transcript)
        rows = []
        if path:
            found = _analyze(home, path)
            tag = stable_tag(home, str(session))
            for t in found['turn_stats']:
                if not t['n'] or t['n'] <= mine['recorded'] or not (t['done'] or closed):
                    continue
                calls = [c for c in found['calls'] if c['turn'] == t['n']]
                own = [w for w in mine['waits'] if _within(w['at'], t['start'], None if not t['done'] else t['end'])]
                mine['waits'] = [w for w in mine['waits'] if w not in own]
                rows.append({
                    'v': RECORD_VERSION, 'recorded': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                    'project': home.name, 'session': tag, 'session_id': str(session), 'kind': kind, 'owner': owner or None, 'agent': agent or None,
                    'turn': t['n'], 'done': bool(t['done']), 'start': t['start'], 'end': t['end'], 'busy_seconds': t['busy'], 'idle_seconds': t['idle'],
                    'said': _clip(t['said']), 'reply': _clip(t['reply']), 'interrupts': t['interrupts'],
                    'tokens': {'read': t['read'], 'write': t['write']},
                    'approvals': [{'at': w['at'], 'seconds': int(w.get('seconds') or 0)} for w in own],
                    'stalls': [{k: v for k, v in s.items() if k != 'turn'} for s in found['stalls'] if s['turn'] == t['n']],
                    'calls_total': len(calls),
                    'calls': [{'tool': c['tool'], 'what': c['what'], 'detail': c['detail'], 'status': c['status'], 'why': c['why'] or None,
                               'rf': c['rf'], 'commit': c['commit'], 'files': c['files']} for c in calls[:CALLS_CAP]],
                })
                mine['recorded'] = t['n']
        out = place / f'events-{dt.date.today():%Y-%m}.jsonl'
        if rows:
            with open(out, 'a', encoding='utf-8') as f:
                f.write(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        state[str(session)] = mine
        _write_state(place, state)
        _guide(place)
    return {'written': len(rows), 'file': str(out), 'pending_waits': len(mine['waits'])}


def _guide(place):
    target = place / 'README.md'
    if target.is_file() and f'记录格式版本 {RECORD_VERSION}' in target.read_text(encoding='utf-8'):
        return
    target.write_text(GUIDE, encoding='utf-8')


GUIDE = f"""# RelayForge 后台记录

记录格式版本 {RECORD_VERSION}。这个文件夹由 RelayForge 自动写，设置里打开「后台记录」才会写。只在本机，不进仓库。

## 有什么

- `events-年-月.jsonl`：一行是某个会话说完的一轮，按时间追加。
- `state.json`：程序自己用的进度（每个会话记到第几轮、还没归到某一轮的「等批准」），分析时不用看。

## 一行里的字段

| 字段 | 意思 |
|---|---|
| `session` / `session_id` | 会话编号（S-…）和 CLI 的会话 ID。看全部步骤：`rf.py history session 会话编号 --steps`（原始记录还在时） |
| `kind` | 会话种类：{'、'.join(f'{k}（{v}）' for k, v in KIND_WORDS.items())} |
| `owner` | 属于哪个需求（R-…）、修复（F-…）或问题（I-…），日常聊天是 null |
| `agent` | claude 或 codex |
| `turn` | 第几轮（用户说一句话算一轮的开始；上下文压缩后的续接、Codex 的环境说明不算） |
| `done` | 这一轮是不是说完了；会话中途被关掉的最后一轮是 false |
| `start` / `end` | 这一轮开始和最后一个动静的时间 |
| `busy_seconds` / `idle_seconds` | 实际干活的秒数；中间 10 分钟以上没有任何动静的时间另算在 idle 里 |
| `said` / `reply` | 用户这句话、AI 最后回复的开头（截短，像密钥的打码） |
| `interrupts` | 这一轮用户打断了几次 |
| `tokens` | 这一轮读入和写出的 token |
| `approvals` | 这一轮停下来等你批准的几次：开始时间、等了多少秒 |
| `stalls` | 中间停住的地方：`seconds` 停了多久；`waiting` 为 true 是在等一次调用的结果（命令跑得久或挂住了、在等批准），false 是模型该出下一步却没动；`after` / `detail` 是停在哪一步 |
| `calls_total` / `calls` | 工具调用（最多列 {CALLS_CAP} 次）：`tool` 工具名，`what` 读 / 跑 / 改 / 找 / 记进展 / 子代理…，`detail` 文件或命令（截短），`status` ok / failed / blocked（被权限规则、rf.py 的用户关口或沙箱拦下）/ refused（用户拒绝）/ pending（到最后都没有结果），`why` 被拦下的原因，`rf` 这次调用真正执行了的 rf.py 命令，`commit` 是不是 git commit，`files` 改了哪些文件 |

## 分析时可以看什么

- 指令遵循：每种会话的开场白在 RelayForge 的 `app/src/main/prompts.js`。比如开发会话应该先读需求文件、动手前 `note plan`、边做边记 `note`、改了代码就提交、不运行 `req done`、不手工改 `.relayforge/`；
  测试会话要用 `test run` 跑、用 `req verdict` 交回；修复会话先复现、用 `issue handback` 交回；排查会话只读、用 `issue finding` 记结论。
  `rf.py monitor`（不带 record）对还在的原始记录按同样的规矩算了一遍，规矩列在 `sessions/monitor.py` 的 RULES。
- 工具：哪些工具、哪些 rf.py 命令（`calls[].rf`）从来没用过；推荐了却没人用的（why、history、issue new …）。
- 打转：同一个 `detail` 连续 failed；blocked 之后同一个目标又试；一轮里同一个文件改很多次；`stalls`、`approvals` 里很长的等待；`busy_seconds` 很长的一轮；`interrupts`。
- 按 `kind`、`agent`、`owner`、时间分开比：Codex 和 Claude Code 谁更容易被拦、哪种会话最常打转、改了开场白以后有没有变好。
"""
