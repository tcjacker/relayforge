"""rf.py test …：测试集的命令分发。出错写到标准错误、退出码 1。

测试集本身在 testsets.py，运行在 testruns.py；这里两边都要用，所以放在入口这一层。分层见 tests/test_layers.py。
"""
import json
import sys
from pathlib import Path

from .bridges import bridge_lines
from .edits import testset_add, testset_config, testset_edit, testset_new, testset_remove
from .decisions import testset_approve, testset_move, testset_reject, testset_revive, testset_save, testset_seen, testset_stale
from .views import testset_analyze, testset_list, testset_show
from .store import RESULT_WORDS, run_text
from .runs import bridge_report, testset_approve_build, testset_bridge, testset_run


# ---------- 命令行用的小工具 ----------

def _json_arg(text, name):
    try:
        return json.loads(text)
    except ValueError as error:
        raise ValueError(f'{name} 要是 JSON（字符串要带引号，比如 \'"2026-10"\'）：{error}') from None


def _read_payload(args):
    if args.file:
        raw = sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8')
    elif sys.stdin is not None and not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        raise ValueError('没有收到数据：用 --file 文件，或从标准输入给 JSON。')
    return _json_arg(raw, '数据')


def _case_fields(args):
    fields = {}
    if args.name is not None:
        fields['name'] = args.name
    if args.cat is not None:
        fields['cat'] = args.cat
    if args.input is not None:
        fields['input'] = _json_arg(args.input, '--input')
    if args.expect is not None:
        fields['expect'] = _json_arg(args.expect, '--expect')
    if args.raises:
        fields['raises'] = args.raises
    if getattr(args, 'usecase', None):
        fields['usecase'] = args.usecase
    return fields


def _list_text(out):
    if not out['sets']:
        return '还没有测试集。'
    lines = []
    for r in out['sets']:
        last = r['last']
        res = f'{RESULT_WORDS.get(last["result"], last["result"])}（{last["counts"]["pass"]}/{last["counts"]["total"]}）' if last else '还没跑'
        flags = [x for x in (f'过期：{r["stale_reason"]}' if r['stale'] else '', f'{r["in_progress"]} 进行中' if r['in_progress'] else '',
                             f'{len(r["pending_changes"])} 条改动等用户确认' if r['pending_changes'] else '', f'AI 新加 {r["unseen"]} 条没看过' if r['unseen'] else '') if x]
        lines.append(f'{r["id"]} {r["title"]}（{r["feature"]} {r["feature_title"]}，{r["cases"]} 条，{res}）' + ('；' + '；'.join(flags) if flags else ''))
        lines += [f'  提示：{h["text"]}' for h in r['hints']]
    return '\n'.join(lines)


def _show_text(s):
    lines = [f'{s["id"]} {s["title"]}（{s["feature"]} {s["feature_title"]}，{"过期：" + s["stale_reason"] if s["stale"] else "在用"}'
             + (f'，{s["in_progress"]} 进行中' if s['in_progress'] else '') + '）',
             f'数据文件：{s["data_file"]}', f'接入代码（{s["lang"]}）：{s["entry"]}' + ('' if s['entry_code'] is not None else '（还不存在）')]
    if s['checks']:
        lines.append('对应的验收条件：' + '；'.join(s['checks']))
    for c in s['cases_list']:
        r = c['result']
        mark = {'pass': '✓', 'fail': '✗', 'broken': '!'}.get(r['status'], '?') if r else '?'
        tail = ('' if not r or r['status'] == 'pass' else '：' + ('；'.join(r['diff'][:2]) or (r['error'] or ''))) if r else '（还没跑' + ('，数据改过' if c['stale_result'] else '') + '）'
        flags = ''.join(x for x in ('〔AI 新加〕' if c.get('unseen') else '', f'〔{c["in_progress"]} 进行中〕' if c.get('in_progress') else '',
                                    f'〔改动 {c["changed"]} 等确认〕' if c.get('changed') else '') if x)
        lines.append(f'  {mark} #{c["n"]} [{c.get("cat")}] {c["name"]}{flags}{tail}')
    for ch in s['pending_changes'] + s['approved_changes']:
        lines.append(f'改动 {ch["id"]}（#{ch["case"]} {ch["name"]}，{"删除" if ch["op"] == "delete" else "修改"}，{ch["by"] or "主线"} 提的，'
                     + ('等用户确认' if ch['state'] == 'pending' else '用户已同意，随需求完成落定') + f'）：{ch["why"]}')
    for h in s['hints']:
        lines.append(f'提示：{h["text"]}')
    if s.get('analysis'):
        lines.append(f'分析（AI 写的，{s["analysis"]["at"]}）：{s["analysis"]["text"]}')
    return '\n'.join(lines)


