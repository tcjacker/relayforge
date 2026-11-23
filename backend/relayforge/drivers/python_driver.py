"""测试集的 Python 驱动：程序（testsets.py）启动它，它加载接入代码、逐条调用、把每条的实际输出交回去。比对不在这里做。

协议（和语言无关；Node、Java 的驱动照同样的协议写，见 FORMAT.md 的「测试集」）：
  启动：程序在代码目录里启动驱动进程（当前目录就是代码目录），标准输入给一份 JSON，写完就关掉：
    {"protocol": 1, "root": "代码目录的绝对路径", "entry": "接入代码，相对代码目录", "cases": [{"n": 3, "input": …}, …]}
  回答：驱动往标准输出一行写一个 JSON 对象，按下面的顺序：
    {"t": "ready", "protocol": 1}                                        接入代码加载好了
    {"t": "load_error", "error": "类型: 说明", "trace": "…"}              加载失败（之后不再写 case），然后退出
    {"t": "case", "n": 3, "ok": true, "output": …, "ms": 1.2}            这一条正常返回，output 是能转成 JSON 的返回值
    {"t": "case", "n": 3, "ok": false, "error": "类型: 说明", "trace": "…", "ms": 1.2}   这一条抛了异常（或返回值不能转成 JSON）
    {"t": "done"}                                                        全部跑完
  标准输出只能有这些行：被测的代码打印的东西一律要转到标准错误（这里把文件描述符 1 接到 2 上，协议走一份复制出来的描述符）。
  程序那边：没有 ready 就退出、中途退出、没写完所有的 case、超时，都算这个测试集「跑不起来」。

Python 的接入代码：一个 .py 文件，导出 run(input)，返回能转成 JSON 的值；准备动作（建数据等）也在 run 里按 input 做。
它不含任何测试数据，也不做比对。每条调用拿到的是 input 的一份深拷贝。
只用标准库；不导入 relayforge 包（它在用户项目的环境里运行）。
"""

import copy
import importlib.util
import json
import os
import sys
import time
import traceback


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != here]  # 不让驱动自己的目录挡住项目里的同名模块
    channel = os.fdopen(os.dup(1), 'w', encoding='utf-8', buffering=1)
    os.dup2(2, 1)  # 被测的代码 print 的东西（连同 C 扩展直接写 fd 1 的）都去标准错误
    sys.stdout = sys.stderr

    def say(obj):
        channel.write(json.dumps(obj, ensure_ascii=False) + '\n')
        channel.flush()

    try:
        req = json.loads(sys.stdin.read() or '{}')
    except ValueError as error:
        say({'t': 'load_error', 'error': f'驱动收到的不是 JSON：{error}', 'trace': ''})
        return 0
    root = os.path.abspath(req.get('root') or os.getcwd())
    entry = os.path.normpath(os.path.join(root, str(req.get('entry') or '')))
    try:
        if not os.path.isfile(entry):
            raise FileNotFoundError(f'找不到接入代码 {req.get("entry")}（在 {root} 下）')
        for p in (root, os.path.dirname(entry)):
            if p not in sys.path:
                sys.path.insert(0, p)
        spec = importlib.util.spec_from_file_location('relayforge_testset_entry', entry)
        if spec is None or spec.loader is None:
            raise ImportError(f'没法把 {req.get("entry")} 当成 Python 模块加载')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        run = getattr(module, 'run', None)
        if not callable(run):
            raise AttributeError(f'接入代码 {req.get("entry")} 没有导出 run(input)')
    except BaseException as error:  # noqa: BLE001 - 加载时出的任何错都是「跑不起来」
        say({'t': 'load_error', 'error': f'{type(error).__name__}: {error}', 'trace': traceback.format_exc()[-4000:]})
        return 0
    say({'t': 'ready', 'protocol': 1})
    for case in req.get('cases') or []:
        started = time.perf_counter()
        try:
            got = run(copy.deepcopy(case.get('input')))
        except BaseException as error:  # noqa: BLE001 - 这一条抛了异常：记下来，接着跑下一条
            if isinstance(error, KeyboardInterrupt):
                raise
            say({'t': 'case', 'n': case.get('n'), 'ok': False, 'error': f'{type(error).__name__}: {error}',
                 'trace': traceback.format_exc()[-4000:], 'ms': round((time.perf_counter() - started) * 1000, 3)})
            continue
        ms = round((time.perf_counter() - started) * 1000, 3)
        try:
            output = json.loads(json.dumps(got, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as error:
            say({'t': 'case', 'n': case.get('n'), 'ok': False, 'error': f'返回值不能转成 JSON：{error}（返回的是 {type(got).__name__}）', 'trace': '', 'ms': ms})
            continue
        say({'t': 'case', 'n': case.get('n'), 'ok': True, 'output': output, 'ms': ms})
    say({'t': 'done'})
    return 0


if __name__ == '__main__':
    sys.exit(main())
