"""rf.py issue …：问题的命令分发。出错写到标准错误、退出码 1。分层见 tests/test_layers.py。
"""
import json
import sys

from ..flows import issue_confirm
from ..testsets.gate import GateFailed
from .convert import issue_from_req, issue_to_req
from .fixes import issue_attach, issue_brief, issue_fix
from .probes import issue_finding, issue_handback, issue_probe, issue_reject, issue_unconfirm
from .record import ISSUE_KINDS, issue_list, issue_new, issue_reply, issue_set, issue_show
from .review import issue_review, issue_review_apply, issue_review_run, issue_review_discard, issue_review_status
from .triage import issue_triage
from ..requirements.record import MergeBusy, NeedsSync

__all__ = ['issue_command']


def issue_command(root, args):
    """rf.py issue …: everything about issues. Errors go to stderr with exit 1; a merge conflict or a busy merge on
    confirm exit 3 and 4 like req done, with the details as JSON for the app."""
    v = list(args.values)
    act = args.action
    out = None
    try:
        if act == 'new':
            out = issue_new(root, ' '.join(v), args.desc or '', args.source, args.image, args.feature, args.op, args.session, args.transcript,
                            duplicate_of=args.duplicate_of, possible_duplicate=args.possible_duplicate,
                            duplicate_reason=args.duplicate_reason, steps=args.steps, observed=args.observed)
            if args.duplicate_of:
                text = f'已向问题 {out["num"]}「{out["title"]}」追加 Agent 复现补充。'
            else:
                text = f'已记下问题 {out["num"]}「{out["title"]}」（来源：{out["source"]}）。应用会先让 AI 初判，再由用户决定怎么处理。'
                if args.possible_duplicate:
                    text += f'已标注可能重复：{args.possible_duplicate}。'
        elif act == 'list':
            out = issue_list(root)
            if not args.json:
                rows = [f'{i["num"]} {i["title"]}（{i["state_name"]}' + (f'，{ISSUE_KINDS[i["kind"]]}' if i['kind'] else '') + (f'，{i["feature"]}' if i['feature'] else '') + '）' for i in out['issues']]
                text = '\n'.join(rows) or '还没有问题。'
        elif act == 'show':
            out = issue_show(root, v[0] if v else '')
            text = json.dumps(out, ensure_ascii=False, indent=2)
        elif act == 'review':
            out = issue_review(root, background=True)
            text = out.get('message') or ('AI 在后台复查待处理问题，用 issue review-status 看进度。' if out['state'] == 'running' else
                                          json.dumps(out, ensure_ascii=False, indent=2))
        elif act == 'review-run':
            out = issue_review_run(root, v[0] if v else '')
            text = json.dumps(out, ensure_ascii=False, indent=2)
        elif act == 'review-status':
            out = issue_review_status(root)
            text = json.dumps(out, ensure_ascii=False, indent=2)
        elif act == 'review-discard':
            out = issue_review_discard(root)
            text = '已关掉这次复查的结果，问题的状态都没有改。'
        elif act == 'review-apply':
            out = issue_review_apply(root, json.load(sys.stdin))
            text = f'已处理 {len(out["applied"])} 条问题。' + ''.join(x['reason'] for x in out['skipped'])
        elif act == 'triage':
            out = issue_triage(root, v[0] if v else '')
            last = [e for e in out['thread'] if e.get('t') == 'judge'][-1]
            text = f'{out["num"]} 初判：{ISSUE_KINDS[last["kind"]]}。{last["reason"]}'
        elif act == 'reply':
            if not v:
                raise ValueError('用法：issue reply #033 "补充的话" [--image 图片]')
            out = issue_reply(root, v[0], ' '.join(v[1:]), args.image, args.to_probe)
            text = f'已补充到问题 {out["num"]}。'
        elif act == 'set':
            if not v:
                raise ValueError('用法：issue set #033 feature RF-101|none、defer、undefer、dismiss "原因或 #18"、reopen、kind bug|request、solved、finish-resolved、finish-closed')
            out = issue_set(root, v[0], v[1] if len(v) > 1 else '', args.reason if args.reason is not None else ' '.join(v[2:]))
            text = f'问题 {out["num"]} 已更新：{out["state_name"]}。'
        elif act == 'fix':
            out = issue_fix(root, v, cli=args.fix_cli, model=args.fix_model)
            text = f'已开始修复 {out["id"]}：分支 {out["branch"]}，工作目录 {out["worktree"]}。'
        elif act == 'probe':
            out = issue_probe(root, v[0] if v else '')
            text = f'问题 {out["num"]} 已交给 AI 排查。'
        elif act == 'finding':
            out = issue_finding(root, v[0] if v else '', args.kind, args.text, args.steps, args.where, args.verified, args.inferred,
                                args.session, args.transcript, observed=args.observed, verified_fix=args.verified_fix)
            text = (f'已记下问题 {out["num"]} 的排查结论：{ISSUE_KINDS[out["kind"]]}。问题现在等用户决定；排查到这里为止，不要自己开始修复、改状态或转需求。'
                    '没有别的要说的，就用一两句话告诉用户结论在对话里。')
        elif act == 'attach':
            if len(v) != 2:
                raise ValueError('用法：issue attach 修复编号或问题编号 会话编号')
            out = issue_attach(root, *v)
            text = f'已挂上：{out.get("id") or out.get("issue")}'
        elif act == 'brief':
            text = issue_brief(root, v[0] if v else None)
            out = {'text': text}
        elif act == 'handback':
            out = issue_handback(root, v[0] if v else None, args.changed, args.verify, args.session, args.transcript, getattr(args, 'no_regression', '') or '')
            left = [x['num'] for x in out['siblings'] if x['state'] == 'fixing']
            text = (f'已交回问题 {out["num"]} 的修复报告，等用户确认。' + (f'同批还有 {"、".join(left)} 没交回，逐条交回。' if left else '')
                    + '不要运行 issue confirm，确认只由用户做。')
        elif act == 'confirm':
            try:
                out = issue_confirm(root, v[0] if v else '', skip_gate=getattr(args, 'skip_gate', False))
            except GateFailed as error:  # 合并闸门挡住了，和 req done 一样
                if args.json:
                    print(json.dumps({'error': str(error), 'gate': error.result}, ensure_ascii=False))
                print(str(error), file=sys.stderr)
                return 5
            except (NeedsSync, MergeBusy) as error:  # like req done: back to the fix session to merge the target in, or wait its turn
                if args.json:
                    sync = isinstance(error, NeedsSync)
                    print(json.dumps({'error': str(error), 'sync': sync, 'conflict': sync and bool(error.files), 'busy': not sync,
                                      'base': getattr(error, 'base', None), 'files': getattr(error, 'files', []), 'loose': getattr(error, 'loose', []),
                                      'merging': getattr(error, 'merging', False), 'holder': getattr(error, 'holder', None)}, ensure_ascii=False))
                print(str(error), file=sys.stderr)
                return 3 if isinstance(error, NeedsSync) else 4
            text = f'问题 {out["num"]} 现在是{out["state_name"]}。'
        elif act == 'unconfirm':
            out = issue_unconfirm(root, v[0] if v else '')
            text = f'问题 {out["num"]} 的确认已撤回。'
        elif act == 'reject':
            if not v:
                raise ValueError('用法：issue reject #033 "还有什么问题"')
            out = issue_reject(root, v[0], ' '.join(v[1:]), args.image)
            text = f'问题 {out["num"]} 回到修复。'
        elif act == 'from-req':
            out = issue_from_req(root, v[0] if v else '', args.feature)
            text = f'需求 {out["source_requirement"]} 已转为问题 {out["num"]}。'
        else:  # to-req
            out = issue_to_req(root, v[0] if v else '')
            text = f'问题 {out["issue"]["num"]} 已转为需求 {out["requirement"]["id"]}（立项中）。'
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False) if args.json else text)
    return 0
