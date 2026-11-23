"""rf.py req cases … 和需求测试阶段的命令（交给测试、跳过测试、记一轮测试……）的分发。分层见 tests/test_layers.py。
"""
import json
import re
import sys
from pathlib import Path

from .record import STAGES
from .testing import req_retest, req_skip_test, req_steps, req_test_mark, req_to_test, req_verdict
from .usecases import case_add, case_decide, case_edit, case_remove, case_save, req_cases, req_classify

__all__ = ['req_cases_command', 'req_testing']


def req_cases_command(root, args):
    """rf.py req case-add / case-edit / case-rm / cases (sessions; who may change what is decided by the verified session token),
    case-ok / case-no / case-save (only the user, in the app: trust.py)."""
    v, act = list(args.values), args.action
    fields = {k: getattr(args, k) for k in ('name', 'cat', 'given', 'when', 'then') if getattr(args, k) is not None}

    def said(made, done):
        if made.get('pending'):
            return f'这是立项以后的改动，记成用例改动 {made["change"]}，等用户在应用里同意或不同意；同意前用例不变。'
        hints = made.get('hints') or []
        return done + (f'（提醒：用例要用业务的话写，这几个词看起来在说代码：{"、".join(hints[:6])}。）' if hints else '')
    if act == 'case-add':
        if not v:
            raise ValueError('用法：req case-add 验收条件序号 --name "覆盖哪种情况" --cat 正常|边界|异常 --given "前提" --when "操作" --then "预期" [--why "立项以后：为什么要加"]')
        made = case_add(root, v[0], fields, args.why)
        return made, said(made, f'已加用例 {made.get("case")}。' + (f'还没有用例、也没标「只能人工看」的验收条件：第 {"、".join(map(str, made["uncovered"]))} 条。' if made['uncovered'] else ''))
    if act == 'case-edit':
        if len(v) != 1:
            raise ValueError('用法：req case-edit 用例编号 [--name …] [--cat …] [--given …] [--when …] [--then …] [--check 验收条件序号] [--why "立项以后：为什么要改"]')
        made = case_edit(root, v[0], fields, args.check, args.why)
        return made, said(made, f'用例 {made["case"]} 已改。')
    if act == 'case-rm':
        if len(v) != 1:
            raise ValueError('用法：req case-rm 用例编号 [--why "立项以后：为什么要删"]')
        made = case_remove(root, v[0], args.why)
        return made, said(made, f'用例 {made["case"]} 已删（编号不再用）。')
    if act == 'cases':
        made = req_cases(root, v[0] if v else None)
        return made, made['text'] or '这个需求还没有验收条件。'
    if act in ('case-ok', 'case-no'):
        if len(v) != 2:
            raise ValueError(f'用法：req {act} 需求编号 改动编号')
        made = case_decide(root, v[0], v[1], act == 'case-ok')
        return made, f'用例改动 {made["change"]["k"]}：' + ('已同意，已生效。' if act == 'case-ok' else '不同意，丢弃。')
    if not v:
        raise ValueError('用法：req case-save 需求编号 --file -（JSON：{"op": "add"|"edit"|"delete"|"classify", "case", "check", "name", "cat", "given", "when", "then", "kind", "why"}）')
    raw = sys.stdin.read() if args.file in (None, '-') else Path(args.file).read_text(encoding='utf-8')
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ValueError('要给 JSON。') from None
    made = case_save(root, v[0], payload)
    return made, '已保存。'


def req_testing(root, args):
    """rf.py req classify / steps / verdict / retest / to-test / skip-test (and the app's test-kicked / test-told)."""
    v, act = list(args.values), args.action
    if act == 'classify':
        if len(v) < 2:
            raise ValueError('用法：req classify 序号[,序号…] 数据|界面 --why "理由"（序号是第几条验收条件）')
        nums = [x for x in re.split(r'[,，、\s]+', v[0]) if x]
        made = req_classify(root, nums, args.why or ' '.join(v[2:]), v[1])
        names = {'data': '数据可验', 'ui': '看界面'}
        return made, ((f'第 {"、".join(map(str, made["classified"]))} 条归为「{names[made["kind"]]}」。' if made['classified'] else '')
                      + (f'改归类是立项以后的改动，记成用例改动 {"、".join(map(str, made["proposed"]))}，等用户同意。' if made['proposed'] else '')
                      + (f'还没归类的：第 {"、".join(map(str, made["left"]))} 条。' if made['left'] else '验收条件都归类了。'))
    if act == 'steps':
        if not v:
            raise ValueError('用法：req steps 序号 "打开哪里" "做什么" "应该看到什么"（一步一次；--clear 清掉重写）')
        made = req_steps(root, v[0], *(v[1:4] + [None] * (3 - len(v[1:4]))), clear=args.clear)
        c = made['check']
        return made, f'第 {c["n"]} 条验收条件现在有 {len(c["steps"])} 步检查。'
    if act == 'verdict':
        if not v:
            raise ValueError('用法：req verdict ok --summary "测了什么、结果怎样" ／ req verdict problems --problem "哪条｜现象｜判断"（或 --file 问题.json）')
        problems = list(args.problem)
        if args.file:
            text = sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8')
            try:
                problems += json.loads(text) if text.strip().startswith('[') else json.loads(text).get('problems', [])
            except (ValueError, AttributeError):
                raise ValueError('问题清单的 JSON 读不懂：要是 [{"where", "what", "judge"}, …]。') from None
        made = req_verdict(root, v[0], args.summary or ' '.join(v[1:]), problems, list(args.unlinked))
        return made, (f'第 {made["round"]} 轮的结论已交回：可以验收了，需求进入待验收。' if made['verdict'] == 'ok'
                      else f'第 {made["round"]} 轮的结论已交回：有问题 {len(made["problems"])} 条，需求退回进行中，问题清单会交给开发会话。')
    if act == 'retest':
        made = req_retest(root, ' '.join(v))
        return made, '已交给测试：需求回到测试中，测试会话会再测一轮。'
    if act in ('to-test', 'skip-test'):
        made = (req_to_test if act == 'to-test' else req_skip_test)(root, v[0] if v else None)
        return made, f'{made["id"]} {made["title"]} 现在是{STAGES[made["stage"]]}。'
    if len(v) != 2:
        raise ValueError(f'用法：req {act} 需求编号 轮次')
    made = req_test_mark(root, v[0], act[5:], v[1])
    return made, json.dumps(made, ensure_ascii=False)