def test_command(root, args):
    """rf.py test …. Errors go to stderr with exit 1."""
    v = list(args.values)
    act = args.action
    try:
        if act == 'new':
            out = testset_new(root, ' '.join(v), args.feature, args.entry, args.lang, args.check, args.session, args.transcript, args.classpath, args.build)
            text = (f'已新建测试集 {out["id"]}「{out["title"]}」（{out["feature"]}），数据文件 {out["data_file"]}。'
                    f'接下来：写接入代码 {out["entry"]}（只导出 run(input)，不放数据），用 test add {out["id"]} 加数据，再 test run {out["id"]}。'
                    + ('构建命令第一次执行前要用户在应用里看过、点同意；没同意时这个测试集跑不起来，不要想办法绕过。' if out.get('build') else ''))
        elif act == 'add':
            if not v:
                raise ValueError('用法：test add TS-001 --name "覆盖哪种情况" --cat 正常|边界|异常 --input JSON --expect JSON，或 --file 数据.json')
            fields = _case_fields(args)
            if fields:
                cases = [fields]
            else:
                payload = _read_payload(args)
                cases = payload.get('cases') if isinstance(payload, dict) and 'cases' in payload else payload if isinstance(payload, list) else [payload]
            out = testset_add(root, v[0], cases, args.session, args.transcript)
            text = f'{out["id"]}：加了 {len(out["added"])} 条' + (f'，跳过 {len(out["skipped"])} 条重复的：' + '；'.join(f'「{s["name"]}」{s["why"]}' for s in out['skipped']) if out['skipped'] else '') + '。'
        elif act == 'edit':
            if len(v) != 2:
                raise ValueError('用法：test edit TS-001 3 [--name …] [--cat …] [--input JSON] [--expect JSON] [--raises …] [--why "理由"]')
            out = testset_edit(root, v[0], v[1], _case_fields(args), args.why, args.session, args.transcript)
            text = (f'{out["id"]} #{out["case"]}：这是已经落定的数据，改动记成 {out["change"]["id"]}，等用户同意；同意前在这个需求里按新的数据跑，主线上不变。'
                    if out['pending'] else f'{out["id"]} #{out["case"]} 已改。')
        elif act == 'rm':
            if len(v) != 2:
                raise ValueError('用法：test rm TS-001 3 [--why "理由"]')
            out = testset_remove(root, v[0], v[1], args.why, args.session, args.transcript)
            text = (f'{out["id"]} #{out["case"]}：这是已经落定的数据，删除记成改动 {out["change"]["id"]}，等用户同意。' if out['pending'] else f'{out["id"]} #{out["case"]} 已删。')
        elif act == 'run':
            out = testset_run(root, v, args.req, args.fix, args.feature, args.all, args.case, args.dir, args.timeout, getattr(args, 'env', None), getattr(args, 'no_env', False),
                              preflight=getattr(args, 'prep_check', False))
            text = run_text(out) if 'blocked' not in out else ('\n'.join((f'{b["note"]}还没经用户同意' if b.get('kind') == 'build' else f'准备步骤 {b["n"]}「{b["note"]}」还没经用户同意')
                                                                         + f'：`{b["command"]}`（在 {b["abs_dir"]}）' for b in out['blocked'])
                                                               or '没有要用户同意的准备步骤和构建命令。')
        elif act == 'analyze':
            if not v:
                raise ValueError('用法：test analyze TS-001 --text "哪些过了、哪些没过、为什么"')
            out = testset_analyze(root, v[0], args.text, args.session, args.transcript)
            text = f'已记下 {out["id"]} 的分析（标为 AI 写的）。'
        elif act == 'list':
            out = testset_list(root, args.req, args.fix, args.feature, True if args.stale else None)
            text = _list_text(out)
        elif act == 'show':
            if not v:
                raise ValueError('用法：test show TS-001 [--req R-…]')
            out = testset_show(root, v[0], args.req or args.fix)
            text = _show_text(out)
        elif act == 'bridge':
            out = testset_bridge(root, args.path, args.lang)
            names = {'python': 'unittest / pytest（Python 测试集）', 'node': 'node --test（Node 测试集）', 'java': 'JUnit 5（Java 测试集）'}
            text = '\n'.join(f'桥接文件 {f["relative"]} ' + ('已写好' if f['changed'] else '已经是最新的') + f'：项目自己的 {names[f["lang"]]}会跑到在用的测试集。' for f in out['files'])
            text += ''.join(f'\n没有生成 {x["lang"]} 的桥接文件：{x["why"]}' for x in out['skipped'])
        elif act == 'bridge-run':  # 桥接文件自己调用：跑在用的测试集，不记结果
            out = bridge_report(root, args.lang or 'python')
            text = bridge_lines(out)
        elif act == 'stale':
            out = testset_stale(root, v[0] if v else '', args.reason)
            text = f'{out["id"]} 已标成过期，不再运行。'
        elif act == 'revive':
            out = testset_revive(root, v[0] if v else '')
            text = f'{out["id"]} 已恢复在用。'
        elif act == 'move':
            out = testset_move(root, v[0] if v else '', args.feature, args.req)
            text = f'{out["id"]} 已改挂靠：{out["feature"]}，需求 {out["requirement"] or "无"}。'
        elif act in ('approve', 'reject'):
            if len(v) != 2:
                raise ValueError(f'用法：test {act} TS-001 改动编号')
            out = (testset_approve if act == 'approve' else testset_reject)(root, v[0], v[1])
            text = f'{out["id"]}：' + ('已同意这条改动。' if act == 'approve' else '已丢弃这条改动。')
        elif act == 'save':
            if not v:
                raise ValueError('用法：test save TS-001 [--case 3|new] [--req R-…] --file 数据.json')
            out = testset_save(root, v[0], _read_payload(args), args.case, args.req or args.fix)
            text = f'{out["id"]} 已保存：新加 {len(out["saved"]["added"])} 条，改了 {len(out["saved"]["changed"])} 条，删了 {len(out["saved"]["removed"])} 条。'
        elif act == 'config':
            if not v:
                raise ValueError('用法：test config TS-001 --file -（JSON：{"lang", "entry", "classpath": [...], "build"}）')
            out = testset_config(root, v[0], _read_payload(args))
            text = f'{out["id"]}：' + ('；'.join(out['changed']) if out['changed'] else '设置没有变化') + '。'
        elif act == 'approve-build':
            if not v:
                raise ValueError('用法：test approve-build TS-001 --print 指纹 [--dir 代码目录]（只由应用调用）')
            out = testset_approve_build(root, v[0], getattr(args, 'fingerprint', None), args.dir)
            text = f'{out["id"]}：同意执行构建命令 `{out["command"]}`（在 {out["dir"]}）。'
        else:  # seen
            out = testset_seen(root, v[0] if v else '', args.case)
            text = f'{out["id"]}：{out["cleared"]} 条标为看过了。'
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False) if args.json else text)
    return 0
