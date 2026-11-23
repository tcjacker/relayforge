"""在后台调用无界面的模型（claude -p，或设置里换成 codex exec），取回它打印的 JSON。

整理会话（extract）、判断问题（issues）、读代码生成大图草稿（bootstrap）、写总结（summary）都用它。分层见 tests/test_layers.py。
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .common import DIR, GUARD
from . import procs
from .identity import SIG_ENV
from .language import display_text, reply_language
from .trust import scrubbed_environ

__all__ = ['extractor_command', 'run_model']


def extractor_command(root):
    override = os.environ.get('RELAYFORGE_EXTRACTOR')
    if override:
        return json.loads(override)
    config = root / DIR / 'config.json'
    if config.exists():
        try:
            return json.loads(config.read_text(encoding='utf-8')).get('extractor') or ['claude', '-p']
        except ValueError:
            pass
    return ['claude', '-p']


OUTPUT_LIMIT = 16 << 20


def run_model(root, instruction, payload, cwd=None, permissions=None, timeout=900, sandbox=None):
    """Send `payload` to the headless model and return the JSON object it prints.

    By default it runs in the temporary directory with no tools granted. `permissions` ({'allow': [...], 'deny': [...]},
    Claude Code's rule syntax) lets it work in `cwd`, for a background investigation that reads code and reruns tests;
    such a run never gets the app's marker, like any other command started on the user's behalf. `sandbox` (Claude Code's
    sandbox settings) puts its shell commands in the system sandbox. Codex is started by the app with its own read-only
    sandbox, so the rules only apply to Claude Code."""
    # 应用的标记、会话令牌一律不交给模型：模型（比如 Codex 的只读沙箱）读得到自己的环境变量，以前只有带 permissions 时才去掉
    env = scrubbed_environ({GUARD: '1'})
    env.pop(SIG_ENV, None)
    language = reply_language()
    if language:
        instruction += '\n\n' + language
    command = extractor_command(root)
    if Path(command[0]).name in ('codex', 'codex.exe'):
        command = command + ['-']
        payload = instruction + '\n\n' + payload
    else:
        if permissions:  # Claude Code（应用找到的可执行文件不一定叫 claude，比如桌面版里带的）
            settings = dict({'permissions': permissions}, **({'sandbox': sandbox} if sandbox else {}))
            command = command + ['--settings', json.dumps(settings, ensure_ascii=False)]
        command = command + [instruction]
    try:  # 超时连同模型开的 shell 和它们的子进程一起停（复查的 Bash 在自己的进程组里，只杀模型本身会留下它们）
        code, out, err = procs.run(command, input=payload, text=True, timeout=timeout, env=env, cwd=str(cwd or tempfile.gettempdir()),
                                   limit=OUTPUT_LIMIT)  # 回答是一个 JSON 对象，正常几十 KB；失控时也只读回这么多
    except OSError as e:
        raise ValueError(display_text(f'启动不了模型命令 {command[0]}：{e}',
                                      f'Could not start the model command {command[0]}: {e}')) from None
    if code is None:
        raise ValueError(display_text(f'模型 {timeout // 60} 分钟内没有回答，已停止。',
                                      f'The model did not reply within {timeout // 60} minutes. The run was stopped.'))
    done = subprocess.CompletedProcess(command, code, out, err)
    start, end = out.find('{'), out.rfind('}')
    if start < 0 or end < start:
        raise ValueError(f'extractor returned no JSON (exit {done.returncode}): {out[:200]!r} {done.stderr[:200]!r}')
    return json.loads(out[start:end + 1])
