#!/usr/bin/env python3
"""RelayForge capture: keep a project's current facts outside the chat session.

Subcommands
  init [TARGET] [--hooks]   set up .relayforge/, AGENTS.md conventions and (optionally) hooks
  hook session-start        print the current-facts summary into the session context
  hook capture              start a detached capture for the session given on stdin
  hook subagent-stop        store a subagent's final report
  capture                   read a transcript, extract items, write an inbox file, merge
  bootstrap                 read an existing repository once and write a first, unconfirmed picture of it
  merge                     apply pending inbox files to the fact files
  render                    write the read-only page to .relayforge/site/index.html
  status                    show what is in .relayforge/

This file is only the entry point; the code is in the relayforge package next to it.
Python 3.10+, standard library only.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目的 .relayforge/bin/ 里留下 __pycache__
if sys.argv[1:2] == ['hook']:  # 钩子在沙箱外运行（Claude Code、Codex 都是），它和它起的后台进程（capture）不算「会话没进沙箱」
    __import__('os').environ['RELAYFORGE_FROM_HOOK'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == '__main__' and sys.argv[1:3] == ['hook', 'codex']:  # Codex 的钩子（app/src/main/codex-hooks.js）：执行前的检查也只加载 guard.py
    import json as _json
    try:
        _data = _json.loads(sys.stdin.read() or '{}')
    except ValueError:
        _data = {}
    if not isinstance(_data, dict) or not __import__('os').environ.get('RELAYFORGE_SESSION_ID'):
        sys.exit(0)  # 不是 RelayForge 开的会话
    if _data.get('hook_event_name') == 'PreToolUse':
        import importlib.util
        _spec = importlib.util.spec_from_file_location('relayforge_guard', Path(__file__).resolve().parent / 'relayforge' / 'core' / 'guard.py')
        _guard = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_guard)
        sys.exit(_guard.codex_main(_data))
    from relayforge.project.status import codex_event  # noqa: E402
    sys.exit(codex_event(_data))

if __name__ == '__main__' and sys.argv[1:3] == ['hook', 'guard']:  # 每次改文件、跑 rf.py 之前都要过一下：只加载这一个文件，不加载整个程序
    import importlib.util
    _spec = importlib.util.spec_from_file_location('relayforge_guard', Path(__file__).resolve().parent / 'relayforge' / 'core' / 'guard.py')
    _guard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_guard)
    sys.exit(_guard.main(sys.argv[3:]))

from relayforge import *  # noqa: E402,F401,F403  `import rf` 仍然拿得到所有名字
from relayforge.cli import main  # noqa: E402

if __name__ == '__main__':
    from relayforge.core.procs import stop_on_signals  # noqa: E402
    stop_on_signals()  # 应用叫停（SIGTERM）、终端关掉（SIGHUP）时，正在等的命令连同它们的后代一起停
    sys.exit(main())
